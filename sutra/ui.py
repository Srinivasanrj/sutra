"""Everything the user sees: streaming replies, tool calls, diffs and permission prompts."""

import json

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from sutra.llm import ToolCall
from sutra.permissions import UserStop

THINKING_TAIL_LINES = 6


def _short_args(args: dict, limit: int = 80) -> str:
    parts = []
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v)
        s = s.replace("\n", "\\n")
        parts.append(f"{k}={s[:40] + '…' if len(s) > 40 else s}")
    text = ", ".join(parts)
    return text[:limit] + "…" if len(text) > limit else text


class TerminalUI:
    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.show_thinking = True

    # ---------- streaming a reply ----------
    def stream(self, events) -> tuple[str, list[ToolCall], dict]:
        text, thinking, calls, stats = "", "", [], {}

        def render():
            parts = []
            if thinking and not text:
                tail = "\n".join(thinking.strip().splitlines()[-THINKING_TAIL_LINES:])
                parts.append(Text("💭 " + tail, style="dim italic"))
            if text:
                parts.append(Markdown(text))
            if not parts:
                parts.append(Spinner("dots", text=Text(" Working…", style="cyan")))
            return Group(*parts)

        with Live(render(), console=self.console, refresh_per_second=10, transient=False) as live:
            try:
                for ev in events:
                    if ev.kind == "thinking" and self.show_thinking:
                        thinking += ev.text
                    elif ev.kind == "text":
                        text += ev.text
                    elif ev.kind == "tool_calls":
                        calls.extend(ev.tool_calls)
                    elif ev.kind == "done":
                        stats = ev.stats
                    live.update(render())
            except KeyboardInterrupt:
                events.close()
                text += "\n\n*[stopped]*"
                calls = []  # don't run tools from a reply the user interrupted
                stats = {"stopped": True}
            # When only tools were requested, clear the spinner/thinking instead of leaving it on screen.
            live.update(Markdown(text) if text.strip() else Text(""))
        return text, calls, stats

    # ---------- tools ----------
    def tool_start(self, call: ToolCall) -> None:
        self.console.print(Text.assemble(("● ", "bold cyan"), (call.name, "bold"), (f"({_short_args(call.arguments)})", "cyan")))

    def tool_result(self, call: ToolCall, result: str, ok: bool) -> None:
        lines = result.strip().splitlines() or [""]
        summary = lines[0][:120] + (f"  (+{len(lines) - 1} lines)" if len(lines) > 1 else "")
        self.console.print(Text("  ⎿ " + summary, style="dim" if ok else "red"))

    def ask_permission(self, tool, call: ToolCall, preview: str) -> str:
        if tool.permission == "shell":
            body = Syntax(preview, "powershell", theme="ansi_dark", word_wrap=True)
            title = "Run this command?"
        else:
            body = Syntax(preview, "diff", theme="ansi_dark", word_wrap=True)
            title = f"Apply this change ({call.name})?"
        self.console.print(Panel(body, title=title, border_style="yellow", expand=False))
        while True:
            answer = self._ask("  [y] yes  [n] no  [a] always for this session › ", "yellow")
            if answer in ("y", "yes", ""):
                return "y"
            if answer in ("n", "no"):
                return "n"
            if answer in ("a", "always"):
                return "a"

    def _ask(self, prompt: str, style: str, lower: bool = True) -> str:
        """Read one answer. Raises UserStop when input has ended (e.g. piped input ran out)."""
        try:
            # Text(), not a markup string, so "[y]" is shown as-is instead of read as a style tag.
            raw = self.console.input(Text(prompt, style=style))
        except EOFError:
            raise UserStop("No more input, so the task was stopped.")
        answer = raw.strip().lstrip("﻿").strip()  # PowerShell pipes can add a byte-order mark
        return answer.lower() if lower else answer

    # ---------- planning ----------
    def show_todos(self, items) -> None:
        if not items:
            return
        done = sum(i.status == "completed" for i in items)
        lines = Text(f"  To-do ({done}/{len(items)} done)\n", style="bold")
        for i in items:
            if i.status == "completed":
                lines.append("  ☑ ", style="green").append(i.content + "\n", style="dim strike")
            elif i.status == "in_progress":
                lines.append("  ◐ ", style="yellow").append(i.content + "\n", style="bold")
            else:
                lines.append("  ☐ ", style="dim").append(i.content + "\n")
        self.console.print(lines, end="")

    def review_plan(self, plan: str) -> tuple[bool, str]:
        self.console.print(Panel(Markdown(plan), title="📋 Plan for your approval", border_style="magenta", expand=False))
        while True:
            answer = self._ask("  [y] approve and start  [n] keep planning › ", "magenta")
            if answer in ("y", "yes", ""):
                return True, ""
            if answer in ("n", "no"):
                return False, self._ask("  What should change? (Enter to skip) › ", "magenta", lower=False)

    # ---------- misc ----------
    def notice(self, message: str, style: str = "yellow") -> None:
        self.console.print(Text(message, style=style))

    def stats_line(self, stats: dict) -> None:
        if stats and not stats.get("stopped"):
            self.console.print(Text(
                f"  {stats.get('output_tokens', 0)} tokens · {stats.get('tokens_per_sec', 0)} tok/s · "
                f"{stats.get('total_secs', 0)}s", style="dim"))
