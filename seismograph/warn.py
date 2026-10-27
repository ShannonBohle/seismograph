"""Load WARN Act notices into the evidence store.

Input: CSV files produced by Big Local News' warn-scraper (https://github.com/biglocalnews/warn-scraper,
Apache-2.0), one file per state, e.g. `warn-scraper CA NY TX --data-dir ./warn`. Column names
differ by state, so this loader maps the common variants and skips rows it cannot read.
"""
from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path

from .store import Store, norm_company

COMPANY = ["company", "company name", "employer", "employer name", "business name", "company_name",
           "business legal name", "job_site_name", "location name", "doing business as name", "name"]
NOTICE = ["notice date", "date received", "received date", "warn received date", "notice_date", "date of notice",
          "date of warn notice", "notification date(s)", "initial date reported", "wfdd_received_date",
          "received", "date"]
EFFECTIVE = ["effective date", "layoff date", "date of layoff", "layoff start date", "effective_date",
             "separation date", "lo/cl date", "date layoff/closure starts", "layoff_date", "impact date"]
JOBS = ["jobs", "employees affected", "number of workers", "no. of employees", "affected employees",
        "number affected", "total employees", "employees", "# of employees", "workers affected", "jobs affected",
        "number of affected workers", "total_layoff_number", "expected layoff", "revised layoff"]
LOCATION = ["location", "city", "county", "address", "site", "worksite"]
REASON = ["reason for layoff", "reason for layoffs", "layoff reason", "reason", "reasons", "reason for dislocation",
          "cause", "comments reason"]


def _reason(row: dict) -> str | None:
    """Every reason or cause column this state's file has (most have none), joined, verbatim."""
    parts = []
    for k, v in row.items():
        key = (k or "").strip().lower()
        if ("reason" in key or key in ("cause", "causes")) and str(v or "").strip():
            parts.append(str(v).strip())
    return " | ".join(dict.fromkeys(parts)) or None


def _pick(row: dict, names: list[str]) -> str | None:
    lower = {k.strip().lower(): v for k, v in row.items() if k}
    for n in names:
        if n in lower and str(lower[n]).strip():
            return str(lower[n]).strip()
    for k, v in lower.items():  # looser match: header contains the name
        if any(n in k for n in names[:3]) and str(v).strip():
            return str(v).strip()
    return None


def _date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()[:30]
    for candidate in (s, s.split(" ")[0], s.split("T")[0]):
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%b %d, %Y", "%B %d, %Y", "%m-%d-%Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue
    return None


def _int(s: str | None) -> int | None:
    m = re.search(r"\d[\d,]*", s or "")
    return int(m.group(0).replace(",", "")) if m else None


def load(store: Store, files: list[Path], log) -> dict:
    counts = {}
    for f in files:
        state = f.stem.split("_")[0].upper()[:2]
        n = 0
        with f.open(newline="", encoding="utf-8", errors="replace") as fh:
            for i, row in enumerate(csv.DictReader(fh)):
                company = _pick(row, COMPANY)
                if not company:
                    continue
                key = hashlib.sha1(f"{state}|{i}|{company}|{_pick(row, NOTICE)}".encode()).hexdigest()[:16]
                reason = _reason(row)
                if reason:
                    store.upsert("warn_reasons", {"notice_id": f"{state}:{key}", "state": state, "reason": reason[:2000]},
                                 "notice_id")
                store.upsert("warn_notices", {
                    "notice_id": f"{state}:{key}", "state": state, "company": company,
                    "company_norm": norm_company(company), "notice_date": _date(_pick(row, NOTICE)),
                    "effective_date": _date(_pick(row, EFFECTIVE)), "jobs": _int(_pick(row, JOBS)),
                    "location": _pick(row, LOCATION), "source": f"warn-scraper (Big Local News), {state}",
                }, "notice_id")
                n += 1
        counts[state] = counts.get(state, 0) + n
        log.log("WarnLoader", "load_warn", input_summary=str(f.name), output_summary=f"{n} notices for {state}")
    return counts


STATES = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
          "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY").split()


def scrape_all(data_dir: Path, log, workers: int = 6, timeout_s: int = 600) -> dict:
    """Run Big Local News' warn-scraper for every state it supports; record which worked and which did not."""
    import shutil
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    exe = shutil.which("warn-scraper") or str(Path(sys.executable).with_name(
        "warn-scraper.exe" if sys.platform == "win32" else "warn-scraper"))
    data_dir.mkdir(parents=True, exist_ok=True)

    def one(state: str) -> tuple[str, str, str]:
        try:
            r = subprocess.run([exe, state, "--data-dir", str(data_dir)], capture_output=True, text=True,
                               timeout=timeout_s)
            ok = r.returncode == 0
            return state, "ok" if ok else "failed", (r.stderr or r.stdout).strip().splitlines()[-1:][0][:200] if not ok and (r.stderr or r.stdout).strip() else ""
        except subprocess.TimeoutExpired:
            return state, "timeout", f"no result after {timeout_s}s"
        except Exception as exc:  # scraper missing for this state, network, etc.
            return state, "failed", str(exc)[:200]

    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for state, status, detail in pool.map(one, STATES):
            results[state] = status
            log.log("WarnLoader", "scrape_state", status="success" if status == "ok" else "failure",
                    input_summary=state, output_summary=detail or status)
    return results
