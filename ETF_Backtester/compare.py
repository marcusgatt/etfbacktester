#!/usr/bin/env python3
"""Compare ETFs side by side over the same periods.

  python3 compare.py                                         # 8 ASX ETFs, default periods
  python3 compare.py --tickers ASX:VHY ASX:VAS ASX:A200 --periods 2015-01-01:2026-12-31 2020-01-01:2026-12-31

Every fund in a period starts on the same date. Funds without reliable data
(price AND dividends) from the period start are skipped, not given a later start.

Two views per fund:
  Fund       — $1 invested at the start: total return (dividends reinvested),
               price-only growth, average yield, volatility, worst fall. Before tax.
  You        — $1,000/month contributed (same rules as backtest.py), dividends
               reinvested (via the fund's DRP if it has one, otherwise with the next
               contribution), everything sold on the last day. Shown before tax and
               after Australian tax (dividends, franking, CGT on the final sale) at
               several income levels — see tax.py.

  python3 compare.py --incomes 25000 --cgt old       # your income, pre-2027 CGT rules
"""

import argparse
import math
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import Settings, load_market_data, run_backtest  # noqa: E402
from strategies import MonthlyDCA  # noqa: E402
from tax import fund_tax, marginal_rate  # noqa: E402


def fund_stats(md, start, end):
    """Lump-sum stats from actual closes + dividends (reinvested at the ex-date close)."""
    i0 = next(i for i, d in enumerate(md.dates) if d >= start)
    i1 = max(i for i, d in enumerate(md.dates) if d <= end)
    tr, peak, worst, rets, divs = 1.0, 1.0, 0.0, [], 0.0
    for i in range(i0 + 1, i1 + 1):
        div = md.dividends.get(md.dates[i], 0.0)
        r = (md.closes[i] + div) / md.closes[i - 1]
        tr *= r
        rets.append(math.log(r))
        peak = max(peak, tr)
        worst = min(worst, tr / peak - 1)
        divs += div / md.closes[i - 1]
    years = (md.dates[i1] - md.dates[i0]).days / 365.25
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1) * 252)
    return dict(
        first=md.dates[i0], last=md.dates[i1], years=years,
        total=tr ** (1 / years) - 1,
        price=(md.closes[i1] / md.closes[i0]) ** (1 / years) - 1,
        yld=divs / years, vol=vol, worst=worst, growth=tr,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", nargs="+", default=["ASX:VHY", "ASX:VAS", "ASX:A200", "ASX:IVV",
                                                  "ASX:VTS", "ASX:NDQ", "ASX:VGS", "ASX:VEQ"])
    p.add_argument("--periods", nargs="+",
                   default=["2013-01-01:2026-12-31", "2016-01-01:2026-12-31", "2018-06-01:2026-12-31"],
                   help="START:END pairs (YYYY-MM-DD:YYYY-MM-DD)")
    p.add_argument("--contribution", type=float, default=1000.0)
    p.add_argument("--bank-rate", type=float, default=5.5)
    p.add_argument("--fee", type=float, default=3.0)
    p.add_argument("--incomes", nargs="+", type=float, default=[20_000, 70_000, 150_000],
                   help="your other taxable income per year — one after-tax column each")
    p.add_argument("--cgt", choices=["new", "old"], default="new",
                   help="new = indexation + 30%% minimum (gains from 1 July 2027, default); old = 50%% discount")
    p.add_argument("--no-drp", action="store_true", help="reinvest with the next contribution even if a DRP exists")
    a = p.parse_args()

    data = {t: load_market_data(t) for t in a.tickers}
    for raw in a.periods:
        start, e = (date.fromisoformat(x) for x in raw.split(":"))
        end = min([e] + [md.dates[-1] for md in data.values()])
        funds = {t: md for t, md in data.items() if md.valid_from <= start + timedelta(days=31)}
        missing = [f"{t} (from {md.valid_from})" for t, md in data.items() if t not in funds]
        print(f"\n=== {start} to {end}")
        if missing:
            print(f"  skipped — no reliable data yet: {', '.join(missing)}")
        if not funds:
            continue

        print(f"\n  {'Fund ($1 lump sum)':<14}{'Total return':>14}{'Price only':>12}{'Avg yield':>11}"
              f"{'Volatility':>12}{'Worst fall':>12}{'$1 grew to':>12}")
        for t, md in funds.items():
            f = fund_stats(md, start, end)
            print(f"  {t:<14}{f['total']:>13.2%}/y{f['price']:>10.2%}/y{f['yld']:>10.2%}"
                  f"{f['vol']:>12.1%}{f['worst']:>12.1%}{'$' + format(f['growth'], '.2f'):>12}")

        rows = {}
        for t, md in funds.items():
            base = Settings(ticker=t, start=start, end=end, contribution=a.contribution,
                            bank_rate=a.bank_rate, fee=a.fee, cgt_regime=a.cgt, drp=not a.no_drp)
            pre = run_backtest(md, replace(base, tax=False), MonthlyDCA(reinvest_dividends=True))
            post = [run_backtest(md, replace(base, income=inc), MonthlyDCA(reinvest_dividends=True))
                    for inc in a.incomes]
            rows[t] = (md, pre, post)

        print(f"\n  You: ${a.contribution:,.0f}/month, dividends reinvested, all sold on {end} "
              f"({'new' if a.cgt == 'new' else 'old'} CGT rules)")
        head = f"  {'':<10}{'':>4}{'Contributed':>12}{'Before tax':>21}"
        for inc in a.incomes:
            head += f"{'After tax @ $' + format(inc, ',.0f'):>22}"
        print(head)
        print(f"  {'':<10}{'DRP':>4}{'':>12}" + f"{'value':>12}{'XIRR':>9}" * (1 + len(a.incomes)))
        for t, (md, pre, post) in rows.items():
            drp = "yes" if (not a.no_drp and fund_tax(md.symbol).drp) else "no"
            line = (f"  {t:<10}{drp:>4}{'$' + format(pre.total_contributed, ',.0f'):>12}"
                    f"{'$' + format(pre.after_tax_value, ',.0f'):>12}{pre.after_tax_xirr:>9.2%}")
            for r in post:
                line += f"{'$' + format(r.after_tax_value, ',.0f'):>12}{r.after_tax_xirr:>9.2%}"
            print(line)

        print("\n  Best after tax:  " + "   ".join(
            f"${inc:,.0f} income → {max(rows, key=lambda t: rows[t][2][k].after_tax_value)}"
            for k, inc in enumerate(a.incomes)))
        print(f"\n  {'Tax over the period':<14}" + "".join(
            f"{'@ $' + format(inc, ',.0f') + ' (' + format(marginal_rate(inc), '.0%') + ' marginal)':>34}"
            for inc in a.incomes))
        print(f"  {'':<14}" + f"{'net tax':>12}{'of which CGT':>14}{'franking':>10}" * len(a.incomes))
        for t, (md, pre, post) in rows.items():
            net = lambda x: ("refund $" if x < 0 else "$") + format(abs(x), ",.0f")
            print(f"  {t:<14}" + "".join(f"{net(r.tax_paid):>12}{'$' + format(r.cgt_paid, ',.0f'):>14}"
                                         f"{'$' + format(r.franking, ',.0f'):>10}" for r in post))
    print("\nFund fees (MER) are already inside these prices. Tax: 2026-27 rates for every year, Medicare levy "
          "and LITO included; franking/foreign credit ratios per fund are estimates in tax.py.")


if __name__ == "__main__":
    main()
