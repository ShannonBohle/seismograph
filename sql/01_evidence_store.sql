-- Evidence store for Seismograph (PostgreSQL). Zetaris federates these tables as the
-- datasource seismo_pg (see sql/02_zetaris_register.sql). Applied automatically by
-- `python -m seismograph init-db`.

CREATE TABLE IF NOT EXISTS filings (
    accession      TEXT PRIMARY KEY,          -- SEC accession number, e.g. 0001193125-25-012345
    cik            TEXT NOT NULL,
    company        TEXT NOT NULL,
    form           TEXT,
    file_date      DATE,
    items          TEXT,                      -- 8-K items, e.g. "2.05,7.01"
    index_url      TEXT,
    discovered_run TEXT,
    discovered_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,             -- accession:filename
    accession   TEXT NOT NULL,
    url         TEXT NOT NULL,
    doc_type    TEXT,                         -- 8-K, EX-99.1, 10-K ...
    text        TEXT NOT NULL,                -- plain text extracted from the filing HTML
    sha256      TEXT NOT NULL,
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS warn_notices (
    notice_id      TEXT PRIMARY KEY,          -- state:row hash
    state          TEXT NOT NULL,
    company        TEXT NOT NULL,
    company_norm   TEXT NOT NULL,
    notice_date    DATE,
    effective_date DATE,
    jobs           INTEGER,
    location       TEXT,
    source         TEXT NOT NULL,             -- e.g. "warn-scraper (Big Local News), CA"
    loaded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS records (
    record_id         TEXT PRIMARY KEY,
    run_id            TEXT NOT NULL,
    company           TEXT NOT NULL,
    company_norm      TEXT NOT NULL,
    cik               TEXT,
    event_date        DATE,
    jobs_cut          INTEGER,
    jobs_cut_text     TEXT,                   -- the employer's own wording for the number, verbatim
    ai_attribution    TEXT NOT NULL,          -- explicit | partial | none
    ai_quote          TEXT,                   -- employer's words, verbatim from the source
    doc_id            TEXT NOT NULL,
    source_url        TEXT NOT NULL,
    doc_type          TEXT,
    quote_verified    BOOLEAN NOT NULL,
    judge_verdict     TEXT,                   -- pass | fail
    judge_critique    TEXT,
    extractor_model   TEXT,
    judge_model       TEXT,
    warn_match        TEXT,                   -- JSON list of matching WARN notices, if any
    gap_flags         TEXT,                   -- JSON list of flagged gaps
    memory_id         TEXT,                   -- H-MEM memory holding this record
    supersedes        TEXT,
    status            TEXT NOT NULL,          -- pending_review | approved | rejected | duplicate
    reviewer_note     TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at       TIMESTAMP
);

CREATE TABLE IF NOT EXISTS company_financials (
    fact_id      TEXT PRIMARY KEY,            -- cik:metric:period_end
    cik          TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    company      TEXT NOT NULL,
    metric       TEXT NOT NULL,               -- capex | net_income | revenue
    xbrl_tag     TEXT,
    period_end   DATE NOT NULL,
    value_usd    DOUBLE PRECISION NOT NULL,
    form         TEXT,
    accession    TEXT,
    filed        DATE,
    source_url   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id   TEXT PRIMARY KEY,
    record_id    TEXT NOT NULL,               -- the approved record this follows up
    cik          TEXT,
    company      TEXT NOT NULL,
    doc_id       TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    file_date    DATE,
    kind         TEXT NOT NULL,               -- savings_achieved | savings_expected | charges | problem | reversal | other
    quote        TEXT NOT NULL,               -- verbatim from the later filing
    amount_text  TEXT,
    verified     BOOLEAN NOT NULL,
    model        TEXT,
    run_id       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issuers (
    issuer_id      TEXT PRIMARY KEY,          -- the value in records.cik: an SEC CIK, or LEI:<lei> for UK announcements
    company        TEXT NOT NULL,
    market         TEXT NOT NULL,             -- "SEC (US)" or "FCA NSM (UK)"
    country        TEXT,                      -- business-address country from SEC submissions data; NULL if not known
    country_source TEXT                       -- where the country came from
);

CREATE TABLE IF NOT EXISTS warn_reasons (
    notice_id   TEXT PRIMARY KEY,             -- same id as warn_notices.notice_id
    state       TEXT NOT NULL,
    reason      TEXT NOT NULL                 -- the reason as the state's file records it, verbatim
);

CREATE TABLE IF NOT EXISTS restructuring_events (
    event_id          TEXT PRIMARY KEY,       -- ERM:<factsheet number>
    country           TEXT NOT NULL,
    company           TEXT NOT NULL,
    company_norm      TEXT NOT NULL,
    announced         DATE,
    sector            TEXT,
    event_type        TEXT,                   -- e.g. Internal restructuring, Closure, Offshoring
    employment_change INTEGER,                -- negative = planned job losses
    description       TEXT,                   -- Eurofound's summary (not the employer's words)
    source_url        TEXT NOT NULL,
    source            TEXT NOT NULL,
    sha256            TEXT
);

-- Disclosure-intelligence views: the reusable layer any Zetaris user (or the Ask agent) can query.
-- Only published records (verified quote, Judge pass, editor approval, safety cleared) appear here.
CREATE OR REPLACE VIEW v_employer_claims AS
SELECT r.record_id, r.company, r.event_date, r.jobs_cut, r.jobs_cut_text, r.ai_attribution, r.ai_quote,
       r.source_url, r.doc_type, i.country, i.market
FROM records r LEFT JOIN issuers i ON i.issuer_id = r.cik
WHERE r.status = 'approved' AND r.judge_verdict = 'pass' AND r.quote_verified = TRUE
  AND (COALESCE(r.gap_flags, '') NOT LIKE '%SAFETY:%' OR lower(COALESCE(r.reviewer_note, '')) LIKE '%safety checked%');

CREATE OR REPLACE VIEW v_warn_notices AS
SELECT w.notice_id, w.state, w.company, w.company_norm, w.notice_date, w.jobs, w.location, r.reason, w.source
FROM warn_notices w LEFT JOIN warn_reasons r ON r.notice_id = w.notice_id;

CREATE OR REPLACE VIEW v_reported_outcomes AS
SELECT o.record_id, o.company, o.file_date, o.kind, o.quote, o.amount_text, o.source_url
FROM outcomes o WHERE o.verified = TRUE;
