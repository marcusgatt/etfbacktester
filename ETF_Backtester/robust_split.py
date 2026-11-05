#!/usr/bin/env python3
"""Which VHY / VGS / IEM split holds up whichever market leads next? (minimax regret)

  python3 robust_split.py                    # 5-year windows, mid-2006 to now
  python3 robust_split.py --window 10 --risk-aversion 6
  python3 robust_split.py --no-show

For every rolling window (default 5 years, sliding a month at a time) each split's
certainty-equivalent return (CER) is compared with the split that turned out best IN
THAT WINDOW. The gap is the split's "regret". A robust split has small regret in all
windows, not just the recent US-led ones. Scores:
  mean regret  — average shortfall vs hindsight-best (% per year)
  worst regret — biggest shortfall in any window (minimax regret picks the smallest)

History goes back to mid-2006 (AUD/USD data limit) using stand-ins converted to AUD:
  Australia     VHY from Oct 2012; before that EWA (MSCI Australia)
  International VGS <- URTH (MSCI World) from 2012; before that 70% SPY + 30% EFA
  Emerging      IEM <- EEM (MSCI Emerging)
Student lens (franking refunded) by default, as in optimise_split.py.
"""

import argparse
import sys
from pathlib import Path

import matplotlib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import load_market_data  # noqa: E402
from optimise_split import load, monthly_returns  # noqa: E402
from period_check import in_aud  # noqa: E402
from tax import fund_tax  # noqa: E402

REPORTS = Path(__file__).resolve().parent / "reports"
FRANKING_BROAD_AU = 0.30     # franking per $ of dividend for the broad Australian market (EWA era)


def usd_fund_monthly(sym, fx, franking=0.0):
    return monthly_returns(in_aud(load_market_data(sym), fx, sym), franking)


def spliced(real_ticker, proxy):
    """Real (or already stand-in-spliced) monthly returns once reliable, proxy before."""
    md = load(real_ticker)
    real = monthly_returns(md, fund_tax(load_market_data(real_ticker).symbol).franking)
    first = (md.valid_from.year, md.valid_from.month)
    out = {m: v for m, v in proxy.items() if m <= first}
    out.update({m: v for m, v in real.items() if m > first})
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--window", type=int, default=5, help="window length in years (default 5)")
    p.add_argument("--risk-aversion", type=float, default=3.0)
    p.add_argument("--lens", choices=["student", "pre-tax"], default="student")
    p.add_argument("--step", type=int, default=2)
    p.add_argument("--no-show", action="store_true")
    a = p.parse_args()
    if a.no_show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm, colors

    fx = load_market_data("AUDUSD=X")
    spy, efa = usd_fund_monthly("SPY", fx), usd_fund_monthly("EFA", fx)
    dev = {m: tuple(0.7 * spy[m][k] + 0.3 * efa[m][k] for k in (0, 1)) for m in spy if m in efa}
    series = [spliced("ASX:VHY", usd_fund_monthly("EWA", fx, FRANKING_BROAD_AU)),
              spliced("ASX:VGS", dev),
              spliced("ASX:IEM", usd_fund_monthly("EEM", fx))]
    months = sorted(set.intersection(*(set(s) for s in series)))
    months = [m for m in months if m >= (2006, 7)]
    k = 1 if a.lens == "student" else 0
    R = np.array([[s[m][k] for s in series] for m in months])            # months x 3
    W = np.array([(w1, w2, 100 - w1 - w2) for w1 in range(0, 101, a.step)
                  for w2 in range(0, 101 - w1, a.step)], dtype=float) / 100
    P = W @ R.T                                                           # splits x months

    n = 12 * a.window
    logs = np.log1p(P)
    c = np.concatenate([np.zeros((len(W), 1)), np.cumsum(logs, axis=1)], axis=1)
    c1 = np.concatenate([np.zeros((len(W), 1)), np.cumsum(P, axis=1)], axis=1)
    c2 = np.concatenate([np.zeros((len(W), 1)), np.cumsum(P ** 2, axis=1)], axis=1)
    starts = np.arange(0, P.shape[1] - n + 1)
    cagr = np.exp((c[:, starts + n] - c[:, starts]) * 12 / n) - 1        # splits x windows
    mean = (c1[:, starts + n] - c1[:, starts]) / n
    var = ((c2[:, starts + n] - c2[:, starts]) - n * mean ** 2) / (n - 1)
    vol = np.sqrt(var * 12)
    cer = cagr - (a.risk_aversion - 1) / 2 * vol ** 2
    regret = cer.max(axis=0) - cer                                        # splits x windows
    mean_regret, worst_regret = regret.mean(axis=1), regret.max(axis=1)

    span = f"{months[0][0]}-{months[0][1]:02d} to {months[-1][0]}-{months[-1][1]:02d}"
    print(f"{len(months)} months ({span}), {len(starts)} rolling {a.window}-year windows, "
          f"{len(W)} splits, λ = {a.risk_aversion:g}, {a.lens} lens\n")
    winners = {}
    for j in range(len(starts)):
        w = tuple(int(round(x * 100)) for x in W[cer[:, j].argmax()])
        winners[w] = winners.get(w, 0) + 1
    print("Hindsight-best split per window (VHY/VGS/IEM: number of windows it won):")
    print("  " + ", ".join(f"{'/'.join(map(str, w))}: {n_}" for w, n_ in
                           sorted(winners.items(), key=lambda kv: -kv[1])))

    def row(label, i):
        w = "/".join(str(int(round(x * 100))) for x in W[i])
        print(f"  {label:<28}{w:>10}   mean regret {mean_regret[i]:6.2%}   worst regret {worst_regret[i]:6.2%}   "
              f"full-period CER {cer_full[i]:6.2%}")

    full_cagr = np.exp(c[:, -1] * 12 / P.shape[1]) - 1
    cer_full = full_cagr - (a.risk_aversion - 1) / 2 * (P.std(axis=1, ddof=1) * np.sqrt(12)) ** 2
    find = lambda t: int(np.flatnonzero((np.round(W * 100) == t).all(axis=1))[0])
    print("\nRegret = how far a split's CER fell short of that window's best split (% per year).")
    row("Lowest mean regret", int(mean_regret.argmin()))
    row("Lowest worst regret (minimax)", int(worst_regret.argmin()))
    row("Best full-period CER", int(cer_full.argmax()))
    for t in ((0, 100, 0), (24, 66, 10), (20, 70, 10), (30, 60, 10), (40, 50, 10), (2, 88, 10), (100, 0, 0), (0, 0, 100)):
        row("/".join(map(str, t)), find(t))

    # plots: mean and worst regret as 3D bars (lower = greener)
    REPORTS.mkdir(exist_ok=True)
    w1s, w2s = W[:, 0] * 100, W[:, 1] * 100
    fig = plt.figure(figsize=(17, 8))
    pct = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.1%}")
    for i, (vals, title, best) in enumerate(((mean_regret, "Mean regret", mean_regret.argmin()),
                                             (worst_regret, "Worst regret (minimax)", worst_regret.argmin())), 1):
        ax = fig.add_subplot(1, 2, i, projection="3d")
        norm = colors.Normalize(vals.min(), vals.max())
        ax.bar3d(w1s, w2s, np.zeros_like(vals), a.step * 0.95, a.step * 0.95, vals,
                 color=cm.RdYlGn_r(norm(vals)), shade=True, linewidth=0)
        b = "/".join(str(int(round(x * 100))) for x in W[best])
        ax.text(w1s[best] + 1, w2s[best] + 1, vals[best] + 0.004, f"◆ best {b}", fontsize=9, weight="bold")
        ax.set_xlabel("VHY %"); ax.set_ylabel("VGS %"); ax.set_zlabel(f"{title} (lower = better)")
        ax.zaxis.set_major_formatter(pct)
        ax.view_init(elev=38, azim=40)        # look from the high-VGS side so the low (green) valley is visible
        ax.set_title(f"{title}: shortfall vs each window's best split")
        fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cm.RdYlGn_r), ax=ax, shrink=0.5, pad=0.1, format=pct)
    fig.suptitle(f"VHY / VGS / IEM (IEM = rest) — {len(starts)} rolling {a.window}-year windows, {span}, "
                 f"λ = {a.risk_aversion:g}, {a.lens} lens", fontsize=13)
    out = REPORTS / f"split3d_regret_{a.window}y_{a.lens}_lambda{a.risk_aversion:g}.png"
    fig.savefig(out, dpi=120, bbox_inches="tight")
    print(f"\nSaved {out}")
    if not a.no_show:
        plt.show()


if __name__ == "__main__":
    main()
