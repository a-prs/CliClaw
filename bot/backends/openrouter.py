"""OpenRouter API backend — access to 1000+ models via HTTP."""

import base64
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

import httpx

from backends.base import APIBackend, CLIResult

logger = logging.getLogger("backend.openrouter")

API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "meta-llama/llama-4-maverick:free"


class OpenRouterBackend(APIBackend):
    name = "openrouter"
    display_name = "OpenRouter"

    def __init__(self, api_key: str, work_dir: str, timeout: int = 120, model: str = ""):
        super().__init__(api_key, work_dir, timeout, model or DEFAULT_MODEL)

    async def execute(
        self,
        prompt: str,
        session_id: Optional[str] = None,
    ) -> Optional[CLIResult]:
        """Send prompt to OpenRouter API with context from DB + memory."""
        session_id = session_id or str(uuid.uuid4())

        messages = self._build_messages(prompt, session_id)

        logger.info(f"[openrouter] Model={self.model}, messages={len(messages)}, session={session_id[:8]}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{API_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "HTTP-Referer": "https://github.com/a-prs/CliClaw",
                        "X-Title": "CliClaw",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                    },
                )
        except httpx.TimeoutException:
            logger.error("[openrouter] Request timed out")
            return CLIResult(text="Timeout — model took too long to respond.", session_id=session_id)
        except httpx.ConnectError as e:
            logger.error(f"[openrouter] Connection error: {e}")
            return CLIResult(text="Connection error. Check internet.", session_id=session_id)

        if response.status_code == 429:
            logger.warning("[openrouter] Rate limited")
            return CLIResult(text="Rate limit. Wait a minute and try again.", session_id=session_id)

        if response.status_code == 402:
            logger.error("[openrouter] Payment required")
            return CLIResult(text="Credits exhausted. Use a free model or top up.", session_id=session_id)

        if response.status_code != 200:
            error_text = response.text[:300]
            logger.error(f"[openrouter] API error {response.status_code}: {error_text}")
            return CLIResult(text=f"API error {response.status_code}. Try another model.", session_id=session_id)

        data = response.json()

        # Extract response text
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            logger.error(f"[openrouter] Unexpected response format: {data}")
            return CLIResult(text="Unexpected API response.", session_id=session_id)

        # Extract cost if available
        cost = 0.0
        usage = data.get("usage", {})
        if usage:
            # OpenRouter returns total_cost in some responses
            cost = data.get("total_cost", 0.0)

        logger.info(f"[openrouter] Done: {len(text)} chars, cost=${cost:.4f}")

        return CLIResult(
            text=text,
            session_id=session_id,
            num_turns=1,
            cost_usd=cost,
            raw=data,
        )

    def _build_messages(self, prompt: str, session_id: str) -> list[dict]:
        """Build OpenAI-compatible messages array."""
        messages = []

        # 1. System message
        system_parts = []

        # Core identity
        identity = self._load_identity()
        if identity:
            system_parts.append(identity)

        # API-specific instructions: NO tool use, NO JSON commands
        system_parts.append(
            "IMPORTANT RULES:\n"
            "- You are a TEXT-ONLY assistant. You CANNOT read files, run commands, or use tools.\n"
            "- NEVER output JSON tool calls like {\"type\": \"read\"}. Just answer in plain text.\n"
            "- When user asks to create a reminder/schedule, output the schedule as a JSON block "
            "inside your text response. The bot will parse it and save automatically.\n"
            "- When user asks about existing reminders, check the schedules section below."
        )

        # User facts from memory
        memory = self._get_memory_context(prompt)
        if memory:
            system_parts.append(
                "VERIFIED USER FACTS (use these to answer questions):\n\n"
                f"{memory}"
            )

        # Current schedules (so model can list them without file access)
        schedules_content = self._load_schedules()
        if schedules_content:
            system_parts.append(f"CURRENT SCHEDULES:\n{schedules_content}")

        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        # 2. History: last 5 messages from DB
        try:
            from db import get_recent_messages
            history = get_recent_messages(session_id, limit=5)
            messages.extend(history)
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")

        # 3. Current user message (with optional image)
        user_content = self._build_user_content(prompt)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, prompt: str):
        """Build user message content. Handle @image_path for vision models."""
        # Check for image reference: "text @/path/to/image.jpg"
        image_match = re.search(r'@(/\S+\.(?:jpg|jpeg|png|gif|webp))', prompt)
        if not image_match:
            return prompt

        image_path = image_match.group(1)
        text = prompt[:image_match.start()].strip()
        if not text:
            text = "Describe this image"

        # Read and encode image (limit 1MB to avoid OOM)
        try:
            path = Path(image_path)
            if not path.exists() or path.stat().st_size > 1_000_000:
                logger.warning(f"Image too large or missing: {image_path}")
                return prompt  # Fall back to text-only

            b64 = base64.b64encode(path.read_bytes()).decode()
            suffix = path.suffix.lstrip(".")
            mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(suffix, "jpeg")

            return [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
            ]
        except Exception as e:
            logger.warning(f"Failed to encode image: {e}")
            return prompt

    def _load_identity(self) -> str | None:
        """Read IDENTITY.md from workspace."""
        identity_path = Path(self.work_dir) / "IDENTITY.md"
        if identity_path.exists():
            return identity_path.read_text(encoding="utf-8")[:2000]
        return None

    def _load_schedules(self) -> str | None:
        """Read schedules.json so model can list reminders without file access."""
        schedules_path = Path(self.work_dir) / "schedules.json"
        if schedules_path.exists():
            try:
                content = schedules_path.read_text(encoding="utf-8").strip()
                if content and content != "[]":
                    return content[:1500]
            except OSError:
                pass
        return None

    def _get_memory_context(self, prompt: str) -> str | None:
        """Search memory vault for relevant context."""
        try:
            from memory.hooks import get_memory_context
            return get_memory_context(prompt)
        except Exception as e:
            logger.warning(f"Memory search failed: {e}")
            return None

    # --- Model management ---

    @staticmethod
    async def fetch_free_models(api_key: str) -> list[dict]:
        """Query OpenRouter for free models, sorted by context length."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{API_BASE}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            if resp.status_code != 200:
                return []

            models = resp.json().get("data", [])
            free = [m for m in models if ":free" in m.get("id", "")]
            free.sort(key=lambda m: m.get("context_length", 0), reverse=True)
            return free[:20]
        except Exception as e:
            logger.error(f"Failed to fetch models: {e}")
            return []
