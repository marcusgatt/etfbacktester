"""Backtest engine: market data, the day-by-day simulator, and result metrics.

Pure standard library — no pip installs needed.

Simulation model (one ETF + one bank account):
  * Contributions land in the bank account on a schedule (monthly by default).
  * Cash in the bank earns interest every calendar day (rate is % p.a., accrued
    daily like an Australian savings account).
  * You're entitled to a dividend if you held shares the day before the ex-dividend
    date; it's paid `pay_lag_days` later (Yahoo only provides ex-dates). With
    `drp=True` and a fund that runs a DRP, it's reinvested in new units at the
    ex-date close with no brokerage (leftover cents carry to the next payment);
    otherwise the cash lands in the bank and a strategy can choose to invest it.
  * Your strategy runs after each day's close and sees prices up to and including
    that close. Orders it places fill at the NEXT trading day's open (no peeking),
    in whole shares, with a brokerage fee per trade.
  * With `tax=True` (default), Australian tax is charged — see tax.py. At the end
    everything is sold and the last tax bill settled, giving after-tax results.
"""

from __future__ import annotations

import bisect
import calendar
import copy
import json
import math
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import tax

DATA_DIR = Path(__file__).resolve().parent / "data"


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
@dataclass
class MarketData:
    symbol: str                 # Yahoo symbol, e.g. VHY.AX
    currency: str
    dates: list[date]
    opens: list[float]
    closes: list[float]
    adj_closes: list[float]       # dividend-adjusted closes (total-return series)
    dividends: dict[date, float]  # ex-date -> cash per share
    valid_from: date | None = None  # earliest date with trustworthy dividend data


def yahoo_symbol(ticker: str) -> str:
    """'ASX:VHY' -> 'VHY.AX'; 'NASDAQ:QQQ' -> 'QQQ'; Yahoo symbols pass through."""
    t = ticker.strip().upper()
    if ":" not in t:
        return t
    exch, code = t.split(":", 1)
    suffix = {"ASX": ".AX", "LON": ".L", "TSE": ".TO", "NZE": ".NZ"}.get(exch, "")
    return code + suffix


def load_market_data(ticker: str, refresh: bool = False, max_age_hours: float = 12) -> MarketData:
    """Daily prices + dividends from Yahoo Finance, cached in ./data."""
    symbol = yahoo_symbol(ticker)
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / f"{symbol}.json"
    fresh = cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600
    if refresh or not fresh:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               f"?period1=0&period2={int(time.time()) + 86400}&interval=1d&events=div%2Csplit")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            json.loads(raw)["chart"]["result"][0]["timestamp"]  # validate before caching
            cache.write_bytes(raw)
        except Exception as e:
            if not cache.exists():
                raise SystemExit(f"Couldn't download data for {symbol} ({e}). Check the ticker "
                                 f"(e.g. ASX:VHY or VHY.AX) and your internet connection.")
            print(f"  (download failed: {e} — using cached data from "
                  f"{datetime.fromtimestamp(cache.stat().st_mtime):%Y-%m-%d %H:%M})")

    res = json.loads(cache.read_text())["chart"]["result"][0]
    tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "UTC")
    q = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or q["close"]
    by_day: dict[date, tuple[float, float, float]] = {}
    for ts, o, c, a in zip(res["timestamp"], q["open"], q["close"], adj):
        if c is None or c <= 0:
            continue
        d = datetime.fromtimestamp(ts, tz).date()
        by_day[d] = (o if o and o > 0 else c, c, a if a and a > 0 else c)  # later rows win (live bar)
    days = sorted(by_day)
    divs = {}
    for ev in (res.get("events") or {}).get("dividends", {}).values():
        d = datetime.fromtimestamp(ev["date"], tz).date()
        divs[d] = divs.get(d, 0.0) + float(ev["amount"])
    md = MarketData(symbol, res["meta"].get("currency", ""), days,
                    [by_day[d][0] for d in days], [by_day[d][1] for d in days],
                    [by_day[d][2] for d in days], divs)
    _clean(md)
    return md


_warned: set[str] = set()


def _clean(md: MarketData) -> None:
    """Repair Yahoo data glitches: one-day price spikes that snap back next day are
    replaced with the previous close; a lasting >3x jump (an unadjusted share split)
    means older data is on a different scale, so everything before it is dropped."""
    notes = []
    c = md.closes
    for i in range(1, len(c) - 1):
        r1, r2 = c[i] / c[i - 1], c[i + 1] / c[i - 1]
        if (r1 > 3 or r1 < 1 / 3) and 0.8 < r2 < 1.25:
            notes.append(f"bad price on {md.dates[i]} (${c[i]:.2f}) replaced with previous close")
            md.opens[i], c[i], md.adj_closes[i] = c[i - 1], c[i - 1], md.adj_closes[i - 1]
    cut = max((i for i in range(1, len(c)) if not 1 / 3 < c[i] / c[i - 1] < 3), default=0)
    if cut:
        notes.append(f"price scale break on {md.dates[cut]} (likely an unadjusted split) — "
                     f"using data from then on only")
        for name in ("dates", "opens", "closes", "adj_closes"):
            setattr(md, name, getattr(md, name)[cut:])
        md.dividends = {d: a for d, a in md.dividends.items() if d >= md.dates[0]}
    # Yahoo often lacks dividends for a fund's early years (e.g. VAS/VHY before 2013).
    # Treat data as reliable from one payment interval before the first recorded dividend.
    dv = sorted(md.dividends)
    md.valid_from = md.dates[0]
    if len(dv) >= 2:
        md.valid_from = max(md.dates[0], dv[0] - (dv[1] - dv[0]))
        if (md.valid_from - md.dates[0]).days > 200:
            notes.append(f"no dividend data before {dv[0]} — results are only reliable from {md.valid_from}")
    if notes and md.symbol not in _warned:
        _warned.add(md.symbol)
        for n in notes:
            print(f"  note ({md.symbol}): {n}")


# --------------------------------------------------------------------------- #
# Settings & results
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    ticker: str = "ASX:VHY"
    start: date = date(2015, 1, 1)
    end: date | None = None                 # None = latest available
    contribution: float = 1000.0            # added to the bank each period
    frequency: str = "monthly"              # monthly | fortnightly | weekly
    initial: float = 0.0                    # lump sum in the bank on day one
    bank_rate: float = 5.5                  # % p.a., accrued daily
    fee: float = 3.0                        # brokerage per trade ($)
    pay_lag_days: int = 16                  # ex-date -> cash in bank
    fill: str = "next_open"                 # next_open | close
    tax: bool = True                        # Australian income tax + CGT (see tax.py)
    income: float = 40_000.0                # your other taxable income each year
    cgt_regime: str = "new"                 # new (indexation + 30% minimum) | old (50% discount)
    drp: bool = False                       # reinvest dividends via the fund's DRP, if it has one


@dataclass
class Trade:
    date: date
    side: str          # BUY | SELL
    shares: int
    price: float
    fee: float
    cash_after: float
    note: str = ""


@dataclass
class Result:
    name: str
    description: str
    dates: list[date] = field(default_factory=list)
    values: list[float] = field(default_factory=list)      # total portfolio value each day
    cash: list[float] = field(default_factory=list)
    contributed: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    logs: list[tuple[date, str]] = field(default_factory=list)
    flows: list[tuple[date, float]] = field(default_factory=list)  # external cash flows (for XIRR)
    dividends: float = 0.0
    interest: float = 0.0
    fees: float = 0.0
    final_shares: int = 0
    final_cash: float = 0.0
    final_price: float = 0.0
    receivable: float = 0.0
    drp_units: int = 0
    franking: float = 0.0          # franking credits earned
    tax_paid: float = 0.0          # net tax over the whole run (after credits/refunds), incl. final sale
    cgt_paid: float = 0.0          # part of tax_paid due to capital gains (incl. any 30% top-up)
    tax_years: list[tuple[int, dict]] = field(default_factory=list)
    after_tax_value: float = 0.0   # cash in hand after selling everything and settling all tax

    # ---- metrics -------------------------------------------------------- #
    @property
    def final_value(self) -> float:
        return self.values[-1] if self.values else 0.0

    @property
    def total_contributed(self) -> float:
        return self.contributed[-1] if self.contributed else 0.0

    @property
    def profit(self) -> float:
        return self.final_value - self.total_contributed

    @property
    def xirr(self) -> float | None:
        flows = self.flows + [(self.dates[-1], self.final_value)]
        return xirr(flows)

    @property
    def after_tax_xirr(self) -> float | None:
        """XIRR if you sold everything on the last day and paid all tax owed."""
        return xirr(self.flows + [(self.dates[-1], self.after_tax_value)])

    @property
    def avg_cash_pct(self) -> float:
        pts = [c / v for c, v in zip(self.cash, self.values) if v > 0]
        return sum(pts) / len(pts) if pts else 0.0

    @property
    def avg_buy_price(self) -> float | None:
        buys = [t for t in self.trades if t.side == "BUY"]
        n = sum(t.shares for t in buys)
        return sum(t.shares * t.price for t in buys) / n if n else None

    @property
    def max_drawdown(self) -> float:
        """Worst peak-to-trough fall in profit, as a share of money contributed at the
        time — measures how bad it got without counting new deposits as gains."""
        worst, peak = 0.0, -math.inf
        for v, c in zip(self.values, self.contributed):
            p = v - c
            peak = max(peak, p)
            if c > 0:
                worst = min(worst, (p - peak) / c)
        return worst


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised money-weighted return. Negative flows = money in, positive = money out."""
    flows = [(d, a) for d, a in flows if abs(a) > 1e-9]
    if not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(r):
        return sum(a / (1 + r) ** ((d - t0).days / 365.0) for d, a in flows)

    lo, hi = -0.9999, 10.0
    if npv(lo) * npv(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------- #
# Strategy interface
# --------------------------------------------------------------------------- #
class Strategy:
    """Subclass this. Override `on_day` (and optionally `setup`).

    Store any state you need on `self`. Every strategy instance is run in its own
    fresh simulation, so state never leaks between strategies.
    """
    name = "Unnamed strategy"
    description = ""

    def setup(self, ctx: "Context") -> None:
        pass

    def on_day(self, ctx: "Context") -> None:
        raise NotImplementedError


class Context:
    """What a strategy can see and do.

    Price history helpers take `adjusted=False` by default: the actual traded
    price, which drops by the dividend on each ex-date. Pass `adjusted=True` for
    the dividend-adjusted (total-return) series — better for trend and drawdown
    signals on high-yield ETFs, where raw prices drift down with every payout.
    Trades always happen at the actual price.
    """

    def __init__(self, sim: "_Simulation"):
        self._s = sim

    def _series(self, adjusted: bool) -> list[float]:
        return self._s.md.adj_closes if adjusted else self._s.md.closes

    # ---- today ---------------------------------------------------------- #
    @property
    def date(self) -> date:
        return self._s.md.dates[self._s.i]

    @property
    def price(self) -> float:
        """Today's closing price."""
        return self._s.md.closes[self._s.i]

    @property
    def open(self) -> float:
        return self._s.md.opens[self._s.i]

    @property
    def is_first_day(self) -> bool:
        return self._s.i == self._s.first_i

    @property
    def is_new_month(self) -> bool:
        """True on the first trading day of each calendar month."""
        i = self._s.i
        return i == 0 or self._s.md.dates[i - 1].month != self.date.month

    @property
    def contribution_today(self) -> float:
        """Amount deposited into the bank today (0 on non-contribution days)."""
        return self._s.contrib_today

    @property
    def dividend_today(self) -> float:
        """Dividend cash that landed in the bank today."""
        return self._s.div_paid_today

    # ---- account -------------------------------------------------------- #
    @property
    def cash(self) -> float:
        """Bank balance minus money already committed to unfilled buy orders."""
        return self._s.cash - self._s.pending_buy_total()

    @property
    def shares(self) -> int:
        """Shares held, including pending (unfilled) orders."""
        return self._s.shares + self._s.pending_share_delta()

    @property
    def holdings_value(self) -> float:
        return self._s.shares * self.price

    @property
    def total_value(self) -> float:
        return self._s.cash + self._s.shares * self.price + self._s.receivable()

    @property
    def contributed(self) -> float:
        return self._s.contributed

    @property
    def settings(self) -> Settings:
        return self._s.settings

    # ---- price history (up to and including today) ---------------------- #
    def closes(self, n: int, adjusted: bool = False) -> list[float]:
        """The last n trading-day closes, oldest first, ending today."""
        i = self._s.i
        return self._series(adjusted)[max(0, i - n + 1): i + 1]

    def closes_since(self, days: int, adjusted: bool = False) -> list[float]:
        """Closes over the last `days` calendar days, including today."""
        i = self._s.i
        cutoff = self.date - timedelta(days=days - 1)
        j = bisect.bisect_left(self._s.md.dates, cutoff, 0, i + 1)
        return self._series(adjusted)[j: i + 1]

    def lowest(self, days: int, adjusted: bool = False) -> float:
        return min(self.closes_since(days, adjusted))

    def highest(self, days: int, adjusted: bool = False) -> float:
        return max(self.closes_since(days, adjusted))

    def is_lowest(self, days: int, adjusted: bool = False) -> bool:
        """Is today's close the lowest close of the last `days` calendar days?"""
        return self.closes(1, adjusted)[0] <= self.lowest(days, adjusted)

    def drawdown(self, days: int, adjusted: bool = True) -> float:
        """How far today's close is below the highest close of the last `days`
        calendar days (0.10 = 10% below). Adjusted by default."""
        return 1 - self.closes(1, adjusted)[0] / self.highest(days, adjusted)

    def sma(self, n: int, adjusted: bool = False) -> float:
        """Simple moving average of the last n trading-day closes."""
        xs = self.closes(n, adjusted)
        return sum(xs) / len(xs)

    def month_end_closes(self, n: int, adjusted: bool = False) -> list[float]:
        """Closes on the last trading day of each of the last n *completed* calendar
        months (the current month isn't included), oldest first."""
        dates, closes, out = self._s.md.dates, self._series(adjusted), []
        j = self._s.i - 1
        while j >= 0 and len(out) < n:
            if dates[j].month != dates[j + 1].month:
                out.append(closes[j])
            j -= 1
        return out[::-1]

    def change(self, days: int) -> float:
        """% change from the close `days` calendar days ago (or nearest before) to today."""
        xs = self.closes_since(days + 1)
        return self.price / xs[0] - 1

    # ---- orders --------------------------------------------------------- #
    def buy(self, amount: float, note: str = "") -> None:
        """Spend up to `amount` dollars (fee included) on whole shares."""
        amount = min(amount, self.cash)
        if amount > self._s.settings.fee:
            self._s.place("BUY", amount=amount, note=note)

    def buy_all(self, note: str = "") -> None:
        self.buy(self.cash, note)

    def buy_shares(self, n: int, note: str = "") -> None:
        if n > 0:
            self._s.place("BUY", shares=int(n), note=note)

    def sell(self, shares: int | None = None, note: str = "") -> None:
        """Sell `shares` (default: everything). Proceeds minus fee go to the bank."""
        n = self.shares if shares is None else min(int(shares), self.shares)
        if n > 0:
            self._s.place("SELL", shares=n, note=note)

    def sell_all(self, note: str = "") -> None:
        self.sell(None, note)

    def log(self, message: str) -> None:
        """Record a note that shows up in the report's activity log."""
        self._s.result.logs.append((self.date, message))


# --------------------------------------------------------------------------- #
# Simulator
# --------------------------------------------------------------------------- #
def _add_months(d: date, n: int, day: int) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _contribution_dates(s: Settings, end: date) -> list[date]:
    out, k = [], 0
    while True:
        if s.frequency == "monthly":
            d = _add_months(s.start, k, s.start.day)
        elif s.frequency in ("fortnightly", "weekly"):
            d = s.start + timedelta(days=k * (14 if s.frequency == "fortnightly" else 7))
        else:
            raise ValueError(f"Unknown frequency {s.frequency!r}")
        if d > end:
            return out
        out.append(d)
        k += 1


class _Simulation:
    def __init__(self, md: MarketData, s: Settings, strategy: Strategy):
        self.md, self.settings, self.strategy = md, s, strategy
        self.result = Result(strategy.name, strategy.description)
        self.cash = 0.0
        self.shares = 0
        self.contributed = 0.0
        self.pending: list[dict] = []
        self.unpaid_divs: list[tuple[date, float, float]] = []   # (pay date, amount, DRP price)
        self.i = 0
        self.first_i = 0
        self.contrib_today = 0.0
        self.div_paid_today = 0.0
        # tax & reinvestment state
        self.fund = tax.fund_tax(md.symbol)
        self.use_drp = s.drp and self.fund.drp
        self.drp_residual = 0.0                       # DRP cents carried to the next payment
        self.lots: list[list] = []                    # [buy date, units, cost base], oldest first
        self.years: dict[int, tax.TaxYear] = {}
        self.settled: set[int] = set()
        self.carried_loss = 0.0

    def pending_buy_total(self) -> float:
        total = 0.0
        for o in self.pending:
            if o["side"] == "BUY":
                total += o["amount"] if o["amount"] is not None else o["shares"] * self.md.closes[self.i] + self.settings.fee
        return total

    def pending_share_delta(self) -> int:
        return sum((-o["shares"] if o["side"] == "SELL" else 0) for o in self.pending)

    def receivable(self) -> float:
        return sum(a for _, a, _ in self.unpaid_divs) + self.drp_residual

    def place(self, side, amount=None, shares=None, note=""):
        self.pending.append(dict(side=side, amount=amount, shares=shares, note=note))
        if self.settings.fill == "close":
            self.fill_orders(self.md.closes[self.i])

    # ---- tax helpers ---------------------------------------------------- #
    def year(self, d: date) -> tax.TaxYear:
        return self.years.setdefault(tax.fy_of(d), tax.TaxYear())

    def dispose(self, n: int, proceeds: float, today: date) -> None:
        """Sell n units first-in, first-out and record each parcel's gain or loss."""
        per_unit = proceeds / n
        while n > 0:
            lot = self.lots[0]
            k = min(n, lot[1])
            cost = lot[2] * k / lot[1]
            gain, long = tax.capital_gain(cost, per_unit * k, lot[0], today, self.settings.cgt_regime)
            y = self.year(today)
            if gain < 0:
                y.losses -= gain
            elif long:
                y.gains_long += gain
            else:
                y.gains_short += gain
            lot[1] -= k
            lot[2] -= cost
            n -= k
            if lot[1] == 0:
                self.lots.pop(0)

    def settle(self, fy: int, today: date) -> None:
        s, r = self.settings, self.result
        b, self.carried_loss = tax.year_tax(self.years[fy], s.income, s.cgt_regime, self.carried_loss)
        self.settled.add(fy)
        self.cash -= b["net"]
        r.tax_paid += b["net"]
        r.cgt_paid += b["cgt"]
        r.franking += b["franking"]
        r.tax_years.append((fy, b))
        r.logs.append((today, f"FY{fy} tax {'refund' if b['net'] < 0 else 'paid'}: ${abs(b['net']):,.2f}"
                              f" (franking credits ${b['franking']:,.2f}, CGT ${b['cgt']:,.2f})"))

    def fill_orders(self, price: float):
        fee = self.settings.fee
        today = self.md.dates[self.i]
        for o in self.pending:
            if o["side"] == "BUY":
                budget = min(o["amount"] if o["amount"] is not None else o["shares"] * price + fee, self.cash)
                n = math.floor((budget - fee) / price) if budget > fee else 0
                if o["shares"] is not None:
                    n = min(n, o["shares"])
                if n <= 0:
                    continue
                self.cash -= n * price + fee
                self.shares += n
                self.lots.append([today, n, n * price + fee])
            else:
                n = min(o["shares"], self.shares)
                if n <= 0:
                    continue
                self.cash += n * price - fee
                self.shares -= n
                self.dispose(n, n * price - fee, today)
            self.result.fees += fee
            self.result.trades.append(Trade(today, o["side"], n, price, fee, self.cash, o["note"]))
        self.pending = []

    def run(self) -> Result:
        md, s, r = self.md, self.settings, self.result
        end = s.end or md.dates[-1]
        self.first_i = bisect.bisect_left(md.dates, s.start)
        last_i = bisect.bisect_right(md.dates, end) - 1
        if self.first_i >= len(md.dates) or last_i < self.first_i:
            raise SystemExit(f"No price data between {s.start} and {end} "
                             f"(data covers {md.dates[0]} to {md.dates[-1]}).")
        contrib_dates = _contribution_dates(s, md.dates[last_i])
        ci = 0
        ctx = Context(self)
        rate = s.bank_rate / 100 / 365
        prev_day = md.dates[self.first_i]

        if s.initial > 0:
            self.cash += s.initial
            self.contributed += s.initial
            r.flows.append((prev_day, -s.initial))

        for i in range(self.first_i, last_i + 1):
            self.i = i
            today = md.dates[i]
            self.contrib_today = self.div_paid_today = 0.0

            # 1. bank interest for the calendar days since the last trading day
            days = (today - prev_day).days
            if days > 0 and self.cash > 0:
                interest = self.cash * ((1 + rate) ** days - 1)
                self.cash += interest
                r.interest += interest
                self.year(today).interest += interest
            prev_day = today

            # 2. dividend entitlement: shares held going into the ex-date. A distribution
            #    with an early-July ex-date is income of the year that just ended.
            if today in md.dividends and self.shares > 0:
                amt = self.shares * md.dividends[today]
                y = self.year(today - timedelta(days=1))
                y.dividends += amt
                y.franking += amt * self.fund.franking
                y.foreign += amt * self.fund.foreign
                self.unpaid_divs.append((today + timedelta(days=s.pay_lag_days), amt, md.closes[i]))
                ctx.log(f"Ex-dividend: {self.shares} shares x ${md.dividends[today]:.4f} = ${amt:,.2f} "
                        f"(paid ~{s.pay_lag_days} days later{', reinvested via DRP' if self.use_drp else ''})")

            # 3. yesterday's orders fill at today's open
            if self.pending:
                self.fill_orders(md.opens[i])

            # 4. dividends paid today: DRP units, or cash into the bank
            due = [(a, p) for d, a, p in self.unpaid_divs if d <= today]
            if due:
                self.unpaid_divs = [x for x in self.unpaid_divs if x[0] > today]
                for amt, drp_price in due:
                    r.dividends += amt
                    if self.use_drp:
                        pot = amt + self.drp_residual
                        n = math.floor(pot / drp_price)
                        self.drp_residual = pot - n * drp_price
                        if n:
                            self.shares += n
                            self.lots.append([today, n, n * drp_price])
                            r.drp_units += n
                            ctx.log(f"DRP: {n} units at ${drp_price:.2f}")
                    else:
                        self.cash += amt
                        self.div_paid_today += amt

            # 5. last financial year's tax bill / refund (31 October)
            if s.tax:
                for fy in sorted(self.years):
                    if fy not in self.settled and tax.settle_date(fy) <= today:
                        self.settle(fy, today)

            # 6. scheduled contributions
            while ci < len(contrib_dates) and contrib_dates[ci] <= today:
                self.cash += s.contribution
                self.contributed += s.contribution
                self.contrib_today += s.contribution
                r.flows.append((today, -s.contribution))
                ci += 1

            # 7. strategy decides after the close
            if i == self.first_i:
                self.strategy.setup(ctx)
            self.strategy.on_day(ctx)

            r.dates.append(today)
            r.values.append(self.cash + self.shares * md.closes[i] + self.receivable())
            r.cash.append(self.cash)
            r.contributed.append(self.contributed)

        r.final_shares, r.final_cash = self.shares, self.cash
        r.final_price, r.receivable = md.closes[last_i], self.receivable()
        self.cash_out(md.dates[last_i], md.closes[last_i])
        return r

    def cash_out(self, today: date, price: float) -> None:
        """Sell everything at the last close, collect dividends still owed, settle all tax."""
        r, s = self.result, self.settings
        self.cash += self.receivable()          # declared dividends (already in the tax ledger) + DRP cents
        self.unpaid_divs, self.drp_residual = [], 0.0
        if self.shares:
            proceeds = self.shares * price - s.fee
            self.cash += proceeds
            self.dispose(self.shares, proceeds, today)
            r.logs.append((today, f"Final sale: {self.shares} units at ${price:.2f} = ${proceeds:,.2f} after fee"))
            self.shares = 0
        if s.tax:
            for fy in sorted(self.years):
                if fy not in self.settled:
                    self.settle(fy, today)
        r.after_tax_value = self.cash


def run_backtest(md: MarketData, settings: Settings, strategy: Strategy) -> Result:
    # deepcopy so state a strategy keeps on `self` never carries over between runs
    return _Simulation(md, settings, copy.deepcopy(strategy)).run()
