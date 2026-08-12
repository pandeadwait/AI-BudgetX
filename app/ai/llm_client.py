"""The single chokepoint for every provider call.

Nothing in this codebase talks to an LLM except through `complete()`. That is
what makes three guarantees cheap to enforce and cheap to prove:

  1. redaction happens here, so no caller can forget it
  2. every call lands in LLMAuditLog as a queryable row
  3. every response is cached to disk by sha256(prompt), so a rate limit
     mid-demo replays instead of failing
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from string import Template

import httpx
from sqlalchemy.orm import Session

from app.ai.redact import redact
from app.models import LLMAuditLog

MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash")
API_KEY = os.getenv("GEMINI_API_KEY", "")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

CACHE_DIR = Path(os.getenv("LLM_CACHE_DIR", ".llm_cache"))
PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str, **vars: object) -> str:
    """Versioned prompt file + $placeholders.

    Template, not f-string or .format: prompts contain literal JSON braces, and
    user text is substituted as a delimited variable rather than concatenated
    into instructions.
    """
    template = Template((PROMPT_DIR / f"{name}.txt").read_text())
    return template.safe_substitute(**vars)


def _cache_key(prompt: str, temperature: float) -> Path:
    digest = hashlib.sha256(f"{MODEL}|{temperature}|{prompt}".encode()).hexdigest()
    return CACHE_DIR / f"{digest}.txt"


def _call_provider(prompt: str, temperature: float) -> str:
    response = httpx.post(
        ENDPOINT.format(model=MODEL),
        params={"key": API_KEY},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": 800,
                # 2.5-flash reasons before answering and charges that to the
                # output budget, so a one-sentence request comes back truncated
                # mid-word and JSON comes back unparseable. Every task here is
                # "narrate these facts" or "return this JSON" — no reasoning
                # required, and switching it off halves the latency.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def complete(
    db: Session,
    task: str,
    prompt: str,
    *,
    user_id: int | None = 1,
    temperature: float = 0.0,
    fallback: str = "",
) -> str:
    """Redact, cache, call, audit. Never raises — degrades to `fallback`."""
    prompt, stripped = redact(prompt)
    cache_file = _cache_key(prompt, temperature)
    started = time.perf_counter()

    text, cache_hit, model_used = "", False, MODEL
    if cache_file.exists():
        text, cache_hit = cache_file.read_text(), True
    elif API_KEY:
        try:
            text = _call_provider(prompt, temperature)
            # Only a genuine provider response is worth caching. Caching the
            # fallback would freeze one outage into every later run and report
            # it as a cache hit — a lie that survives the provider recovering.
            CACHE_DIR.mkdir(exist_ok=True)
            cache_file.write_text(text)
        except Exception as exc:  # rate limit, timeout, malformed response
            text, model_used = fallback, f"{MODEL} (failed: {type(exc).__name__})"
    else:
        text, model_used = fallback, "none (no API key — deterministic fallback)"

    db.add(
        LLMAuditLog(
            user_id=user_id,
            task=task,
            model=model_used,
            prompt_sent=prompt,
            fields_stripped=",".join(stripped),
            response=text,
            latency_ms=int((time.perf_counter() - started) * 1000),
            cache_hit=cache_hit,
        )
    )
    db.commit()
    return text or fallback


def complete_json(
    db: Session, task: str, prompt: str, *, temperature: float = 0.0, fallback: dict
) -> dict:
    """Same guarantees, plus one retry that hands the parse error back."""
    raw = complete(db, task, prompt, temperature=temperature)
    if not raw:
        return fallback  # degraded (no key / provider down) — a retry buys nothing
    for attempt in (raw, None):
        if attempt is None:
            retry = f"{prompt}\n\nYour previous reply was not valid JSON. Reply with JSON only."
            attempt = complete(db, f"{task}_retry", retry, temperature=temperature)
        try:
            return json.loads(attempt[attempt.index("{") : attempt.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            continue
    return fallback
