# Sutra

A terminal AI coding agent, in the style of Claude Code, that runs entirely on a **local LLM** through [Ollama](https://ollama.com). No API keys, no cost, works offline. It is designed to run well on a laptop CPU.

> 🚧 Work in progress. Core agent is done; advanced features are being added.

## Features (so far)

- 💬 Streaming chat in the terminal, with Markdown and code highlighting
- 🛠️ Tools: `read_file`, `list_files`, `search_code`, `edit_file`, `write_file`, `run_command`
- 🔁 Agent loop: the model calls tools, sees the results and keeps going until the task is done
- 🔒 Permission system: every file change shows a diff and every shell command asks first (`/mode` to change)
- 🧱 Workspace sandbox: tools cannot touch files outside the project folder
- 🧠 Project memory: put notes in `SUTRA.md` and the agent reads them on every session
- 💭 Thinking on/off (`/think`) for qwen3 models

## Quick start

```bash
ollama pull qwen3:4b
uv sync
uv run sutra            # work in the current folder
uv run sutra path/to/project
uv run sutra -p "explain what main.py does"   # one-shot mode
```

## Commands

| Command | What it does |
| --- | --- |
| `/help` | List commands |
| `/clear` | Start a fresh conversation |
| `/model [name]` | Show or switch the Ollama model |
| `/think` | Toggle the model's reasoning |
| `/mode [default\|accept-edits\|auto]` | Permission mode |
| `/tools` | List tools |
| `/exit` | Quit |

## Running the tests

```bash
uv run pytest
```
