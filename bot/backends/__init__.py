"""Backend factory."""

from backends.base import CLIBackend, CLIResult
from backends.qwen import QwenBackend
from backends.claude import ClaudeBackend
from backends.codex import CodexBackend

BACKENDS = {
    "qwen": QwenBackend,
    "claude": ClaudeBackend,
    "codex": CodexBackend,
}


def get_backend(name: str, bin_path: str, work_dir: str, timeout: int = 600) -> CLIBackend:
    cls = BACKENDS.get(name)
    if not cls:
        available = ", ".join(BACKENDS.keys())
        raise ValueError(f"Unknown backend: {name}. Available: {available}")
    return cls(bin_path=bin_path, work_dir=work_dir, timeout=timeout)
