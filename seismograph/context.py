from __future__ import annotations

from dataclasses import dataclass

from . import config
from .edgar import Edgar
from .hmem import HMEM
from .logs import TraceLog
from .nightshift import Nightshift
from .replay import Replay
from .store import Store
from .zetaris import Zetaris


@dataclass
class Context:
    settings: config.Settings
    log: TraceLog
    replay: Replay
    store: Store
    zetaris: Zetaris
    nightshift: Nightshift | None
    edgar: Edgar
    hmem: HMEM


def build(echo: bool = True, run_id: str | None = None) -> Context:
    s = config.load()
    log = TraceLog(s.log_dir, run_id=run_id, echo=echo, sample_mode=s.sample_mode)
    replay = Replay(s.sample_dir, s.sample_mode)
    if s.sample_mode:
        store = Store(None, sqlite_path=s.out_dir / "sample-evidence.sqlite")
        hmem_path = s.out_dir / "sample-hmem.sqlite"
        ns = None
        log.log("Pipeline", "sample_mode", status="success",
                output_summary="SAMPLE_MODE=true: replaying recorded Zetaris, Nightshift and SEC responses from "
                               "data/sample/; no network calls")
    else:
        store = Store(s.pg_dsn)
        hmem_path = s.hmem_path
        ns = Nightshift(s.meterless_base_url, s.meterless_token, s.meterless_app, log, s.require_nemotron)
    store.init()
    hmem_path.parent.mkdir(parents=True, exist_ok=True)
    hmem = HMEM(hmem_path, actor="agent:seismograph")
    return Context(s, log, replay, store, Zetaris(s, log, replay, local_store=store), ns,
                   Edgar(s.sec_user_agent, replay), hmem)
