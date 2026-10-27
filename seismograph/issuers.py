"""Where each employer is: market (SEC or UK FCA) and country, so coverage claims can be counted, not asserted.

Country comes from the business address in SEC submissions data (data.sec.gov/submissions). For announcements from
other exchanges (UK, Australia, India, Japan, China) the country is the listing market, labelled as such.
"""
from __future__ import annotations

US_CODES = set(("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
                "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU AS MP").split())
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def country_name(name: str | None) -> str | None:
    """SEC describes Canadian addresses by province ("Ontario, Canada", "Canada (Federal Level)"); report the country."""
    if not name:
        return name
    if "canada" in name.lower():
        return "Canada"
    return name


def _sec_country(edgar, cik: str) -> tuple[str | None, str]:
    url = SUBMISSIONS_URL.format(cik=int(cik))
    data = edgar.get_json(url)
    addr = ((data or {}).get("addresses") or {}).get("business") or {}
    code = (addr.get("stateOrCountry") or "").strip().upper()
    desc = (addr.get("stateOrCountryDescription") or "").strip()
    if not code:
        return None, url
    if code in US_CODES:
        return "United States", url
    return country_name((desc.title() if desc.isupper() else desc) or code), url


def ensure(ctx, rows: list[dict]) -> dict[str, dict]:
    """Make sure every employer in `rows` (dicts with cik and company) has an issuers row; return them by id."""
    z = ctx.zetaris
    have = {str(r["issuer_id"]): r for r in z.query(
        f"SELECT issuer_id, company, market, country, country_source FROM {z.t('issuers')}",
        agent="Publisher", purpose="known employer locations")}
    for r in rows:
        iid = str(r.get("cik") or "")
        if not iid or (iid in have and not (iid.startswith("LEI:") and not have[iid].get("country"))):
            continue
        prefix = iid.split(":")[0]
        if iid.startswith("LEI:"):
            row = {"issuer_id": iid, "company": r["company"], "market": "FCA NSM (UK)", "country": "United Kingdom",
                   "country_source": "listing market (UK regulated announcement); not necessarily the company's domicile"}
        elif prefix.lower() in __import__("seismograph.foreign", fromlist=["ADAPTERS"]).ADAPTERS:
            from . import foreign
            src = prefix.lower()
            row = {"issuer_id": iid, "company": r["company"], "market": foreign.MARKET[src], "country": foreign.COUNTRY[src],
                   "country_source": "listing market; not necessarily the company's domicile"}
        elif iid.isdigit():
            try:
                country, src = _sec_country(ctx.edgar, iid)
            except Exception as exc:  # sample recording missing, network: leave it unknown
                country, src = None, f"lookup failed: {type(exc).__name__}"
            row = {"issuer_id": iid, "company": r["company"], "market": "SEC (US)", "country": country,
                   "country_source": src}
        else:
            continue
        ctx.store.upsert("issuers", row, "issuer_id")
        ctx.log.log("Publisher", "issuer_location", input_summary=r["company"],
                    output_summary=f"{row['market']}; country {row['country'] or 'not known'}")
        have[iid] = row
    return have
