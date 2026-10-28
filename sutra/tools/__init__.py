"""All tools the agent can use, collected in one registry."""

from sutra.tools.base import Tool, ToolError, Workspace
from sutra.tools.files import build_file_tools
from sutra.tools.shell import build_shell_tool


def build_tools(ws: Workspace) -> dict[str, Tool]:
    tools = build_file_tools(ws) + [build_shell_tool(ws)]
    return {t.name: t for t in tools}


__all__ = ["Tool", "ToolError", "Workspace", "build_tools"]
