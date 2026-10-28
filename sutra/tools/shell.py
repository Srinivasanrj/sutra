"""The run_command tool: runs a shell command inside the workspace."""

import os
import subprocess
import sys

from sutra.tools.base import Tool, ToolError, Workspace, truncate

MAX_TIMEOUT = 600


def _shell_args(command: str) -> list[str]:
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
    return ["bash", "-lc", command]


def build_shell_tool(ws: Workspace) -> Tool:
    def preview(command: str, timeout: int = 60) -> str:
        return command

    def run_command(command: str, timeout: int = 60) -> str:
        if not command.strip():
            raise ToolError("command is empty.")
        timeout = max(1, min(int(timeout), MAX_TIMEOUT))
        try:
            proc = subprocess.run(
                _shell_args(command), cwd=ws.root, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout}s: {command}")
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
        return truncate(f"exit code {proc.returncode}\n{out.strip() or '(no output)'}")

    shell_name = "PowerShell" if sys.platform == "win32" else "bash"
    return Tool(
        name="run_command",
        description=(
            f"Run a {shell_name} command in the workspace folder (e.g. run tests, install packages, git status). "
            "Returns the exit code and output. Avoid commands that wait for input."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run"},
                "timeout": {"type": "integer", "description": "Seconds before it is stopped", "default": 60},
            },
            "required": ["command"],
        },
        run=run_command,
        permission="shell",
        preview=preview,
    )
