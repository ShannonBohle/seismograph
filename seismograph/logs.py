"""Structured JSONL trace log. Every line is written at the moment the action happens.

Fields follow the hackathon AI Usage Policy's recommended shape: timestamp, agent_name,
action, input_summary, output_summary, target_agent, model, status, retry_count, plus
run_id and free-form details (Zetaris SQL, H-MEM ledger activity, errors).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SECRET = re.compile(r"(nvapi-[A-Za-z0-9_\-]+|Bearer\s+[A-Za-z0-9._\-]+|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)")


def scrub(value):
    """Remove anything that looks like a key or token before it reaches a log."""
    if isinstance(value, str):
        return _SECRET.sub("[redacted]", value)
    if isinstance(value, dict):
        return {k: ("[redacted]" if re.search(r"token|password|api_key|secret", k, re.I) else scrub(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


class TraceLog:
    def __init__(self, log_dir: Path, run_id: str | None = None, echo: bool = True, sample_mode: bool = False):
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"run-{self.run_id}.jsonl"
        self.echo = echo
        self.sample_mode = sample_mode
        self.events: list[dict] = []

    def log(self, agent_name: str, action: str, *, input_summary: str = "", output_summary: str = "",
            target_agent: str | None = None, model: str | None = None, status: str = "success",
            retry_count: int = 0, **details) -> dict:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "run_id": self.run_id,
            "mode": "sample" if self.sample_mode else "live",
            "agent_name": agent_name,
            "action": action,
            "input_summary": scrub(input_summary)[:500],
            "output_summary": scrub(output_summary)[:500],
            "target_agent": target_agent,
            "model": model,
            "status": status,
            "retry_count": retry_count,
        }
        if details:
            event["details"] = scrub(details)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self.events.append(event)
        if self.echo:
            arrow = f" -> {target_agent}" if target_agent else ""
            mdl = f" [{model}]" if model else ""
            print(f"{event['timestamp']} {agent_name:<14} {action:<22} {status:<8}{mdl}{arrow}  {event['output_summary'][:110]}")
        return event
