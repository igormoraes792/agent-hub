"use strict";
/* Agent Hub — interface. Sem framework: estado em `S`, renderização direta no DOM. */

const S = {
  meta: null,
  chats: [],
  agents: [],
  chatId: null,
  view: "start",
  pending: {},        // chatId -> [request]
  stream: null,       // elemento da resposta em streaming do chat aberto
  toolEls: {},        // toolId -> <details>
  planEls: {},        // planId -> elemento
  editingAgentId: null,
  slashIndex: 0,
};

const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = (text) => {
  try { return DOMPurify.sanitize(marked.parse(text || "", { breaks: true, gfm: true })); }
  catch { return `<p>${esc(text)}</p>`; }
};
function h(tag, attrs = {}, html = "") {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  if (html) el.innerHTML = html;
  return el;
}
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}
async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || res.statusText);
  return data;
}

/* ------------------------------------------------------------------ opções por provedor */
const CODEX_APPROVAL = [
  { value: "on-request", label: "Pedir quando precisar" },
  { value: "untrusted", label: "Pedir para quase tudo" },
  { value: "never", label: "Nunca pedir" },
];
const EFFORT_LABEL = { minimal: "mínimo", low: "baixo", medium: "médio", high: "alto", xhigh: "muito alto", max: "máximo" };

function modelOptions(provider) {
  if (provider === "claude") return (S.meta?.claude.models || []).map((m) => ({ value: m.value, label: m.displayName || m.value, title: m.description }));
  const def = S.meta?.codex.defaultModel;
  return [{ value: "", label: `Padrão do Codex${def ? ` (${def})` : ""}` },
    ...(S.meta?.codex.models || []).map((m) => ({ value: m.value, label: m.displayName || m.value }))];
}
function effortOptions(provider) {
  const levels = provider === "claude" ? S.meta?.claude.effortLevels : S.meta?.codex.effortLevels;
  return [{ value: "", label: "Padrão" }, ...(levels || []).map((l) => ({ value: l, label: EFFORT_LABEL[l] || l }))];
}
function selectHtml(id, options, value, attrs = "") {
  return `<select id="${id}" ${attrs}>${options.map((o) =>
    `<option value="${esc(o.value)}" ${o.title ? `title="${esc(o.title)}"` : ""} ${String(o.value) === String(value ?? "") ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
}
/* Campos de modelo/esforço/permissões. `prefix` diferencia os formulários que usam o mesmo bloco. */
function providerFieldsHtml(prefix, provider, v = {}) {
  let html = `<label>Modelo ${selectHtml(`${prefix}-model`, modelOptions(provider), v.model ?? (provider === "claude" ? "default" : ""))}</label>
    <label>Esforço de raciocínio ${selectHtml(`${prefix}-effort`, effortOptions(provider), v.effort)}</label>`;
  if (provider === "claude") {
    html += `<label>Permissões ${selectHtml(`${prefix}-permission_mode`, S.meta.claude.permissionModes, v.permission_mode || "default")}</label>`;
  } else {
    html += `<label>Sandbox ${selectHtml(`${prefix}-sandbox`, S.meta.codex.sandboxModes, v.sandbox || "workspace-write")}</label>
      <label>Aprovação ${selectHtml(`${prefix}-approval_policy`, CODEX_APPROVAL, v.approval_policy || "on-request")}</label>`;
  }
  return html;
}
function readProviderFields(prefix, provider) {
  const val = (f) => $(`#${prefix}-${f}`)?.value;
  const out = { model: val("model"), effort: val("effort") };
  if (provider === "claude") out.permission_mode = val("permission_mode");
  else { out.sandbox = val("sandbox"); out.approval_policy = val("approval_policy"); }
  return out;
}

/* ------------------------------------------------------------------ navegação */
function show(view) {
  S.view = view;
  for (const v of ["chat", "agents", "settings", "import", "start"]) $(`#view-${v}`).classList.toggle("hidden", v !== view);
  $("#sidebar").classList.remove("open");
  renderChatList();
  renderSideAgents();
}

/* ------------------------------------------------------------------ lista de chats */
function renderChatList() {
  const q = $("#chat-search").value.trim().toLowerCase();
  const list = $("#chat-list");
  list.innerHTML = "";
  const agentsById = Object.fromEntries(S.agents.map((a) => [a.id, a]));
  const groups = { "Hoje": [], "Últimos 7 dias": [], "Anteriores": [] };
  const now = Date.now() / 1000;
  for (const c of S.chats) {
    if (q && !c.title.toLowerCase().includes(q)) continue;
    const age = now - (c.updated_at || 0);
    (age < 86400 ? groups["Hoje"] : age < 7 * 86400 ? groups["Últimos 7 dias"] : groups["Anteriores"]).push(c);
  }
  for (const [label, items] of Object.entries(groups)) {
    if (!items.length) continue;
    list.append(h("div", { class: "group-label" }, esc(label)));
    for (const c of items) {
      const agent = agentsById[c.agent_id];
      const waiting = (S.pending[c.id] || []).length > 0;
      const st = waiting ? "waiting" : c.status;
      const el = h("a", { class: `chat-item ${c.id === S.chatId && S.view === "chat" ? "active" : ""}`, href: `#/chat/${c.id}`, title: c.title },
        `<span class="dot ${c.provider}"></span>
         <div class="t">${esc(c.title)}${agent ? `<div class="sub">🤖 ${esc(agent.name)}</div>` : ""}</div>
         <span class="st ${st}" title="${st === "running" ? "Análise em andamento" : st === "waiting" ? "Aguardando sua aprovação" : ""}"></span>`);
      el.addEventListener("contextmenu", (e) => { e.preventDefault(); openChatMenu(c, e.clientX, e.clientY); });
      list.append(el);
    }
  }
  if (!list.children.length) list.append(h("div", { class: "muted small", style: "padding:10px" }, q ? "Nada encontrado." : "Nenhum chat ainda."));
  renderSideAgents();  // o ícone de carregamento dos agentes acompanha o estado dos chats
}

/* Menu do botão direito sobre um chat da barra lateral. */
function openChatMenu(chat, x, y) {
  const menu = $("#ctx-menu");
  menu.innerHTML = "";
  const item = (label, fn, cls = "") => menu.append(h("button", { class: cls, role: "menuitem", onclick: () => { closeChatMenu(); fn(); } }, label));
  item("Abrir", () => (location.hash = `#/chat/${chat.id}`));
  item("✏️ Renomear", () => renameChat(chat));
  menu.append(h("div", { class: "sep" }));
  item("🗑 Excluir chat", () => deleteChat(chat), "danger");
  menu.classList.remove("hidden");
  // mantém o menu dentro da janela
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.min(x, innerWidth - r.width - 8) + "px";
  menu.style.top = Math.min(y, innerHeight - r.height - 8) + "px";
}
function closeChatMenu() { $("#ctx-menu").classList.add("hidden"); }

async function renameChat(chat) {
  const title = prompt("Novo nome do chat:", chat.title);
  if (!title || !title.trim() || title.trim() === chat.title) return;
  try { upsertChat(await api("PATCH", `/api/chats/${chat.id}`, { title: title.trim() })); }
  catch (e) { toast(e.message); }
}

async function deleteChat(chat) {
  const running = chat.status && chat.status !== "idle";
  const msg = `Excluir o chat "${chat.title}"?` +
    (running ? "\n\nEle está trabalhando agora e será interrompido." : "") +
    (chat.source_id ? "\n\nA conversa original no VS Code não é afetada." : "");
  if (!confirm(msg)) return;
  try { await api("DELETE", `/api/chats/${chat.id}`); toast("Chat excluído."); }
  catch (e) { toast(e.message); }
}

function upsertChat(chat) {
  const i = S.chats.findIndex((c) => c.id === chat.id);
  if (i >= 0) S.chats[i] = { ...S.chats[i], ...chat };
  else S.chats.unshift(chat);
  S.chats.sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  renderChatList();
  if (chat.id === S.chatId) renderChatHeader();
}

/* ------------------------------------------------------------------ chat aberto */
const currentChat = () => S.chats.find((c) => c.id === S.chatId);

async function openChat(id) {
  if (S.chatId !== id) { S.anexos.chat = []; renderTray("chat"); }  // anexos não "vazam" para outro chat
  S.chatId = id;
  show("chat");
  const msgs = $("#messages");
  msgs.innerHTML = '<div class="msg-wrap"><div class="sys">Carregando…</div></div>';
  let data;
  try { data = await api("GET", `/api/chats/${id}/events`); }
  catch (e) { msgs.innerHTML = `<div class="msg-wrap"><div class="err-box">${esc(e.message)}</div></div>`; return; }
  if (S.chatId !== id) return;
  upsertChat(data.chat);
  S.pending[id] = data.pending || [];
  S.toolEls = {}; S.planEls = {}; S.stream = null; S.activity = null;
  msgs.innerHTML = "";
  const wrap = h("div", { class: "msg-wrap no-anim" });  // histórico aparece de uma vez, sem animação
  msgs.append(wrap);
  if (!data.events.length) wrap.append(h("div", { class: "sys" }, introText(data.chat)));
  for (const ev of data.events) safeRender(ev, wrap);
  if (data.chat.status === "idle") finishActivity();
  requestAnimationFrame(() => wrap.classList.remove("no-anim"));
  renderChatHeader();
  renderApprovals();
  scrollDown(true);
  $("#input").focus();
}

function introText(chat) {
  const agent = S.agents.find((a) => a.id === chat.agent_id);
  return esc(`${chat.provider === "claude" ? "Claude" : "Codex"}${agent ? ` · agente ${agent.name}` : ""} · pasta ${chat.cwd}`) +
    "<br>A sessão é aberta quando você enviar a primeira mensagem.";
}

function renderChatHeader() {
  const c = currentChat();
  if (!c) return;
  const prov = $("#chat-provider");
  prov.textContent = c.provider;
  prov.className = `badge ${c.provider}`;
  if (document.activeElement !== $("#chat-title")) $("#chat-title").value = c.title;
  const waiting = (S.pending[c.id] || []).length > 0;
  const st = waiting ? "waiting" : c.status;
  const pill = $("#chat-status");
  pill.className = `status-pill ${st}`;
  pill.textContent = { running: "● trabalhando…", waiting: "⏳ aguardando você", idle: "" }[st] || "";
  $("#btn-stop").classList.toggle("hidden", st === "idle");
  S.chatBusy = st !== "idle";
  atualizarBotaoEnviar("chat");
  const agent = S.agents.find((a) => a.id === c.agent_id);
  const modelLabel = modelOptions(c.provider).find((m) => m.value === (c.model ?? ""))?.label || c.model || "padrão";
  $("#composer-info").textContent = `${modelLabel}${c.effort ? " · " + (EFFORT_LABEL[c.effort] || c.effort) : ""}${agent ? " · 🤖 " + agent.name : ""}`;
  if (!$("#chat-settings").classList.contains("hidden")) renderChatSettings();
}

function renderChatSettings() {
  const c = currentChat();
  const bar = $("#chat-settings");
  const started = c.session_id || c.thread_id;
  const agent = S.agents.find((a) => a.id === c.agent_id);
  // Chat de agente: modelo e permissões só mudam no agente; aqui só aparece o resumo.
  const fields = agent
    ? `<div class="agent-locked"><div><b>🤖 ${esc(agent.name)}</b> · ${esc(chatSettingsSummary(c))}</div>
         <div class="hint">Modelo, esforço e permissões deste chat vêm do agente e só mudam nas configurações dele.</div>
         <button class="btn" id="cs-edit-agent" style="margin-top:6px">✏️ Editar agente</button></div>`
    : providerFieldsHtml("cs", c.provider, c);
  bar.innerHTML = fields +
    `<div class="grow"></div>
     <div><div class="cwd" title="Pasta de trabalho">📁 ${esc(c.cwd)}</div>
     <div class="row gap" style="margin-top:6px">
       <button class="btn" id="cs-restart" title="Encerra o processo; a próxima mensagem reabre a sessão com o histórico">↻ Reiniciar sessão</button>
       <button class="btn" id="cs-delete">🗑 Excluir</button>
     </div></div>`;
  for (const sel of $$("select", bar)) {
    sel.addEventListener("change", async () => {
      try { upsertChat(await api("PATCH", `/api/chats/${c.id}`, readProviderFields("cs", c.provider))); toast("Configuração aplicada."); }
      catch (e) { toast(e.message); }
    });
  }
  $("#cs-restart").onclick = async () => { await api("POST", `/api/chats/${c.id}/restart`); toast("Sessão reiniciada."); };
  $("#cs-delete").onclick = () => deleteChat(c);
  if ($("#cs-edit-agent")) $("#cs-edit-agent").onclick = () => (location.hash = `#/agents/${agent.id}`);
  if (started && !agent) bar.title = "Mudanças valem a partir da próxima mensagem.";
}

function chatSettingsSummary(c) {
  const find = (list, v) => (list || []).find((o) => String(o.value) === String(v ?? ""))?.label;
  const parts = [find(modelOptions(c.provider), c.model) || c.model || "modelo padrão"];
  if (c.effort) parts.push(`esforço ${EFFORT_LABEL[c.effort] || c.effort}`);
  if (c.provider === "claude") parts.push(find(S.meta.claude.permissionModes, c.permission_mode) || c.permission_mode);
  else parts.push(find(S.meta.codex.sandboxModes, c.sandbox) || c.sandbox, find(CODEX_APPROVAL, c.approval_policy) || c.approval_policy);
  return parts.filter(Boolean).join(" · ");
}

/* ------------------------------------------------------------------ renderização de eventos */
function toolSummary(name, input) {
  if (!input || typeof input !== "object") return "";
  const pick = input.command || input.file_path || input.path || input.pattern || input.url || input.query ||
    input.description || input.prompt || input.skill || input.notebook_path || (input.arquivos && input.arquivos.join(", "));
  if (pick) return String(pick).replace(/\s+/g, " ").slice(0, 200);
  const s = JSON.stringify(input);
  return s.length > 2 ? s.slice(0, 200) : "";
}
function toolIcon(name) {
  const n = String(name || "").toLowerCase();
  if (n.includes("bash") || n.includes("shell") || n.includes("powershell")) return "⌘";
  if (n.includes("read")) return "📄";
  if (n.includes("edit") || n.includes("write") || n.includes("arquivo")) return "✏️";
  if (n.includes("grep") || n.includes("glob") || n.includes("search")) return "🔍";
  if (n.includes("web") || n.includes("fetch")) return "🌐";
  if (n.includes("task") || n.includes("agent") || n.includes("subagente")) return "🤖";
  if (n.startsWith("mcp")) return "🔌";
  if (n.includes("todo")) return "☑️";
  return "🔧";
}
function formatInput(input) {
  if (input == null) return "";
  if (typeof input === "string") return input;
  if (input.command && Object.keys(input).length <= 3) return input.command + (input.cwd ? `\n\n# em ${input.cwd}` : "");
  return JSON.stringify(input, null, 2);
}

/* Um evento com problema nunca pode impedir que o resto da conversa apareça. */
function safeRender(ev, wrap) {
  try { renderEvent(ev, wrap); }
  catch (err) {
    console.error("evento não exibido", ev, err);
    (wrap || $("#messages .msg-wrap"))?.append(h("div", { class: "sys warn" }, `[um item (${esc(ev.kind)}) não pôde ser exibido]`));
  }
}

/* Nome amigável e categoria de cada ação técnica (o detalhe completo fica dentro do grupo recolhido). */
function toolLabel(name, input = {}) {
  const n = String(name || "").toLowerCase();
  const arquivo = (p) => String(p || "").split(/[\\/]/).pop();
  if (n.includes("bash") || n.includes("shell") || n.includes("powershell")) return input?.description || "Executando comando";
  if (n === "read") return `Lendo ${arquivo(input?.file_path) || "arquivo"}`;
  if (n === "write") return `Criando ${arquivo(input?.file_path) || "arquivo"}`;
  if (n === "edit" || n.includes("arquivo")) return `Editando ${arquivo(input?.file_path) || "arquivo"}`;
  if (n === "grep" || n === "glob") return "Procurando nos arquivos";
  if (n.startsWith("web")) return "Pesquisando na web";
  if (n === "agent" || n === "task" || n.includes("subagente")) return `Delegando: ${input?.description || input?.subagent_type || "subagente"}`;
  if (n.includes("superset")) return "Consultando o Superset";
  if (n.includes("powerbi")) return "Consultando o Power BI";
  if (n === "todowrite") return "Atualizando o plano";
  if (n === "toolsearch") return "Preparando ferramentas";
  if (n === "askuserquestion") return "Preparando perguntas";
  if (n.startsWith("mcp")) return `Usando ${name.split("__")[1] || "integração"}`;
  return name;
}
function toolCategory(name) {
  const n = String(name || "").toLowerCase();
  if (n.includes("bash") || n.includes("shell") || n.includes("powershell")) return "comandos";
  if (["read", "write", "edit", "glob", "grep", "notebookedit"].includes(n) || n.includes("arquivo")) return "arquivos";
  if (n.includes("superset")) return "Superset";
  if (n.includes("powerbi")) return "Power BI";
  if (n.startsWith("web")) return "web";
  if (n === "agent" || n === "task" || n.includes("subagente")) return "subagentes";
  return "outros";
}

/* Grupo de atividade: junta os passos técnicos de um trecho da resposta numa linha discreta,
   que mostra o passo atual enquanto trabalha e um resumo quando termina. Clique para ver os detalhes. */
function activityGroup(wrap) {
  const g = S.activity;
  if (g && g.box.isConnected && g.box.parentNode === wrap && wrap.lastElementChild === g.box && !g.done) return g;
  const box = h("div", { class: "msg activity-box" });
  const det = h("details", { class: "activity running" },
    `<summary><span class="act-icon"></span><span class="act-text">Trabalhando…</span><span class="act-meta"></span></summary><div class="act-body"></div>`);
  box.append(det);
  wrap.append(box);
  S.activity = { box, det, body: $(".act-body", det), count: 0, cats: {}, done: false };
  return S.activity;
}
function addActivity(wrap, node, label, category) {
  const g = activityGroup(wrap);
  g.body.append(node);
  if (category) {
    g.count++;
    g.cats[category] = (g.cats[category] || 0) + 1;
  }
  if (label) $(".act-text", g.det).textContent = `${label}…`;
  $(".act-meta", g.det).textContent = g.count ? `${g.count} ${g.count === 1 ? "etapa" : "etapas"}` : "";
  return g;
}
function finishActivity() {
  const g = S.activity;
  if (!g || g.done) return;
  g.done = true;
  g.det.classList.remove("running");
  g.det.classList.add("done");
  const cats = Object.entries(g.cats).sort((a, b) => b[1] - a[1]).map(([c, n]) => `${c} ×${n}`).join(" · ");
  $(".act-text", g.det).textContent = g.count ? `${g.count} ${g.count === 1 ? "etapa concluída" : "etapas concluídas"}` : "Preparação concluída";
  $(".act-meta", g.det).textContent = cats;
  if (!g.count) g.box.classList.add("sem-etapas");  // só avisos técnicos (ex.: conexão): não ocupa espaço na conversa
}

async function copiarTexto(texto) {
  try { await navigator.clipboard.writeText(texto); }
  catch {
    const ta = h("textarea");
    ta.value = texto;
    document.body.append(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

/* Ações que aparecem ao passar o mouse numa resposta (como no Claude Desktop). */
const ICON_COPIAR = `<svg viewBox="0 0 20 20" width="15" height="15" aria-hidden="true"><rect x="7" y="7" width="9" height="9" rx="2" stroke="currentColor" stroke-width="1.6" fill="none"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-6A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7" stroke="currentColor" stroke-width="1.6" fill="none"/></svg>`;
function acoesMensagem(el, texto) {
  if (el.querySelector(":scope > .msg-actions")) return;
  const bar = h("div", { class: "msg-actions" });
  const b = h("button", { class: "act-btn", type: "button", title: "Copiar resposta" }, ICON_COPIAR);
  b.onclick = async () => {
    await copiarTexto(texto);
    b.innerHTML = "✓";
    b.classList.add("ok");
    setTimeout(() => { b.innerHTML = ICON_COPIAR; b.classList.remove("ok"); }, 1500);
  };
  bar.append(b);
  el.append(bar);
}

/* Botão "Copiar" em cada bloco de código das respostas. */
function enhanceCode(el) {
  for (const pre of $$("pre", el)) {
    if (pre.dataset.copy) continue;
    pre.dataset.copy = "1";
    const holder = h("div", { class: "code-block" });
    pre.replaceWith(holder);
    holder.append(pre);
    const lang = ($("code", pre)?.className.match(/language-(\w+)/) || [])[1];
    const bar = h("div", { class: "code-bar" }, `<span>${esc(lang || "código")}</span>`);
    const btn = h("button", { class: "copy-btn", type: "button", title: "Copiar código" }, "⧉ Copiar");
    btn.onclick = async () => {
      await copiarTexto(($("code", pre) || pre).innerText);
      btn.textContent = "✓ Copiado";
      btn.classList.add("ok");
      setTimeout(() => { btn.textContent = "⧉ Copiar"; btn.classList.remove("ok"); }, 1600);
    };
    bar.append(btn);
    holder.prepend(bar);
  }
}
function mdNode(cls, text) {
  const el = h("div", { class: cls }, `<div class="md">${md(text)}</div>`);
  enhanceCode(el);
  return el;
}

/* Mensagens de sistema que o usuário precisa ver; as demais (conexão, tarefas internas) vão para o grupo de atividade. */
const SYS_VISIBLE = /^(Interrompido|Sessão|Importado|A pasta original)/;
const STATUS_PT = { completed: "concluída", failed: "falhou", stopped: "interrompida", killed: "interrompida", running: "em andamento" };

/* Uma linha só: avisos internos às vezes trazem o comando inteiro (dezenas de linhas). */
const PARECE_COMANDO = /^\s*(\w+=|cd\s|python|pwsh|powershell|bash|cmd|\$|\.\/|[A-Za-z]:[\\/])|&&|\$\(|\|\s*\w/;
function resumoLinha(texto, max = 140) {
  let t = String(texto || "").replace(/^Tarefa (completed|failed|stopped|killed|running):/, (_, s) => `Tarefa ${STATUS_PT[s] || s}:`);
  // Avisos antigos traziam o próprio comando como "nome" da tarefa: mostra um texto amigável (o comando fica no tooltip).
  const m = t.match(/^(Tarefa [^:]+): ([\s\S]*)$/);
  if (m && PARECE_COMANDO.test(m[2].split("\n")[0])) return `${m[1]}: comando em segundo plano`;
  const primeira = t.split("\n")[0].trim();
  const cortado = primeira.length > max || t.includes("\n");
  return (primeira.length > max ? primeira.slice(0, max) : primeira) + (cortado ? " …" : "");
}

function renderEvent(ev, wrap) {
  wrap = wrap || $("#messages .msg-wrap");
  if (!wrap) return;
  $("#live-indicator")?.remove();  // sai antes de desenhar (é recolocado no fim por updateLive)
  switch (ev.kind) {
    case "user": {
      finishActivity();
      const imgs = (ev.images || []).map((i) => {
        const url = `/api/chats/${encodeURIComponent(S.chatId)}/anexos/${encodeURIComponent(i.file)}`;
        return `<a href="${url}" target="_blank" rel="noopener" title="${esc(i.name)}"><img src="${url}" alt="${esc(i.name)}" loading="lazy"></a>`;
      }).join("");
      wrap.append(h("div", { class: "msg user" },
        `<div class="bubble">${imgs ? `<div class="bubble-imgs">${imgs}</div>` : ""}${ev.text ? `<div>${esc(ev.text)}</div>` : ""}</div>`));
      break;
    }
    case "assistant": {
      if (ev.parent) {  // texto intermediário de subagente: detalhe técnico
        addActivity(wrap, mdNode("act-note", ev.text), null, null);
        break;
      }
      finishActivity();
      if (S.stream && S.stream.isConnected) {  // reaproveita a bolha que já estava sendo escrita: sem "pulo"
        S.stream.classList.remove("streaming");
        $(".md", S.stream).innerHTML = md(ev.text);
        enhanceCode(S.stream);
        acoesMensagem(S.stream, ev.text);
        S.stream = null;
      } else {
        const node = mdNode(`msg assistant${ev.greeting ? " greeting" : ""}`, ev.text);
        wrap.append(node);
        if (!ev.greeting) acoesMensagem(node, ev.text);
      }
      break;
    }
    case "thinking":
      addActivity(wrap, h("details", { class: "thinking" }, `<summary>💭 Raciocínio</summary><pre>${esc(ev.text)}</pre>`), "Pensando", null);
      break;
    case "tool": {
      if (S.stream && !ev.parent) { S.stream.remove(); S.stream = null; }
      const d = h("details", { class: "tool" },
        `<summary><span class="ticon">${toolIcon(ev.name)}</span><span class="tname">${esc(toolLabel(ev.name, ev.input))}</span>
         <span class="tsum">${esc(toolSummary(ev.name, ev.input))}</span><span class="tstate">⏳</span></summary>
         <pre class="tin">${esc(formatInput(ev.input))}</pre>`);
      addActivity(wrap, d, toolLabel(ev.name, ev.input), toolCategory(ev.name));
      S.toolEls[ev.id] = d;
      if (ev.name === "TodoWrite" && ev.input?.todos) {
        renderPlan({ id: "todo", steps: ev.input.todos.map((t) => ({ step: t.content, status: t.status === "in_progress" ? "inProgress" : t.status })) }, wrap);
      }
      break;
    }
    case "tool_result": {
      const d = S.toolEls[ev.id];
      if (!d) break;
      $(".tstate", d).textContent = ev.is_error ? "✗" : "✓";
      if (ev.is_error) d.classList.add("err");
      if (ev.output) d.append(h("pre", { class: "tout" }, esc(ev.output)));
      break;
    }
    case "plan":
      renderPlan(ev, wrap);
      break;
    case "system":
      if (ev.level === "warn" || SYS_VISIBLE.test(ev.text || "")) wrap.append(h("div", { class: `sys ${ev.level || ""}` }, esc(ev.text)));
      else addActivity(wrap, h("div", { class: "act-sys", title: ev.text || "" }, esc(resumoLinha(ev.text))), null, null);
      break;
    case "error":
      finishActivity();
      wrap.append(h("div", { class: "msg" }, `<div class="err-box">${esc(ev.text)}</div>`));
      break;
    case "result": {
      if (S.stream) { S.stream.classList.remove("streaming"); S.stream = null; }
      finishActivity();
      const detalhes = [];
      if (ev.usage && (ev.usage.input || ev.usage.output)) detalhes.push(`${fmtTok(ev.usage.input)} tokens de entrada / ${fmtTok(ev.usage.output)} de saída`);
      if (ev.cost_usd) detalhes.push(`custo estimado ~US$ ${ev.cost_usd.toFixed(3)}`);
      const tempo = ev.duration_ms ? `${(ev.duration_ms / 1000).toFixed(ev.duration_ms < 10000 ? 1 : 0).replace(".", ",")}s` : "";
      wrap.append(h("div", { class: `result${ev.is_error ? " err" : ""}`, title: detalhes.join(" · ") },
        `${ev.is_error ? "✗ Terminou com erro" : "✓ Concluído"}${tempo ? ` em ${tempo}` : ""}`));
      break;
    }
  }
}
const fmtTok = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));

function renderPlan(ev, wrap) {
  const icon = { completed: "✅", inProgress: "▶️", pending: "⬜" };
  const html = `<div class="small muted" style="margin-bottom:4px">📋 Plano${ev.explanation ? " — " + esc(ev.explanation) : ""}</div>` +
    (ev.steps || []).map((s) => `<div class="step ${s.status}"><span>${icon[s.status] || "⬜"}</span><span>${esc(s.step)}</span></div>`).join("");
  let el = S.planEls[ev.id];
  if (el && el.isConnected) { el.innerHTML = html; return; }
  el = h("div", { class: "plan" }, html);
  const box = h("div", { class: "msg" });
  box.append(el);
  wrap.append(box);
  S.planEls[ev.id] = el;
}

/* Indicador ao vivo no fim da conversa: aparece quando o chat ainda trabalha mas nada está se mexendo na tela
   (ex.: subagentes rodando em segundo plano depois que a resposta principal terminou). */
function updateLive() {
  const wrap = $("#messages .msg-wrap");
  $("#live-indicator")?.remove();
  if (!wrap || S.view !== "chat") return;
  const c = currentChat();
  if (!c) return;
  const tasks = c.tasks || [];
  const waiting = (S.pending[c.id] || []).length > 0;
  const busy = c.status && c.status !== "idle" && !waiting;
  const activityLive = S.activity && !S.activity.done && wrap.lastElementChild === S.activity.box;
  const streaming = S.stream && S.stream.isConnected;
  if (!busy || (!tasks.length && (activityLive || streaming))) return;
  const texto = tasks.length ? `Em segundo plano: ${tasks.join(" · ")}` : "Trabalhando…";
  wrap.append(h("div", { id: "live-indicator", class: "live", title: texto },
    `<span class="live-spin"></span><span class="live-text">${esc(texto)}</span>`));
}

function onDelta(text) {
  const wrap = $("#messages .msg-wrap");
  if (!wrap) return;
  $("#live-indicator")?.remove();
  if (!S.stream || !S.stream.isConnected) {
    S.stream = h("div", { class: "msg assistant streaming" }, '<div class="md"></div>');
    S.stream._text = "";
    wrap.append(S.stream);
  }
  S.stream._text += text;
  $(".md", S.stream).innerHTML = md(S.stream._text);
  updateLive();
  scrollDown();
}

function scrollDown(force = false) {
  const m = $("#messages");
  if (force) m.scrollTop = m.scrollHeight;  // abrir o chat: vai direto ao fim
  else if (m.scrollHeight - m.scrollTop - m.clientHeight < 200) m.scrollTo({ top: m.scrollHeight, behavior: "smooth" });
}

/* ------------------------------------------------------------------ aprovações */
function renderApprovals() {
  const box = $("#approvals");
  box.innerHTML = "";
  for (const req of S.pending[S.chatId] || []) box.append(approvalCard(req));
  renderChatHeader();
  updateLive();
  scrollDown(true);
}

function approvalCard(req) {
  const card = h("div", { class: "approval" });
  const respond = async (body) => {
    $$("button", card).forEach((b) => (b.disabled = true));
    try { await api("POST", `/api/chats/${S.chatId}/approvals/${req.id}`, body); }
    catch (e) { toast(e.message); $$("button", card).forEach((b) => (b.disabled = false)); }
  };
  card.append(h("div", { class: "ah" }, `⚠️ ${esc(req.title || "Aprovação necessária")}${req.tool ? ` <span class="badge ${req.provider}">${esc(req.tool)}</span>` : ""}`));

  if (req.kind === "question") {
    const answers = {};
    for (const q of req.questions || []) {
      const qel = h("div", { class: "q" }, `<div class="qh">${esc(q.header ? q.header + " — " : "")}${esc(q.question)}</div>`);
      const opts = h("div", { class: "opts" });
      answers[q.id] = [];
      for (const o of q.options || []) {
        const b = h("button", { type: "button", class: "opt" }, `${esc(o.label)}${o.description ? `<small>${esc(o.description)}</small>` : ""}`);
        b.onclick = () => {
          if (q.multiSelect) {
            b.classList.toggle("on");
            answers[q.id] = $$(".opt.on", opts).map((x) => x.dataset.label);
          } else {
            $$(".opt", opts).forEach((x) => x.classList.remove("on"));
            b.classList.add("on");
            answers[q.id] = [o.label];
          }
        };
        b.dataset.label = o.label;
        opts.append(b);
      }
      qel.append(opts);
      if (q.isOther !== false) {
        const other = h("input", { class: "input", placeholder: "Outra resposta…", style: "margin-top:6px" });
        other.oninput = () => { if (other.value.trim()) { $$(".opt", opts).forEach((x) => x.classList.remove("on")); answers[q.id] = [other.value.trim()]; } };
        qel.append(other);
      }
      card.append(qel);
    }
    const actions = h("div", { class: "actions" });
    actions.append(h("button", { class: "btn primary", onclick: () => {
      const out = {};
      for (const [k, v] of Object.entries(answers)) out[k] = v.length > 1 ? v : (v[0] ?? "");
      respond({ decision: "allow", answers: out });
    } }, "Responder"));
    actions.append(h("button", { class: "btn ghost", onclick: () => respond({ decision: "deny", message: "O usuário preferiu não responder." }) }, "Pular"));
    card.append(actions);
    return card;
  }

  if (req.kind === "plan") {
    card.append(h("div", { class: "md" }, md(req.detail)));
    enhanceCode(card);
    const msg = h("input", { class: "input grow", placeholder: "Ajustes no plano (opcional)…" });
    const actions = h("div", { class: "actions" });
    actions.append(h("button", { class: "btn primary", onclick: () => respond({ decision: "allow" }) }, "Aprovar e executar"));
    actions.append(msg);
    actions.append(h("button", { class: "btn", onclick: () => respond({ decision: "deny", message: msg.value || "Continue planejando." }) }, "Continuar planejando"));
    card.append(actions);
    return card;
  }

  if (req.detail) card.append(h("div", { class: "detail" }, esc(req.detail)));
  if (req.diff) card.append(h("pre", {}, esc(req.diff)));
  else if (req.input != null) card.append(h("pre", {}, esc(formatInput(req.input))));
  const actions = h("div", { class: "actions" });
  actions.append(h("button", { class: "btn primary", onclick: () => respond({ decision: "allow" }) }, "Permitir"));
  if (req.can_always) actions.append(h("button", { class: "btn", onclick: () => respond({ decision: "allow_always" }) }, "Permitir sempre (nesta sessão)"));
  const why = h("input", { class: "input grow", placeholder: "Motivo / o que fazer em vez disso (opcional)" });
  actions.append(why);
  actions.append(h("button", { class: "btn", onclick: () => respond({ decision: "deny", message: why.value || undefined }) }, "Recusar"));
  card.append(actions);
  return card;
}

/* ------------------------------------------------------------------ compositor */
/* ------------------------------------------------------------------ imagens anexadas */
S.anexos = { chat: [], start: [] };
const TIPOS_IMAGEM = /^image\/(png|jpeg|gif|webp)$/;

function lerComoDataURL(file) {
  return new Promise((ok, erro) => {
    const r = new FileReader();
    r.onload = () => ok(r.result);
    r.onerror = () => erro(new Error(`Não consegui ler "${file.name}".`));
    r.readAsDataURL(file);
  });
}
function carregarImagem(src) {
  return new Promise((ok, erro) => {
    const img = new Image();
    img.onload = () => ok(img);
    img.onerror = () => erro(new Error("Imagem inválida."));
    img.src = src;
  });
}
/* Lê a imagem e, se for muito grande, reduz para no máximo 2000 px (a IA não precisa de mais e o envio fica leve). */
async function prepararImagem(file) {
  if (!TIPOS_IMAGEM.test(file.type)) throw new Error(`"${file.name}" não é uma imagem suportada (PNG, JPG, GIF ou WEBP).`);
  if (file.size > 20 * 1024 * 1024) throw new Error(`"${file.name}" é grande demais (máximo 20 MB).`);
  let dataUrl = await lerComoDataURL(file);
  let tipo = file.type;
  if (tipo !== "image/gif") {
    const img = await carregarImagem(dataUrl);
    const escala = Math.min(1, 2000 / Math.max(img.width, img.height));
    if (escala < 1 || file.size > 3.5 * 1024 * 1024) {
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * escala);
      c.height = Math.round(img.height * escala);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      tipo = "image/jpeg";
      dataUrl = c.toDataURL(tipo, 0.88);
    }
  }
  return { name: file.name || "imagem colada", media_type: tipo, data: dataUrl.split(",")[1], preview: dataUrl };
}
async function adicionarAnexos(onde, arquivos) {
  const imagens = [...arquivos].filter((f) => f && f.type && f.type.startsWith("image/"));
  if (!imagens.length) return false;
  for (const f of imagens) {
    if (S.anexos[onde].length >= 10) { toast("No máximo 10 imagens por mensagem."); break; }
    try { S.anexos[onde].push(await prepararImagem(f)); }
    catch (e) { toast(e.message); }
  }
  renderTray(onde);
  return true;
}
function renderTray(onde) {
  const tray = $(onde === "chat" ? "#tray" : "#start-tray");
  const lista = S.anexos[onde];
  tray.classList.toggle("hidden", !lista.length);
  atualizarBotaoEnviar(onde);
  tray.innerHTML = "";
  lista.forEach((a, i) => {
    const item = h("div", { class: "att", title: a.name }, `<img src="${a.preview}" alt="${esc(a.name)}">`);
    item.append(h("button", { type: "button", class: "att-x", title: "Remover", onclick: () => { lista.splice(i, 1); renderTray(onde); } }, "✕"));
    tray.append(item);
  });
}
function anexosParaEnvio(onde) {
  return S.anexos[onde].map(({ name, media_type, data }) => ({ name, media_type, data }));
}
/* Liga colar (Ctrl+V), arrastar e o botão 📎 a uma caixa de mensagem. */
function ligarAnexos(onde, textarea, botao, campoArquivo, areaSoltar) {
  textarea.addEventListener("paste", async (e) => {
    const arquivos = [...(e.clipboardData?.items || [])].filter((i) => i.kind === "file").map((i) => i.getAsFile());
    if (arquivos.some((f) => f && f.type.startsWith("image/"))) {
      e.preventDefault();
      await adicionarAnexos(onde, arquivos);
    }
  });
  botao.onclick = () => campoArquivo.click();
  campoArquivo.onchange = async () => { await adicionarAnexos(onde, campoArquivo.files); campoArquivo.value = ""; };
  areaSoltar.addEventListener("dragover", (e) => {
    if ([...(e.dataTransfer?.items || [])].some((i) => i.kind === "file")) { e.preventDefault(); areaSoltar.classList.add("soltar-aqui"); }
  });
  areaSoltar.addEventListener("dragleave", (e) => { if (!areaSoltar.contains(e.relatedTarget)) areaSoltar.classList.remove("soltar-aqui"); });
  areaSoltar.addEventListener("drop", async (e) => {
    areaSoltar.classList.remove("soltar-aqui");
    if (e.dataTransfer?.files?.length) { e.preventDefault(); await adicionarAnexos(onde, e.dataTransfer.files); textarea.focus(); }
  });
}

/* Botão ↑: "aceso" só com texto ou imagem. Com o chat trabalhando aparece o ■ (parar) e o ↑ só volta se houver algo a enviar. */
function atualizarBotaoEnviar(onde) {
  const input = $(onde === "chat" ? "#input" : "#start-input");
  const btn = $(onde === "chat" ? "#btn-send" : "#start-send");
  const pronto = !!input.value.trim() || S.anexos[onde].length > 0;
  btn.classList.toggle("pronto", pronto);
  if (onde === "chat") btn.classList.toggle("hidden", !!S.chatBusy && !pronto);
}

async function send() {
  const input = $("#input");
  const text = input.value.trim();
  const attachments = anexosParaEnvio("chat");
  if ((!text && !attachments.length) || !S.chatId) return;
  const anexosAntes = S.anexos.chat;
  input.value = "";
  S.anexos.chat = [];
  renderTray("chat");
  autosize();
  hideSlash();
  atualizarBotaoEnviar("chat");
  try { await api("POST", `/api/chats/${S.chatId}/send`, { text, attachments }); }
  catch (e) { toast(e.message); input.value = text; S.anexos.chat = anexosAntes; renderTray("chat"); }
}
function autosize() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 260) + "px";
}
function slashItems() {
  const c = currentChat();
  const v = $("#input").value;
  if (!c || c.provider !== "claude" || !v.startsWith("/") || v.includes(" ") || v.includes("\n")) return [];
  const q = v.slice(1).toLowerCase();
  return (S.meta?.claude.commands || []).filter((cmd) => cmd.name.toLowerCase().includes(q)).slice(0, 40);
}
function renderSlash() {
  const items = slashItems();
  const menu = $("#slash-menu");
  if (!items.length) return hideSlash();
  S.slashIndex = Math.min(S.slashIndex, items.length - 1);
  menu.innerHTML = items.map((c, i) => `<div class="it ${i === S.slashIndex ? "on" : ""}" data-i="${i}"><b>/${esc(c.name)}</b><span>${esc(c.description)}</span></div>`).join("");
  menu.classList.remove("hidden");
  $$(".it", menu).forEach((el) => (el.onmousedown = (e) => { e.preventDefault(); pickSlash(items[+el.dataset.i]); }));
}
function hideSlash() { $("#slash-menu").classList.add("hidden"); S.slashIndex = 0; }
function pickSlash(cmd) { $("#input").value = `/${cmd.name} `; hideSlash(); $("#input").focus(); }

/* ------------------------------------------------------------------ seletor de pasta */
/* Abre a janela "Selecionar pasta" do Windows (o servidor roda nesta máquina) e preenche `input`. */
async function pickFolder(input, title) {
  toast("Abrindo a janela de pastas do Windows… (se não aparecer, veja a barra de tarefas)", 4000);
  try {
    const r = await api("POST", "/api/fs/pick-folder", { initial: input.value.trim(), title });
    if (r.path) { input.value = r.path; input.dispatchEvent(new Event("change")); }
  } catch (e) { toast(e.message); }
}
function folderFieldHtml(id, value, placeholder = "") {
  return `<div class="row gap"><input class="input grow" id="${id}" value="${esc(value || "")}" placeholder="${esc(placeholder)}" spellcheck="false">
    <button type="button" class="btn" data-pick="${id}" title="Escolher pasta no Windows">📁 Escolher</button></div>`;
}
function bindFolderButtons(root, title) {
  $$("[data-pick]", root).forEach((b) => (b.onclick = () => pickFolder($(`#${b.dataset.pick}`), title)));
}

/* ------------------------------------------------------------------ início de conversa */
/* Tela usada ao clicar em um agente ou em "+ Novo chat": mostra as configurações da conversa.
   O chat só é criado quando a primeira mensagem é enviada. */
S.start = null;  // { agentId, provider }

function openStart(agentId = null, provider = null) {
  const agent = S.agents.find((a) => a.id === agentId);
  if (agentId && !agent) { toast("Agente não encontrado."); location.hash = "#/"; return; }
  S.chatId = null;
  S.start = { agentId: agent?.id || null, provider: agent?.provider || provider || localStorageGet("lastProvider") || "claude" };
  show("start");
  renderStart();
  $("#start-input").focus();
}

/* Mensagem que o agente mostra ao ser selecionado (configurável no agente; há um texto padrão). */
function agentGreeting(agent) {
  if (agent.welcome && agent.welcome.trim()) return agent.welcome.trim();
  return `Olá! Eu sou o **${agent.name}**.${agent.description ? ` ${agent.description}` : ""}\n\nComo posso ajudar?`;
}

function renderStart() {
  const { agentId, provider } = S.start;
  const agent = S.agents.find((a) => a.id === agentId);
  const badge = $("#start-badge");
  badge.textContent = provider;
  badge.className = `badge ${provider}${agent ? "" : " hidden"}`;
  $("#start-title").textContent = agent ? agent.name : "";
  $("#start-edit-agent").classList.toggle("hidden", !agent);
  $("#view-start").classList.toggle("hero", !agent);  // chat livre: saudação e caixa de mensagem no centro
  const cwd = agent?.cwd || localStorageGet("lastCwd") || S.meta.home;
  const body = $("#start-body");
  const opts = $("#start-opts");
  if (agent) {
    // Agente: a tela é só a mensagem de boas-vindas. Modelo, permissões e pasta vêm do agente
    // e só mudam nas configurações dele (botão "Editar agente").
    body.innerHTML = `<div class="msg-wrap">
      <div class="msg assistant greeting"><div class="who">🤖 ${esc(agent.name)}</div><div class="md">${md(agentGreeting(agent))}</div></div>
    </div>`;
    enhanceCode(body);
    opts.innerHTML = "";
  } else {
    body.innerHTML = `<div class="hero-box"><span class="logo hero-logo">A</span><h1 class="hero-title">${saudacao()}</h1></div>`;
    opts.innerHTML = `
      <div class="opts-row">
        <div class="seg seg-sm" id="start-provider">
          <button type="button" data-p="claude" class="${provider === "claude" ? "on" : ""}">Claude</button>
          <button type="button" data-p="codex" class="${provider === "codex" ? "on" : ""}">Codex</button>
        </div>
        <details class="opts"><summary>Modelo, permissões e pasta</summary>
          ${provider === "codex" && !S.meta.codex.available ? `<div class="err-box">codex.exe não encontrado. Instale a extensão do Codex no VS Code ou defina CODEX_PATH.</div>` : ""}
          <div class="grid2">${providerFieldsHtml("ds", provider, {})}</div>
          <label>Pasta de trabalho ${folderFieldHtml("ds-cwd", cwd)}</label>
          <div class="hint">A pasta não pode ser trocada depois da primeira mensagem. Dica: para instruções e configurações salvas, escolha um agente na barra lateral.</div>
        </details>
      </div>`;
    bindFolderButtons(opts, "Pasta de trabalho desta conversa");
    $$("select", opts).forEach((s) => s.addEventListener("change", atualizarInfoStart));
  }
  $$("#start-provider button").forEach((b) => (b.onclick = () => {
    S.start.provider = b.dataset.p;
    localStorageSet("lastProvider", b.dataset.p);
    renderStart();
  }));
  atualizarInfoStart();
  atualizarBotaoEnviar("start");
  renderSideAgents();
}

function saudacao() {
  const h = new Date().getHours();
  return h < 5 ? "Boa noite" : h < 12 ? "Bom dia" : h < 18 ? "Boa tarde" : "Boa noite";
}

/* Etiqueta do modelo dentro da caixa de mensagem da tela de início. */
function atualizarInfoStart() {
  if (!S.start) return;
  const agent = S.agents.find((a) => a.id === S.start.agentId);
  const provider = S.start.provider;
  const valor = agent ? (agent.model ?? "") : ($("#ds-model")?.value ?? "");
  const nome = modelOptions(provider).find((m) => String(m.value) === String(valor))?.label || valor || "padrão";
  $("#start-info").textContent = `${provider === "claude" ? "Claude" : "Codex"} · ${nome}`;
}

async function startSend() {
  const input = $("#start-input");
  const text = input.value.trim();
  const attachments = anexosParaEnvio("start");
  if ((!text && !attachments.length) || !S.start) return;
  const { agentId, provider } = S.start;
  const agent = S.agents.find((a) => a.id === agentId);
  const btn = $("#start-send");
  btn.disabled = true;
  try {
    // Com agente, tudo vem dele (o servidor completa); sem agente, vale o que foi escolhido na tela.
    const body = agent
      ? { agent_id: agent.id, cwd: agent.cwd || localStorageGet("lastCwd") || S.meta.home, greeting: agentGreeting(agent) }
      : { provider, cwd: $("#ds-cwd").value.trim(), ...readProviderFields("ds", provider) };
    const chat = await api("POST", "/api/chats", body);
    if (!agent) localStorageSet("lastCwd", body.cwd);
    upsertChat(chat);
    await api("POST", `/api/chats/${chat.id}/send`, { text, attachments });
    input.value = "";
    S.anexos.start = [];
    renderTray("start");
    S.start = null;
    location.hash = `#/chat/${chat.id}`;
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; }
}

/* ------------------------------------------------------------------ agentes na barra lateral */
S.agentsCollapsed = true;  // a lista sempre começa recolhida ao abrir a página

function renderSideAgents() {
  const collapsed = S.agentsCollapsed;
  $("#agents-toggle").classList.toggle("collapsed", collapsed);
  $("#agents-count").textContent = S.agents.length ? `(${S.agents.length})` : "";
  const list = $("#side-agent-list");
  list.classList.toggle("hidden", collapsed);
  list.innerHTML = "";
  if (!S.agents.length) {
    list.append(h("a", { class: "side-agent muted", href: "#/agents/new" }, "+ Criar o primeiro agente"));
    return;
  }
  for (const a of S.agents) {
    const active = S.view === "start" && S.start?.agentId === a.id;
    list.append(h("a", { class: `side-agent ${active ? "active" : ""}`, href: `#/novo/${a.id}`, title: a.description || a.name },
      `<span class="dot ${a.provider}"></span><span class="t">${esc(a.name)}</span><span class="st ${agentStatus(a.id)}" title="${agentStatusTitle(a.id)}"></span>`));
  }
}

/* Estado de um agente na barra lateral: "waiting" se algum chat dele aguarda aprovação, "running" se algum trabalha. */
function agentStatus(agentId) {
  const chats = S.chats.filter((c) => c.agent_id === agentId);
  if (chats.some((c) => (S.pending[c.id] || []).length)) return "waiting";
  if (chats.some((c) => c.status === "running" || c.status === "waiting")) return "running";
  return "";
}
function agentStatusTitle(agentId) {
  return { waiting: "Um chat deste agente aguarda sua aprovação", running: "Análise em andamento" }[agentStatus(agentId)] || "";
}
function localStorageGet(k) { try { return localStorage.getItem("hub." + k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem("hub." + k, v); } catch { /* sem storage */ } }

/* ------------------------------------------------------------------ agentes */
function renderAgentList() {
  const list = $("#agent-list");
  list.innerHTML = "";
  if (!S.agents.length) list.innerHTML = '<div class="muted small">Nenhum agente ainda. Crie um para salvar instruções, modelo e permissões que você usa sempre.</div>';
  for (const a of S.agents) {
    list.append(h("div", { class: `agent-card ${a.id === S.editingAgentId ? "active" : ""}`, onclick: () => editAgent(a.id) },
      `<div class="an"><span class="badge ${a.provider}">${esc(a.provider)}</span>${esc(a.name)}</div><div class="ad">${esc(a.description || "")}</div>`));
  }
  const fileAgents = S.meta?.claude.agents || [];
  if (fileAgents.length) {
    list.append(h("div", { class: "group-label", style: "padding-left:0" }, "Subagentes do Claude Code"));
    for (const a of fileAgents) {
      const title = a.label ? `${esc(a.label)} <code class="small muted">${esc(a.name)}</code>` : esc(a.name);
      list.append(h("div", { class: "agent-card", title: a.description }, `<div class="an">${title}</div><div class="ad">${esc(a.description)}</div>`));
    }
    list.append(h("div", { class: "hint" }, "Estes vêm do Claude Code (internos e os da pasta ~/.claude/agents) e já ficam disponíveis em qualquer chat Claude. O nome em código é o que o Claude usa para chamar o subagente."));
  }
}

function showAgentPlaceholder() {
  const form = $("#agent-form");
  form.classList.remove("hidden");
  form.onsubmit = (e) => e.preventDefault();
  form.innerHTML = `<div class="empty" style="text-align:left;margin:0">
    <h2>Agentes</h2>
    <p>Um agente guarda <b>instruções, IA, modelo, permissões e pasta</b> que você usa sempre. Abra um chat a partir dele e tudo já vem configurado.</p>
    <p>Agentes Claude podem chamar outros agentes Claude como <b>subagentes</b> e podem ser exportados para <code>~/.claude/agents</code>, ficando disponíveis também no Claude Code do VS Code.</p>
    <button type="button" class="btn primary" id="ag-first">+ Criar agente</button></div>`;
  $("#ag-first").onclick = () => editAgent(null);
}

function editAgent(id, draft = null) {
  S.editingAgentId = id;
  const a = draft || (id ? S.agents.find((x) => x.id === id) : { provider: "claude", name: "", prompt: "" });
  renderAgentList();
  const form = $("#agent-form");
  form.classList.remove("hidden");
  const claudeAgents = S.agents.filter((x) => x.provider === "claude" && x.id !== id);
  const p = a.provider;
  form.innerHTML = `
    <h2>${id ? "Editar agente" : "Novo agente"}</h2>
    <div class="grid2">
      <label>Nome <input class="input" id="ag-name" value="${esc(a.name)}" required></label>
      <label>IA
        <div class="seg" id="ag-provider" style="display:flex">
          <button type="button" data-p="claude" class="${p === "claude" ? "on" : ""}">Claude</button>
          <button type="button" data-p="codex" class="${p === "codex" ? "on" : ""}">Codex</button>
        </div>
      </label>
    </div>
    <label>Descrição <input class="input" id="ag-description" value="${esc(a.description || "")}" placeholder="Para que serve (também usada quando ele vira subagente)"></label>
    <div class="grid2" id="ag-provider-fields">${providerFieldsHtml("ag", p, a)}</div>
    <label>Pasta padrão ${folderFieldHtml("ag-cwd", a.cwd, "vazio = última pasta usada")}</label>
    <label>Instruções do agente
      <textarea id="ag-prompt" placeholder="Ex.: Você é um analista de BI da Herval. Sempre consulte o Superset antes de responder…">${esc(a.prompt || "")}</textarea>
    </label>
    <div class="hint">${p === "claude" ? "Somadas ao prompt padrão do Claude Code (ele continua com ferramentas, skills, MCP e CLAUDE.md)." : "Enviadas como developer instructions do Codex (somam-se ao AGENTS.md e ao config.toml)."}</div>
    <label>Mensagem de boas-vindas
      <textarea id="ag-welcome" class="welcome" placeholder="${esc(agentGreeting({ name: a.name || "nome do agente", description: a.description }))}">${esc(a.welcome || "")}</textarea>
    </label>
    <div class="hint">Aparece quando você seleciona o agente na barra lateral, antes da primeira mensagem, e fica no início do chat. Aceita Markdown (**negrito**, listas…). Vazio = usa o texto de exemplo acima.</div>
    ${p === "claude" ? `
    <div class="grid2">
      <label>Ferramentas disponíveis <input class="input" id="ag-tools" value="${esc((a.tools || []).join(", "))}" placeholder="vazio = todas · ex.: Read, Grep, mcp__superset-herval__*"></label>
      <label>Ferramentas bloqueadas <input class="input" id="ag-disallowed_tools" value="${esc((a.disallowed_tools || []).join(", "))}" placeholder="ex.: Bash, Write"></label>
    </div>
    <label>Subagentes que ele pode chamar</label>
    <div class="checks">${claudeAgents.length ? claudeAgents.map((s) => `<label><input type="checkbox" class="ag-sub" value="${s.id}" ${(a.subagents || []).includes(s.id) ? "checked" : ""}> ${esc(s.name)}</label>`).join("") : '<span class="muted small">Crie outros agentes Claude para usá-los como subagentes.</span>'}</div>
    ` : ""}
    <div class="actions">
      <button type="submit" class="btn primary">Salvar</button>
      ${id ? `<button type="button" class="btn" id="ag-chat">💬 Novo chat com este agente</button>` : ""}
      ${id && p === "claude" ? `<button type="button" class="btn" id="ag-export" title="Grava em ~/.claude/agents para usar também no Claude Code / VS Code">⤓ Exportar para ~/.claude/agents</button>` : ""}
      <div class="grow"></div>
      ${id ? `<button type="button" class="btn ghost" id="ag-delete">🗑 Excluir</button>` : ""}
    </div>`;
  bindFolderButtons(form, "Pasta padrão do agente");
  $$("#ag-provider button").forEach((b) => (b.onclick = () => {
    if (b.dataset.p === p) return;
    // Mantém o que já foi digitado; modelo e esforço não valem entre provedores.
    editAgent(id, { ...a, ...readAgentForm(p), provider: b.dataset.p, model: undefined, effort: undefined });
  }));
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = readAgentForm(p);
    try {
      const saved = id ? await api("PUT", `/api/agents/${id}`, body) : await api("POST", "/api/agents", body);
      await loadAgents();
      editAgent(saved.id);
      toast("Agente salvo.");
    } catch (err) { toast(err.message); }
  };
  if (id) {
    $("#ag-chat").onclick = () => (location.hash = `#/novo/${id}`);
    $("#ag-delete").onclick = async () => {
      if (!confirm(`Excluir o agente "${a.name}"?`)) return;
      await api("DELETE", `/api/agents/${id}`);
      S.editingAgentId = null;
      await loadAgents();
      renderAgentList();
      showAgentPlaceholder();
    };
    const exp = $("#ag-export");
    if (exp) exp.onclick = async () => { try { const r = await api("POST", `/api/agents/${id}/export-claude`); toast(`Gravado em ${r.path}`, 6000); } catch (err) { toast(err.message); } };
  }
}
function readAgentForm(provider) {
  const v = (id) => $(`#${id}`)?.value ?? "";
  const body = {
    name: v("ag-name").trim(), description: v("ag-description").trim(), provider, cwd: v("ag-cwd").trim(),
    prompt: v("ag-prompt"), welcome: v("ag-welcome"), ...readProviderFields("ag", provider),
  };
  if (provider === "claude") {
    body.tools = v("ag-tools");
    body.disallowed_tools = v("ag-disallowed_tools");
    body.subagents = $$(".ag-sub:checked").map((x) => x.value);
  }
  return body;
}

/* ------------------------------------------------------------------ importar conversas */
S.importProvider = "claude";
S.importItems = { claude: null, codex: null };

async function renderImport(force = false) {
  const p = S.importProvider;
  const list = $("#import-list");
  if (!S.importItems[p] || force) {
    list.innerHTML = '<div class="muted small">Carregando conversas…</div>';
    try { S.importItems[p] = await api("GET", `/api/import/${p}`); }
    catch (e) { list.innerHTML = `<div class="err-box">${esc(e.message)}</div>`; return; }
  }
  if (S.importProvider === p) renderImportList();
}

function renderImportList() {
  const p = S.importProvider;
  const q = $("#import-search").value.trim().toLowerCase();
  const items = (S.importItems[p] || []).filter((it) => !q || it.title.toLowerCase().includes(q) || (it.cwd || "").toLowerCase().includes(q));
  const list = $("#import-list");
  list.innerHTML = "";
  if (!items.length) { list.innerHTML = `<div class="muted small">${q ? "Nada encontrado." : "Nenhuma conversa encontrada."}</div>`; return; }
  for (const it of items) {
    const when = it.updated ? new Date(it.updated * 1000).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "";
    const row = h("div", { class: "import-item" },
      `<span class="badge ${p}">${p}</span>
       <div class="info"><div class="tt" title="${esc(it.title)}">${esc(it.title)}</div>
       <div class="mt">${esc(when)}${it.cwd ? ` · <code title="${esc(it.cwd)}">${esc(it.cwd)}</code>` : ""}</div></div>`);
    const btn = it.imported_chat_id
      ? h("button", { class: "btn", onclick: () => (location.hash = `#/chat/${it.imported_chat_id}`) }, "Abrir")
      : h("button", { class: "btn primary" }, "Importar");
    if (!it.imported_chat_id) {
      btn.onclick = async () => {
        btn.disabled = true;
        btn.textContent = "Importando…";
        try {
          const chat = await api("POST", `/api/import/${p}/${encodeURIComponent(it.id)}`);
          it.imported_chat_id = chat.id;
          upsertChat(chat);
          location.hash = `#/chat/${chat.id}`;
        } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Importar"; }
      };
    }
    row.append(btn);
    list.append(row);
  }
}

/* ------------------------------------------------------------------ ambiente */
function updateMcpAlert() {
  const n = S.meta?.mcpIssues || 0;
  const badge = $("#mcp-alert");
  badge.textContent = n ? `⚠ ${n}` : "";
  badge.classList.toggle("hidden", !n);
  badge.title = n ? `${n} servidor(es) MCP com problema` : "";
}

async function renderMcpCard() {
  const box = $("#mcp-card-body");
  if (!box) return;
  let data;
  try { data = await api("GET", "/api/mcp"); }
  catch (e) { box.innerHTML = `<div class="err-box">${esc(e.message)}</div>`; return; }
  const label = { ok: "✅ ok", missing: "❌ quebrado", outdated: "⚠️ desatualizado", remote: "🌐 remoto" };
  const scopeLabel = { user: "global", local: "local", project: "projeto", global: "global" };
  box.innerHTML = `<table class="mcp-table"><thead><tr><th>IA</th><th>Servidor</th><th>Status</th><th>Executável</th><th></th></tr></thead><tbody></tbody></table>`;
  const tbody = $("tbody", box);
  for (const s of data.servers) {
    const tr = h("tr", {}, `<td><span class="badge ${s.ia}">${s.ia}</span></td>
      <td><b>${esc(s.name)}</b><div class="small muted">${esc(scopeLabel[s.scope] || s.scope)}${s.project ? " · " + esc(s.project) : ""}</div></td>
      <td class="st-${s.status}">${label[s.status] || esc(s.status)}${s.detail ? `<div class="small muted">${esc(s.detail)}</div>` : ""}</td>
      <td class="path">${esc(s.command || "")}</td><td></td>`);
    if (s.fix) {
      const b = h("button", { class: "btn primary", title: Object.entries(s.fix).map(([a, b]) => `${a}\n→ ${b}`).join("\n\n") }, "Corrigir");
      b.onclick = async () => {
        b.disabled = true;
        try {
          const r = await api("POST", "/api/mcp/fix", { ia: s.ia, scope: s.scope, name: s.name, project: s.project });
          toast(`Corrigido. Backup em ${r.backup}`, 7000);
          await loadMeta(); updateMcpAlert(); renderMcpCard();
        } catch (e) { toast(e.message, 7000); b.disabled = false; }
      };
      tr.lastElementChild.append(b);
    }
    tbody.append(tr);
  }
  if (!data.servers.length) box.innerHTML = '<div class="muted small">Nenhum servidor MCP configurado.</div>';
}

async function renderSettings() {
  const m = S.meta;
  const body = $("#settings-body");
  body.innerHTML = `
    <div class="card"><h3><span class="badge claude">Claude</span> Claude Agent SDK</h3>
      <div>Versão do Claude Code: <b>${esc(m.claude.version || "consultando…")}</b></div>
      ${m.claude.error ? `<div class="err-box" style="margin-top:8px">${esc(m.claude.error)}</div>` : ""}
      <div class="hint">Usa o login que você já tem no Claude Code (~/.claude). Carrega CLAUDE.md, skills, subagentes, hooks e servidores MCP das suas configurações.</div>
      <div class="hint">${m.claude.models.length} modelos · ${m.claude.commands.length} comandos/skills · ${m.claude.agents.length} subagentes</div>
      <div class="row gap" style="margin-top:10px"><button class="btn" id="st-refresh">↻ Recarregar modelos, comandos e subagentes</button></div>
    </div>
    <div class="card"><h3><span class="badge codex">Codex</span> Codex app-server</h3>
      ${m.codex.available ? `<div class="kv">${esc(m.codex.path)}</div>` : `<div class="err-box">codex.exe não encontrado.</div>`}
      <div class="hint">Usa o login do Codex (~/.codex/auth.json), o config.toml (modelo padrão, MCP) e o AGENTS.md.</div>
      <div style="margin-top:10px">Sandbox do Windows: <b id="st-sbx">verificando…</b></div>
      <div class="hint">Sem o sandbox configurado, o Codex não consegue rodar comandos nos modos "Somente leitura" e "Pode editar a pasta do chat". A configuração é feita pelo próprio Codex. O modo "elevado" pede permissão de administrador (UAC).</div>
      <div class="row gap" style="margin-top:8px">
        <button class="btn" id="st-sbx-unelev">Configurar sandbox (sem admin)</button>
        <button class="btn" id="st-sbx-elev">Configurar sandbox (elevado)</button>
      </div>
    </div>
    <div class="card"><h3>Servidores MCP</h3>
      <div class="hint">Os mesmos que o Claude Code e o Codex usam no VS Code. MCPs que rodam de dentro de uma extensão do VS Code (como o Power BI) têm a versão da extensão no caminho: quando ela atualiza, aparece aqui como desatualizado ou quebrado, com o botão <b>Corrigir</b>. A correção faz backup do arquivo antes.</div>
      <div id="mcp-card-body"><div class="muted small" style="margin-top:8px">Verificando…</div></div>
      <div class="row gap" style="margin-top:10px"><button class="btn" id="st-mcp-recheck">↻ Verificar novamente</button></div>
    </div>
    <div class="card"><h3>Aparência</h3>
      <label>Tema</label>
      <div class="seg" id="st-theme"><button data-t="">Sistema</button><button data-t="light">Claro</button><button data-t="dark">Escuro</button></div>
      <label>Fonte das respostas</label>
      <div class="seg" id="st-fonte"><button data-f="">Estilo Claude (serifada)</button><button data-f="sans">Sem serifa</button></div>
    </div>`;
  $("#st-refresh").onclick = async () => { await api("POST", "/api/meta/refresh"); toast("Consultando o Claude… (alguns segundos)"); setTimeout(async () => { await loadMeta(); renderSettings(); }, 8000); };
  const theme = localStorageGet("theme") || "";
  $$("#st-theme button").forEach((b) => { b.classList.toggle("on", b.dataset.t === theme); b.onclick = () => { setTheme(b.dataset.t); renderSettings(); }; });
  const fonte = localStorageGet("fonte") || "";
  $$("#st-fonte button").forEach((b) => { b.classList.toggle("on", b.dataset.f === fonte); b.onclick = () => { setFonte(b.dataset.f); renderSettings(); }; });
  const setup = async (mode) => {
    try { await api("POST", "/api/codex/sandbox/setup", { mode }); toast("Configuração do sandbox iniciada…"); }
    catch (e) { toast(e.message); }
  };
  $("#st-sbx-unelev").onclick = () => setup("unelevated");
  $("#st-sbx-elev").onclick = () => setup("elevated");
  $("#st-mcp-recheck").onclick = async () => { await loadMeta(); updateMcpAlert(); renderMcpCard(); };
  renderMcpCard();
  if (m.codex.available) {
    const r = await api("GET", "/api/codex/sandbox").catch((e) => ({ status: "error", error: e.message }));
    const label = { ready: "✅ pronto", notConfigured: "⚠️ não configurado", error: "erro: " + (r.error || "") }[r.status] || r.status;
    const el = $("#st-sbx");
    if (el) el.textContent = label;
  }
}
function setFonte(f) {
  localStorageSet("fonte", f);
  if (f) document.documentElement.dataset.fonte = f;
  else delete document.documentElement.dataset.fonte;
}
function setTheme(t) {
  localStorageSet("theme", t);
  if (t) document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

/* ------------------------------------------------------------------ tempo real */
function connectWs() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    switch (m.type) {
      case "hello":
        S.pending = {};
        for (const p of m.pending) (S.pending[p.chat_id] ||= []).push(p.request);
        for (const c of S.chats) c.status = m.statuses[c.id] || "idle";
        renderChatList();
        if (S.view === "chat") renderApprovals();
        break;
      case "event":
        if (m.chat_id === S.chatId && S.view === "chat") {
          safeRender(m.event);
          updateLive();
          scrollDown(m.event.kind === "user");
        }
        break;
      case "delta":
        if (m.chat_id === S.chatId && S.view === "chat") onDelta(m.text);
        break;
      case "status": {
        const c = S.chats.find((x) => x.id === m.chat_id);
        if (c) { c.status = m.status; renderChatList(); if (c.id === S.chatId) renderChatHeader(); }
        if (m.status === "idle" && m.chat_id === S.chatId) finishActivity();
        if (m.chat_id === S.chatId) updateLive();
        break;
      }
      case "tasks": {
        const c = S.chats.find((x) => x.id === m.chat_id);
        if (c) c.tasks = m.tasks;
        if (m.chat_id === S.chatId) updateLive();
        break;
      }
      case "approval":
        (S.pending[m.chat_id] ||= []).push(m.request);
        renderChatList();
        if (m.chat_id === S.chatId) renderApprovals();
        else notify(`Um chat está aguardando sua aprovação`);
        break;
      case "approval_done":
        S.pending[m.chat_id] = (S.pending[m.chat_id] || []).filter((r) => r.id !== m.id);
        renderChatList();
        if (m.chat_id === S.chatId) renderApprovals();
        break;
      case "chat_updated":
        upsertChat(m.chat);
        break;
      case "chat_deleted":
        S.chats = S.chats.filter((c) => c.id !== m.chat_id);
        if (S.chatId === m.chat_id) { S.chatId = null; location.hash = "#/"; }
        renderChatList();
        break;
      case "codex_sandbox":
        toast(m.result.success ? `Sandbox do Codex configurado (${m.result.mode}).` : `Falha ao configurar sandbox: ${m.result.error || ""}`, 7000);
        if (S.view === "settings") renderSettings();
        break;
    }
  };
  ws.onclose = () => setTimeout(connectWs, 1500);
}
function notify(text) {
  if (document.hasFocus()) { toast(text); return; }
  try { if (Notification.permission === "granted") new Notification("Agent Hub", { body: text }); } catch { /* sem notificação */ }
}

/* ------------------------------------------------------------------ carga e rotas */
async function loadMeta() { S.meta = await api("GET", "/api/meta"); }
async function loadChats() { S.chats = await api("GET", "/api/chats"); renderChatList(); }
async function loadAgents() { S.agents = await api("GET", "/api/agents"); if (S.view === "agents") renderAgentList(); renderChatList(); renderSideAgents(); }

function route() {
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/chat\/(\w+)/);
  if (m) return openChat(m[1]);
  const ag = hash.match(/^#\/agents(?:\/(\w+))?/);
  if (ag) {
    show("agents");
    if (ag[1] === "new") editAgent(null);
    else if (ag[1]) editAgent(ag[1]);
    else if (S.editingAgentId) editAgent(S.editingAgentId);
    else { renderAgentList(); showAgentPlaceholder(); }
    return;
  }
  if (hash === "#/settings") { show("settings"); renderSettings(); return; }
  if (hash === "#/import") { show("import"); renderImport(); return; }
  const nv = hash.match(/^#\/novo(?:\/(\w+))?(?:\?p=(claude|codex))?/);
  if (nv) return openStart(nv[1] || null, nv[2] || null);
  openStart(null, null);  // tela inicial = novo chat (como no Claude Desktop)
}

function bind() {
  $("#btn-new-chat").onclick = () => { if (location.hash === "#/novo") openStart(null, null); else location.hash = "#/novo"; };
  $("#agents-toggle").onclick = () => { S.agentsCollapsed = !S.agentsCollapsed; renderSideAgents(); };
  $("#agents-add").onclick = (e) => { e.stopPropagation(); location.hash = "#/agents/new"; };
  $("#start-send").onclick = startSend;
  ligarAnexos("chat", $("#input"), $("#btn-attach"), $("#file-input"), $("#view-chat"));
  ligarAnexos("start", $("#start-input"), $("#btn-start-attach"), $("#start-file-input"), $("#view-start"));
  $("#start-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); startSend(); }
  });
  $("#start-input").addEventListener("input", (e) => {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 260) + "px";
    atualizarBotaoEnviar("start");
  });
  $("#start-edit-agent").onclick = () => S.start?.agentId && (location.hash = `#/agents/${S.start.agentId}`);
  $("#btn-agents").onclick = () => (location.hash = "#/agents");
  $("#btn-import").onclick = () => (location.hash = "#/import");
  $$("#import-provider button").forEach((b) => (b.onclick = () => {
    S.importProvider = b.dataset.p;
    $$("#import-provider button").forEach((x) => x.classList.toggle("on", x === b));
    renderImport();
  }));
  $("#import-search").oninput = () => renderImportList();
  $("#btn-settings").onclick = () => (location.hash = "#/settings");
  $("#btn-new-agent").onclick = () => editAgent(null);
  $("#chat-search").oninput = renderChatList;
  $("#btn-menu").onclick = () => $("#sidebar").classList.toggle("open");
  $$("[data-action=menu]").forEach((b) => (b.onclick = () => $("#sidebar").classList.toggle("open")));

  const input = $("#input");
  input.addEventListener("input", () => { autosize(); renderSlash(); atualizarBotaoEnviar("chat"); });
  input.addEventListener("keydown", (e) => {
    const menuOpen = !$("#slash-menu").classList.contains("hidden");
    if (menuOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      const n = slashItems().length;
      S.slashIndex = (S.slashIndex + (e.key === "ArrowDown" ? 1 : n - 1)) % n;
      renderSlash();
      return;
    }
    if (menuOpen && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey))) {
      const items = slashItems();
      if (items.length && input.value.trim() !== `/${items[S.slashIndex].name}`) { e.preventDefault(); pickSlash(items[S.slashIndex]); return; }
    }
    if (e.key === "Escape") hideSlash();
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  input.addEventListener("blur", () => setTimeout(hideSlash, 150));
  $("#btn-send").onclick = send;
  $("#btn-stop").onclick = () => api("POST", `/api/chats/${S.chatId}/interrupt`).catch((e) => toast(e.message));
  $("#btn-chat-settings").onclick = () => {
    const bar = $("#chat-settings");
    bar.classList.toggle("hidden");
    if (!bar.classList.contains("hidden")) renderChatSettings();
  };
  const title = $("#chat-title");
  const saveTitle = async () => {
    const c = currentChat();
    if (c && title.value.trim() && title.value.trim() !== c.title) upsertChat(await api("PATCH", `/api/chats/${c.id}`, { title: title.value.trim() }));
  };
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } });
  title.addEventListener("blur", saveTitle);
  window.addEventListener("hashchange", route);
  document.addEventListener("click", (e) => { if (!e.target.closest("#ctx-menu")) closeChatMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeChatMenu(); });
  window.addEventListener("resize", closeChatMenu);
  $("#chat-list").addEventListener("scroll", closeChatMenu);
}

(async function init() {
  setTheme(localStorageGet("theme") || "");
  setFonte(localStorageGet("fonte") || "");
  bind();
  try { await loadMeta(); } catch (e) { toast("Falha ao carregar: " + e.message); return; }
  await Promise.all([loadChats(), loadAgents()]);
  connectWs();
  route();
  updateMcpAlert();
  if (S.meta.mcpIssues) toast(`⚠ ${S.meta.mcpIssues} servidor(es) MCP com problema. Veja em Ambiente.`, 8000);
  try { if (Notification.permission === "default") Notification.requestPermission(); } catch { /* ok */ }
  // o Claude pode demorar alguns segundos para informar modelos/comandos na primeira vez
  if (!S.meta.claude.version) setTimeout(loadMeta, 10000);
})();
