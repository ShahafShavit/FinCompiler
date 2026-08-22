-- Uncategorized ledger rows (MIG-I3 sample report).
-- Run from repo root:
--   sqlite3 data/ledger.sqlite < reports/uncategorized.sql
-- Or:
--   PYTHONPATH=app/backend python -c "import sqlite3,config; c=sqlite3.connect(config.ledger_db_file); print(c.execute(open('reports/uncategorized.sql',encoding='utf-8').read()).fetchall()[:20])"

SELECT
  id,
  "תאריך",
  statement_month,
  "מקור עסקה",
  "בחובה",
  "בזכות",
  "קטגוריה",
  fingerprint
FROM ledger_transaction
WHERE COALESCE(excluded_from_calculations, 0) = 0
  AND (
    "קטגוריה" IS NULL
    OR TRIM(COALESCE("קטגוריה", '')) = ''
    OR LOWER(TRIM("קטגוריה")) = 'awaiting'
  )
ORDER BY COALESCE(statement_month, strftime('%Y-%m', date("תאריך"))) DESC, id DESC
LIMIT 500;
