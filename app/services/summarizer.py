"""Email summarization service for MailMate AI.

Generates a concise 1-2 sentence summary of each email using a language
model.  Supports two backends:

  1. Local vLLM  (default) — Qwen2.5-0.5B-Instruct at localhost:8000
     OpenAI-compatible API, no credentials needed, already running.

  2. HuggingFace Inference API — any chat model via HF_TOKEN + HF_SUMMARY_MODEL.
     Set HF_TOKEN and HF_SUMMARY_MODEL in .env to enable.

If both are unavailable the function returns an empty string and the
caller falls back to showing the email subject only.

This module never classifies, creates tasks, or sends notifications.
"""

import logging
import os
import requests

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an email assistant. Summarize the email in 1-2 clear sentences. "
    "Cover: what is being asked or communicated, any deadline mentioned, "
    "and any required action. Be factual, concise, and avoid repeating the subject line."
)

# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _summarize_vllm(subject: str, body: str, max_tokens: int = 100) -> str:
    """Call the local vLLM OpenAI-compatible endpoint."""
    url = os.environ.get("VLLM_URL", "http://localhost:8000")
    model = os.environ.get("VLLM_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Subject: {subject}\n\n{body[:2000]}"},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    try:
        resp = requests.post(f"{url}/v1/chat/completions",
                             json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        log.warning("summarizer: vllm failed err=%s", type(exc).__name__)
        return ""


def _summarize_hf(subject: str, body: str, max_tokens: int = 100) -> str:
    """Call the HuggingFace Inference API."""
    token = os.environ.get("HF_TOKEN", "")
    model = os.environ.get("HF_SUMMARY_MODEL", "")
    if not token or not model:
        return ""

    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(api_key=token)
        r = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Subject: {subject}\n\n{body[:2000]}"},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return r.choices[0].message.content.strip()
    except Exception as exc:
        log.warning("summarizer: hf failed err=%s", type(exc).__name__)
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize_email(subject: str, body: str) -> str:
    """Return a 1-2 sentence summary of the email.

    Uses local vLLM (Qwen2.5-0.5B-Instruct at localhost:8000) by default —
    free, no credentials, already running.

    Falls back to HuggingFace Inference API if HF_TOKEN and HF_SUMMARY_MODEL
    are set and vLLM is unavailable.

    Returns empty string if all backends fail — callers degrade gracefully.
    """
    if not subject and not body:
        return ""

    # Primary: local vLLM (free, no credits needed)
    summary = _summarize_vllm(subject, body)
    if summary:
        log.debug("summarizer: used local vllm")
        return summary

    # Fallback: HuggingFace Inference API (requires PRO/credits)
    summary = _summarize_hf(subject, body)
    if summary:
        log.debug("summarizer: used hf api")
        return summary

    log.warning("summarizer: all backends failed — no summary available")
    return ""
