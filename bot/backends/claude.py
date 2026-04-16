"""Claude Code CLI backend."""

import json
from typing import Optional

from backends.base import CLIBackend, CLIResult


class ClaudeBackend(CLIBackend):
    name = "claude"
    display_name = "Claude Code"
    identity_filename = "CLAUDE.md"

    def __init__(self, bin_path: str, work_dir: str, timeout: int = 600):
        super().__init__(bin_path, work_dir, timeout)
        self.allowed_tools = "Bash,Read,Write,Edit,Glob,Grep,Agent"

    def build_command(self, prompt: str, session_id: Optional[str] = None) -> list[str]:
        cmd = [self.bin_path, "-p", prompt, "--output-format", "json"]
        cmd += ["--permission-mode", "dontAsk", "--allowedTools", self.allowed_tools]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

    def parse_output(self, raw: str) -> Optional[CLIResult]:
        """Claude outputs JSON array or JSONL with {type:result} entries."""
        # Try JSON array first (newer versions)
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for item in reversed(data):
                    if isinstance(item, dict) and item.get("type") == "result":
                        return CLIResult(
                            text=item.get("result", ""),
                            session_id=item.get("session_id"),
                            num_turns=item.get("num_turns", 0),
                            cost_usd=item.get("total_cost_usd", 0.0),
                            raw=item,
                        )
        except json.JSONDecodeError:
            pass

        # Fallback: line-by-line JSONL
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                data = json.loads(line)
                if "result" in data:
                    return CLIResult(
                        text=data["result"],
                        session_id=data.get("session_id"),
                        num_turns=data.get("num_turns", 0),
                        cost_usd=data.get("total_cost_usd", 0.0),
                        raw=data,
                    )
            except json.JSONDecodeError:
                continue
        return None
