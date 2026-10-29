"""Plan mode and the to-do list."""

import pytest

from sutra.agent import Agent
from sutra.config import Settings
from sutra.permissions import Permissions, UserStop
from sutra.tools import ToolError, Workspace
from sutra.tools.planning import TodoList, steps_from_plan
from tests.test_agent import FakeLLM, FakeUI, reply_text, reply_tool

PLAN = "Plan:\n1. Read hello.py\n2. Change 'hi' to 'hello'\n3. Run python hello.py"


def make(tmp_path, replies, plan_answers=None, answer="y"):
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    llm, ui = FakeLLM(replies), FakeUI(answer, plan_answers)
    agent = Agent(Settings(), llm, Workspace(tmp_path), ui, Permissions())
    return agent, llm, ui


# ---------- to-do list ----------

def test_todo_list_accepts_plain_strings_and_loose_statuses():
    todos = TodoList()
    warnings = todos.replace(["write code", {"content": "test it", "status": "Done"}, {"content": "x", "status": "??"}])
    assert [(i.content, i.status) for i in todos.items] == [
        ("write code", "pending"), ("test it", "completed"), ("x", "pending")]
    assert any("unknown status" in w for w in warnings)


def test_todo_list_keeps_only_one_in_progress():
    todos = TodoList()
    warnings = todos.replace([{"content": "a", "status": "in_progress"}, {"content": "b", "status": "in_progress"}])
    assert [i.status for i in todos.items] == ["in_progress", "pending"]
    assert warnings


def test_todo_list_rejects_empty():
    with pytest.raises(ToolError):
        TodoList().replace([])


def test_steps_from_plan_reads_numbers_and_bullets():
    assert steps_from_plan("Intro\n1. one\n2) two\n- three\n* four\nnot a step") == ["one", "two", "three", "four"]


def test_steps_from_plan_splits_a_numbered_list_written_on_one_line():
    assert steps_from_plan("1. Read calc.py. 2. Add subtract. 3. Add multiply.") == [
        "Read calc.py.", "Add subtract.", "Add multiply."]


def test_todo_write_tool_updates_list_and_ui(tmp_path):
    replies = [reply_tool("todo_write", todos=[{"content": "step 1", "status": "in_progress"}]), reply_text("ok")]
    agent, llm, ui = make(tmp_path, replies)
    agent.run("do a big task")
    assert agent.todos.items[0].content == "step 1"
    assert ui.todo_snapshots == [[("step 1", "in_progress")]]
    assert "0/1 done" in llm.calls[1][-1]["content"]


# ---------- plan mode ----------

def last_tool_result(messages):
    return next(m["content"] for m in reversed(messages) if m["role"] == "tool")


def test_plan_mode_hides_and_blocks_edit_tools(tmp_path):
    replies = [reply_tool("write_file", path="x.txt", content="no"), reply_tool("exit_plan_mode", plan=PLAN),
               reply_text("ok")]
    agent, llm, _ = make(tmp_path, replies, plan_answers=[(True, "")])
    agent.plan_mode = True
    offered = {t.name for t in agent.available_tools()}
    assert offered == {"read_file", "list_files", "search_code", "exit_plan_mode"}
    agent.run("make a file")  # the model asks for write_file anyway
    assert not (tmp_path / "x.txt").exists()
    assert "not allowed in plan mode" in last_tool_result(llm.calls[1])


def test_plan_mode_reminder_is_sent_but_not_stored(tmp_path):
    agent, llm, _ = make(tmp_path, [reply_tool("exit_plan_mode", plan=PLAN), reply_text("ok")],
                         plan_answers=[(True, "")])
    agent.plan_mode = True
    agent.run("plan something")
    assert "PLAN MODE IS ON" in llm.calls[0][-1]["content"]
    assert all("PLAN MODE IS ON" not in m["content"] for m in agent.messages)


def test_plan_written_as_text_is_still_shown_for_approval(tmp_path):
    replies = [reply_text(PLAN), reply_text("Starting now.")]
    agent, llm, ui = make(tmp_path, replies, plan_answers=[(True, "")])
    agent.plan_mode = True
    assert agent.run("say hello") == "Starting now."
    assert ui.plans == [PLAN] and agent.plan_mode is False
    assert "approved the plan" in llm.calls[1][-1]["content"]


def test_input_ending_during_plan_review_stops_cleanly(tmp_path):
    class EndedUI(FakeUI):
        def review_plan(self, plan):
            raise UserStop("No more input, so the task was stopped.")

    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    ui = EndedUI()
    agent = Agent(Settings(), FakeLLM([reply_tool("exit_plan_mode", plan=PLAN)]), Workspace(tmp_path), ui)
    agent.plan_mode = True
    assert agent.run("say hello") == ""
    assert any("No more input" in r[2] for r in ui.results)


def test_prompt_labels_are_shown_literally_and_bom_is_ignored():
    import io
    from rich.console import Console
    from sutra.ui import TerminalUI

    out = io.StringIO()
    ui = TerminalUI(Console(file=out, force_terminal=False, width=100))
    ui.console.input = lambda prompt: (ui.console.print(prompt, end=""), "﻿Y")[1]
    assert ui.review_plan("1. do it") == (True, "")
    assert "[y] approve and start" in out.getvalue()


def test_empty_reply_in_plan_mode_gets_one_nudge(tmp_path):
    replies = [reply_text(""), reply_tool("exit_plan_mode", plan=PLAN), reply_text("ok")]
    agent, llm, ui = make(tmp_path, replies, plan_answers=[(True, "")])
    agent.plan_mode = True
    agent.run("say hello")
    assert any("call exit_plan_mode" in m["content"] for m in llm.calls[1] if m["role"] == "user")
    assert len(ui.plans) == 1


def test_exit_plan_mode_is_hidden_outside_plan_mode(tmp_path):
    agent, _, _ = make(tmp_path, [reply_text("ok")])
    assert "exit_plan_mode" not in {t.name for t in agent.available_tools()}
    assert "not in plan mode" in agent.execute(reply_tool("exit_plan_mode", plan="1. x")[0].tool_calls[0])


def test_approved_plan_turns_into_todos_and_unlocks_edits(tmp_path):
    replies = [
        reply_tool("read_file", path="hello.py"),
        reply_tool("exit_plan_mode", plan=PLAN),
        reply_tool("edit_file", path="hello.py", old_text="hi", new_text="hello"),
        reply_text("Done."),
    ]
    agent, llm, ui = make(tmp_path, replies, plan_answers=[(True, "")])
    agent.plan_mode = True
    agent.run("say hello instead of hi")
    assert agent.plan_mode is False
    assert ui.todo_snapshots[0] == [
        ("Read hello.py", "in_progress"), ("Change 'hi' to 'hello'", "pending"), ("Run python hello.py", "pending")]
    assert "approved the plan" in last_tool_result(llm.calls[2])
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hello')\n"


def test_rejected_plan_sends_feedback_and_stays_in_plan_mode(tmp_path):
    replies = [
        reply_tool("exit_plan_mode", plan=PLAN),
        reply_tool("exit_plan_mode", plan=PLAN + "\n4. Add a test"),
        reply_text("Plan approved, starting."),
    ]
    agent, llm, ui = make(tmp_path, replies, plan_answers=[(False, "also add a test"), (True, "")])
    agent.plan_mode = True
    agent.run("say hello")
    assert "Their feedback: also add a test" in last_tool_result(llm.calls[1])
    assert len(ui.plans) == 2 and agent.plan_mode is False
