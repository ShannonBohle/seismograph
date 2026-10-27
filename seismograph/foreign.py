"""Official company-disclosure systems outside the US and UK.

Each adapter returns announcements whose headline matches workforce or AI terms, as Candidates for the same
Extractor -> Verifier -> Judge -> editor loop. Quotes stay in the original language and are checked verbatim against
the source; nothing is translated before verification.

  asx     Australia   ASX company announcements (the public JSON feed behind asx.com.au; PDFs)
  bse     India       BSE corporate announcements (the public JSON feed behind bseindia.com; PDFs)
  tdnet   Japan       TDnet timely disclosure, Tokyo Stock Exchange (public daily HTML lists; PDFs)
  cninfo  China       CNINFO, the designated disclosure site for Shanghai and Shenzhen (public search; PDFs)
  amf     France      info-financiere.gouv.fr, France's official storage mechanism (open-data API; PDFs)
  dart    S. Korea    DART OpenAPI (free key required: DART_API_KEY)
  edinet  Japan       EDINET API v2 (free key required: EDINET_API_KEY)

None of these documents a public API: each adapter sends the same requests as the exchange's own public web page.
If a site changes or refuses, the failure is logged and the other sources carry on. Sources that could not be reached
programmatically (HKEXnews: bot protection; SGXNet: per-session token; SEDAR+; Germany's Unternehmensregister) are
listed in the README rather than scraped around.
"""
from __future__ import annotations

import io
import re
import time
from datetime import date, timedelta
from urllib.parse import urlencode

import requests

from .agents import Candidate
from .edgar import html_to_text

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Seismograph research agent"

EN_TERMS = re.compile(r"restructur|redundanc|retrench|workforce|job cut|job loss|headcount|lay-?off|voluntary retirement|"
                      r"\bVRS\b|cost reduction|operating model|artificial intelligence|\bAI\b", re.I)
JA_TERMS = re.compile(r"希望退職|早期退職|人員削減|退職者の募集|構造改革|リストラ|人員の適正化|人工知能|生成AI|ＡＩ")
ZH_TERMS = ["裁员", "人员优化", "减员", "人工智能"]

FR_TERMS = re.compile(r"suppression[s]? de postes|suppression[s]? d.emplois|plan de départs|plan de sauvegarde de l.emploi|"
                      r"\bPSE\b|réduction des effectifs|restructuration|licenciement|intelligence artificielle|\bIA\b", re.I)
KO_TERMS = re.compile(r"구조조정|희망퇴직|인력\s*감축|감원|인원\s*감축|인공지능|\bAI\b")
PT_TERMS = re.compile(r"demiss|desligamento|reestrutura|redução d[eo] (quadro|pessoal|efetivo)|"
                      r"plano de demissão voluntária|\bPDV\b|layoff|intelig[eê]ncia artificial|automação", re.I)

COUNTRY = {"asx": "Australia", "bse": "India", "tdnet": "Japan", "cninfo": "China", "amf": "France",
           "dart": "South Korea", "edinet": "Japan", "cvm": "Brazil"}
MARKET = {"asx": "ASX (Australia)", "bse": "BSE (India)", "tdnet": "TDnet / Tokyo Stock Exchange (Japan)",
          "cninfo": "CNINFO / Shanghai and Shenzhen exchanges (China)", "nsm": "FCA NSM (UK)",
          "amf": "info-financiere.gouv.fr / AMF (France)", "dart": "DART / Financial Supervisory Service (South Korea)",
          "edinet": "EDINET / Financial Services Agency (Japan)",
          "cvm": "CVM / B3 (Brazil)"}
LANGUAGE = {"asx": "English", "bse": "English", "tdnet": "Japanese", "cninfo": "Chinese", "amf": "French",
            "dart": "Korean", "edinet": "Japanese", "cvm": "Portuguese"}


def _days(start: str, end: str) -> list[date]:
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    return [d1 - timedelta(days=i) for i in range((d1 - d0).days + 1)]


def _get(url: str, headers: dict | None = None, **kw) -> requests.Response:
    last = None
    for i in range(3):
        try:
            r = requests.get(url, headers={"User-Agent": UA, **(headers or {})}, timeout=60, **kw)
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last = exc
            time.sleep(2 * 2 ** i)
    raise last


# ------------------------------------------------------------------ Australia: ASX
ASX_FEED = "https://asx.api.markitdigital.com/asx-research/1.0/markets/announcements"
ASX_FILE = "https://cdn-api.markitdigital.com/apiman-gateway/ASX/asx-research/1.0/file/{key}?access_token=83ff96335c2d45a094df02a206a39ff4"


def asx(start: str, end: str, max_pages: int = 60) -> list[dict]:
    out = []
    for page in range(max_pages):
        data = _get(f"{ASX_FEED}?{urlencode({'page': page, 'itemsPerPage': 100})}",
                    {"Referer": "https://www.asx.com.au/"}).json()
        items = (data.get("data") or {}).get("items") or []
        if not items:
            break
        for it in items:
            day = (it.get("date") or "")[:10]
            if day > end:
                continue
            headline = it.get("headline") or ""
            if day >= start and EN_TERMS.search(headline):
                sym = it.get("symbol") or ""
                out.append({"id": it["documentKey"], "issuer": sym, "company": sym, "title": headline,
                            "date": day, "type": ", ".join(it.get("announcementTypes") or []),
                            "url": ASX_FILE.format(key=it["documentKey"])})
        if (items[-1].get("date") or "")[:10] < start:
            break
        time.sleep(0.5)
    return out


# --------------------------------------------------------------------- India: BSE
BSE_FEED = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
BSE_FILE = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{name}"
BSE_HEADERS = {"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}


def bse(start: str, end: str, max_pages_per_day: int = 40) -> list[dict]:
    out = []
    for d in _days(start, end):
        ds = d.strftime("%Y%m%d")
        for page in range(1, max_pages_per_day + 1):
            q = {"pageno": page, "strCat": -1, "strPrevDate": ds, "strScrip": "", "strSearch": "P",
                 "strToDate": ds, "strType": "C", "subcategory": -1}
            rows = (_get(f"{BSE_FEED}?{urlencode(q)}", BSE_HEADERS).json() or {}).get("Table") or []
            for x in rows:
                text = f"{x.get('HEADLINE') or ''} {x.get('NEWSSUB') or ''}"
                if x.get("ATTACHMENTNAME") and EN_TERMS.search(text):
                    out.append({"id": x["NEWSID"], "issuer": str(x.get("SCRIP_CD")), "company": x.get("SLONGNAME") or "",
                                "title": (x.get("NEWSSUB") or x.get("HEADLINE") or "")[:300], "date": ds[:4] + "-" + ds[4:6] + "-" + ds[6:],
                                "type": x.get("CATEGORYNAME") or "", "url": BSE_FILE.format(name=x["ATTACHMENTNAME"])})
            if len(rows) < 50:
                break
            time.sleep(0.4)
    return out


# -------------------------------------------------------------------- Japan: TDnet
TDNET_LIST = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day}.html"
TDNET_FILE = "https://www.release.tdnet.info/inbs/{name}"
_TD_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_TD_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TD_LINK = re.compile(r'href="([^"]+\.pdf)"')


def tdnet(start: str, end: str, max_pages_per_day: int = 15) -> list[dict]:
    out = []
    for d in _days(start, end):
        day = d.strftime("%Y%m%d")
        for page in range(1, max_pages_per_day + 1):
            try:
                r = _get(TDNET_LIST.format(page=page, day=day))
            except requests.HTTPError:
                break  # no more pages for this day (or none at all, e.g. weekends)
            r.encoding = "utf-8"
            rows = _TD_ROW.findall(r.text)
            found = 0
            for row in rows:
                cells = [html_to_text(c) for c in _TD_CELL.findall(row)]
                link = _TD_LINK.search(row)
                if len(cells) < 4 or not link:
                    continue
                found += 1
                title = cells[3]
                if JA_TERMS.search(title) or EN_TERMS.search(title):
                    out.append({"id": link.group(1).rsplit(".", 1)[0], "issuer": cells[1], "company": cells[2],
                                "title": title, "date": d.isoformat(), "type": "適時開示",
                                "url": TDNET_FILE.format(name=link.group(1))})
            if found < 100:
                break
            time.sleep(0.5)
    return out


# -------------------------------------------------------------------- China: CNINFO
CNINFO_QUERY = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_FILE = "https://static.cninfo.com.cn/{path}"


def cninfo(start: str, end: str) -> list[dict]:
    out = []
    for column in ("szse", "sse"):
        for term in ZH_TERMS:
            body = {"pageNum": "1", "pageSize": "30", "column": column, "tabName": "fulltext", "plate": "", "stock": "",
                    "searchkey": term, "secid": "", "category": "", "trade": "", "seDate": f"{start}~{end}",
                    "sortName": "", "sortType": "", "isHLtitle": "true"}
            r = requests.post(CNINFO_QUERY, data=body, timeout=60,
                              headers={"User-Agent": UA, "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
                                       "X-Requested-With": "XMLHttpRequest"})
            r.raise_for_status()
            for a in (r.json().get("announcements") or []):
                title = re.sub(r"<[^>]+>", "", a.get("announcementTitle") or "")
                when = a.get("announcementTime")
                day = date.fromtimestamp(when / 1000).isoformat() if isinstance(when, (int, float)) else ""
                out.append({"id": str(a.get("announcementId")), "issuer": a.get("secCode") or "", "company": a.get("secName") or "",
                            "title": title, "date": day, "type": "公告", "url": CNINFO_FILE.format(path=a.get("adjunctUrl") or "")})
            time.sleep(1.5)
    return out


# ------------------------------------------------------------- France: AMF / DILA
AMF_RECORDS = "https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records"
AMF_SEARCH = ["suppression de postes", "plan de départs", "plan de sauvegarde", "réduction des effectifs",
              "restructuration", "intelligence artificielle"]


def amf(start: str, end: str) -> list[dict]:
    """France's official storage mechanism, published as open data (Licence Ouverte) on info-financiere.gouv.fr."""
    out, seen = [], set()
    for term in AMF_SEARCH:
        where = (f'search(informationdeposee_inf_tit_inf, "{term}") AND uin_dat_amf >= date\'{start}\' '
                 f'AND uin_dat_amf <= date\'{end}\'')
        q = {"limit": 100, "where": where, "order_by": "uin_dat_amf desc"}
        for x in (_get(f"{AMF_RECORDS}?{urlencode(q)}").json().get("results") or []):
            title = x.get("informationdeposee_inf_tit_inf") or ""
            key = x.get("uin_idt_uin") or x.get("url_de_recuperation")
            if key in seen or not FR_TERMS.search(title) or "droit préférentiel" in title:
                continue
            seen.add(key)
            out.append({"id": str(key), "issuer": x.get("identificationsociete_iso_cd_lei") or x.get("identificationsociete_iso_nom_soc") or "",
                        "company": x.get("identificationsociete_iso_nom_soc") or "", "title": title,
                        "date": (x.get("uin_dat_amf") or "")[:10], "type": x.get("type_of_information") or "",
                        "url": x.get("url_de_recuperation") or ""})
        time.sleep(0.5)
    return out


# ------------------------------------------------- Brazil: CVM open data (IPE: fatos relevantes, comunicados)
CVM_IPE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip"
CVM_CATEGORIES = ("fato relevante", "comunicado ao mercado", "aviso aos acionistas")


def cvm(start: str, end: str) -> list[dict]:
    """Brazil's securities regulator (CVM) publishes every listed company's periodic and occasional filings
    (IPE) as open data: one zip per year holding a semicolon-separated CSV with a download link per filing."""
    import csv, zipfile
    out, seen = [], set()
    STATS["cvm"] = {"rows": 0, "in_window": 0, "material_or_notice": 0, "columns": []}
    for year in sorted({start[:4], end[:4]}):
        data = _get(CVM_IPE.format(year=year)).content
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                raw = z.read(name)
                for enc in ("utf-8-sig", "latin-1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                for row in csv.DictReader(io.StringIO(text), delimiter=";"):
                    row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                    st = STATS["cvm"]; st["rows"] += 1; st["columns"] = st["columns"] or sorted(row)
                    day = (row.get("data_entrega") or row.get("data_referencia") or "")[:10]
                    cat = (row.get("categoria") or "").lower()
                    subject = " ".join(filter(None, [row.get("assunto"), row.get("tipo"), row.get("especie")]))
                    if not (start <= day <= end):
                        continue
                    st["in_window"] += 1
                    if not any(c in cat for c in CVM_CATEGORIES):
                        continue
                    st["material_or_notice"] += 1
                    if not PT_TERMS.search(subject):
                        continue
                    key = row.get("protocolo_entrega") or row.get("link_download")
                    if key in seen or not row.get("link_download"):
                        continue
                    seen.add(key)
                    out.append({"id": key, "issuer": re.sub(r"\D", "", row.get("cnpj_companhia") or "") or row.get("codigo_cvm", ""),
                                "company": row.get("nome_companhia") or "", "title": f"{row.get('categoria')}: {subject}"[:300],
                                "date": day, "type": row.get("categoria") or "", "url": row["link_download"]})
    return out


# ------------------------------------------------- South Korea: DART (needs a key)
def dart(start: str, end: str, max_pages: int = 30) -> list[dict]:
    """DART OpenAPI (opendart.fss.or.kr). Needs a free key in DART_API_KEY; without one the source is skipped."""
    import os
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        raise RuntimeError("DART_API_KEY not set: register at opendart.fss.or.kr to enable South Korea")
    out = []
    for page in range(1, max_pages + 1):
        q = {"crtfc_key": key, "bgn_de": start.replace("-", ""), "end_de": end.replace("-", ""), "page_no": page,
             "page_count": 100}
        data = _get(f"https://opendart.fss.or.kr/api/list.json?{urlencode(q)}").json()
        for x in data.get("list") or []:
            if KO_TERMS.search(x.get("report_nm") or ""):
                out.append({"id": x["rcept_no"], "issuer": x.get("corp_code") or "", "company": x.get("corp_name") or "",
                            "title": x.get("report_nm") or "", "date": f"{x['rcept_dt'][:4]}-{x['rcept_dt'][4:6]}-{x['rcept_dt'][6:]}",
                            "type": "공시", "url": f"https://opendart.fss.or.kr/api/document.xml?rcept_no={x['rcept_no']}"})
        if page >= int(data.get("total_page") or 1):
            break
        time.sleep(0.5)
    return out


# ---------------------------------------------------- Japan: EDINET (needs a key)
def edinet(start: str, end: str) -> list[dict]:
    """EDINET API v2 (Financial Services Agency). Needs a free key in EDINET_API_KEY; without one it is skipped."""
    import os
    key = os.environ.get("EDINET_API_KEY", "")
    if not key:
        raise RuntimeError("EDINET_API_KEY not set: register at disclosure2.edinet-fsa.go.jp to enable EDINET")
    out = []
    for d in _days(start, end):
        q = {"date": d.isoformat(), "type": 2, "Subscription-Key": key}
        for x in _get(f"https://api.edinet-fsa.go.jp/api/v2/documents.json?{urlencode(q)}").json().get("results") or []:
            desc = x.get("docDescription") or ""
            if JA_TERMS.search(desc) and x.get("pdfFlag") == "1":
                out.append({"id": x["docID"], "issuer": x.get("edinetCode") or "", "company": x.get("filerName") or "",
                            "title": desc, "date": d.isoformat(), "type": "有価証券報告書等",
                            "url": f"https://api.edinet-fsa.go.jp/api/v2/documents/{x['docID']}?type=2"})
        time.sleep(0.5)
    return out


STATS: dict = {}
ADAPTERS = {"asx": asx, "bse": bse, "tdnet": tdnet, "cninfo": cninfo, "amf": amf, "dart": dart, "edinet": edinet, "cvm": cvm}
KEYED = {"dart": "DART_API_KEY", "edinet": "EDINET_API_KEY"}


# ------------------------------------------------------------------ shared plumbing
def search(replay, source: str, start: str, end: str) -> list[dict]:
    request = {"source": source, "start": start, "end": end}
    if replay.sample_mode:
        return replay.load("foreign_search", request)
    hits = ADAPTERS[source](start, end)
    replay.save("foreign_search", request, hits)
    return hits


def candidates(replay, log, source: str, start: str, end: str, known: set[str]) -> list[Candidate]:
    try:
        hits = search(replay, source, start, end)
    except Exception as exc:
        log.log("Scout", f"{source}_search", status="failure", input_summary=f"{MARKET[source]} {start}..{end}",
                output_summary=f"{type(exc).__name__}: {exc}"[:200])
        return []
    log.log("Scout", f"{source}_search", input_summary=f"{MARKET[source]} {start}..{end}",
            output_summary=f"{len(hits)} announcements with workforce or AI terms in the headline"
                           + (f" (scanned: {STATS[source]})" if STATS.get(source) else ""))
    out, seen = [], set()
    for h in hits:
        doc_id = f"{source.upper()}:{h['id']}"
        if doc_id in seen or doc_id in known or not h.get("url"):
            continue
        seen.add(doc_id)
        lang = LANGUAGE[source]
        note = "" if lang == "English" else f" (original language {lang}; quote verbatim in {lang})"
        out.append(Candidate(accession=doc_id, cik=f"{source.upper()}:{h['issuer']}", company=h["company"],
                             form=MARKET[source], file_date=h["date"], items=h["title"][:200], doc_id=doc_id,
                             url=h["url"], doc_type=f"{COUNTRY[source]} announcement: {h['title'][:80]}{note}"))
    return out


_last = [0.0]


def fetch_text(replay, url: str, attempts: int = 4) -> str:
    """Download an announcement (PDF or HTML) and return its text. Spaced and retried."""
    request = {"url": url}
    if replay.sample_mode:
        return replay.load("sec_doc", request)
    import os
    headers = {"User-Agent": UA}
    if "bseindia" in url:
        headers.update(BSE_HEADERS)
    fetch_url = url
    if "opendart.fss.or.kr" in url:
        fetch_url = f"{url}&crtfc_key={os.environ.get('DART_API_KEY', '')}"
    elif "edinet-fsa.go.jp" in url:
        fetch_url = f"{url}&Subscription-Key={os.environ.get('EDINET_API_KEY', '')}"
    last = None
    for i in range(attempts):
        wait = 1.0 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            r = requests.get(fetch_url, headers=headers, timeout=90)
            r.raise_for_status()
            if r.content[:2] == b"PK":  # DART returns the filing as a zip of XML/HTML files
                text = zip_text(r.content)
            elif r.content[:4] == b"%PDF" or "pdf" in (r.headers.get("content-type") or "").lower():
                text = pdf_text(r.content)
            else:
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding
                text = html_to_text(r.text)
            replay.save("sec_doc", request, text)
            return text
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            last = exc
            time.sleep(2 * 2 ** i)
    raise last


def zip_text(data: bytes) -> str:
    import zipfile
    parts = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            raw = z.read(name)
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    parts.append(html_to_text(raw.decode(enc)))
                    break
                except UnicodeDecodeError:
                    continue
    return "\n\n".join(parts).strip()


def pdf_text(data: bytes, max_pages: int = 60) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:max_pages]:
            parts.append(page.extract_text() or "")
    text = "\n\n".join(parts)
    return re.sub(r"[ \t]+", " ", text).strip()
