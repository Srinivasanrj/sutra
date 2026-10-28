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
from sutra.permissions import Permissions
from sutra.tools import Tool, ToolError, Workspace, build_tools

PROJECT_MEMORY_FILE = "SUTRA.md"
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
- Keep replies short and concrete. Use Markdown. When a task is done, summarise what you changed in a few lines."""


class UI(Protocol):
    """What the agent needs from the terminal. Tests use a fake version."""

    def stream(self, events) -> tuple[str, list[ToolCall], dict]: ...
    def tool_start(self, call: ToolCall) -> None: ...
    def tool_result(self, call: ToolCall, result: str, ok: bool) -> None: ...
    def ask_permission(self, tool: Tool, call: ToolCall, preview: str) -> str: ...
    def notice(self, message: str, style: str = "yellow") -> None: ...


class Agent:
    def __init__(self, settings: Settings, llm: OllamaClient, workspace: Workspace, ui: UI,
                 permissions: Permissions | None = None):
        self.settings = settings
        self.llm = llm
        self.ws = workspace
        self.ui = ui
        self.permissions = permissions or Permissions()
        self.tools = build_tools(workspace)
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

    def _request_messages(self) -> list[dict]:
        """The messages actually sent. With thinking off, qwen3 still reasons out loud unless
        the turn ends with its '/no_think' switch, so we add it to the latest user message."""
        if self.think:
            return self.messages
        msgs = list(self.messages)
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "user":
                msgs[i] = {**msgs[i], "content": msgs[i]["content"] + " /no_think"}
                break
        return msgs

    # ---------- main loop ----------
    def run(self, user_text: str) -> str:
        """Handle one user request. Returns the final answer text."""
        self.messages.append({"role": "user", "content": user_text})
        schemas = [t.schema() for t in self.tools.values()]

        for _ in range(self.settings.max_steps):
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
            self.ui.tool_result(call, result, ok=True)
            return result
        except ToolError as e:
            msg = f"Error: {e}"
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
