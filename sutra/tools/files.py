"""File tools: read, list, search, edit and write."""

import difflib
import fnmatch
import os
import re
from pathlib import Path

from sutra.tools.base import IGNORED_DIRS, Tool, ToolError, Workspace, is_text_file, truncate

MAX_SEARCH_FILE_BYTES = 1_000_000


def _walk(ws: Workspace, start: Path):
    """Yield files under start, skipping ignored folders, in a stable order."""
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def make_diff(old: str, new: str, path: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}", n=2,
    )
    return "".join(diff) or "(no changes)"


def build_file_tools(ws: Workspace) -> list[Tool]:
    # ---------- read_file ----------
    def read_file(path: str, offset: int = 1, limit: int = 300) -> str:
        p = ws.resolve(path)
        if not p.is_file():
            raise ToolError(f"File not found: {path}")
        if not is_text_file(p):
            raise ToolError(f"{path} looks like a binary file; it can't be read as text.")
        lines = _read_text(p).splitlines()
        offset = max(1, int(offset))
        chunk = lines[offset - 1 : offset - 1 + int(limit)]
        width = len(str(offset + len(chunk)))
        body = "\n".join(f"{i:>{width}}| {line}" for i, line in enumerate(chunk, start=offset))
        end = offset + len(chunk) - 1
        header = f"{ws.rel(p)} (lines {offset}-{end} of {len(lines)})"
        if end < len(lines):
            header += f" - call again with offset={end + 1} to read more"
        return truncate(f"{header}\n{body}")

    # ---------- list_files ----------
    def list_files(path: str = ".", pattern: str = "*", max_results: int = 200) -> str:
        start = ws.resolve(path)
        if not start.is_dir():
            raise ToolError(f"Not a folder: {path}")
        found = []
        for f in _walk(ws, start):
            rel = ws.rel(f)
            if fnmatch.fnmatch(f.name, pattern) or fnmatch.fnmatch(rel, pattern):
                found.append(rel)
                if len(found) >= max_results:
                    found.append(f"... (stopped at {max_results} files; narrow the path or pattern)")
                    break
        return "\n".join(found) if found else f"No files matching '{pattern}' in {ws.rel(start)}"

    # ---------- search_code ----------
    def search_code(pattern: str, path: str = ".", file_pattern: str = "*", max_results: int = 50) -> str:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error:
            rx = re.compile(re.escape(pattern), re.IGNORECASE)  # treat it as plain text
        start = ws.resolve(path)
        files = [start] if start.is_file() else _walk(ws, start)
        hits = []
        for f in files:
            if not fnmatch.fnmatch(f.name, file_pattern):
                continue
            try:
                if f.stat().st_size > MAX_SEARCH_FILE_BYTES or not is_text_file(f):
                    continue
            except OSError:
                continue
            for n, line in enumerate(_read_text(f).splitlines(), start=1):
                if rx.search(line):
                    hits.append(f"{ws.rel(f)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        hits.append(f"... (stopped at {max_results} matches)")
                        return "\n".join(hits)
        return "\n".join(hits) if hits else f"No matches for '{pattern}'"

    # ---------- edit_file ----------
    def _plan_edit(path: str, old_text: str, new_text: str) -> tuple[Path, str, str]:
        p = ws.resolve(path)
        if not p.is_file():
            raise ToolError(f"File not found: {path}. Use write_file to create a new file.")
        content = _read_text(p)
        count = content.count(old_text)
        if not old_text:
            raise ToolError("old_text is empty. Copy the exact lines you want to replace.")
        if count == 0:
            raise ToolError(
                f"old_text was not found in {path}. Read the file again and copy the text exactly "
                "(without the line-number prefix)."
            )
        if count > 1:
            raise ToolError(f"old_text appears {count} times in {path}. Include more surrounding lines so it is unique.")
        return p, content, content.replace(old_text, new_text, 1)

    def edit_preview(path: str, old_text: str, new_text: str) -> str:
        p, old, new = _plan_edit(path, old_text, new_text)
        return make_diff(old, new, ws.rel(p))

    def edit_file(path: str, old_text: str, new_text: str) -> str:
        p, old, new = _plan_edit(path, old_text, new_text)
        p.write_text(new, encoding="utf-8")
        added = new.count("\n") - old.count("\n")
        return f"Edited {ws.rel(p)} ({'+' if added >= 0 else ''}{added} lines)."

    # ---------- write_file ----------
    def write_preview(path: str, content: str) -> str:
        p = ws.resolve(path)
        if p.is_file():
            return make_diff(_read_text(p), content, ws.rel(p))
        return f"New file {ws.rel(p)} ({len(content.splitlines())} lines)\n" + "\n".join(
            f"+{line}" for line in content.splitlines()[:40]
        )

    def write_file(path: str, content: str) -> str:
        p = ws.resolve(path)
        existed = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"{'Overwrote' if existed else 'Created'} {ws.rel(p)} ({len(content.splitlines())} lines)."

    return [
        Tool(
            name="read_file",
            description="Read a text file. Output lines are prefixed with line numbers like '12| '.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace"},
                    "offset": {"type": "integer", "description": "First line to read (1-based)", "default": 1},
                    "limit": {"type": "integer", "description": "How many lines to read", "default": 300},
                },
                "required": ["path"],
            },
            run=read_file,
        ),
        Tool(
            name="list_files",
            description="List files in a folder (recursive). Optional glob pattern such as '*.py'.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder to list", "default": "."},
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.py'", "default": "*"},
                },
                "required": [],
            },
            run=list_files,
        ),
        Tool(
            name="search_code",
            description="Search file contents with a regular expression (case-insensitive). Returns file:line: text.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex or plain text to find"},
                    "path": {"type": "string", "description": "Folder or file to search", "default": "."},
                    "file_pattern": {"type": "string", "description": "Only files matching this glob, e.g. '*.py'", "default": "*"},
                },
                "required": ["pattern"],
            },
            run=search_code,
        ),
        Tool(
            name="edit_file",
            description=(
                "Replace one exact snippet in an existing file. old_text must match the file exactly "
                "(copy it from read_file without the line-number prefix) and appear only once."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "Exact text to replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_text", "new_text"],
            },
            run=edit_file,
            permission="edit",
            preview=edit_preview,
        ),
        Tool(
            name="write_file",
            description="Create a new file, or completely replace an existing one, with the given content.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "Full file content"},
                },
                "required": ["path", "content"],
            },
            run=write_file,
            permission="edit",
            preview=write_preview,
        ),
    ]
