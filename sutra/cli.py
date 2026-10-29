"""Entry point: the interactive prompt and slash commands."""

import argparse
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from rich.panel import Panel
from rich.table import Table

from sutra import __version__
from sutra.agent import Agent
from sutra.config import Settings
from sutra.llm import LLMError, OllamaClient
from sutra.permissions import MODES, Permissions
from sutra.tools import Workspace
from sutra.ui import TerminalUI

HELP = {
    "/help": "Show this list",
    "/clear": "Forget the conversation and start fresh",
    "/model [name]": "Show or switch the model",
    "/think": "Turn the model's thinking on/off (on = smarter, slower)",
    "/mode [name]": f"Permission mode: {', '.join(MODES)}",
    "/plan": "Plan mode on/off: the agent only reads, then asks you to approve a plan",
    "/todos": "Show the current to-do list",
    "/tools": "List the tools the agent can use",
    "Shift+Tab": "Cycle modes: default → accept-edits → plan",
    "/exit": "Quit (or press Ctrl+D)",
}


def welcome(ui: TerminalUI, agent: Agent) -> None:
    body = (
        f"[bold]Sutra[/bold] v{__version__} · local AI coding agent\n"
        f"[dim]model[/dim] {agent.model}   [dim]thinking[/dim] {'on' if agent.think else 'off'}   "
        f"[dim]mode[/dim] {mode_label(agent)}\n"
        f"[dim]folder[/dim] {agent.ws.root}\n\n"
        "[dim]Ask anything about this folder. /help for commands, Ctrl+C to stop a reply.[/dim]"
    )
    ui.console.print(Panel(body, border_style="cyan", expand=False))


def handle_command(line: str, agent: Agent, ui: TerminalUI, llm: OllamaClient) -> bool:
    """Run a slash command. Returns False when the user wants to quit."""
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    if cmd in ("/exit", "/quit"):
        return False
    if cmd == "/help":
        table = Table(show_header=False, box=None)
        for k, v in HELP.items():
            table.add_row(f"[cyan]{k}[/cyan]", v)
        ui.console.print(table)
    elif cmd == "/clear":
        agent.reset()
        ui.notice("Conversation cleared.", "green")
    elif cmd == "/think":
        agent.think = not agent.think
        ui.notice(f"Thinking {'on' if agent.think else 'off'}.", "green")
    elif cmd == "/model":
        if not arg:
            ui.notice(f"Current model: {agent.model}. Installed: {', '.join(llm.list_models())}", "cyan")
        elif arg not in llm.list_models():
            ui.notice(f"Model '{arg}' is not installed. Run: ollama pull {arg}", "red")
        else:
            agent.model = arg
            ui.notice(f"Switched to {arg}.", "green")
    elif cmd == "/mode":
        if not arg:
            ui.notice(f"Current mode: {agent.permissions.mode}. Options: {', '.join(MODES)}", "cyan")
        else:
            try:
                agent.permissions.set_mode(arg)
                ui.notice(f"Mode set to {arg}.", "green")
            except ValueError as e:
                ui.notice(str(e), "red")
    elif cmd == "/plan":
        agent.plan_mode = not agent.plan_mode
        ui.notice(f"Plan mode {'on: the agent will only read and then propose a plan' if agent.plan_mode else 'off'}.", "magenta")
    elif cmd == "/todos":
        if agent.todos.items:
            ui.show_todos(agent.todos.items)
        else:
            ui.notice("No to-do list yet.", "cyan")
    elif cmd == "/tools":
        for t in agent.tools.values():
            tag = f" [yellow]({t.permission})[/yellow]" if t.permission else ""
            ui.console.print(f"[cyan]{t.name}[/cyan]{tag}: {t.description}")
    else:
        ui.notice(f"Unknown command {cmd}. Type /help.", "red")
    return True


def mode_label(agent: Agent) -> str:
    return "plan" if agent.plan_mode else agent.permissions.mode


def cycle_mode(agent: Agent) -> None:
    """Shift+Tab: default → accept-edits → plan → default."""
    if agent.plan_mode:
        agent.plan_mode = False
        agent.permissions.set_mode("default")
    elif agent.permissions.mode == "default":
        agent.permissions.set_mode("accept-edits")
    else:
        agent.permissions.set_mode("default")
        agent.plan_mode = True


def toolbar(agent: Agent):
    colors = {"plan": "ansimagenta", "accept-edits": "ansigreen", "auto": "ansired", "default": "ansicyan"}
    mode = mode_label(agent)
    return HTML(
        f" mode: <b><style fg='{colors.get(mode, 'ansicyan')}'>{mode}</style></b>"
        f"  ·  model: {agent.model}  ·  thinking: {'on' if agent.think else 'off'}"
        "  ·  <i>shift+tab to switch mode</i>"
    )


def run_once(agent: Agent, ui: TerminalUI, prompt: str) -> int:
    try:
        agent.run(prompt)
        ui.stats_line(agent.last_stats)
        return 0
    except LLMError as e:
        ui.notice(str(e), "red")
        return 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sutra", description="Local AI coding agent")
    parser.add_argument("folder", nargs="?", default=".", help="Project folder to work in (default: current)")
    parser.add_argument("-p", "--prompt", help="Run one request and exit (non-interactive)")
    parser.add_argument("--model", help="Model to use (overrides SUTRA_MODEL)")
    parser.add_argument("--mode", choices=MODES, default="default", help="Permission mode")
    parser.add_argument("--plan", action="store_true", help="Start in plan mode")
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.model:
        settings.model = args.model
    ui = TerminalUI()
    root = Path(args.folder)
    if not root.is_dir():
        ui.notice(f"Folder not found: {root}", "red")
        sys.exit(1)

    llm = OllamaClient(settings.ollama_url)
    try:
        installed = llm.list_models()
    except LLMError as e:
        ui.notice(str(e), "red")
        sys.exit(1)
    if settings.model not in installed:
        ui.notice(f"Model '{settings.model}' is not installed. Run: ollama pull {settings.model}", "red")
        sys.exit(1)

    agent = Agent(settings, llm, Workspace(root), ui, Permissions(args.mode))
    agent.plan_mode = args.plan

    if args.prompt:
        sys.exit(run_once(agent, ui, args.prompt))

    welcome(ui, agent)
    history = Path.home() / ".sutra" / "history"
    history.parent.mkdir(parents=True, exist_ok=True)
    keys = KeyBindings()

    @keys.add("s-tab")
    def _(event):
        cycle_mode(agent)
        event.app.invalidate()  # redraw the toolbar

    session = PromptSession(
        history=FileHistory(str(history)), key_bindings=keys, bottom_toolbar=lambda: toolbar(agent)
    )

    while True:
        try:
            line = session.prompt("› ").strip()
        except KeyboardInterrupt:
            continue
        except EOFError:
            break
        if not line:
            continue
        if line.startswith("/"):
            try:
                if not handle_command(line, agent, ui, llm):
                    break
            except LLMError as e:
                ui.notice(str(e), "red")
            continue
        try:
            run_once(agent, ui, line)
        except KeyboardInterrupt:
            ui.notice("[stopped]")
    ui.console.print("[dim]Bye![/dim]")


if __name__ == "__main__":
    main()
