"""Record-and-replay cache behind SAMPLE_MODE.

In a live run with RECORD_SAMPLE=true, every external response (Nightshift completions and
SEC fetches) is saved under data/sample/, keyed by a hash of the request. SAMPLE_MODE=true
replays those saved responses with no network calls, so a judge can run the agent without
Zetaris, Nightshift or keys. Replayed data is labelled "sample" in every log line. Nothing
in data/sample/ is written by hand.

Replay first looks for the exact request. If there is none, it accepts a recorded request
that matches on the fields that determine the answer, so a replay works on another day
and with other model settings:
  sec_search  same query and forms (the date window is ignored; the latest recording wins)
  nightshift  same system prompt and the same user prompt up to the memory section
              (the model name is ignored; the model that actually answered is replayed and logged)
  sec_doc     same URL
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

MEMORY_MARKER = "What memory already holds"


class SampleMissing(RuntimeError):
    pass


def _nightshift_core(req: dict) -> tuple[str, str]:
    return req.get("system", ""), req.get("user", "").split(MEMORY_MARKER)[0]


MATCHERS = {
    "sec_search": lambda a, b: a.get("q") == b.get("q") and a.get("forms") == b.get("forms"),
    "nightshift": lambda a, b: _nightshift_core(a) == _nightshift_core(b),
    "sec_doc": lambda a, b: a.get("url") == b.get("url"),
}


class Replay:
    def __init__(self, sample_dir: Path, sample_mode: bool):
        self.dir = sample_dir
        self.sample_mode = sample_mode
        self.record = (not sample_mode) and os.environ.get("RECORD_SAMPLE", "").lower() in ("1", "true", "yes")
        self._index: dict[str, list[dict]] = {}

    @staticmethod
    def key(kind: str, request: dict) -> str:
        blob = json.dumps(request, sort_keys=True, ensure_ascii=False)
        return f"{kind}-{hashlib.sha256(blob.encode()).hexdigest()[:24]}"

    def _all(self, kind: str) -> list[dict]:
        if kind not in self._index:
            items = []
            for p in sorted((self.dir / kind).glob("*.json")):
                try:
                    items.append(json.loads(p.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
            # latest recording first (recorded_at when present, else the request's end date)
            items.sort(key=lambda d: d.get("recorded_at") or d.get("request", {}).get("enddt") or "", reverse=True)
            self._index[kind] = items
        return self._index[kind]

    def find(self, kind: str, request: dict):
        path = self.dir / kind / f"{self.key(kind, request)}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        match = MATCHERS.get(kind)
        if match:
            for item in self._all(kind):
                if match(item.get("request", {}), request):
                    return item["response"]
        return None

    def has(self, kind: str, request: dict) -> bool:
        return self.find(kind, request) is not None

    def load(self, kind: str, request: dict):
        found = self.find(kind, request)
        if found is None:
            raise SampleMissing(f"SAMPLE_MODE: no recorded {kind} response for this request. "
                                "Record one with a live run and RECORD_SAMPLE=true.")
        return found

    def save(self, kind: str, request: dict, response) -> None:
        if not self.record:
            return
        folder = self.dir / kind
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.key(kind, request)}.json"
        path.write_text(json.dumps({"request": request, "response": response,
                                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
