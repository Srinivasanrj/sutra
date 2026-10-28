"""The Tool type, the workspace sandbox, and helpers shared by all tools."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Folders that are never worth showing to the model.
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build", ".sutra", ".idea", ".vscode",
}

MAX_OUTPUT_CHARS = 12_000


class ToolError(Exception):
    """An expected failure (bad path, text not found, ...). Sent back to the model as a message."""


class Workspace:
    """The folder the agent works in. Every path the model gives is checked against it."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def resolve(self, path: str) -> Path:
        p = Path(path or ".")
        full = (p if p.is_absolute() else self.root / p).resolve()
        if full != self.root and self.root not in full.parents:
            raise ToolError(f"'{path}' is outside the workspace ({self.root}). Use a path inside it.")
        return full

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(path)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema for the arguments
    run: Callable[..., str]
    # None = always allowed; "edit" = changes files; "shell" = runs commands.
    permission: str | None = None
    # For "edit"/"shell" tools: builds what the user sees before approving (a diff, the command).
    preview: Callable[..., str] | None = None

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated: {len(text) - limit} more characters]"


def is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" not in f.read(2048)
    except OSError:
        return False
