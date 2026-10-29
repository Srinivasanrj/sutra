"""Agent-loop tests with a scripted fake model, so they run without Ollama."""

from sutra.agent import Agent
from sutra.config import Settings
from sutra.llm import Event, ToolCall
from sutra.permissions import Permissions
from sutra.tools import Workspace


class FakeLLM:
    """Returns pre-written replies, one per call, and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, model, messages, tools=None, think=False, num_ctx=8192):
        self.calls.append([dict(m) for m in messages])
        yield from self.replies.pop(0)


class FakeUI:
    def __init__(self, answer="y", plan_answers=None):
        self.answer = answer
        self.asked = []
        self.results = []
        self.plan_answers = list(plan_answers or [])  # (approved, feedback) per plan review
        self.plans = []
        self.todo_snapshots = []

    def review_plan(self, plan):
        self.plans.append(plan)
        return self.plan_answers.pop(0)

    def show_todos(self, items):
        self.todo_snapshots.append([(i.content, i.status) for i in items])

    def stream(self, events):
        text, calls = "", []
        for ev in events:
            if ev.kind == "text":
                text += ev.text
            elif ev.kind == "tool_calls":
                calls += ev.tool_calls
        return text, calls, {}

    def tool_start(self, call):
        pass

    def tool_result(self, call, result, ok):
        self.results.append((call.name, ok, result))

    def ask_permission(self, tool, call, preview):
        self.asked.append((tool.name, preview))
        return self.answer

    def notice(self, message, style="yellow"):
        self.results.append(("notice", False, message))


def reply_text(text):
    return [Event("text", text=text), Event("done")]


def reply_tool(name, **args):
    return [Event("tool_calls", tool_calls=[ToolCall(name, args)]), Event("done")]


def make_agent(tmp_path, replies, answer="y", mode="default", max_steps=25):
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    llm = FakeLLM(replies)
    ui = FakeUI(answer)
    agent = Agent(Settings(max_steps=max_steps), llm, Workspace(tmp_path), ui, Permissions(mode))
    return agent, llm, ui


def test_plain_answer_needs_one_round(tmp_path):
    agent, llm, _ = make_agent(tmp_path, [reply_text("Hello!")])
    assert agent.run("hi") == "Hello!"
    assert len(llm.calls) == 1


def test_tool_result_is_sent_back_to_the_model(tmp_path):
    agent, llm, _ = make_agent(tmp_path, [reply_tool("read_file", path="hello.py"), reply_text("It prints hi.")])
    assert agent.run("what does hello.py do?") == "It prints hi."
    second_round = llm.calls[1]
    assert second_round[-1]["role"] == "tool"
    assert "print('hi')" in second_round[-1]["content"]


def test_edit_asks_permission_and_applies(tmp_path):
    replies = [reply_tool("edit_file", path="hello.py", old_text="hi", new_text="hello"), reply_text("Done.")]
    agent, _, ui = make_agent(tmp_path, replies, answer="y")
    agent.run("say hello instead")
    assert ui.asked and ui.asked[0][0] == "edit_file" and "+print('hello')" in ui.asked[0][1]
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hello')\n"


def test_declined_edit_is_not_applied(tmp_path):
    replies = [reply_tool("edit_file", path="hello.py", old_text="hi", new_text="bye"), reply_text("OK.")]
    agent, llm, _ = make_agent(tmp_path, replies, answer="n")
    agent.run("change it")
    assert (tmp_path / "hello.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert "declined" in llm.calls[1][-1]["content"]


def test_accept_edits_mode_skips_the_question(tmp_path):
    replies = [reply_tool("write_file", path="new.txt", content="x"), reply_text("Done.")]
    agent, _, ui = make_agent(tmp_path, replies, mode="accept-edits")
    agent.run("make a file")
    assert ui.asked == [] and (tmp_path / "new.txt").exists()


def test_always_allow_remembers_the_answer(tmp_path):
    replies = [
        reply_tool("write_file", path="a.txt", content="1"),
        reply_tool("write_file", path="b.txt", content="2"),
        reply_text("Done."),
    ]
    agent, _, ui = make_agent(tmp_path, replies, answer="a")
    agent.run("make two files")
    assert len(ui.asked) == 1 and (tmp_path / "b.txt").exists()


def test_unknown_tool_and_missing_args_become_errors_for_the_model(tmp_path):
    replies = [reply_tool("delete_everything"), reply_tool("read_file"), reply_text("Sorry.")]
    agent, llm, _ = make_agent(tmp_path, replies)
    agent.run("go")
    assert "unknown tool" in llm.calls[1][-1]["content"]
    assert "missing required argument(s): path" in llm.calls[2][-1]["content"]


def test_max_steps_stops_endless_loops(tmp_path):
    replies = [reply_tool("list_files") for _ in range(3)]
    agent, llm, ui = make_agent(tmp_path, replies, max_steps=3)
    assert agent.run("loop forever") == ""
    assert len(llm.calls) == 3 and any("Stopped after 3 steps" in r[2] for r in ui.results)


def test_no_think_switch_is_sent_but_not_stored(tmp_path):
    agent, llm, _ = make_agent(tmp_path, [reply_text("<think>hmm</think>Hello!")])
    assert agent.run("hi") == "Hello!"
    assert llm.calls[0][-1]["content"] == "hi /no_think"
    assert agent.messages[1]["content"] == "hi"  # history stays clean


def test_project_memory_file_goes_into_system_prompt(tmp_path):
    (tmp_path / "SUTRA.md").write_text("Always use tabs.", encoding="utf-8")
    agent, _, _ = make_agent(tmp_path, [reply_text("ok")])
    assert "Always use tabs." in agent.messages[0]["content"]
