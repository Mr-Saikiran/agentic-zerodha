const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---- formatting -------------------------------------------------------------

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const money = (v, precise) => (v == null ? "–" : (precise ? inr2 : inr).format(v));
const signed = (v, precise) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${money(Math.abs(v), precise)}`);
const pct = (v) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${num.format(Math.abs(v))}%`);
const cls = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ago = (ts) => {
  const m = Math.round((Date.now() / 1000 - ts) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
};

// ---- API --------------------------------------------------------------------

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) {
    showLogin();
    throw new Error(body.detail || "Not logged in");
  }
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

// ---- state --------------------------------------------------------------------

const state = {
  holdings: [],
  positions: [],
  funds: null,
  news: null,
  insights: null,
  newsFilter: "all",
  sort: { key: "current_value", asc: false },
  charts: {},
};

// ---- auth / shell -----------------------------------------------------------

function showLogin(message) {
  $("#shell").hidden = true;
  $("#login-screen").hidden = false;
  if (message) $("#login-notice").textContent = message;
}

async function boot() {
  let s;
  try {
    s = await api("api/status");
  } catch (e) {
    return showLogin(e.message);
  }
  if (!s.logged_in) {
    return showLogin(
      s.missing_settings.length
        ? `Set ${s.missing_settings.join(", ")} in .env and restart the server.`
        : undefined
    );
  }
  $("#login-screen").hidden = true;
  $("#shell").hidden = false;
  $("#demo-badge").hidden = !s.demo;
  $("#model-name").textContent = s.model;
  const name = s.user?.user_name || s.user?.user_id || "You";
  $("#user-chip").innerHTML = `<span class="avatar">${esc(name[0])}</span><span class="name">${esc(name)}</span>`;
  if (window.matchMedia("(max-width: 980px)").matches) $("#shell").classList.add("copilot-hidden");

  renderWelcome();
  await Promise.all([loadPortfolio(), loadMarket()]);
}

$("#logout").onclick = async () => {
  await api("logout", { method: "POST" });
  location.reload();
};

// ---- navigation ---------------------------------------------------------------

function go(page) {
  $$(".nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => (p.hidden = p.id !== `page-${page}`));
  $(".main").scrollTop = 0;
  if (page === "news" && !state.news) loadNews();
  if (page === "insights" && !state.insights) renderInsightsEmpty();
}
$$(".nav button").forEach((b) => (b.onclick = () => go(b.dataset.page)));

$("#copilot-toggle").onclick = () => $("#shell").classList.toggle("copilot-hidden");
$("#copilot-close").onclick = () => $("#shell").classList.add("copilot-hidden");
function openCopilot() {
  $("#shell").classList.remove("copilot-hidden");
}

$("#refresh").onclick = async () => {
  $("#refresh").disabled = true;
  try {
    await Promise.all([loadPortfolio(), loadMarket()]);
  } catch (e) {
    toastError(e.message);
  }
  $("#refresh").disabled = false;
};

function toastError(msg) {
  addMessage("error", msg);
  openCopilot();
}

// ---- market ticker ------------------------------------------------------------

async function loadMarket() {
  try {
    const idx = await api("api/market");
    $("#ticker").innerHTML = idx
      .map(
        (i) => `<div class="tick"><span class="n">${esc(i.name)}</span>
          <span><span class="v">${num.format(i.last_price)}</span><span class="c ${cls(i.change)}">${pct(i.change_pct)}</span></span></div>`
      )
      .join("");
  } catch {
    $("#ticker").innerHTML = "";
  }
}

// ---- portfolio ----------------------------------------------------------------

async function loadPortfolio() {
  const [holdings, positions, funds] = await Promise.all([
    api("api/holdings"),
    api("api/positions"),
    api("api/funds"),
  ]);
  Object.assign(state, { holdings, positions, funds });
  renderOverview();
  renderHoldings();
  renderPositions();
}

function totals() {
  const h = state.holdings;
  const invested = h.reduce((a, x) => a + x.invested, 0);
  const current = h.reduce((a, x) => a + x.current_value, 0);
  const day = h.reduce((a, x) => a + x.day_change, 0);
  return { invested, current, pnl: current - invested, day, prev: current - day };
}

function renderOverview() {
  const t = totals();
  $("#ov-value").textContent = money(t.current);
  const dayPct = t.prev ? (t.day / t.prev) * 100 : 0;
  const totPct = t.invested ? (t.pnl / t.invested) * 100 : 0;
  $("#ov-day").className = `pill ${cls(t.day)}`;
  $("#ov-day").innerHTML = `<span class="${cls(t.day)}">${signed(t.day)} (${pct(dayPct)})</span>`;
  $("#ov-total").className = `pill ${cls(t.pnl)}`;
  $("#ov-total").innerHTML = `<span class="${cls(t.pnl)}">${signed(t.pnl)} (${pct(totPct)})</span>`;

  $("#kpi-invested").textContent = money(t.invested);
  $("#kpi-cash").textContent = money(state.funds?.live_balance);
  $("#kpi-count").textContent = state.holdings.length;
  const top = [...state.holdings].sort((a, b) => b.current_value - a.current_value)[0];
  $("#kpi-top").innerHTML = top
    ? `${esc(top.symbol)}<div class="kpi-sub">${num.format((top.current_value / t.current) * 100)}% of portfolio</div>`
    : "–";

  const movers = [...state.holdings].sort((a, b) => Math.abs(b.day_change_pct) - Math.abs(a.day_change_pct)).slice(0, 5);
  $("#movers").innerHTML = movers.length
    ? movers
        .map(
          (h) => `<div class="mover">
            <div><div class="sym">${esc(h.symbol)}</div><div class="sub">${num.format(h.last_price)}</div></div>
            <div style="text-align:right"><div class="${cls(h.day_change_pct)} num">${pct(h.day_change_pct)}</div>
            <div class="sub num">${signed(h.day_change)}</div></div></div>`
        )
        .join("")
    : `<p class="muted">No holdings yet.</p>`;

  $("#hero-chips").innerHTML = [
    "Give me today's portfolio brief",
    "Am I too concentrated?",
    top ? `Should I worry about ${top.symbol}?` : "How is the market today?",
  ]
    .map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`)
    .join("");

  drawCharts();
}

function themeColors() {
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  return { text: v("--text"), muted: v("--muted"), border: v("--border"), up: v("--up"), down: v("--down"), surface: v("--surface") };
}

const PALETTE = ["#7c7cff", "#a78bfa", "#22c3ee", "#2fd08a", "#f5b83d", "#ff8a65", "#f472b6", "#60a5fa", "#34d399", "#c084fc", "#94a3b8"];

function drawCharts() {
  if (!window.Chart) return;
  const c = themeColors();
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  Chart.defaults.color = c.muted;

  const sorted = [...state.holdings].sort((a, b) => b.current_value - a.current_value);
  const top = sorted.slice(0, 8);
  const rest = sorted.slice(8).reduce((a, h) => a + h.current_value, 0);
  const labels = [...top.map((h) => h.symbol), ...(rest ? ["Others"] : [])];
  const values = [...top.map((h) => h.current_value), ...(rest ? [rest] : [])];

  state.charts.alloc?.destroy();
  state.charts.alloc = new Chart($("#chart-alloc"), {
    type: "doughnut",
    data: { labels, datasets: [{ data: values, backgroundColor: PALETTE, borderColor: c.surface, borderWidth: 2 }] },
    options: {
      maintainAspectRatio: false,
      cutout: "68%",
      plugins: {
        legend: { position: "right", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 10 } },
        tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${money(ctx.raw)}` } },
      },
    },
  });

  const byPnl = [...state.holdings].sort((a, b) => b.pnl - a.pnl);
  state.charts.pnl?.destroy();
  state.charts.pnl = new Chart($("#chart-pnl"), {
    type: "bar",
    data: {
      labels: byPnl.map((h) => h.symbol),
      datasets: [{ data: byPnl.map((h) => h.pnl), backgroundColor: byPnl.map((h) => (h.pnl >= 0 ? c.up : c.down)), borderRadius: 6, maxBarThickness: 36 }],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => ` ${signed(ctx.raw)}` } } },
      scales: {
        x: { grid: { display: false } },
        y: { grid: { color: c.border }, border: { display: false }, ticks: { callback: (v) => inr.format(v) } },
      },
    },
  });
}

// ---- holdings / positions tables -------------------------------------------------

const HOLDING_COLS = [
  ["symbol", "Instrument", (h) => `<div class="sym-cell"><b>${esc(h.symbol)}</b><span>${esc(h.exchange)} · ${num.format(h.quantity)} qty</span></div>`],
  ["average_price", "Avg cost", (h) => num.format(h.average_price)],
  ["last_price", "LTP", (h) => num.format(h.last_price)],
  ["current_value", "Current", (h) => money(h.current_value)],
  ["weight", "Weight", (h) => `<div class="weight"><span>${num.format(h.weight)}%</span><div class="weight-bar"><i style="width:${Math.min(100, h.weight * 2.5)}%"></i></div></div>`],
  ["pnl", "P&amp;L", (h) => `<span class="${cls(h.pnl)}">${signed(h.pnl)}</span><br><span class="small ${cls(h.pnl)}">${pct(h.invested ? (h.pnl / h.invested) * 100 : 0)}</span>`],
  ["day_change_pct", "Today", (h) => `<span class="${cls(h.day_change_pct)}">${pct(h.day_change_pct)}</span>`],
  [null, "", (h) => `<button class="ask-btn" data-q="Analyse my position in ${esc(h.symbol)}: performance, weight in my portfolio, and recent news.">✦ Ask</button>`],
];

function renderHoldings() {
  const t = totals();
  const q = $("#holdings-search").value.trim().toUpperCase();
  const rows = state.holdings
    .map((h) => ({ ...h, weight: t.current ? (h.current_value / t.current) * 100 : 0 }))
    .filter((h) => !q || h.symbol.includes(q));
  const { key, asc } = state.sort;
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    const d = typeof x === "string" ? x.localeCompare(y) : x - y;
    return asc ? d : -d;
  });
  const head = HOLDING_COLS.map(
    ([k, label]) => `<th data-key="${k ?? ""}" class="${k === key ? `sorted ${asc ? "asc" : ""}` : ""}">${label}</th>`
  ).join("");
  const body = rows.length
    ? rows.map((h) => `<tr>${HOLDING_COLS.map(([, , f]) => `<td>${f(h)}</td>`).join("")}</tr>`).join("")
    : `<tr><td class="empty" colspan="${HOLDING_COLS.length}">No holdings found</td></tr>`;
  $("#holdings-table").innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
  $$("#holdings-table th[data-key]").forEach((th) => {
    if (!th.dataset.key) return;
    th.onclick = () => {
      const k = th.dataset.key;
      state.sort = { key: k, asc: state.sort.key === k ? !state.sort.asc : k === "symbol" };
      renderHoldings();
    };
  });
}
$("#holdings-search").oninput = renderHoldings;

function renderPositions() {
  const cols = ["Instrument", "Product", "Qty", "Avg", "LTP", "P&amp;L"];
  const rows = state.positions;
  const body = rows.length
    ? rows
        .map(
          (p) => `<tr><td><div class="sym-cell"><b>${esc(p.symbol)}</b><span>${esc(p.exchange)}</span></div></td>
          <td><span class="badge neutral">${esc(p.product)}</span></td><td>${num.format(p.quantity)}</td>
          <td>${num.format(p.average_price)}</td><td>${num.format(p.last_price)}</td>
          <td class="${cls(p.pnl)}">${signed(p.pnl, true)}</td></tr>`
        )
        .join("")
    : `<tr><td class="empty" colspan="${cols.length}">No open positions today</td></tr>`;
  $("#positions-table").innerHTML = `<thead><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${body}</tbody>`;
}

// ---- news -------------------------------------------------------------------------

function loadingState(steps) {
  return `<div class="card"><div class="state">
      <div class="typing"><i></i><i></i><i></i></div>
      <div class="progress-steps">${steps.map((s) => `<span>${s}</span>`).join("")}</div>
    </div></div>
    <div class="skeleton-grid">${'<div class="shimmer"></div>'.repeat(6)}</div>`;
}

function errorState(msg, retryId) {
  return `<div class="card"><div class="state"><div class="big-ico">⚠</div><p>${esc(msg)}</p>
    <button class="btn ghost small" id="${retryId}">Try again</button></div></div>`;
}

// News and insights run as background jobs on the server: start one, then poll until it's ready.
async function pollJob(path, refresh) {
  let res = await api(`${path}${refresh ? "?refresh=true" : ""}`);
  while (res.status === "running") {
    await new Promise((r) => setTimeout(r, 3000));
    res = await api(path);
  }
  return res;
}

async function loadNews(refresh = false) {
  const body = $("#news-body");
  $("#news-refresh").disabled = true;
  body.innerHTML = loadingState(["Searching the web for news on your holdings…", "This usually takes 30–60 seconds."]);
  try {
    state.news = await pollJob("api/news", refresh);
    renderNews();
  } catch (e) {
    body.innerHTML = errorState(e.message, "news-retry");
    $("#news-retry").onclick = () => loadNews(true);
  }
  $("#news-refresh").disabled = false;
}
$("#news-refresh").onclick = () => loadNews(true);

function renderNews() {
  const n = state.news;
  $("#news-meta").textContent = `Researched by Claude · updated ${ago(n.generated_at)}`;
  const held = new Set(state.holdings.map((h) => h.symbol));
  const filters = { all: "All", mine: "My holdings", market: "Market", positive: "Positive", negative: "Negative" };
  const f = state.newsFilter;
  const items = n.items.filter((i) =>
    f === "all" ? true
    : f === "mine" ? i.symbols.some((s) => held.has(s))
    : f === "market" ? !i.symbols.length
    : i.sentiment === f
  );
  const sentimentBadge = { positive: "up", negative: "down", neutral: "neutral" };

  $("#news-body").innerHTML = `
    <div class="card market-summary"><div class="spark-ico">✦</div>
      <div><div class="label">Market mood</div><p class="summary-text">${esc(n.market_summary)}</p></div></div>
    <div class="chips news-filters">${Object.entries(filters)
      .map(([k, v]) => `<button class="chip ${k === f ? "active" : ""}" data-filter="${k}">${v}</button>`)
      .join("")}</div>
    <div class="news-grid">${
      items.length
        ? items
            .map(
              (i) => `<article class="card news-card">
          <div class="news-top">
            <span class="badge ${sentimentBadge[i.sentiment]}">${esc(i.sentiment)}</span>
            ${i.impact === "high" ? `<span class="badge warn">High impact</span>` : ""}
            ${i.symbols.map((s) => `<span class="sym-tag">${esc(s)}</span>`).join("")}
          </div>
          <h4><a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a></h4>
          <p>${esc(i.summary)}</p>
          <div class="news-foot"><span>${esc(i.source)}${i.published ? ` · ${esc(i.published)}` : ""}</span>
            <button class="ask-btn" data-q="${esc(`How does this news affect my portfolio? "${i.title}" (${i.source})`)}">✦ Ask</button></div>
        </article>`
            )
            .join("")
        : `<p class="muted">No stories match this filter.</p>`
    }</div>`;
  $$("[data-filter]").forEach((b) => (b.onclick = () => ((state.newsFilter = b.dataset.filter), renderNews())));
}

function safeUrl(u) {
  try {
    const url = new URL(u);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "#";
  } catch {
    return "#";
  }
}

// ---- insights -----------------------------------------------------------------------

function renderInsightsEmpty() {
  $("#insights-body").innerHTML = `<div class="card"><div class="state">
    <div class="big-ico">✦</div>
    <h3>Get an AI review of your portfolio</h3>
    <p>Claude classifies your holdings by sector, checks concentration and recent news, and suggests where you could add, trim or diversify. Takes about a minute.</p>
    <button class="btn primary" id="insights-run-2">✦ Generate review</button></div></div>`;
  $("#insights-run-2").onclick = () => loadInsights(false);
}

async function loadInsights(refresh) {
  $("#insights-run").disabled = true;
  $("#insights-body").innerHTML = loadingState([
    "Classifying holdings by sector…",
    "Checking concentration and recent news…",
    "Drafting ideas — usually 45–90 seconds.",
  ]);
  try {
    state.insights = await pollJob("api/insights", refresh);
    renderInsights();
    $("#insights-run").textContent = "↻ Regenerate";
  } catch (e) {
    $("#insights-body").innerHTML = errorState(e.message, "insights-retry");
    $("#insights-retry").onclick = () => loadInsights(true);
  }
  $("#insights-run").disabled = false;
}
$("#insights-run").onclick = () => loadInsights(!!state.insights);

const IDEA_TYPES = {
  add_more: ["Add more", "up"],
  trim: ["Trim", "down"],
  diversify: ["Diversify", ""],
  rebalance: ["Rebalance", "warn"],
  research: ["Research", "neutral"],
};
const SEVERITY = { high: "down", medium: "warn", low: "neutral" };

function confBars(level) {
  const n = { low: 1, medium: 2, high: 3 }[level] || 1;
  return `<span class="conf">${[1, 2, 3].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}&nbsp;${esc(level)} confidence</span>`;
}

function renderInsights() {
  const x = state.insights;
  $("#insights-meta").textContent = `Generated by Claude · ${ago(x.generated_at)}`;
  const score = Math.max(0, Math.min(100, x.health_score));
  const ringColor = score >= 70 ? "var(--up)" : score >= 45 ? "var(--warn)" : "var(--down)";

  $("#insights-body").innerHTML = `
    <div class="insight-top">
      <div class="card score">
        <div class="ring" style="--p:${score};--c:${ringColor}"><div class="ring-inner"><b>${score}</b></div></div>
        <div class="label">Portfolio health</div>
      </div>
      <div class="card"><div class="label" style="margin-bottom:6px">Summary</div><p class="summary-text">${esc(x.summary)}</p></div>
    </div>

    <div class="grid-2">
      <div class="card"><div class="card-head"><h3>Sector allocation</h3></div><div class="donut-wrap"><canvas id="chart-sector"></canvas></div></div>
      <div class="card"><div class="card-head"><h3>Strengths &amp; risks</h3></div>
        <ul class="list">
          ${x.strengths.map((s) => `<li><span class="mark badge up">✓</span><span>${esc(s)}</span></li>`).join("")}
          ${x.risks
            .map(
              (r) => `<li><span class="mark badge ${SEVERITY[r.severity]}">!</span>
              <span><span class="risk-title">${esc(r.title)}</span> <span class="badge ${SEVERITY[r.severity]}">${esc(r.severity)}</span><br>
              <span class="muted">${esc(r.detail)}</span></span></li>`
            )
            .join("")}
        </ul>
      </div>
    </div>

    <div class="page-head"><h2 style="font-size:17px">Ideas to consider</h2></div>
    <div class="ideas-grid">
      ${x.ideas
        .map((i) => {
          const [label, tone] = IDEA_TYPES[i.type] || [i.type, "neutral"];
          return `<div class="card idea">
            <div class="news-top"><span class="badge ${tone}">${label}</span>${i.symbols.map((s) => `<span class="sym-tag">${esc(s)}</span>`).join("")}</div>
            <h4>${esc(i.title)}</h4>
            <p>${esc(i.rationale)}</p>
            <div class="idea-foot">${confBars(i.confidence)}
              <button class="ask-btn" data-q="${esc(`Dig deeper into this idea: "${i.title}". What are the risks, and what should I check before acting?`)}">✦ Discuss</button></div>
          </div>`;
        })
        .join("")}
    </div>
    <p class="disclaimer">These ideas are generated by AI from your portfolio data and public news. They are not investment advice from a SEBI-registered adviser. Do your own research before acting.</p>`;

  if (window.Chart) {
    const c = themeColors();
    state.charts.sector?.destroy();
    state.charts.sector = new Chart($("#chart-sector"), {
      type: "doughnut",
      data: {
        labels: x.sectors.map((s) => s.name),
        datasets: [{ data: x.sectors.map((s) => s.weight_pct), backgroundColor: PALETTE, borderColor: c.surface, borderWidth: 2 }],
      },
      options: {
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: { position: "right", labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 10 } },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.label}: ${num.format(ctx.raw)}%` } },
        },
      },
    });
  }
}

// ---- copilot ----------------------------------------------------------------------

const SUGGESTIONS = [
  "Summarise my portfolio today",
  "Which holdings are dragging returns?",
  "What's moving the market today?",
  "How diversified am I across sectors?",
  "Any news on my top 3 holdings?",
];

function renderWelcome() {
  $("#messages").innerHTML = `<div class="welcome">
    <h3><span class="spark">✦</span> Hi, I'm your portfolio copilot</h3>
    <p>I can read your holdings, positions and funds, pull live quotes, and search the web for news. Ask me anything.</p>
  </div>`;
  $("#suggest").innerHTML = SUGGESTIONS.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
}

function addMessage(kind, text) {
  const el = document.createElement("div");
  el.className = `msg ${kind}`;
  el.textContent = text;
  $("#messages").appendChild(el);
  scrollChat();
  return el;
}

function scrollChat() {
  const m = $("#messages");
  m.scrollTop = m.scrollHeight;
}

function renderMarkdown(text) {
  const html = window.marked ? marked.parse(text, { breaks: true }) : esc(text);
  return window.DOMPurify ? DOMPurify.sanitize(html) : esc(text);
}

let busy = false;

async function ask(question) {
  if (busy || !question.trim()) return;
  busy = true;
  openCopilot();
  $(".welcome")?.remove();
  addMessage("user", question);

  const bot = document.createElement("div");
  bot.className = "msg bot";
  bot.innerHTML = `<div class="bot-head"><span class="logo">◆</span> Copilot</div>
    <div class="tools"></div><div class="md"><span class="typing"><i></i><i></i><i></i></span></div>`;
  $("#messages").appendChild(bot);
  scrollChat();
  const toolsEl = $(".tools", bot);
  const mdEl = $(".md", bot);

  let text = "";
  let frame = 0;
  const paint = () => {
    frame = 0;
    mdEl.innerHTML = renderMarkdown(text);
    scrollChat();
  };
  const markToolsDone = () => $$(".tool-pill", toolsEl).forEach((p) => p.classList.add("done"));

  try {
    const res = await fetch("api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: question }),
    });
    if (res.status === 401) return showLogin();
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `Request failed (${res.status})`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if (!line.startsWith("data: ")) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.type === "text") {
          markToolsDone();
          text += ev.text;
          if (!frame) frame = requestAnimationFrame(paint);
        } else if (ev.type === "tool") {
          markToolsDone();
          toolsEl.insertAdjacentHTML("beforeend", `<span class="tool-pill"><span class="dot"></span>${esc(ev.label)}</span>`);
          scrollChat();
        } else if (ev.type === "error") {
          throw new Error(ev.message);
        }
      }
    }
    markToolsDone();
    paint();
    if (!text) mdEl.innerHTML = `<span class="muted">(no reply)</span>`;
  } catch (e) {
    markToolsDone();
    if (!text) bot.remove();
    addMessage("error", e.message);
  } finally {
    busy = false;
  }
}

$("#composer").onsubmit = (e) => {
  e.preventDefault();
  const ta = $("#prompt");
  const q = ta.value;
  ta.value = "";
  ta.style.height = "auto";
  ask(q);
};
$("#prompt").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("#composer").requestSubmit();
  }
});
$("#prompt").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${e.target.scrollHeight}px`;
});
$("#chat-reset").onclick = async () => {
  if (busy) return;
  await api("api/chat/reset", { method: "POST" });
  renderWelcome();
};

// Any element with data-q anywhere in the app sends that question to the copilot.
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-q]");
  if (btn) ask(btn.dataset.q);
});

boot();
