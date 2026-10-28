"""Decides whether a tool may run, asking the user when needed.

Modes:
  default       ask before every file change and every shell command
  accept-edits  file changes are allowed automatically; shell commands still ask
  auto          everything is allowed (use only in a throwaway folder)
"""

MODES = ("default", "accept-edits", "auto")


class Permissions:
    def __init__(self, mode: str = "default"):
        self.set_mode(mode)
        self.always_allowed: set[str] = set()  # tool names the user approved for the whole session

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose one of: {', '.join(MODES)}")
        self.mode = mode

    def needs_approval(self, tool) -> bool:
        if tool.permission is None or self.mode == "auto":
            return False
        if tool.permission == "edit" and self.mode == "accept-edits":
            return False
        return tool.name not in self.always_allowed

    def record(self, tool, answer: str) -> bool:
        """Store the user's answer ('y', 'n' or 'a' for always) and return whether to run."""
        if answer == "a":
            self.always_allowed.add(tool.name)
            return True
        return answer == "y"
