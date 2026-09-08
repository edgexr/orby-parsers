"""Shared HTTP client for calling an OpenAI-compatible vision-capable
chat endpoint, used by every feature in this codebase that needs a real
vision-language model rather than a classical OCR library (see
README_bankcheck_ocr.md for why) - today that's check_ocr.py (check
payee/memo OCR) and redact_pdf_text.py (finding/redacting sensitive text
inside embedded images). stdlib-only (no extra pyruntime dependency),
since the model itself already runs elsewhere (see pkg/ingest's
EnsureVisionModelFunc on the Go side) - this module just talks to it.
"""

import json
import re
import urllib.request


def call_vision(vision: dict, image_b64: str, prompt: str, image_format: str = "png") -> str:
    """Sends image_b64 (base64-encoded, image_format e.g. "png") plus a
    text prompt to vision - an OpenAI-compatible chat endpoint described
    as {"endpoint", "model", "api_key"}, endpoint ending in ".../v1" -
    and returns the model's raw text response (message.content).

    Raises (urllib.error.URLError, OSError, TimeoutError, KeyError,
    ValueError, ...) on any network/HTTP/response-shape failure -
    callers decide whether that's fatal for their use case.
    """
    headers = {"Content-Type": "application/json"}
    if vision.get("api_key"):
        headers["Authorization"] = f"Bearer {vision['api_key']}"
    payload = {
        "model": vision["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/{image_format};base64,{image_b64}"}},
                ],
            }
        ],
        "temperature": 0,
        # Qwen3.5 (Orby's builtin model family) is a reasoning model that
        # otherwise burns most of its token budget on a <think> block
        # before answering - same accuracy, 2-2.5x faster with this off
        # (see README_bankcheck_ocr.md). Non-Qwen3.5 endpoints simply
        # ignore an unrecognized chat_template_kwargs field.
        "chat_template_kwargs": {"enable_thinking": False},
        # llama-server's prompt/KV cache defaults to enabled and is
        # reused across every request on a long-running server - not
        # just the request this call makes, but also whatever chat/other
        # vision calls happened before it in the same session (this
        # server is shared, long-lived, and reused across features - see
        # pkg/llmserver.Manager). Its multimodal (mtmd) cache-reuse
        # support is still explicitly labeled experimental by llama.cpp
        # itself, and every call here sends a *different* image behind
        # an often textually-identical prompt prefix (e.g. two check
        # pages of the same pixel dimensions) - exactly the shape of
        # request most likely to trip a cache-reuse bug that reuses a
        # previous request's image tokens for this one, silently
        # answering about the wrong image. Each of these calls is a
        # single, independent, non-conversational request, so there's no
        # legitimate reuse to lose by disabling this.
        "cache_prompt": False,
    }
    req = urllib.request.Request(
        vision["endpoint"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.load(resp)
    return result["choices"][0]["message"]["content"]


def extract_json_array(text: str) -> list:
    """Extracts a top-level JSON array from text, tolerating a model that
    adds prose or a markdown code fence around it despite being asked to
    reply with only JSON. Returns [] if nothing array-shaped parses.
    """
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
