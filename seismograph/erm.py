"""European restructuring events from Eurofound's European Restructuring Monitor (ERM).

Europe has no public equivalent of US WARN notices: collective-redundancy notifications go to national authorities and
are not published. The ERM (https://apps.eurofound.europa.eu/restructuring-events/) is the closest public record. It is
compiled by Eurofound's correspondents from national media and company websites, covers events of roughly 100 jobs or
more in the EU, Norway and the UK, and gives each event a type, a job change and a written description.

Two limits matter for how it is used here: the descriptions are Eurofound's summaries, not the employer's words, and
an AI mention in a description can come from a union or a journalist. So the ERM is used as a regulator-style
comparison source (does the event exist, what reason does the summary give), never as a source of employer quotes.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime
from urllib.parse import urlencode

import requests

from .edgar import html_to_text
from .store import norm_company

BASE = "https://apps.eurofound.europa.eu/restructuring-events"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Seismograph research agent"
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_DETAIL = re.compile(r'href="(?:/restructuring-events)?/detail/(\d+)"')


def _get(url: str) -> str:
    last = None
    for i in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status()
            return r.text
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last = exc
            time.sleep(2 * 2 ** i)
    raise last


def _date(s: str) -> str | None:
    try:
        return datetime.strptime(s.strip(), "%d %B %Y").date().isoformat()
    except ValueError:
        return None


def events(start: str, end: str, max_pages: int = 80) -> list[dict]:
    out, seen = [], set()
    for page in range(1, max_pages + 1):
        q = {"company": "", "date-from": start, "date-to": end}
        if page > 1:
            q["page"] = page
        html = _get(f"{BASE}/search?{urlencode(q)}")
        rows = 0
        for row in _ROW.findall(html):
            cells = [html_to_text(c) for c in _CELL.findall(row)]
            m = _DETAIL.search(row)
            if len(cells) < 6 or not m or m.group(1) in seen:
                continue
            rows += 1
            seen.add(m.group(1))
            change = re.sub(r"[^\d+-]", "", cells[5])
            out.append({"event_id": m.group(1), "announced": _date(cells[0]), "country": cells[1], "company": cells[2],
                        "sector": cells[3], "type": cells[4], "employment_change": int(change) if re.fullmatch(r"[+-]?\d+", change) else None,
                        "url": f"{BASE}/detail/{m.group(1)}"})
        if rows == 0:
            break
        time.sleep(0.5)
    return out


def description(url: str) -> str:
    text = html_to_text(_get(url))
    i = text.find("Description")
    if i < 0:
        return ""
    body = text[i + len("Description"):]
    for stop in ("\nSources", "\nRelated", "\nEurofound apps"):
        j = body.find(stop)
        if j > 0:
            body = body[:j]
    return re.sub(r"\s+", " ", body).strip()[:4000]


def load(ctx, start: str, end: str, with_descriptions: bool = True) -> dict:
    replay, store, log = ctx.replay, ctx.store, ctx.log
    request = {"start": start, "end": end}
    if replay.sample_mode:
        evs = replay.load("erm_events", request)
    else:
        evs = events(start, end)
        if with_descriptions:
            for e in evs:
                try:
                    e["description"] = description(e["url"])
                except Exception as exc:
                    e["description"] = ""
                    log.log("ERMLoader", "erm_detail", status="failure", input_summary=e["url"], output_summary=str(exc)[:200])
                time.sleep(0.4)
        replay.save("erm_events", request, evs)
    for e in evs:
        store.upsert("restructuring_events", {
            "event_id": f"ERM:{e['event_id']}", "country": e["country"], "company": e["company"],
            "company_norm": norm_company(e["company"]), "announced": e["announced"], "sector": e["sector"],
            "event_type": e["type"], "employment_change": e["employment_change"],
            "description": e.get("description") or "", "source_url": e["url"],
            "source": "Eurofound European Restructuring Monitor (media-compiled)",
            "sha256": hashlib.sha256((e.get("description") or "").encode()).hexdigest()}, "event_id")
    countries = sorted({e["country"] for e in evs})
    log.log("ERMLoader", "load_erm", input_summary=f"{start}..{end}",
            output_summary=f"{len(evs)} events, {len(countries)} countries")
    return {"events": len(evs), "countries": countries}
