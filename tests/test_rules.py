"""Tests for the statbarn rule engine (pure functions, no I/O)."""

import uuid

import polars as pl

from trevor.agent.rules import (
    assess_object,
    rule_dominance,
    rule_file_not_empty,
    rule_justification_present,
    rule_min_cell_count,
    rule_missing_values_flagged,
    rule_no_individual_records,
    rule_statbarn_matches_type,
    rule_suppression_documented,
)
from trevor.models.request import OutputObjectMetadata


def _make_meta(**kwargs):
    defaults = {"logical_object_id": uuid.uuid4()}
    defaults.update(kwargs)
    return OutputObjectMetadata(**defaults)


# --- Individual rules ---


def test_file_not_empty_pass():
    r = rule_file_not_empty(b"data")
    assert r.passed
    assert r.severity == "critical"


def test_file_not_empty_fail():
    r = rule_file_not_empty(b"")
    assert not r.passed


def test_justification_present_pass():
    meta = _make_meta(researcher_justification="This is needed for analysis")
    r = rule_justification_present(metadata=meta)
    assert r.passed


def test_justification_present_fail():
    meta = _make_meta(researcher_justification="")
    r = rule_justification_present(metadata=meta)
    assert not r.passed


def test_suppression_documented_not_applicable():
    meta = _make_meta(suppression_notes="")
    r = rule_suppression_documented(statbarn="frequency_table", metadata=meta)
    assert r.passed  # no SDC keywords


def test_suppression_documented_fail():
    meta = _make_meta(suppression_notes="")
    r = rule_suppression_documented(statbarn="suppressed_crosstab", metadata=meta)
    assert not r.passed


def test_suppression_documented_pass():
    meta = _make_meta(suppression_notes="Cells < 10 suppressed with *")
    r = rule_suppression_documented(statbarn="suppressed_crosstab", metadata=meta)
    assert r.passed


def test_statbarn_matches_type_empty():
    r = rule_statbarn_matches_type(statbarn="", output_type="tabular", filename="data.csv")
    assert not r.passed


def test_statbarn_matches_type_present():
    r = rule_statbarn_matches_type(
        statbarn="freq_table", output_type="tabular", filename="data.csv"
    )
    assert r.passed


# --- Tabular rules with polars ---


def test_min_cell_count_pass():
    df = pl.DataFrame({"a": [10, 20, 30], "b": [15, 25, 35]})
    r = rule_min_cell_count(df, threshold=10)
    assert r.passed


def test_min_cell_count_fail():
    df = pl.DataFrame({"a": [5, 20, 30], "b": [15, 25, 35]})
    r = rule_min_cell_count(df, threshold=10)
    assert not r.passed
    assert r.severity == "critical"


def test_dominance_pass():
    df = pl.DataFrame({"a": [100, 80, 60]})
    r = rule_dominance(df, p_percent=70)
    assert r.passed  # 80/100 = 80% >= 70%


def test_dominance_fail():
    df = pl.DataFrame({"a": [100, 10, 5]})
    r = rule_dominance(df, p_percent=70)
    assert not r.passed  # 10/100 = 10% < 70%


def test_no_individual_records_pass():
    df = pl.DataFrame({"a": list(range(50))})
    r = rule_no_individual_records(df)
    assert r.passed


def test_no_individual_records_fail():
    df = pl.DataFrame({"a": list(range(1500))})
    r = rule_no_individual_records(df)
    assert not r.passed


def test_missing_values_pass():
    df = pl.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    r = rule_missing_values_flagged(df)
    assert r.passed


def test_missing_values_fail():
    df = pl.DataFrame({"a": [1, None, 3], "b": [4, 5, None]})
    r = rule_missing_values_flagged(df)
    assert not r.passed


# --- Full assessment ---


def test_assess_object_non_tabular():
    meta = _make_meta(researcher_justification="Needed for publication")
    oid = uuid.uuid4()
    result = assess_object(
        object_id=oid,
        output_type="figure",
        statbarn="scatter_plot",
        file_content=b"PNG data",
        filename="chart.png",
        metadata=meta,
    )
    assert result.object_id == oid
    assert result.disclosure_risk == "none"
    assert result.recommendation == "approve"
    assert len(result.rule_checks) == 4  # universal rules only


def test_assess_object_csv():
    csv_data = b"group,count\nA,15\nB,20\nC,25\n"
    meta = _make_meta(researcher_justification="Frequency table for analysis")
    result = assess_object(
        object_id=uuid.uuid4(),
        output_type="tabular",
        statbarn="freq_table",
        file_content=csv_data,
        filename="data.csv",
        metadata=meta,
    )
    # Should run universal + tabular rules
    rule_names = [r.rule for r in result.rule_checks]
    assert "min_cell_count" in rule_names
    assert "dominance_rule" in rule_names
    assert "file_not_empty" in rule_names


def test_assess_object_csv_with_violation():
    csv_data = b"group,count\nA,5\nB,20\n"
    meta = _make_meta(researcher_justification="Analysis")
    result = assess_object(
        object_id=uuid.uuid4(),
        output_type="tabular",
        statbarn="freq_table",
        file_content=csv_data,
        filename="data.csv",
        metadata=meta,
        min_cell_count=10,
    )
    assert result.disclosure_risk == "high"
    assert result.recommendation == "escalate"


def test_assess_object_empty_file():
    meta = _make_meta(researcher_justification="Test")
    result = assess_object(
        object_id=uuid.uuid4(),
        output_type="other",
        statbarn="test",
        file_content=b"",
        filename="empty.txt",
        metadata=meta,
    )
    assert result.disclosure_risk == "high"
    assert result.recommendation == "escalate"
