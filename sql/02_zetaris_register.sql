-- Register the Seismograph evidence store with Zetaris. Run each statement on its own in
-- the Zetaris SQL Editor (http://localhost:3000), as an administrator.
--
-- The evidence store runs in this project's docker compose (service evidence-db, host
-- port 5433). The Zetaris containers reach it through Docker Desktop's
-- host.docker.internal name. If CREATE DATASOURCE cannot connect, see README
-- "Troubleshooting: Zetaris cannot reach the evidence store".

CREATE DATASOURCE seismo_pg DESCRIBE BY "Seismograph_evidence_store" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://host.docker.internal:5433/seismograph",
  username "seismo",
  password "seismo",
  fetchsize "1000"
);

REGISTER DATASOURCE TABLES FROM seismo_pg;

-- Check: each should return a count (0 is fine before the first run).
SELECT COUNT(*) AS filings FROM seismo_pg.filings;
SELECT COUNT(*) AS documents FROM seismo_pg.documents;
SELECT COUNT(*) AS warn_notices FROM seismo_pg.warn_notices;
SELECT COUNT(*) AS records FROM seismo_pg.records;

-- After a run, tables registered before they had rows may need their metadata refreshed:
-- REGISTER DATASOURCE TABLES FROM seismo_pg;
