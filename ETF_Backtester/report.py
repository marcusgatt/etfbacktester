"""Self-contained HTML report (no internet needed to view it)."""

import html
import json
from pathlib import Path

REPORT_DIR = Path(__file__).resolve().parent / "reports"


def write_report(md, settings, results) -> Path:
    dates = results[0].dates
    idx = {d: i for i, d in enumerate(dates)}
    i0 = md.dates.index(dates[0])
    data = {
        "symbol": md.symbol,
        "ticker": settings.ticker,
        "currency": md.currency,
        "settings": {
            "contribution": settings.contribution, "frequency": settings.frequency,
            "initial": settings.initial, "bank_rate": settings.bank_rate, "fee": settings.fee,
            "pay_lag": settings.pay_lag_days, "fill": settings.fill,
            "tax": settings.tax, "income": settings.income, "cgt": settings.cgt_regime, "drp": settings.drp,
        },
        "dates": [d.isoformat() for d in dates],
        "price": [round(c, 4) for c in md.closes[i0:i0 + len(dates)]],
        "contributed": [round(c, 2) for c in results[0].contributed],
        "strategies": [],
    }
    for r in results:
        x = r.xirr
        data["strategies"].append({
            "name": r.name,
            "description": r.description,
            "values": [round(v, 2) for v in r.values],
            "trades": [{"i": idx[t.date], "side": t.side, "shares": t.shares, "price": round(t.price, 4),
                        "fee": t.fee, "cash": round(t.cash_after, 2), "note": t.note} for t in r.trades],
            "logs": [{"i": idx[d], "msg": m} for d, m in r.logs if d in idx],
            "m": {
                "final": r.final_value, "contributed": r.total_contributed, "profit": r.profit,
                "xirr": x, "dividends": r.dividends, "interest": r.interest, "fees": r.fees,
                "trades": len(r.trades), "cash_pct": r.avg_cash_pct, "drawdown": r.max_drawdown,
                "shares": r.final_shares, "cash": r.final_cash, "receivable": r.receivable,
                "avg_buy": r.avg_buy_price,
                "after_tax": r.after_tax_value, "after_tax_xirr": r.after_tax_xirr,
                "tax_paid": r.tax_paid, "cgt_paid": r.cgt_paid, "franking": r.franking,
            },
        })

    REPORT_DIR.mkdir(exist_ok=True)
    tag = settings.ticker.replace(":", "_").replace(".", "_")
    out = REPORT_DIR / f"{tag}_{dates[0]}_to_{dates[-1]}.html"
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(f"{md.symbol} backtest")
    out.write_text(TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #5f5e59;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --good: #006300; --bad: #b42b2b;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --s4: #eda100; --s5: #e87ba4; --s6: #008300; --s7: #6250d6; --s8: #e34948;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --good: #0ca30c; --bad: #e66767;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --s4: #c98500; --s5: #d55181; --s6: #008300; --s7: #9085e9; --s8: #e66767;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
       font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1180px; margin: 0 auto; padding: 28px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; font-weight: 650; }
h2 { font-size: 16px; margin: 0; font-weight: 600; }
.sub { color: var(--ink-2); margin: 0 0 24px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 18px 18px 14px; margin-bottom: 20px; }
.card-head { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.hint { color: var(--muted); font-size: 12.5px; margin: 2px 0 0; }
select { font: inherit; color: var(--ink); background: var(--surface); border: 1px solid var(--axis); border-radius: 6px; padding: 4px 8px; max-width: 100%; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 8px 10px; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--grid); }
th { color: var(--ink-2); font-weight: 600; font-size: 12.5px; }
th:first-child, td:first-child { text-align: left; }
td.name { white-space: normal; min-width: 240px; }
td.name .desc { color: var(--muted); font-size: 12.5px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 7px; vertical-align: 0; }
.best { font-size: 11px; font-weight: 600; color: var(--good); margin-left: 4px; }
.neg { color: var(--bad); }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 0 0 8px; color: var(--ink-2); font-size: 12.5px; }
.legend span { display: inline-flex; align-items: center; }
.legend .dash { width: 16px; height: 0; border-top: 2px dashed var(--muted); margin-right: 7px; }
.chart { position: relative; width: 100%; }
.chart svg { display: block; width: 100%; overflow: visible; }
.chart text { fill: var(--muted); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.tip { position: absolute; pointer-events: none; background: var(--surface); color: var(--ink);
       border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12.5px;
       box-shadow: 0 4px 16px rgba(0,0,0,.14); white-space: nowrap; display: none; z-index: 2;
       font-variant-numeric: tabular-nums; }
.tip .d { color: var(--ink-2); margin-bottom: 4px; font-weight: 600; }
.tip .row { display: flex; justify-content: space-between; gap: 14px; }
.tip .row b { font-weight: 600; }
.log { max-height: 420px; overflow: auto; }
.log td { white-space: normal; }
.log td:nth-child(1), .log td:nth-child(2) { white-space: nowrap; }
.log th:nth-child(-n+3), .log td:nth-child(-n+3) { text-align: left; }
.tag { font-size: 11px; font-weight: 600; padding: 1px 6px; border-radius: 4px; border: 1px solid var(--axis); color: var(--ink-2); }
ul.notes { color: var(--ink-2); padding-left: 18px; margin: 0; }
ul.notes li { margin: 4px 0; }
</style>
</head>
<body>
<main>
  <h1 id="title"></h1>
  <p class="sub" id="subtitle"></p>

  <section class="card">
    <div class="card-head"><h2>Results</h2></div>
    <div class="table-wrap"><table id="summary"></table></div>
    <p class="hint">Final value = ETF holdings at the last close + bank balance + dividends declared but not yet paid.
      XIRR is the annualised return on the money you put in, accounting for when you put it in.
      After tax = cash in hand after selling everything on the last day and paying all tax owed (negative tax paid = net refund, from franking credits).
      Worst drawdown = biggest fall in profit from its peak, as a share of the money contributed at the time.</p>
  </section>

  <section class="card">
    <div class="card-head"><h2>Portfolio value</h2></div>
    <div class="legend" id="legend-value"></div>
    <div class="chart" id="chart-value"></div>
  </section>

  <section class="card">
    <div class="card-head">
      <h2>Ahead or behind</h2>
      <label>compared with <select id="ref-select"></select></label>
    </div>
    <div class="legend" id="legend-diff"></div>
    <div class="chart" id="chart-diff"></div>
    <p class="hint">Each line is that strategy's portfolio value minus the chosen strategy's. Above zero = ahead.</p>
  </section>

  <section class="card">
    <div class="card-head">
      <h2 id="price-title">Price and trades</h2>
      <label>trades for <select id="trade-select"></select></label>
    </div>
    <div class="legend" id="legend-price"></div>
    <div class="chart" id="chart-price"></div>
  </section>

  <section class="card">
    <div class="card-head"><h2 id="log-title">Activity</h2></div>
    <div class="log"><table id="log"></table></div>
  </section>

  <section class="card">
    <div class="card-head"><h2>Assumptions</h2></div>
    <ul class="notes" id="notes"></ul>
  </section>
</main>

<script>
const D = __DATA__;
const N = D.dates.length;
const T = D.dates.map(s => Date.parse(s + "T00:00:00"));
const SLOTS = 8;
const color = k => k < SLOTS ? `var(--s${k + 1})` : "var(--muted)";
const cur = new Intl.NumberFormat("en-AU", {style: "currency", currency: D.currency || "AUD", maximumFractionDigits: 0});
const cur2 = new Intl.NumberFormat("en-AU", {style: "currency", currency: D.currency || "AUD", minimumFractionDigits: 2, maximumFractionDigits: 2});
const money = v => v == null ? "n/a" : cur.format(v);
const signed = v => (v > 0 ? "+" : v < 0 ? "−" : "") + cur.format(Math.abs(v));
const pct = (v, dp = 2) => v == null ? "n/a" : (v * 100).toFixed(dp) + "%";
const fmtDate = t => new Date(t).toLocaleDateString("en-AU", {day: "numeric", month: "short", year: "numeric"});
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const S = D.strategies;

// ---------- header ----------
const st = D.settings;
document.getElementById("title").textContent = `${D.ticker} strategy backtest`;
document.getElementById("subtitle").textContent =
  `${fmtDate(T[0])} – ${fmtDate(T[N - 1])} · ${cur.format(st.contribution)} ${st.frequency}` +
  (st.initial ? ` + ${cur.format(st.initial)} up front` : "") +
  ` · bank ${st.bank_rate}% p.a. · ${cur2.format(st.fee)} per trade · ${money(D.contributed[N - 1])} contributed in total` +
  (st.tax ? ` · tax on ${cur.format(st.income)} income, ${st.cgt} CGT rules` : " · no tax") + (st.drp ? " · DRP" : "");

// ---------- summary table ----------
(() => {
  const best = Math.max(...S.map(s => s.m.after_tax_xirr ?? -Infinity));
  const head = ["Strategy", "Final value", "Profit", "XIRR", "After tax", "After-tax XIRR", "Tax paid", "Dividends", "Interest", "Fees", "Trades", "Avg in cash", "Worst drawdown"];
  let h = "<thead><tr>" + head.map(x => `<th>${x}</th>`).join("") + "</tr></thead><tbody>";
  S.forEach((s, k) => {
    const m = s.m;
    h += `<tr><td class="name"><span class="sw" style="background:${color(k)}"></span><b>${esc(s.name)}</b>` +
         `<div class="desc">${esc(s.description)}</div></td>` +
         `<td>${money(m.final)}</td><td class="${m.profit < 0 ? "neg" : ""}">${signed(m.profit)}</td>` +
         `<td>${pct(m.xirr)}</td><td>${money(m.after_tax)}</td>` +
         `<td><b>${pct(m.after_tax_xirr)}</b>${m.after_tax_xirr === best ? '<span class="best">▲ best</span>' : ""}</td>` +
         `<td>${signed(m.tax_paid)}</td><td>${money(m.dividends)}</td><td>${money(m.interest)}</td><td>${money(m.fees)}</td><td>${m.trades}</td>` +
         `<td>${pct(m.cash_pct, 0)}</td><td>${m.drawdown < 0 ? pct(m.drawdown, 1) : "–"}</td></tr>`;
  });
  document.getElementById("summary").innerHTML = h + "</tbody>";
})();

// ---------- chart helpers ----------
function niceTicks(lo, hi, n = 5) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(f => f * mag).find(s => s >= raw);
  const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step, out = [];
  for (let v = a; v <= b + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}
function compact(v) {
  const a = Math.abs(v), sgn = v < 0 ? "−" : "";
  if (a < 1e-9) return "$0";
  if (a >= 1e6) return sgn + "$" + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1) + "M";
  if (a >= 1e3) return sgn + "$" + (a / 1e3).toFixed(a >= 1e4 ? 0 : 1) + "k";
  return sgn + "$" + a.toFixed(a < 10 ? 2 : 0);
}
function legend(id, items) {
  document.getElementById(id).innerHTML = items.map(it =>
    it.dash ? `<span><i class="dash"></i>${esc(it.name)}</span>`
            : `<span><i class="sw" style="background:${it.color}${it.hollow ? ";background:transparent;border:2px solid " + it.color : ""}"></i>${esc(it.name)}</span>`
  ).join("");
}

// series: [{name, color, values, dash, width}], markers: [{i, y, color, hollow}], tip(i) -> html
function lineChart(hostId, {series, markers = [], zero = false, height = 340, tip}) {
  const host = document.getElementById(hostId);
  function draw() {
    const W = host.clientWidth || 800, H = height, m = {l: 58, r: 12, t: 10, b: 26};
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    let lo = Infinity, hi = -Infinity;
    for (const s of series) for (const v of s.values) { if (v < lo) lo = v; if (v > hi) hi = v; }
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const yt = niceTicks(lo, hi);
    const y0 = yt[0], y1 = yt[yt.length - 1];
    const X = i => m.l + (T[i] - T[0]) / (T[N - 1] - T[0] || 1) * iw;
    const Y = v => m.t + ih - (v - y0) / (y1 - y0) * ih;
    let g = "";
    for (const v of yt) {
      const isZero = zero && Math.abs(v) < 1e-9;
      g += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(${isZero ? "--axis" : "--grid"})" stroke-width="1"/>`;
      g += `<text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${compact(v)}</text>`;
    }
    const y0yr = new Date(T[0]).getFullYear(), y1yr = new Date(T[N - 1]).getFullYear();
    const every = Math.max(1, Math.ceil((y1yr - y0yr + 1) / Math.max(2, Math.floor(iw / 70))));
    for (let yr = y0yr + 1; yr <= y1yr; yr++) {
      if ((yr - y0yr) % every) continue;
      const t = Date.parse(yr + "-01-01T00:00:00");
      const x = m.l + (t - T[0]) / (T[N - 1] - T[0]) * iw;
      g += `<line x1="${x}" x2="${x}" y1="${m.t + ih}" y2="${m.t + ih + 4}" stroke="var(--axis)"/>`;
      g += `<text x="${x}" y="${H - 6}" text-anchor="middle">${yr}</text>`;
    }
    g += `<line x1="${m.l}" x2="${W - m.r}" y1="${m.t + ih}" y2="${m.t + ih}" stroke="var(--axis)"/>`;
    // thin each path to ~2 points per pixel
    const stride = Math.max(1, Math.floor(N / (iw * 2)));
    for (const s of series) {
      let d = "";
      for (let i = 0; i < N; i += stride) d += (d ? "L" : "M") + X(i).toFixed(1) + "," + Y(s.values[i]).toFixed(1);
      d += "L" + X(N - 1).toFixed(1) + "," + Y(s.values[N - 1]).toFixed(1);
      g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" stroke-linejoin="round" stroke-linecap="round"${s.dash ? ' stroke-dasharray="5 4"' : ""}/>`;
    }
    for (const mk of markers) {
      g += `<circle cx="${X(mk.i)}" cy="${Y(mk.y)}" r="4.5" fill="${mk.hollow ? "var(--surface)" : mk.color}" stroke="${mk.hollow ? mk.color : "var(--surface)"}" stroke-width="2"/>`;
    }
    g += `<g class="hover" style="display:none"><line class="vx" y1="${m.t}" y2="${m.t + ih}" stroke="var(--muted)" stroke-width="1"/>` +
         series.map(s => `<circle r="4" fill="${s.color}" stroke="var(--surface)" stroke-width="2"/>`).join("") + "</g>";
    g += `<rect x="${m.l}" y="${m.t}" width="${iw}" height="${ih}" fill="transparent" class="hit"/>`;
    host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img">${g}</svg><div class="tip"></div>`;

    const svg = host.querySelector("svg"), hov = svg.querySelector(".hover"), tipEl = host.querySelector(".tip");
    const vx = hov.querySelector(".vx"), dots = hov.querySelectorAll("circle");
    const move = ev => {
      const r = svg.getBoundingClientRect();
      const px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
      const t = T[0] + (px - m.l) / iw * (T[N - 1] - T[0]);
      let a = 0, b = N - 1;
      while (b - a > 1) { const c = (a + b) >> 1; if (T[c] <= t) a = c; else b = c; }
      const i = Math.abs(T[a] - t) <= Math.abs(T[b] - t) ? a : b;
      const x = X(i);
      hov.style.display = "";
      vx.setAttribute("x1", x); vx.setAttribute("x2", x);
      series.forEach((s, k) => { dots[k].setAttribute("cx", x); dots[k].setAttribute("cy", Y(s.values[i])); });
      tipEl.innerHTML = tip(i);
      tipEl.style.display = "block";
      const tw = tipEl.offsetWidth, left = x + 14 + tw > W ? x - 14 - tw : x + 14;
      tipEl.style.left = Math.max(0, left) + "px";
      tipEl.style.top = m.t + "px";
    };
    const leave = () => { hov.style.display = "none"; tipEl.style.display = "none"; };
    const hit = svg.querySelector(".hit");
    hit.addEventListener("mousemove", move); hit.addEventListener("touchmove", move, {passive: true});
    hit.addEventListener("mouseleave", leave); hit.addEventListener("touchend", leave);
  }
  draw();
  let w = host.clientWidth;
  new ResizeObserver(() => { if (host.clientWidth !== w) { w = host.clientWidth; draw(); } }).observe(host);
}
const tipRows = rows => rows.map(r => `<div class="row"><span>${r.sw ? `<i class="sw" style="background:${r.sw}"></i>` : ""}${esc(r.k)}</span><b>${r.v}</b></div>`).join("");

// ---------- portfolio value ----------
(() => {
  const series = S.map((s, k) => ({name: s.name, color: color(k), values: s.values}));
  series.push({name: "Money contributed", color: "var(--muted)", values: D.contributed, dash: true, width: 1.5});
  legend("legend-value", series.map(s => ({name: s.name, color: s.color, dash: s.dash})));
  lineChart("chart-value", {series, tip: i => {
    const order = S.map((s, k) => k).sort((a, b) => S[b].values[i] - S[a].values[i]);
    return `<div class="d">${fmtDate(T[i])}</div>` +
      tipRows(order.map(k => ({k: S[k].name, v: money(S[k].values[i]), sw: color(k)}))) +
      tipRows([{k: "Contributed", v: money(D.contributed[i])}]);
  }});
})();

// ---------- ahead / behind ----------
(() => {
  const sel = document.getElementById("ref-select");
  let def = S.findIndex(s => /\+ dividends/.test(s.name));
  if (def < 0) def = S.findIndex(s => /Invest every/.test(s.name));
  if (def < 0) def = 0;
  sel.innerHTML = S.map((s, k) => `<option value="${k}"${k === def ? " selected" : ""}>${esc(s.name)}</option>`).join("");
  const render = () => {
    const ref = +sel.value, rv = S[ref].values;
    const ks = S.map((_, k) => k).filter(k => k !== ref);
    const series = ks.map(k => ({name: S[k].name, color: color(k), values: S[k].values.map((v, i) => v - rv[i])}));
    legend("legend-diff", series.map(s => ({name: s.name, color: s.color})));
    lineChart("chart-diff", {series, zero: true, tip: i => `<div class="d">${fmtDate(T[i])} · vs ${esc(S[ref].name)}</div>` +
      tipRows(ks.map(k => ({k: S[k].name, v: signed(S[k].values[i] - rv[i]), sw: color(k)})))});
  };
  sel.addEventListener("change", render);
  render();
})();

// ---------- price + trades, activity log ----------
(() => {
  const sel = document.getElementById("trade-select");
  const traders = S.map((s, k) => k).filter(k => S[k].trades.length);
  let def = traders.find(k => !/^(Invest every|Bank only)/.test(S[k].name));
  if (def == null) def = traders[0] ?? 0;
  sel.innerHTML = S.map((s, k) => `<option value="${k}"${k === def ? " selected" : ""}>${esc(s.name)} (${s.trades.length} trades)</option>`).join("");
  document.getElementById("price-title").textContent = `${D.ticker} price and trades`;
  const render = () => {
    const k = +sel.value, s = S[k];
    const byDay = new Map();
    for (const t of s.trades) { if (!byDay.has(t.i)) byDay.set(t.i, []); byDay.get(t.i).push(t); }
    const markers = s.trades.map(t => ({i: t.i, y: t.price, color: color(k), hollow: t.side === "SELL"}));
    const hasSell = s.trades.some(t => t.side === "SELL");
    legend("legend-price", [{name: "Daily close", color: "var(--ink-2)"}, {name: "Buy (filled at open)", color: color(k)}]
      .concat(hasSell ? [{name: "Sell", color: color(k), hollow: true}] : []));
    lineChart("chart-price", {series: [{name: "Close", color: "var(--ink-2)", values: D.price, width: 1.5}], markers,
      tip: i => {
        let h = `<div class="d">${fmtDate(T[i])}</div>` + tipRows([{k: "Close", v: cur2.format(D.price[i])}]);
        for (const t of byDay.get(i) || [])
          h += tipRows([{k: `${t.side === "BUY" ? "Bought" : "Sold"} ${t.shares} @ ${cur2.format(t.price)}`, v: cur.format(t.shares * t.price), sw: color(k)}]) +
               (t.note ? `<div style="color:var(--muted)">${esc(t.note)}</div>` : "");
        return h;
      }});
    // activity table: trades + strategy/engine notes, newest first
    const rows = s.trades.map(t => ({i: t.i, type: t.side, text:
        `${t.shares} @ ${cur2.format(t.price)} = ${cur2.format(t.shares * t.price)} + ${cur2.format(t.fee)} fee` +
        (t.note ? ` — ${esc(t.note)}` : ""), cash: t.cash}))
      .concat(s.logs.map(l => ({i: l.i, type: /^Ex-dividend/.test(l.msg) ? "DIVIDEND" : "NOTE", text: esc(l.msg), cash: null})))
      .sort((a, b) => b.i - a.i);
    document.getElementById("log-title").textContent = `Activity — ${s.name}`;
    document.getElementById("log").innerHTML =
      `<thead><tr><th>Date</th><th>Type</th><th>Detail</th><th>Bank after</th></tr></thead><tbody>` +
      (rows.length ? rows.map(r => `<tr><td>${fmtDate(T[r.i])}</td><td><span class="tag">${r.type}</span></td><td>${r.text}</td><td>${r.cash == null ? "" : money(r.cash)}</td></tr>`).join("")
                   : `<tr><td colspan="4" style="color:var(--muted)">No activity — this strategy never trades.</td></tr>`) + "</tbody>";
  };
  sel.addEventListener("change", render);
  render();
})();

// ---------- assumptions ----------
document.getElementById("notes").innerHTML = [
  `Contributions of ${cur.format(st.contribution)} land in the bank ${st.frequency}; the bank pays ${st.bank_rate}% p.a., accrued daily on whatever is uninvested.`,
  `Strategies decide after each day's close using prices up to that close. Orders fill ${st.fill === "close" ? "at that same close" : "at the next trading day's open"}, in whole shares, with a ${cur2.format(st.fee)} fee per trade.`,
  st.drp ? `Dividends are reinvested through the fund's DRP where it has one (new units at the ex-date close, no brokerage); otherwise the cash lands in the bank ~${st.pay_lag} days after the ex-date.`
         : `Dividends are not reinvested automatically: you receive them if you held shares going into the ex-dividend date, and the cash lands in the bank ~${st.pay_lag} days later. A strategy can choose to invest that cash.`,
  `Prices are actual daily ${D.symbol} closes from Yahoo Finance (not dividend-adjusted, since dividends are modelled separately).`,
  st.tax ? `Tax: interest and dividends are taxed at your marginal rate on top of ${cur.format(st.income)} other income (2026-27 rates, LITO and Medicare levy, every year); franking credits are refunded if they exceed your tax. Each year's bill is paid from the bank on 31 October. Capital gains use the ${st.cgt === "new" ? "new rules (CPI-indexed cost base, 30% minimum on real gains)" : "old 50% discount"}, sold first-in first-out. Real bank rates also changed over this period; a flat ${st.bank_rate}% is a simplification.`
         : `Taxes are ignored. Real bank rates also changed over this period; a flat ${st.bank_rate}% is a simplification.`,
  `Past performance isn't a forecast. A rule tuned to fit this history may not hold up in the future.`,
].map(s => `<li>${s}</li>`).join("");
</script>
</body>
</html>
"""
