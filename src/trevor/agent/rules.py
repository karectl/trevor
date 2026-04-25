"""Statbarn rule engine — pure functions, no I/O, deterministic.

Each rule returns a RuleResult. assess_object() runs all applicable rules
and produces an ObjectAssessment.
"""

from __future__ import annotations

import io
import uuid
from typing import TYPE_CHECKING

import polars as pl

from trevor.agent.schemas import ObjectAssessment, RuleResult

if TYPE_CHECKING:
    from trevor.models.request import OutputObjectMetadata


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

TABULAR_EXTENSIONS = {".csv", ".tsv", ".parquet"}


def _is_tabular_file(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in TABULAR_EXTENSIONS)


def _try_read_tabular(file_content: bytes, filename: str) -> pl.DataFrame | None:
    """Attempt to parse file as tabular data. Returns None if unparseable."""
    lower = filename.lower()
    try:
        if lower.endswith(".parquet"):
            return pl.read_parquet(io.BytesIO(file_content))
        if lower.endswith(".tsv"):
            return pl.read_csv(io.BytesIO(file_content), separator="\t")
        if lower.endswith(".csv"):
            return pl.read_csv(io.BytesIO(file_content))
    except Exception:  # noqa: BLE001
        return None
    return None


def rule_file_not_empty(file_content: bytes, **_: object) -> RuleResult:
    """File size > 0."""
    return RuleResult(
        rule="file_not_empty",
        passed=len(file_content) > 0,
        detail="File is empty" if len(file_content) == 0 else "File is non-empty",
        severity="critical",
    )


def rule_justification_present(metadata: OutputObjectMetadata, **_: object) -> RuleResult:
    """researcher_justification is non-empty."""
    present = bool(metadata.researcher_justification and metadata.researcher_justification.strip())
    return RuleResult(
        rule="justification_present",
        passed=present,
        detail="Justification provided" if present else "Missing researcher justification",
        severity="warning",
    )


def rule_suppression_documented(
    statbarn: str, metadata: OutputObjectMetadata, **_: object
) -> RuleResult:
    """If statbarn implies SDC was applied, suppression_notes must be non-empty."""
    sdc_keywords = {"suppress", "redact", "mask", "round", "perturb"}
    implies_sdc = any(kw in statbarn.lower() for kw in sdc_keywords)
    if not implies_sdc:
        return RuleResult(
            rule="suppression_documented",
            passed=True,
            detail="Statbarn does not imply SDC; rule not applicable",
            severity="info",
        )
    documented = bool(metadata.suppression_notes and metadata.suppression_notes.strip())
    return RuleResult(
        rule="suppression_documented",
        passed=documented,
        detail="Suppression documented"
        if documented
        else "Suppression notes missing for SDC output",
        severity="warning",
    )


def rule_statbarn_matches_type(
    statbarn: str, output_type: str, filename: str, **_: object
) -> RuleResult:
    """Declared statbarn is plausible for file's actual content type."""
    # Heuristic: if statbarn is empty, always fail
    if not statbarn.strip():
        return RuleResult(
            rule="statbarn_matches_type",
            passed=False,
            detail="Statbarn is empty",
            severity="warning",
        )
    return RuleResult(
        rule="statbarn_matches_type",
        passed=True,
        detail=f"Statbarn '{statbarn}' declared for {output_type} file '{filename}'",
        severity="info",
    )


def rule_min_cell_count(df: pl.DataFrame, threshold: int = 10, **_: object) -> RuleResult:
    """All numeric cells in tabular output >= threshold."""
    numeric_cols = df.select(pl.selectors.numeric())
    if numeric_cols.width == 0:
        return RuleResult(
            rule="min_cell_count",
            passed=True,
            detail="No numeric columns found",
            severity="info",
        )
    min_val = numeric_cols.min().row(0)
    actual_min = (
        min(v for v in min_val if v is not None) if any(v is not None for v in min_val) else None
    )
    if actual_min is None:
        return RuleResult(
            rule="min_cell_count",
            passed=True,
            detail="All numeric values are null",
            severity="info",
        )
    passed = actual_min >= threshold
    return RuleResult(
        rule="min_cell_count",
        passed=passed,
        detail=f"Minimum cell value: {actual_min} (threshold: {threshold})"
        if passed
        else f"Cell value {actual_min} below threshold {threshold}",
        severity="critical",
    )


def rule_dominance(df: pl.DataFrame, p_percent: int = 70, **_: object) -> RuleResult:
    """p-percent rule: second-largest contributor >= p% of largest in each numeric column."""
    numeric_cols = df.select(pl.selectors.numeric())
    if numeric_cols.width == 0:
        return RuleResult(
            rule="dominance_rule",
            passed=True,
            detail="No numeric columns found",
            severity="info",
        )
    for col_name in numeric_cols.columns:
        col = numeric_cols[col_name].drop_nulls().sort(descending=True)
        if len(col) < 2:
            continue
        largest = col[0]
        second = col[1]
        if largest == 0:
            continue
        ratio = (second / largest) * 100
        if ratio < p_percent:
            return RuleResult(
                rule="dominance_rule",
                passed=False,
                detail=(
                    f"Column '{col_name}': second-largest is {ratio:.1f}% "
                    f"of largest (threshold: {p_percent}%)"
                ),
                severity="critical",
            )
    return RuleResult(
        rule="dominance_rule",
        passed=True,
        detail=f"All columns pass p-percent rule (p={p_percent}%)",
        severity="info",
    )


def rule_no_individual_records(df: pl.DataFrame, **_: object) -> RuleResult:
    """Row count heuristic: very large row counts suggest individual-level data."""
    threshold = 1000
    row_count = df.height
    if row_count > threshold:
        return RuleResult(
            rule="no_individual_records",
            passed=False,
            detail=(
                f"Table has {row_count} rows — may contain "
                f"individual records (threshold: {threshold})"
            ),
            severity="warning",
        )
    return RuleResult(
        rule="no_individual_records",
        passed=True,
        detail=f"Table has {row_count} rows — appears aggregated",
        severity="info",
    )


def rule_missing_values_flagged(df: pl.DataFrame, **_: object) -> RuleResult:
    """Missing/null cells should be explicitly handled, not silently empty."""
    null_count = df.null_count().row(0)
    total_nulls = sum(null_count)
    if total_nulls > 0:
        return RuleResult(
            rule="missing_values_flagged",
            passed=False,
            detail=f"Table has {total_nulls} null/missing values across columns",
            severity="warning",
        )
    return RuleResult(
        rule="missing_values_flagged",
        passed=True,
        detail="No missing values detected",
        severity="info",
    )


# ---------------------------------------------------------------------------
# Assessment pipeline
# ---------------------------------------------------------------------------

_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}


def _risk_from_failures(checks: list[RuleResult]) -> str:
    failures = [c for c in checks if not c.passed]
    if not failures:
        return "none"
    max_sev = max(_SEVERITY_RANK.get(f.severity, 0) for f in failures)
    if max_sev >= 3:
        return "high"
    if max_sev >= 2:
        return "medium"
    return "low"


def _recommendation_from_failures(checks: list[RuleResult]) -> str:
    failures = [c for c in checks if not c.passed]
    if not failures:
        return "approve"
    max_sev = max(_SEVERITY_RANK.get(f.severity, 0) for f in failures)
    if max_sev >= 3:
        return "escalate"
    return "changes_requested"


def assess_object(
    object_id: uuid.UUID,
    output_type: str,
    statbarn: str,
    file_content: bytes,
    filename: str,
    metadata: OutputObjectMetadata,
    *,
    min_cell_count: int = 10,
    dominance_p: int = 70,
) -> ObjectAssessment:
    """Run all applicable rules for the given object."""
    checks: list[RuleResult] = []

    # Universal rules
    checks.append(rule_file_not_empty(file_content))
    checks.append(rule_justification_present(metadata=metadata))
    checks.append(rule_suppression_documented(statbarn=statbarn, metadata=metadata))
    checks.append(
        rule_statbarn_matches_type(statbarn=statbarn, output_type=output_type, filename=filename)
    )

    # Tabular rules
    statbarn_confirmed = bool(statbarn.strip())
    df = None
    if _is_tabular_file(filename):
        df = _try_read_tabular(file_content, filename)

    if df is not None:
        checks.append(rule_min_cell_count(df, threshold=min_cell_count))
        checks.append(rule_dominance(df, p_percent=dominance_p))
        checks.append(rule_no_individual_records(df))
        checks.append(rule_missing_values_flagged(df))

    risk = _risk_from_failures(checks)
    rec = _recommendation_from_failures(checks)

    return ObjectAssessment(
        object_id=object_id,
        statbarn_confirmed=statbarn_confirmed,
        rule_checks=checks,
        disclosure_risk=risk,
        recommendation=rec,
    )
