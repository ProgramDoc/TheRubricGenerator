"""Shared helpers extracted from main.py so agent modules can import them
without creating circular imports.

Reads API config from environment at import time (same pattern as main.py)."""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from fastapi import HTTPException

logger = logging.getLogger("rubricgen")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL   = os.environ.get("ANTHROPIC_MODEL",   "claude-sonnet-4-20250514")
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY",    "")
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY",    "")


def strip_markdown_fences(raw: str) -> str:
    """Remove markdown code fences wrapping JSON, if present."""
    raw = raw.strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', raw, re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw


def time_ms(fn: Callable, *args, **kwargs) -> tuple[Any, int]:
    """Run a callable and return (result, elapsed_milliseconds)."""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return result, elapsed_ms


def call_anthropic(messages: list, system: str, max_tokens: int = 4096,
                   model: str | None = None) -> str:
    """Call Anthropic API and return text response."""
    if not ANTHROPIC_API_KEY:
        raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
    payload = json.dumps({
        "model": model or ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        return data["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Anthropic error: %s", body)
        raise HTTPException(502, f"Anthropic API error: {body[:200]}")


def call_gemini(system: str, user_text: str, model: str, pdf_b64: str | None = None,
                max_tokens: int = 4096) -> str:
    """Call Google Gemini API and return text response. Supports inline PDF."""
    if not GEMINI_API_KEY:
        raise HTTPException(500, "GEMINI_API_KEY not configured")

    parts: list[dict] = []
    if pdf_b64:
        parts.append({"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}})
    parts.append({"text": user_text})

    payload = json.dumps({
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }).encode()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        candidates = data.get("candidates", [])
        if not candidates:
            raise HTTPException(502, f"Gemini returned no candidates: {str(data)[:200]}")
        parts_out = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts_out)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("Gemini error: %s", body)
        raise HTTPException(502, f"Gemini API error: {body[:200]}")


def call_openai(messages: list, model: str, max_tokens: int = 4096) -> str:
    """Call OpenAI API. Delegates to call_openai_compatible."""
    return call_openai_compatible(
        base_url="https://api.openai.com/v1",
        api_key=OPENAI_API_KEY,
        model=model, messages=messages, max_tokens=max_tokens,
        provider_label="OpenAI",
    )


MOONSHOT_API_KEY = os.environ.get("MOONSHOT_API_KEY", "")


def call_openai_compatible(base_url: str, api_key: str, model: str,
                           messages: list, max_tokens: int = 4096,
                           provider_label: str = "OpenAI-compatible") -> str:
    """Generic caller for any OpenAI-compatible /v1/chat/completions endpoint.
    Works for OpenAI, Kimi K2, vLLM, Together, Fireworks, and custom models."""
    if not api_key:
        raise HTTPException(500, f"{provider_label} API key not configured")
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        logger.error("%s error (%s): %s", provider_label, url, body)
        raise HTTPException(502, f"{provider_label} API error: {body[:200]}")


def parse_json_response(raw: str) -> dict:
    """Strip fences and parse. Raises HTTPException on failure."""
    cleaned = strip_markdown_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("JSON parse error: %s | raw: %s", e, cleaned[:300])
        raise HTTPException(500, "LLM returned invalid JSON")
