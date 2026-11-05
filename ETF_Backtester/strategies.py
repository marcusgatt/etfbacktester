"""Your strategies live here. Add a class, then add an instance of it to STRATEGIES.

Every strategy gets the same money: contributions land in the bank account on the
schedule set in backtest.py (monthly $1,000 by default), the bank pays interest
daily, and dividends land in the bank (not reinvested, unless --drp is on and the
fund has a DRP). Tax bills/refunds come out of / go into the bank each 31 October.
`on_day` runs after each day's close; any order you place fills at the next
trading day's open.

What you can use inside on_day(self, ctx):

  Today         ctx.date, ctx.price (today's close), ctx.open
                ctx.is_new_month, ctx.is_first_day
                ctx.contribution_today, ctx.dividend_today   ($ that landed in the bank today)
  Account       ctx.cash      bank balance not already committed to an order
                ctx.shares    shares held
                ctx.holdings_value, ctx.total_value, ctx.contributed
  History       (each takes adjusted=True for dividend-adjusted prices — see below)
                ctx.closes(n)          last n trading-day closes, ending today
                ctx.closes_since(days) closes over the last `days` calendar days
                ctx.lowest(days), ctx.highest(days)
                ctx.is_lowest(days)    True if today's close is the lowest in `days` calendar days
                ctx.sma(n)             n-trading-day moving average
                ctx.month_end_closes(n) closes at the end of the last n completed months
                ctx.drawdown(days)     how far below the `days` high we are (0.10 = 10%; adjusted by default)
                ctx.change(days)       % change over the last `days` calendar days (0.05 = +5%)
  Orders        ctx.buy(dollars)       whole shares, fee included; capped at ctx.cash
                ctx.buy_all(), ctx.buy_shares(n), ctx.sell(n), ctx.sell_all()
                every order takes an optional note="why" that shows in the trade log
  Notes         ctx.log("message")     shows in the report's activity log

Price history before your start date is available, so lookbacks work from day one.

Raw vs adjusted prices: the actual price drops by the dividend on every ex-date
(VHY pays ~5-6% a year), so raw prices drift down even when the market doesn't.
Use adjusted=True for trend / drawdown signals so payouts don't look like falls.
Trades always happen at the actual price.
"""

from engine import Strategy


# --------------------------------------------------------------------------- #
# Baselines — keep these in the list so you always have something to beat
# --------------------------------------------------------------------------- #
class BankOnly(Strategy):
    baseline = True       # baselines always run, even with --only
    name = "Bank only"
    description = "Never buys. Every contribution (and its interest) stays in the bank."

    def on_day(self, ctx):
        pass


class MonthlyDCA(Strategy):
    """Invest each contribution as soon as it arrives. Dividends stay in the bank
    unless reinvest_dividends=True."""

    baseline = True

    def __init__(self, reinvest_dividends: bool = False):
        self.reinvest = reinvest_dividends
        self.name = "Invest every contribution" + (" + dividends" if reinvest_dividends else "")
        self.description = ("Buys with each contribution the next morning — classic dollar-cost averaging. "
                            + ("Dividends are reinvested too (with the next contribution, to save a fee)."
                               if reinvest_dividends else "Dividends stay in the bank earning interest."))

    def setup(self, ctx):
        self.kept = 0.0          # dividends (plus their interest) set aside in the bank
        self.last = ctx.date

    def on_day(self, ctx):
        if not self.reinvest:
            self.kept *= (1 + ctx.settings.bank_rate / 100 / 365) ** (ctx.date - self.last).days
            self.kept += ctx.dividend_today
        self.last = ctx.date
        # Dividends being reinvested wait for the next contribution's buy (one $3 fee, not
        # two) — unless there are no regular contributions to ride along with.
        lone_dividend = self.reinvest and ctx.dividend_today and not ctx.settings.contribution
        if ctx.contribution_today or lone_dividend:
            # everything except set-aside dividends, so whole-share leftovers get invested next time
            ctx.buy(ctx.cash - self.kept, note="contribution" if ctx.contribution_today else "dividend")


# --------------------------------------------------------------------------- #
# Your strategy: save in the bank, buy the dip
# --------------------------------------------------------------------------- #
class DipBuyer(Strategy):
    """Contributions pool in the bank. When today's close is the lowest of the past
    month, invest ALL savings. Once savings grow past `big_savings`, also accept a
    smaller dip — lowest of the past week — and invest `relaxed_fraction` of savings.
    """

    def __init__(self, strict_days=30, relaxed_days=7, big_savings=5000.0,
                 relaxed_fraction=0.5, min_buy=500.0):
        self.strict_days = strict_days
        self.relaxed_days = relaxed_days
        self.big_savings = big_savings
        self.relaxed_fraction = relaxed_fraction
        self.min_buy = min_buy    # don't bother trading tiny amounts ($3 fee)
        self.name = f"Dip buyer ({strict_days}d low / {relaxed_days}d low over ${big_savings:,.0f})"
        self.description = (f"Saves in the bank. Invests everything when price hits a {strict_days}-day low. "
                            f"If savings exceed ${big_savings:,.0f}, a {relaxed_days}-day low is enough "
                            f"to invest {relaxed_fraction:.0%} of savings. Ignores buys under ${min_buy:,.0f}.")

    def on_day(self, ctx):
        if ctx.cash < self.min_buy:
            return
        if ctx.is_lowest(self.strict_days):
            ctx.buy_all(note=f"{self.strict_days}-day low ${ctx.price:.2f}")
        elif ctx.cash > self.big_savings and ctx.is_lowest(self.relaxed_days):
            ctx.buy(ctx.cash * self.relaxed_fraction,
                    note=f"{self.relaxed_days}-day low ${ctx.price:.2f}, savings ${ctx.cash:,.0f}")


# --------------------------------------------------------------------------- #
# Another template to copy from: buy when price is below its moving average
# --------------------------------------------------------------------------- #
class BelowAverage(Strategy):
    def __init__(self, days=200, min_buy=500.0):
        self.days, self.min_buy = days, min_buy
        self.name = f"Buy below {days}-day average"
        self.description = f"Invests all savings whenever the close is below its {days}-trading-day average."

    def on_day(self, ctx):
        if ctx.cash >= self.min_buy and ctx.price < ctx.sma(self.days):
            ctx.buy_all(note=f"${ctx.price:.2f} < avg ${ctx.sma(self.days):.2f}")


# --------------------------------------------------------------------------- #
# Value averaging (Michael Edleson, 1988)
# --------------------------------------------------------------------------- #
class ValueAveraging(Strategy):
    """Holdings should follow a target path that rises by one contribution each
    period and compounds at `target_growth` a year. Each contribution day, buy
    whatever closes the gap — more after a fall, nothing after a big rise.
    Spare cash (including dividends) waits in the bank for the next shortfall."""

    def __init__(self, target_growth=0.07, sell_excess=False, min_buy=100.0):
        self.growth, self.sell_excess, self.min_buy = target_growth, sell_excess, min_buy
        self.name = f"Value averaging ({target_growth:.0%} target growth)"
        self.description = (f"Target holdings grow by one contribution each period plus {target_growth:.0%} a year. "
                            "Each contribution day, invest the shortfall from the bank"
                            + ("; sell any excess above target." if sell_excess
                               else "; never sells — surplus cash waits for a shortfall."))

    def setup(self, ctx):
        self.target = 0.0
        self.last = ctx.date

    def on_day(self, ctx):
        if not ctx.contribution_today:
            return
        self.target *= (1 + self.growth) ** ((ctx.date - self.last).days / 365)
        self.target += ctx.contribution_today
        self.last = ctx.date
        gap = self.target - ctx.shares * ctx.price
        if gap >= self.min_buy:
            ctx.buy(gap, note=f"target ${self.target:,.0f}, short ${gap:,.0f}")
        elif self.sell_excess and -gap >= ctx.price + ctx.settings.fee:
            ctx.sell(int(-gap // ctx.price), note=f"target ${self.target:,.0f}, over by ${-gap:,.0f}")


# --------------------------------------------------------------------------- #
# Trend following: 10-month moving average (Mebane Faber, 2007)
# --------------------------------------------------------------------------- #
class TrendFilter(Strategy):
    """At the start of each month: if last month's close is above the average of
    the last `months` month-end closes (dividend-adjusted, as in Faber's paper —
    raw prices fall at every ex-date), be fully invested; otherwise sell
    everything and wait in the bank. Selling would trigger CGT in real life,
    which this model ignores, so the result is flattering."""

    def __init__(self, months=10):
        self.months = months
        self.name = f"Trend filter ({months}-month average)"
        self.description = (f"Monthly check: fully invested while price is above its {months}-month average, "
                            "otherwise sells everything to cash. Ignores CGT on the sales.")

    def setup(self, ctx):
        self.invested = True

    def on_day(self, ctx):
        if ctx.is_new_month or ctx.is_first_day:
            closes = ctx.month_end_closes(self.months, adjusted=True)
            if len(closes) == self.months:
                last, avg = closes[-1], sum(closes) / len(closes)
                self.invested = last > avg
                if not self.invested and ctx.shares:
                    ctx.sell_all(note=f"adj. month-end {last:.2f} < {self.months}m avg {avg:.2f}")
        # while the trend is up, keep all savings (contributions, dividends) invested
        if self.invested and ctx.cash >= 500:
            ctx.buy_all(note="trend up")


# --------------------------------------------------------------------------- #
# Enhanced DCA: always invest most of each contribution, save the rest for dips
# --------------------------------------------------------------------------- #
class EnhancedDCA(Strategy):
    """Invest `base_fraction` of every contribution straight away. The rest (plus
    dividends) builds a reserve in the bank, which is invested in full whenever
    price is `dip` or more below its 1-year high (dividend-adjusted, so payouts
    don't count as dips)."""

    def __init__(self, base_fraction=0.75, dip=0.10, min_buy=500.0):
        self.base, self.dip, self.min_buy = base_fraction, dip, min_buy
        self.name = f"Enhanced DCA ({base_fraction:.0%} now, reserve for {dip:.0%} dips)"
        self.description = (f"Invests {base_fraction:.0%} of each contribution immediately. The rest and all "
                            f"dividends build a reserve, invested whenever price is {dip:.0%}+ below its 1-year high.")

    def on_day(self, ctx):
        dd = ctx.drawdown(365, adjusted=True)
        if dd >= self.dip and ctx.cash >= self.min_buy:
            ctx.buy_all(note=f"{dd:.0%} below 1-year high (dividend-adjusted)")
        elif ctx.contribution_today:
            ctx.buy(ctx.contribution_today * self.base, note="base contribution")


# --------------------------------------------------------------------------- #
# Which strategies run. Add your own, or the same class with different settings,
# e.g. DipBuyer(big_savings=10000) next to DipBuyer() to compare thresholds.
# --------------------------------------------------------------------------- #
STRATEGIES = [
    BankOnly(),
    MonthlyDCA(),                           # dividends pile up in the bank
    MonthlyDCA(reinvest_dividends=True),    # the fair benchmark for strategies that invest all savings
    DipBuyer(),
    BelowAverage(),
    ValueAveraging(),
    TrendFilter(),
    EnhancedDCA(),
]
