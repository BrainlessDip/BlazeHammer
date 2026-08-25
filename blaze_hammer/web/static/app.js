/* Blaze Hammer Web — vanilla JS dashboard (no build step). */
"use strict";

// ---------------------------------------------------------------- helpers --

const $ = (id) => document.getElementById(id);
const state = {
  ws: null,
  wsTimer: null,
  connected: false,
  authed: false,
  runId: null,
  running: false,
  filter: "all",
  logEntries: new Map(), // index -> entry
  originals: {},         // editor id -> server text
};

function toast(message, kind = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className =
    "fixed bottom-4 right-4 z-50 max-w-sm rounded border px-4 py-3 text-sm shadow-xl fade-in " +
    (kind === "error"
      ? "border-red-500/60 bg-red-950/80 text-red-300"
      : kind === "success"
        ? "border-emerald-500/60 bg-emerald-950/80 text-emerald-300"
        : "border-ink-600 bg-ink-800");
  el.classList.remove("hidden");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("hidden"), 3500);
}

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && state.authed) showLogin();
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `${res.status} ${res.statusText}`;
    throw Object.assign(new Error(detail), { status: res.status });
  }
  return data;
}

// ------------------------------------------------------------------- auth --

function showLogin() {
  state.authed = false;
  $("login-view").classList.remove("hidden");
  $("login-view").classList.add("flex");
  $("app-view").classList.add("hidden");
}

function showApp() {
  state.authed = true;
  $("login-view").classList.add("hidden");
  $("login-view").classList.remove("flex");
  $("app-view").classList.remove("hidden");
  $("app-view").classList.add("fade-in");
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-error").classList.add("hidden");
  try {
    const me = await api("/api/auth/login", {
      method: "POST",
      body: { username: $("login-user").value, password: $("login-pass").value },
    });
    $("whoami").textContent = me.username || "";
    $("login-pass").value = "";
    showApp();
    boot();
  } catch (err) {
    const el = $("login-error");
    el.textContent = err.message;
    el.classList.remove("hidden");
  }
});

$("logout-btn").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch {}
  wsClose();
  showLogin();
});

// -------------------------------------------------------------- websocket --

function setWsStatus(kind) {
  const wrap = $("ws-status");
  const dot = wrap.querySelector(".dot");
  const label = wrap.querySelector(".label");
  const styles = {
    on:  ["bg-emerald-500 pulse-dot", "Connected"],
    off: ["bg-slate-600",             "Offline"],
    retry:["bg-amber-400",            "Reconnecting\u2026"],
  };
  const [cls, text] = styles[kind] || styles.off;
  dot.className = `dot w-2 h-2 rounded-full ${cls}`;
  label.innerHTML = text;
}

function wsConnect() {
  wsClose();
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(`${proto}${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => { setWsStatus("on"); };
  ws.onclose = () => {
    setWsStatus(state.authed ? "retry" : "off");
    if (state.authed) {
      clearTimeout(state.wsTimer);
      state.wsTimer = setTimeout(wsConnect, 1500 + Math.random() * 1000);
    }
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (msg) => {
    let event;
    try { event = JSON.parse(msg.data); } catch { return; }
    handleEvent(event);
  };
}

function wsClose() {
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
    state.ws = null;
  }
}

let lastSeq = 0;

function handleEvent(ev) {
  switch (ev.type) {
    case "hello":
      renderRuns(ev.runs || []);
      break;
    case "run.started":
      state.running = true;
      state.runId = ev.run_id;
      lastSeq = 0;
      clearLog();
      syncButtons();
      break;
    case "stats.updated":
      if (ev.run_id === state.runId || !state.running) renderStats(ev);
      break;
    case "request.completed":
      if ((ev.seq || 0) < lastSeq) return; // dedupe after reconnect
      lastSeq = ev.seq || lastSeq;
      appendLogRow(ev);
      break;
    case "run.completed":
    case "run.stopped":
      state.running = false;
      syncButtons();
      renderStats(ev);
      toast(
        ev.type === "run.completed"
          ? `Run finished \u2014 ${ev.completed}/${ev.requested} in ${ev.elapsed_s}s`
          : "Run stopped",
        ev.type === "run.completed" ? "success" : "info",
      );
      refreshRuns();
      break;
    case "run.error":
      state.running = false;
      syncButtons();
      toast(`Run failed: ${ev.message}`, "error");
      break;
    case "auth.expired":
      showLogin();
      toast("Session expired \u2014 sign in again", "error");
      break;
    default:
      break;
  }
}

// ------------------------------------------------------------------ stats --

function ms(v) {
  return v == null ? "-" : `${Math.round(v)}ms`;
}

function renderStats(s) {
  $("s-completed").textContent = fmtNum(s.completed);
  $("s-requested").textContent = `/ ${fmtNum(s.requested)}`;
  $("s-success").textContent = fmtNum(s.success);
  $("s-failed").textContent = fmtNum(s.failed);
  $("s-rps").innerHTML = `${fmtNum(s.rps)}<span class="text-sm text-slate-500">/s</span>`;

  const pct = s.requested > 0 ? Math.min(100, (s.completed / s.requested) * 100) : 0;
  $("progress-bar").style.width = `${pct}%`;
  $("progress-pct").textContent = `${pct.toFixed(1)}%`;
  $("l-elapsed").textContent = s.elapsed_s != null ? `${s.elapsed_s}s` : "-";
  const lat = s.latency_ms || {};
  $("l-mean").textContent = ms(lat.mean);
  $("l-p50").textContent = ms(lat.p50);
  $("l-p95").textContent = ms(lat.p95);
  $("l-p99").textContent = ms(lat.p99);
  $("l-max").textContent = ms(lat.max);

  const codes = s.status_codes || {};
  const maxCount = Math.max(1, ...Object.values(codes));
  $("status-hist").innerHTML = Object.entries(codes)
    .map(([code, count]) => {
      const width = Math.max(2, Math.round((count / maxCount) * 100));
      const color = code.startsWith("2") ? "bg-emerald-500"
        : code.startsWith("4") ? "bg-amber-500" : "bg-red-500";
      return `<div class="flex items-center gap-2 text-xs">
        <span class="w-10 font-mono text-slate-400">${code}</span>
        <div class="flex-1 h-3 bg-ink-800 rounded overflow-hidden">
          <div class="h-full ${color}" style="width:${width}%"></div></div>
        <span class="w-14 text-right font-mono text-slate-500">${fmtNum(count)}</span></div>`;
    })
    .join("");
}

function fmtNum(n) {
  return (n ?? 0).toLocaleString("en-US");
}

// -------------------------------------------------------------------- log --

const FILTERS = {
  all: () => true,
  success: (r) => r.ok,
  client: (r) => r.status >= 400 && r.status < 500,
  server: (r) => r.status >= 500,
  net: (r) => !r.ok && !r.status,
};

function logMatches(ev) {
  const f = FILTERS[state.filter] || FILTERS.all;
  return f(ev);
}

function appendLogRow(ev) {
  state.logEntries.set(ev.index, ev);
  $("log-empty").classList.add("hidden");
  if (!logMatches(ev)) return;
  const tbody = $("log-body");
  const tr = document.createElement("tr");
  const color = !ev.ok ? "text-red-400"
    : ev.status >= 500 ? "text-red-300"
    : ev.status >= 400 ? "text-amber-300" : "text-emerald-400";
  tr.className = "hover:bg-ink-800 cursor-pointer border-t border-ink-800";
  tr.dataset.index = ev.index;
  tr.innerHTML = `<td class="px-3 py-1 text-slate-500">${ev.index}</td>
    <td class="px-2 py-1"><span class="${color}">${ev.status ?? "ERR"}</span></td>
    <td class="px-2 py-1 text-right text-slate-400">${ev.latency_ms}</td>
    <td class="px-3 py-1 text-right text-slate-600">${ev.error_category ?? ""}</td>`;
  tr.addEventListener("click", () => openDrawer(ev.index));
  tbody.prepend(tr);
  while (tbody.rows.length > 200) tbody.deleteRow(-1);
}

function clearLog() {
  $("log-body").innerHTML = "";
  $("log-empty").classList.remove("hidden");
  state.logEntries.clear();
}

document.querySelectorAll(".filter").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll(".filter").forEach((b) =>
      b.className = "filter px-2.5 py-1 rounded text-slate-400 hover:bg-ink-700");
    btn.className = "filter px-2.5 py-1 rounded bg-ember-600/20 text-ember-400";
    state.filter = btn.dataset.filter;
    rebuildLogFromEvents();
  }),
);

function rebuildLogFromEvents() {
  $("log-body").innerHTML = "";
  const rows = [...state.logEntries.values()].sort((a, b) => b.index - a.index);
  for (const ev of rows.slice(0, 200)) appendLogRow(ev);
}

$("clear-log-btn").addEventListener("click", clearLog);

// ------------------------------------------------------------------ drawer --

async function openDrawer(index) {
  $("drawer-index").textContent = `#${index}`;
  const content = $("drawer-content");
  content.innerHTML = '<p class="text-slate-500">Loading\u2026</p>';
  $("drawer").classList.remove("translate-x-full");
  try {
    const data = await api(`/api/runs/${state.runId}/log`);
    const entry = (data.entries || []).find((e) => e.index === index);
    content.innerHTML = entry ? renderEntry(entry)
      : '<p class="text-slate-500">Entry not retained (buffer capped).</p>';
  } catch (err) {
    content.innerHTML = `<p class="text-red-400">${esc(err.message)}</p>`;
  }
}

function kvBlock(title, obj) {
  if (!obj) return "";
  return `<div><div class="text-slate-500 uppercase tracking-wider mb-1 text-[11px]">${title}</div>
    <pre class="bg-ink-950 border border-ink-700 rounded p-2 overflow-auto whitespace-pre-wrap">${esc(JSON.stringify(obj, null, 2))}</pre></div>`;
}

function renderEntry(e) {
  return `
    <div class="grid grid-cols-2 gap-2">
      <div><span class="text-slate-500">Status</span> <span class="${e.ok ? "text-emerald-400" : "text-red-400"}">${e.status ?? "ERR"}</span></div>
      <div><span class="text-slate-500">Latency</span> ${e.latency_ms}ms</div>
      <div><span class="text-slate-500">Attempts</span> ${e.attempts}</div>
      <div><span class="text-slate-500">Category</span> ${e.error_category ?? "-"}</div>
    </div>
    ${e.error ? `<p class="text-red-400">${esc(e.error)}</p>` : ""}
    ${kvBlock("Request Headers", e.request_headers)}
    ${kvBlock("Request Body", e.request_body)}
    ${e.response_body_excerpt
      ? kvBlock("Response (excerpt)", { body: e.response_body_excerpt }) : ""}`;
}

$("drawer-close").addEventListener("click", () =>
  $("drawer").classList.add("translate-x-full"));
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") $("drawer").classList.add("translate-x-full");
});

function esc(s) {
  const div = document.createElement("div");
  div.textContent = String(s);
  return div.innerHTML;
}

// ------------------------------------------------------------ tabs/editors --

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.remove("border-ember-500", "text-ember-400", "font-medium");
      t.classList.add("border-transparent", "text-slate-400");
    });
    tab.classList.add("border-ember-500", "text-ember-400", "font-medium");
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
    $(`tab-${tab.dataset.tab}`).classList.remove("hidden");
  }),
);

document.querySelectorAll(".fmt-btn").forEach((btn) =>
  btn.addEventListener("click", () => {
    const ta = $(btn.dataset.fmt);
    try {
      ta.value = JSON.stringify(JSON.parse(ta.value), null, 2);
      editorStatus(btn.dataset.fmt, "ok");
    } catch (err) {
      editorStatus(btn.dataset.fmt, `invalid JSON: ${err.message}`, true);
    }
  }));

document.querySelectorAll(".reset-btn").forEach((btn) =>
  btn.addEventListener("click", () => {
    $(btn.dataset.reset).value = state.originals[btn.dataset.reset] ?? "";
    editorStatus(btn.dataset.reset, "reset from configured file");
  }));

["editor-payload", "editor-headers"].forEach((id) =>
  $(id).addEventListener("input", () => {
    try {
      const trimmed = $(id).value.trim();
      if (trimmed) JSON.parse(trimmed);
      editorStatus(id, "valid JSON");
    } catch (err) {
      editorStatus(id, `invalid: ${err.message.slice(0, 60)}`, true);
    }
  }));

function editorStatus(id, msg, bad = false) {
  const target = id === "editor-payload" ? $("payload-status") : $("headers-status");
  target.textContent = msg;
  target.className = bad ? "text-red-400" : "text-emerald-500";
}

// ------------------------------------------------------------- config load --

async function loadConfig() {
  const cfg = await api("/api/config");
  $("f-target").value = cfg.target;
  $("f-method").value = cfg.method;
  $("f-post-type").value = cfg.post_type;
  $("f-requests").value = cfg.requests;
  $("f-concurrency").value = cfg.concurrency;
  $("f-delay").value = cfg.delay;
  $("f-rate").value = cfg.rate ?? "";
  $("f-timeout").value = cfg.timeout;
  $("f-retries").value = cfg.retries;
  $("f-seed").value = cfg.seed ?? "";
  $("f-faker-locale").value = cfg.faker_locale ?? "";

  const tpl = await api("/api/config/templates");
  $("editor-payload").value = tpl.payload_text ?? "";
  $("editor-headers").value = tpl.headers_text ?? "";
  state.originals["editor-payload"] = tpl.payload_text ?? "";
  state.originals["editor-headers"] = tpl.headers_text ?? "";
}

async function loadProfiles() {
  const profiles = await api("/api/profiles");
  const select = $("f-profile");
  select.querySelectorAll("option:not(:first-child)").forEach((o) => o.remove());
  for (const p of profiles) {
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name;
    select.appendChild(opt);
  }
}

async function refreshRuns() {
  const list = await api("/api/runs");
  renderRuns(list.runs || []);
}

function renderRuns(runs) {
  const active = runs.find((r) => r.status === "running");
  if (active && !state.running) {
    state.runId = active.run_id;
    state.running = true;
    syncButtons();
  } else if (!active && state.running) {
    // server restarted or run vanished; keep local state until events arrive
  }
}

// -------------------------------------------------------------- run control --

function collectOverrides() {
  const num = (id) => { const v = $(id).value.trim(); return v === "" ? null : Number(v); };
  return {
    target: $("f-target").value.trim() || null,
    method: $("f-method").value,
    post_type: $("f-post-type").value,
    requests: num("f-requests"),
    concurrency: num("f-concurrency"),
    delay: num("f-delay"),
    rate: num("f-rate"),
    timeout: num("f-timeout"),
    retries: num("f-retries"),
    seed: num("f-seed"),
    faker_locale: $("f-faker-locale").value.trim() || null,
    profile: $("f-profile").value || null,
    payload_text: $("editor-payload").value.trim() || null,
    headers_text: $("editor-headers").value.trim() || null,
  };
}

function stripNulls(obj) {
  return Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== null));
}

$("start-btn").addEventListener("click", async () => {
  try {
    const body = stripNulls(collectOverrides());
    const summary = await api("/api/runs", { method: "POST", body });
    state.runId = summary.run_id;
    state.running = true;
    lastSeq = 0;
    clearLog();
    syncButtons();
    toast(`Run started (${summary.run_id})`, "success");
  } catch (err) {
    toast(err.message, "error");
  }
});

$("stop-btn").addEventListener("click", async () => {
  if (!state.runId) return;
  try {
    await api(`/api/runs/${state.runId}/stop`, { method: "POST" });
    toast("Stop requested\u2026");
  } catch (err) {
    toast(err.message, "error");
  }
});

$("preview-btn").addEventListener("click", async () => {
  try {
    const body = { ...stripNulls(collectOverrides()), count: 3 };
    const out = await api("/api/preview", { method: "POST", body });
    document.querySelector('[data-tab="preview"]').click();
    $("preview-output").innerHTML = out.plans.map(renderPlan).join("");
  } catch (err) {
    toast(err.message, "error");
  }
});

function renderPlan(plan) {
  return `<div class="rounded border border-ink-700 p-3 fade-in">
    <div class="mb-2"><span class="text-ember-400 font-semibold">${esc(plan.method)}</span>
      <span class="text-slate-300">${esc(plan.url)}</span></div>
    ${plan.headers ? `<pre class="text-slate-500 whitespace-pre-wrap">Headers:\n${esc(JSON.stringify(plan.headers, null, 2))}</pre>` : ""}
    ${plan.body ? `<pre class="mt-2 whitespace-pre-wrap text-slate-300">${esc(JSON.stringify(plan.body, null, 2))}</pre>` : ""}
  </div>`;
}

$("save-config-btn").addEventListener("click", async () => {
  const overrides = stripNulls(collectOverrides());
  if (!confirm("Save current configuration to blazehammer.yaml?\n\nThe file will be REGENERATED from the canonical template (hand-written comments are replaced by generated ones).")) return;
  try {
    await api("/api/config/save", {
      method: "POST",
      body: { confirm: true,
        target: overrides.target, method: overrides.method,
        requests: overrides.requests, concurrency: overrides.concurrency },
    });
    toast("Saved to blazehammer.yaml", "success");
    await loadConfig();
  } catch (err) {
    toast(err.message, "error");
  }
});

function syncButtons() {
  $("start-btn").disabled = state.running;
  $("stop-btn").disabled = !state.running;
}

// ------------------------------------------------------------------- boot --

async function boot() {
  try {
    const me = await api("/api/me");
    $("whoami").textContent = me.username;
  } catch { return; }
  showApp();
  try {
    await Promise.all([loadConfig(), loadProfiles()]);
  } catch (err) {
    toast(err.message, "error");
  }
  syncButtons();
  wsConnect();
}

(async function init() {
  try {
    const me = await api("/api/me");
    $("whoami").textContent = me.username;
    showApp();
    await Promise.all([loadConfig(), loadProfiles()]);
    syncButtons();
    wsConnect();
  } catch {
    showLogin();
  }
})();
