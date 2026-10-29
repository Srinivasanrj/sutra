"""Planning tools: the to-do list (todo_write) and leaving plan mode (exit_plan_mode)."""

import re
from dataclasses import dataclass
from typing import Callable

from sutra.tools.base import Tool, ToolError

STATUSES = ("pending", "in_progress", "completed")
MAX_TODOS = 20
_STEP_LINE = re.compile(r"^\s*(?:\d+[.)]|[-*•]|\[[ x]\])\s+(.+?)\s*$")
_INLINE_STEP = re.compile(r"\s+(?=\d+[.)]\s)")  # "1. a 2. b" written on one line


@dataclass
class TodoItem:
    content: str
    status: str = "pending"


class TodoList:
    """The agent's checklist for the current task."""

    def __init__(self):
        self.items: list[TodoItem] = []

    def replace(self, raw: list) -> list[str]:
        """Replace the whole list. Accepts {content, status} objects or plain strings
        (small models often send just strings). Returns warnings for the model."""
        if not isinstance(raw, list):
            raise ToolError("todos must be a list of {content, status} objects.")
        items, warnings = [], []
        for entry in raw[:MAX_TODOS]:
            if isinstance(entry, str):
                entry = {"content": entry}
            if not isinstance(entry, dict) or not str(entry.get("content", "")).strip():
                continue
            status = str(entry.get("status", "pending")).strip().lower().replace(" ", "_").replace("-", "_")
            if status in ("done", "complete", "finished"):
                status = "completed"
            if status not in STATUSES:
                warnings.append(f"unknown status '{status}' treated as pending")
                status = "pending"
            items.append(TodoItem(str(entry["content"]).strip(), status))
        if not items:
            raise ToolError("todos is empty. Send at least one {content, status} item.")
        active = [i for i in items if i.status == "in_progress"]
        if len(active) > 1:
            for extra in active[1:]:
                extra.status = "pending"
            warnings.append("only one item can be in_progress; kept the first")
        self.items = items
        return warnings

    def summary(self) -> str:
        done = sum(i.status == "completed" for i in self.items)
        lines = [f"{done}/{len(self.items)} done"]
        marks = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
        lines += [f"{marks[i.status]} {i.content}" for i in self.items]
        return "\n".join(lines)


def steps_from_plan(plan: str) -> list[str]:
    """Turn the numbered or bulleted lines of a plan into to-do items."""
    lines = [part for line in plan.splitlines() for part in _INLINE_STEP.split(line)]
    steps = [m.group(1) for line in lines if (m := _STEP_LINE.match(line))]
    return [s.strip("* ") for s in steps][:MAX_TODOS]


def build_planning_tools(todo_list: TodoList, on_plan: Callable[[str], str]) -> list[Tool]:
    def todo_write(todos: list) -> str:
        warnings = todo_list.replace(todos)
        note = f" (note: {'; '.join(warnings)})" if warnings else ""
        return f"To-do list updated{note}.\n{todo_list.summary()}"

    def exit_plan_mode(plan: str) -> str:
        if not plan.strip():
            raise ToolError("plan is empty. Write the step-by-step plan first.")
        return on_plan(plan)

    return [
        Tool(
            name="todo_write",
            description=(
                "Create or update your to-do list for a multi-step task. Send the WHOLE list every time. "
                "Mark exactly one item in_progress while you work on it, and mark items completed as soon as they are done."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "The full list",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {"type": "string", "description": "What to do"},
                                "status": {"type": "string", "enum": list(STATUSES)},
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
            run=todo_write,
        ),
        Tool(
            name="exit_plan_mode",
            description=(
                "Only in plan mode: present your finished plan to the user for approval. "
                "Write it as a numbered list of concrete steps (which files to change and how)."
            ),
            parameters={
                "type": "object",
                "properties": {"plan": {"type": "string", "description": "The step-by-step plan, in Markdown"}},
                "required": ["plan"],
            },
            run=exit_plan_mode,
        ),
    ]
