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

COMPANY = ["company", "company name", "employer", "employer name", "business name", "company_name", "name"]
NOTICE = ["notice date", "date received", "received date", "warn received date", "notice_date", "date of notice",
          "received", "date"]
EFFECTIVE = ["effective date", "layoff date", "date of layoff", "layoff start date", "effective_date",
             "separation date", "lo/cl date"]
JOBS = ["jobs", "employees affected", "number of workers", "no. of employees", "affected employees",
        "number affected", "total employees", "employees", "# of employees", "workers affected", "jobs affected"]
LOCATION = ["location", "city", "county", "address", "site", "worksite"]


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
