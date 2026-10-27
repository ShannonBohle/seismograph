"""Settings come from environment variables (and an optional, gitignored .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    sample_mode: bool
    # Zetaris (data layer), UI-proxy route as in zetaris/getting-started
    zetaris_base_url: str
    zetaris_org_id: str
    zetaris_api_key: str
    zetaris_username: str
    zetaris_password: str
    zetaris_engine_id: str
    zetaris_db: str  # Zetaris name of the registered evidence-store datasource
    # Evidence store (PostgreSQL that Zetaris federates)
    pg_dsn: str
    # Meterless Nightshift (primary AI provider)
    meterless_base_url: str
    meterless_token: str
    meterless_app: str
    extractor_model: str
    judge_model: str
    require_nemotron: bool
    # SEC
    sec_user_agent: str
    # Paths
    hmem_path: Path
    log_dir: Path
    out_dir: Path
    sample_dir: Path


def load() -> Settings:
    load_dotenv()
    e = os.environ.get
    return Settings(
        sample_mode=_bool("SAMPLE_MODE"),
        zetaris_base_url=e("ZETARIS_BASE_URL", "http://localhost:3000").rstrip("/"),
        zetaris_org_id=e("ZETARIS_ORG_ID", ""),
        zetaris_api_key=e("ZETARIS_API_KEY", ""),
        zetaris_username=e("ZETARIS_USERNAME", ""),
        zetaris_password=e("ZETARIS_PASSWORD", ""),
        zetaris_engine_id=e("ZETARIS_ENGINE_ID", ""),
        zetaris_db=e("ZETARIS_EVIDENCE_DS", "seismo_pg"),
        pg_dsn=e("EVIDENCE_PG_DSN", "postgresql://seismo:seismo@localhost:5433/seismograph"),
        meterless_base_url=e("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1"),
        meterless_token=e("METERLESS_RUNTIME_TOKEN", ""),
        meterless_app=e("METERLESS_APP", "seismograph"),
        extractor_model=e("EXTRACTOR_MODEL", e("METERLESS_MODEL", "meterless/auto")),
        judge_model=e("JUDGE_MODEL", e("METERLESS_MODEL", "meterless/auto")),
        require_nemotron=_bool("REQUIRE_NEMOTRON", False),
        sec_user_agent=e("SEC_USER_AGENT", ""),
        hmem_path=Path(e("HMEM_PATH", str(ROOT / "data" / "hmem.sqlite"))),
        log_dir=Path(e("LOG_DIR", str(ROOT / "logs"))),
        out_dir=Path(e("OUT_DIR", str(ROOT / "out"))),
        sample_dir=Path(e("SAMPLE_DIR", str(ROOT / "data" / "sample"))),
    )
