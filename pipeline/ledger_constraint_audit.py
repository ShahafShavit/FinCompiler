"""
Exhaustive **read-time** audit that every row satisfies the same predicates as ``full_schema.sql``
CHECK / NOT NULL / UNIQUE / FK rules (SQLite does not offer PRAGMA revalidate_all_checks).

**Triggers** are not re-executed here; this only proves **row data** matches table constraints.
Trigger bodies should stay covered by integration tests or manual checks.

Run after imports or in CI. Keep queries aligned with ``schema/ledger/full_schema.sql``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class ConstraintViolation:
    """One named rule with violating row count (and optional sample ids)."""

    table: str
    rule_id: str
    description: str
    count: int
    sample_detail: str = ""


@dataclass
class LedgerAuditReport:
    integrity_check: str
    foreign_key_violation_count: int
    violations: list[ConstraintViolation] = field(default_factory=list)
    expected_triggers_present: bool = True
    missing_triggers: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return (
            self.integrity_check == "ok"
            and self.foreign_key_violation_count == 0
            and all(v.count == 0 for v in self.violations)
            and self.expected_triggers_present
        )


def _count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0])


def audit_ledger_constraints(conn: sqlite3.Connection) -> LedgerAuditReport:
    """
    Run structural PRAGMAs plus one violation-count query per logical constraint.

    ``conn`` should use ``PRAGMA foreign_keys = ON`` if you rely on FK semantics (audit runs
    ``PRAGMA foreign_key_check`` which is independent).
    """
    report = LedgerAuditReport(
        integrity_check=conn.execute("PRAGMA integrity_check").fetchone()[0],
        foreign_key_violation_count=len(list(conn.execute("PRAGMA foreign_key_check"))),
    )

    if report.integrity_check != "ok":
        return report

    expected = (
        "tr_store_category_before_insert_static_limit",
        "tr_store_before_update_static_requires_single_category",
        "tr_ledger_transaction_touch_category_updated_at",
        "tr_ledger_transaction_touch_data_updated_at",
    )
    have = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN ({})".format(
                ",".join("?" * len(expected))
            ),
            expected,
        )
    }
    missing = tuple(x for x in expected if x not in have)
    report.expected_triggers_present = len(missing) == 0
    report.missing_triggers = missing

    checks: list[tuple[str, str, str, str]] = [
        # ledger_transaction — mirror CREATE TABLE checks in full_schema.sql
        (
            "ledger_transaction",
            "lt_taarich_iso_date",
            'תאריך must be NULL or ISO date where date(x)=x',
            """SELECT id FROM ledger_transaction
               WHERE NOT ("תאריך" IS NULL OR date("תאריך") = "תאריך")""",
        ),
        (
            "ledger_transaction",
            "lt_bחובה_not_null",
            "בחובה NOT NULL",
            """SELECT id FROM ledger_transaction WHERE "בחובה" IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_bזכות_not_null",
            "בזכות NOT NULL",
            """SELECT id FROM ledger_transaction WHERE "בזכות" IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_ingested_at_date_only",
            "ingested_at NOT NULL and date(ingested_at)=ingested_at",
            """SELECT id FROM ledger_transaction
               WHERE ingested_at IS NULL OR date(ingested_at) != ingested_at""",
        ),
        (
            "ledger_transaction",
            "lt_category_updated_at_datetime",
            "category_updated_at NULL or parseable by datetime()",
            """SELECT id FROM ledger_transaction
               WHERE category_updated_at IS NOT NULL
                 AND datetime(category_updated_at) IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_data_updated_at_datetime",
            "data_updated_at NULL or parseable by datetime()",
            """SELECT id FROM ledger_transaction
               WHERE data_updated_at IS NOT NULL
                 AND datetime(data_updated_at) IS NULL""",
        ),
        (
            "ledger_transaction",
            "lt_statement_month_shape",
            "statement_month NULL or YYYY-MM with valid first day",
            """SELECT id FROM ledger_transaction
               WHERE NOT (
                 statement_month IS NULL
                 OR (
                   length(statement_month) = 7
                   AND date(statement_month || '-01') IS NOT NULL
                   AND strftime('%Y-%m', statement_month || '-01') = statement_month
                 )
               )""",
        ),
        (
            "ledger_transaction",
            "lt_fingerprint_trim",
            "fingerprint NULL or non-empty after trim",
            """SELECT id FROM ledger_transaction
               WHERE NOT (fingerprint IS NULL OR length(trim(fingerprint)) > 0)""",
        ),
        (
            "ledger_transaction",
            "lt_fingerprint_unique",
            "UNIQUE(fingerprint) for non-NULL values (duplicate fingerprint values)",
            """SELECT fingerprint FROM ledger_transaction
               WHERE fingerprint IS NOT NULL
               GROUP BY fingerprint
               HAVING COUNT(*) > 1""",
        ),
        (
            "store_category",
            "sc_store_name_not_null",
            "store_name NOT NULL",
            """SELECT store_name FROM store_category WHERE store_name IS NULL""",
        ),
        (
            "store_category",
            "sc_category_not_null",
            "category NOT NULL",
            """SELECT store_name FROM store_category WHERE category IS NULL""",
        ),
        # store
        (
            "store",
            "store_is_static_boolean",
            "is_static IN (0, 1)",
            """SELECT store_name FROM store WHERE NOT (is_static IN (0, 1))""",
        ),
        (
            "store",
            "store_name_not_null",
            "store_name NOT NULL (PK)",
            """SELECT store_name FROM store WHERE store_name IS NULL""",
        ),
        (
            "store",
            "store_is_static_not_null",
            "is_static NOT NULL",
            """SELECT store_name FROM store WHERE is_static IS NULL""",
        ),
        # similar_category_pair
        (
            "similar_category_pair",
            "scp_p_neq_p2",
            "p1 != p2",
            """SELECT p1 FROM similar_category_pair WHERE p1 = p2""",
        ),
        (
            "similar_category_pair",
            "scp_p1_not_null",
            "p1 NOT NULL",
            """SELECT p1 FROM similar_category_pair WHERE p1 IS NULL""",
        ),
        (
            "similar_category_pair",
            "scp_p2_not_null",
            "p2 NOT NULL",
            """SELECT p1 FROM similar_category_pair WHERE p2 IS NULL""",
        ),
        # holdings_balance
        (
            "holdings_balance",
            "hb_as_of_date",
            "as_of_date ISO date where date(x)=x",
            """SELECT as_of_date FROM holdings_balance
               WHERE NOT (date(as_of_date) = as_of_date)""",
        ),
        (
            "holdings_balance",
            "hb_balance_not_null",
            "balance_ils NOT NULL",
            """SELECT as_of_date FROM holdings_balance WHERE balance_ils IS NULL""",
        ),
        (
            "holdings_balance",
            "hb_activity_not_null",
            "activity_type NOT NULL",
            """SELECT as_of_date FROM holdings_balance WHERE activity_type IS NULL""",
        ),
        (
            "holdings_balance",
            "hb_as_of_not_null",
            "as_of_date NOT NULL",
            """SELECT as_of_date FROM holdings_balance WHERE as_of_date IS NULL""",
        ),
        # schema_migrations
        (
            "schema_migrations",
            "sm_version_not_null",
            "version NOT NULL (PK)",
            """SELECT version FROM schema_migrations WHERE version IS NULL""",
        ),
        (
            "schema_migrations",
            "sm_name_not_null",
            "name NOT NULL",
            """SELECT version FROM schema_migrations WHERE name IS NULL""",
        ),
        (
            "schema_migrations",
            "sm_applied_not_null",
            "applied_at NOT NULL",
            """SELECT version FROM schema_migrations WHERE applied_at IS NULL""",
        ),
    ]

    # store_category: composite PK uniqueness is automatic; FK covered by foreign_key_check
    # Duplicate PK rows cannot exist. Optional explicit duplicate check skipped.

    for table, rule_id, description, sql in checks:
        try:
            n = _count(conn, sql)
        except sqlite3.Error as e:
            report.violations.append(
                ConstraintViolation(
                    table=table,
                    rule_id=rule_id,
                    description=description,
                    count=-1,
                    sample_detail=str(e),
                )
            )
            continue
        detail = ""
        if n > 0:
            try:
                sample = conn.execute(sql + " LIMIT 3").fetchall()
                detail = repr(sample)
            except sqlite3.Error:
                pass
        report.violations.append(
            ConstraintViolation(
                table=table,
                rule_id=rule_id,
                description=description,
                count=n,
                sample_detail=detail,
            )
        )

    return report


def format_report(report: LedgerAuditReport) -> str:
    lines = [
        f"PRAGMA integrity_check: {report.integrity_check}",
        f"PRAGMA foreign_key_check violations: {report.foreign_key_violation_count}",
        f"Expected triggers present: {report.expected_triggers_present}"
        + (f" (missing: {report.missing_triggers})" if report.missing_triggers else ""),
        "",
        "Constraint audits (0 = pass):",
    ]
    for v in report.violations:
        if v.count == 0:
            lines.append(f"  [OK] {v.table}.{v.rule_id} — {v.description}")
        elif v.count < 0:
            lines.append(f"  [ERR] {v.table}.{v.rule_id} — query error: {v.sample_detail}")
        else:
            lines.append(
                f"  [FAIL] {v.table}.{v.rule_id} — {v.description} — {v.count} row(s) {v.sample_detail}"
            )
    lines.append("")
    lines.append("OVERALL: PASSED" if report.ok else "OVERALL: FAILED")
    return "\n".join(lines)
