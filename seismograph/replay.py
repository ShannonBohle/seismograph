"""Record-and-replay cache behind SAMPLE_MODE.

In a live run with RECORD_SAMPLE=true, every external response (Zetaris query results,
Nightshift completions, SEC fetches) is saved under data/sample/, keyed by a hash of
the request. SAMPLE_MODE=true replays those saved responses with no network calls, so a
judge can run the agent without Zetaris, Nightshift or keys. Replayed data is
labelled "sample" in every log line. Nothing in data/sample/ is written by hand.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class SampleMissing(RuntimeError):
    pass


class Replay:
    def __init__(self, sample_dir: Path, sample_mode: bool):
        self.dir = sample_dir
        self.sample_mode = sample_mode
        self.record = (not sample_mode) and os.environ.get("RECORD_SAMPLE", "").lower() in ("1", "true", "yes")

    @staticmethod
    def key(kind: str, request: dict) -> str:
        blob = json.dumps(request, sort_keys=True, ensure_ascii=False)
        return f"{kind}-{hashlib.sha256(blob.encode()).hexdigest()[:24]}"

    def load(self, kind: str, request: dict):
        path = self.dir / kind / f"{self.key(kind, request)}.json"
        if not path.exists():
            raise SampleMissing(f"SAMPLE_MODE: no recorded {kind} response for this request ({path.name}). "
                                "Record one with a live run and RECORD_SAMPLE=true.")
        return json.loads(path.read_text(encoding="utf-8"))["response"]

    def save(self, kind: str, request: dict, response) -> None:
        if not self.record:
            return
        folder = self.dir / kind
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{self.key(kind, request)}.json"
        path.write_text(json.dumps({"request": request, "response": response}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
