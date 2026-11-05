"""Australian personal tax for the backtester (resident individual, no HELP debt).

What's modelled:
  * Income tax on bank interest and ETF distributions, at your marginal rate on top
    of `income` (your other taxable income), using the 2026-27 rates for every
    year — so funds are compared under the rules you'll actually invest under.
    Includes the low income tax offset (LITO) and the Medicare levy.
  * Franking credits (refundable — paid back to you if they exceed your tax) and
    foreign income tax offsets (non-refundable — wasted if you owe no tax).
  * Capital gains tax on every sale, including selling everything at the end:
      "old" — 50% discount on gains from units held over 12 months (rules for
              gains up to 30 June 2027).
      "new" — Treasury Laws Amendment (Tax Reform No. 1) Act 2026: no discount;
              the cost base of units held 12+ months is indexed to CPI, and net
              real gains are taxed at least 30% (gains from 1 July 2027).
    Losses offset gains and carry forward; parcels are sold first-in, first-out.
  * Each financial year's tax is settled on 31 October after it ends (when you'd
    lodge); the last year is settled on the end date, after the final sale.

Simplifications: real distributions also carry capital-gain and tax-deferred
components (and AMIT cost-base adjustments); here every distribution is income
plus the per-fund credits in FUNDS. Thresholds aren't indexed over time.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

# --------------------------------------------------------------------------- #
# Income tax (2026-27 resident rates: 16% bracket cut to 15% from 1 July 2026)
# --------------------------------------------------------------------------- #
BRACKETS = [(0, 0.0), (18_200, 0.15), (45_000, 0.30), (135_000, 0.37), (190_000, 0.45)]
MEDICARE_RATE = 0.02
MEDICARE_LOW = 27_222      # single low-income threshold (2024-25); levy phases in at 10c per $ above it
MIN_CGT_RATE = 0.30        # "new" regime floor on net real capital gains


def _base_tax(x: float) -> float:
    t = 0.0
    for k, (lo, rate) in enumerate(BRACKETS):
        hi = BRACKETS[k + 1][0] if k + 1 < len(BRACKETS) else math.inf
        if x > lo:
            t += (min(x, hi) - lo) * rate
    return t


def _lito(x: float) -> float:
    if x <= 37_500:
        return 700.0
    if x <= 45_000:
        return 700 - 0.05 * (x - 37_500)
    return max(0.0, 325 - 0.015 * (x - 45_000))


def _medicare(x: float) -> float:
    return 0.0 if x <= MEDICARE_LOW else min(MEDICARE_RATE * x, 0.10 * (x - MEDICARE_LOW))


def income_tax_parts(x: float) -> tuple[float, float]:
    """(income tax after LITO, Medicare levy) on taxable income x."""
    return max(0.0, _base_tax(x) - _lito(x)), _medicare(x)


def income_tax(x: float) -> float:
    return sum(income_tax_parts(x))


def marginal_rate(income: float) -> float:
    """Effective marginal rate on the next $1,000 (includes LITO phase-out and Medicare)."""
    return (income_tax(income + 1000) - income_tax(income)) / 1000


# --------------------------------------------------------------------------- #
# Per-fund distribution tax character
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FundTax:
    franking: float   # franking credits per $1 of cash distribution (refundable)
    foreign: float    # foreign tax offset per $1 of cash distribution (non-refundable)
    drp: bool         # issuer runs a dividend reinvestment plan
    note: str = ""


# Ratios are rough multi-year averages — check the fund's annual tax statement (AMMA)
# and edit them if you want more precision. Fully franked = 30/70 = 0.43.
FUNDS = {
    "VHY.AX": FundTax(0.38, 0.00, True, "from an Apr 2026 distribution statement (franking credit / cash = 0.38)"),
    "VAS.AX": FundTax(0.30, 0.01, True, "~70% franked"),
    "A200.AX": FundTax(0.32, 0.00, True, "~75% franked"),
    "IVV.AX": FundTax(0.00, 0.12, True, "US dividends, ~15% US withholding"),
    "NDQ.AX": FundTax(0.00, 0.12, True, "US dividends, ~15% US withholding"),
    "VGS.AX": FundTax(0.00, 0.10, True, "global dividends, mixed withholding"),
    "VEQ.AX": FundTax(0.00, 0.10, True, "European dividends, mixed withholding"),
    "VTS.AX": FundTax(0.00, 0.176, False, "US-domiciled: 15% withheld (15/85), no DRP"),
    "VGE.AX": FundTax(0.00, 0.10, True, "emerging-market dividends, mixed withholding"),
    "IEM.AX": FundTax(0.00, 0.10, True, "emerging-market dividends, mixed withholding"),
    "NDIA.AX": FundTax(0.00, 0.20, False, "Indian dividends (~20% withholding); DRP not confirmed"),
    # diversified funds: ~57% of their cash distributions come from the Australian part
    "DHHF.AX": FundTax(0.17, 0.05, True, "~37% Australian shares, rest global"),
    "VDHG.AX": FundTax(0.15, 0.04, True, "~36% Australian shares, 10% bonds, rest global"),
}
UNKNOWN_FUND = FundTax(0.0, 0.0, False, "unknown fund — no credits, no DRP assumed")


def fund_tax(symbol: str) -> FundTax:
    return FUNDS.get(symbol.upper(), UNKNOWN_FUND)


# --------------------------------------------------------------------------- #
# CPI (ABS All groups, weighted average of eight capital cities, quarterly)
# --------------------------------------------------------------------------- #
CPI_URL = ("https://data.api.abs.gov.au/rest/data/ABS,CPI,1.1.0/1.10001.10.50.Q"
           "?startPeriod=1999-Q1")
CPI_FALLBACK_GROWTH = 0.03    # p.a., for quarters after the last published one
_cpi: dict[tuple[int, int], float] | None = None


def _load_cpi() -> dict[tuple[int, int], float]:
    global _cpi
    if _cpi is not None:
        return _cpi
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / "cpi_au.json"
    if not cache.exists() or time.time() - cache.stat().st_mtime > 30 * 86400:
        try:
            req = urllib.request.Request(CPI_URL, headers={"Accept": "application/vnd.sdmx.data+csv"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                lines = resp.read().decode().strip().splitlines()
            head = lines[0].split(",")
            ti, vi = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
            rows = {r.split(",")[ti]: float(r.split(",")[vi]) for r in lines[1:]}
            if rows:
                cache.write_text(json.dumps(rows, sort_keys=True))
        except Exception as e:
            if not cache.exists():
                raise SystemExit(f"Couldn't download CPI from the ABS ({e}); needed for --cgt new.")
            print(f"  (CPI download failed: {e} — using cached CPI)")
    raw = json.loads(cache.read_text())
    _cpi = {(int(k[:4]), int(k[-1])): v for k, v in raw.items()}
    return _cpi


def cpi_at(d: date) -> float:
    """CPI for the quarter containing d; extrapolated past the last published quarter."""
    cpi = _load_cpi()
    key = (d.year, (d.month - 1) // 3 + 1)
    if key in cpi:
        return cpi[key]
    first, last = min(cpi), max(cpi)
    if key < first:
        return cpi[first]
    quarters = (key[0] - last[0]) * 4 + key[1] - last[1]
    return cpi[last] * (1 + CPI_FALLBACK_GROWTH) ** (quarters / 4)


# --------------------------------------------------------------------------- #
# Financial-year ledger
# --------------------------------------------------------------------------- #
def fy_of(d: date) -> int:
    """Financial year label: FY2026 = 1 July 2025 to 30 June 2026."""
    return d.year + 1 if d.month >= 7 else d.year


def settle_date(fy: int) -> date:
    return date(fy, 10, 31)


def held_over_12_months(bought: date, sold: date) -> bool:
    """ATO test: at least 12 months between the days, excluding both."""
    try:
        anniversary = bought.replace(year=bought.year + 1)
    except ValueError:                       # 29 Feb
        anniversary = date(bought.year + 1, 3, 1)
    return sold > anniversary


@dataclass
class TaxYear:
    interest: float = 0.0
    dividends: float = 0.0      # cash distributions
    franking: float = 0.0
    foreign: float = 0.0
    gains_short: float = 0.0    # held 12 months or less (old: no discount; new: not indexed)
    gains_long: float = 0.0     # held over 12 months (old: discountable; new: real gain after indexing)
    losses: float = 0.0


def capital_gain(cost: float, proceeds: float, bought: date, sold: date, regime: str) -> tuple[float, bool]:
    """(gain or negative loss, long-held?) for one parcel."""
    long = held_over_12_months(bought, sold)
    if regime == "new" and long:
        indexed = cost * cpi_at(sold) / cpi_at(bought)
        if proceeds >= indexed:
            return proceeds - indexed, True
        if proceeds >= cost:
            return 0.0, True                  # indexation can't create a loss
        return proceeds - cost, True          # losses use the nominal cost base
    return proceeds - cost, long


def year_tax(y: TaxYear, income: float, regime: str, carried_loss: float) -> tuple[dict, float]:
    """Extra tax for one financial year caused by investing (negative = refund).
    Returns (breakdown, capital losses carried forward)."""
    losses = y.losses + carried_loss
    short, long = y.gains_short, y.gains_long
    use = min(losses, short); short -= use; losses -= use
    use = min(losses, long); long -= use; losses -= use
    net_cg = short + (long * 0.5 if regime == "old" else long)

    investment_income = y.interest + y.dividends + y.franking + y.foreign
    total = income + investment_income + net_cg
    base_it, base_ml = income_tax_parts(income)
    it, ml = income_tax_parts(total)
    extra = (it + ml) - (base_it + base_ml)
    fito = min(y.foreign, max(0.0, it - base_it))           # non-refundable, can't cut Medicare

    cgt = income_tax(total) - income_tax(total - net_cg)   # tax on the gain as the top slice
    top_up = max(0.0, MIN_CGT_RATE * net_cg - cgt) if regime == "new" else 0.0

    net = extra - fito + top_up - y.franking
    return dict(net=net, cgt=cgt + top_up, top_up=top_up, net_gain=net_cg,
                franking=y.franking, fito=fito, taxable=total - income), losses
