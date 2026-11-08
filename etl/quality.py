"""
Declarative data-quality checks driven by config/expectations.yaml.

    results, quarantined = run_checks(tables, expectations["staging"])

Each check returns a CheckResult; rows failing a *critical* check are removed
from the table and returned in `quarantined[table]` with a `_dq_reason` column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd


@dataclass
class CheckResult:
    table: str
    column: str
    check: str
    severity: str
    rows_checked: int
    rows_failed: int
    note: str = ""

    @property
    def status(self) -> str:
        if self.rows_failed == 0:
            return "PASS"
        return "FAIL" if self.severity == "critical" else "WARN"

    @property
    def fail_pct(self) -> float:
        return 100.0 * self.rows_failed / self.rows_checked if self.rows_checked else 0.0


def _failing_mask(df: pd.DataFrame, spec: dict, tables: dict[str, pd.DataFrame]) -> pd.Series:
    col, check = spec["column"], spec["check"]
    s = df[col]
    if check == "not_null":
        return s.isna() | s.map(lambda v: isinstance(v, str) and v.strip() == "")
    if check == "unique":
        return s.duplicated(keep="first") & s.notna()
    if check == "in_set":
        return ~s.isin(spec["values"]) & s.notna() if spec.get("allow_null", False) else ~s.isin(spec["values"])
    if check == "regex":
        pat = re.compile(spec["pattern"])
        ok = s.map(lambda v: isinstance(v, str) and bool(pat.match(v)))
        return (~ok) & s.notna() if spec.get("allow_null", False) else ~ok
    if check == "range":
        num = pd.to_numeric(s, errors="coerce")
        mask = pd.Series(False, index=df.index)
        if "min" in spec:
            mask |= num < spec["min"]
        if "max" in spec:
            mask |= num > spec["max"]
        return mask | (num.isna() & s.notna())
    if check == "foreign_key":
        ref = tables[spec["ref_table"]][spec["ref_column"]].dropna().unique()
        return ~s.isin(ref)
    if check == "not_future":
        d = pd.to_datetime(s, errors="coerce")
        return d > pd.Timestamp.today().normalize()
    raise ValueError(f"unknown check: {check}")


def run_checks(tables: dict[str, pd.DataFrame], expectations: dict) -> tuple[list[CheckResult], dict[str, pd.DataFrame]]:
    results: list[CheckResult] = []
    quarantined: dict[str, pd.DataFrame] = {}
    # critical checks first so that quarantine happens before FK checks on other tables
    for table, specs in expectations.items():
        if table not in tables:
            continue
        df = tables[table]
        drop = pd.Series(False, index=df.index)
        reasons = pd.Series("", index=df.index)
        for spec in sorted(specs, key=lambda s: 0 if s["severity"] == "critical" else 1):
            mask = _failing_mask(df, spec, tables).fillna(False).astype(bool)
            results.append(CheckResult(table, spec["column"], spec["check"], spec["severity"], len(df), int(mask.sum()), spec.get("note", "")))
            if spec["severity"] == "critical" and mask.any():
                reasons[mask] = reasons[mask].where(reasons[mask] == "", reasons[mask] + "; ") + f"{spec['column']}:{spec['check']}"
                drop |= mask
        if drop.any():
            q = df[drop].copy()
            q["_dq_reason"] = reasons[drop]
            quarantined[table] = q
            tables[table] = df[~drop].copy()
    return results, quarantined


def results_frame(results: list[CheckResult]) -> pd.DataFrame:
    return pd.DataFrame([{
        "table": r.table, "column": r.column, "check": r.check, "severity": r.severity, "status": r.status,
        "rows_checked": r.rows_checked, "rows_failed": r.rows_failed, "fail_pct": round(r.fail_pct, 2), "note": r.note,
    } for r in results])
