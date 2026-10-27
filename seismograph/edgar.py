"""SEC EDGAR access: full-text search for candidate filings and document download.

SEC fair-access rules: a descriptive User-Agent with contact email is required, and no
more than 10 requests per second. This module sleeps between calls to stay well under.
"""
from __future__ import annotations

import html
import re
import time
from html.parser import HTMLParser
from urllib.parse import urlencode

import requests

from .replay import Replay

FTS_URL = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
_last_call = [0.0]


class _Text(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    text = html.unescape("".join(p.parts)).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


class Edgar:
    def __init__(self, user_agent: str, replay: Replay):
        if not user_agent and not replay.sample_mode:
            raise RuntimeError("Set SEC_USER_AGENT, e.g. 'Seismograph research you@example.com' (SEC requires it).")
        self.ua = user_agent
        self.replay = replay

    def _get(self, url: str) -> requests.Response:
        wait = 0.2 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        r = requests.get(url, headers={"User-Agent": self.ua, "Accept-Encoding": "gzip, deflate"}, timeout=60)
        r.raise_for_status()
        return r

    def search(self, query: str, forms: str, start: str, end: str, max_hits: int = 40,
               ciks: str | None = None) -> list[dict]:
        """Full-text search. Returns one dict per matching document. ciks: comma-separated 10-digit CIKs."""
        request = {"q": query, "forms": forms, "dateRange": "custom", "startdt": start, "enddt": end}
        if ciks:
            request["ciks"] = ciks
        if self.replay.sample_mode:
            payload = self.replay.load("sec_search", request)
        else:
            payload = self._get(f"{FTS_URL}?{urlencode(request)}").json()
            self.replay.save("sec_search", request, payload)
        hits = []
        for h in (payload.get("hits", {}) or {}).get("hits", [])[:max_hits]:
            src = h.get("_source", {})
            _id = h.get("_id", "")
            adsh = src.get("adsh") or _id.split(":")[0]
            filename = _id.split(":", 1)[1] if ":" in _id else ""
            ciks = src.get("ciks") or [""]
            names = src.get("display_names") or [""]
            cik = str(int(ciks[0])) if str(ciks[0]).isdigit() else str(ciks[0])
            company = re.sub(r"\s*\(.*$", "", names[0]).strip()
            hits.append({
                "accession": adsh, "cik": cik, "company": company, "form": src.get("form") or src.get("root_form"),
                "file_date": src.get("file_date"), "items": ",".join(src.get("items") or []),
                "filename": filename, "file_type": src.get("file_type") or "",
                "url": f"{ARCHIVES}/{cik}/{adsh.replace('-', '')}/{filename}" if filename else "",
                "index_url": f"{ARCHIVES}/{cik}/{adsh.replace('-', '')}/",
            })
        return hits

    def get_json(self, url: str):
        """SEC JSON endpoints (company tickers, XBRL company facts), recorded for sample mode."""
        request = {"url": url}
        if self.replay.sample_mode:
            return self.replay.load("sec_json", request)
        data = self._get(url).json()
        self.replay.save("sec_json", request, data)
        return data

    def fetch_text(self, url: str) -> str:
        request = {"url": url}
        if self.replay.sample_mode:
            return self.replay.load("sec_doc", request)
        text = html_to_text(self._get(url).text)
        self.replay.save("sec_doc", request, text)
        return text
