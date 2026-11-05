# ETF strategy backtester

Tests investing strategies on one ETF's real daily price and dividend history
(Yahoo Finance). Pure Python standard library — nothing to install.

## Run it

```bash
python3 backtest.py                                    # ASX:VHY, $1,000/month from 2015, 5.5% bank, $3 fee
python3 backtest.py --ticker ASX:VAS --start 2018-01-01
python3 backtest.py --contribution 500 --frequency fortnightly --initial 10000
python3 backtest.py --only dip                          # your strategies matching "dip" + the baselines
python3 backtest.py --income 25000 --drp                # your tax bracket, dividends reinvested via DRP
python3 backtest.py --help                              # every option
```

Results print in the terminal and an HTML report opens in your browser
(saved in `reports/`). Prices are cached in `data/` for 12 hours; `--refresh`
forces a re-download.

## Compare funds, test robustness

```bash
python3 compare.py                    # 8 ETFs, after tax at $20k / $70k / $150k income
python3 compare.py --incomes 25000 --cgt old
python3 compare.py --tickers ASX:VAS ASX:VGS ASX:IVV --periods 2016-01-01:2026-12-31
python3 robustness.py                 # every strategy x every fund x rolling 5-year windows
python3 robustness.py --years 3 --step 3 --bank-rates 4
```

`robustness.py` measures each strategy against "Invest every contribution +
dividends" in the same fund and window, and writes `reports/robustness.html`.

Data notes: Yahoo has no dividends for VAS/VHY/IVV/VTS before 2013, so data is
only used from shortly before each fund's first recorded dividend. Bad prices
(IVV has a one-day spike in Dec 2015 and an unadjusted split before 2011) are
repaired or dropped automatically, with a note printed.

## Write your own strategy

Add a class to `strategies.py` and put an instance in the `STRATEGIES` list:

```python
class BuyAfterBigDrop(Strategy):
    name = "Buy after a 5% drop"
    description = "Invests all savings when price is 5%+ below its 60-day high."

    def on_day(self, ctx):
        if ctx.cash > 500 and ctx.price < ctx.highest(60) * 0.95:
            ctx.buy_all(note="5% off the high")
```

`on_day` runs after every trading day's close. The top of `strategies.py` lists
everything `ctx` gives you (price history helpers, cash, shares, buy/sell).
To compare settings, add the same class twice with different parameters, e.g.
`DipBuyer(big_savings=5000)` and `DipBuyer(big_savings=10000)`.

## How the simulation works

- Contributions go into the bank account on schedule; uninvested cash earns the
  bank rate, accrued daily.
- Orders fill at the **next day's open** (so a strategy can't trade on a price it
  only learns at the close), in whole shares, with the brokerage fee.
- Dividends are paid as cash into the bank ~16 days after the ex-date (if you held
  shares going into it) — not reinvested unless your strategy buys with them.
- Price helpers use the actual traded price by default. Pass `adjusted=True` for
  dividend-adjusted prices: VHY's raw price drops by each payout, so trend and
  drawdown signals should use adjusted prices. The trend filter and enhanced DCA
  already do.
- Changing bank rates are ignored (a flat rate is used).

## Tax (tax.py)

On by default (`--no-tax` turns it off). A resident individual with `--income`
of other taxable income (default $40,000), using 2026-27 rates for every year:

- Bank interest and dividends are taxed at your marginal rate (LITO and Medicare
  levy included). Franking credits are refundable; foreign tax offsets are not.
  Each fund's franking/foreign credit per $1 of distribution is an estimate in
  `FUNDS` in tax.py (VHY's comes from a real statement), so edit them as needed.
- Capital gains tax on every sale, parcels sold first-in, first-out. `--cgt new`
  (default) means CPI-indexed cost base for units held 12+ months plus a 30% minimum
  on net real gains, which applies to gains from 1 July 2027. `--cgt old` means a 50% discount.
  CPI comes from the ABS (cached in `data/cpi_au.json`).
- Each financial year's tax is paid from (or refunded to) the bank on 31 October.
- At the end everything is sold in one go and the last tax bill settled. The
  "after tax" value and XIRR use that cash. Selling everything in one year is
  the worst case for CGT; spreading sales over several years would cost less.
- `--drp` reinvests dividends through the fund's DRP (no brokerage, leftover cents
  carried) where the fund has one. VTS has no DRP. Without a DRP,
  "Invest every contribution + dividends" adds dividends to the next monthly buy.
- Not modelled: capital-gain and tax-deferred parts of distributions, AMIT cost
  base adjustments, HELP repayments, and threshold changes over time.

Compare a strategy that invests all its savings against **"Invest every
contribution + dividends"**, because both put dividends back to work. The plain
"Invest every contribution" baseline leaves dividends in the bank.
