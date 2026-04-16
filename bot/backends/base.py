"""Base class for CLI backends. Strategy pattern — each backend owns its logic."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("backend")


@dataclass
class CLIResult:
    """Unified result from any CLI backend."""
    text: str
    session_id: Optional[str] = None
    num_turns: int = 0
    cost_usd: float = 0.0
    raw: Optional[dict] = None


class CLIBackend:
    """Base class. Each backend fully owns its command building, parsing, auth."""

    name: str = "base"
    display_name: str = "Base"
    identity_filename: str = "IDENTITY.md"

    def __init__(self, bin_path: str, work_dir: str, timeout: int = 600):
        self.bin_path = bin_path
        self.work_dir = work_dir
        self.timeout = timeout

    async def execute(
        self,
        prompt: str,
        session_id: Optional[str] = None,
    ) -> Optional[CLIResult]:
        """Run CLI as subprocess and return parsed result."""
        cmd = self.build_command(prompt, session_id)
        logger.info(f"[{self.name}] Running: {' '.join(cmd[:5])}... session={session_id}")

        try:
            from pathlib import Path
            Path(self.work_dir).mkdir(parents=True, exist_ok=True)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.work_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout,
            )

        except asyncio.TimeoutError:
            logger.error(f"[{self.name}] Timed out after {self.timeout}s")
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return None
        except FileNotFoundError:
            logger.error(f"[{self.name}] Binary not found: {self.bin_path}")
            return None

        raw = stdout.decode("utf-8", errors="replace").strip()
        result = self.parse_output(raw)

        if result is None:
            error = stderr.decode("utf-8", errors="replace").strip()
            if error:
                logger.error(f"[{self.name}] stderr: {error[:300]}")
            if raw:
                logger.warning(f"[{self.name}] No structured output, wrapping raw stdout")
                return CLIResult(text=raw, session_id=session_id)
            return None

        logger.info(f"[{self.name}] Done: turns={result.num_turns}, session={result.session_id}")
        return result

    def build_command(self, prompt: str, session_id: Optional[str] = None) -> list[str]:
        """Override in subclass."""
        raise NotImplementedError

    def parse_output(self, raw: str) -> Optional[CLIResult]:
        """Override in subclass."""
        raise NotImplementedError

    def get_auth_instructions(self) -> str:
        """Human-readable auth setup instructions for install.sh."""
        return "No auth required."
