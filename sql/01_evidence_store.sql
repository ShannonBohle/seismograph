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
