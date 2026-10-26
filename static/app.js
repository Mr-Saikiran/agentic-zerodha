const $ = (sel) => document.querySelector(sel);

const inr = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const money = (v) => (v == null ? "–" : inr.format(v));
const signClass = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) {
    await loadStatus();
    throw new Error(body.detail || "Not logged in");
  }
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

// ---- auth ---------------------------------------------------------------

async function loadStatus() {
  const s = await api("/api/status");
  const auth = $("#auth");
  const notice = $("#notice");

  if (s.missing_settings.length) {
    notice.hidden = false;
    notice.textContent = `Set ${s.missing_settings.join(", ")} in your .env file, then restart the server.`;
  } else {
    notice.hidden = true;
  }

  if (s.logged_in) {
    auth.innerHTML = `<span>${escapeHtml(s.user?.user_name || s.user?.user_id || "Logged in")}</span>
      <button id="logout" class="ghost">Log out</button>`;
    $("#logout").onclick = async () => {
      await api("/logout", { method: "POST" });
      location.reload();
    };
    $("#app").hidden = false;
    return true;
  }

  auth.innerHTML = `<a class="button" href="/login">Log in with Zerodha</a>`;
  $("#app").hidden = true;
  if (!s.missing_settings.length) {
    notice.hidden = false;
    notice.textContent = "Log in with Zerodha to load your portfolio. Kite sessions expire every morning around 6 AM IST.";
  }
  return false;
}

// ---- portfolio ------------------------------------------------------------

let currentTab = "holdings";
let cache = { holdings: [], positions: [] };

const columns = {
  holdings: [
    ["Symbol", (r) => escapeHtml(r.symbol)],
    ["Qty", (r) => num.format(r.quantity)],
    ["Avg", (r) => num.format(r.average_price)],
    ["LTP", (r) => num.format(r.last_price)],
    ["Current", (r) => money(r.current_value)],
    ["P&L", (r) => `<span class="${signClass(r.pnl)}">${money(r.pnl)}</span>`],
    ["Day %", (r) => `<span class="${signClass(r.day_change_pct)}">${num.format(r.day_change_pct)}%</span>`],
  ],
  positions: [
    ["Symbol", (r) => escapeHtml(r.symbol)],
    ["Product", (r) => escapeHtml(r.product)],
    ["Qty", (r) => num.format(r.quantity)],
    ["Avg", (r) => num.format(r.average_price)],
    ["LTP", (r) => num.format(r.last_price)],
    ["P&L", (r) => `<span class="${signClass(r.pnl)}">${money(r.pnl)}</span>`],
  ],
};

function renderTable() {
  const cols = columns[currentTab];
  const rows = cache[currentTab];
  const head = `<tr>${cols.map(([h]) => `<th>${h}</th>`).join("")}</tr>`;
  const body = rows.length
    ? rows.map((r) => `<tr>${cols.map(([, f]) => `<td>${f(r)}</td>`).join("")}</tr>`).join("")
    : `<tr><td class="empty" colspan="${cols.length}">No ${currentTab}</td></tr>`;
  $("#table").innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}

async function loadPortfolio() {
  const [holdings, positions, funds] = await Promise.all([
    api("/api/holdings"),
    api("/api/positions"),
    api("/api/funds"),
  ]);
  cache = { holdings, positions };

  const invested = holdings.reduce((a, h) => a + h.invested, 0);
  const current = holdings.reduce((a, h) => a + h.current_value, 0);
  const pnl = current - invested;
  $("#c-invested").textContent = money(invested);
  $("#c-current").textContent = money(current);
  const pct = invested ? ` (${num.format((pnl / invested) * 100)}%)` : "";
  $("#c-pnl").innerHTML = `<span class="${signClass(pnl)}">${money(pnl)}${pct}</span>`;
  $("#c-cash").textContent = money(funds.live_balance);
  renderTable();
}

document.querySelectorAll(".tabs [data-tab]").forEach((btn) => {
  btn.onclick = () => {
    document.querySelectorAll(".tabs [data-tab]").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    currentTab = btn.dataset.tab;
    renderTable();
  };
});

$("#refresh").onclick = async () => {
  $("#refresh").disabled = true;
  try { await loadPortfolio(); } catch (e) { alert(e.message); }
  $("#refresh").disabled = false;
};

// ---- chat -----------------------------------------------------------------

function addMessage(cls, text) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.textContent = text;
  $("#messages").appendChild(el);
  el.scrollIntoView({ block: "end" });
  return el;
}

$("#chat-form").onsubmit = async (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage("user", text);
  const thinking = addMessage("meta", "Thinking…");
  const sendBtn = $("#chat-form button");
  sendBtn.disabled = true;
  try {
    const res = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text }) });
    thinking.remove();
    if (res.tool_calls.length) addMessage("meta", `Used: ${[...new Set(res.tool_calls)].join(", ")}`);
    addMessage("bot", res.reply || "(no reply)");
  } catch (err) {
    thinking.remove();
    addMessage("error", err.message);
  }
  sendBtn.disabled = false;
  input.focus();
};

$("#reset").onclick = async () => {
  await api("/api/chat/reset", { method: "POST" });
  $("#messages").innerHTML = "";
  addMessage("hint", "New conversation started.");
};

// ---- boot -----------------------------------------------------------------

(async () => {
  try {
    if (await loadStatus()) await loadPortfolio();
  } catch (e) {
    const notice = $("#notice");
    notice.hidden = false;
    notice.textContent = e.message;
  }
})();
