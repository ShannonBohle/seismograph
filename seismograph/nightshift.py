"""Meterless Nightshift client: the model provider for every call the agent makes at run time.

Follows the Meterless Runtime provider contract
(https://github.com/Meterless/Meterless/blob/main/docs/agent-ready/add-meterless-provider.md):
OpenAI-compatible, base URL normalised to <host>/v1, real bearer token, a stable
`meterless-app` header on every request, `meterless-scope: user` on chat, a 180 s
timeout, and GET /v1/models as the source of truth for online/unauthorized/offline.

Every call records which model actually answered (the `model` field of the response),
so the logs can show whether a Nemotron model handled a request.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

from .logs import TraceLog

TIMEOUT_S = 180


def normalise_base_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        url = "http://" + url
    url = re.sub(r"/(v1/)?(chat/completions|models|healthz)/?$", "", url.rstrip("/"))
    url = re.sub(r"(/v1)+$", "", url.rstrip("/"))
    return url + "/v1"


class ProviderError(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    requested_model: str
    served_model: str
    is_nemotron: bool
    usage: dict


class Nightshift:
    def __init__(self, base_url: str, token: str, app: str, log: TraceLog, require_nemotron: bool = False):
        self.base = normalise_base_url(base_url)
        self.token = token
        self.app = app
        self.log = log
        self.require_nemotron = require_nemotron

    def _headers(self, chat: bool = False) -> dict:
        h = {"Authorization": f"Bearer {self.token}", "meterless-app": self.app, "Content-Type": "application/json"}
        if chat:
            h["meterless-scope"] = "user"
        return h

    def status(self) -> tuple[str, list[str]]:
        """Return ("online" | "unauthorized" | "offline", model ids)."""
        try:
            r = requests.get(f"{self.base}/models", headers=self._headers(), timeout=15)
        except requests.RequestException as exc:
            self.log.log("Nightshift", "provider_status", status="failure", output_summary=f"offline: {exc}")
            return "offline", []
        if r.status_code in (401, 403):
            self.log.log("Nightshift", "provider_status", status="failure", output_summary="unauthorized: check METERLESS_RUNTIME_TOKEN")
            return "unauthorized", []
        if r.status_code != 200:
            self.log.log("Nightshift", "provider_status", status="failure", output_summary=f"offline: HTTP {r.status_code}")
            return "offline", []
        ids = [m.get("id", "") for m in r.json().get("data", [])]
        self.log.log("Nightshift", "provider_status", output_summary=f"online; {len(ids)} model ids", details_models=ids[:50])
        return "online", ids

    def chat(self, model: str, system: str, user: str, *, agent: str, json_mode: bool = True,
             temperature: float = 0.0, max_tokens: int = 1200) -> Completion:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(f"{self.base}/chat/completions", headers=self._headers(chat=True),
                              data=json.dumps(body), timeout=TIMEOUT_S)
            if r.status_code == 400 and json_mode:  # some routes reject response_format; retry once without it
                body.pop("response_format", None)
                r = requests.post(f"{self.base}/chat/completions", headers=self._headers(chat=True),
                                  data=json.dumps(body), timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            raise ProviderError(f"Nightshift unreachable at {self.base}: {exc}") from exc
        if r.status_code in (401, 403):
            raise ProviderError("Nightshift rejected the token (unauthorized). Paste the token from Nightshift's Console.")
        if r.status_code != 200:
            raise ProviderError(f"Nightshift HTTP {r.status_code}: {r.text[:300]}")
        payload = r.json()
        text = payload["choices"][0]["message"].get("content") or ""
        served = payload.get("model") or r.headers.get("x-meterless-model") or "unreported"
        is_nemo = "nemotron" in served.lower()
        if self.require_nemotron and not is_nemo:
            raise ProviderError(f"REQUIRE_NEMOTRON is set but Nightshift served {served!r}. Pin a Nemotron model in a lane.")
        return Completion(text=text, requested_model=model, served_model=served, is_nemotron=is_nemo,
                          usage=payload.get("usage") or {})


def parse_json(text: str) -> dict:
    """Parse a model's JSON answer, tolerating code fences and leading prose."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model answer contained no JSON object")
    return json.loads(text[start:end + 1])
