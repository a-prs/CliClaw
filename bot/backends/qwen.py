"""Qwen Code CLI backend."""

import json
from typing import Optional

from backends.base import CLIBackend, CLIResult


class QwenBackend(CLIBackend):
    name = "qwen"
    display_name = "Qwen Code"
    identity_filename = "QWEN.md"

    def build_command(self, prompt: str, session_id: Optional[str] = None) -> list[str]:
        cmd = [self.bin_path, "-p", prompt, "--output-format", "json"]
        cmd += ["--yolo"]
        cmd += ["--auth-type", "qwen-oauth"]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

    def parse_output(self, raw: str) -> Optional[CLIResult]:
        """Qwen outputs JSON array: [{type:system}, {type:assistant}, ..., {type:result}]"""
        # Try JSON array first
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

        # Fallback: line-by-line
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line.startswith(("{", "[")):
                continue
            try:
                data = json.loads(line)
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
                        num_turns=data.get("num_turns", 0),
                        raw=data,
                    )
            except json.JSONDecodeError:
                continue
        return None
