"""UK regulatory announcements from the FCA National Storage Mechanism (NSM).

The NSM (https://data.fca.org.uk/#/nsm/nationalstoragemechanism) is the FCA's public archive of regulated
announcements by UK-listed companies (RNS and other news services, annual and half-year reports). Its terms allow use
of the material for any lawful purpose. The FCA does not document a programmatic API; this module sends the same
search request the public search page sends, and downloads the announcement text from the same public links.
If the service changes, the search fails, the failure is logged, and nothing else in Seismograph is affected.

There is no public UK equivalent of US WARN notices: HR1 advance notifications of collective redundancies go to the
Insolvency Service and are not published.
"""
from __future__ import annotations

import json

import requests

from .agents import Candidate

SEARCH_URL = "https://api.data.fca.org.uk/search?index=nsm-search"
ARTEFACT_BASE = "https://data.fca.org.uk/artefacts/"
HEADERS = {"Content-Type": "application/json", "Origin": "https://data.fca.org.uk",
           "Referer": "https://data.fca.org.uk/"}
QUERIES = ["redundancies artificial intelligence", "redundancies automation", "headcount artificial intelligence",
           "workforce reduction artificial intelligence", "job cuts artificial intelligence",
           "restructuring roles artificial intelligence"]
TEXT_FORMATS = {"Plain text", "HTML"}   # announcements; PDFs (prospectuses, annual reports) are skipped


def request_body(words: str, start: str, end: str, size: int = 100) -> dict:
    match = [["match_all_words", words]]
    return {"from": 0, "size": size,
            "primarySearch": [{"name": "document_content", "value": match}, {"name": "headline", "value": match}],
            "latestFlag": "Y",
            "criteriaObj": {"criteria": [], "dateCriteria": [{"name": "publication_date", "value": {
                "from": f"{start}T00:00:00Z", "to": f"{end}T23:59:59Z"}}]},
            "sort": "submitted_date", "sortorder": "desc"}


def search(replay, words: str, start: str, end: str, size: int = 100) -> list[dict]:
    body = request_body(words, start, end, size)
    request = {"q": words, "start": start, "end": end, "size": size}
    if replay.sample_mode:
        payload = replay.load("uk_search", request)
    else:
        r = requests.post(SEARCH_URL, headers=HEADERS, data=json.dumps(body), timeout=60)
        r.raise_for_status()
        payload = r.json()
        replay.save("uk_search", request, payload)
    hits = []
    for h in (payload.get("hits") or {}).get("hits", []):
        s = h.get("_source", {})
        link = s.get("download_link") or ""
        if not link or s.get("document_format") not in TEXT_FORMATS:
            continue
        hits.append({
            "disclosure_id": s.get("disclosure_id") or s.get("seq_id") or h.get("_id"),
            "company": (s.get("company") or "").rstrip("; ").strip(),
            "headline": s.get("headline") or "",
            "type": s.get("type") or "",
            "published": (s.get("publication_date") or s.get("submitted_date") or "")[:10],
            "lei": s.get("lei") or "",
            "news_service": s.get("source") or "",
            "url": link if link.startswith("http") else ARTEFACT_BASE + link,
        })
    return hits


def candidates(replay, log, start: str, end: str, known: set[str], queries: list[str] | None = None) -> list[Candidate]:
    seen, out = set(), []
    for q in queries or QUERIES:
        try:
            hits = search(replay, q, start, end)
        except Exception as exc:  # one failing query should not stop the run
            log.log("Scout", "uk_search", status="failure", input_summary=q, output_summary=f"{type(exc).__name__}: {exc}"[:200])
            continue
        log.log("Scout", "uk_search", input_summary=f"FCA NSM | {q} | {start}..{end}",
                output_summary=f"{len(hits)} announcements in text format")
        for h in hits:
            doc_id = f"NSM:{h['disclosure_id']}"
            if doc_id in seen or doc_id in known:
                continue
            seen.add(doc_id)
            out.append(Candidate(accession=f"NSM:{h['disclosure_id']}", cik=f"LEI:{h['lei']}" if h["lei"] else "",
                                 company=h["company"], form=f"UK {h['news_service']}".strip(),
                                 file_date=h["published"], items=h["headline"][:200], doc_id=doc_id, url=h["url"],
                                 doc_type=f"UK announcement: {h['type']}"[:80]))
    return out


def probe(log, words: str = "redundancies") -> dict:
    """Show whether the NSM search answers and how many announcements match."""
    from datetime import date, timedelta
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=90)).isoformat()
    try:
        r = requests.post(SEARCH_URL, headers=HEADERS, data=json.dumps(request_body(words, start, end, 5)), timeout=30)
        status = r.status_code
        data = r.json() if status == 200 else {}
        total = ((data.get("hits") or {}).get("total") or {}).get("value")
        first = [((h.get("_source") or {}).get("company"), (h.get("_source") or {}).get("headline"))
                 for h in (data.get("hits") or {}).get("hits", [])[:3]]
        sample = r.text[:300] if status != 200 else ""
    except Exception as exc:
        status, total, first, sample = None, None, [], f"{type(exc).__name__}: {exc}"[:300]
    ok = status == 200
    log.log("UKSource", "nsm_probe", status="success" if ok else "failure", input_summary=words,
            output_summary=f"HTTP {status}; {total} matches in the last 90 days")
    return {"http_status": status, "reachable": ok, "matches_last_90_days": total, "first": first, "error": sample}


_last = [0.0]


def fetch_text(replay, url: str, attempts: int = 4) -> str:
    """Download one announcement. The FCA server sometimes resets connections, so space calls and retry with backoff."""
    import time

    from .edgar import html_to_text
    request = {"url": url}
    if replay.sample_mode:
        return replay.load("sec_doc", request)
    last_exc = None
    for i in range(attempts):
        wait = 1.5 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Seismograph research agent)",
                                           "Referer": "https://data.fca.org.uk/"}, timeout=60)
            r.raise_for_status()
            text = html_to_text(r.text)
            replay.save("sec_doc", request, text)
            return text
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last_exc = exc
            time.sleep(2 * 2 ** i)
    raise last_exc
