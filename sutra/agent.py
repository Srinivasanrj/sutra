"""The agent loop.

One user request can take several rounds:
  1. send the conversation + tool list to the model
  2. if the reply asks for tools, run them and add their results to the conversation
  3. repeat until the model answers without asking for a tool (or we hit max_steps)

The model itself has no memory: `self.messages` is the memory, re-sent every round.
"""

import json
import platform
import re
from typing import Protocol

from sutra.config import Settings
from sutra.llm import Event, LLMError, OllamaClient, ToolCall
from sutra.permissions import Permissions, UserStop
from sutra.tools import Tool, ToolError, Workspace, build_tools
from sutra.tools.planning import TodoItem, TodoList, build_planning_tools, steps_from_plan

PROJECT_MEMORY_FILE = "SUTRA.md"
PLAN_ONLY_TOOL = "exit_plan_mode"

PLAN_MODE_NOTE = """PLAN MODE IS ON. Do not change any files or run commands.
1. Investigate with list_files, search_code and read_file.
2. Then call exit_plan_mode with a numbered, step-by-step plan: which files to change and what to change in each.
Do not start implementing until the user approves the plan."""
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)  # reasoning some models put inside the answer

SYSTEM_PROMPT = """You are Sutra, a coding agent working in the user's terminal.
Workspace folder: {root}
Operating system: {os} (shell commands run in {shell})

How to work:
- Look before you answer: use list_files, search_code and read_file to inspect the code. Never guess file contents.
- To change a file, use edit_file with old_text copied exactly from read_file output, without the "12| " line-number prefix. Use write_file only for new files or full rewrites.
- Use run_command to run tests or scripts and check your work.
- Paths are relative to the workspace folder.
- If a tool returns an error, read it, fix the problem, and try again.
- For a task with 3 or more steps, first call todo_write with the steps, then keep it updated: one item in_progress at a time, mark items completed as you finish them.
- Keep replies short and concrete. Use Markdown. When a task is done, summarise what you changed in a few lines."""


class UI(Protocol):
    """What the agent needs from the terminal. Tests use a fake version."""

    def stream(self, events) -> tuple[str, list[ToolCall], dict]: ...
    def tool_start(self, call: ToolCall) -> None: ...
    def tool_result(self, call: ToolCall, result: str, ok: bool) -> None: ...
    def ask_permission(self, tool: Tool, call: ToolCall, preview: str) -> str: ...
    def review_plan(self, plan: str) -> tuple[bool, str]: ...
    def show_todos(self, items: list[TodoItem]) -> None: ...
    def notice(self, message: str, style: str = "yellow") -> None: ...


class Agent:
    def __init__(self, settings: Settings, llm: OllamaClient, workspace: Workspace, ui: UI,
                 permissions: Permissions | None = None):
        self.settings = settings
        self.llm = llm
        self.ws = workspace
        self.ui = ui
        self.permissions = permissions or Permissions()
        self.todos = TodoList()
        self.plan_mode = False
        self.tools = build_tools(workspace)
        for tool in build_planning_tools(self.todos, self._review_plan):
            self.tools[tool.name] = tool
        self.model = settings.model
        self.think = settings.think
        self.last_stats: dict = {}
        self.reset()

    # ---------- conversation ----------
    def system_prompt(self) -> str:
        is_windows = platform.system() == "Windows"
        prompt = SYSTEM_PROMPT.format(
            root=self.ws.root, os=platform.system(), shell="PowerShell" if is_windows else "bash"
        )
        memory = self.ws.root / PROJECT_MEMORY_FILE
        if memory.is_file():
            prompt += f"\n\nProject notes from {PROJECT_MEMORY_FILE}:\n{memory.read_text(encoding='utf-8')[:4000]}"
        return prompt

    def reset(self) -> None:
        self.messages: list[dict] = [{"role": "system", "content": self.system_prompt()}]
        self.todos.items = []

    def _request_messages(self) -> list[dict]:
        """The messages actually sent, which differ slightly from the stored history:
        - in plan mode, a reminder of the plan-mode rules goes at the end (small models forget);
        - with thinking off, qwen3 still reasons out loud unless the latest user message ends
          with its '/no_think' switch."""
        msgs = list(self.messages)
        if not self.think:
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i]["role"] == "user":
                    msgs[i] = {**msgs[i], "content": msgs[i]["content"] + " /no_think"}
                    break
        if self.plan_mode:
            msgs.append({"role": "system", "content": PLAN_MODE_NOTE})
        return msgs

    # ---------- plan mode ----------
    def available_tools(self) -> list[Tool]:
        """Plan mode offers only the read-only file tools plus exit_plan_mode, so a small model has one
        obvious way to finish. Normal mode offers everything except exit_plan_mode."""
        if self.plan_mode:
            return [t for t in self.tools.values() if t.permission is None and t.name != "todo_write"]
        return [t for t in self.tools.values() if t.name != PLAN_ONLY_TOOL]

    def _review_plan(self, plan: str) -> str:
        """Called by the exit_plan_mode tool: the user approves the plan or sends feedback."""
        approved, feedback = self.ui.review_plan(plan)
        if not approved:
            reason = f" Their feedback: {feedback}" if feedback else " Ask them what to change."
            return f"The user did not approve the plan.{reason} Revise it and call exit_plan_mode again."
        self.plan_mode = False
        steps = steps_from_plan(plan) or ["Carry out the approved plan"]
        self.todos.items = [TodoItem(s, "in_progress" if i == 0 else "pending") for i, s in enumerate(steps)]
        self.ui.show_todos(self.todos.items)
        return (
            "The user approved the plan. Plan mode is off: you may now edit files and run commands.\n"
            f"Your to-do list was created from the plan:\n{self.todos.summary()}\n"
            "Work through it in order, and call todo_write to update statuses as you finish each step."
        )

    # ---------- main loop ----------
    def run(self, user_text: str) -> str:
        """Handle one user request. Returns the final answer text."""
        try:
            return self._loop(user_text)
        except UserStop as e:
            self.ui.notice(str(e))
            return ""

    def _loop(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        nudged = False

        for _ in range(self.settings.max_steps):
            schemas = [t.schema() for t in self.available_tools()]  # changes when plan mode ends
            events = self.llm.chat(
                self.model, self._request_messages(), tools=schemas, think=self.think,
                num_ctx=self.settings.num_ctx,
            )
            text, calls, stats = self.ui.stream(events)
            text = THINK_BLOCK.sub("", text).strip()
            self.last_stats = stats

            assistant = {"role": "assistant", "content": text}
            if calls:
                assistant["tool_calls"] = [
                    {"function": {"name": c.name, "arguments": c.arguments}} for c in calls
                ]
            self.messages.append(assistant)

            if not calls and self.plan_mode:
                # Small models often write the plan as text, or stop, instead of calling exit_plan_mode.
                if text:
                    feedback = self._review_plan(text)  # treat the text as the proposed plan
                    self.messages.append({"role": "user", "content": feedback})
                    continue
                if not nudged:
                    nudged = True
                    self.messages.append({"role": "user", "content": "Now call exit_plan_mode with your step-by-step plan."})
                    continue
            if not calls:
                return text

            for call in calls:
                result = self.execute(call)
                self.messages.append({"role": "tool", "tool_name": call.name, "content": result})

        self.ui.notice(f"Stopped after {self.settings.max_steps} steps (SUTRA_MAX_STEPS).")
        return ""

    # ---------- running one tool ----------
    def execute(self, call: ToolCall) -> str:
        tool = self.tools.get(call.name)
        if tool is None:
            msg = f"Error: unknown tool '{call.name}'. Available tools: {', '.join(self.tools)}."
            self.ui.tool_result(call, msg, ok=False)
            return msg

        # Enforced in code, not just in the prompt: a model can ask for a tool it wasn't offered.
        if self.plan_mode and tool.permission is not None:
            msg = (f"Error: {call.name} is not allowed in plan mode. Only read files now, then call "
                   "exit_plan_mode with your plan.")
            self.ui.tool_result(call, msg, ok=False)
            return msg
        if not self.plan_mode and tool.name == PLAN_ONLY_TOOL:
            msg = "Error: you are not in plan mode. Continue with the task."
            self.ui.tool_result(call, msg, ok=False)
            return msg

        args, problem = self._check_args(tool, call.arguments)
        if problem:
            self.ui.tool_result(call, problem, ok=False)
            return problem

        self.ui.tool_start(call)
        try:
            if self.permissions.needs_approval(tool):
                preview = tool.preview(**args) if tool.preview else json.dumps(args, indent=2)
                answer = self.ui.ask_permission(tool, call, preview)
                if not self.permissions.record(tool, answer):
                    msg = "The user declined this action. Do not retry it; ask the user what they want instead."
                    self.ui.tool_result(call, "declined", ok=False)
                    return msg
            result = tool.run(**args)
            if tool.name == "todo_write":
                self.ui.show_todos(self.todos.items)
            elif tool.name != PLAN_ONLY_TOOL:
                self.ui.tool_result(call, result, ok=True)
            return result
        except ToolError as e:
            msg = f"Error: {e}"
        except UserStop:
            raise
        except Exception as e:  # a bug in a tool should not crash the whole session
            msg = f"Error: {type(e).__name__}: {e}"
        self.ui.tool_result(call, msg, ok=False)
        return msg

    @staticmethod
    def _check_args(tool: Tool, raw: dict) -> tuple[dict, str | None]:
        """Drop unknown arguments and report missing required ones (small models get these wrong)."""
        if "_raw" in raw:
            return {}, f"Error: the arguments for {tool.name} were not valid JSON. Send them as a JSON object."
        props = tool.parameters.get("properties", {})
        args = {k: v for k, v in raw.items() if k in props}
        missing = [k for k in tool.parameters.get("required", []) if k not in args]
        if missing:
            return args, f"Error: {tool.name} is missing required argument(s): {', '.join(missing)}."
        return args, None


__all__ = ["Agent", "LLMError", "Event"]
