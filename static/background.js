const SOURCES = ["Financial Times", "Bloomberg", "Reuters"];
const TAG = { "Financial Times": "FT", Bloomberg: "BBG", Reuters: "RTRS" };

function renderClocks() {
  const host = document.getElementById("clocks");
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
  host.innerHTML = `<div class="clock solo">
    <div class="city">HONG KONG</div>
    <div class="time">${time}</div>
    <div class="date">${date}</div>
  </div>`;
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

function render(data) {
  const grouped = data.newsBySource || {};
  const host = document.getElementById("wires");
  host.innerHTML = SOURCES.map((src) => {
    const items = grouped[src] || [];
    const list = items.length
      ? items
          .map((n) => {
            const href = n.link ? `href="${n.link}" target="_blank" rel="noopener"` : "";
            return `<a class="wire-item" ${href}>
              <div class="wire-time">${fmtTime(n.time)}</div>
              <div class="wire-title">${n.title}</div>
            </a>`;
          })
          .join("")
      : `<div class="wire-empty">Waiting for ${src} headlines…</div>`;
    return `<section class="wire-col">
      <div class="col-h"><h2>${TAG[src]}  ${src.toUpperCase()}</h2><span>authority wire</span></div>
      <div class="wire-list">${list}</div>
    </section>`;
  }).join("");
  document.getElementById("stamp").textContent = data.updatedAtHkt
    ? `AS OF  ${data.updatedAtHkt}`
    : "WAITING FOR WIRES";
}

async function load() {
  try {
    const res = await fetch("/api/news", { cache: "no-store" });
    render(await res.json());
  } catch (err) {
    document.getElementById("stamp").textContent = "WIRE ERROR";
    console.error(err);
  }
}

renderClocks();
setInterval(renderClocks, 1000);
load();
setInterval(load, 20000);
