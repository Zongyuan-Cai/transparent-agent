"use strict";

const $ = (id) => document.getElementById(id);
const state = {config: null, sessions: [], id: null, detail: null, events: new Map(), source: null,
  filter: "all", search: "", page: 0, follow: true, selected: null, views: [], viewIndex: 0,
  generation: 0, detailGeneration: 0, renderPending: false, liveMessage: null, tools: new Map(), busy: false};
const PAGE_SIZE = 45;
const labels = {session_start: "会话建立", session_resumed: "恢复会话", context_loaded: "上下文就绪",
  agent_start: "任务开始", user_message: "用户输入", model_request_start: "模型请求",
  api_request: "HTTP 请求", api_response_start: "HTTP 响应", api_response_chunk: "响应字节",
  api_response_parsed: "解析响应", api_response_end: "响应结束", model_text_delta: "文本片段",
  model_reasoning_delta: "推理片段", model_response: "模型输出", tool_call_start: "工具执行",
  tool_call_end: "工具结果", agent_finish: "任务结束", agent_error: "运行错误",
  session_cleared: "清空对话", skill_loaded: "加载 Skill", prompt_expanded: "展开模板"};
const stopLabels = {final: "任务完成", cancelled: "已停止", max_steps: "达到步数上限", error: "运行失败"};

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function toast(message, error = false) {
  const el = $("toast"); el.textContent = message; el.classList.toggle("error", error); el.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { el.hidden = true; }, error ? 7000 : 3000);
}
async function api(path, body) {
  const options = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json",
    "X-Transparent-Token": state.config.token}, body: JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
const sessionPath = (suffix = "") => `/api/sessions/${state.id}${suffix}`;
const basename = (path) => path.split(/[\\/]/).filter(Boolean).pop() || path;
function setBusy(status, error, stopReason) {
  state.busy = status === "running" || status === "cancelling";
  $("run-status").className = `run-status ${state.busy ? "running" : status === "failed" ? "failed" : ""}`;
  $("status-label").textContent = error || (status === "cancelling" ? "正在停止并保存…" : state.busy ? "Agent 正在运行" : stopLabels[stopReason] || "准备就绪");
  $("send-button").hidden = state.busy; $("stop-button").hidden = !state.busy;
  $("stop-button").disabled = status === "cancelling";
  $("clear-session").disabled = !state.id || state.busy;
  const session = state.sessions.find((s) => s.session_id === state.id);
  if (session) { session.status = status; renderSessions(); }
}
function updateContext(detail) {
  $("project-name").textContent = basename(detail.workspace);
  $("project-name").title = detail.workspace;
  $("workspace-path").textContent = detail.workspace;
  $("model-label").textContent = detail.model; $("model-label").title = detail.model;
  $("session-label").textContent = detail.session_id ? detail.session_id.slice(0, 8) : "新会话";
}
async function refreshSessions() {
  state.sessions = (await api("/api/sessions")).sessions; renderSessions();
}
function renderSessions() {
  const list = $("session-list"); list.replaceChildren();
  $("session-count").textContent = state.sessions.length;
  if (!state.sessions.length) list.append(node("p", "empty-list", "还没有会话"));
  for (const session of state.sessions) {
    const button = node("button", `session-item ${session.session_id === state.id ? "active" : ""}`);
    button.setAttribute("aria-current", session.session_id === state.id ? "true" : "false");
    button.title = `${session.workspace}\n${session.session_id}`;
    button.append(node("span", "session-title", session.title || "新会话"));
    const sub = node("span", `session-subtitle ${["running", "cancelling"].includes(session.status) ? "running" : ""}`);
    sub.append(node("span", "status-dot"), document.createTextNode(`${session.model} · ${session.status === "running" ? "运行中" : session.status === "failed" ? "异常" : session.session_id.slice(0, 6)}`));
    button.append(sub); button.addEventListener("click", () => selectSession(session.session_id).catch(showError));
    list.append(button);
  }
}
function showError(error) { toast(error.message || String(error), true); }
function scrollChat(force = false) {
  const el = $("conversation");
  if (force || el.scrollHeight - el.scrollTop - el.clientHeight < 160) requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
}

// Build a small Markdown subset with text nodes. Model HTML is never inserted.
function markdown(target, text) {
  target.replaceChildren();
  const blocks = String(text || "").split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g);
  for (const block of blocks) {
    if (!block) continue;
    if (block.startsWith("```")) {
      const pre = node("pre"); const code = node("code", "", block.replace(/^```[^\n]*\n/, "").replace(/```$/, ""));
      pre.append(code); target.append(pre); continue;
    }
    let paragraph = [], list = null;
    const inline = (el, value) => {
      for (const part of value.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g)) {
        if (part.startsWith("`") && part.endsWith("`")) el.append(node("code", "", part.slice(1, -1)));
        else if (part.startsWith("**") && part.endsWith("**")) el.append(node("strong", "", part.slice(2, -2)));
        else el.append(document.createTextNode(part));
      }
    };
    const flush = () => { if (paragraph.length) { const p = node("p"); inline(p, paragraph.join("\n")); target.append(p); paragraph = []; } };
    for (const line of block.split("\n")) {
      const heading = line.match(/^(#{1,4})\s+(.+)$/), bullet = line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)$/);
      if (heading) { flush(); list = null; const h = node(heading[1].length <= 2 ? "h3" : "h4"); inline(h, heading[2]); target.append(h); }
      else if (bullet) { flush(); if (!list) { list = node("ul"); target.append(list); } const li = node("li"); inline(li, bullet[1]); list.append(li); }
      else { list = null; if (line.trim()) paragraph.push(line); else flush(); }
    }
    flush();
  }
}
function makeMessage(role, text = "", reasoning = "") {
  $("welcome").hidden = true;
  const el = node("article", `message ${role}`), heading = node("div", "message-heading");
  heading.append(node("span", "avatar", role === "user" ? "YOU" : "T_"), node("span", "", role === "user" ? "你" : "Transparent"));
  if (role !== "user") heading.append(node("small", "", "AGENT"));
  const reason = node("details", "reasoning"); reason.hidden = !reasoning;
  const reasonText = node("pre", "", reasoning); reason.append(node("summary", "", "模型返回的推理文本"), reasonText);
  const content = node("div", "message-content");
  if (role === "user") content.textContent = text; else markdown(content, text);
  el.append(heading, reason, content); $("messages").append(el);
  return {el, content, reason, reasonText, text, reasoning};
}
function toolCard(call, result, seq) {
  const card = node("button", `tool-card ${result?.is_error ? "error" : ""}`);
  card.append(node("span", "", `⌘ ${call.name}`), node("small", "", result ? result.cancelled ? "已停止" : result.is_error ? "出错" : "完成" : "执行中"));
  card.append(node("pre", "", result ? (result.output || "").slice(0, 700) : call.arguments || ""));
  card.addEventListener("click", () => { if (seq) inspectEvent(seq).catch(showError); openInspector(); });
  $("messages").append(card); state.tools.set(call.id || call.call_id, card); return card;
}
function renderHistory(detail) {
  $("messages").replaceChildren(); state.tools.clear(); state.liveMessage = null;
  $("welcome").hidden = detail.messages.length > 0;
  const records = [...state.events.values()];
  let responseCursor = [...records].reverse().find((r) => r.type === "session_cleared")?.seq || 0;
  for (const [messageIndex, message] of detail.messages.entries()) {
    if (message.role === "tool") continue;
    makeMessage(message.role, message.content || "", message.reasoning || "");
    const response = message.role === "assistant" ? records.find((r) => r.type === "model_response" && r.seq > responseCursor
      && (message.content || "").startsWith(r.data.text || "")
      && JSON.stringify(r.data.tool_calls || []) === JSON.stringify((message.tool_calls || []).map(({id, name}) => ({id, name})))) : null;
    if (response) responseCursor = response.seq;
    for (const call of message.tool_calls || []) {
      const matchesCall = (r) => r.data.call_id === call.id && (!response || (r.run_id === response.run_id && r.data.step === response.data.step));
      const result = [...records].reverse().find((r) => r.type === "tool_call_end" && matchesCall(r));
      const stored = detail.messages.slice(messageIndex + 1, messageIndex + 1 + message.tool_calls.length).find((m) => m.role === "tool" && m.tool_call_id === call.id);
      toolCard(call, result?.data || (stored ? {output: stored.content, is_error: stored.content?.startsWith("Error:")} : null),
        result?.seq || records.find((r) => r.type === "tool_call_start" && matchesCall(r))?.seq);
    }
  }
  if (state.busy) {
    const start = [...records].reverse().find((r) => r.type === "model_request_start");
    if (start && !records.some((r) => r.type === "model_response" && r.run_id === start.run_id && r.data.step === start.data.step)) {
      const current = records.filter((r) => r.seq > start.seq && r.run_id === start.run_id);
      state.liveMessage = makeMessage("assistant", current.filter((r) => r.type === "model_text_delta").map((r) => r.data.text).join(""),
        current.filter((r) => r.type === "model_reasoning_delta").map((r) => r.data.text).join(""));
    }
  }
  scrollChat(true);
}
async function refreshDetail(generation = state.generation) {
  if (!state.id) return;
  const detail = await api(sessionPath());
  if (generation !== state.generation) return;
  state.detail = detail; updateContext(detail);
  setBusy(detail.status, detail.error, detail.result?.stop_reason); renderHistory(detail);
  const usage = detail.result?.usage;
  $("run-meta").textContent = usage?.total_tokens ? `${usage.total_tokens.toLocaleString()} tokens · ${detail.result.steps} steps` : "";
}
async function selectSession(id) {
  if (state.source) state.source.close();
  const generation = ++state.generation; state.id = id; state.events.clear(); state.selected = null;
  state.detailGeneration++; state.follow = true; state.page = 0; state.filter = "all"; state.search = "";
  $("event-search").value = ""; $("event-detail").hidden = true; $("workspace").classList.remove("sidebar-open");
  $("prompt").value = ""; renderSessions(); updateFilter();
  let after = 0;
  while (true) {
    const page = await api(sessionPath(`/events?after=${after}&limit=500`));
    if (generation !== state.generation) return;
    page.events.forEach((event) => state.events.set(event.seq, event)); after = page.next;
    if (after >= page.total) break;
  }
  await refreshDetail(generation);
  if (generation !== state.generation) return;
  renderEvents();
  $("download-trace").href = sessionPath("/trace"); $("download-trace").setAttribute("aria-disabled", "false");
  const source = state.source = new EventSource(sessionPath(`/stream?after=${after}`));
  source.onopen = () => { if (generation === state.generation) { $("stream-state").classList.remove("offline"); $("stream-state").lastChild.textContent = " LIVE"; } };
  source.onerror = () => { if (generation === state.generation) { $("stream-state").classList.add("offline"); $("stream-state").lastChild.textContent = " 重连中"; } };
  source.addEventListener("trace_error", (message) => {
    if (generation !== state.generation) return;
    source.close(); $("stream-state").classList.add("offline"); $("stream-state").lastChild.textContent = " TRACE 错误";
    toast(JSON.parse(message.data).error, true);
  });
  source.onmessage = (message) => {
    if (generation !== state.generation) return;
    const event = JSON.parse(message.data); if (state.events.has(event.seq)) return;
    state.events.set(event.seq, event); liveEvent(event, generation); queueRender();
    const selected = state.events.get(state.selected);
    if (selected?.data.request_id && (selected.data.request_id === event.data.request_id || event.type === "agent_finish") && !state.detailTimer) {
      state.detailTimer = setTimeout(() => {
        state.detailTimer = null;
        if (generation === state.generation && !$("event-detail").hidden && state.selected) inspectEvent(state.selected, true).catch(showError);
      }, 700);
    }
  };
  source.addEventListener("status", (message) => {
    if (generation !== state.generation) return;
    const status = JSON.parse(message.data), wasBusy = state.busy;
    setBusy(status.status, status.error, status.stop_reason);
    if (wasBusy && !state.busy) { refreshDetail(generation).catch(showError); refreshSessions().catch(showError); }
  });
  history.replaceState(null, "", `#${id}`);
}
function liveEvent(event, generation) {
  const data = event.data;
  if (event.type === "agent_start") { state.liveMessage = null; setBusy("running"); }
  else if (event.type === "user_message") { makeMessage("user", data.text); scrollChat(true); }
  else if (event.type === "model_request_start") {
    state.liveMessage = makeMessage("assistant"); state.liveMessage.content.append(node("span", "pending-marker", "正在等待模型…")); scrollChat();
  } else if (event.type === "model_text_delta" || event.type === "model_reasoning_delta") {
    if (!state.liveMessage) state.liveMessage = makeMessage("assistant");
    const live = state.liveMessage;
    if (event.type === "model_text_delta") { live.text += data.text || ""; markdown(live.content, live.text); }
    else { live.reasoning += data.text || ""; live.reason.hidden = false; live.reasonText.textContent = live.reasoning; }
    scrollChat();
  } else if (event.type === "model_response") {
    const live = state.liveMessage;
    if (live) {
      // The summary is bounded; read the original to finish a long response exactly.
      api(sessionPath(`/events/${event.seq}`)).then((record) => {
        if (generation !== state.generation || !live.el.isConnected) return;
        live.text = record.data.text || ""; markdown(live.content, live.text);
        live.reasoning = record.data.reasoning || ""; live.reason.hidden = !live.reasoning; live.reasonText.textContent = live.reasoning;
      }).catch(showError);
    }
  } else if (event.type === "tool_call_start") { toolCard({id: data.call_id, ...data}, null, event.seq); scrollChat(); }
  else if (event.type === "tool_call_end") {
    const old = state.tools.get(data.call_id); const card = toolCard({id: data.call_id, ...data}, data, event.seq);
    if (old) { old.replaceWith(card); } scrollChat();
  } else if (event.type === "agent_finish") {
    setBusy("idle", data.error, data.stop_reason); refreshDetail(generation).catch(showError); refreshSessions().catch(showError);
  } else if (event.type === "agent_error") toast(data.message || "运行出错", true);
}
function category(event) {
  if (event.type.includes("request")) return "request";
  if (event.type.startsWith("tool_")) return "tool";
  if (event.type.includes("response") || event.type.includes("delta")) return "response";
  return "other";
}
function filteredEvents() {
  return [...state.events.values()].filter((event) => {
    if (state.filter !== "all" && category(event) !== state.filter) return false;
    return !state.search || `${event.seq} ${event.type} ${labels[event.type] || ""} ${JSON.stringify(event.data)}`.toLowerCase().includes(state.search);
  });
}
function queueRender() {
  if (state.renderPending) return; state.renderPending = true;
  requestAnimationFrame(() => { state.renderPending = false; renderEvents(); });
}
function renderEvents() {
  const all = [...state.events.values()], filtered = filteredEvents(), pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  if (state.follow) state.page = pages - 1; state.page = Math.min(state.page, pages - 1);
  $("request-count").textContent = all.filter((e) => e.type === "api_request").length;
  $("tool-count").textContent = all.filter((e) => e.type === "tool_call_start").length;
  $("event-count").textContent = all.length.toLocaleString();
  const list = $("event-list"), stick = state.follow; list.replaceChildren();
  if (!filtered.length) {
    const empty = node("div", "trace-empty"); empty.append(node("span", "", "⌁"), node("strong", "", all.length ? "没有匹配的事件" : "等待第一条记录"), node("p", "", all.length ? "尝试切换筛选或搜索词。" : "发送任务后，完整运行过程会出现在这里。")); list.append(empty);
  }
  for (const event of filtered.slice(state.page * PAGE_SIZE, (state.page + 1) * PAGE_SIZE)) {
    const cat = category(event), data = event.data;
    const row = node("button", `event-row ${state.selected === event.seq ? "selected" : ""}`);
    row.append(node("span", `event-icon ${cat} ${data.is_error || event.type === "agent_error" ? "error" : ""}`, cat === "request" ? "↗" : cat === "response" ? "↙" : cat === "tool" ? "⌘" : "·"));
    const info = node("span", "event-info");
    info.append(node("span", "event-name", `${event.type}${data.name ? ` · ${data.name}` : ""}`));
    const preview = data.message || data.output || data.text || data.arguments || (data.request_id ? `${data.request_id.slice(0, 12)}${data.status_code ? ` · HTTP ${data.status_code}` : ""}` : data.stop_reason || labels[event.type] || "");
    info.append(node("span", "event-preview", typeof preview === "string" ? preview.replace(/\s+/g, " ") : JSON.stringify(preview)));
    info.append(node("span", "event-time", new Date(event.timestamp).toLocaleTimeString("zh-CN", {hour12: false})));
    row.append(info, node("span", "event-seq", `#${event.seq}`));
    row.title = `#${event.seq} ${labels[event.type] || event.type}`;
    row.addEventListener("click", () => inspectEvent(event.seq).catch(showError)); list.append(row);
  }
  $("page-label").textContent = filtered.length ? `${state.page + 1} / ${pages} · ${filtered.length} 条` : "0 条记录";
  $("previous-page").disabled = state.page === 0; $("next-page").disabled = state.page >= pages - 1;
  $("follow-events").classList.toggle("active", state.follow); $("follow-events").setAttribute("aria-pressed", String(state.follow));
  if (stick) list.scrollTop = list.scrollHeight;
}
function updateFilter() {
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.classList.toggle("active", button.dataset.filter === state.filter);
    button.setAttribute("aria-selected", String(button.dataset.filter === state.filter));
  }); renderEvents();
}
function decodeBase64(value) { return new TextDecoder().decode(Uint8Array.from(atob(value), (ch) => ch.charCodeAt(0))); }
function pretty(value) { return JSON.stringify(value, null, 2); }
function prettyBody(value) { try { return pretty(JSON.parse(value)); } catch { return value; } }
async function inspectEvent(seq, refresh = false) {
  const generation = state.generation, requestGeneration = ++state.detailGeneration;
  const previousView = refresh ? state.views[state.viewIndex]?.name : null;
  const previousScroll = refresh ? $("detail-content").scrollTop : 0;
  state.selected = seq; renderEvents(); $("event-detail").hidden = false;
  if (!refresh) {
    $("detail-title").textContent = `#${seq} · 正在读取完整记录…`;
    $("detail-content").textContent = ""; $("detail-tabs").replaceChildren();
  }
  const record = await api(sessionPath(`/events/${seq}`));
  if (generation !== state.generation || requestGeneration !== state.detailGeneration) return;
  const views = [];
  if (record.data.request_id) {
    const request = await api(sessionPath(`/requests/${encodeURIComponent(record.data.request_id)}`));
    if (generation !== state.generation || requestGeneration !== state.detailGeneration) return;
    const wire = request.records.find((r) => r.type === "api_request");
    const normalized = request.records.find((r) => r.type === "model_request_start");
    if (wire) views.push({name: "请求 body", text: prettyBody(decodeBase64(wire.data.body_base64))});
    if (request.response_text !== null) views.push({name: "原始响应", text: request.response_text || "（尚未收到响应字节）"});
    if (normalized) views.push({name: "模型输入", text: pretty(normalized.data)});
    views.push({name: "请求全记录", text: pretty(request)});
  }
  if (record.type === "tool_call_end") views.push({name: "工具全文", text: record.data.full_output ?? record.data.output ?? ""}, {name: "模型所见", text: record.data.model_content ?? record.data.output ?? ""});
  if (record.type === "context_loaded") views.push({name: "上下文", text: record.data.content || ""});
  views.push({name: "事件 JSON", text: pretty(record)});
  state.views = views; state.viewIndex = Math.max(0, views.findIndex((view) => view.name === previousView));
  $("detail-title").textContent = `#${seq} · ${record.type}`;
  renderDetailViews();
  $("detail-content").scrollTop = previousScroll;
}
function renderDetailViews() {
  $("detail-tabs").replaceChildren();
  state.views.forEach((view, index) => {
    const button = node("button", index === state.viewIndex ? "active" : "", view.name);
    button.addEventListener("click", () => { state.viewIndex = index; renderDetailViews(); }); $("detail-tabs").append(button);
  }); $("detail-content").textContent = state.views[state.viewIndex]?.text || "";
}
function openInspector() {
  $("workspace").classList.remove("inspector-hidden"); $("workspace").classList.add("inspector-open");
}
function showNewDialog() {
  $("new-workspace").value = state.detail?.workspace || state.config.workspace;
  $("new-model").value = state.detail?.model || state.config.model; $("new-error").hidden = true;
  $("new-dialog").showModal();
}
async function sendPrompt(event) {
  event.preventDefault(); const text = $("prompt").value.trim(); if (!text || state.busy) return;
  $("send-button").disabled = true;
  try {
    if (!state.id) { const session = await api("/api/sessions", {}); await refreshSessions(); await selectSession(session.session_id); }
    const generation = state.generation;
    const result = await api(sessionPath("/runs"), {text});
    if (generation !== state.generation) return;
    $("prompt").value = ""; setBusy(result.status, result.error, result.result?.stop_reason);
    if (result.status === "idle") await refreshDetail(generation);
  } catch (error) { showError(error); } finally { $("send-button").disabled = false; }
}

$("composer-form").addEventListener("submit", sendPrompt);
$("prompt").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); $("composer-form").requestSubmit(); } });
$("new-session").addEventListener("click", showNewDialog);
$("new-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("create-button").disabled = true; $("new-error").hidden = true;
  try { const session = await api("/api/sessions", {workspace: $("new-workspace").value.trim(), model: $("new-model").value.trim()});
    $("new-dialog").close(); await refreshSessions(); await selectSession(session.session_id); $("prompt").focus();
  } catch (error) { $("new-error").textContent = error.message; $("new-error").hidden = false; }
  finally { $("create-button").disabled = false; }
});
$("stop-button").addEventListener("click", async () => { try { const status = await api(sessionPath("/cancel"), {}); setBusy(status.status, status.error, status.result?.stop_reason); } catch (error) { showError(error); } });
document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
document.querySelectorAll("[data-prompt]").forEach((button) => button.addEventListener("click", () => { $("prompt").value = button.dataset.prompt; $("prompt").focus(); }));
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => { state.filter = button.dataset.filter; state.page = 0; updateFilter(); }));
$("event-search").addEventListener("input", () => { state.search = $("event-search").value.trim().toLowerCase(); state.page = 0; renderEvents(); });
$("previous-page").addEventListener("click", () => { state.follow = false; state.page--; renderEvents(); });
$("next-page").addEventListener("click", () => { state.follow = false; state.page++; renderEvents(); });
$("follow-events").addEventListener("click", () => { state.follow = !state.follow; renderEvents(); });
$("close-detail").addEventListener("click", () => { state.detailGeneration++; $("event-detail").hidden = true; state.selected = null; renderEvents(); });
$("copy-detail").addEventListener("click", async () => { try { await navigator.clipboard.writeText($("detail-content").textContent); toast("已复制当前详情"); } catch { toast("无法访问剪贴板，可选中详情手动复制", true); } });
$("clear-session").addEventListener("click", () => { if (state.id && !state.busy) $("clear-dialog").showModal(); });
$("confirm-clear").addEventListener("click", async () => {
  try { await api(sessionPath("/clear"), {}); $("clear-dialog").close(); await refreshDetail(); await refreshSessions(); toast("对话已清空，trace 已保留"); } catch (error) { showError(error); }
});
$("resource-button").addEventListener("click", async () => {
  const list = $("resource-list"); list.replaceChildren();
  try {
    if (state.id) state.detail = await api(sessionPath());
    const resources = state.detail?.resources || {skills: [], prompts: []};
    for (const item of [...resources.skills.map((s) => ({command: `/skill:${s.name}`, description: s.description})), ...resources.prompts.map((name) => ({command: `/prompt:${name}`, description: "Prompt template"}))]) {
      const button = node("button", "resource-item"); button.append(node("strong", "", item.command), node("small", "", item.description));
      button.addEventListener("click", () => { $("prompt").value = `${item.command} ${$("prompt").value}`; $("resource-dialog").close(); $("prompt").focus(); }); list.append(button);
    }
    if (!list.children.length) list.append(node("p", "resource-empty", state.id ? "此会话暂未发现资源。可在工作区配置 .agents/skills 或使用 --skill-dir 启动服务。已存会话的资源会在首次运行时重新发现。" : "创建会话后，可查看该工作区发现的资源。"));
    $("resource-dialog").showModal();
  } catch (error) { showError(error); }
});
$("config-button").addEventListener("click", () => {
  const dl = $("config-detail"); dl.replaceChildren();
  for (const [label, value] of [["工作区", state.config.workspace], ["模型", state.config.model], ["API endpoint", state.config.base_url], ["API key", state.config.configured ? "已配置（保留在服务端）" : "尚未配置 OPENAI_API_KEY"], ["最大步数", state.config.max_steps]]) dl.append(node("dt", "", label), node("dd", "", String(value)));
  $("config-dialog").showModal();
});
$("sidebar-toggle").addEventListener("click", () => $("workspace").classList.toggle("sidebar-open"));
$("inspector-close").addEventListener("click", () => $("workspace").classList.remove("inspector-open"));
$("inspect-toggle").addEventListener("click", () => {
  if (matchMedia("(max-width:1000px)").matches) $("workspace").classList.toggle("inspector-open");
  else $("workspace").classList.toggle("inspector-hidden");
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { $("workspace").classList.remove("sidebar-open", "inspector-open"); }
  if (event.key.toLowerCase() === "n" && !event.ctrlKey && !event.metaKey && !event.altKey && !/INPUT|TEXTAREA/.test(document.activeElement.tagName) && !document.querySelector("dialog[open]")) showNewDialog();
});
document.addEventListener("click", (event) => {
  if (matchMedia("(max-width:1000px)").matches && !event.target.closest(".inspector, #inspect-toggle") && !document.querySelector("dialog[open]")) $("workspace").classList.remove("inspector-open");
  if (matchMedia("(max-width:680px)").matches && !event.target.closest(".sidebar, #sidebar-toggle")) $("workspace").classList.remove("sidebar-open");
});
window.addEventListener("beforeunload", () => state.source?.close());

async function init() {
  state.config = await api("/api/config"); updateContext(state.config); $("clear-session").disabled = true;
  $("provider-label").textContent = new URL(state.config.base_url).host;
  $("provider-label").title = state.config.base_url; await refreshSessions();
  const id = location.hash.slice(1);
  if (state.sessions.some((s) => s.session_id === id)) await selectSession(id);
  if (!state.config.configured) toast("服务端尚未配置 OPENAI_API_KEY，配置后重启即可开始任务", true);
}
init().catch(showError);
