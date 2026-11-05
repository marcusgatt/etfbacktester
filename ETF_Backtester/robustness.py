#!/usr/bin/env python3
"""Is any strategy robustly better? Run every strategy on every ETF over many
overlapping windows and measure how often — and by how much — it beats simply
investing everything as it arrives ("Invest every contribution + dividends").

  python3 robustness.py                       # defaults below
  python3 robustness.py --years 3 --step 3    # 3-year windows starting every 3 months
  python3 robustness.py --bank-rates 5.5      # one bank-rate scenario only

Each window: $1,000/month (from strategies' usual settings), $3 fee. Windows only
start once a fund has reliable price + dividend data. XIRRs are after tax (on
$40,000 other income, new CGT rules), selling everything at the end of each window.
"""

import argparse
import html
import json
import statistics as stats
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import Settings, load_market_data, run_backtest  # noqa: E402
from strategies import STRATEGIES  # noqa: E402

TICKERS = ["ASX:VAS", "ASX:VHY", "ASX:A200", "ASX:IVV", "ASX:VTS", "ASX:NDQ", "ASX:VGS", "ASX:VEQ"]
BENCHMARK = "Invest every contribution + dividends"


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def windows(md, years: int, step: int):
    """Window starts on the 1st of a month, every `step` months, ending by the last price."""
    s = add_months(md.valid_from, 1) if md.valid_from.day != 1 else md.valid_from
    s = date(s.year, s.month, 1)
    out = []
    while (e := add_months(s, 12 * years)) <= md.dates[-1]:
        out.append((s, e))
        s = add_months(s, step)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", nargs="+", default=TICKERS)
    p.add_argument("--years", type=int, default=5, help="window length in years (default 5)")
    p.add_argument("--step", type=int, default=6, help="months between window starts (default 6)")
    p.add_argument("--bank-rates", nargs="+", type=float, default=[5.5, 3.0],
                   help="bank-rate scenarios, %% p.a. (default 5.5 and 3.0)")
    p.add_argument("--contribution", type=float, default=1000.0)
    p.add_argument("--fee", type=float, default=3.0)
    a = p.parse_args()

    names = [s.name for s in STRATEGIES]
    if BENCHMARK not in names:
        raise SystemExit(f'strategies.py must include the benchmark "{BENCHMARK}".')
    data = {t: load_market_data(t) for t in a.tickers}
    t0 = time.time()
    scenarios = []
    for rate in a.bank_rates:
        rows = []   # one per (ticker, window, strategy)
        for t, md in data.items():
            for s, e in windows(md, a.years, a.step):
                base = Settings(ticker=t, start=s, end=e, contribution=a.contribution, bank_rate=rate, fee=a.fee)
                res = {st.name: run_backtest(md, base, st) for st in STRATEGIES}
                bx = res[BENCHMARK].after_tax_xirr
                for n, r in res.items():
                    rows.append(dict(ticker=t, start=s.isoformat(), end=e.isoformat(), strategy=n,
                                     xirr=r.after_tax_xirr, excess=r.after_tax_xirr - bx, dd=r.max_drawdown,
                                     cash=r.avg_cash_pct, trades=len(r.trades)))
        scenarios.append(dict(rate=rate, rows=rows))
        print(f"bank {rate}%: {len(rows) // len(names)} windows x {len(names)} strategies "
              f"({time.time() - t0:.0f}s)")

    for sc in scenarios:
        summarise(sc, names, data, a)
    out = write_html(scenarios, names, list(data), a)
    print(f"\nReport: {out}")


def summary_rows(rows, names):
    out = []
    for n in names:
        rs = [r for r in rows if r["strategy"] == n]
        ex = [r["excess"] for r in rs]
        out.append(dict(name=n, median_xirr=stats.median(r["xirr"] for r in rs),
                        median_excess=stats.median(ex), mean_excess=stats.fmean(ex),
                        win=sum(x > 0.0005 for x in ex) / len(ex), worst=min(ex), best=max(ex),
                        dd=stats.fmean(r["dd"] for r in rs), n=len(rs)))
    return out


def summarise(sc, names, data, a):
    rows = sc["rows"]
    print(f"\n=== Bank at {sc['rate']}% — {a.years}-year windows, every {a.step} months, all funds "
          f"(excess = XIRR minus '{BENCHMARK}')\n")
    print(f"  {'Strategy':<46}{'Median XIRR':>12}{'Median excess':>15}{'Beats bench':>13}"
          f"{'Worst':>9}{'Best':>9}{'Avg drawdown':>14}")
    for s in summary_rows(rows, names):
        bench = s["name"] == BENCHMARK
        print(f"  {s['name'][:45]:<46}{s['median_xirr']:>11.2%} "
              f"{'—' if bench else format(s['median_excess'] * 100, '+.2f') + ' pts':>14}"
              f"{'—' if bench else format(s['win'], '.0%'):>13}"
              f"{'—' if bench else format(s['worst'] * 100, '+.1f'):>9}"
              f"{'—' if bench else format(s['best'] * 100, '+.1f'):>9}{s['dd']:>13.1%}")
    print(f"\n  Median excess by fund (pts/yr):")
    ts = list(data)
    print(f"  {'':<46}" + "".join(f"{t.split(':')[-1]:>7}" for t in ts))
    for n in names:
        if n == BENCHMARK:
            continue
        cells = []
        for t in ts:
            ex = [r["excess"] for r in rows if r["strategy"] == n and r["ticker"] == t]
            cells.append(f"{stats.median(ex) * 100:>+7.2f}" if ex else f"{'':>7}")
        print(f"  {n[:45]:<46}" + "".join(cells))


def write_html(scenarios, names, tickers, a) -> Path:
    payload = {
        "names": names, "tickers": tickers, "bench": BENCHMARK, "years": a.years, "step": a.step,
        "scenarios": [{"rate": sc["rate"], "summary": summary_rows(sc["rows"], names), "rows": sc["rows"]}
                      for sc in scenarios],
    }
    (HERE / "reports").mkdir(exist_ok=True)
    out = HERE / "reports" / "robustness.html"
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(payload).replace("</", "<\\/")), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strategy robustness</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --pos: #2a78d6; --neg: #e34948; --mid: #f0efec;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --pos: #3987e5; --neg: #e66767; --mid: #383835;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 28px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; font-weight: 650; }
h2 { font-size: 16px; margin: 0; font-weight: 600; }
.sub { color: var(--ink-2); margin: 0 0 20px; max-width: 820px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 20px; }
.card-head { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.hint { color: var(--muted); font-size: 12.5px; margin: 8px 0 0; }
select { font: inherit; color: var(--ink); background: var(--surface); border: 1px solid var(--axis); border-radius: 6px; padding: 4px 8px; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 8px 10px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--grid); }
th { color: var(--ink-2); font-weight: 600; font-size: 12.5px; }
th:first-child, td:first-child { text-align: left; white-space: normal; min-width: 220px; }
td.cell { border: 2px solid var(--surface); border-radius: 6px; text-align: center; min-width: 72px; }
td.cell small { display: block; color: var(--ink-2); font-size: 11px; }
tr.bench td { color: var(--muted); }
.legend { display: flex; align-items: center; gap: 8px; color: var(--ink-2); font-size: 12.5px; margin-top: 10px; flex-wrap: wrap; }
.legend .ramp { display: inline-flex; }
.legend .ramp i { width: 22px; height: 12px; display: inline-block; }
</style>
</head>
<body>
<main>
  <h1>Is any strategy robustly better?</h1>
  <p class="sub" id="sub"></p>
  <div class="card-head" style="margin-bottom:16px">
    <span></span>
    <label>Bank interest on uninvested cash <select id="rate"></select></label>
  </div>
  <section class="card">
    <div class="card-head"><h2>All funds, all windows</h2></div>
    <div class="table-wrap"><table id="summary"></table></div>
    <p class="hint">Excess = the strategy's annualised return (XIRR) minus the benchmark's in the same fund and window.
      "Beats benchmark" counts windows where it was ahead by more than 0.05 pts/yr. Windows overlap, so they aren't independent tests.</p>
  </section>
  <section class="card">
    <div class="card-head"><h2>Median excess by fund</h2></div>
    <div class="table-wrap"><table id="grid"></table></div>
    <div class="legend"><span>Behind benchmark</span><span class="ramp" id="ramp"></span><span>Ahead</span>
      <span style="margin-left:12px">Small number = share of windows ahead</span></div>
  </section>
</main>
<script>
const D = __DATA__;
const pct = (v, dp = 2) => (v * 100).toFixed(dp) + "%";
const pts = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v * 100).toFixed(2);
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const LIM = 0.015;  // colour saturates at ±1.5 pts/yr
const fill = v => {
  const k = Math.min(1, Math.abs(v) / LIM) * 60;
  return `color-mix(in srgb, var(${v >= 0 ? "--pos" : "--neg"}) ${k.toFixed(0)}%, var(--mid))`;
};
document.getElementById("ramp").innerHTML = [-1, -0.6, -0.25, 0, 0.25, 0.6, 1].map(f => `<i style="background:${fill(f * LIM)}"></i>`).join("");
const sel = document.getElementById("rate");
sel.innerHTML = D.scenarios.map((s, i) => `<option value="${i}">${s.rate}% p.a.</option>`).join("");
function render() {
  const sc = D.scenarios[+sel.value];
  const nWin = sc.rows.length / D.names.length;
  document.getElementById("sub").textContent =
    `Every strategy run on ${D.tickers.length} ETFs over ${nWin} rolling ${D.years}-year windows (a new window every ${D.step} months), ` +
    `$1,000/month, $3 per trade. Benchmark: “${D.bench}”.`;
  let h = `<thead><tr><th>Strategy</th><th>Median return</th><th>Median excess</th><th>Beats benchmark</th><th>Worst</th><th>Best</th><th>Avg worst drawdown</th></tr></thead><tbody>`;
  const sorted = [...sc.summary].sort((a, b) => (b.name === D.bench) - (a.name === D.bench) || b.median_excess - a.median_excess);
  for (const s of sorted) {
    const b = s.name === D.bench;
    h += `<tr class="${b ? "bench" : ""}"><td><b>${esc(s.name)}</b>${b ? " (benchmark)" : ""}</td><td>${pct(s.median_xirr)}</td>` +
         `<td>${b ? "—" : pts(s.median_excess) + " pts"}</td><td>${b ? "—" : pct(s.win, 0)}</td>` +
         `<td>${b ? "—" : pts(s.worst)}</td><td>${b ? "—" : pts(s.best)}</td><td>${pct(s.dd, 1)}</td></tr>`;
  }
  document.getElementById("summary").innerHTML = h + "</tbody>";
  let g = `<thead><tr><th>Strategy</th>${D.tickers.map(t => `<th style="text-align:center">${esc(t.split(":").pop())}</th>`).join("")}</tr></thead><tbody>`;
  for (const s of sorted) {
    if (s.name === D.bench) continue;
    g += `<tr><td>${esc(s.name)}</td>`;
    for (const t of D.tickers) {
      const ex = sc.rows.filter(r => r.strategy === s.name && r.ticker === t).map(r => r.excess).sort((a, b) => a - b);
      if (!ex.length) { g += `<td class="cell">–</td>`; continue; }
      const med = ex.length % 2 ? ex[(ex.length - 1) / 2] : (ex[ex.length / 2 - 1] + ex[ex.length / 2]) / 2;
      const win = ex.filter(x => x > 0.0005).length / ex.length;
      g += `<td class="cell" style="background:${fill(med)}" title="${ex.length} windows">${pts(med)}<small>${pct(win, 0)}</small></td>`;
    }
    g += "</tr>";
  }
  document.getElementById("grid").innerHTML = g + "</tbody>";
}
sel.addEventListener("change", render);
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
