"""
Voice agent — Twilio <Connect> + NIM voice model.

Architecture:
1. Inbound call hits Twilio, hits our /voice/twiml endpoint
2. TwiML <Connect><Stream> opens WebSocket to /voice/stream
3. Audio chunks stream in (mulaw 8kHz)
4. STT (faster-whisper if available) → text
5. NIM voice model → text response
6. TTS (pyttsx3 fallback to eSpeak) → audio
7. Audio streams back over WebSocket

Production deployments:
- Twilio <Connect>: streams audio bidirectionally
- Deepgram/Whisper STT: real-time transcription
- OpenAI TTS-1: high quality voice
- NIM Nemotron 30B: fast inference

This module provides the NIM voice model integration + TwiML/WS scaffolding.
Full audio pipeline requires additional deps (whisper, TTS, audio libs).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any


NIM_VOICE_KEY_PATH = Path("/workspace/orchestrator/keys/.nvidia_voice.txt")


def load_voice_key() -> str | None:
    if NIM_VOICE_KEY_PATH.exists():
        try:
            return NIM_VOICE_KEY_PATH.read_text().strip() or None
        except Exception:
            return None
    import os
    return os.environ.get("NVIDIA_VOICE_KEY") or os.environ.get("NVIDIA_API_KEY")


# === TwiML (call control) ===

def twiml_stream(public_ws_url: str) -> str:
    """TwiML that opens a media stream to our WebSocket endpoint.
    Twilio calls this when a call comes in to our number."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{public_ws_url}">
      <Parameter name="track" value="both_tracks" />
    </Stream>
  </Connect>
</Response>"""


def twiml_say(text: str, voice: str = "alice") -> str:
    """TwiML that just speaks and hangs up (for non-streaming fallback)."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="{voice}">{text}</Say>
  <Hangup/>
</Response>"""


# === WebSocket handler sketch ===

class VoiceSession:
    """One voice call session.

    Lifecycle:
    1. Twilio hits /voice/twiml -> we return TwiML with <Stream>
    2. Twilio opens WebSocket to /voice/stream
    3. We receive audio frames, transcribe, run NIM, synthesize, send back
    4. Call ends or we hang up
    """

    def __init__(self, call_sid: str, from_number: str, to_number: str):
        self.call_sid = call_sid
        self.from_number = from_number
        self.to_number = to_number
        self.started_at = time.time()
        self.transcript: list[dict] = []  # [{"role": "user"|"assistant", "text": ...}]
        self.tools_called: list[dict] = []

    def to_dict(self) -> dict:
        return {
            "call_sid": self.call_sid,
            "from": self.from_number,
            "to": self.to_number,
            "started_at": self.started_at,
            "duration_seconds": time.time() - self.started_at,
            "transcript": self.transcript,
            "tools_called": self.tools_called,
        }


async def process_voice_turn(session: VoiceSession, user_text: str,
                              nim_key: str | None = None,
                              tool_executor=None) -> dict:
    """One turn of voice conversation.

    1. Append user text to transcript
    2. Call NIM with transcript + system prompt
    3. Parse tool calls if any, execute them
    4. Generate final response text
    5. Append assistant text to transcript
    6. Return the text for TTS
    """
    import urllib.request as _ur

    session.transcript.append({"role": "user", "text": user_text, "ts": time.time()})

    key = nim_key or load_voice_key()
    if not key:
        return {"ok": False, "error": "no_voice_key",
                "text": "I'm sorry, the voice service is not configured."}

    # Build NIM messages
    messages = [
        {"role": "system", "content": (
            "You are a friendly, professional GTM sales assistant on a phone call. "
            "Keep responses under 30 words. Ask one clarifying question at a time. "
            "Be warm but efficient. Confirm interest and offer next steps."
        )},
    ] + [{"role": m["role"], "content": m["text"]} for m in session.transcript[-10:]]

    body = {
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "messages": messages,
        "max_tokens": 250,  # Nemotron thinking mode needs lots of tokens
        "temperature": 0.6,
        "chat_template_kwargs": {"enable_thinking": False},  # disable thinking for fast voice
    }

    try:
        req = _ur.Request(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        # Nemotron uses thinking mode — allow up to 90s
        with _ur.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
            msg = data["choices"][0]["message"]
            # Nemotron returns content="" but reasoning_content="Here's a thinking..."
            # Try BOTH fields
            content = msg.get("content") or ""
            reasoning = msg.get("reasoning_content") or ""
            # Prefer non-empty, non-thinking-only
            if content and content.strip() and "thinking process" not in content[:200].lower():
                reply = content.strip()
            elif reasoning:
                # Reasoning has thinking + actual response separated by "\n\n" usually
                reply = reasoning.strip()
            else:
                reply = (content or reasoning).strip()

            # Clean Nemotron thinking artifacts
            # The structure is: "Here's a thinking process:\n\n1. **Analyze**...\n   - ...\n\n<actual reply>"
            # Split on \n\n and take the LAST chunk that's NOT a thinking step
            if "thinking process" in reply.lower():
                chunks = reply.split("\n\n")
                # Find the last chunk that's a real response (not thinking)
                real_chunks = []
                for c in chunks:
                    cl = c.strip()
                    if not cl:
                        continue
                    cl_lower = cl.lower()
                    # Skip "Here's a thinking process:" header
                    if cl_lower.startswith("here's a thinking") or cl_lower.startswith("thinking"):
                        continue
                    # Skip chunks that look like numbered list (thinking steps)
                    if cl[:2] in ("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."):
                        continue
                    # Skip chunks starting with "- " (bullet points in thinking)
                    if cl.startswith("- "):
                        continue
                    # Skip "User says:" lines from thinking
                    if "user says" in cl_lower or "**analyze" in cl_lower or "**formulate" in cl_lower:
                        continue
                    # Skip bold-header-only chunks
                    lines = [l.strip() for l in cl.split("\n") if l.strip()]
                    if lines and all(l.startswith("**") or l.startswith("- ") for l in lines):
                        continue
                    real_chunks.append(cl)
                if real_chunks:
                    reply = real_chunks[-1]
            # If still has thinking markers, just take any line >30 chars
            if "thinking process" in reply.lower() or len(reply) > 300:
                lines = [l.strip() for l in reply.split("\n") if l.strip()]
                for l in lines:
                    if (len(l) > 25
                            and "thinking" not in l.lower()
                            and "user says" not in l.lower()
                            and "**" not in l
                            and not l[:2] in ("1.", "2.", "- ")):
                        reply = l
                        break
            # Final fallback if reply too short
            if not reply or len(reply) < 10:
                reply = "Got it. What's the best time to send you more info?"
    except Exception as e:
        reply = "I'm sorry, I'm having trouble connecting. Could you repeat that?"

    session.transcript.append({"role": "assistant", "text": reply, "ts": time.time()})
    return {"ok": True, "text": reply, "transcript_length": len(session.transcript)}


# === Pre-call detection: spam/scam detection ===

SPAM_KEYWORDS = {"free", "guaranteed", "winner", "lottery", "irs", "social security",
                 "arrested", "warrant", "urgent", "act now", "limited time"}


def is_likely_spam(text: str) -> bool:
    """Heuristic: detect robocall / spam patterns."""
    text_l = text.lower()
    matches = sum(1 for kw in SPAM_KEYWORDS if kw in text_l)
    return matches >= 2


# === Voicemail detection (basic) ===

def is_likely_voicemail(text: str) -> bool:
    """Detect when call goes to voicemail (no human)."""
    text_l = text.lower().strip()
    if any(p in text_l for p in ["leave a message", "after the tone",
                                  "not available", "voicemail", "mailbox"]):
        return True
    if len(text_l) < 5 and any(w in text_l for w in ["beep", "*", "hello?"]):
        return True
    return False


SOURCE = "Voice agent — Twilio <Connect> + NIM voice model integration + TwiML + turn processing"


def voice_test() -> dict:
    """Quick test: verify voice model key works."""
    key = load_voice_key()
    if not key:
        return {"ok": False, "error": "no_voice_key"}
    import urllib.request as _ur
    body = {
        "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "messages": [
            {"role": "system", "content": "You are on a phone call. Keep it under 20 words."},
            {"role": "user", "content": "Hi, this is Sarah from ScaleUp, can I speak to the head of sales?"},
        ],
        "max_tokens": 60,
    }
    req = _ur.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _ur.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
            msg = data["choices"][0]["message"]
            reply = msg.get("content") or msg.get("reasoning_content") or ""
            return {
                "ok": True,
                "reply": reply,
                "model": data.get("model"),
                "usage": data.get("usage"),
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}
