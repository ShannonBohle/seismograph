"""Independent verbatim check of published quotes.

Does not import or copy the repository verifier. For each record in
out/records.json this script downloads source_url, converts the payload
to plain text, collapses whitespace, and tests whether ai_quote and
jobs_cut_text appear as substrings. It also recomputes the WARN headline
from the counts already stored in out/gap.json.
"""
from __future__ import annotations

import json
import re
import sys
import time
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "out" / "records.json"
GAP = ROOT / "out" / "gap.json"
OUT = Path(__file__).resolve().parent / "quote-verification.json"

USER_AGENT = (
    "SeismographReplication/1.0 (independent quote check; your-email@example.com)"
)
MIN_INTERVAL = 0.5  # at most 2 requests per second
TIMEOUT = 60


class _HtmlText(HTMLParser):
    SKIP = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag in {"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "td", "th", "blockquote"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "tr", "li", "h1", "h2", "h3", "h4", "td", "th"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    parser = _HtmlText()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return raw
    return "".join(parser.parts)


def pdf_to_text(data: bytes) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(BytesIO(data)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def normalise_whitespace(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\ufeff", " ")
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    return re.sub(r"\s+", " ", text).strip()


def looks_like_pdf(url: str, content_type: str, data: bytes) -> bool:
    if "pdf" in (content_type or "").lower():
        return True
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return True
    return data[:5] == b"%PDF-"


def looks_like_html(content_type: str, data: bytes) -> bool:
    ct = (content_type or "").lower()
    if "html" in ct or "xml" in ct or "xhtml" in ct:
        return True
    head = data[:400].lstrip().lower()
    return head.startswith(b"<!doctype") or head.startswith(b"<html") or head.startswith(b"<?xml")


def to_plain_text(url: str, content_type: str, data: bytes) -> str:
    if looks_like_pdf(url, content_type, data):
        return pdf_to_text(data)
    text = data.decode("utf-8", errors="replace")
    if looks_like_html(content_type, data) or "<" in text[:2000]:
        return html_to_text(text)
    return text


def contains_verbatim(haystack: str, needle: str | None) -> tuple[bool | None, str]:
    if needle is None:
        return None, "field is null; nothing to check"
    needle_n = normalise_whitespace(str(needle))
    if not needle_n:
        return None, "field is empty; nothing to check"
    return (needle_n in haystack), f"looked for {needle_n!r}"


def fetch(session: requests.Session, url: str, last_at: list[float]) -> tuple[int, str, bytes]:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    last_exc = None
    for attempt in range(3):
        wait = MIN_INTERVAL - (time.monotonic() - last_at[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = session.get(url, headers=headers, timeout=TIMEOUT, allow_redirects=True)
            last_at[0] = time.monotonic()
            return r.status_code, r.headers.get("Content-Type", ""), r.content
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            last_at[0] = time.monotonic()
            time.sleep(1.5 * (attempt + 1))
    raise last_exc


def recompute_headline(gap: dict) -> dict:
    warn = gap.get("warn") or {}
    citing = int(warn.get("notices_citing_ai") or 0)
    with_reason = int(warn.get("notices_with_reason") or 0)
    states_reason = list(warn.get("states_recording_a_reason") or [])
    states_loaded = list(warn.get("states_loaded") or [])
    share = (citing / with_reason) if with_reason else None
    recomputed = (
        f"{citing:,} of {with_reason:,} WARN layoff notices that state a reason "
        f"cite AI or automation ({len(states_reason)} of {len(states_loaded)} "
        f"states loaded record a reason)."
    )
    published = gap.get("headline") or ""
    ai_notices = warn.get("ai_notices") or []
    reasons = [str(n.get("reason") or "") for n in ai_notices]
    reason_mentions_ai = [
        bool(re.search(r"artificial intelligence|automation|machine learning|\bai\b", r, re.I))
        for r in reasons
    ]
    return {
        "notices_citing_ai": citing,
        "notices_with_reason": with_reason,
        "share": share,
        "share_percent": None if share is None else round(share * 100, 4),
        "states_recording_a_reason": states_reason,
        "states_loaded_count": len(states_loaded),
        "recomputed_headline": recomputed,
        "published_headline": published,
        "headline_matches_counts": recomputed == published,
        "ai_notice_reasons": reasons,
        "ai_notice_reasons_mention_ai_or_automation": reason_mentions_ai,
    }


def _print(msg: str) -> None:
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    records = json.loads(RECORDS.read_text(encoding="utf-8"))
    gap = json.loads(GAP.read_text(encoding="utf-8"))
    session = requests.Session()
    last_at = [0.0]
    results = []
    quote_ok = 0
    jobs_ok = 0
    quote_fail = 0
    jobs_fail = 0
    fetch_fail = 0

    for rec in records:
        rid = rec.get("record_id")
        url = rec.get("source_url") or ""
        row = {
            "record_id": rid,
            "company": rec.get("company"),
            "source_url": url,
            "ai_attribution": rec.get("ai_attribution"),
            "status": None,
        }
        try:
            status, content_type, data = fetch(session, url, last_at)
            row["http_status"] = status
            row["content_type"] = content_type
            row["bytes"] = len(data)
            if status >= 400 or not data:
                row["status"] = "fetch_fail"
                row["error"] = f"HTTP {status} or empty body"
                fetch_fail += 1
                results.append(row)
                _print(f"FAIL fetch  {rid}  {rec.get('company')}  HTTP {status}")
                continue
            text = to_plain_text(url, content_type, data)
            norm = normalise_whitespace(text)
            row["plain_text_chars"] = len(norm)
            q_ok, q_note = contains_verbatim(norm, rec.get("ai_quote"))
            j_ok, j_note = contains_verbatim(norm, rec.get("jobs_cut_text"))
            row["ai_quote"] = rec.get("ai_quote")
            row["jobs_cut_text"] = rec.get("jobs_cut_text")
            row["ai_quote_found"] = q_ok
            row["jobs_cut_text_found"] = j_ok
            row["ai_quote_note"] = q_note
            row["jobs_cut_text_note"] = j_note
            checks = [v for v in (q_ok, j_ok) if v is not None]
            if q_ok is True:
                quote_ok += 1
            elif q_ok is False:
                quote_fail += 1
            if j_ok is True:
                jobs_ok += 1
            elif j_ok is False:
                jobs_fail += 1
            row["status"] = "pass" if checks and all(checks) or not checks else "fail"
            if not checks:
                row["status"] = "pass_nothing_to_check"
            mark = "PASS" if row["status"].startswith("pass") else "FAIL"
            _print(f"{mark}  {rid}  {rec.get('company')}  quote={q_ok}  jobs={j_ok}")
        except Exception as exc:
            row["status"] = "fetch_fail"
            row["error"] = f"{type(exc).__name__}: {exc}"
            fetch_fail += 1
            _print(f"FAIL fetch  {rid}  {rec.get('company')}  {row['error']}")
        results.append(row)

    headline = recompute_headline(gap)
    _print("\nHeadline recompute:")
    _print(f"  published : {headline['published_headline']}")
    _print(f"  recomputed: {headline['recomputed_headline']}")
    _print(f"  share     : {headline['share']} ({headline['share_percent']}%)")
    _print(f"  match     : {headline['headline_matches_counts']}")

    payload = {
        "records_checked": len(results),
        "quote_pass": quote_ok,
        "quote_fail": quote_fail,
        "jobs_cut_text_pass": jobs_ok,
        "jobs_cut_text_fail": jobs_fail,
        "fetch_fail": fetch_fail,
        "headline": headline,
        "results": results,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _print(f"\nWrote {OUT}")
    any_fail = quote_fail or jobs_fail or fetch_fail or not headline["headline_matches_counts"]
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
