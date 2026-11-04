"""Optional MiniMax API access for the context module.

The development assistant that performs the code change is a MiniMax model.  This
module makes that participation reproducible: it sends a prompt to the MiniMax
chat completion API and appends the request and the response to a JSON Lines
log, so a reviewer can read exactly what the model was given and what it
answered.

The API is optional.  The recorded development session in ``docs/dev-session.md``
is the primary evidence; the API path exists so the same flow can be replayed
with a MiniMax Token Plan key.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minimax_ctx.config import Settings

LOG_NAME = "minimax-api-log.jsonl"


@dataclass(frozen=True, slots=True)
class MiniMaxResult:
    """One recorded MiniMax call."""

    model: str
    prompt: str
    answer: str
    usage: dict[str, Any]
    log_path: str


class MiniMaxClient:
    """A thin, logged wrapper around the MiniMax chat completion API."""

    def __init__(self, settings: Settings, *, log_dir: Path | None = None) -> None:
        self._settings = settings
        self._log_dir = log_dir or (settings.workspace / "logs")

    @property
    def available(self) -> bool:
        return bool(self._settings.minimax_api_key)

    def _endpoint(self) -> str:
        return f"{self._settings.minimax_base_url.rstrip('/')}/chat/completions"

    def complete(self, prompt: str, *, system: str = "", max_tokens: int = 4096) -> MiniMaxResult:
        """Send one prompt to MiniMax and record the exchange."""

        if not self.available:
            raise RuntimeError("MINIMAX_API_KEY is not set; the MiniMax API path is optional")

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._settings.minimax_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            self._endpoint(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._settings.minimax_api_key}",
                "Content-Type": "application/json",
                "User-Agent": "minimax-sdd-context-module",
            },
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")
            self._append_log(
                {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "endpoint": self._endpoint(),
                    "model": payload["model"],
                    "messages": messages,
                    "error": f"HTTP {error.code}: {body[:2000]}",
                    "elapsed_sec": round(time.time() - started, 3),
                }
            )
            raise

        data = json.loads(raw)
        answer = ""
        choices = data.get("choices") or []
        if choices:
            answer = (choices[0].get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "endpoint": self._endpoint(),
            "model": payload["model"],
            "messages": messages,
            "answer": answer,
            "usage": usage,
            "elapsed_sec": round(time.time() - started, 3),
        }
        log_path = self._append_log(record)
        return MiniMaxResult(
            model=payload["model"],
            prompt=prompt,
            answer=answer,
            usage=usage,
            log_path=str(log_path),
        )

    def _append_log(self, record: dict[str, Any]) -> Path:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_dir / LOG_NAME
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path


def default_log_dir() -> Path:
    """Return the directory the API log is written to."""

    override = os.environ.get("MINIMAX_LOG_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "logs"


__all__ = ["LOG_NAME", "MiniMaxClient", "MiniMaxResult", "default_log_dir"]
