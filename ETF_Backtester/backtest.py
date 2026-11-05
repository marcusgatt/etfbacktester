#!/usr/bin/env python3
"""Backtest ETF investing strategies on real price + dividend history.

  python3 backtest.py                                   # VHY, defaults below
  python3 backtest.py --ticker ASX:VAS --start 2018-01-01
  python3 backtest.py --contribution 500 --frequency fortnightly --bank-rate 5
  python3 backtest.py --only dip                        # just strategies whose name contains "dip"
  python3 backtest.py --strategies my_ideas.py          # load STRATEGIES from another file

Strategies are defined in strategies.py. Results print here and open as an
HTML report (charts + trade log) in your browser.
"""

import argparse
import importlib.util
import sys
import webbrowser
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import Settings, load_market_data, run_backtest  # noqa: E402
from report import write_report  # noqa: E402

# Defaults — edit these, or override any of them on the command line.
DEFAULTS = Settings(
    ticker="ASX:VHY",
    start=date(2015, 1, 1),
    end=None,                 # None = up to the latest price
    contribution=1000.0,
    frequency="monthly",
    initial=0.0,
    bank_rate=5.5,
    fee=3.0,
    pay_lag_days=16,
    fill="next_open",
    tax=True,
    income=40_000.0,          # your other taxable income per year
    cgt_regime="new",         # new (from 1 July 2027) | old (50% discount)
    drp=False,
)


def load_strategies(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not getattr(mod, "STRATEGIES", None):
        raise SystemExit(f"{path} has no STRATEGIES list.")
    return mod.STRATEGIES


def money(x):
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def main():
    d = DEFAULTS
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticker", default=d.ticker, help="e.g. ASX:VHY, ASX:VAS, VHY.AX, VOO (default %(default)s)")
    p.add_argument("--start", type=date.fromisoformat, default=d.start, help="YYYY-MM-DD (default %(default)s)")
    p.add_argument("--end", type=date.fromisoformat, default=d.end, help="YYYY-MM-DD (default: latest)")
    p.add_argument("--contribution", type=float, default=d.contribution, help="$ per period (default %(default)s)")
    p.add_argument("--frequency", choices=["monthly", "fortnightly", "weekly"], default=d.frequency)
    p.add_argument("--initial", type=float, default=d.initial, help="lump sum in the bank on day one")
    p.add_argument("--bank-rate", type=float, default=d.bank_rate, help="savings rate, %% p.a. (default %(default)s)")
    p.add_argument("--fee", type=float, default=d.fee, help="brokerage per trade (default %(default)s)")
    p.add_argument("--pay-lag", type=int, default=d.pay_lag_days, help="days from ex-date to dividend payment")
    p.add_argument("--fill", choices=["next_open", "close"], default=d.fill,
                   help="next_open (default, realistic) or close (fill at the close the strategy just saw)")
    p.add_argument("--income", type=float, default=d.income,
                   help="your other taxable income per year, for tax (default %(default)s)")
    p.add_argument("--cgt", choices=["new", "old"], default=d.cgt_regime,
                   help="new = indexation + 30%% minimum (default); old = 50%% discount")
    p.add_argument("--no-tax", action="store_true", help="ignore all tax")
    p.add_argument("--drp", action="store_true",
                   help="reinvest dividends through the fund's DRP (no fee) when it has one")
    p.add_argument("--strategies", type=Path, default=HERE / "strategies.py", help="file defining STRATEGIES")
    p.add_argument("--only", help="run only strategies whose name contains this text (baselines always kept)")
    p.add_argument("--refresh", action="store_true", help="re-download prices even if cached today")
    p.add_argument("--no-open", action="store_true", help="don't open the report in a browser")
    a = p.parse_args()

    s = Settings(a.ticker, a.start, a.end, a.contribution, a.frequency, a.initial,
                 a.bank_rate, a.fee, a.pay_lag, a.fill, tax=not a.no_tax, income=a.income,
                 cgt_regime=a.cgt, drp=a.drp)
    strategies = load_strategies(a.strategies)
    if a.only:
        strategies = [st for st in strategies
                      if a.only.lower() in st.name.lower() or getattr(st, "baseline", False)]

    print(f"Loading {s.ticker} ...")
    md = load_market_data(s.ticker, refresh=a.refresh)
    print(f"  {len(md.dates):,} trading days ({md.dates[0]} to {md.dates[-1]}), {len(md.dividends)} dividends")

    if s.start < md.valid_from:
        print(f"  WARNING: {md.symbol} has no dividend data before ~{md.valid_from}; starting there instead.")
        s.start = md.valid_from

    results = []
    for st in strategies:
        results.append(run_backtest(md, s, st))

    r0 = results[0]
    print(f"\n{md.symbol}  {r0.dates[0]} to {r0.dates[-1]}  ·  ${s.contribution:,.0f} {s.frequency}"
          f"  ·  bank {s.bank_rate}%  ·  fee ${s.fee:g}"
          + (f"  ·  tax on ${s.income:,.0f} income, {s.cgt_regime} CGT rules" if s.tax else "  ·  no tax")
          + ("  ·  DRP" if s.drp else "") + "\n")
    pct = lambda x: "n/a" if x is None else f"{x:.2%}"
    cols = ["Strategy", "Value", "XIRR", "After tax*", "XIRR*", "Tax paid", "Dividends", "Trades", "Avg cash"]
    rows = []
    for r in results:
        rows.append([r.name, money(r.final_value), pct(r.xirr), money(r.after_tax_value), pct(r.after_tax_xirr),
                     money(r.tax_paid), money(r.dividends), str(len(r.trades)), f"{r.avg_cash_pct:.0%}"])
    widths = [max(len(c), *(len(row[i]) for row in rows)) for i, c in enumerate(cols)]
    line = lambda row: "  ".join(v.ljust(w) if i == 0 else v.rjust(w) for i, (v, w) in enumerate(zip(row, widths)))
    print(line(cols))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(line(row))
    print(f"\nTotal contributed: {money(r0.total_contributed)}")
    print("* After tax = cash in hand after selling everything on the last day and settling all tax "
          "(Value/XIRR are before that final sale; tax paid along the way is already deducted).")

    out = write_report(md, s, results)
    print(f"Report: {out}")
    if not a.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
