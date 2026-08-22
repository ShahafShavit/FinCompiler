# Saved SQL reports (MIG-I3)

Ad-hoc queries over the canonical ledger (`data/ledger.sqlite`). These are **not** a product UI — use Explore filters for everyday browsing.

| File | Purpose |
|------|---------|
| [uncategorized.sql](uncategorized.sql) | Included rows still needing a category |

```bash
sqlite3 data/ledger.sqlite < reports/uncategorized.sql
```

From the repo root with the project config:

```bash
PYTHONPATH=app/backend python -c "import sqlite3, config; print(sqlite3.connect(config.ledger_db_file).execute(open('reports/uncategorized.sql', encoding='utf-8').read()).fetchall()[:5])"
```
