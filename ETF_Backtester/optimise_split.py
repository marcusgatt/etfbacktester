#!/usr/bin/env python3
"""Three-layer portfolio (Australia / international / emerging): compare splits,
search for the best growth-vs-volatility balance, and test whether it holds up.

  python3 optimise_split.py
  python3 optimise_split.py --funds ASX:VHY ASX:IVV ASX:IEM
  python3 optimise_split.py --funds ASX:A200 ASX:VGS ASX:IEM --step 10

Works on monthly total returns (dividends reinvested) in AUD, rebalanced back to
the target weights every month — roughly what topping up the lagging fund with each
contribution does. Before tax, plus a "student lens": franking credits added as
cash, which is what a low-income investor gets back as a refund.
Stand-ins (see period_check.py) cover dates before a fund's own data.
"""

import argparse
import itertools
import math
import statistics as st
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import load_market_data  # noqa: E402
from period_check import STAND_INS, in_aud, splice  # noqa: E402
from tax import fund_tax  # noqa: E402

CASH_RATE = 0.02     # average Australian cash rate 2013-2026, for the return-per-risk score


def monthly_returns(md, franking: float):
    """{(year, month): (total return, total return + franking credits)} from month-end closes."""
    out, prev, divs, credit = {}, None, 0.0, 0.0
    for i, d in enumerate(md.dates):
        if d in md.dividends:
            divs += md.dividends[d]
        last_of_month = i + 1 == len(md.dates) or md.dates[i + 1].month != d.month
        if not last_of_month:
            continue
        if prev is not None:
            r = (md.closes[i] + divs) / prev - 1
            out[(d.year, d.month)] = (r, r + divs * franking / prev)
        prev, divs = md.closes[i], 0.0
    return out


def load(ticker):
    md = load_market_data(ticker)
    if ticker in STAND_INS:
        fx = load_market_data("AUDUSD=X")
        md = splice(md, in_aud(load_market_data(STAND_INS[ticker]), fx, md.symbol))
    return md


def stats(rets):
    """CAGR, annualised volatility, worst peak-to-trough fall, return-per-risk score."""
    growth, peak, worst = 1.0, 1.0, 0.0
    for r in rets:
        growth *= 1 + r
        peak = max(peak, growth)
        worst = min(worst, growth / peak - 1)
    cagr = growth ** (12 / len(rets)) - 1
    vol = st.stdev(rets) * math.sqrt(12)
    return dict(cagr=cagr, vol=vol, worst=worst, score=(cagr - CASH_RATE) / vol)


def portfolio(series, weights, months, lens):
    k = 1 if lens == "student" else 0
    return [sum(w * series[f][m][k] for f, w in zip(series, weights)) for m in months]


def months_between(series, start, end):
    common = set.intersection(*(set(s) for s in series.values()))
    return sorted(m for m in common if (start.year, start.month) < m <= (end.year, end.month))


def fmt(name, s):
    return (f"  {name:<34}{s['cagr']:>8.2%}{s['vol']:>9.1%}{s['worst']:>9.1%}{s['score']:>8.2f}")


HEAD = f"  {'':<34}{'Return':>8}{'Volatil.':>9}{'Worst':>9}{'Score':>8}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--funds", nargs=3, default=["ASX:VHY", "ASX:VGS", "ASX:IEM"],
                   help="Australian, international and emerging-market fund")
    p.add_argument("--start", type=date.fromisoformat, default=date(2012, 12, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 16))
    p.add_argument("--split", type=date.fromisoformat, default=date(2019, 12, 31),
                   help="optimise up to this date, then test after it")
    p.add_argument("--step", type=int, default=5, help="weight grid step, %%")
    a = p.parse_args()

    series = {f: monthly_returns(load(f), fund_tax(load_market_data(f).symbol).franking) for f in a.funds}
    names = [f.split(":")[-1] for f in a.funds]
    all_m = months_between(series, a.start, a.end)
    print(f"Monthly data {all_m[0][0]}-{all_m[0][1]:02d} to {all_m[-1][0]}-{all_m[-1][1]:02d} "
          f"({len(all_m)} months). Score = (return - {CASH_RATE:.0%} cash) / volatility: higher is better.")

    print("\nCorrelation of monthly returns (1 = move together, 0 = unrelated):")
    for x, y in itertools.combinations(range(3), 2):
        xs = [series[a.funds[x]][m][0] for m in all_m]
        ys = [series[a.funds[y]][m][0] for m in all_m]
        print(f"  {names[x]}-{names[y]}: {st.correlation(xs, ys):.2f}")

    grid = [(w1, w2, 100 - w1 - w2) for w1 in range(0, 101, a.step) for w2 in range(0, 101 - w1, a.step)]
    candidates = {
        f"100% {names[1]}": (0, 100, 0),
        "Global market weights (2/88/10)": (2, 88, 10),
        "20 / 70 / 10": (20, 70, 10),
        "30 / 60 / 10": (30, 60, 10),
        "DHHF-like (37 / 56 / 7)": (37, 56, 7),
        "40 / 50 / 10": (40, 50, 10),
        "50 / 50 / 0": (50, 50, 0),
        f"100% {names[0]}": (100, 0, 0),
    }

    for lens in ("pre-tax", "student"):
        print(f"\n=== {a.start} to {a.end}, {lens}" + (" (franking credits refunded as cash)" if lens == "student" else "")
              + f" — split = {names[0]} / {names[1]} / {names[2]} %")
        print(HEAD)
        for label, w in candidates.items():
            print(fmt(label, stats(portfolio(series, [x / 100 for x in w], all_m, lens))))
        res = {w: stats(portfolio(series, [x / 100 for x in w], all_m, lens)) for w in grid}
        best = max(res, key=lambda w: res[w]["score"])
        minvol = min(res, key=lambda w: res[w]["vol"])
        print(fmt(f"Best score ({'/'.join(map(str, best))})", res[best]))
        print(fmt(f"Lowest volatility ({'/'.join(map(str, minvol))})", res[minvol]))

    # all-in-one funds over their own lifetimes, against the same splits
    for fund in ("ASX:VDHG", "ASX:DHHF"):
        md = load_market_data(fund)
        own = monthly_returns(md, fund_tax(md.symbol).franking)
        months = sorted(m for m in own if m in set(all_m))
        print(f"\n=== {fund} vs splits, {months[0][0]}-{months[0][1]:02d} to {months[-1][0]}-{months[-1][1]:02d}")
        for lens, k in (("pre-tax", 0), ("student", 1)):
            print(f"  [{lens}]" + HEAD[len(f'  [{lens}]'):])
            print(fmt(fund, stats([own[m][k] for m in months])))
            for label in ("30 / 60 / 10", "DHHF-like (37 / 56 / 7)", "40 / 50 / 10"):
                print(fmt(label, stats(portfolio(series, [x / 100 for x in candidates[label]], months, lens))))

    # does an optimised split hold up out of sample?
    train = [m for m in all_m if m <= (a.split.year, a.split.month)]
    test = [m for m in all_m if m > (a.split.year, a.split.month)]
    print(f"\n=== Out-of-sample check: optimise on {train[0][0]}-{train[-1][0]}, then hold it {test[0][0]}-{test[-1][0]}")
    for lens in ("pre-tax", "student"):
        fit = {w: stats(portfolio(series, [x / 100 for x in w], train, lens)) for w in grid}
        best = max(fit, key=lambda w: fit[w]["score"])
        after = {w: stats(portfolio(series, [x / 100 for x in w], test, lens)) for w in grid}
        rank = sorted(after, key=lambda w: -after[w]["score"]).index(best) + 1
        hindsight = max(after, key=lambda w: after[w]["score"])
        print(f"  [{lens}]" + HEAD[len(f'  [{lens}]'):])
        print(fmt(f"Picked on {train[0][0]}-{train[-1][0]} ({'/'.join(map(str, best))})", after[best])
              + f"   ← ranked {rank} of {len(grid)} afterwards")
        print(fmt(f"Best in hindsight ({'/'.join(map(str, hindsight))})", after[hindsight]))
        print(fmt("Simple 30 / 60 / 10", after[(30, 60, 10)]))


if __name__ == "__main__":
    main()
