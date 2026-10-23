from __future__ import annotations

import io
import json
from datetime import date

import pandas as pd
import pytest

from app.services.ingestion import IngestionError, clean, detect_format, map_columns, read_frame

TODAY = date(2025, 6, 30)


def frame(csv: str) -> pd.DataFrame:
    return read_frame("csv", csv.encode(), 1000)


def test_fuzzy_column_mapping():
    m = map_columns(["Order Date", "SKU", "Units Sold", "Item Name"])
    assert m == {"date": "Order Date", "product_id": "SKU", "sales": "Units Sold", "product_name": "Item Name"}
    m = map_columns(["txn_date", "item_code", "qty"])
    assert set(m) == {"date", "product_id", "sales"}


def test_missing_required_columns():
    with pytest.raises(IngestionError) as e:
        clean(frame("when,what\n2025-01-01,a\n"), TODAY)
    assert "date" in str(e.value) or "product_id" in str(e.value)


def test_cleaning_rules_and_quality():
    csv = (
        "date,sku,qty\n"
        "2025-01-01,A,5\n"
        "2025-01-02,A,0\n"  # explicit zero
        "2025-01-04,A,3\n"  # gap on 01-03
        "2025-01-04,A,2\n"  # duplicate -> summed
        "not-a-date,A,1\n"
        "2025-01-05,,1\n"
        "2025-01-05,A,-4\n"
        "2025-01-05,A,abc\n"
        "2099-01-01,A,1\n"
        ",,\n"
    )
    r = clean(frame(csv), TODAY)
    assert r.total_rows == 10
    assert r.empty_rows == 1
    assert r.rejected_rows == 5
    assert r.accepted_rows == 4
    assert r.duplicates_merged == 1
    assert r.explicit_zero_rows == 1
    assert r.gap_days == 1 and r.expected_days == 4
    assert r.error_counts == {
        "invalid_date": 1,
        "missing_product": 1,
        "negative_quantity": 1,
        "invalid_quantity": 1,
        "future_date": 1,
    }
    rows = {(x.sku, x.sale_date): x.quantity for x in r.frame.itertuples()}
    assert rows[("A", date(2025, 1, 4))] == 5
    # validity 4/9, completeness 3/4, uniqueness 3/4
    assert r.quality_score == pytest.approx(round(100 * (0.5 * 4 / 9 + 0.3 * 0.75 + 0.2 * 0.75), 1))
    assert r.errors and all(e["row"] >= 2 for e in r.errors)


def test_detect_format_rejects_mismatch():
    assert detect_format("a.csv", b"date,sku,qty\n") == "csv"
    with pytest.raises(IngestionError):
        detect_format("a.xlsx", b"date,sku,qty\n")
    with pytest.raises(IngestionError):
        detect_format("a.exe", b"MZ")
    with pytest.raises(IngestionError):
        detect_format("a.csv", b"PK\x03\x04binary")
    with pytest.raises(IngestionError):
        detect_format("a.json", b"date,sku")


def test_json_and_excel_parsing():
    records = [{"date": "2025-01-01", "sku": "A", "sales": 2}]
    df = read_frame("json", json.dumps({"records": records}).encode(), 10)
    assert clean(df, TODAY).accepted_rows == 1
    buf = io.BytesIO()
    pd.DataFrame(records).to_excel(buf, index=False)
    df = read_frame("xlsx", buf.getvalue(), 10)
    assert clean(df, TODAY).accepted_rows == 1
    with pytest.raises(IngestionError):
        read_frame("json", b'{"records": 5}', 10)


def test_row_limit_and_encoding():
    with pytest.raises(IngestionError):
        read_frame("csv", ("date,sku,qty\n" + "2025-01-01,A,1\n" * 11).encode(), 10)
    with pytest.raises(IngestionError):
        read_frame("csv", b"date,sku,qty\n\xff\xfe\xfa,A,1\n", 10)


def test_all_rows_invalid_scores_zero():
    r = clean(frame("date,sku,qty\nbad,A,1\n"), TODAY)
    assert r.accepted_rows == 0 and r.quality_score == 0.0
