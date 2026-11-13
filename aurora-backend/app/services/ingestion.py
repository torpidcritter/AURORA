"""Dataset parsing, fuzzy column recognition, cleaning and quality scoring.

Pure functions (no DB access) so they are easy to test with malformed inputs.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

CANONICAL_ALIASES: dict[str, list[str]] = {
    "date": ["date", "timestamp", "day", "txn_date", "order_date", "sale_date", "transaction_date", "ds"],
    "product_id": ["product_id", "sku", "item_code", "product", "item_id", "product_code", "item", "article"],
    "sales": ["sales", "quantity", "units_sold", "demand", "qty", "units", "quantity_sold", "y"],
    "product_name": ["product_name", "name", "item_name", "description", "title"],
    "category": ["category", "product_category", "department", "group"],
}
REQUIRED = ("date", "product_id", "sales")
MAX_ERROR_SAMPLES = 50


class IngestionError(ValueError):
    """The file cannot be processed at all (wrong type, unreadable, missing required columns)."""

    def __init__(self, message: str, details: object = None):
        super().__init__(message)
        self.details = details


def _norm(col: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(col).strip().lower()).strip("_")


def map_columns(columns: list[str]) -> dict[str, str]:
    """Map canonical field -> source column using exact alias matches first, then substring matches."""
    normed = {c: _norm(c) for c in columns}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for canon, aliases in CANONICAL_ALIASES.items():
        for alias in aliases:
            hit = next((c for c, n in normed.items() if n == alias and c not in used), None)
            if hit is not None:
                mapping[canon] = hit
                used.add(hit)
                break
    for canon in REQUIRED:
        if canon in mapping:
            continue
        for alias in CANONICAL_ALIASES[canon]:
            hit = next((c for c, n in normed.items() if c not in used and len(alias) > 2 and alias in n), None)
            if hit is not None:
                mapping[canon] = hit
                used.add(hit)
                break
    return mapping


def detect_format(filename: str, data: bytes) -> str:
    """Decide the format from extension *and* content; reject mismatches."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    head = data[:8]
    if ext in ("xlsx", "xlsm"):
        if not head.startswith(b"PK\x03\x04"):
            raise IngestionError("File has an .xlsx extension but is not a valid Excel workbook")
        return "xlsx"
    if ext == "json":
        stripped = data.lstrip()[:1]
        if stripped not in (b"[", b"{"):
            raise IngestionError("File has a .json extension but does not contain JSON")
        return "json"
    if ext in ("csv", "txt", "tsv"):
        if head.startswith(b"PK\x03\x04") or b"\x00" in data[:4096]:
            raise IngestionError("File looks binary, not CSV text")
        return "csv"
    raise IngestionError(f"Unsupported file type '.{ext}'. Upload CSV, Excel (.xlsx) or JSON.")


def read_frame(fmt: str, data: bytes, max_rows: int) -> pd.DataFrame:
    try:
        if fmt == "csv":
            text = data.decode("utf-8-sig")
            sep = "\t" if text[:2000].count("\t") > text[:2000].count(",") else ","
            df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False, nrows=max_rows + 1)
        elif fmt == "xlsx":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:  # zip-bomb guard
                total = sum(i.file_size for i in zf.infolist())
                if total > 200 * 1024 * 1024:
                    raise IngestionError("Workbook expands to more than 200 MB")
            df = pd.read_excel(io.BytesIO(data), dtype=str, nrows=max_rows + 1, engine="openpyxl").fillna("")
        else:
            payload = json.loads(data.decode("utf-8"))
            if isinstance(payload, dict):
                payload = payload.get("records") or payload.get("data") or []
            if not isinstance(payload, list) or not all(isinstance(r, dict) for r in payload):
                raise IngestionError('JSON must be a list of objects or {"records": [...]}')
            df = pd.DataFrame(payload[: max_rows + 1]).astype(str)
    except IngestionError:
        raise
    except UnicodeDecodeError as exc:
        raise IngestionError("File is not UTF-8 encoded text") from exc
    except Exception as exc:  # noqa: BLE001 - parser errors vary by engine
        raise IngestionError(f"Could not parse file: {type(exc).__name__}") from exc
    if len(df) > max_rows:
        raise IngestionError(f"File has more than {max_rows} rows")
    return df


@dataclass
class CleanResult:
    frame: pd.DataFrame  # columns: sale_date (date), sku, quantity (float), product_name, category
    mapping: dict[str, str]
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    errors: list[dict[str, object]] = field(default_factory=list)
    error_counts: dict[str, int] = field(default_factory=dict)
    duplicates_merged: int = 0
    empty_rows: int = 0
    explicit_zero_rows: int = 0
    gap_days: int = 0
    expected_days: int = 0
    products: int = 0
    date_min: date | None = None
    date_max: date | None = None
    quality_score: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def report(self) -> dict[str, object]:
        return {
            "total_rows": self.total_rows,
            "accepted_rows": self.accepted_rows,
            "rejected_rows": self.rejected_rows,
            "empty_rows": self.empty_rows,
            "duplicates_merged": self.duplicates_merged,
            "explicit_zero_rows": self.explicit_zero_rows,
            "gap_days": self.gap_days,
            "expected_days": self.expected_days,
            "products": self.products,
            "date_min": self.date_min.isoformat() if self.date_min else None,
            "date_max": self.date_max.isoformat() if self.date_max else None,
            "error_counts": self.error_counts,
            "error_samples": self.errors,
            "warnings": self.warnings,
            "quality_score": self.quality_score,
            "quality_formula": "100 * (0.5*validity + 0.3*completeness + 0.2*uniqueness)",
        }


def clean(df: pd.DataFrame, today: date | None = None) -> CleanResult:
    today = today or datetime.utcnow().date()
    mapping = map_columns(list(df.columns))
    missing = [c for c in REQUIRED if c not in mapping]
    if missing:
        raise IngestionError(
            "Required columns not found: " + ", ".join(missing),
            details={
                "missing": missing,
                "columns_found": [str(c) for c in df.columns],
                "accepted_names": {k: CANONICAL_ALIASES[k] for k in missing},
            },
        )
    total = len(df)
    res = CleanResult(frame=pd.DataFrame(), mapping=mapping, total_rows=total, accepted_rows=0, rejected_rows=0)

    work = pd.DataFrame(
        {
            "raw_date": df[mapping["date"]].astype(str).str.strip(),
            "sku": df[mapping["product_id"]].astype(str).str.strip(),
            "raw_qty": df[mapping["sales"]].astype(str).str.strip().str.replace(",", "", regex=False),
            "product_name": df[mapping["product_name"]].astype(str).str.strip() if "product_name" in mapping else "",
            "category": df[mapping["category"]].astype(str).str.strip() if "category" in mapping else "",
        }
    )
    work["row"] = np.arange(2, total + 2)  # 1-based + header row, as a spreadsheet user sees it

    empty = (work["raw_date"] == "") & (work["sku"] == "") & (work["raw_qty"] == "")
    res.empty_rows = int(empty.sum())
    work = work[~empty]

    parsed = pd.to_datetime(work["raw_date"], errors="coerce", utc=True, format="mixed")
    naive = parsed.dt.tz_convert(None).dt.normalize()
    work["sale_date"] = naive.dt.date
    qty = pd.to_numeric(work["raw_qty"], errors="coerce")
    work["quantity"] = qty

    sku_l = work["sku"].str.lower()
    is_nat = parsed.isna()
    checks: list[tuple[str, pd.Series, str]] = [
        ("missing_product", (work["sku"] == "") | sku_l.isin(["nan", "none", "null"]), "Product identifier is empty"),
        ("product_too_long", work["sku"].str.len() > 120, "Product identifier longer than 120 characters"),
        ("invalid_date", is_nat, "Unparseable date"),
        ("future_date", ~is_nat & (naive > pd.Timestamp(today)), "Date is in the future"),
        ("date_out_of_range", ~is_nat & (naive < pd.Timestamp(1990, 1, 1)), "Date is before 1990"),
        ("invalid_quantity", qty.isna() | ~np.isfinite(qty.fillna(0)), "Quantity is not a number"),
        ("negative_quantity", qty < 0, "Negative quantity (returns must be uploaded separately)"),
        ("quantity_out_of_range", qty > 1e9, "Quantity above 1e9"),
    ]
    bad = pd.Series(False, index=work.index)
    for code, mask, msg in checks:
        mask = mask.fillna(False).astype(bool)
        n = int(mask.sum())
        if not n:
            continue
        bad |= mask
        res.error_counts[code] = n
        for _, r in work[mask].head(MAX_ERROR_SAMPLES).iterrows():
            if len(res.errors) >= MAX_ERROR_SAMPLES:
                break
            shown = r["raw_date"] if "date" in code else r["raw_qty"] if "quantity" in code else r["sku"]
            res.errors.append({"row": int(r["row"]), "code": code, "message": f"{msg}: '{str(shown)[:40]}'"})
    res.errors.sort(key=lambda e: e["row"])  # type: ignore[arg-type, return-value]
    good = work[~bad].copy()
    res.rejected_rows = int(bad.sum())

    before = len(good)
    meta = good.groupby("sku").agg(product_name=("product_name", "last"), category=("category", "last"))
    agg = good.groupby(["sku", "sale_date"], as_index=False)["quantity"].sum()
    res.duplicates_merged = before - len(agg)
    if res.duplicates_merged:
        res.warnings.append(f"{res.duplicates_merged} rows shared a product and date and were summed")
    agg = agg.merge(meta, left_on="sku", right_index=True, how="left")
    res.frame = agg
    res.accepted_rows = before
    res.explicit_zero_rows = int((agg["quantity"] == 0).sum())

    if len(agg):
        res.date_min = min(agg["sale_date"])
        res.date_max = max(agg["sale_date"])
        res.products = int(agg["sku"].nunique())
        spans = agg.groupby("sku")["sale_date"].agg(["min", "max", "count"])
        expected = ((spans["max"] - spans["min"]).apply(lambda d: d.days) + 1).sum()
        res.expected_days = int(expected)
        res.gap_days = int(expected - spans["count"].sum())
        from app.ml.training import TrainConfig

        min_days = TrainConfig().min_length()
        short = spans[(spans["max"] - spans["min"]).apply(lambda d: d.days) + 1 < min_days]
        if len(short):
            res.warnings.append(
                f"{len(short)} product(s) have under {min_days} days of history; "
                "only statistical baselines will be used for them"
            )
    validity = res.accepted_rows / max(total - res.empty_rows, 1)
    completeness = 1 - res.gap_days / res.expected_days if res.expected_days else 0.0
    uniqueness = 1 - res.duplicates_merged / max(res.accepted_rows, 1)
    res.quality_score = (
        round(100 * (0.5 * validity + 0.3 * completeness + 0.2 * uniqueness), 1) if res.accepted_rows else 0.0
    )
    return res


def daterange(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
