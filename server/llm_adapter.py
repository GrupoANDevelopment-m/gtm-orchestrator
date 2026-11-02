"""
NvidiaLLMRouter — Production LLM adapter for gtm-engine agents.

Replaces the stub LocalLLMRouter in upstream gtm-engine. Calls NVIDIA NIM
with OpenAI-compatible API. Falls back to LocalLLMRouter behavior if no
keys are configured, so demo-mode still works.

Drop-in replacement: same complete() / embed() signatures, same JSON output.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

# Match the upstream gtm-engine signature exactly
NVIDIA_BASE = os.environ.get("NVIDIA_BASE", "https://integrate.api.nvidia.com/v1")
DEFAULT_MODEL = os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")


def _load_key():
    """Read NVIDIA key from env or keys/.nvidia_main.txt"""
    key = os.environ.get("NVIDIA_API_KEY")
    if key:
        return key.strip()
    # Fall back to keys/ directory
    keys_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keys")
    for fname in (".nvidia_main.txt", ".nvidia_step.txt", ".nvidia_m2.txt"):
        p = os.path.join(keys_dir, fname)
        if os.path.exists(p):
            return open(p).read().strip()
    return None


def _nim_chat(system_prompt: str, user_prompt: str, *, model: str = None) -> str:
    """Call NVIDIA NIM chat completions. Returns the assistant content.
    Truncates inputs to fit context, retries on transient errors.
    Output is sanitized (emojis stripped) per user requirement: no emojis anywhere.
    """
    import time as _t
    from text_sanitizer import clean_text
    key = _load_key()
    if not key:
        raise RuntimeError("no NVIDIA_API_KEY configured")
    # Truncate to avoid token-limit errors
    sys_short = (system_prompt or "")[:6000]
    user_short = user_prompt[:8000]
    body = {
        "model": model or DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": sys_short or "You are a helpful assistant."},
            {"role": "user", "content": user_short},
        ],
        "temperature": 0.4,
        "top_p": 0.95,
        "max_tokens": 1024,
        "stream": False,
        # Disable Nemotron thinking mode by default for fast direct responses.
        # (the model would otherwise spend 80% of output tokens on "thinking".)
        "chat_template_kwargs": {"enable_thinking": False},
    }
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f"{NVIDIA_BASE}/chat/completions",
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read().decode())
            msg = data["choices"][0]["message"]
            raw = msg.get("content") or msg.get("reasoning_content") or ""
            return clean_text(raw)
        except urllib.error.HTTPError as e:
            err_body = ""
            try: err_body = e.read().decode()[:300]
            except: pass
            last_err = f"HTTP {e.code}: {err_body}"
            if e.code in (400, 401, 403):
                raise RuntimeError(last_err)
            _t.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            _t.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"NIM failed after 3 attempts: {last_err}")


class NvidiaLLMRouter:
    """Drop-in replacement for LocalLLMRouter that calls NVIDIA NIM.

    Same signatures: complete(prompt, *, system_prompt, format) -> str (JSON).
    Same output shape: a JSON string the gtm-engine agents can parse.
    """

    def __init__(self, *, model: str = None, fallback=None):
        self._model = model or DEFAULT_MODEL
        self._fallback = fallback  # optional: a LocalLLMRouter instance for graceful degradation
        self._key = _load_key()

    @property
    def is_live(self) -> bool:
        return self._key is not None

    def complete(self, prompt: str, *, system_prompt: str | None = None, format: str | None = None) -> str:
        """Call NIM, return JSON string. If the agent expects JSON (format='json'),
        we ask the model to respond in JSON and strip any markdown fences."""
        if not self.is_live:
            # Graceful fallback to the stub
            if self._fallback is not None:
                return self._fallback.complete(prompt, system_prompt=system_prompt, format=format)
            return '{"result": "ok"}'

        sys = system_prompt or "You are a helpful assistant."
        if format == "json":
            sys = sys + "\n\nYou MUST respond with valid JSON only. No prose, no markdown fences."
        try:
            content = _nim_chat(sys, prompt, model=self._model)
        except Exception as e:
            if self._fallback is not None:
                return self._fallback.complete(prompt, system_prompt=system_prompt, format=format)
            return json.dumps({"error": f"NVIDIA call failed: {e}"})

        if format == "json":
            # Strip ```json fences if present
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                # drop first line (```json or ```) and last line (```)
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
            # try to parse — if it fails, return as-is
            try:
                parsed = json.loads(content)
                return json.dumps(parsed)
            except json.JSONDecodeError:
                # Try to extract first {...} block
                import re
                m = re.search(r"\{[\s\S]*\}", content)
                if m:
                    try:
                        return m.group(0)
                    except Exception:
                        pass
                return content
        return content

    def embed(self, text: str) -> list[float]:
        """Embed via NVIDIA NIM. Falls back to LocalLLMRouter.embed if unavailable."""
        if not self.is_live:
            return self._fallback.embed(text) if self._fallback else [0.0] * 16
        try:
            body = {"model": "nvidia/nv-embedqa-e5-v5", "input": [text], "input_type": "passage"}
            req = urllib.request.Request(
                f"{NVIDIA_BASE}/embeddings",
                data=json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode())
            return data["data"][0]["embedding"]
        except Exception:
            return self._fallback.embed(text) if self._fallback else [0.0] * 16
