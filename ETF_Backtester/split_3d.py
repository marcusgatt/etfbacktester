#!/usr/bin/env python3
"""3D maps of every Australia / international / emerging split (default VHY / VGS / IEM)
in 2% steps, coloured red (worse) to green (better).

  python3 split_3d.py                          # opens rotatable windows + saves PNGs in reports/
  python3 split_3d.py --risk-aversion 6        # care more about volatility
  python3 split_3d.py --lens pre-tax --no-show
  python3 split_3d.py --funds ASX:A200 ASX:IVV ASX:IEM

Main score = certainty-equivalent return (CER): CAGR - (lambda - 1)/2 x volatility^2,
i.e. the guaranteed yearly return you'd rate as equally good. lambda ~3 is a typical
investor; higher lambda punishes volatility more. Same monthly data, rebalancing and
"student lens" (franking refunded) as optimise_split.py.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import matplotlib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import load_market_data  # noqa: E402
from optimise_split import CASH_RATE, load, monthly_returns, months_between, portfolio, stats  # noqa: E402
from tax import fund_tax  # noqa: E402

REPORTS = Path(__file__).resolve().parent / "reports"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--funds", nargs=3, default=["ASX:VHY", "ASX:VGS", "ASX:IEM"])
    p.add_argument("--start", type=date.fromisoformat, default=date(2012, 12, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 22))
    p.add_argument("--step", type=int, default=2)
    p.add_argument("--risk-aversion", type=float, default=3.0, help="lambda in the CER score (default 3)")
    p.add_argument("--lens", choices=["student", "pre-tax"], default="student")
    p.add_argument("--no-show", action="store_true", help="only save PNGs")
    p.add_argument("--mark", nargs="*", default=["26/68/6"],
                   help="splits to label on the charts, e.g. --mark 26/68/6 30/64/6 (must be on the grid)")
    a = p.parse_args()
    if a.no_show:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm, colors

    series = {f: monthly_returns(load(f), fund_tax(load_market_data(f).symbol).franking) for f in a.funds}
    n1, n2, n3 = (f.split(":")[-1] for f in a.funds)
    months = months_between(series, a.start, a.end)
    span = f"{months[0][0]}-{months[0][1]:02d} to {months[-1][0]}-{months[-1][1]:02d}"
    lam = a.risk_aversion

    rows = []
    for w1 in range(0, 101, a.step):
        for w2 in range(0, 101 - w1, a.step):
            s = stats(portfolio(series, [w1 / 100, w2 / 100, (100 - w1 - w2) / 100], months, a.lens))
            s["cer"] = s["cagr"] - (lam - 1) / 2 * s["vol"] ** 2
            rows.append((w1, w2, 100 - w1 - w2, s))
    w1s = np.array([r[0] for r in rows]); w2s = np.array([r[1] for r in rows]); w3s = np.array([r[2] for r in rows])
    metric = {k: np.array([r[3][k] for r in rows]) for k in ("cer", "cagr", "vol", "worst", "score")}
    best = int(np.argmax(metric["cer"]))
    print(f"{len(rows)} splits, {span}, {a.lens} lens, lambda = {lam:g}")
    for label, i in (("Best CER", best), ("Best Sharpe", int(np.argmax(metric["score"]))),
                     ("Lowest volatility", int(np.argmin(metric["vol"])))):
        r = rows[i]
        print(f"  {label:<18} {n1} {r[0]:>3}% / {n2} {r[1]:>3}% / {n3} {r[2]:>3}%  "
              f"CER {r[3]['cer']:.2%}  return {r[3]['cagr']:.2%}  vol {r[3]['vol']:.1%}  "
              f"worst {r[3]['worst']:.1%}  Sharpe {r[3]['score']:.2f}")
    marks = []
    for m in a.mark:
        t = tuple(int(x) for x in m.split("/"))
        i = next((i for i, r in enumerate(rows) if r[:3] == t), None)
        if i is None:
            raise SystemExit(f"--mark {m}: not on the {a.step}% grid (the three numbers must add to 100)")
        marks.append(i)
        r = rows[i]
        print(f"  Marked {m:<11} CER {r[3]['cer']:.2%}  return {r[3]['cagr']:.2%}  vol {r[3]['vol']:.1%}  "
              f"worst {r[3]['worst']:.1%}  Sharpe {r[3]['score']:.2f}  "
              f"(CER rank {int((metric['cer'] > metric['cer'][i]).sum()) + 1} of {len(rows)})")
    REPORTS.mkdir(exist_ok=True)
    tag = f"{n1}_{n2}_{n3}_{a.lens}_lambda{lam:g}"

    def bars(ax, values, label, fmt, higher_is_better=True):
        """One 2x2-wide column per split: x = fund 1 %, y = fund 2 %, fund 3 = the rest."""
        norm = colors.Normalize(values.min(), values.max())
        cmap = cm.RdYlGn if higher_is_better else cm.RdYlGn_r
        base = values.min() - 0.05 * (values.max() - values.min())
        ax.bar3d(w1s, w2s, np.full_like(values, base), a.step * 0.95, a.step * 0.95, values - base,
                 color=cmap(norm(values)), shade=True, linewidth=0)
        ax.set_xlabel(f"{n1} %"); ax.set_ylabel(f"{n2} %"); ax.set_zlabel(label)
        ax.set_xlim(0, 100 + a.step); ax.set_ylim(0, 100 + a.step)
        ax.zaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(fmt))
        ax.view_init(elev=28, azim=-130)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        return sm

    pct = lambda v, _: f"{v:.1%}"
    # 1. main figure: CER
    fig = plt.figure(figsize=(12, 9))
    ax = fig.add_subplot(projection="3d")
    sm = bars(ax, metric["cer"], "Certainty-equivalent return", pct)
    for i, txt in [(best, "best ")] + [(m, "") for m in marks]:
        ax.text(w1s[i] + 1, w2s[i] + 1, metric["cer"][i] + 0.002, f"◆ {txt}{rows[i][0]}/{rows[i][1]}/{rows[i][2]}",
                fontsize=9, weight="bold")
    fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.08, format=matplotlib.ticker.FuncFormatter(pct), label="CER per year")
    ax.set_title(f"Every {n1} / {n2} / {n3} split ({n3} = 100 − {n1} − {n2}), {a.step}% steps\n"
                 f"Height & colour = CER = return − {(lam - 1) / 2:g} × volatility²   (λ = {lam:g}, {a.lens} lens, {span})")
    f1 = REPORTS / f"split3d_cer_{tag}.png"
    fig.savefig(f1, dpi=130, bbox_inches="tight")

    # 2. the ingredients: return, volatility, worst fall, Sharpe
    fig2 = plt.figure(figsize=(16, 12))
    panels = [("cagr", "Return per year", pct, True), ("vol", "Volatility (lower = greener)", pct, False),
              ("worst", "Worst fall (month-end)", pct, True),
              ("score", f"Sharpe = (return − {CASH_RATE:.0%}) / volatility", lambda v, _: f"{v:.2f}", True)]
    for k, (key, label, fmt, hib) in enumerate(panels, 1):
        axk = fig2.add_subplot(2, 2, k, projection="3d")
        smk = bars(axk, metric[key], label, fmt, hib)
        axk.set_title(label)
        fig2.colorbar(smk, ax=axk, shrink=0.5, pad=0.1, format=matplotlib.ticker.FuncFormatter(fmt))
    fig2.suptitle(f"{n1} / {n2} / {n3} splits — the ingredients ({a.lens} lens, {span})", fontsize=14)
    f2 = REPORTS / f"split3d_parts_{tag}.png"
    fig2.savefig(f2, dpi=110, bbox_inches="tight")

    # 3. the true 3-axis view: every split is a point on the plane VHY + VGS + IEM = 100
    fig3 = plt.figure(figsize=(11, 9))
    ax3 = fig3.add_subplot(projection="3d", computed_zorder=False)   # draw in zorder, so marks stay on top
    norm = colors.Normalize(metric["cer"].min(), metric["cer"].max())
    ax3.scatter(w1s, w2s, w3s, c=cm.RdYlGn(norm(metric["cer"])), s=28, marker="s", depthshade=False)
    def label(i, title, colour, lift, ha):
        """Leader line up from split i to a box spelling out its numbers."""
        r = rows[i]
        ax3.plot([w1s[i], w1s[i]], [w2s[i], w2s[i]], [w3s[i], w3s[i] + lift - 1], color=colour, lw=1.5, zorder=10)
        ax3.text(w1s[i], w2s[i], w3s[i] + lift,
                 f"{title} {r[0]}/{r[1]}/{r[2]}\nCER {r[3]['cer']:.1%} · return {r[3]['cagr']:.1%} · vol {r[3]['vol']:.1%}",
                 fontsize=9, weight="bold", color=colour, ha=ha, zorder=11,
                 bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=colour, alpha=0.85))

    ax3.scatter([w1s[best]], [w2s[best]], [w3s[best]], s=200, facecolors="none", edgecolors="black",
                linewidths=2, zorder=10)
    label(best, "◯ best", "black", 52, "right")
    for i in marks:
        ax3.scatter([w1s[i]], [w2s[i]], [w3s[i]], s=130, marker="*", color="magenta", edgecolors="black",
                    linewidths=1.2, depthshade=False, zorder=10)
        if i != best:
            label(i, "★", "navy", 18, "center")
    ax3.set_xlabel(f"{n1} %"); ax3.set_ylabel(f"{n2} %"); ax3.set_zlabel(f"{n3} %")
    ax3.set_xlim(0, 100); ax3.set_ylim(0, 100); ax3.set_zlim(0, 100)
    ax3.view_init(elev=22, azim=45)
    fig3.colorbar(cm.ScalarMappable(norm=norm, cmap=cm.RdYlGn), ax=ax3, shrink=0.55, pad=0.1,
                  format=matplotlib.ticker.FuncFormatter(pct), label="CER per year")
    ax3.set_title(f"All splits on the plane {n1} + {n2} + {n3} = 100%, coloured by CER"
                  f" (circle = best{', ★ = marked' if marks else ''})")
    f3 = REPORTS / f"split3d_plane_{tag}.png"
    fig3.savefig(f3, dpi=130, bbox_inches="tight")

    print(f"Saved:\n  {f1}\n  {f2}\n  {f3}")
    if not a.no_show:
        plt.show()


if __name__ == "__main__":
    main()
