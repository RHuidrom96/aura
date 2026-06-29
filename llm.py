"""Provider-agnostic LLM helper.

Supports Anthropic, OpenAI, Google Gemini, and any OpenAI-compatible endpoint
(which covers a local Ollama server). Flask-agnostic: callers pass the secret
used to encrypt stored API keys.

Network calls go through `_http_post_json`, which tests can monkeypatch.
"""

from __future__ import annotations
import os
import json
import base64
import hashlib
import urllib.request
import urllib.error


class LLMError(Exception):
    pass


# --------------------------------------------------------------------------
# provider catalogue (drives the admin UI)
# --------------------------------------------------------------------------

PROVIDERS = [
    {"id": "anthropic", "name": "Anthropic (Claude)",
     "needs_key": True, "needs_base_url": False,
     "default_base_url": "", "default_model": "claude-haiku-4-5-20251001",
     "desc": "Claude models via the Anthropic API."},
    {"id": "openai", "name": "OpenAI (GPT)",
     "needs_key": True, "needs_base_url": False,
     "default_base_url": "", "default_model": "gpt-5.4-mini",
     "desc": "GPT models via the OpenAI API."},
    {"id": "gemini", "name": "Google Gemini",
     "needs_key": True, "needs_base_url": False,
     "default_base_url": "", "default_model": "gemini-2.5-flash",
     "desc": "Gemini models via the Google AI (Generative Language) API."},
    {"id": "openai_compatible", "name": "OpenAI-compatible endpoint",
     "needs_key": False, "needs_base_url": True,
     "default_base_url": "", "default_model": "",
     "desc": "Any server speaking the OpenAI /chat/completions API (Together, Groq, vLLM, LM Studio, …)."},
]
PROVIDER_IDS = {p["id"] for p in PROVIDERS}


def provider_by_id(pid):
    for p in PROVIDERS:
        if p["id"] == pid:
            return p
    return None


# --------------------------------------------------------------------------
# key encryption (Fernet, key derived from the app secret)
# --------------------------------------------------------------------------

def _fernet(secret):
    from cryptography.fernet import Fernet
    key = base64.urlsafe_b64encode(hashlib.sha256((secret or "").encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(plaintext, secret):
    if not plaintext:
        return ""
    return _fernet(secret).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token, secret):
    if not token:
        return ""
    try:
        return _fernet(secret).decrypt(token.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# config resolution
# --------------------------------------------------------------------------

def env_default_config():
    """Instance-wide default from environment, or None."""
    provider = os.environ.get("MT_EVAL_AI_PROVIDER", "").strip()
    if not provider or provider not in PROVIDER_IDS:
        return None
    meta = provider_by_id(provider)
    return {
        "provider": provider,
        "model": os.environ.get("MT_EVAL_AI_MODEL", "").strip() or meta["default_model"],
        "base_url": os.environ.get("MT_EVAL_AI_BASE_URL", "").strip() or meta["default_base_url"],
        "api_key": os.environ.get("MT_EVAL_AI_API_KEY", "").strip(),
        "source": "instance default",
    }


def campaign_config(campaign, secret):
    """Per-campaign config from stored fields, or None if not enabled/usable."""
    if not getattr(campaign, "ai_enabled", False):
        return None
    provider = (campaign.ai_provider or "").strip()
    if provider not in PROVIDER_IDS:
        return None
    meta = provider_by_id(provider)
    return {
        "provider": provider,
        "model": (campaign.ai_model or "").strip() or meta["default_model"],
        "base_url": (campaign.ai_base_url or "").strip() or meta["default_base_url"],
        "api_key": decrypt_secret(campaign.ai_api_key_enc or "", secret),
        "source": "campaign",
    }


def effective_config(campaign, secret):
    """Campaign config takes priority; otherwise the instance default."""
    return campaign_config(campaign, secret) or env_default_config()


def is_configured(cfg):
    if not cfg:
        return False
    meta = provider_by_id(cfg.get("provider"))
    if not meta:
        return False
    if not cfg.get("model"):
        return False
    if meta["needs_key"] and not cfg.get("api_key"):
        return False
    if meta["needs_base_url"] and not cfg.get("base_url"):
        return False
    return True


# --------------------------------------------------------------------------
# HTTP (monkeypatchable)
# --------------------------------------------------------------------------

def _http_post_json(url, headers, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise LLMError(f"Provider returned HTTP {e.code}. {body}")
    except urllib.error.URLError as e:
        raise LLMError(f"Could not reach the model endpoint ({e.reason}). "
                       "If using Ollama, make sure it is running.")
    except Exception as e:
        raise LLMError(f"Request failed: {e}")


# --------------------------------------------------------------------------
# call dispatch
# --------------------------------------------------------------------------

def call_llm(cfg, system, user, history=None, max_tokens=600, temperature=0.2, timeout=30):
    """Return the assistant's text for a system prompt + optional prior turns + new user turn.

    `history` is an optional list of {"role": "user"|"assistant", "content": str}
    representing earlier turns in the conversation (most recent last).
    """
    if not is_configured(cfg):
        raise LLMError("The assistant is not fully configured.")
    provider = cfg["provider"]
    hist = [{"role": h["role"], "content": h["content"]} for h in (history or [])
            if h.get("role") in ("user", "assistant") and h.get("content")]

    if provider == "anthropic":
        out = _http_post_json(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01",
             "content-type": "application/json"},
            {"model": cfg["model"], "max_tokens": max_tokens, "temperature": temperature,
             "system": system, "messages": hist + [{"role": "user", "content": user}]},
            timeout)
        parts = out.get("content") or []
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
        return text.strip()

    if provider == "gemini":
        contents = [{"role": ("model" if h["role"] == "assistant" else "user"),
                     "parts": [{"text": h["content"]}]} for h in hist]
        contents.append({"role": "user", "parts": [{"text": user}]})
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{cfg['model']}:generateContent?key={cfg['api_key']}")
        out = _http_post_json(
            url, {"content-type": "application/json"},
            {"systemInstruction": {"parts": [{"text": system}]},
             "contents": contents,
             # Gemini 2.5+ are "thinking" models. We leave thinking enabled (it improves
             # answer quality) but give a generous output budget so the reasoning and the
             # visible answer both fit; a tight budget is what previously yielded no text.
             "generationConfig": {"maxOutputTokens": max(max_tokens, 2048),
                                  "temperature": temperature}},
            timeout)
        if isinstance(out, dict) and out.get("error"):
            err = out["error"]
            raise LLMError("Gemini API error: "
                           + str(err.get("message") if isinstance(err, dict) else err))
        cands = out.get("candidates") or []
        if not cands:
            fb = out.get("promptFeedback") or {}
            if fb.get("blockReason"):
                raise LLMError("The request was blocked by the model's safety filters "
                               f"({fb.get('blockReason')}).")
            raise LLMError("The model returned no candidates.")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if not text:
            fr = cands[0].get("finishReason") or "unknown"
            raise LLMError(f"The model returned no text (finish reason: {fr}). "
                           "Try increasing the token budget or a different Gemini model.")
        return text

    # openai, openai_compatible, ollama -> OpenAI chat-completions shape
    if provider in ("openai", "openai_compatible"):
        base = cfg.get("base_url") or "https://api.openai.com/v1"
        base = base.rstrip("/")
        headers = {"content-type": "application/json"}
        if cfg.get("api_key"):
            headers["Authorization"] = "Bearer " + cfg["api_key"]
        messages = [{"role": "system", "content": system}] + hist + [{"role": "user", "content": user}]
        out = _http_post_json(
            base + "/chat/completions", headers,
            {"model": cfg["model"], "temperature": temperature, "max_tokens": max_tokens,
             "messages": messages},
            timeout)
        choices = out.get("choices") or []
        if not choices:
            raise LLMError("The model returned no choices.")
        return (choices[0].get("message") or {}).get("content", "").strip()

    raise LLMError(f"Unknown provider '{provider}'.")


def test_config(cfg):
    """Lightweight connectivity check. Returns (ok: bool, message: str)."""
    try:
        txt = call_llm(cfg,
                       "You are a connectivity test. Reply with the single word OK.",
                       "Reply with OK.", max_tokens=10, temperature=0.0, timeout=20)
        if txt:
            return True, "Connection succeeded. Model replied: " + txt[:60]
        return False, "The model responded but returned no text."
    except LLMError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Unexpected error: {e}"
