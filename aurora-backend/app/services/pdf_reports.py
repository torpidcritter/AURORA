"""PDF documents: a purchase order to send to a supplier, and an inventory report (ADR-047).

Rendered server-side with fpdf2 from the same values the screens show; nothing is computed only for the PDF. Text
comes from users (product and supplier names), so it is only ever drawn as text. A Unicode font (DejaVu Sans,
installed in the container image) is used when present; otherwise the built-in Helvetica is used and characters
outside Latin-1 are shown as "?", which the document then says.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace
from sqlalchemy.orm import Session

from app.db.models import Organization, PurchaseOrder
from app.services import inventory_service, purchasing_service, settings_service

FONT_DIRS = ("/usr/share/fonts/truetype/dejavu",)
MAX_REORDER_ROWS = 200
GREY = (110, 110, 110)
HEAD = FontFace(emphasis="BOLD", fill_color=(235, 238, 244))


def unicode_font() -> Path | None:
    """The regular DejaVu Sans file, if installed (bold is optional)."""
    for d in (os.environ.get("PDF_FONT_DIR"), *FONT_DIRS):
        if d and (Path(d) / "DejaVuSans.ttf").is_file():
            return Path(d)
    return None


class _Doc(FPDF):
    def __init__(self, org_name: str, title: str, *, font_dir: Path | None, compress: bool = True) -> None:
        super().__init__(format="A4")
        self.compress = compress
        self.org_name, self.doc_title = org_name, title
        self.lossy = False  # True once a character had to be replaced
        if font_dir is not None:
            self.add_font("body", "", str(font_dir / "DejaVuSans.ttf"))
            bold = font_dir / "DejaVuSans-Bold.ttf"
            self.add_font("body", "B", str(bold if bold.is_file() else font_dir / "DejaVuSans.ttf"))
            self.family = "body"
        else:
            self.family = "helvetica"
        self.set_title(self.t(title))
        self.set_creator("Aurora")
        self.set_auto_page_break(auto=True, margin=15)
        self.add_page()

    def t(self, s: object) -> str:
        """Text safe for the current font."""
        text = "" if s is None else str(s)
        if self.family == "body":
            return text
        safe = text.encode("latin-1", "replace").decode("latin-1")
        if safe != text:
            self.lossy = True
        return safe

    def header(self) -> None:
        self.set_font(self.family, "B", 9)
        self.set_text_color(*GREY)
        self.cell(0, 5, self.t(f"{self.org_name} · Aurora"), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(self.family, "", 8)
        self.set_text_color(*GREY)
        self.cell(0, 5, self.t(f"{self.doc_title} · page {self.page_no()}/{{nb}}"), align="C")

    def h1(self, s: str) -> None:
        self.set_font(self.family, "B", 16)
        self.cell(0, 9, self.t(s), new_x="LMARGIN", new_y="NEXT")

    def h2(self, s: str) -> None:
        self.ln(3)
        self.set_font(self.family, "B", 11)
        self.cell(0, 7, self.t(s), new_x="LMARGIN", new_y="NEXT")

    def para(self, s: str, size: int = 9, grey: bool = False) -> None:
        self.set_font(self.family, "", size)
        if grey:
            self.set_text_color(*GREY)
        self.multi_cell(0, 4.5, self.t(s), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def pairs(self, rows: list[tuple[str, str]]) -> None:
        for k, v in rows:
            self.set_font(self.family, "B", 9)
            self.cell(42, 5, self.t(k))
            self.set_font(self.family, "", 9)
            self.multi_cell(0, 5, self.t(v), new_x="LMARGIN", new_y="NEXT")

    def grid(self, headings: list[str], rows: list[list[str]], widths: tuple[int, ...], right: set[int]) -> None:
        self.set_font(self.family, "", 8)
        align = tuple("RIGHT" if i in right else "LEFT" for i in range(len(headings)))
        with self.table(col_widths=widths, text_align=align, headings_style=HEAD, line_height=4.5) as table:
            table.row([self.t(h) for h in headings])
            for r in rows:
                table.row([self.t(c) for c in r])

    def finish(self) -> bytes:
        if self.lossy:
            self.ln(2)
            self.para("Some characters could not be shown in this font and appear as '?'.", 7, grey=True)
        return bytes(self.output())


def _num(v: float | int | None, digits: int = 0) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}"


def _org_name(db: Session, org_id: uuid.UUID) -> str:
    org = db.get(Organization, org_id)
    return org.name if org else "Organisation"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def purchase_order_pdf(
    db: Session, org_id: uuid.UUID, po: PurchaseOrder, *, font_dir: Path | None = None, compress: bool = True
) -> bytes:
    o = purchasing_service.order_out(db, po)
    doc = _Doc(_org_name(db, org_id), f"Purchase order {o['number']}", font_dir=font_dir, compress=compress)
    doc.h1(f"Purchase order {o['number']}")
    if o["status"] in ("draft", "approved"):
        # A draft or approved order printed before sending must not pass for the sent order.
        doc.para(f"Status: {o['status'].upper()}. This order has not been sent to the supplier.", 10)
    elif o["status"] == "cancelled":
        doc.para("Status: CANCELLED. Do not fulfil this order.", 10)
    location = " ".join(x for x in (o["location_code"], o["location_name"] and f"({o['location_name']})") if x)
    doc.pairs(
        [
            ("Supplier", o["supplier_name"] or "Not assigned"),
            ("Supplier e-mail", o["supplier_email"] or "-"),
            ("Deliver to", location or "-"),
            ("Expected by", o["expected_date"] or "-"),
            ("Status", o["status"]),
            ("Sent", (o["sent_at"] or "-")[:10]),
        ]
    )
    doc.h2("Lines")
    rows = [
        [ln["sku"], ln["name"], _num(ln["quantity"]), _num(ln["unit_cost"], 2), _num(ln["line_value"], 2)]
        for ln in o["lines"]
    ]
    rows.append(
        ["", "Total", _num(o["total_units"]), "", _num(o["total_value"], 2) + ("" if o["total_complete"] else " *")]
    )
    doc.grid(["SKU", "Product", "Quantity", "Unit cost", "Value"], rows, (28, 82, 22, 26, 32), {2, 3, 4})
    if not o["total_complete"]:
        doc.para("* Some lines have no unit cost, so the total is incomplete.", 8, grey=True)
    if o["notes"]:
        doc.h2("Notes")
        doc.para(o["notes"])
    doc.ln(4)
    doc.para(f"Generated {_stamp()} from Aurora.", 7, grey=True)
    return doc.finish()


def inventory_report_pdf(
    db: Session, org_id: uuid.UUID, *, font_dir: Path | None = None, compress: bool = True
) -> bytes:
    eff = settings_service.for_org(db, org_id)
    matrix = inventory_service.risk_matrix(db, org_id, org_settings=eff)
    known = [r for r in matrix if r["status"] == "ok"]
    levels = {lvl: sum(1 for r in matrix if r["risk_level"] == lvl) for lvl in ("critical", "high", "medium", "low")}
    reorder = [r for r in known if r.get("should_reorder")]
    value = sum(r.get("order_value") or 0 for r in reorder)
    priced = all(r.get("order_value") is not None for r in reorder)
    as_of = max((r.get("forecast_as_of") or "" for r in known), default="") or "no sales yet"
    doc = _Doc(_org_name(db, org_id), "Inventory report", font_dir=font_dir, compress=compress)
    doc.h1("Inventory report")
    doc.para(f"Generated {_stamp()}. Forecasts use sales up to {as_of}.", 8, grey=True)
    doc.h2("Summary")
    doc.pairs(
        [
            ("Products scored", f"{len(known)} of {len(matrix)} (the rest have no stock count or no sales)"),
            ("Stockout risk", ", ".join(f"{n} {lvl}" for lvl, n in levels.items())),
            ("To reorder now", f"{len(reorder)} product(s)"),
            ("Order value", f"{value:,.2f}" + ("" if priced else " (some products have no unit cost)")),
            ("Service level", f"{eff.default_service_level:.0%} cycle service level unless set per product"),
        ]
    )
    doc.h2("Products to reorder")
    if not reorder:
        doc.para("Nothing needs reordering at the moment.")
    else:
        rows = [
            [
                r["sku"],
                r["name"],
                r["risk_level"],
                "-" if r.get("days_until_stockout") is None else f"{r['days_until_stockout']:.1f}",
                _num(r.get("on_hand")),
                _num(r.get("reorder_point")),
                _num(r.get("recommended_order_qty")),
                _num(r.get("order_value"), 2),
            ]
            for r in reorder[:MAX_REORDER_ROWS]
        ]
        doc.grid(
            ["SKU", "Product", "Risk", "Days left", "On hand", "Reorder pt", "Order", "Value"],
            rows,
            (24, 52, 16, 18, 18, 20, 18, 24),
            {3, 4, 5, 6, 7},
        )
        if len(reorder) > MAX_REORDER_ROWS:
            doc.para(f"Showing the {MAX_REORDER_ROWS} most urgent of {len(reorder)}; see Recommendations in Aurora.", 8)
    unknown = [r["sku"] for r in matrix if r["status"] == "unknown_inventory"]
    if unknown:
        doc.h2("Not scored: no stock count")
        doc.para(", ".join(unknown[:100]) + (f" and {len(unknown) - 100} more" if len(unknown) > 100 else ""), 8)
    doc.h2("How these numbers are made")
    doc.para(
        "Safety stock = z x forecast error x sqrt(lead time); reorder point = forecast demand over the lead time plus "
        "safety stock; the order covers the lead time plus the coverage days, rounded to pack size and minimum order. "
        "Planner overrides and promotions change the demand used. Forecasts are estimates and can be wrong.",
        8,
        grey=True,
    )
    return doc.finish()
