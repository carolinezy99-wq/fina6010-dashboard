const TAG = {
  "Financial Times": "FT",
  Bloomberg: "BBG",
  Reuters: "RTRS",
  WSJ: "WSJ",
  CNBC: "CNBC",
  BBC: "BBC",
  AP: "AP",
  Nikkei: "Nikkei",
  SCMP: "SCMP",
  MarketWatch: "MW",
  CoinDesk: "CoinDesk",
  Guardian: "Guardian",
  Axios: "Axios",
  NPR: "NPR",
  ABC: "ABC",
  Economist: "Economist",
  "Yahoo Finance": "YF",
  "Google News": "GN",
};

let SNAP = null;
let activeId = "equities";
let mountedClass = "";

const esc = (s) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const fmtPct = (v) => {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const n = Math.round(Number(v) * 100 + Number.EPSILON) / 100;
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}${Math.abs(n).toFixed(2)}%`;
};

const dirClass = (v) => {
  if (v === null || v === undefined) return "flat";
  if (v > 0.005) return "up";
  if (v < -0.005) return "down";
  return "flat";
};

// Venues that stream tick-by-tick spot. Coinbase quotes true USD; BNB is not
// listed there, so it comes off OKX's USDT book instead.
const LIVE_FEED = {
  btc: { venue: "coinbase", product: "BTC-USD", label: "Coinbase BTC/USD" },
  eth: { venue: "coinbase", product: "ETH-USD", label: "Coinbase ETH/USD" },
  sol: { venue: "coinbase", product: "SOL-USD", label: "Coinbase SOL/USD" },
  xrp: { venue: "coinbase", product: "XRP-USD", label: "Coinbase XRP/USD" },
  doge: { venue: "coinbase", product: "DOGE-USD", label: "Coinbase DOGE/USD" },
  bnb: { venue: "okx", product: "BNB-USDT", label: "OKX BNB/USDT" },
};

const TICKS = {};

function fmtCoin(v) {
  const d = Math.abs(v) >= 10 ? 2 : 4;
  return v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function withTick(r) {
  const t = TICKS[r.id];
  if (!t) return r;
  return { ...r, last: t.price, lastDisplay: fmtCoin(t.price), dayPct: t.pct == null ? r.dayPct : t.pct };
}

function liveChart(r) {
  // official daily fixings have no intraday leg, so they offer a shorter menu
  const ranges = r.ranges && r.ranges.length ? r.ranges : ["1D", "5D", "1M", "3M", "YTD"];
  const wanted = r.cls === "crypto" || r.cls === "fx" ? "5D" : "1M";
  const def = ranges.includes(wanted) ? wanted : ranges[0];
  const btns = ranges
    .map((k) => `<button type="button" data-range="${k}" class="${k === def ? "on" : ""}">${k}</button>`)
    .join("");
  const feed = LIVE_FEED[r.id];
  const site = r.chartSite || (r.cls === "crypto" ? "CoinGecko" : "Yahoo Finance");
  const src = feed
    ? `${feed.label} candles + live ticks`
    : r.cls === "crypto"
      ? "CoinGecko spot, USD"
      : r.ranges && !r.ranges.includes("1D")
        ? r.exchangeFill
          ? `${site} — official daily fixing · ${r.exchangeFill}`
          : `${site} — official daily yield fixing`
        : r.cls === "fx"
          ? "Yahoo Finance API — near-real-time FX pair"
          : r.cls === "commodities"
            ? "Yahoo Finance API — front-month futures print"
            : r.kind === "yield"
              ? "Yahoo Finance API — benchmark yield"
              : "Yahoo Finance API — cash index print";
  const jump = [
    r.chartUrl
      ? `<a class="lc-jump" href="${esc(r.chartUrl)}" target="_blank" rel="noopener">Open on ${esc(site)} &#8599;</a>`
      : "",
    r.crossCheck && r.crossCheck.url
      ? `<a class="lc-jump cross-check" href="${esc(r.crossCheck.url)}" target="_blank" rel="noopener">${esc(r.crossCheck.label || "Cross-check official data")} &#8599;</a>`
      : "",
  ].join("");
  const sourceNote =
    r.crossCheck && r.crossCheck.note ? `<span class="source-note">${esc(r.crossCheck.note)}</span>` : "";
  const own = `<div class="lc-box" data-pane="own">
    <div class="range-row">${btns}${jump}</div>
    <div class="lc-host" id="lc-${esc(r.id)}"></div>
    <div class="chart-meta">${esc(src)} · HKT · auto-refresh 30s <span class="lc-stamp" data-stamp>loading…</span>${sourceNote}</div>
  </div>`;
  return own;
}

const VIEW = {};

function refMarkup(r) {
  const ref = r.ref;
  const link = `<a class="lc-jump" href="${esc(ref.url)}" target="_blank" rel="noopener">Open on ${esc(ref.site)} &#8599;</a>`;
  if (ref.kind === "fred") {
    return `<a class="ref-img" href="${esc(ref.url)}" target="_blank" rel="noopener" title="Open ${esc(ref.symbol)} on FRED">
        <img src="${esc(ref.img)}" alt="${esc(ref.what)} — FRED chart" onerror="this.closest('.ref-box').classList.add('failed')" />
      </a>
      <p class="ref-fail">FRED did not return the chart just now. ${link}</p>
      <div class="chart-meta">Source: FRED, Federal Reserve Bank of St. Louis · ${esc(ref.what)} · series ${esc(ref.symbol)} · ${esc(ref.freq)} ${link}</div>`;
  }
  return `<div class="tradingview-widget-container"><div class="tradingview-widget-container__widget"></div></div>
    <div class="chart-meta">Source: TradingView · ${esc(ref.what)} (${esc(ref.symbol)}) · live ${link}</div>`;
}

function mountRef(box, r) {
  if (box.dataset.mounted) return;
  box.dataset.mounted = "1";
  box.innerHTML = refMarkup(r);
  if (r.ref.kind !== "tv") return;
  const sc = document.createElement("script");
  sc.src = "https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js";
  sc.async = true;
  sc.text = JSON.stringify({
    symbol: r.ref.symbol,
    width: "100%",
    height: 300,
    locale: "en",
    dateRange: "1M",
    colorTheme: "light",
    isTransparent: true,
    autosize: false,
    largeChartUrl: r.ref.url,
  });
  box.querySelector(".tradingview-widget-container").appendChild(sc);
}

function showView(id, view) {
  const card = document.querySelector(`[data-asset="${id}"]`);
  const row = SNAP && (SNAP.assets || []).find((a) => a.id === id);
  if (!card || !row || !row.ref) return;
  VIEW[id] = view;
  card.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  card.querySelectorAll("[data-pane]").forEach((p) => {
    p.hidden = p.dataset.pane !== view;
  });
  if (view === "ref") mountRef(card.querySelector('[data-pane="ref"]'), row);
}

const LC = {};

function destroyCharts() {
  Object.keys(LC).forEach((id) => {
    try {
      LC[id].ro.disconnect();
      LC[id].chart.remove();
    } catch (err) {
      /* ignore */
    }
    delete LC[id];
  });
}

function precisionFor(r, level) {
  if (r.kind === "yield") return 3;
  const px = Math.abs(Number(level ?? r.last) || 0);
  if (!px) return 2;
  if (px < 1) return 5;
  if (px < 20) return 4;
  return 2;
}

function applyPrecision(st, r, level) {
  const p = precisionFor(r, level);
  if (st.precision === p) return;
  st.precision = p;
  st.series.applyOptions({ priceFormat: { type: "price", precision: p, minMove: 10 ** -p } });
}

function ensureChart(r) {
  const el = document.getElementById(`lc-${r.id}`);
  if (!el || !window.LightweightCharts) return null;
  if (LC[r.id]) return LC[r.id];
  const chart = LightweightCharts.createChart(el, {
    width: el.clientWidth || 320,
    height: 280,
    layout: { background: { color: "#ffffff" }, textColor: "#333333", attributionLogo: false },
    grid: { vertLines: { color: "#edf0f5" }, horzLines: { color: "#edf0f5" } },
    rightPriceScale: { borderColor: "#ccc1b7" },
    timeScale: { borderColor: "#ccc1b7", timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 },
    localization: { locale: "en-GB" },
  });
  const series = chart.addLineSeries({
    color: "#990f3d",
    lineWidth: 2,
    priceFormat: { type: "price", precision: precisionFor(r), minMove: 10 ** -precisionFor(r) },
  });
  const ro = new ResizeObserver(() => {
    if (el.clientWidth) chart.applyOptions({ width: el.clientWidth });
  });
  ro.observe(el);
  if (r.chartUrl) {
    // a plain click jumps out; dragging still pans the chart
    let start = null;
    el.style.cursor = "pointer";
    el.title = `Open on ${r.chartSite || "Yahoo Finance"}`;
    el.addEventListener("mousedown", (e) => {
      start = { x: e.clientX, y: e.clientY, t: Date.now() };
    });
    el.addEventListener("mouseup", (e) => {
      if (!start) return;
      const moved = Math.hypot(e.clientX - start.x, e.clientY - start.y);
      const held = Date.now() - start.t;
      start = null;
      if (moved < 5 && held < 400) window.open(r.chartUrl, "_blank", "noopener");
    });
  }
  LC[r.id] = { chart, series, range: null, ro };
  return LC[r.id];
}

async function loadRange(r, rangeKey, quiet) {
  const box = document.querySelector(`[data-asset="${r.id}"]`);
  if (box) {
    box.querySelectorAll("[data-range]").forEach((b) => b.classList.toggle("on", b.dataset.range === rangeKey));
  }
  const st = ensureChart(r);
  if (!st) return;
  st.range = rangeKey;
  const stamp = box ? box.querySelector("[data-stamp]") : null;
  try {
    const res = await fetch(`/api/chart?id=${encodeURIComponent(r.id)}&range=${encodeURIComponent(rangeKey)}`, {
      cache: "no-store",
    });
    const data = await res.json();
    if (LC[r.id] !== st || st.range !== rangeKey) return;
    let pts = (data.points || []).filter((p) => p && p.time && p.value != null);
    // the library renders unix stamps as UTC; HKT is a fixed +8 so shifting intraday
    // stamps makes the axis read in the same clock as the rest of the board
    const shifted = rangeKey === "1D" || rangeKey === "5D" || r.cls === "crypto";
    if (shifted) {
      pts = pts.map((p) => ({ time: p.time + 28800, value: p.value }));
    }
    if (pts.length) {
      st.series.setData(pts);
      st.intraday = shifted;
      st.step = data.step || null;
      st.lastTime = pts[pts.length - 1].time;
      st.seeded = false;
      applyPrecision(st, r, pts[pts.length - 1].value);
    }
    if (!quiet) st.chart.timeScale().fitContent();
    const streaming = TICKS[r.id] && Date.now() - TICKS[r.id].at < 15000;
    if (stamp && !streaming) {
      const asOf = data.asOf ? fmtTime(data.asOf * 1000) : "";
      stamp.textContent = data.stale
        ? `· cached chart, last verified ${asOf || "earlier"} HKT`
        : asOf
          ? `· quote ${asOf} HKT`
          : "· no fresh quote";
    }
  } catch (err) {
    if (LC[r.id] !== st) return;
    if (stamp) stamp.textContent = "· feed error";
    console.error(err);
  }
}

function defaultRange(r) {
  const ranges = r.ranges && r.ranges.length ? r.ranges : ["1D", "5D", "1M", "3M", "YTD"];
  const wanted = r.cls === "crypto" || r.cls === "fx" ? "5D" : "1M";
  return ranges.includes(wanted) ? wanted : ranges[0];
}

function mountLiveCharts(rows) {
  destroyCharts();
  rows.forEach((r) => {
    loadRange(r, defaultRange(r));
    if (VIEW[r.id] === "ref") showView(r.id, "ref");
  });
}

function refreshCharts() {
  if (!SNAP || document.hidden) return;
  Object.keys(LC).forEach((id) => {
    // a streaming card already has the freshest point; polling it only burns
    // quota, unless its history never arrived and the line is stream-only
    if (TICKS[id] && Date.now() - TICKS[id].at < 60000 && !LC[id].seeded) return;
    const row = (SNAP.assets || []).find((a) => a.id === id);
    if (row) loadRange(row, LC[id].range || defaultRange(row), true);
  });
}

function applyTick(id, price, pct) {
  if (!Number.isFinite(price)) return;
  TICKS[id] = { price, pct: Number.isFinite(pct) ? pct : null, at: Date.now() };
  const card = document.querySelector(`[data-asset="${id}"]`);
  if (card) {
    const mv = card.querySelector(".mv");
    const shown = TICKS[id].pct;
    if (mv && shown != null) {
      mv.className = `mv ${dirClass(shown)}`;
      mv.textContent = fmtPct(shown);
    }
    const lastDd = card.querySelector('[data-field="last"]');
    if (lastDd) lastDd.textContent = `$${fmtCoin(price)}`;
    const dayDd = card.querySelector('[data-field="day"]');
    if (dayDd && shown != null) {
      dayDd.className = dirClass(shown);
      dayDd.textContent = fmtPct(shown);
    }
    const stamp = card.querySelector("[data-stamp]");
    if (stamp) {
      const note = LC[id] && LC[id].seeded ? " · stream only, history pending" : "";
      stamp.textContent = `· live tick ${fmtClock(TICKS[id].at)} HKT${note}`;
    }
  }
  const st = LC[id];
  if (!st) return;
  const now = Math.floor(Date.now() / 1000) + 28800;
  if (st.lastTime == null) {
    // history feed came back empty, so draw the line from the stream instead
    applyPrecision(st, { id }, price);
    st.series.setData([{ time: now, value: price }]);
    st.intraday = true;
    st.seeded = true;
  } else {
    // exchange candles: a tick revises the open candle, and opens the next one on the boundary
    const slot = st.step ? Math.floor((now - 28800) / st.step) * st.step + 28800 : now;
    st.series.update({ time: st.intraday ? Math.max(slot, st.lastTime) : st.lastTime, value: price });
  }
  if (st.intraday) {
    const slot = st.step ? Math.floor((now - 28800) / st.step) * st.step + 28800 : now;
    st.lastTime = Math.max(slot, st.lastTime ?? slot);
  }
}

function fmtClock(ms) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Hong_Kong",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ms));
}

function openSocket(url, onOpen, onMessage) {
  let sock = null;
  let wait = 1000;
  const connect = () => {
    try {
      sock = new WebSocket(url);
    } catch (err) {
      setTimeout(connect, wait);
      return;
    }
    sock.addEventListener("open", () => {
      wait = 1000;
      if (onOpen) onOpen(sock);
    });
    sock.addEventListener("message", (e) => {
      try {
        onMessage(JSON.parse(e.data));
      } catch (err) {
        /* ignore malformed frame */
      }
    });
    sock.addEventListener("close", () => {
      setTimeout(connect, wait);
      wait = Math.min(wait * 2, 30000);
    });
    sock.addEventListener("error", () => sock.close());
  };
  connect();
}

function startTickers() {
  if (!("WebSocket" in window)) return;
  const byProduct = {};
  Object.entries(LIVE_FEED).forEach(([id, f]) => {
    byProduct[f.product] = id;
  });
  const cb = Object.values(LIVE_FEED)
    .filter((f) => f.venue === "coinbase")
    .map((f) => f.product);
  openSocket(
    "wss://ws-feed.exchange.coinbase.com",
    (sock) => sock.send(JSON.stringify({ type: "subscribe", product_ids: cb, channels: ["ticker"] })),
    (msg) => {
      if (msg.type !== "ticker") return;
      const id = byProduct[msg.product_id];
      if (!id) return;
      const price = parseFloat(msg.price);
      const open = parseFloat(msg.open_24h);
      applyTick(id, price, open > 0 ? (price / open - 1) * 100 : null);
    },
  );
  const okx = Object.values(LIVE_FEED)
    .filter((f) => f.venue === "okx")
    .map((f) => ({ channel: "tickers", instId: f.product }));
  if (okx.length) {
    openSocket(
      "wss://ws.okx.com:8443/ws/v5/public",
      (sock) => {
        sock.send(JSON.stringify({ op: "subscribe", args: okx }));
        // OKX drops idle connections after 30s
        const ping = setInterval(() => {
          if (sock.readyState === 1) sock.send("ping");
          else clearInterval(ping);
        }, 25000);
      },
      (msg) => {
        const d = (msg.data || [])[0];
        if (!d || !d.instId) return;
        const id = byProduct[d.instId];
        if (!id) return;
        const price = parseFloat(d.last);
        const open = parseFloat(d.open24h);
        applyTick(id, price, open > 0 ? (price / open - 1) * 100 : null);
      },
    );
  }
}

function renderClocks() {
  const now = new Date();
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Hong_Kong",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(now);
  const date = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Hong_Kong",
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(now);
  document.getElementById("clocks").innerHTML = `<div class="clock">
    <div class="city">Hong Kong</div>
    <div class="time">${time}</div>
    <div class="date">${date}</div>
  </div>`;
}

function stats(r) {
  const dayLabel = r.cls === "crypto" ? "24 hours" : r.sessionCode === "open" ? "Today" : "Latest session";
  const last = r.cls === "crypto" && r.lastDisplay ? `$${r.lastDisplay}` : r.lastDisplay || "—";
  const dayExtra =
    r.kind === "yield" && r.dayBps != null ? ` (${r.dayBps > 0 ? "+" : ""}${r.dayBps} bp)` : "";
  return `<dl class="stats">
    <div><dt>${r.kind === "yield" ? "Yield level" : "Last price"}</dt><dd data-field="last">${esc(last)}</dd></div>
    <div><dt>${dayLabel}</dt><dd data-field="day" class="${dirClass(r.dayPct)}">${fmtPct(r.dayPct)}${esc(dayExtra)}</dd></div>
    <div><dt>${r.cls === "crypto" ? "7 days" : "5 observations"}</dt><dd class="${dirClass(r.weekPct)}">${fmtPct(r.weekPct)}</dd></div>
    <div><dt>1 month</dt><dd class="${dirClass(r.monthPct)}">${fmtPct(r.monthPct)}</dd></div>
  </dl>`;
}

function basisLine(r) {
  if (r.cls === "crypto") {
    const feed = LIVE_FEED[r.id];
    return `24h move and price: ${feed ? feed.label : "CoinGecko"} live stream (CoinGecko while it connects).`;
  }
  const asOf = r.quoteTime ? ` · quote ${fmtTime(r.quoteTime * 1000)} HKT` : "";
  return `${esc(r.changeBasis || "previous published close")} · ${esc(r.source || "")}${esc(asOf)}`;
}

function sessionBadge(code, label) {
  const cls = code === "open" ? "open" : code === "pre" || code === "post" ? "amber" : "closed";
  return `<span class="sess ${cls}">${esc(label)}</span>`;
}

function fmtTime(raw) {
  if (!raw) return "";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Hong_Kong",
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
    hour12: false,
  }).format(d);
}

function ago(ts) {
  if (!ts) return "";
  const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  return hrs < 24 ? `${hrs}h ago` : `${Math.round(hrs / 24)}d ago`;
}

const WIRES_SHOWN = 5;

function sourceRefs(items) {
  return (items || []).filter(n => n && n.link).map(n => `<a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.source)} · ${esc(n.date || "source") } ↗</a>`).join("");
}

function policyComparison(banks) {
  if (!banks || !banks.length) return "";
  return `<div class="policy-grid">${banks.map(b => `<div class="policy-side"><strong>${esc(b.currency)} · ${esc(b.bank)}</strong><span class="evidence-status">${esc(b.status)}</span><p>${esc(b.text)}</p>${b.source ? sourceRefs([b.source]) : ""}</div>`).join("")}</div>`;
}

function assetReading(r) {
  if (!r.reading) return "";
  return `${r.verification ? `<div class="verification"><b>${esc(r.verification.status)}</b><p>${esc(r.verification.text)}</p>${r.verification.source ? sourceRefs([r.verification.source]) : ""}</div>` : ""}<details class="asset-reading"><summary>Evidence & explanation · ${esc(r.reading.confidence)} confidence</summary>${readBlock(r.reading)}</details>`;
}

function wireLine(n) {
  const when = n.date || (n.ts ? new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Hong_Kong',day:'2-digit',month:'short'}).format(new Date(n.ts*1000)) : "");
  return `<a class="wire" href="${esc(n.link)}" target="_blank" rel="noopener noreferrer"><span class="wire-meta"><em>${esc(n.source || "Source")}</em><time>${esc(when)}</time></span><strong>${esc(n.title)} ↗</strong></a>`;
}

function headlines(r, open) {
  const items = (r.headlines || []).filter(n => n.title && n.link);
  if (!items.length) return `<p class="no-wire">No article in this week’s sources discusses this market.</p>`;
  const head=items.slice(0,WIRES_SHOWN).map(n=>wireLine(n)).join("");
  const rest=items.slice(WIRES_SHOWN);
  return head + (rest.length ? `<details class="wire-more"${open ? " open" : ""}><summary>${rest.length} more</summary>${rest.map(n=>wireLine(n)).join("")}</details>` : "");
}

function wireLabel(r) {
  const n = (r.headlines || []).filter(h => h.title && h.link).length;
  return `Related news · ${esc(r.newsWindow || "past 7 days")}${n ? ` · ${n}` : ""}`;
}

function renderTabs(classes) {
  document.getElementById("jump").innerHTML = classes
    .map((c) => `<button type="button" class="${c.id === activeId ? "on" : ""}" data-id="${esc(c.id)}">${esc(c.title)}</button>`)
    .join("");
}

function cardMarkup(raw) {
  const r = withTick(raw);
  const closed = r.sessionCode && r.sessionCode !== "open";
  const mover = r.moverRank ? `<span class="pill mover">Biggest mover ${r.moverRank}</span>` : "";
  const extra =
    r.cls === "crypto" && (r.mcap || r.volume)
      ? `<p class="px">Market cap ${esc(r.mcap || "—")} · 24h volume ${esc(r.volume || "—")}</p>`
      : "";
  return `<article class="card ${closed ? "is-closed" : ""} ${r.moverRank ? "is-mover" : ""}" data-asset="${esc(r.id)}">
        <div class="card-h">
          <div>
            <h3>${esc(r.label)}</h3>
            <div class="tags">${sessionBadge(r.sessionCode, r.sessionLabel)} ${mover}</div>
          </div>
          <div class="mv ${dirClass(r.dayPct)}">${fmtPct(r.dayPct)}</div>
        </div>
        ${stats(r)}
        <p class="data-basis">${basisLine(r)}</p>
        ${extra}
        ${liveChart(r)}
        <div class="bg-block">
          <div class="bg-label">${wireLabel(r)}</div>
          ${headlines(r)}
        </div>
      </article>`;
}

function patchCards(rows) {
  rows.forEach((raw) => {
    const r = withTick(raw);
    const card = document.querySelector(`[data-asset="${r.id}"]`);
    if (!card) return;
    const closed = r.sessionCode && r.sessionCode !== "open";
    card.classList.toggle("is-closed", !!closed);
    card.classList.toggle("is-mover", !!r.moverRank);
    const mv = card.querySelector(".mv");
    if (mv) {
      mv.className = `mv ${dirClass(r.dayPct)}`;
      mv.textContent = fmtPct(r.dayPct);
    }
    const tags = card.querySelector(".tags");
    const mover = r.moverRank ? `<span class="pill mover">Biggest mover ${r.moverRank}</span>` : "";
    if (tags) tags.innerHTML = `${sessionBadge(r.sessionCode, r.sessionLabel)} ${mover}`;
    const dl = card.querySelector("dl.stats");
    if (dl) dl.outerHTML = stats(r);
    const basis = card.querySelector(".data-basis");
    if (basis) basis.innerHTML = basisLine(r);
    const reading = card.querySelector(".asset-analysis");
    if (reading) reading.remove();
    const wires = card.querySelector(".bg-block");
    if (wires) {
      const open = !!wires.querySelector("details.wire-more[open]");
      wires.innerHTML = `<div class="bg-label">${wireLabel(r)}</div>${headlines(r, open)}`;
    }
  });
}

const DAYSEL = {};

function barsChart(bars, caption) {
  if (!bars || !bars.length) return "";
  const top = Math.max(...bars.filter((b) => b.pct != null).map((b) => Math.abs(b.pct)), 0.01);
  const rows = bars
    .map((b) => {
      if (b.pct == null) {
        return `<div class="bar-row is-gap">
        <span class="bl">${esc(b.label)}</span>
        <span class="bt gap-note">${esc(b.note || "No reading")}</span>
        <span class="bv flat">—</span>
      </div>`;
      }
      const w = Math.max(2, (Math.abs(b.pct) / top) * 100);
      const bp = b.bp != null ? ` <small>${b.bp > 0 ? "+" : ""}${b.bp}bp</small>` : "";
      return `<div class="bar-row${b.focus ? " is-focus" : ""}">
        <span class="bl">${esc(b.label)}</span>
        <span class="bt">
          <span class="half neg">${b.pct < 0 ? `<i style="width:${w}%"></i>` : ""}</span>
          <span class="half pos">${b.pct >= 0 ? `<i style="width:${w}%"></i>` : ""}</span>
        </span>
        <span class="bv ${dirClass(b.pct)}">${fmtPct(b.pct)}${bp}</span>
      </div>`;
    })
    .join("");
  return `<figure class="bars"><figcaption>${esc(caption)}</figcaption>${rows}</figure>`;
}

function pathChart(path, name) {
  if (!path || !path.length) return "";
  const top = Math.max(...path.map((p) => Math.abs(p.pct || 0)), 0.01);
  const cols = path
    .map((p) => {
      if (p.pct == null) {
        return `<div class="pcol is-closed"><span class="pv">closed</span><span class="ptrack"></span><span class="pl">${esc(p.label)}</span></div>`;
      }
      const h = Math.max(3, (Math.abs(p.pct) / top) * 100);
      return `<div class="pcol" title="${esc(p.label)} ${fmtPct(p.pct)}">
        <span class="pv ${dirClass(p.pct)}">${fmtPct(p.pct)}</span>
        <span class="ptrack"><span class="phalf up">${p.pct >= 0 ? `<i style="height:${h}%"></i>` : ""}</span><span class="phalf dn">${p.pct < 0 ? `<i style="height:${h}%"></i>` : ""}</span></span>
        <span class="pl">${esc(p.label)}</span>
      </div>`;
    })
    .join("");
  return `<figure class="path"><figcaption>${esc(name)}, day by day this week</figcaption><div class="pcols">${cols}</div></figure>`;
}

function readBlock(d) {
  const explanation = d.explanation || [d.catalyst, d.mechanism].filter(Boolean).join(" ");
  const next = d.next || "";
  const refs = (d.evidence || [])
    .filter((e) => e && e.link)
    .map(
      (e) =>
        `<li><a href="${esc(e.link)}" target="_blank" rel="noopener"><span class="src-name">${esc(e.source || "")}</span>${
          e.date ? ` <span class="src-date">${esc(e.date)}</span>` : ""
        } ${esc(e.title || "")} &#8599;</a></li>`,
    )
    .join("");
  return `<div class="read">
    <h4>4. Explanation</h4>
    <p class="why">${esc(explanation)}</p>
    ${refs ? `<div class="why-src"><span class="src-label">Source</span><ul>${refs}</ul></div>` : ""}
    <h4 class="nx">5. What's next</h4>
    <p class="next">${esc(next)}</p>
  </div>`;
}

function focusPct(d) {
  const hit = (d.bars || []).find((b) => b.focus);
  return hit ? hit.pct : null;
}

function dayChip(d, on, top) {
  const bits = (d.label || "").split(" ");
  const pct = focusPct(d);
  if (!d.move) {
    return `<button type="button" class="day-chip is-empty" disabled>
      <span class="dow">${esc(bits[0] || "")}</span><span class="dom">${esc(bits.slice(1).join(" "))}</span>
      <span class="chip-name">${d.state === "ahead" ? "Not yet" : "No print"}</span>
    </button>`;
  }
  const h = pct == null ? 0 : Math.max(8, (Math.abs(pct) / top) * 100);
  return `<button type="button" class="day-chip ${on ? "on" : ""} is-${esc(d.state)}" data-day="${esc(d.date)}">
    <span class="dow">${esc(bits[0] || "")}${d.state === "today" ? " · today" : ""}</span>
    <span class="dom">${esc(bits.slice(1).join(" "))}</span>
    <span class="chip-name">${esc(d.focus)}</span>
    <span class="chip-move ${dirClass(pct)}">${fmtPct(pct)}</span>
    <span class="chip-meter"><i class="${dirClass(pct)}" style="width:${h}%"></i></span>
  </button>`;
}

function dayPanel(brief, clsId) {
  const days = brief.days || [];
  const done = days.filter((d) => d.move);
  const sel = done.find((d) => d.date === DAYSEL[clsId]) || done[done.length - 1];
  const top = Math.max(...done.map((d) => Math.abs(focusPct(d) || 0)), 0.01);
  const chips = days.map((d) => dayChip(d, sel && d.date === sel.date, top)).join("");
  const detail = sel
    ? `<div class="day-detail">
        <div class="hero">
          <p class="hero-k">1. Report</p>
          <p class="hero-name">${esc(sel.focus)}</p>
          <p class="hero-k">2. Move</p>
          <p class="hero-move ${dirClass(focusPct(sel))}">${esc(sel.move)}</p>
          <p class="hero-k">3. Term</p>
          <p class="term-line">${esc(sel.term || "Daily (close-to-close)")}</p>
          ${barsChart(sel.bars, `${sel.label}: percentage change`)}
        </div>
        ${readBlock(sel)}
      </div>`
    : `<p class="quiet pad">No completed session this week yet.</p>`;
  return `<div class="panel panel-day">
    <div class="panel-band"><b>Daily</b><span>One biggest mover per session — click a day</span></div>
    <div class="day-strip${days.length === 7 ? " is-7" : ""}">${chips}</div>
    ${detail}
  </div>`;
}

function weekPanel(week) {
  if (!week || !week.move) {
    return `<div class="panel panel-week">
      <div class="panel-band"><b>This week</b><span>Biggest mover over the week</span></div>
      <p class="quiet pad">${esc((week && week.explanation) || "The weekly path is not available yet.")}</p>
    </div>`;
  }
  const pct = focusPct(week);
  const path = week.path || [];
  const top = Math.max(...path.map((p) => Math.abs(p.pct || 0)), 0.01);
  const strip = path
    .map((p) => {
      if (p.pct == null) {
        return `<div class="day-chip is-empty"><span class="dow">${esc(p.label)}</span><span class="chip-name">${p.ahead ? "Not yet" : "Closed"}</span></div>`;
      }
      const h = Math.max(8, (Math.abs(p.pct) / top) * 100);
      return `<div class="day-chip">
        <span class="dow">${esc(p.label)}</span>
        <span class="dom">${p.today ? "So far" : "Session"}</span>
        <span class="chip-name">${esc(week.focus)}</span>
        <span class="chip-move ${dirClass(p.pct)}">${fmtPct(p.pct)}</span>
        <span class="chip-meter"><i class="${dirClass(p.pct)}" style="width:${h}%"></i></span>
      </div>`;
    })
    .join("");
  return `<div class="panel panel-week">
    <div class="panel-band"><b>This week</b><span>Biggest mover over the week</span></div>
    <div class="day-strip${path.length === 7 ? " is-7" : ""}">${strip}</div>
    <div class="week-body">
      <div class="hero">
        <p class="hero-k">1. Report</p>
        <p class="hero-name">${esc(week.focus)}</p>
        <p class="hero-k">2. Move</p>
        <p class="hero-move ${dirClass(pct)}">${esc(week.move)}</p>
        <p class="hero-k">3. Term</p>
        <p class="term-line">${esc(week.term || "Week-to-date (from daily closes)")}</p>
        ${barsChart(week.bars, "Move since Monday: percentage change")}
      </div>
      ${readBlock(week)}
    </div>
  </div>`;
}

function briefMarkup(brief, clsId) {
  if (!brief) return "";
  return `<section class="brief" data-brief="${esc(clsId)}">
    <div class="brief-h">
      <h2>${esc(brief.title || "Why it moved")}</h2>
      <p>${esc(brief.note || "")}</p>
    </div>
    <div class="brief-grid">
      ${dayPanel(brief, clsId)}
      ${weekPanel(brief.week)}
    </div>
  </section>`;
}

function briefFor(data, clsId) {
  return (data.briefs && data.briefs[clsId]) || null;
}

function renderBoard(data, force) {
  const cls = (data.classes || []).find((c) => c.id === activeId) || data.classes[0];
  if (!cls) return;
  activeId = cls.id;
  const rows = (data.assets || [])
    .filter((a) => a.cls === cls.id)
    .sort((a, b) => (a.moverRank || 99) - (b.moverRank || 99));
  renderTabs(data.classes);
  if (!force && mountedClass === cls.id && document.querySelector("[data-asset]")) {
    const existing = document.querySelector(".brief");
    const brief = briefFor(data, cls.id);
    if (brief) {
      if (existing) existing.outerHTML = briefMarkup(brief, cls.id);
      else document.getElementById("board").insertAdjacentHTML("afterbegin", briefMarkup(brief, cls.id));
    } else if (existing) existing.remove();
    patchCards(rows);
    return;
  }
  mountedClass = cls.id;
  const sessCode = (cls.session || "CLOSED").toLowerCase();
  const sessCls = sessCode === "open" ? "open" : sessCode === "mixed" ? "pre" : "closed";
  const cards = rows.map(cardMarkup).join("");
  const brief = briefMarkup(briefFor(data, cls.id), cls.id);
  document.getElementById("board").innerHTML = `${brief}<section class="book">
    <div class="book-h">
      <div>
        <h2>${esc(cls.title)}</h2>
        <p class="book-sub">${esc(cls.sub || "")}</p>
      </div>
      <div class="book-meta">${sessionBadge(sessCls === "open" ? "open" : sessCls === "pre" ? "pre" : "closed", cls.session || "CLOSED")}</div>
    </div>
    <div class="cards">${cards}</div>
  </section>`;
  mountLiveCharts(rows);
}

const MOOD_WORD = {
  "risk-on": "Broad confidence",
  "risk-off": "Broad caution",
  mixed: "No clear market-wide signal",
};
const VOTE_WORD = { on: "supports confidence", off: "supports caution", flat: "neutral" };

function renderMood() {
  const el = document.getElementById("mood");
  if (!el) return;
  el.className = "mood";
  el.innerHTML = "";
}

function renderMeta(data) {
  document.getElementById("fresh").textContent = `Data snapshot ${data.updatedAtHkt || "—"} · page checks every ${data.autoRefreshSec || 15}s`;
  document.getElementById("pulse").classList.toggle("off", data.status === "error");
  renderMood(data.pulse || {});
  document.getElementById("sources").innerHTML = `<h2>Sources</h2>${(data.sources || [])
    .map((s) => `<p><strong>${esc(s.name)}</strong> — ${esc(s.covers)}. ${esc(s.lag)}.</p>`)
    .join("")}`;
}

async function load() {
  try {
    const res = await fetch("/api/snapshot", { cache: "no-store" });
    const data = await res.json();
    if (!data.assets || !data.assets.length) return;
    SNAP = data;
    renderMeta(data);
    renderBoard(data);
  } catch (err) {
    document.getElementById("fresh").textContent = "Feed error";
    console.error(err);
  }
}

function toggleFs() {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen();
  else document.exitFullscreen();
}

document.getElementById("fs").addEventListener("click", toggleFs);
document.getElementById("reload")?.addEventListener("click", () => load());
document.getElementById("jump").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-id]");
  if (!btn || !SNAP) return;
  activeId = btn.dataset.id;
  mountedClass = "";
  renderBoard(SNAP, true);
});
document.getElementById("board").addEventListener("click", (e) => {
  const chip = e.target.closest("[data-day]");
  if (chip && SNAP) {
    DAYSEL[activeId] = chip.dataset.day;
    const existing = document.querySelector(".brief");
    const brief = briefFor(SNAP, activeId);
    if (existing && brief) existing.outerHTML = briefMarkup(brief, activeId);
    return;
  }
  const tab = e.target.closest("[data-view]");
  if (tab) {
    const id = tab.closest("[data-asset]")?.dataset.asset;
    if (id) showView(id, tab.dataset.view);
    return;
  }
  const btn = e.target.closest("[data-range]");
  if (!btn || !SNAP) return;
  const id = btn.closest("[data-asset]")?.dataset.asset;
  const row = (SNAP.assets || []).find((a) => a.id === id);
  if (row) loadRange(row, btn.dataset.range);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "f" || e.key === "F") {
    e.preventDefault();
    toggleFs();
  }
});

renderClocks();
setInterval(renderClocks, 1000);
load();
setInterval(load, 12000);
setInterval(refreshCharts, 30000);
startTickers();
