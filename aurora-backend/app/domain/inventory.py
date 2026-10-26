"""Inventory decision math. Pure functions; every unit is "units of product" or "days".

Assumptions (documented in docs/ARCHITECTURE_DECISIONS.md, ADR-006):
* Daily forecast errors are independent with standard deviation sigma_d (units/day), so the standard deviation
  of demand over L days is sigma_d * sqrt(L).  Safety stock = z * sigma_d * sqrt(L).
* Optional lead-time variability sigma_L (days): SS = z * sqrt(L * sigma_d^2 + mu_d^2 * sigma_L^2).
* z = Phi^-1(service_level): a one-sided *cycle service level* (probability of no stockout during a
  replenishment cycle) under a normal approximation.  0.95 -> 1.645.
* Reorder point ROP = mu_d * L + SS.
* Order-up-to level S = mu_d * (L + C) + SS, where C is the review/coverage window.  The spec's
  ROQ = mu_d*C - I + SS omits demand during the lead time; we include it so an order placed today still
  covers C days *after* it arrives.  ROQ = S - inventory_position when the position is at/below ROP, else 0.
* Inventory position = on_hand + on_order.  Days-until-stockout and stockout probability use on_hand only,
  because the arrival date of open orders is unknown (a conservative choice).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.stats import norm

URGENCY_WEIGHTS = {"time": 0.40, "probability": 0.35, "inventory": 0.25}


class InventoryInputError(ValueError):
    pass


@dataclass
class InventoryInputs:
    forecast: list[float]  # daily point forecast starting tomorrow, units/day
    sigma_d: float  # SD of daily demand forecast error, units/day
    on_hand: float
    on_order: float = 0.0
    lead_time_days: float = 7.0
    lead_time_std_days: float = 0.0
    coverage_days: float = 14.0
    service_level: float = 0.95
    pack_size: int = 1
    min_order_qty: int = 0

    def validate(self) -> None:
        if not self.forecast:
            raise InventoryInputError("forecast is empty")
        if any((not math.isfinite(f)) or f < 0 for f in self.forecast):
            raise InventoryInputError("forecast values must be finite and non-negative")
        if not math.isfinite(self.sigma_d) or self.sigma_d < 0:
            raise InventoryInputError("sigma_d must be finite and non-negative")
        if self.on_hand < 0 or self.on_order < 0:
            raise InventoryInputError("inventory quantities cannot be negative")
        if not 0 < self.lead_time_days <= 365:
            raise InventoryInputError("lead time must be in (0, 365] days")
        if not 0 <= self.lead_time_std_days <= 180:
            raise InventoryInputError("lead-time standard deviation must be in [0, 180] days")
        if not 0 < self.coverage_days <= 365:
            raise InventoryInputError("coverage window must be in (0, 365] days")
        if not 0.5 < self.service_level < 0.9999:
            raise InventoryInputError("service level must be between 0.5 and 0.9999")
        if self.pack_size < 1 or self.min_order_qty < 0:
            raise InventoryInputError("pack size must be >= 1 and MOQ >= 0")


@dataclass
class InventoryResult:
    mean_daily_demand: float
    sigma_d: float
    z: float
    safety_stock: float
    reorder_point: float
    order_up_to: float
    inventory_position: float
    recommended_order_qty: int
    qty_to_order_up_to: float
    should_reorder: bool
    days_until_stockout: float | None
    days_of_cover: float | None
    stockout_probability_lead_time: float
    urgency: float
    urgency_components: dict[str, float]
    risk_level: str
    overstock_units: float
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


def z_for(service_level: float) -> float:
    return float(norm.ppf(service_level))


def mean_over(forecast: list[float], days: float) -> float:
    """Average daily demand over the first `days` days; beyond the forecast, the horizon mean is used."""
    f = np.asarray(forecast, dtype=float)
    n = int(math.ceil(days))
    if n <= len(f):
        return float(f[:n].mean())
    ext = np.concatenate([f, np.full(n - len(f), f.mean())])
    return float(ext.mean())


def days_until_stockout(forecast: list[float], on_hand: float, max_days: int = 3650) -> float | None:
    """First (fractional) day on which cumulative forecast demand exceeds on-hand stock.
    Returns None when no stockout is expected (zero demand).

    Edge cases: negative stock means unfilled backorders, so the product is already out (0). Zero stock with zero
    forecast demand is not a stockout (None): nothing is expected to sell, e.g. a discontinued SKU."""
    f = np.asarray(forecast, dtype=float)
    if on_hand < 0:
        return 0.0
    if on_hand == 0:
        return 0.0 if f.sum() > 0 else None
    cum = np.cumsum(f)
    idx = int(np.searchsorted(cum, on_hand, side="right"))
    if idx < len(f):
        prev = cum[idx - 1] if idx > 0 else 0.0
        return float(idx + (on_hand - prev) / f[idx]) if f[idx] > 0 else float(idx)
    rate = f.mean()
    if rate <= 0:
        return None
    remaining = on_hand - cum[-1]
    d = len(f) + remaining / rate
    return float(min(d, max_days))


def stockout_probability(
    forecast: list[float], sigma_d: float, on_hand: float, lead_time: float, lead_time_std: float = 0.0
) -> float:
    mu_daily = mean_over(forecast, lead_time)
    mu_L = mu_daily * lead_time
    sd = math.sqrt(lead_time * sigma_d**2 + (mu_daily**2) * lead_time_std**2)
    if sd <= 1e-12:
        return 1.0 if mu_L > on_hand else 0.0
    return float(1 - norm.cdf((on_hand - mu_L) / sd))


def classify(urgency: float, dts: float | None, lead_time: float) -> str:
    if urgency >= 75 or (dts is not None and dts <= 2):
        return "critical"
    if urgency >= 50 or (dts is not None and dts <= lead_time):
        return "high"
    if urgency >= 25:
        return "medium"
    return "low"


def compute(inp: InventoryInputs) -> InventoryResult:
    inp.validate()
    notes: list[str] = []
    L, C = inp.lead_time_days, inp.coverage_days
    mu = mean_over(inp.forecast, L)
    mu_cycle = mean_over(inp.forecast, L + C)
    if L + C > len(inp.forecast):
        notes.append(
            f"Lead time + coverage ({L + C:g} d) exceeds the {len(inp.forecast)}-day forecast; "
            "the horizon's mean demand is assumed beyond it"
        )
    z = z_for(inp.service_level)
    sd_L = math.sqrt(L * inp.sigma_d**2 + mu**2 * inp.lead_time_std_days**2)
    ss = z * sd_L
    rop = mu * L + ss
    s_level = mu_cycle * (L + C) + ss
    ip = inp.on_hand + inp.on_order
    should = ip <= rop and (mu > 0 or ss > 0)
    raw_qty = max(0.0, s_level - ip)
    qty = 0
    if should and raw_qty > 0:
        qty = int(math.ceil(raw_qty / inp.pack_size) * inp.pack_size)
        if qty < inp.min_order_qty:
            qty = int(math.ceil(inp.min_order_qty / inp.pack_size) * inp.pack_size)
            notes.append("Order rounded up to the minimum order quantity")
    dts = days_until_stockout(inp.forecast, inp.on_hand)
    p_out = stockout_probability(inp.forecast, inp.sigma_d, inp.on_hand, L, inp.lead_time_std_days)
    s_time = 0.0 if dts is None else 100 * min(max(1 - dts / (2 * L), 0.0), 1.0)
    s_prob = 100 * p_out
    s_inv = 100 * min(max(1 - ip / rop, 0.0), 1.0) if rop > 0 else 0.0
    urgency = (
        URGENCY_WEIGHTS["time"] * s_time
        + URGENCY_WEIGHTS["probability"] * s_prob
        + URGENCY_WEIGHTS["inventory"] * s_inv
    )
    overstock = max(0.0, ip - s_level) if mu > 0 else ip
    if mu == 0:
        notes.append("Forecast demand is zero: no stockout is expected and any stock is idle")
    return InventoryResult(
        mean_daily_demand=mu,
        sigma_d=inp.sigma_d,
        z=z,
        safety_stock=ss,
        reorder_point=rop,
        order_up_to=s_level,
        inventory_position=ip,
        recommended_order_qty=qty,
        qty_to_order_up_to=raw_qty,
        should_reorder=should,
        days_until_stockout=dts,
        days_of_cover=(inp.on_hand / mu) if mu > 0 else None,
        stockout_probability_lead_time=p_out,
        urgency=urgency,
        urgency_components={"time": round(s_time, 2), "probability": round(s_prob, 2), "inventory": round(s_inv, 2)},
        risk_level=classify(urgency, dts, L),
        overstock_units=overstock,
        notes=notes,
    )
