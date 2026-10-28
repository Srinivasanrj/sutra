"""Settings, loaded from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    ollama_url: str = "http://localhost:11434"
    model: str = "qwen3:1.7b"
    fast_model: str = "qwen3:1.7b"
    think: bool = False
    num_ctx: int = 8192
    max_steps: int = 25

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv()
        env = os.environ
        return cls(
            ollama_url=env.get("SUTRA_OLLAMA_URL", cls.ollama_url).rstrip("/"),
            model=env.get("SUTRA_MODEL", cls.model),
            fast_model=env.get("SUTRA_FAST_MODEL", cls.fast_model),
            think=_bool(env.get("SUTRA_THINK", "false")),
            num_ctx=int(env.get("SUTRA_NUM_CTX", cls.num_ctx)),
            max_steps=int(env.get("SUTRA_MAX_STEPS", cls.max_steps)),
        )
