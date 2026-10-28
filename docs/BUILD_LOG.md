# Sutra – Build Log

## Stage 1: Core agent (done, 28 Oct 2026)

**Result:** a working Claude Code–style agent. It chats in the terminal, reads and searches code, edits files (showing a diff first), runs commands (asking first), and loops until the task is done. **24 automated tests pass**, and a live test fixed a real bug in 38 seconds on this laptop.

### Your laptop and the model choice

| Item | Value | What it means |
| --- | --- | --- |
| CPU | Intel i7-1195G7, 4 cores / 8 threads | The model runs on the CPU |
| GPU | Intel Iris Xe (no NVIDIA) | Ollama can't use it, so CPU only |
| RAM | 16 GB, but usually under 1 GB free | The main speed limit |
| Model | **qwen3:1.7b** (default) | 31 tokens/sec when RAM is free, about 10 when it's full |
| Bigger model | qwen3:4b | Smarter, but timed out with full RAM. Use `/model qwen3:4b` after closing Chrome and ChatGPT |

**Measured:** `qwen3:4b` ran at 0.1 tokens/sec when Windows was swapping memory to disk (21.4 GB in use on a 16 GB machine). `qwen3:1.7b` ran at 31.5 tokens/sec.

### Files and what each one does

| File | Job |
| --- | --- |
| `sutra/config.py` | Reads settings from `.env` (model, Ollama address, context size, step limit) |
| `sutra/llm.py` | Talks to Ollama's `/api/chat`, streams the reply, and splits it into events: thinking, text, tool calls, done. Turns connection problems into friendly messages |
| `sutra/tools/base.py` | The `Tool` type, the **workspace sandbox** (blocks paths outside the project), output truncation |
| `sutra/tools/files.py` | `read_file`, `list_files`, `search_code`, `edit_file`, `write_file`, plus diff previews |
| `sutra/tools/shell.py` | `run_command` (PowerShell on Windows), with a timeout |
| `sutra/permissions.py` | Decides whether to ask you: modes `default`, `accept-edits`, `auto`, plus "always allow" |
| `sutra/agent.py` | **The agent loop**: the brain of the project |
| `sutra/ui.py` | Terminal display: live Markdown, spinner, tool lines, diff panels, y/n/a prompts |
| `sutra/cli.py` | Starts everything; the `›` prompt, slash commands, and `-p` one-shot mode |
| `tests/` | 24 tests. The agent tests use a **fake model**, so they run in under a second without Ollama |

### How one request flows

```
You type a request
   │
   ▼
agent.run()  ──►  send messages + tool list to the model (llm.py)
   ▲                         │
   │                         ▼
   │              model replies: text, or "call read_file(path=...)"
   │                         │
   │          ┌──── no tool calls? ───► show the answer, done
   │          │
   │          ▼
   │   for each tool call:
   │     check the arguments are valid
   │     ask permission if it edits files or runs a command (diff shown first)
   │     run the tool, catching errors
   └──── add the result to messages as {"role": "tool"} and loop again
         (stops after 25 rounds as a safety limit)
```

### Key ideas (learn these for interviews)

1. **The model has no memory.** `agent.messages` *is* the memory. The whole list is sent on every round, and `/clear` resets it.
2. **Tool calling.** Each tool has a name, a description and a JSON Schema for its arguments. The model doesn't run anything itself: it returns *"please call X with these arguments"*, and our code runs it.
3. **The agent loop.** Model → tool → result → model, repeated until the model replies with plain text. `max_steps` stops infinite loops.
4. **Errors go back to the model, not to you.** If `edit_file` can't find the text, the model receives *"old_text was not found… read the file again"* and fixes its own mistake. This matters a lot with small models.
5. **Exact-match editing.** `edit_file` replaces one snippet that must appear **exactly once**. That's safer than rewriting whole files, and it's the same approach Claude Code uses.
6. **Permissions with a preview.** Before any change, you see the actual diff. The preview is computed without writing anything.
7. **Sandbox.** `Workspace.resolve()` rejects any path that escapes the project folder (e.g. `../../Windows`).
8. **Small-model fixes.** qwen3 kept reasoning inside its answer even with thinking off, so Sutra adds qwen3's `/no_think` switch to the request (not to the saved history) and strips any `<think>` blocks. Unknown arguments are dropped, and missing ones produce a clear error.
9. **Testing an AI system without the AI.** `FakeLLM` replays scripted replies, so the loop, permissions and error handling are tested exactly and quickly.

### Likely interview questions, with short answers

| Question | Answer |
| --- | --- |
| How does your agent decide when it's finished? | When the model replies without asking for any tool. There's also a 25-step safety limit. |
| What happens if the model edits the wrong place? | The edit needs an exact, unique match, or it fails with a message telling the model to re-read the file. The user also sees the diff and can say no. |
| How do you stop it deleting system files? | Every path goes through a workspace sandbox, and shell commands need approval unless the user picks `auto` mode. |
| Why Ollama's native API instead of the OpenAI format? | It lets us turn thinking on and off (a big speed win on CPU) and returns tool arguments as parsed JSON. |
| How did you test it? | 24 pytest tests: tools on temporary folders, and the agent loop with a fake model, covering permissions, errors and step limits. Plus live tests with qwen3. |
| What was the hardest problem? | Speed on a CPU with little free RAM. I measured both models (4B: 0.1 tok/s while swapping; 1.7B: 31 tok/s) and made 1.7B the default. |

## Next stages

| Stage | Feature | Why it's impressive |
| --- | --- | --- |
| 2 | Plan mode + live to-do list | The agent plans first and you approve. Shows agent design |
| 3 | Checkpoints + `/undo` | Every edit is snapshotted and can be rolled back |
| 4 | Saved sessions + context compaction | Long chats are summarised to fit a small model's context window |
| 5 | Tree-sitter repo map | The model gets a compact map of functions and classes instead of whole files |
| 6 | Sub-agents + MCP client | Helper agents for searching; connect external tools through MCP |
| 7 | 30-task benchmark + reliability fixes | Measured success rate: the numbers for your resume |
| 8 | README, demo GIF, GitHub | Shipping it |
