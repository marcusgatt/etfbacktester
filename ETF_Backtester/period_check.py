#!/usr/bin/env python3
"""Compare the 8 ASX ETFs over any periods, using long-running stand-ins (converted
to AUD) for funds too young to cover a period.

  python3 period_check.py                                      # pre-AI-boom decade vs last 10 years
  python3 period_check.py --periods 2012-12-01:2022-11-30 2016-10-01:2026-10-16
  python3 period_check.py --incomes 25000 90000 --cgt old
  python3 period_check.py --tickers ASX:VGS ASX:VGE ASX:IEM ASX:NDIA ASX:VHY

Stand-ins track the same index: NDQ <- QQQ (Nasdaq-100), VGS <- URTH (MSCI World),
VEQ <- VGK (FTSE Developed Europe) — US-listed, converted with daily AUD/USD — and
A200 <- STW (SPDR ASX 200), VGE <- VWO (FTSE Emerging), IEM <- EEM (MSCI Emerging),
NDIA <- INDY (Nifty 50). They're only used for dates before the real fund's data
starts; the tracking check shows how close each stand-in has been since then.
Their fees differ a little (QQQ 0.20% vs NDQ 0.48% a year; VGK ~0.1% vs VEQ 0.35%).
"""

import argparse
import math
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare import fund_stats  # noqa: E402
from engine import MarketData, Settings, load_market_data, run_backtest  # noqa: E402
from strategies import MonthlyDCA  # noqa: E402
from tax import marginal_rate  # noqa: E402

TICKERS = ["ASX:VHY", "ASX:VAS", "ASX:A200", "ASX:IVV", "ASX:VTS", "ASX:NDQ", "ASX:VGS", "ASX:VEQ"]
STAND_INS = {"ASX:NDQ": "QQQ", "ASX:VGS": "URTH", "ASX:VEQ": "VGK", "ASX:A200": "STW.AX",
             "ASX:VGE": "VWO", "ASX:IEM": "EEM", "ASX:NDIA": "INDY"}


def in_aud(md: MarketData, fx: MarketData, as_symbol: str) -> MarketData:
    """Convert a USD fund to AUD with the AUD/USD close on (or before) each day."""
    if md.currency == "AUD":
        return replace(md, symbol=as_symbol)
    rate, j, out = {}, 0, ([], [], [], [])
    for d in md.dates:
        while j + 1 < len(fx.dates) and fx.dates[j + 1] <= d:
            j += 1
        if fx.dates[j] <= d:
            rate[d] = fx.closes[j]
    days = [d for d in md.dates if d in rate]
    idx = {d: i for i, d in enumerate(md.dates)}
    opens = [md.opens[idx[d]] / rate[d] for d in days]
    closes = [md.closes[idx[d]] / rate[d] for d in days]
    adj = [md.adj_closes[idx[d]] / rate[d] for d in days]
    divs = {d: a / rate[d] for d, a in md.dividends.items() if d in rate}
    return MarketData(as_symbol, "AUD", days, opens, closes, adj, divs, max(md.valid_from, days[0]))


def splice(real: MarketData, proxy: MarketData) -> MarketData:
    """Stand-in history before the real fund's reliable data, real fund from then on —
    the stand-in rescaled to the real fund's price on the first real day."""
    cut = real.valid_from
    k = proxy.closes[max(i for i, d in enumerate(proxy.dates) if d <= cut)]
    scale = real.closes[real.dates.index(min(d for d in real.dates if d >= cut))] / k
    pre = [i for i, d in enumerate(proxy.dates) if d < cut]
    post = [i for i, d in enumerate(real.dates) if d >= cut]
    adj_scale = real.adj_closes[post[0]] / (proxy.adj_closes[pre[-1]] if pre else 1)
    return MarketData(
        real.symbol, "AUD",
        [proxy.dates[i] for i in pre] + [real.dates[i] for i in post],
        [proxy.opens[i] * scale for i in pre] + [real.opens[i] for i in post],
        [proxy.closes[i] * scale for i in pre] + [real.closes[i] for i in post],
        [proxy.adj_closes[i] * adj_scale for i in pre] + [real.adj_closes[i] for i in post],
        {**{d: a * scale for d, a in proxy.dividends.items() if d < cut},
         **{d: a for d, a in real.dividends.items() if d >= cut}},
        proxy.valid_from)


def tracking(real: MarketData, proxy: MarketData) -> tuple[float, float, date]:
    """Annual total return of real fund vs stand-in over their overlap (real data period)."""
    start, end = real.valid_from, min(real.dates[-1], proxy.dates[-1])
    a, b = fund_stats(real, start, end), fund_stats(proxy, start, end)
    return a["total"], b["total"], start


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", nargs="+", default=TICKERS)
    p.add_argument("--periods", nargs="+", default=["2012-12-01:2022-11-30", "2016-10-01:2026-10-16"])
    p.add_argument("--incomes", nargs="+", type=float, default=[20_000, 90_000])
    p.add_argument("--cgt", choices=["new", "old"], default="new")
    p.add_argument("--contribution", type=float, default=1000.0)
    p.add_argument("--bank-rate", type=float, default=5.5)
    a = p.parse_args()

    fx = load_market_data("AUDUSD=X")
    real = {t: load_market_data(t) for t in a.tickers}
    data, notes = {}, []
    print("Stand-in tracking (annual total return in AUD, while both existed):")
    for t, md in real.items():
        if t in STAND_INS:
            proxy = in_aud(load_market_data(STAND_INS[t]), fx, md.symbol)
            r, s, since = tracking(md, proxy)
            print(f"  {t:<9} real {r:6.2%}  vs  {STAND_INS[t]:<6} in AUD {s:6.2%}   (since {since})")
            data[t] = splice(md, proxy)
            notes.append(f"{t} before {md.valid_from} = {STAND_INS[t]}")
        else:
            data[t] = md

    for raw in a.periods:
        start, end = (date.fromisoformat(x) for x in raw.split(":"))
        end = min([end] + [md.dates[-1] for md in data.values()])
        funds = {t: md for t, md in data.items() if md.valid_from <= start + timedelta(days=31)}
        stand = [t for t in funds if t in STAND_INS and real[t].valid_from > start + timedelta(days=31)]
        print(f"\n=== {start} to {end}" + (f"   (stand-in data used for: {', '.join(stand)})" if stand else ""))

        print(f"\n  {'Fund ($1 lump sum)':<14}{'Total return':>14}{'Price only':>12}{'Avg yield':>11}"
              f"{'Volatility':>12}{'Worst fall':>12}{'$1 grew to':>12}")
        for t, md in sorted(funds.items(), key=lambda kv: -fund_stats(kv[1], start, end)["total"]):
            f = fund_stats(md, start, end)
            print(f"  {t:<14}{f['total']:>13.2%}/y{f['price']:>10.2%}/y{f['yld']:>10.2%}"
                  f"{f['vol']:>12.1%}{f['worst']:>12.1%}{'$' + format(f['growth'], '.2f'):>12}")

        print(f"\n  You: ${a.contribution:,.0f}/month, dividends reinvested, all sold on {end} ({a.cgt} CGT rules)")
        print(f"  {'':<10}{'Before tax':>21}" + "".join(f"{'After tax @ $' + format(i, ',.0f'):>22}" for i in a.incomes))
        rows = []
        for t, md in funds.items():
            base = Settings(ticker=t, start=start, end=end, contribution=a.contribution,
                            bank_rate=a.bank_rate, cgt_regime=a.cgt, drp=True)
            pre = run_backtest(md, replace(base, tax=False), MonthlyDCA(reinvest_dividends=True))
            post = [run_backtest(md, replace(base, income=i), MonthlyDCA(reinvest_dividends=True)) for i in a.incomes]
            rows.append((t, pre, post))
        for t, pre, post in sorted(rows, key=lambda r: -r[2][0].after_tax_value):
            print(f"  {t:<10}{'$' + format(pre.after_tax_value, ',.0f'):>12}{pre.after_tax_xirr:>9.2%}"
                  + "".join(f"{'$' + format(r.after_tax_value, ',.0f'):>12}{r.after_tax_xirr:>9.2%}" for r in post))
        print(f"  (contributed ${rows[0][1].total_contributed:,.0f}; marginal rates "
              + ", ".join(f"${i:,.0f}: {marginal_rate(i):.0%}" for i in a.incomes) + ")")

    print("\nAUD prices throughout. Stand-ins: " + "; ".join(notes) + ".")


if __name__ == "__main__":
    main()
