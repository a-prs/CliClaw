"""OpenAI Codex CLI backend."""

import json
from typing import Optional

from backends.base import CLIBackend, CLIResult


class CodexBackend(CLIBackend):
    name = "codex"
    display_name = "Codex CLI"
    identity_filename = "IDENTITY.md"

    def build_command(self, prompt: str, session_id: Optional[str] = None) -> list[str]:
        cmd = [self.bin_path, "-p", prompt, "--output-format", "json"]
        cmd += ["--full-auto"]
        # Codex CLI may not support --resume
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

    def parse_output(self, raw: str) -> Optional[CLIResult]:
        """Codex CLI output format — adapt as needed."""
        # Try JSON array
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                for item in reversed(data):
                    if isinstance(item, dict) and item.get("type") == "result":
                        return CLIResult(
                            text=item.get("result", ""),
                            session_id=item.get("session_id"),
                            num_turns=item.get("num_turns", 0),
                            raw=item,
                        )
            elif isinstance(data, dict) and "result" in data:
                return CLIResult(
                    text=data["result"],
                    session_id=data.get("session_id"),
                    raw=data,
                )
        except json.JSONDecodeError:
            pass

        # Fallback: line-by-line
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
                        raw=data,
                    )
            except json.JSONDecodeError:
                continue
        return None
