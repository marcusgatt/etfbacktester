#!/usr/bin/env python3
"""ETF timing comparison: invest a lump sum now vs. wait for a possible dip.

Model (deterministic, for "what-if" exploration):
  * The ETF follows a trend line growing at the annual growth rate, starting
    from the price you enter on the "Invest now" start date.
  * On the "Wait" start date the market price sits `dip %` below that trend
    (a negative dip means the price ran up above trend instead).
  * After the dip the price either stays that far below trend (permanent) or
    recovers linearly back to the trend line by the end date.
  * Both scenarios hold the same ETF, so after the wait date they see the same
    price path; the difference comes from entry price, fees and cash interest.
"""

import calendar
import math
import tkinter as tk
from dataclasses import dataclass
from datetime import date, datetime
from tkinter import ttk

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.ticker
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

DAYS_PER_YEAR = 365.25
NOW_COLOR = "#2a6fdb"
WAIT_COLOR = "#e07b22"
BAD = "#c62828"


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def years_between(a: date, b: date) -> float:
    return (b - a).days / DAYS_PER_YEAR


@dataclass
class Params:
    amount: float
    fee: float
    sell_fee: bool
    price0: float
    growth: float      # decimal, e.g. 0.07
    dip: float         # decimal, e.g. 0.03 = 3% below trend
    recover: bool
    cash_rate: float   # decimal, interest earned while waiting
    fractional: bool
    now_date: date
    wait_date: date
    end_date: date


def trend_price(p: Params, d: date) -> float:
    return p.price0 * (1 + p.growth) ** years_between(p.now_date, d)


def market_price(p: Params, d: date) -> float:
    """Trend price with the dip phased in before the wait date and
    (optionally) phased out between the wait date and the end date."""
    if d <= p.wait_date:
        span = (p.wait_date - p.now_date).days
        frac = 1.0 if span == 0 else (d - p.now_date).days / span
        dip = p.dip * frac
    elif p.recover:
        span = (p.end_date - p.wait_date).days
        dip = p.dip * (1 - (d - p.wait_date).days / span)
    else:
        dip = p.dip
    return trend_price(p, d) * (1 - dip)


def buy(cash: float, price: float, fee: float, fractional: bool):
    investable = cash - fee
    if investable <= 0:
        return 0.0, cash, 0.0
    shares = investable / price if fractional else math.floor(investable / price)
    if shares == 0:
        return 0.0, cash, 0.0   # can't afford a single share: don't pay the fee
    return shares, investable - shares * price, fee


def simulate(p: Params) -> dict:
    total_years = years_between(p.now_date, p.end_date)
    end_price = market_price(p, p.end_date)

    def scenario(start: date, cash: float):
        price = market_price(p, start)
        shares, leftover, fees = buy(cash, price, p.fee, p.fractional)
        end_value = shares * end_price + leftover
        if p.sell_fee and shares > 0:
            end_value -= p.fee
            fees += p.fee
        gain = end_value - p.amount
        cagr = (end_value / p.amount) ** (1 / total_years) - 1 if end_value > 0 else -1.0
        return dict(start=start, price=price, cash=cash, shares=shares,
                    leftover=leftover, fees=fees, end_price=end_price,
                    end_value=end_value, gain=gain, gain_pct=gain / p.amount,
                    cagr=cagr)

    wait_cash = p.amount * (1 + p.cash_rate) ** years_between(p.now_date, p.wait_date)
    now = scenario(p.now_date, p.amount)
    wait = scenario(p.wait_date, wait_cash)

    # Break-even: the wait-date price at which both scenarios own the same
    # number of shares (exact with fractional shares, approximate otherwise).
    be_price = now["price"] * (wait_cash - p.fee) / (p.amount - p.fee)
    be_dip = 1 - be_price / trend_price(p, p.wait_date)
    be_vs_today = be_price / now["price"] - 1

    # Value-over-time series for the chart.
    n = 400
    span = (p.end_date - p.now_date).days
    dates, now_vals, wait_vals = [], [], []
    for i in range(n + 1):
        d = date.fromordinal(p.now_date.toordinal() + round(span * i / n))
        price = market_price(p, d)
        dates.append(d)
        now_vals.append(now["shares"] * price + now["leftover"])
        if d < p.wait_date:
            wait_vals.append(p.amount * (1 + p.cash_rate) ** years_between(p.now_date, d))
        else:
            wait_vals.append(wait["shares"] * price + wait["leftover"])

    return dict(now=now, wait=wait, be_price=be_price, be_dip=be_dip,
                be_vs_today=be_vs_today, dates=dates, now_vals=now_vals,
                wait_vals=wait_vals)


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
def money(x: float) -> str:
    if abs(x) < 0.005:
        x = 0.0
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def signed_money(x: float) -> str:
    return ("+" if x >= 0 else "-") + f"${abs(x):,.2f}"


def pct(x: float, signed: bool = False) -> str:
    return f"{x * 100:+.2f}%" if signed else f"{x * 100:.2f}%"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ETF Timing: Invest Now vs. Wait")
        self.geometry("1280x800")
        self.minsize(1050, 680)
        ttk.Style(self).theme_use("clam")
        self._pending = None

        today = date.today()
        self.v = {
            "amount": tk.StringVar(value="1000"),
            "fee": tk.StringVar(value="3.00"),
            "sell_fee": tk.BooleanVar(value=False),
            "price0": tk.StringVar(value="100.00"),
            "growth": tk.DoubleVar(value=7.0),
            "dip": tk.DoubleVar(value=2.0),
            "recover": tk.StringVar(value="Stays below trend"),
            "cash_rate": tk.StringVar(value="0.0"),
            "fractional": tk.BooleanVar(value=True),
            "now_date": tk.StringVar(value=today.isoformat()),
            "wait_months": tk.StringVar(value="1"),
            "wait_date": tk.StringVar(value=add_months(today, 1).isoformat()),
            "end_date": tk.StringVar(value=add_months(today, 120).isoformat()),
        }

        self._build_layout()
        for name, var in self.v.items():
            if name == "wait_months":
                var.trace_add("write", self._on_wait_months)
            else:
                var.trace_add("write", self._schedule)
        self.recalculate()

    # ---- layout ----------------------------------------------------------- #
    def _build_layout(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        self._build_inputs(left)
        self._build_results(right)
        self._build_chart(right)

    def _row(self, parent, r, label, widget_factory, hint=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=3)
        w = widget_factory(parent)
        w.grid(row=r, column=1, sticky="ew", pady=3, padx=(8, 0))
        if hint:
            ttk.Label(parent, text=hint, foreground="#777", font=("TkDefaultFont", 8)
                      ).grid(row=r + 1, column=0, columnspan=2, sticky="w")
        return w

    def _slider_row(self, parent, r, label, var, lo, hi, step):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", pady=(6, 0))
        ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=step,
                    width=8).grid(row=r, column=1, sticky="e", pady=(6, 0))
        tk.Scale(parent, variable=var, from_=lo, to=hi, resolution=step,
                 orient="horizontal", showvalue=False, highlightthickness=0
                 ).grid(row=r + 1, column=0, columnspan=2, sticky="ew")

    def _build_inputs(self, parent):
        money_box = ttk.LabelFrame(parent, text="Money", padding=8)
        money_box.pack(fill="x")
        money_box.columnconfigure(1, weight=1)
        v = self.v
        self._row(money_box, 0, "Investment amount ($)",
                  lambda p: ttk.Entry(p, textvariable=v["amount"], width=12))
        self._row(money_box, 1, "Broker fee per trade ($)",
                  lambda p: ttk.Entry(p, textvariable=v["fee"], width=12))
        ttk.Checkbutton(money_box, text="Also charge fee when selling at end",
                        variable=v["sell_fee"]).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(money_box, text="Fractional shares allowed",
                        variable=v["fractional"]).grid(row=3, column=0, columnspan=2, sticky="w")
        self._row(money_box, 4, "Cash interest while waiting (%/yr)",
                  lambda p: ttk.Entry(p, textvariable=v["cash_rate"], width=12))

        etf_box = ttk.LabelFrame(parent, text="ETF", padding=8)
        etf_box.pack(fill="x", pady=8)
        etf_box.columnconfigure(1, weight=1)
        self._row(etf_box, 0, "Price on 'Invest now' date ($)",
                  lambda p: ttk.Entry(p, textvariable=v["price0"], width=12))
        self._slider_row(etf_box, 1, "Annual growth rate (%)", v["growth"], -10, 25, 0.25)
        self._slider_row(etf_box, 3, "Price dip at wait date (% below trend)", v["dip"], -15, 35, 0.25)
        ttk.Label(etf_box, text="Negative = price rose above trend while you waited",
                  foreground="#777", font=("TkDefaultFont", 8)
                  ).grid(row=5, column=0, columnspan=2, sticky="w")
        self._row(etf_box, 6, "After the dip",
                  lambda p: ttk.Combobox(p, textvariable=v["recover"], state="readonly", width=24,
                                         values=["Stays below trend",
                                                 "Recovers to trend by end"]))

        dates_box = ttk.LabelFrame(parent, text="Dates (YYYY-MM-DD)", padding=8)
        dates_box.pack(fill="x")
        dates_box.columnconfigure(1, weight=1)
        self._row(dates_box, 0, "'Invest now' start date",
                  lambda p: ttk.Entry(p, textvariable=v["now_date"], width=12))
        self._row(dates_box, 1, "Wait period (months)",
                  lambda p: ttk.Spinbox(p, textvariable=v["wait_months"], from_=0, to=60,
                                        increment=1, width=10),
                  hint="Sets the wait start date relative to the 'now' date")
        self._row(dates_box, 3, "'Wait' start date",
                  lambda p: ttk.Entry(p, textvariable=v["wait_date"], width=12))
        self._row(dates_box, 4, "End date (both)",
                  lambda p: ttk.Entry(p, textvariable=v["end_date"], width=12))

        self.status = ttk.Label(parent, text="", foreground=BAD, wraplength=330, justify="left")
        self.status.pack(fill="x", pady=(8, 0))

    def _build_results(self, parent):
        self.verdict = ttk.Label(parent, text="", font=("TkDefaultFont", 14, "bold"))
        self.verdict.grid(row=0, column=0, sticky="w")
        self.breakeven = ttk.Label(parent, text="", wraplength=800, justify="left")
        self.breakeven.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        parent.bind("<Configure>", lambda e: self.breakeven.config(wraplength=max(300, e.width - 10)))

        cols = ("now", "wait", "diff")
        self.table = ttk.Treeview(parent, columns=cols, height=11, selectmode="none")
        self.table.heading("#0", text="")
        self.table.heading("now", text="Invest now")
        self.table.heading("wait", text="Wait")
        self.table.heading("diff", text="Wait − Now")
        self.table.column("#0", width=150, minwidth=140)
        self.table.column("now", width=120, minwidth=100, anchor="e")
        self.table.column("wait", width=120, minwidth=100, anchor="e")
        self.table.column("diff", width=170, minwidth=150, anchor="e")
        self.table.grid(row=3, column=0, sticky="ew", pady=(8, 0))

    def _build_chart(self, parent):
        self.fig = Figure(figsize=(8, 4), dpi=100, layout="constrained")
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().grid(row=2, column=0, sticky="nsew")

    # ---- events ------------------------------------------------------------ #
    def _schedule(self, *_):
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(150, self.recalculate)

    def _on_wait_months(self, *_):
        try:
            months = int(self.v["wait_months"].get())
            start = date.fromisoformat(self.v["now_date"].get().strip())
        except (ValueError, tk.TclError):
            return
        self.v["wait_date"].set(add_months(start, months).isoformat())

    def _read_params(self) -> Params:
        v = self.v

        def num(key, label):
            try:
                return float(v[key].get())
            except (ValueError, tk.TclError):
                raise ValueError(f"{label} must be a number.")

        def day(key, label):
            try:
                return date.fromisoformat(v[key].get().strip())
            except ValueError:
                raise ValueError(f"{label} must be a date like 2026-10-16.")

        p = Params(
            amount=num("amount", "Investment amount"),
            fee=num("fee", "Broker fee"),
            sell_fee=v["sell_fee"].get(),
            price0=num("price0", "ETF price"),
            growth=num("growth", "Growth rate") / 100,
            dip=num("dip", "Dip") / 100,
            recover=v["recover"].get().startswith("Recovers"),
            cash_rate=num("cash_rate", "Cash interest") / 100,
            fractional=v["fractional"].get(),
            now_date=day("now_date", "'Invest now' start date"),
            wait_date=day("wait_date", "'Wait' start date"),
            end_date=day("end_date", "End date"),
        )
        if p.amount <= p.fee:
            raise ValueError("Investment amount must be larger than the broker fee.")
        if p.fee < 0:
            raise ValueError("Broker fee can't be negative.")
        if p.price0 <= 0:
            raise ValueError("ETF price must be positive.")
        if p.dip >= 1:
            raise ValueError("Dip must be below 100%.")
        if p.wait_date < p.now_date:
            raise ValueError("'Wait' start date must be on or after the 'Invest now' date.")
        if p.end_date <= p.wait_date:
            raise ValueError("End date must be after the 'Wait' start date.")
        return p

    # ---- render ------------------------------------------------------------ #
    def recalculate(self):
        self._pending = None
        try:
            p = self._read_params()
        except ValueError as e:
            self.status.config(text=str(e))
            return
        self.status.config(text="")
        r = simulate(p)
        self._render_table(r)
        self._render_verdict(p, r)
        self._render_chart(p, r)

    def _render_table(self, r):
        now, wait = r["now"], r["wait"]
        self.table.delete(*self.table.get_children())

        def row(label, a, b, fmt, diff_fmt=None, higher_is_better=None):
            diff = ""
            if diff_fmt:
                d = b - a
                diff = diff_fmt(d)
                if higher_is_better is not None and abs(d) > 1e-6:
                    diff += "  ✔ wait" if (d > 0) == higher_is_better else "  ✔ now"
            self.table.insert("", "end", text=label, values=(fmt(a), fmt(b), diff))

        self.table.insert("", "end", text="Buy date",
                          values=(now["start"].isoformat(), wait["start"].isoformat(),
                                  f"{(wait['start'] - now['start']).days} days later"))
        row("Buy price / share", now["price"], wait["price"], money, signed_money, False)
        row("Cash at purchase", now["cash"], wait["cash"], money, signed_money, True)
        row("Fees paid", now["fees"], wait["fees"], money, signed_money, False)
        row("Shares owned", now["shares"], wait["shares"], lambda x: f"{x:,.4f}",
            lambda d: f"{d:+,.4f}", True)
        row("Uninvested cash", now["leftover"], wait["leftover"], money, signed_money)
        row("Price at end date", now["end_price"], wait["end_price"], money)
        row("Value at end date", now["end_value"], wait["end_value"], money, signed_money, True)
        row("Gain ($)", now["gain"], wait["gain"], signed_money, signed_money, True)
        row("Gain (%)", now["gain_pct"], wait["gain_pct"], lambda x: pct(x, True),
            lambda d: f"{d * 100:+.2f} pts", True)
        row("Annualized return*", now["cagr"], wait["cagr"], lambda x: pct(x, True),
            lambda d: f"{d * 100:+.2f} pts", True)

    def _render_verdict(self, p, r):
        diff = r["wait"]["end_value"] - r["now"]["end_value"]
        if abs(diff) < 0.005:
            self.verdict.config(text="It's a tie.", foreground="#333")
        elif diff > 0:
            self.verdict.config(text=f"Waiting wins by {money(diff)}", foreground=WAIT_COLOR)
        else:
            self.verdict.config(text=f"Investing now wins by {money(-diff)}", foreground=NOW_COLOR)

        approx = "" if p.fractional else " (approx., whole shares)"
        if abs(r["be_vs_today"]) < 0.00005:
            vs_now = "the same as the 'now' price"
        else:
            direction = "below" if r["be_vs_today"] < 0 else "above"
            vs_now = f"{pct(abs(r['be_vs_today']))} {direction} the 'now' price"
        self.breakeven.config(text=(
            f"Break-even{approx}: waiting only pays off if the price on "
            f"{p.wait_date.isoformat()} is at or below {money(r['be_price'])} — "
            f"a dip of at least {pct(r['be_dip'])} below trend "
            f"({vs_now}). "
            f"Your scenario assumes {pct(p.dip)}.\n"
            "* Annualized over the full period from the 'Invest now' date to the end date, "
            "since the money is set aside for both from day one."))

    def _render_chart(self, p, r):
        ax = self.ax
        ax.clear()
        x = [datetime.combine(d, datetime.min.time()) for d in r["dates"]]
        ax.plot(x, r["now_vals"], color=NOW_COLOR, lw=2, label="Invest now")
        ax.plot(x, r["wait_vals"], color=WAIT_COLOR, lw=2, label="Wait", ls="--")
        ax.axhline(p.amount, color="#999", lw=1, ls=":", label="Amount invested")
        ax.axvline(datetime.combine(p.wait_date, datetime.min.time()),
                   color=WAIT_COLOR, lw=1, alpha=0.5)
        ax.annotate("wait buy", (datetime.combine(p.wait_date, datetime.min.time()), 0),
                    xycoords=("data", "axes fraction"), xytext=(4, 4),
                    textcoords="offset points", fontsize=8, color=WAIT_COLOR)

        wait_higher = r["wait_vals"][-1] >= r["now_vals"][-1]
        for vals, color, above in ((r["now_vals"], NOW_COLOR, not wait_higher),
                                   (r["wait_vals"], WAIT_COLOR, wait_higher)):
            ax.annotate(money(vals[-1]), (x[-1], vals[-1]), xytext=(-6, 8 if above else -8),
                        textcoords="offset points", ha="right", va="bottom" if above else "top",
                        fontsize=9, fontweight="bold", color=color)

        ax.set_title("Portfolio value over time")
        ax.set_ylabel("Value ($)")
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda y, _: f"${y:,.0f}"))
        loc = mdates.AutoDateLocator()
        ax.xaxis.set_major_locator(loc)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
        ax.grid(alpha=0.3)
        ax.legend(loc="upper left")
        self.canvas.draw_idle()


if __name__ == "__main__":
    App().mainloop()
