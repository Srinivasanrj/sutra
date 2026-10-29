import pytest

from sutra.tools import ToolError, Workspace, build_tools


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n\nprint(add(1, 2))\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "util.py").write_text("NAME = 'sutra'\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "junk.py").write_text("ignore me\n", encoding="utf-8")
    return Workspace(tmp_path)


@pytest.fixture
def tools(ws):
    return build_tools(ws)


def test_read_file_numbers_lines(tools):
    out = tools["read_file"].run(path="app.py")
    assert "1| def add(a, b):" in out
    assert "lines 1-4 of 4" in out


def test_read_file_offset_and_limit(tools):
    out = tools["read_file"].run(path="app.py", offset=2, limit=1)
    assert "2|     return a + b" in out
    assert "offset=3" in out


def test_paths_outside_workspace_are_rejected(tools):
    with pytest.raises(ToolError, match="outside the workspace"):
        tools["read_file"].run(path="../secret.txt")


def test_list_files_skips_ignored_folders(tools):
    out = tools["list_files"].run()
    assert "app.py" in out and "src/util.py" in out
    assert ".venv" not in out


def test_list_files_pattern(tools):
    assert tools["list_files"].run(pattern="util*") == "src/util.py"


def test_search_code(tools):
    out = tools["search_code"].run(pattern="return a")
    assert out == "app.py:2: return a + b"


def test_search_code_bad_regex_falls_back_to_plain_text(tools):
    assert "app.py:1" in tools["search_code"].run(pattern="add(a,")


def test_edit_file_replaces_unique_text(tools, ws):
    tools["edit_file"].run(path="app.py", old_text="return a + b", new_text="return a + b + 0")
    assert "return a + b + 0" in (ws.root / "app.py").read_text(encoding="utf-8")


def test_edit_file_preview_is_a_diff_and_does_not_write(tools, ws):
    diff = tools["edit_file"].preview(path="app.py", old_text="return a + b", new_text="return b + a")
    assert "-    return a + b" in diff and "+    return b + a" in diff
    assert "return b + a" not in (ws.root / "app.py").read_text(encoding="utf-8")


def test_edit_file_text_not_found(tools):
    with pytest.raises(ToolError, match="not found"):
        tools["edit_file"].run(path="app.py", old_text="nope", new_text="x")


def test_edit_file_ambiguous_text(tools, ws):
    (ws.root / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    with pytest.raises(ToolError, match="appears 2 times"):
        tools["edit_file"].run(path="dup.py", old_text="x = 1", new_text="x = 2")


def test_read_file_strips_windows_bom(tools, ws):
    (ws.root / "bom.py").write_text("x = 1\n", encoding="utf-8-sig")
    assert "1| x = 1" in tools["read_file"].run(path="bom.py")


def test_write_file_creates_folders(tools, ws):
    tools["write_file"].run(path="new/deep/file.txt", content="hello\n")
    assert (ws.root / "new" / "deep" / "file.txt").read_text(encoding="utf-8") == "hello\n"


def test_run_command(tools):
    out = tools["run_command"].run(command="echo sutra-ok")
    assert out.startswith("exit code 0") and "sutra-ok" in out


def test_tools_needing_permission(tools):
    assert {n for n, t in tools.items() if t.permission} == {"edit_file", "write_file", "run_command"}
