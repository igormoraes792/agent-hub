"use strict";
/* Agent Hub — interface. Sem framework: estado em `S`, renderização direta no DOM. */

const S = {
  meta: null,
  chats: [],
  agents: [],
  chatId: null,
  view: "start",
  settingsSection: "geral",
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
/* Links nas respostas abrem em nova aba (nunca substituem o Agent Hub); links internos (#/…) continuam na mesma aba. */
try {
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName !== "A") return;
    const href = node.getAttribute("href") || "";
    if (!href || href.startsWith("#")) return;
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  });
} catch { /* sem DOMPurify: o md() já cai no texto puro */ }
/* Rede de segurança: qualquer link de conteúdo (respostas, aprovações, saudação) que não seja interno abre em nova aba. */
document.addEventListener("click", (e) => {
  const a = e.target.closest?.("a[href]");
  if (!a || !a.closest(".md, .bubble, .approval")) return;
  const href = a.getAttribute("href") || "";
  if (href.startsWith("#") || a.target === "_blank") return;
  e.preventDefault();
  window.open(a.href, "_blank", "noopener");
});
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
const MSG_OFFLINE = "O servidor do Agent Hub está desligado. Abra pelo atalho “Agent Hub” (ou Iniciar.bat); esta tela reconecta sozinha.";
async function api(method, url, body) {
  let res;
  try {
    res = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    mostrarOffline(true);
    throw new Error(MSG_OFFLINE);
  }
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
  if (provider === "claude") return (S.meta?.claude?.models || []).map((m) => ({ value: m.value, label: m.displayName || m.value, title: m.description }));
  if (provider === "antigravity") return (S.meta?.antigravity?.models || []).map((m) => ({ value: m.value, label: m.displayName || m.value, title: m.description }));
  const def = S.meta?.codex?.defaultModel;
  return [{ value: "", label: `Padrão do Codex${def ? ` (${def})` : ""}` },
    ...(S.meta?.codex?.models || []).map((m) => ({ value: m.value, label: m.displayName || m.value }))];
}
function effortOptions(provider) {
  const levels = provider === "claude" ? S.meta?.claude?.effortLevels : (provider === "antigravity" ? S.meta?.antigravity?.effortLevels : S.meta?.codex?.effortLevels);
  return [{ value: "", label: "Padrão" }, ...(levels || []).map((l) => ({ value: l, label: EFFORT_LABEL[l] || l }))];
}
function selectHtml(id, options, value, attrs = "") {
  return `<select id="${id}" ${attrs}>${options.map((o) =>
    `<option value="${esc(o.value)}" ${o.title ? `title="${esc(o.title)}"` : ""} ${String(o.value) === String(value ?? "") ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select>`;
}
/* Campos de modelo/esforço/permissões. `prefix` diferencia os formulários que usam o mesmo bloco. */
function providerFieldsHtml(prefix, provider, v = {}) {
  const defModel = provider === "claude" ? "default" : (provider === "antigravity" ? "gemini-3.8-flash-high" : "");
  // No Antigravity o nível de raciocínio faz parte do modelo, ex.: "Gemini 4 Neon (XHigh)"; não há campo separado
  let html = `<label>Modelo ${selectHtml(`${prefix}-model`, modelOptions(provider), v.model ?? defModel)}</label>` +
    (provider === "antigravity"
      ? `<div class="hint" style="align-self:end">O nível de raciocínio vem no próprio modelo: Low, Medium, High ou XHigh.</div>`
      : `<label>Esforço de raciocínio ${selectHtml(`${prefix}-effort`, effortOptions(provider), v.effort)}</label>`);
  if (provider === "claude") {
    html += `<label>Permissões ${selectHtml(`${prefix}-permission_mode`, S.meta?.claude?.permissionModes || [], v.permission_mode || "default")}</label>`;
  } else if (provider === "antigravity") {
    html += `<label>Modo de execução ${selectHtml(`${prefix}-permission_mode`, S.meta?.antigravity?.permissionModes || [], v.permission_mode || "auto")}</label>`;
  } else {
    html += `<label>Sandbox ${selectHtml(`${prefix}-sandbox`, S.meta?.codex?.sandboxModes || [], v.sandbox || "workspace-write")}</label>
      <label>Aprovação ${selectHtml(`${prefix}-approval_policy`, CODEX_APPROVAL, v.approval_policy || "on-request")}</label>`;
  }
  return html;
}
function readProviderFields(prefix, provider) {
  const val = (f) => $(`#${prefix}-${f}`)?.value;
  const out = { model: val("model"), effort: val("effort") };
  if (provider === "claude" || provider === "antigravity") out.permission_mode = val("permission_mode");
  else { out.sandbox = val("sandbox"); out.approval_policy = val("approval_policy"); }
  return out;
}

/* ------------------------------------------------------------------ navegação */
function show(view) {
  S.view = view;
  for (const v of ["chat", "settings", "import", "start", "consumo"]) {
    const el = $(`#view-${v}`);
    if (el) el.classList.toggle("hidden", v !== view);
  }
  $("#sidebar").classList.remove("open");
  renderChatList();
  renderSideAgents();
}

/* ------------------------------------------------------------------ lista de chats */
/* Sem diferenciar maiúsculas nem acentos ("apresentacao" acha "Apresentação"). */
function norm(s) { return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }

function renderChatList() {
  const q = $("#chat-search").value.trim();
  const termos = norm(q).split(/\s+/).filter(Boolean);
  // resultado da busca no texto (só quando o botão está ligado e já respondeu para este mesmo texto)
  const noTexto = S.busca.ligada && S.busca.q === q ? S.busca.chats || {} : {};
  const list = $("#chat-list");
  list.innerHTML = "";
  const agentsById = Object.fromEntries(S.agents.map((a) => [a.id, a]));
  const groups = { "Hoje": [], "Últimos 7 dias": [], "Anteriores": [] };
  const arquivados = [];
  const now = Date.now() / 1000;
  for (const c of S.chats) {
    if (termos.length && !termos.every((t) => norm(c.title).includes(t)) && !noTexto[c.id]) continue;
    if (c.archived) { arquivados.push(c); continue; }
    const age = now - (c.updated_at || 0);
    (age < 86400 ? groups["Hoje"] : age < 7 * 86400 ? groups["Últimos 7 dias"] : groups["Anteriores"]).push(c);
  }
  for (const [label, items] of Object.entries(groups)) {
    if (!items.length) continue;
    // cada período recolhe como a seção de agentes; a escolha fica guardada neste navegador
    const recolhido = S.gruposRecolhidos.includes(label);
    const tit = h("div", { class: `group-label section-toggle ${recolhido ? "collapsed" : ""}`, role: "button", tabindex: "0",
                           title: "Mostrar/ocultar", onclick: () => alternarGrupo(label) },
      `<span class="chev">▾</span> ${esc(label)} <span class="grp-count">(${items.length})</span>`);
    tit.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); alternarGrupo(label); } });
    list.append(tit);
    if (recolhido) continue;
    for (const c of items) list.append(itemChat(c, agentsById, noTexto[c.id], termos));
  }
  if (!list.children.length) list.append(h("div", { class: "muted small", style: "padding:10px" }, q ? "Nada encontrado." : "Nenhum chat ainda."));
  renderArquivados(arquivados, agentsById, q, noTexto, termos);
  renderSideAgents();  // o ícone de carregamento dos agentes acompanha o estado dos chats
}

function itemChat(c, agentsById, achado = null, termos = []) {
  const agent = agentsById[c.agent_id];
  const waiting = (S.pending[c.id] || []).length > 0;
  const st = waiting ? "waiting" : c.status;
  const trechos = achado ? achado.trechos.map((t) => `<div class="sub trecho"><b>${esc(t.quem)}:</b> ${trechoHtml(t)}</div>`).join("")
    + (achado.total > achado.trechos.length ? `<div class="sub trecho-mais">+${achado.total - achado.trechos.length} mensage${achado.total - achado.trechos.length > 1 ? "ns" : "m"}</div>` : "") : "";
  const el = h("a", { class: `chat-item ${c.id === S.chatId && S.view === "chat" ? "active" : ""}`, href: `#/chat/${c.id}`, title: c.title },
    `<span class="dot ${c.provider}"></span>
     <div class="t">${esc(c.title)}${agent ? `<div class="sub">🤖 ${esc(agent.name)}</div>` : ""}${trechos}</div>
     <span class="st ${st}" title="${st === "running" ? "Análise em andamento" : st === "waiting" ? "Aguardando sua aprovação" : ""}"></span>`);
  el.addEventListener("contextmenu", (e) => { e.preventDefault(); openChatMenu(c, e.clientX, e.clientY); });
  if (achado) el.addEventListener("click", () => { S.destaque = termos; });  // ao abrir, rola até a mensagem encontrada
  return el;
}

/* Trecho da mensagem com as palavras buscadas em destaque (posições vindas do servidor). */
function trechoHtml(t) {
  let html = "", pos = 0;
  for (const [a, b] of t.marcas) {
    if (a < pos) continue;
    html += esc(t.texto.slice(pos, a)) + `<mark>${esc(t.texto.slice(a, b))}</mark>`;
    pos = b;
  }
  return (t.inicio ? "…" : "") + html + esc(t.texto.slice(pos)) + (t.fim ? "…" : "");
}

/* Busca no texto das conversas: só roda com o botão ligado, 350 ms depois que a digitação para. */
S.busca = { ligada: false, q: "", chats: null, timer: null, seq: 0 };
function agendarBuscaConteudo() {
  clearTimeout(S.busca.timer);
  const q = $("#chat-search").value.trim();
  const st = $("#search-status");
  S.busca.seq++;  // descarta respostas de buscas anteriores
  if (!S.busca.ligada || q.length < 2) {
    S.busca.q = ""; S.busca.chats = null;
    st.textContent = S.busca.ligada ? "Busca no texto ligada: digite ao menos 2 letras." : "";
    st.classList.toggle("hidden", !S.busca.ligada);
    return;
  }
  st.textContent = "Buscando no texto das conversas…";
  st.classList.remove("hidden");
  const seq = S.busca.seq;
  S.busca.timer = setTimeout(async () => {
    try {
      const r = await api("GET", `/api/busca?q=${encodeURIComponent(q)}`);
      if (seq !== S.busca.seq) return;
      S.busca.q = q; S.busca.chats = r.chats;
      const n = Object.keys(r.chats).length;
      const m = Object.values(r.chats).reduce((s, v) => s + v.total, 0);
      st.textContent = n ? `${m} mensage${m > 1 ? "ns" : "m"} em ${n} conversa${n > 1 ? "s" : ""}` : "Nada no texto das conversas.";
      renderChatList();
    } catch (e) { if (seq === S.busca.seq) st.textContent = e.message; }
  }, 350);
}
function alternarBuscaConteudo() {
  S.busca.ligada = !S.busca.ligada;
  const b = $("#search-content");
  b.classList.toggle("on", S.busca.ligada);
  b.setAttribute("aria-pressed", S.busca.ligada);
  $("#chat-search").placeholder = S.busca.ligada ? "Buscar no texto dos chats…" : "Buscar chats…";
  $("#chat-search").focus();
  agendarBuscaConteudo();
  renderChatList();
}

/* Ao abrir um chat pela busca no texto: rola até a primeira mensagem com as palavras e pisca em volta dela. */
function destacarAchado(wrap, termos) {
  const alvo = [...wrap.querySelectorAll(".msg.user, .msg.assistant")]
    .find((m) => { const n = norm(m.textContent); return termos.every((t) => n.includes(t)); });
  if (!alvo) return false;
  alvo.scrollIntoView({ block: "center" });
  alvo.classList.add("busca-achado");
  setTimeout(() => alvo.classList.remove("busca-achado"), 2600);
  return true;
}

S.gruposRecolhidos = (() => { try { return JSON.parse(localStorageGet("gruposRecolhidos") || "[]"); } catch { return []; } })();
function alternarGrupo(label) {
  const i = S.gruposRecolhidos.indexOf(label);
  if (i >= 0) S.gruposRecolhidos.splice(i, 1); else S.gruposRecolhidos.push(label);
  localStorageSet("gruposRecolhidos", JSON.stringify(S.gruposRecolhidos));
  renderChatList();
}

/* Seção "Arquivados" abaixo dos agentes: começa recolhida; na busca, abre sozinha se achar algo arquivado. */
S.archivedCollapsed = true;
function renderArquivados(itens, agentsById, q, noTexto = {}, termos = []) {
  const total = S.chats.filter((c) => c.archived).length;
  const recolhido = S.archivedCollapsed && !(q && itens.length);
  $("#archived-toggle").classList.toggle("collapsed", recolhido);
  $("#archived-count").textContent = total ? `(${total})` : "";
  const list = $("#side-archived-list");
  list.classList.toggle("hidden", recolhido);
  list.innerHTML = "";
  if (!itens.length) {
    list.append(h("div", { class: "muted small", style: "padding:6px 12px" }, q && total ? "Nada encontrado nos arquivados." : "Nenhum chat arquivado. Clique com o botão direito num chat e escolha Arquivar."));
    return;
  }
  for (const c of itens) list.append(itemChat(c, agentsById, noTexto[c.id], termos));
}

async function arquivarChat(chat, arquivar) {
  try {
    upsertChat(await api("PATCH", `/api/chats/${chat.id}`, { archived: arquivar }));
    toast(arquivar ? `"${chat.title}" foi para Arquivados.` : `"${chat.title}" voltou para a lista de chats.`);
  } catch (e) { toast(e.message); }
}

/* Menu do botão direito sobre um chat da barra lateral. */
function openChatMenu(chat, x, y) {
  const menu = $("#ctx-menu");
  menu.innerHTML = "";
  const item = (label, fn, cls = "") => menu.append(h("button", { class: cls, role: "menuitem", onclick: () => { closeChatMenu(); fn(); } }, label));
  item("Abrir", () => (location.hash = `#/chat/${chat.id}`));
  item("✏️ Renomear", () => renameChat(chat));
  if (chat.archived) item("📤 Desarquivar", () => arquivarChat(chat, false));
  else item("🗄 Arquivar", () => arquivarChat(chat, true));
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
  if (S.chatId !== id) { guardarRascunho(); restaurarRascunho(id); }  // cada chat com o seu texto e anexos
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
  const termos = S.destaque;
  S.destaque = null;
  if (!(termos && termos.length && destacarAchado(wrap, termos))) scrollDown(true);
  $("#input").focus({ preventScroll: true });
}

/* Rascunho por chat: a caixa de texto é uma só, então o que não foi enviado fica guardado ao trocar de chat. */
S.rascunhos = {};  // chatId -> { texto, anexos }

function guardarRascunho() {
  if (!S.chatId) return;
  const texto = $("#input").value;
  if (texto.trim() || S.anexos.chat.length) S.rascunhos[S.chatId] = { texto, anexos: S.anexos.chat };
  else delete S.rascunhos[S.chatId];
}

function restaurarRascunho(id) {
  const r = S.rascunhos[id] || { texto: "", anexos: [] };
  $("#input").value = r.texto;
  S.anexos.chat = r.anexos;
  renderTray("chat");
  autosize();
  hideSlash();
  atualizarBotaoEnviar("chat");
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
  const esf = c.provider !== "antigravity" && c.effort;  // no Antigravity o nível já está no nome do modelo
  $("#composer-info").textContent = `${modelLabel}${esf ? " · " + (EFFORT_LABEL[esf] || esf) : ""}${agent ? " · 🤖 " + agent.name : ""}`;
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
  if (c.effort && c.provider !== "antigravity") parts.push(`esforço ${EFFORT_LABEL[c.effort] || c.effort}`);
  if (c.provider === "claude") parts.push(find(S.meta?.claude?.permissionModes, c.permission_mode) || c.permission_mode);
  else if (c.provider === "antigravity") parts.push(find(S.meta?.antigravity?.permissionModes, c.permission_mode) || c.permission_mode);
  else parts.push(find(S.meta?.codex?.sandboxModes, c.sandbox) || c.sandbox, find(CODEX_APPROVAL, c.approval_policy) || c.approval_policy);
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
  // Algo entrou depois do grupo (aviso, plano, subagente…): o grupo anterior acabou, então fecha antes de abrir outro.
  // Sem isso ele ficava "carregando" para sempre no meio da conversa.
  if (g && !g.done) finishActivity(g);
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
function finishActivity(g = S.activity) {
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
  cartoesArquivos(el, texto);
}

/* Arquivos citados na resposta (caminho completo de um .pptx, .pdf, .xlsx...): cartão com Abrir, Baixar e, no
   PowerPoint, PDF e prévia dos slides. Só aparecem os que existem no disco. */
const EXT_CARTAO = "pptx|pdf|xlsx|xlsm|docx|csv|png";
const ICONE_ARQUIVO = { pptx: "📽️", pdf: "📕", xlsx: "📊", xlsm: "📊", csv: "📊", docx: "📝", png: "🖼️" };
function caminhosCitados(texto) {
  const achados = new Set();
  const comEspaco = new RegExp(`[\`"]([A-Za-z]:[\\\\/][^\`"\\n]+?\\.(?:${EXT_CARTAO}))[\`"]`, "gi");
  const semEspaco = new RegExp(`[A-Za-z]:[\\\\/][^\\s"'<>|*?\`]+?\\.(?:${EXT_CARTAO})(?![\\w.])`, "gi");
  for (const m of String(texto || "").matchAll(comEspaco)) achados.add(m[1]);
  for (const m of String(texto || "").matchAll(semEspaco)) {
    if (![...achados].some((a) => a.endsWith(m[0]) || a.includes(m[0]))) achados.add(m[0]);
  }
  return [...achados].slice(0, 8);
}

async function cartoesArquivos(el, texto) {
  const caminhos = caminhosCitados(texto);
  if (!caminhos.length || el.querySelector(":scope > .arq-cartoes")) return;
  const caixa = h("div", { class: "arq-cartoes" });
  const barra = el.querySelector(":scope > .msg-actions");
  if (barra) barra.before(caixa); else el.append(caixa);
  const infos = await Promise.all(caminhos.map((p) =>
    api("GET", `/api/arquivos/info?path=${encodeURIComponent(p)}`).catch(() => null)));
  const vistos = new Set();
  for (const info of infos) {
    if (!info?.existe || vistos.has(info.path.toLowerCase())) continue;
    vistos.add(info.path.toLowerCase());
    caixa.append(cartaoArquivo(info));
  }
  if (!caixa.children.length) caixa.remove();
}

const urlArquivo = (path, inline = false) => `/api/arquivos/baixar?path=${encodeURIComponent(path)}${inline ? "&inline=true" : ""}`;

function cartaoArquivo(info) {
  const pptx = info.ext === "pptx";
  const card = h("div", { class: "arq-card" },
    `<div class="arq-linha">
       <span class="doc-ico">${ICONE_ARQUIVO[info.ext] || "📄"}</span>
       <div class="doc-info"><b title="${esc(info.path)}">${esc(info.nome)}</b><small>${esc(info.pasta)} · ${tamanhoLegivel(info.tamanho)}</small></div>
       <div class="arq-acoes">
         <button type="button" class="arq-btn" data-a="abrir" title="Abrir no programa do Windows">Abrir</button>
         <a class="arq-btn" href="${urlArquivo(info.path)}" download title="Baixar uma cópia">Baixar</a>
         ${pptx ? `<button type="button" class="arq-btn" data-a="pdf" title="Exportar para PDF (ao lado do arquivo)">PDF</button>
                   <button type="button" class="arq-btn" data-a="previa" title="Ver os slides aqui">Prévia</button>` : ""}
         ${info.ext === "pdf" ? `<a class="arq-btn" href="${urlArquivo(info.path, true)}" target="_blank" rel="noopener" title="Ver no navegador">Ver</a>` : ""}
         <button type="button" class="arq-btn" data-a="pasta" title="Mostrar na pasta">📁</button>
       </div>
     </div>
     <div class="arq-previa hidden"></div>`);
  const ocupado = async (btn, rotulo, fn) => {
    const antes = btn.textContent;
    btn.disabled = true;
    btn.textContent = rotulo;
    try { await fn(); } catch (e) { toast(e.message, 6000); } finally { btn.disabled = false; btn.textContent = antes; }
  };
  card.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-a]");
    if (!btn) return;
    const acao = btn.dataset.a;
    if (acao === "abrir") ocupado(btn, "Abrindo…", () => api("POST", "/api/arquivos/abrir", { path: info.path }));
    if (acao === "pasta") api("POST", "/api/arquivos/abrir-pasta", { path: info.path }).catch((err) => toast(err.message));
    if (acao === "pdf") ocupado(btn, "Gerando…", async () => {
      const r = await api("POST", "/api/arquivos/exportar", { path: info.path, formato: "pdf" });
      const pdf = await api("GET", `/api/arquivos/info?path=${encodeURIComponent(r.arquivos[0])}`);
      if (pdf.existe) card.after(cartaoArquivo(pdf));
      toast("PDF gerado ao lado do arquivo.");
    });
    if (acao === "previa") {
      const area = $(".arq-previa", card);
      if (area.children.length) { area.classList.toggle("hidden"); return; }
      ocupado(btn, "Gerando…", async () => {
        const r = await api("POST", "/api/arquivos/exportar", { path: info.path, formato: "png" });
        area.innerHTML = r.arquivos.map((p, i) =>
          `<a href="${urlArquivo(p, true)}" target="_blank" rel="noopener" title="Slide ${i + 1}">
             <img src="${urlArquivo(p, true)}" alt="Slide ${i + 1}" loading="lazy"><span>${i + 1}</span></a>`).join("");
        area.classList.remove("hidden");
      });
    }
  });
  return card;
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
        const url = anexoUrl(i.file);
        return `<a href="${url}" target="_blank" rel="noopener" title="${esc(i.name)}"><img src="${url}" alt="${esc(i.name)}" loading="lazy"></a>`;
      }).join("");
      const docs = (ev.files || []).map((d) =>
        `<a class="bubble-doc" href="${anexoUrl(d.file)}" target="_blank" rel="noopener" title="Baixar ${esc(d.name)}">
           <span class="doc-ico">${EXT_DOCUMENTOS[extensao(d.name)] || "📄"}</span><span class="doc-nome">${nomeQuebravel(d.name)}</span></a>`).join("");
      wrap.append(h("div", { class: "msg user" },
        `<div class="bubble">${imgs ? `<div class="bubble-imgs">${imgs}</div>` : ""}${docs ? `<div class="bubble-docs">${docs}</div>` : ""}${ev.text ? `<div>${esc(ev.text)}</div>` : ""}</div>${horaMensagem(ev.ts)}`));
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
/* Documentos aceitos (vão para o disco; a IA recebe o caminho e, no Excel, cada aba em CSV). */
const EXT_DOCUMENTOS = { xlsx: "📊", xlsm: "📊", xls: "📊", csv: "📊", pdf: "📕", docx: "📝", pptx: "📽️", txt: "📄", json: "📄", md: "📄" };
const extensao = (nome) => (String(nome || "").match(/\.([a-z0-9]+)$/i) || [])[1]?.toLowerCase() || "";
const ehDocumento = (f) => !!EXT_DOCUMENTOS[extensao(f?.name)];
/* Data e hora discretas abaixo de cada mensagem do usuário (mouse em cima: data completa com segundos).
   Conversas importadas antigas podem não ter horário: aí não mostra nada. */
function horaMensagem(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const data = d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
  const hora = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  const completo = d.toLocaleString("pt-BR", { dateStyle: "full", timeStyle: "medium" });
  return `<time class="msg-hora" datetime="${d.toISOString()}" title="${esc(completo)}">${data} · ${hora}</time>`;
}
/* Nome de arquivo com pontos de quebra naturais (depois de _ - ] ) e antes da extensão), para não cortar ".pd|f". */
const nomeQuebravel = (nome) => esc(nome).replace(/([_\-\])])/g, "$1<wbr>").replace(/\.(?=[^.]+$)/, "<wbr>.");
const ehImagem = (f) => !!f?.type && TIPOS_IMAGEM.test(f.type);

async function prepararDocumento(file) {
  if (file.size > 30 * 1024 * 1024) throw new Error(`"${file.name}" é grande demais (máximo 30 MB).`);
  const dataUrl = await lerComoDataURL(file);
  return { name: file.name, media_type: file.type || "", data: dataUrl.split(",")[1] || "", doc: true, ext: extensao(file.name), size: file.size };
}

async function adicionarAnexos(onde, arquivos) {
  const lista = [...arquivos].filter(Boolean);
  const validos = lista.filter((f) => ehImagem(f) || ehDocumento(f));
  const recusados = lista.filter((f) => !ehImagem(f) && !ehDocumento(f));
  if (recusados.length) toast(`Tipo não suportado: ${recusados.map((f) => f.name).join(", ")}. Use imagens, Excel, CSV, PDF, Word, PowerPoint, TXT, JSON ou MD.`, 6000);
  if (!validos.length) return false;
  for (const f of validos) {
    if (S.anexos[onde].length >= 10) { toast("No máximo 10 arquivos por mensagem."); break; }
    try { S.anexos[onde].push(ehImagem(f) ? await prepararImagem(f) : await prepararDocumento(f)); }
    catch (e) { toast(e.message); }
  }
  renderTray(onde);
  return true;
}
const tamanhoLegivel = (b) => (b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1).replace(".", ",")} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
function renderTray(onde) {
  const tray = $(onde === "chat" ? "#tray" : "#start-tray");
  const lista = S.anexos[onde];
  tray.classList.toggle("hidden", !lista.length);
  atualizarBotaoEnviar(onde);
  tray.innerHTML = "";
  lista.forEach((a, i) => {
    const item = a.doc
      ? h("div", { class: "att att-doc", title: a.name },
          `<span class="doc-ico">${EXT_DOCUMENTOS[a.ext] || "📄"}</span><span class="doc-info"><b>${esc(a.name)}</b><small>${esc(a.ext.toUpperCase())} · ${tamanhoLegivel(a.size)}</small></span>`)
      : h("div", { class: "att", title: a.name }, `<img src="${a.preview}" alt="${esc(a.name)}">`);
    item.append(h("button", { type: "button", class: "att-x", title: "Remover", onclick: () => { lista.splice(i, 1); renderTray(onde); } }, "✕"));
    tray.append(item);
  });
}
function anexosParaEnvio(onde) {
  return S.anexos[onde].map(({ name, media_type, data }) => ({ name, media_type, data }));
}
function anexoUrl(file) { return `/api/chats/${encodeURIComponent(S.chatId)}/anexos/${encodeURIComponent(file)}`; }
/* Liga colar (Ctrl+V), arrastar e o botão 📎 a uma caixa de mensagem. */
function ligarAnexos(onde, textarea, botao, campoArquivo, areaSoltar) {
  textarea.addEventListener("paste", async (e) => {
    const arquivos = [...(e.clipboardData?.items || [])].filter((i) => i.kind === "file").map((i) => i.getAsFile());
    if (arquivos.some((f) => f && (ehImagem(f) || ehDocumento(f)))) {
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
  guardarRascunho();
  S.chatId = null;
  const defProvider = agent?.provider || provider || localStorageGet("lastProvider") || "antigravity";
  S.start = { agentId: agent?.id || null, provider: defProvider };
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
  const cwd = agent?.cwd || localStorageGet("lastCwd") || S.meta?.home || "";
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
    const hasAnyConnected = (S.accounts || []).some((a) => a.status === "connected");
    const emptyBanner = (!hasAnyConnected && (S.accounts || []).length > 0)
      ? `<div class="empty-start-banner">
          <div class="empty-start-icon">💡</div>
          <div class="empty-start-info">
            <b>Nenhuma IA conectada no momento</b>
            <div>Conecte o Google Antigravity, Claude ou Codex para começar a conversar e criar agentes.</div>
          </div>
          <button type="button" class="btn primary btn-sm" id="btn-start-add-ai">➕ Adicionar IA</button>
        </div>`
      : "";
    body.innerHTML = `${emptyBanner}<div class="hero-box"><span class="logo hero-logo">A</span><h1 class="hero-title">${saudacao()}</h1></div>`;
    opts.innerHTML = `
      <div class="opts-row">
        <div class="seg seg-sm" id="start-provider">
          <button type="button" data-p="antigravity" class="${provider === "antigravity" ? "on" : ""}">Antigravity</button>
          <button type="button" data-p="claude" class="${provider === "claude" ? "on" : ""}">Claude</button>
          <button type="button" data-p="codex" class="${provider === "codex" ? "on" : ""}">Codex</button>
        </div>
        <details class="opts"><summary>Modelo, permissões e pasta</summary>
          ${provider === "antigravity" && !S.meta?.antigravity?.available ? `<div class="err-box">agy.exe não encontrado. Instale o Google Antigravity ou defina AGY_PATH.</div>` : ""}
          ${provider === "codex" && !S.meta?.codex?.available ? `<div class="err-box">codex.exe não encontrado. Instale a extensão do Codex no VS Code ou defina CODEX_PATH.</div>` : ""}
          <div class="grid2">${providerFieldsHtml("ds", provider, padraoChat(provider))}</div>
          <label>Pasta de trabalho ${folderFieldHtml("ds-cwd", cwd)}</label>
          <div class="hint">A pasta não pode ser trocada depois da primeira mensagem. Dica: para instruções e configurações salvas, escolha um agente na barra lateral.</div>
        </details>
      </div>`;
    bindFolderButtons(opts, "Pasta de trabalho desta conversa");
    $$("select", opts).forEach((s) => s.addEventListener("change", atualizarInfoStart));
    if ($("#btn-start-add-ai")) $("#btn-start-add-ai").onclick = () => openAddAiModal();
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

/* Chat livre novo no Claude começa no Opus com raciocínio alto (dá para trocar em "Modelo, permissões e pasta"). */
const PADRAO_CHAT_CLAUDE = { model: "opus", effort: "high" };
const PADRAO_CHAT_ANTIGRAVITY = { model: "gemini-3.8-flash-high" };

/* Modelo e esforço com que um chat novo abre: o modelo escolhido em Configurações → Contas, senão o padrão do hub. */
function padraoChat(provider) {
  const base = provider === "claude" ? PADRAO_CHAT_CLAUDE : (provider === "antigravity" ? PADRAO_CHAT_ANTIGRAVITY : {});
  const escolhido = S.meta?.modelosPadrao?.[provider];
  return escolhido ? { ...base, model: escolhido } : base;
}

/* Seletor "Modelo padrão dos chats novos" no cartão de cada conta. */
function caixaModeloPadrao(provider) {
  const fixo = provider === "claude" ? PADRAO_CHAT_CLAUDE.model : (provider === "antigravity" ? PADRAO_CHAT_ANTIGRAVITY.model : null);
  let opcoes = modelOptions(provider);  // no Codex já vem "Padrão do Codex (modelo do config.toml)"
  if (fixo) {
    const rotulo = opcoes.find((o) => o.value === fixo)?.label || fixo;
    opcoes = [{ value: "", label: `Padrão do hub (${rotulo})` }, ...opcoes];
  }
  const atual = S.meta?.modelosPadrao?.[provider] || "";
  return `<div class="account-model-box">
      <label class="account-model-label">Modelo padrão dos chats novos
        ${selectHtml(`padrao-${provider}`, opcoes, atual, `class="sel-modelo-padrao" data-p="${provider}"`)}</label>
      <div class="hint">Vale para os próximos chats do ${esc(NOME_IA[provider] || provider)}. Chats já abertos e agentes mantêm o modelo deles.</div>
    </div>`;
}

/* Etiqueta do modelo dentro da caixa de mensagem da tela de início. */
function atualizarInfoStart() {
  if (!S.start) return;
  const agent = S.agents.find((a) => a.id === S.start.agentId);
  const provider = S.start.provider;
  const valor = agent ? (agent.model ?? "") : ($("#ds-model")?.value ?? "");
  const nome = modelOptions(provider).find((m) => String(m.value) === String(valor))?.label || valor || "padrão";
  const esforco = provider === "antigravity" ? null : (agent ? agent.effort : $("#ds-effort")?.value);
  const provNome = provider === "claude" ? "Claude" : (provider === "antigravity" ? "Antigravity" : "Codex");
  $("#start-info").textContent = `${provNome} · ${nome}${esforco ? ` · ${EFFORT_LABEL[esforco] || esforco}` : ""}`;
}

/* Login por IA: o projeto não traz login nenhum. No primeiro chat com uma IA sem conta conectada nesta máquina,
   pede o login (ou uma chave de API) antes de enviar, em vez de mandar a mensagem e falhar. */
const NOME_IA = { claude: "Claude", codex: "Codex", antigravity: "Google Antigravity" };

async function garantirLogin(provider) {
  let acc;
  try {
    const r = await api("GET", "/api/accounts");
    S.accounts = r.accounts || [];
    acc = S.accounts.find((a) => a.id === provider);
  } catch { return true; }  // se a checagem falhar, não impede o envio: o erro real aparece no chat
  if (!acc || acc.status === "connected") return true;
  return pedirLogin(provider, acc);
}

function pedirLogin(provider, acc) {
  return new Promise((resolve) => {
    const nome = NOME_IA[provider] || provider;
    const semPrograma = acc.status === "unavailable" || (provider === "codex" && acc.available === false);
    const fundo = h("div", { class: "modal-backdrop" });
    fundo.innerHTML = `<div class="modal-box login-box" role="dialog" aria-modal="true" aria-labelledby="login-tit">
      <div class="modal-head"><h3 id="login-tit">🔑 Entrar no ${esc(nome)}</h3>
        <button type="button" class="btn ghost icon" data-a="fechar" title="Fechar">✕</button></div>
      <div class="modal-sub">${semPrograma
        ? `O ${esc(nome)} não está instalado neste computador. Instale-o (ou a extensão dele no VS Code) e tente de novo, ou use uma chave de API.`
        : `Para conversar com o ${esc(nome)}, entre na sua conta. O login abre no navegador e fica salvo só neste computador; ele não vai para o projeto nem para o GitHub.`}</div>
      <div class="login-status muted small" aria-live="polite"></div>
      <div class="login-acoes">
        <button type="button" class="btn ghost" data-a="chave">Usar chave de API</button>
        <button type="button" class="btn ghost" data-a="fechar">Cancelar</button>
        ${semPrograma ? "" : `<button type="button" class="btn primary" data-a="entrar">Entrar com o navegador</button>`}
      </div></div>`;
    let timer = null;
    const fim = (ok) => { clearInterval(timer); document.removeEventListener("keydown", esc_); fundo.remove(); resolve(ok); };
    const esc_ = (e) => { if (e.key === "Escape") fim(false); };
    document.addEventListener("keydown", esc_);
    fundo.addEventListener("click", async (e) => {
      const botao = e.target.closest("[data-a]");
      const a = botao?.dataset.a;
      if (e.target === fundo || a === "fechar") return fim(false);
      if (a === "chave") { fim(false); location.hash = "#/settings/contas"; return; }
      if (a !== "entrar") return;
      const st = fundo.querySelector(".login-status");
      botao.disabled = true;
      try { await api("POST", "/api/accounts/login", { provider }); }
      catch (err) { st.textContent = err.message; botao.disabled = false; return; }
      st.textContent = "Conclua o login na janela que abriu. Quando terminar, sua mensagem é enviada sozinha…";
      let n = 0;
      clearInterval(timer);
      timer = setInterval(async () => {
        n++;
        try {
          const r = await api("GET", "/api/accounts");
          S.accounts = r.accounts || [];
          if (S.accounts.find((x) => x.id === provider)?.status === "connected") { toast(`✅ ${nome} conectado.`); fim(true); return; }
        } catch { /* ainda esperando */ }
        if (n >= 120) {  // 5 minutos
          clearInterval(timer);
          st.textContent = "Não detectei o login em 5 minutos. Tente de novo.";
          botao.disabled = false;
        }
      }, 2500);
    });
    document.body.append(fundo);
    fundo.querySelector(semPrograma ? "[data-a=chave]" : "[data-a=entrar]").focus();
  });
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
    if (!(await garantirLogin(agent ? agent.provider : provider))) return;  // sem login: a mensagem fica na caixa
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
  const p = a.provider;
  const outros = S.agents.filter((x) => x.provider === p && x.id !== id);  // subagentes: mesma IA
  const listaSub = `<label>Subagentes que ele pode chamar</label>
    <div class="checks">${outros.length ? outros.map((s) => `<label><input type="checkbox" class="ag-sub" value="${s.id}" ${(a.subagents || []).includes(s.id) ? "checked" : ""}> ${esc(s.name)}</label>`).join("") : `<span class="muted small">Crie outros agentes ${p === "claude" ? "Claude" : (p === "antigravity" ? "Antigravity" : "Codex")} para usá-los como subagentes.</span>`}</div>`;
  form.innerHTML = `
    <h2>${id ? "Editar agente" : "Novo agente"}</h2>
    <div class="grid2">
      <label>Nome <input class="input" id="ag-name" value="${esc(a.name)}" required></label>
      <label>IA
        <div class="seg" id="ag-provider" style="display:flex">
          <button type="button" data-p="antigravity" class="${p === "antigravity" ? "on" : ""}">Antigravity</button>
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
    <div class="hint">${p === "claude" ? "Somadas ao prompt padrão do Claude Code (ele continua com ferramentas, skills, MCP e CLAUDE.md)." : (p === "antigravity" ? "Instruções do agente para o Antigravity / Gemini CLI." : "Enviadas como developer instructions do Codex (somam-se ao AGENTS.md e ao config.toml).")}</div>
    <label>Mensagem de boas-vindas
      <textarea id="ag-welcome" class="welcome" placeholder="${esc(agentGreeting({ name: a.name || "nome do agente", description: a.description }))}">${esc(a.welcome || "")}</textarea>
    </label>
    <div class="hint">Aparece quando você seleciona o agente na barra lateral, antes da primeira mensagem, e fica no início do chat. Aceita Markdown (**negrito**, listas…). Vazio = usa o texto de exemplo acima.</div>
    ${p === "claude" ? `
    <div class="grid2">
      <label>Ferramentas disponíveis <input class="input" id="ag-tools" value="${esc((a.tools || []).join(", "))}" placeholder="vazio = todas · ex.: Read, Grep, mcp__superset-herval__*"></label>
      <label>Ferramentas bloqueadas <input class="input" id="ag-disallowed_tools" value="${esc((a.disallowed_tools || []).join(", "))}" placeholder="ex.: Bash, Write"></label>
    </div>
    ${listaSub}
    ` : `
    <label>Servidores MCP desligados <input class="input" id="ag-disallowed_tools" value="${esc((a.disallowed_tools || []).join(", "))}" placeholder="ex.: mcp__powerbi-modeling-mcp"></label>
    <div class="hint">MCPs do ~/.codex/config.toml que este agente (e os subagentes dele) não deve usar.</div>
    ${listaSub}
    <div class="hint">No Codex, cada subagente vira um papel chamado pela ferramenta spawn_agent, com o modelo, o esforço e as instruções dele.</div>`}
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
  body.disallowed_tools = v("ag-disallowed_tools");
  body.subagents = $$(".ag-sub:checked").map((x) => x.value);
  if (provider === "claude") body.tools = v("ag-tools");
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

/* ------------------------------------------------------------------ configurações */
function updateMcpAlert() {
  const n = S.meta?.mcpIssues || 0;
  const badge = $("#mcp-alert");
  if (badge) {
    badge.textContent = n ? `⚠ ${n}` : "";
    badge.classList.toggle("hidden", !n);
    badge.title = n ? `${n} servidor(es) MCP com problema` : "";
  }
  const tabBadge = $("#st-mcp-badge");
  if (tabBadge) {
    tabBadge.textContent = n ? `⚠ ${n}` : "";
    tabBadge.classList.toggle("hidden", !n);
    tabBadge.title = n ? `${n} servidor(es) MCP com problema` : "";
  }
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

/* Reinicia o servidor (carrega código novo) e recarrega a tela quando ele voltar. */
async function reiniciarServidor() {
  const btn = $("#st-reiniciar");
  let r;
  try { r = await api("POST", "/api/servidor/reiniciar", {}); }
  catch (e) {
    if (!/trabalhando/.test(e.message)) { toast(e.message); return; }
    if (!confirm(`${e.message}\n\nReiniciar mesmo assim?`)) return;
    try { r = await api("POST", "/api/servidor/reiniciar", { forcar: true }); } catch (e2) { toast(e2.message); return; }
  }
  S.reiniciando = true;
  if (btn) { btn.disabled = true; btn.textContent = "Reiniciando…"; }
  toast("Reiniciando o Agent Hub… a tela recarrega sozinha.", 20000);
  const t0 = Date.now();
  const checar = async () => {
    try {
      const i = await (await fetch("/api/servidor", { cache: "no-store" })).json();
      if (i.inicio !== r.inicio) { location.reload(); return; }
    } catch { /* ainda trocando */ }
    if (Date.now() - t0 > 90000) {
      S.reiniciando = false;
      toast("O servidor não voltou. Abra pelo atalho Agent Hub.", 8000);
      if (btn) { btn.disabled = false; btn.textContent = "↻ Reiniciar o Agent Hub"; }
      return;
    }
    setTimeout(checar, 700);
  };
  setTimeout(checar, 1200);
}

function openAddAiModal() {
  const modal = $("#modal-add-ai");
  if (!modal) return;
  modal.classList.remove("hidden");
  const ag = (S.accounts || []).find((a) => a.id === "antigravity");
  const cl = (S.accounts || []).find((a) => a.id === "claude");
  const cx = (S.accounts || []).find((a) => a.id === "codex");
  const badgeHtml = (acc) => {
    if (acc?.status === "connected") return `<span class="badge-status ok">🟢 Conectado</span>`;
    return `<span class="badge-status dis">⚪ Não configurado</span>`;
  };
  if ($("#cat-badge-antigravity")) $("#cat-badge-antigravity").innerHTML = badgeHtml(ag);
  if ($("#cat-badge-claude")) $("#cat-badge-claude").innerHTML = badgeHtml(cl);
  if ($("#cat-badge-codex")) $("#cat-badge-codex").innerHTML = badgeHtml(cx);
}

function closeAddAiModal() {
  const modal = $("#modal-add-ai");
  if (modal) modal.classList.add("hidden");
}

async function renderContas() {
  const container = $("#sec-contas");
  if (!container) return;
  container.innerHTML = `<div class="muted small" style="padding:10px 0">Carregando contas de IA…</div>`;
  let accountsData = [];
  try {
    const res = await api("GET", "/api/accounts");
    accountsData = res.accounts || [];
    S.accounts = accountsData;
  } catch (e) {
    container.innerHTML = `<div class="err-box">Erro ao consultar contas: ${esc(e.message)}</div>`;
    return;
  }

  const antigravity = accountsData.find((a) => a.id === "antigravity") || {};
  const claude = accountsData.find((a) => a.id === "claude") || {};
  const codex = accountsData.find((a) => a.id === "codex") || {};
  const hasAnyConnected = accountsData.some((a) => a.status === "connected");

  const statusBadge = (st) => {
    if (st === "connected") return `<span class="badge-status ok">🟢 Conectado</span>`;
    if (st === "error") return `<span class="badge-status err">⚠️ Erro na conexão</span>`;
    return `<span class="badge-status dis">⚪ Não configurado</span>`;
  };

  const renderAntigravityProfiles = () => {
    const list = antigravity.profiles || [];
    if (!list.length) return "";
    return `
      <div class="account-profiles-box">
        <label style="font-weight:600;font-size:13px;display:block;margin-bottom:4px">Perfis e Contas Salvas:</label>
        ${list.map((p) => `
          <div class="account-profile-item ${p.active ? 'active' : ''}">
            <div class="account-profile-name">
              <span>👤 <b>${esc(p.label || p.email || p.id)}</b></span>
              ${p.active ? '<span class="badge-active">Ativa</span>' : ''}
            </div>
            <div class="row gap align-center">
              ${!p.active ? `<button type="button" class="btn btn-sm btn-ag-switch" data-id="${esc(p.id)}">Usar esta conta</button>` : ''}
              ${list.length > 1 ? `<button type="button" class="btn ghost icon btn-ag-del-prof" data-id="${esc(p.id)}" title="Remover perfil salvo">🗑</button>` : ''}
            </div>
          </div>
        `).join('')}
      </div>
    `;
  };

  const renderCodexProfiles = () => {
    const list = codex.profiles || [];
    if (!list.length) return "";
    return `
      <div class="account-profiles-box">
        <label style="font-weight:600;font-size:13px;display:block;margin-bottom:4px">Perfis e Contas Salvas:</label>
        ${list.map((p) => `
          <div class="account-profile-item ${p.active ? 'active' : ''}">
            <div class="account-profile-name">
              <span>👤 <b>${esc(p.label || p.email || p.id)}</b></span>
              ${p.active ? '<span class="badge-active">Ativa</span>' : ''}
            </div>
            <div class="row gap align-center">
              ${!p.active ? `<button type="button" class="btn btn-sm btn-codex-switch" data-id="${esc(p.id)}">Usar esta conta</button>` : ''}
              ${list.length > 1 ? `<button type="button" class="btn ghost icon btn-codex-del-prof" data-id="${esc(p.id)}" title="Remover perfil salvo">🗑</button>` : ''}
            </div>
          </div>
        `).join('')}
      </div>
    `;
  };

  const renderClaudeProfiles = () => {
    const list = claude.profiles || [];
    if (!list.length) return "";
    return `
      <div class="account-profiles-box">
        <label style="font-weight:600;font-size:13px;display:block;margin-bottom:4px">Chaves de API Salvas:</label>
        ${list.map((p) => `
          <div class="account-profile-item ${claude.has_custom_key && claude.masked_key === p.masked_key ? 'active' : ''}">
            <div class="account-profile-name">
              <span>🔑 <b>${esc(p.label)}</b></span>
              ${claude.has_custom_key && claude.masked_key === p.masked_key ? '<span class="badge-active">Ativa</span>' : ''}
            </div>
            <div class="row gap align-center">
              ${!(claude.has_custom_key && claude.masked_key === p.masked_key) ? `<button type="button" class="btn btn-sm btn-claude-switch" data-id="${esc(p.id)}">Ativar chave</button>` : ''}
              <button type="button" class="btn ghost icon btn-claude-del-prof" data-id="${esc(p.id)}" title="Remover chave salva">🗑</button>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  };

  container.innerHTML = `
    <div class="card" style="margin-bottom:16px">
      <div class="row align-center gap" style="justify-content:space-between;flex-wrap:wrap">
        <div>
          <h3>Gestão de Contas de IA</h3>
          <div class="hint" style="margin-top:2px;font-size:13px">
            Aqui você pode gerenciar, conectar, trocar e alternar entre suas contas de IA <b>diretamente pelo Agent Hub</b>, sem precisar abrir o VS Code.
          </div>
        </div>
        <button type="button" class="btn primary" id="btn-open-add-ai">➕ Adicionar IA</button>
      </div>
    </div>

    ${!hasAnyConnected ? `
      <div class="onboarding-card">
        <div class="onboarding-icon">🚀</div>
        <div class="onboarding-content">
          <h3>Bem-vindo ao Agent Hub!</h3>
          <p>Nenhuma IA está conectada ainda. Conecte sua primeira IA (Google Antigravity, Claude ou Codex) para começar a conversar e criar agentes personalizados.</p>
          <button type="button" class="btn primary lg" id="btn-onboarding-add-ai">➕ Conectar Minha Primeira IA</button>
        </div>
      </div>
    ` : ''}

    <!-- Card Antigravity -->
    <div class="card account-card">
      <div class="account-card-header">
        <div class="account-card-title">
          <span class="badge antigravity">Google Antigravity</span>
          <div>
            <b>${esc(antigravity.titulo || "Google Antigravity / Gemini CLI")}</b>
            <div class="small muted">Google</div>
          </div>
        </div>
        <div>${statusBadge(antigravity.status)}</div>
      </div>

      <div class="account-details-grid">
        ${antigravity.email ? `
        <div class="account-kv-item highlight">
          <span class="k">Conta Google Ativa:</span>
          <span class="v"><b>${esc(antigravity.email)}</b></span>
        </div>` : ""}
        <div class="account-kv-item">
          <span class="k">Autenticação:</span>
          <span class="v">${antigravity.has_custom_key ? "🔑 Chave de API GEMINI_API_KEY" : (antigravity.status === 'connected' ? "🌐 Conta Google / Gemini CLI" : "Não configurado")}</span>
        </div>
        <div class="account-kv-item">
          <span class="k">Executável CLI:</span>
          <span class="v">${antigravity.available ? `<code class="small">${esc(antigravity.path || "agy.exe")}</code>` : `<span class="err-inline">agy.exe não encontrado</span>`}</span>
        </div>
        <div class="account-kv-item">
          <span class="k">Modelos e Recursos:</span>
          <span class="v">${antigravity.models_count ? `${antigravity.models_count} modelos (Gemini 3.8 Flash, Claude Sonnet 4.6 Thinking, GPT-OSS)` : "Gemini 3.8 Flash, Claude Sonnet"} · ferramentas e raciocínio profundo</span>
        </div>
        <div class="account-kv-item">
          <span class="k">Pasta de configuração:</span>
          <span class="v"><code class="small">${esc(antigravity.config_path || "~/.gemini")}</code></span>
        </div>
      </div>

      ${renderAntigravityProfiles()}

      <!-- Chave API Gemini Customizada -->
      <div class="account-key-box">
        <div class="row align-center gap">
          <label style="margin:0;font-size:13px;font-weight:600">Usar Chave de API Google / Gemini (GEMINI_API_KEY):</label>
          <span class="small muted" style="margin-left:auto">${antigravity.has_custom_key ? `Ativa: <code>${esc(antigravity.masked_key)}</code>` : "Opcional"}</span>
        </div>
        <div class="row gap" style="margin-top:6px">
          <input type="password" id="ag-api-key" class="input flex-1" placeholder="${antigravity.has_custom_key ? 'Substituir chave...' : 'AIzaSy... (salvar chave GEMINI_API_KEY)'}" autocomplete="off">
          <input type="text" id="ag-key-label" class="input" style="width:140px" placeholder="Rótulo (opcional)" autocomplete="off">
          <button type="button" class="btn" id="ag-save-key">Salvar chave</button>
          ${antigravity.has_custom_key ? `<button type="button" class="btn ghost" id="ag-remove-key" title="Desativar chave">Remover</button>` : ""}
        </div>
      </div>

      ${caixaModeloPadrao("antigravity")}

      <div class="account-actions-row">
        <div class="account-actions-left">
          <button type="button" class="btn primary" id="ag-btn-login" title="Conectar ou trocar de conta Google">${antigravity.status === 'connected' ? '🔑 Trocar conta' : '🔑 Conectar conta Google'}</button>
          ${antigravity.status === 'connected' ? `<button type="button" class="btn ghost" id="ag-btn-logout">🚪 Desconectar</button>` : ''}
          <button type="button" class="btn" id="ag-btn-refresh">↻ Recarregar</button>
        </div>
        <div class="account-actions-right">
          <a href="#/consumo" class="btn ghost btn-consumo-link">📊 Ver consumo do Antigravity</a>
        </div>
      </div>
    </div>

    <!-- Card Claude -->
    <div class="card account-card">
      <div class="account-card-header">
        <div class="account-card-title">
          <span class="badge claude">Claude</span>
          <div>
            <b>${esc(claude.titulo || "Claude Code & Agent SDK")}</b>
            <div class="small muted">Anthropic</div>
          </div>
        </div>
        <div>${statusBadge(claude.status)}</div>
      </div>

      <div class="account-details-grid">
        <div class="account-kv-item">
          <span class="k">Autenticação ativa:</span>
          <span class="v">${claude.has_custom_key ? "🔑 Chave de API personalizada" : (claude.status === 'connected' ? "💻 Login do terminal (OAuth Anthropic)" : "Desconectado")}</span>
        </div>
        <div class="account-kv-item">
          <span class="k">Versão do Claude:</span>
          <span class="v"><b>${esc(claude.version || "consultando…")}</b></span>
        </div>
        ${claude.user_id ? `
        <div class="account-kv-item">
          <span class="k">Identificador da instalação:</span>
          <span class="v"><code class="small">${esc(claude.user_id.slice(0, 20))}…</code></span>
        </div>` : ""}
        <div class="account-kv-item">
          <span class="k">Modelos e Recursos:</span>
          <span class="v">${claude.models_count ? `${claude.models_count} modelos disponíveis` : "Sonnet, Opus, Haiku"} · subagentes e MCPs</span>
        </div>
        <div class="account-kv-item">
          <span class="k">Pasta de configuração:</span>
          <span class="v"><code class="small">${esc(claude.config_path || "~/.claude")}</code></span>
        </div>
      </div>

      ${claude.error ? `<div class="err-box" style="margin-top:10px">${esc(claude.error)}</div>` : ""}

      ${renderClaudeProfiles()}

      <!-- Chave API Customizada -->
      <div class="account-key-box">
        <div class="row align-center gap">
          <label style="margin:0;font-size:13px;font-weight:600">Usar Chave de API Anthropic:</label>
          <span class="small muted" style="margin-left:auto">${claude.has_custom_key ? `Ativa: <code>${esc(claude.masked_key)}</code>` : "Opcional"}</span>
        </div>
        <div class="row gap" style="margin-top:6px">
          <input type="password" id="claude-api-key" class="input flex-1" placeholder="${claude.has_custom_key ? 'Substituir chave...' : 'sk-ant-api03-... (salvar chave de API)'}" autocomplete="off">
          <input type="text" id="claude-key-label" class="input" style="width:140px" placeholder="Rótulo (opcional)" autocomplete="off">
          <button type="button" class="btn" id="claude-save-key">Salvar chave</button>
          ${claude.has_custom_key ? `<button type="button" class="btn ghost" id="claude-remove-key" title="Desativar chave e voltar ao login de terminal">Remover</button>` : ""}
        </div>
      </div>

      ${caixaModeloPadrao("claude")}

      <div class="account-actions-row">
        <div class="account-actions-left">
          <button type="button" class="btn primary" id="claude-btn-login" title="Login no navegador para conectar ou trocar de conta">${claude.status === 'connected' ? '🔑 Trocar conta' : '🔑 Conectar conta (Login no Navegador)'}</button>
          ${claude.status === 'connected' ? `<button type="button" class="btn ghost" id="claude-btn-logout">🚪 Desconectar</button>` : ''}
          <button type="button" class="btn" id="claude-btn-refresh">↻ Recarregar</button>
        </div>
        <div class="account-actions-right">
          <a href="#/consumo" class="btn ghost btn-consumo-link">📊 Ver consumo do Claude</a>
        </div>
      </div>
    </div>

    <!-- Card Codex -->
    <div class="card account-card">
      <div class="account-card-header">
        <div class="account-card-title">
          <span class="badge codex">Codex</span>
          <div>
            <b>${esc(codex.titulo || "Codex app-server")}</b>
            <div class="small muted">OpenAI / ChatGPT</div>
          </div>
        </div>
        <div>${statusBadge(codex.status)}</div>
      </div>

      <div class="account-details-grid">
        ${codex.email ? `
        <div class="account-kv-item highlight">
          <span class="k">Conta Ativa:</span>
          <span class="v"><b>${esc(codex.user_name || "")}</b> <span class="muted">(${esc(codex.email)})</span></span>
        </div>` : ""}
        <div class="account-kv-item">
          <span class="k">Modo de Autenticação:</span>
          <span class="v">${codex.has_custom_key ? "🔑 Chave de API OpenAI" : (codex.auth_mode === "chatgpt" ? "🌐 ChatGPT (Assinatura / OAuth)" : esc(codex.auth_mode || "Não detectado"))}</span>
        </div>
        <div class="account-kv-item">
          <span class="k">Executável:</span>
          <span class="v">${codex.available ? `<code class="small">${esc(codex.path || "codex.exe")}</code>` : `<span class="err-inline">codex.exe não encontrado</span>`}</span>
        </div>
        ${codex.default_model ? `
        <div class="account-kv-item">
          <span class="k" title="Definido no config.toml do Codex; o hub usa o escolhido em &quot;Modelo padrão dos chats novos&quot;, abaixo">Padrão do Codex:</span>
          <span class="v"><code>${esc(codex.default_model)}</code></span>
        </div>` : ""}
        <div class="account-kv-item">
          <span class="k">Sandbox do Windows:</span>
          <span class="v" id="st-sbx-status">verificando…</span>
        </div>
        <div class="account-kv-item">
          <span class="k">Pasta de configuração:</span>
          <span class="v"><code class="small">${esc(codex.config_path || "~/.codex")}</code></span>
        </div>
      </div>

      ${renderCodexProfiles()}

      <!-- Chave API Customizada -->
      <div class="account-key-box">
        <div class="row align-center gap">
          <label style="margin:0;font-size:13px;font-weight:600">Usar Chave de API OpenAI:</label>
          <span class="small muted" style="margin-left:auto">${codex.has_custom_key ? `Ativa: <code>${esc(codex.masked_key)}</code>` : "Opcional"}</span>
        </div>
        <div class="row gap" style="margin-top:6px">
          <input type="password" id="codex-api-key" class="input flex-1" placeholder="${codex.has_custom_key ? 'Substituir chave...' : 'sk-proj-... (salvar chave de API)'}" autocomplete="off">
          <input type="text" id="codex-key-label" class="input" style="width:140px" placeholder="Rótulo (opcional)" autocomplete="off">
          <button type="button" class="btn" id="codex-save-key">Salvar chave</button>
          ${codex.has_custom_key ? `<button type="button" class="btn ghost" id="codex-remove-key" title="Desativar chave e voltar ao login ChatGPT">Remover</button>` : ""}
        </div>
      </div>

      <div class="card-inner-section">
        <div class="small muted"><b>Sandbox do Windows (Codex):</b> Necessário para rodar comandos nos modos somente leitura ou pasta do chat.</div>
        <div class="row gap" style="margin-top:6px">
          <button type="button" class="btn" id="st-sbx-unelev">Configurar sandbox (sem admin)</button>
          <button type="button" class="btn" id="st-sbx-elev">Configurar sandbox (elevado)</button>
        </div>
      </div>

      ${caixaModeloPadrao("codex")}

      <div class="account-actions-row">
        <div class="account-actions-left">
          <button type="button" class="btn primary" id="codex-btn-login" title="Login no navegador para conectar ou trocar de conta">${codex.status === 'connected' ? '🔑 Trocar conta' : '🔑 Conectar conta (Login no Navegador)'}</button>
          ${codex.status === 'connected' ? `<button type="button" class="btn ghost" id="codex-btn-logout">🚪 Desconectar</button>` : ''}
          <button type="button" class="btn" id="codex-btn-refresh">↻ Recarregar</button>
        </div>
        <div class="account-actions-right">
          <a href="#/consumo" class="btn ghost btn-consumo-link">📊 Ver consumo do Codex</a>
        </div>
      </div>
    </div>
  `;

  // Modelo padrão dos chats novos de cada IA
  $$(".sel-modelo-padrao").forEach((s) => (s.onchange = async () => {
    try {
      const r = await api("POST", "/api/accounts/default-model", { provider: s.dataset.p, model: s.value });
      if (S.meta) S.meta.modelosPadrao = r.modelosPadrao;
      toast(`Chats novos do ${NOME_IA[s.dataset.p]} vão abrir em: ${s.selectedOptions[0].textContent}`);
    } catch (e) { toast(e.message); }
  }));

  // Função auxiliar de monitoramento após iniciar login
  const monitorarLogin = (provider, nomeProvedor) => {
    toast(`Iniciamos o login do ${nomeProvedor}. Conclua a autorização no navegador aberto...`, 15000);
    let tentativas = 0;
    const interval = setInterval(async () => {
      tentativas++;
      try {
        const check = await api("GET", "/api/accounts");
        const acc = check.accounts.find((a) => a.id === provider);
        if (acc && acc.status === "connected") {
          clearInterval(interval);
          toast(`✅ ${nomeProvedor} conectado com sucesso!`, 6000);
          renderContas();
        }
      } catch { /* aguardando */ }
      if (tentativas > 45) clearInterval(interval);
    }, 2500);
  };

  // Botões de Adicionar IA (+ Adicionar IA)
  if ($("#btn-open-add-ai")) $("#btn-open-add-ai").onclick = () => openAddAiModal();
  if ($("#btn-onboarding-add-ai")) $("#btn-onboarding-add-ai").onclick = () => openAddAiModal();
  if ($("#modal-add-ai-close")) $("#modal-add-ai-close").onclick = () => closeAddAiModal();
  const modalBackdrop = $("#modal-add-ai");
  if (modalBackdrop) {
    modalBackdrop.onclick = (e) => { if (e.target === modalBackdrop) closeAddAiModal(); };
  }

  // Ações dentro do modal do Catálogo de IAs
  $$(".btn-cat-connect").forEach((btn) => {
    btn.onclick = async () => {
      const p = btn.dataset.p;
      closeAddAiModal();
      if (p === "antigravity") {
        try {
          await api("POST", "/api/accounts/login", { provider: "antigravity" });
          toast("Google Antigravity aberto.");
        } catch (e) { toast(e.message); }
      } else if (p === "claude") {
        try {
          await api("POST", "/api/accounts/login", { provider: "claude" });
          monitorarLogin("claude", "Claude");
        } catch (e) { toast(e.message); }
      } else if (p === "codex") {
        try {
          await api("POST", "/api/accounts/login", { provider: "codex" });
          monitorarLogin("codex", "Codex");
        } catch (e) { toast(e.message); }
      }
    };
  });

  $$(".btn-cat-key").forEach((btn) => {
    btn.onclick = async () => {
      const p = btn.dataset.p;
      const promptTitle = p === "antigravity"
        ? "Digite a chave do Google Gemini (GEMINI_API_KEY):"
        : (p === "claude" ? "Digite a chave da Anthropic (sk-ant-...):" : "Digite a chave da OpenAI (sk-proj-...):");
      const key = prompt(promptTitle);
      if (key && key.trim()) {
        try {
          await api("POST", "/api/accounts/key", { provider: p, key: key.trim() });
          closeAddAiModal();
          toast("Chave de API salva com sucesso!");
          await renderContas();
        } catch (e) { toast(e.message); }
      }
    };
  });

  // Antigravity Binds
  if ($("#ag-btn-login")) {
    $("#ag-btn-login").onclick = async () => {
      try {
        await api("POST", "/api/accounts/login", { provider: "antigravity" });
        toast("Google Antigravity iniciado.");
      } catch (e) { toast(e.message); }
    };
  }
  if ($("#ag-btn-logout")) {
    $("#ag-btn-logout").onclick = async () => {
      if (!confirm("Deseja desconectar do Antigravity?")) return;
      try {
        await api("POST", "/api/accounts/logout", { provider: "antigravity" });
        toast("Desconectado do Antigravity.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  }
  if ($("#ag-btn-refresh")) {
    $("#ag-btn-refresh").onclick = async () => {
      toast("Atualizando dados do Antigravity…");
      await renderContas();
    };
  }
  if ($("#ag-save-key")) {
    $("#ag-save-key").onclick = async () => {
      const val = $("#ag-api-key").value.trim();
      const lbl = $("#ag-key-label").value.trim() || null;
      if (!val) { toast("Digite uma chave válida ou deixe em branco."); return; }
      try {
        await api("POST", "/api/accounts/key", { provider: "antigravity", key: val, label: lbl });
        toast("Chave GEMINI_API_KEY salva e ativada!");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  }
  if ($("#ag-remove-key")) {
    $("#ag-remove-key").onclick = async () => {
      try {
        await api("POST", "/api/accounts/key", { provider: "antigravity", key: null });
        toast("Chave GEMINI_API_KEY removida.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  }
  $$(".btn-ag-switch").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      btn.disabled = true;
      try {
        await api("POST", "/api/accounts/switch-profile", { provider: "antigravity", profile_id: id });
        toast("Conta alterada com sucesso!");
        renderContas();
      } catch (e) { toast(e.message); btn.disabled = false; }
    };
  });
  $$(".btn-ag-del-prof").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      if (!confirm("Remover este perfil salvo?")) return;
      try {
        await api("DELETE", `/api/accounts/profiles/antigravity/${encodeURIComponent(id)}`);
        toast("Perfil removido.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  });

  // Login / Logout Claude
  $("#claude-btn-login").onclick = async () => {
    try {
      await api("POST", "/api/accounts/login", { provider: "claude" });
      monitorarLogin("claude", "Claude");
    } catch (e) { toast(e.message); }
  };
  if ($("#claude-btn-logout")) {
    $("#claude-btn-logout").onclick = async () => {
      if (!confirm("Deseja desconectar a conta atual do Claude?")) return;
      try {
        await api("POST", "/api/accounts/logout", { provider: "claude" });
        toast("Conta do Claude desconectada.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  }

  // Login / Logout Codex
  $("#codex-btn-login").onclick = async () => {
    try {
      await api("POST", "/api/accounts/login", { provider: "codex" });
      monitorarLogin("codex", "Codex");
    } catch (e) { toast(e.message); }
  };
  if ($("#codex-btn-logout")) {
    $("#codex-btn-logout").onclick = async () => {
      if (!confirm("Deseja desconectar a conta atual do Codex?")) return;
      try {
        await api("POST", "/api/accounts/logout", { provider: "codex" });
        toast("Conta do Codex desconectada.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  }

  // Alternância e exclusão de perfis Codex
  $$(".btn-codex-switch").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      btn.disabled = true;
      try {
        await api("POST", "/api/accounts/switch-profile", { provider: "codex", profile_id: id });
        toast("Conta alterada com sucesso!");
        renderContas();
      } catch (e) { toast(e.message); btn.disabled = false; }
    };
  });
  $$(".btn-codex-del-prof").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      if (!confirm("Remover este perfil salvo?")) return;
      try {
        await api("DELETE", `/api/accounts/profiles/codex/${encodeURIComponent(id)}`);
        toast("Perfil removido.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  });

  // Alternância e exclusão de perfis Claude
  $$(".btn-claude-switch").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      btn.disabled = true;
      try {
        await api("POST", "/api/accounts/switch-profile", { provider: "claude", profile_id: id });
        toast("Chave ativada!");
        renderContas();
      } catch (e) { toast(e.message); btn.disabled = false; }
    };
  });
  $$(".btn-claude-del-prof").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      if (!confirm("Remover esta chave salva?")) return;
      try {
        await api("DELETE", `/api/accounts/profiles/claude/${encodeURIComponent(id)}`);
        toast("Chave removida.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  });

  // Binds de chaves Claude
  $("#claude-save-key").onclick = async () => {
    const val = $("#claude-api-key").value.trim();
    const lbl = $("#claude-key-label").value.trim() || null;
    if (!val) { toast("Digite uma chave válida ou deixe em branco."); return; }
    try {
      await api("POST", "/api/accounts/key", { provider: "claude", key: val, label: lbl });
      toast("Chave do Claude salva e ativada!");
      renderContas();
    } catch (e) { toast(e.message); }
  };
  if ($("#claude-remove-key")) {
    $("#claude-remove-key").onclick = async () => {
      try {
        await api("POST", "/api/accounts/key", { provider: "claude", key: null });
        toast("Chave removida. O Claude voltará ao login local.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  }
  $("#claude-btn-refresh").onclick = async () => {
    await api("POST", "/api/meta/refresh");
    toast("Consultando o Claude Code… (alguns segundos)");
    setTimeout(async () => { await loadMeta(); renderContas(); }, 6000);
  };
  if ($("#codex-btn-refresh")) {
    $("#codex-btn-refresh").onclick = async () => {
      toast("Atualizando dados do Codex…");
      await loadAccounts();
      renderContas();
    };
  }

  // Binds de chaves Codex
  $("#codex-save-key").onclick = async () => {
    const val = $("#codex-api-key").value.trim();
    const lbl = $("#codex-key-label").value.trim() || null;
    if (!val) { toast("Digite uma chave válida ou deixe em branco."); return; }
    try {
      await api("POST", "/api/accounts/key", { provider: "codex", key: val, label: lbl });
      toast("Chave do Codex salva e ativada!");
      renderContas();
    } catch (e) { toast(e.message); }
  };
  if ($("#codex-remove-key")) {
    $("#codex-remove-key").onclick = async () => {
      try {
        await api("POST", "/api/accounts/key", { provider: "codex", key: null });
        toast("Chave removida. O Codex voltará ao login do ChatGPT.");
        renderContas();
      } catch (e) { toast(e.message); }
    };
  }

  const setupSandbox = async (mode) => {
    try { await api("POST", "/api/codex/sandbox/setup", { mode }); toast("Configuração do sandbox iniciada…"); }
    catch (e) { toast(e.message); }
  };
  $("#st-sbx-unelev").onclick = () => setupSandbox("unelevated");
  $("#st-sbx-elev").onclick = () => setupSandbox("elevated");

  if (codex.available) {
    const r = await api("GET", "/api/codex/sandbox").catch((e) => ({ status: "error", error: e.message }));
    const label = { ready: "✅ pronto", notConfigured: "⚠️ não configurado", error: "erro: " + (r.error || "") }[r.status] || r.status;
    const el = $("#st-sbx-status");
    if (el) el.textContent = label;
  }
}

async function renderMcpSection() {
  const container = $("#sec-mcp");
  if (!container) return;
  container.innerHTML = `
    <div class="card">
      <h3>Servidores MCP (Model Context Protocol)</h3>
      <div class="hint" style="margin-top:2px">
        Os mesmos servidores que o Claude Code e o Codex usam no VS Code. MCPs que rodam de dentro de uma extensão do VS Code
        (como o Power BI) têm a versão da extensão no caminho: quando ela atualiza, aparecem aqui como desatualizados ou quebrados, com o botão <b>Corrigir</b>. A correção faz backup do arquivo antes.
      </div>
      <div id="mcp-card-body"><div class="muted small" style="margin-top:12px">Verificando servidores MCP…</div></div>
      <div class="row gap" style="margin-top:14px">
        <button class="btn" id="st-mcp-recheck">↻ Verificar novamente</button>
      </div>
    </div>
  `;
  $("#st-mcp-recheck").onclick = async () => {
    await loadMeta();
    updateMcpAlert();
    renderMcpCard();
  };
  renderMcpCard();
}

async function renderGeralSection() {
  const container = $("#sec-geral");
  if (!container) return;
  container.innerHTML = `
    <div class="card">
      <h3>Agent Hub</h3>
      <div>Servidor ligado <b id="st-inicio">…</b></div>
      <div class="hint">Depois de uma atualização do Agent Hub, reinicie por aqui: o servidor liga de novo já com as mudanças e esta tela recarrega sozinha, sem precisar fechar a janela. Conversas que estiverem trabalhando são interrompidas (você é avisado antes).</div>
      <div class="row gap" style="margin-top:12px"><button class="btn" id="st-reiniciar">↻ Reiniciar o Agent Hub</button></div>
    </div>
    <div class="card">
      <h3>Aparência</h3>
      <label>Tema da interface</label>
      <div class="seg" id="st-theme"><button data-t="">Sistema</button><button data-t="light">Claro</button><button data-t="dark">Escuro</button></div>
      <label style="margin-top:14px">Cor de destaque (Estilo AeroMonitor Glass)</label>
      <div class="theme-accent-row" id="st-accent">
        <button type="button" class="accent-btn" data-a="orange" title="Coral Âmbar (AeroMonitor)"><span style="background:#e88662"></span> Laranja</button>
        <button type="button" class="accent-btn" data-a="blue" title="Azul Celeste"><span style="background:#38bdf8"></span> Azul</button>
        <button type="button" class="accent-btn" data-a="green" title="Verde Esmeralda"><span style="background:#22c55e"></span> Verde</button>
        <button type="button" class="accent-btn" data-a="purple" title="Roxo Neon"><span style="background:#c084fc"></span> Roxo</button>
        <button type="button" class="accent-btn" data-a="yellow" title="Amarelo Ouro"><span style="background:#facc15"></span> Amarelo</button>
      </div>
      <label style="margin-top:14px">Fonte das respostas</label>
      <div class="seg" id="st-fonte"><button data-f="">Estilo Claude (serifada)</button><button data-f="sans">Sem serifa</button></div>
    </div>
  `;
  api("GET", "/api/servidor").then((i) => {
    const el = $("#st-inicio");
    if (el) el.textContent = fmtQuando(i.inicio);
  }).catch(() => {});
  $("#st-reiniciar").onclick = reiniciarServidor;
  const theme = localStorageGet("theme") || "";
  $$("#st-theme button").forEach((b) => {
    b.classList.toggle("on", b.dataset.t === theme);
    b.onclick = () => { setTheme(b.dataset.t); renderGeralSection(); };
  });
  const accent = localStorageGet("accent") || "orange";
  $$("#st-accent .accent-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.a === accent);
    b.onclick = () => { setAccent(b.dataset.a); renderGeralSection(); };
  });
  const fonte = localStorageGet("fonte") || "";
  $$("#st-fonte button").forEach((b) => {
    b.classList.toggle("on", b.dataset.f === fonte);
    b.onclick = () => { setFonte(b.dataset.f); renderGeralSection(); };
  });
}

function switchSettingsSection(sec, subArg = null) {
  S.settingsSection = sec || "geral";
  $$("#settings-tabs button").forEach((b) => {
    b.classList.toggle("on", b.dataset.sec === S.settingsSection);
  });
  for (const s of ["geral", "contas", "agentes", "mcp"]) {
    const el = $(`#sec-${s}`);
    if (el) el.classList.toggle("hidden", s !== S.settingsSection);
  }
  if (S.settingsSection === "geral") renderGeralSection();
  else if (S.settingsSection === "contas") renderContas();
  else if (S.settingsSection === "agentes") {
    renderAgentList();
    if (subArg === "new") editAgent(null);
    else if (subArg) editAgent(subArg);
    else if (S.editingAgentId) editAgent(S.editingAgentId);
    else showAgentPlaceholder();
  }
  else if (S.settingsSection === "mcp") renderMcpSection();
}

function renderSettings(sec = null, subArg = null) {
  switchSettingsSection(sec || S.settingsSection || "geral", subArg);
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
function setAccent(a, aplicarIcone = true) {
  a = a || "orange";
  localStorageSet("accent", a);
  document.documentElement.dataset.accent = a;
  // ícone da janela (favicon) e, no Windows, dos atalhos do hub e da barra de tarefas na mesma cor
  if (!aplicarIcone) return;  // sem cor escolhida neste navegador: o favicon fica com a cor guardada no servidor
  for (const [id, tipo] of [["favicon-32", "png32"], ["favicon-256", "png256"]]) {
    const link = document.getElementById(id);
    if (link) link.href = `/api/icone/${tipo}?cor=${a}`;
  }
  api("POST", "/api/aparencia/icone", { cor: a }).catch(() => { /* ícone é só visual */ });
}

/* ------------------------------------------------------------------ tempo real */
/* Servidor desligado (console fechado, PC reiniciado…): faixa no topo em vez de "Failed to fetch".
   Os chats ficam salvos no disco; quando o servidor volta, a tela recarrega os dados sozinha. */
function mostrarOffline(on) {
  if (on && S.reiniciando) return;  // reinício pedido pelo usuário: a tela recarrega sozinha
  let faixa = $("#offline");
  if (!faixa) {
    faixa = h("div", { id: "offline", class: "offline hidden" }, `<span class="live-spin"></span><span>${esc(MSG_OFFLINE)}</span>`);
    document.body.append(faixa);
  }
  faixa.classList.toggle("hidden", !on);
  if (on && !S.offlineTimer) S.offlineTimer = setInterval(verificarServidor, 2000);
}
/* Pedido leve (o ícone, sem cache) só para saber se o servidor responde. */
const servidorNoAr = () => fetch("/static/icone-32.png", { cache: "no-store" }).then((r) => r.ok, () => false);
async function verificarServidor() {
  if (!(await servidorNoAr())) return;
  clearInterval(S.offlineTimer); S.offlineTimer = null;
  mostrarOffline(false);
  try { await loadMeta(); await Promise.all([loadChats(), loadAgents()]); route(); toast("Reconectado ao servidor."); }
  catch { /* se cair de novo, a próxima ação mostra a faixa outra vez */ }
}
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
        if (m.status === "idle") { clearTimeout(S.usoTimer); S.usoTimer = setTimeout(atualizarAlertaConsumo, 3000); }  // limites mudam a cada resposta
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
        delete S.rascunhos[m.chat_id];
        if (S.chatId === m.chat_id) { S.chatId = null; location.hash = "#/"; }
        renderChatList();
        break;
      case "codex_sandbox":
        toast(m.result.success ? `Sandbox do Codex configurado (${m.result.mode}).` : `Falha ao configurar sandbox: ${m.result.error || ""}`, 7000);
        if (S.view === "settings" && S.settingsSection === "contas") renderContas();
        break;
    }
  };
  ws.onclose = () => { servidorNoAr().then((ok) => ok || mostrarOffline(true)); setTimeout(connectWs, 1500); };
}
function notify(text) {
  if (document.hasFocus()) { toast(text); return; }
  try { if (Notification.permission === "granted") new Notification("Agent Hub", { body: text }); } catch { /* sem notificação */ }
}

/* ------------------------------------------------------------------ carga e rotas */
async function loadAccounts() {
  try {
    const res = await api("GET", "/api/accounts");
    S.accounts = res.accounts || [];
  } catch {
    S.accounts = [];
  }
}
async function loadMeta() {
  S.meta = await api("GET", "/api/meta");
  await loadAccounts();
}
async function loadChats() { S.chats = await api("GET", "/api/chats"); renderChatList(); }
async function loadAgents() { S.agents = await api("GET", "/api/agents"); if (S.view === "settings" && S.settingsSection === "agentes") renderAgentList(); renderChatList(); renderSideAgents(); }

/* ------------------------------------------------------------------ consumo */
/* Aba "Consumo": limites do plano (reais) + de onde vem o uso (aproximado, pelos históricos deste computador). */
S.consumoPeriodo = localStorageGet("consumoPeriodo") || "dia";
const fmtNum = (n, casas = 0) => Number(n || 0).toLocaleString("pt-BR", { maximumFractionDigits: casas, minimumFractionDigits: casas });
const fmtPct = (p) => `${fmtNum(p, p > 0 && p < 1 ? 1 : 0)}%`;
function fmtTokens(n) {
  n = n || 0;
  if (n >= 1e9) return `${fmtNum(n / 1e9, 1)} bi`;
  if (n >= 1e6) return `${fmtNum(n / 1e6, 1)} mi`;
  if (n >= 1e3) return `${fmtNum(n / 1e3, 0)} mil`;
  return fmtNum(n);
}
function fmtQuando(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), hoje = new Date();
  const hora = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === hoje.toDateString()) return `hoje às ${hora}`;
  const amanha = new Date(hoje); amanha.setDate(hoje.getDate() + 1);
  if (d.toDateString() === amanha.toDateString()) return `amanhã às ${hora}`;
  return `${d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" })} às ${hora}`;
}
function fmtFalta(ts) {
  const s = ts - Date.now() / 1000;
  if (s <= 0) return "agora";
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h >= 24 ? `${Math.floor(h / 24)} d ${h % 24} h` : h ? `${h} h ${m} min` : `${m} min`;
}
const fmtHaQuanto = (ts) => { const m = Math.round((Date.now() / 1000 - ts) / 60); return m < 1 ? "agora há pouco" : m < 60 ? `há ${m} min` : `há ${Math.round(m / 60)} h`; };
const corModelo = (nome) => /opus/i.test(nome) ? "var(--claude)" : /sonnet/i.test(nome) ? "#6b8fd6" : /haiku/i.test(nome) ? "#5aa37a" : /fable|mythos/i.test(nome) ? "#a77bd6" : "var(--muted)";

function barraLimite(j) {
  const nivel = j.pct >= 90 ? "alto" : j.pct >= 75 ? "medio" : "";
  return `<div class="lim">
    <div class="lim-top"><b>${esc(j.nome)}</b><span>${fmtPct(j.pct)} usado</span></div>
    <div class="lim-bar ${nivel}"><span style="width:${Math.min(100, j.pct || 0)}%"></span></div>
    ${j.reset ? `<div class="hint">Reinicia em ${fmtFalta(j.reset)} (${fmtQuando(j.reset)})</div>` : ""}
  </div>`;
}
function cardLimites(d) {
  const lc = d.limites_claude, lx = d.codex?.limites, lag = d.limites_antigravity;
  const extra = lc?.extra === "rejected"
    ? `<div class="hint">Uso extra (além do plano): indisponível${lc.extra_motivo === "out_of_credits" ? " — sem créditos" : ""}.</div>` : "";
  return `<div class="card" id="cs-limites"><h3>Limites e Uso das IAs</h3>
    <div class="lim-grid">
      <div><div class="lim-prov"><span class="badge antigravity">Google Antigravity</span>${lag?.status ? ` <span class="muted small">${esc(lag.status)}</span>` : ""}</div>
        <div class="lim-item">
          <div class="lim-h"><span>Uso hoje</span><span class="lim-val">${fmtTokens(lag?.tokens_hoje || 0)} tokens</span></div>
          <div class="lim-barra"><i style="width:${Math.min(100, Math.round((lag?.tokens_hoje || 0) / 1000))}%"></i></div>
        </div>
        <div class="lim-item">
          <div class="lim-h"><span>Uso na semana</span><span class="lim-val">${fmtTokens(lag?.tokens_semana || 0)} tokens</span></div>
          <div class="lim-barra"><i style="width:${Math.min(100, Math.round((lag?.tokens_semana || 0) / 5000))}%"></i></div>
        </div>
        <div class="hint">${fmtNum(lag?.requisicoes_hoje || 0)} requisições hoje · Gemini 3.8 Flash, Claude Sonnet 4.6 Thinking</div>
      </div>
      <div><div class="lim-prov"><span class="badge claude">Claude</span></div>
        ${lc?.janelas?.length ? lc.janelas.map(barraLimite).join("") + extra + `<div class="hint">Atualizado ${fmtHaQuanto(lc.visto_em)} · conta Anthropic</div>`
          : `<div class="hint">Ainda sem informação. Clique em "Atualizar agora".</div>`}
        <button class="btn" id="cs-lim-atualizar" style="margin-top:8px">↻ Atualizar agora</button>
      </div>
      <div><div class="lim-prov"><span class="badge codex">Codex</span>${lx?.plano ? ` <span class="muted small">plano ${esc(lx.plano)}</span>` : ""}</div>
        ${lx?.janelas?.length ? lx.janelas.map(barraLimite).join("") + `<div class="hint">Visto ${fmtHaQuanto(lx.visto_em)} (conversas do Codex)</div>`
          : `<div class="hint">Sem conversas recentes do Codex para ler o limite.</div>`}
      </div>
    </div></div>`;
}
function tabelaPct(titulo, linhas, extraCols = [], vazio = "") {
  if (!linhas.length) return vazio ? `<div class="cs-tab"><div class="cs-tab-h"><span>${esc(titulo)}</span></div><div class="hint">${esc(vazio)}</div></div>` : "";
  return `<div class="cs-tab">
    <div class="cs-tab-h"><span>${esc(titulo)}</span>${extraCols.map((c) => `<span class="cs-num">${esc(c.titulo)}</span>`).join("")}<span class="cs-num">% do uso</span></div>
    ${linhas.map((l) => `<div class="cs-tab-l">
      <span class="cs-nome" title="${esc(l.nome)}">${esc(l.rotulo || l.nome)}<i style="width:${Math.min(100, l.pct)}%"></i></span>
      ${extraCols.map((c) => `<span class="cs-num">${c.valor(l)}</span>`).join("")}
      <span class="cs-num">${fmtPct(l.pct)}</span></div>`).join("")}
  </div>`;
}
function linhaDoTempo(c) {
  const max = Math.max(0.0001, ...c.linha.map((b) => Object.values(b.modelos).reduce((a, v) => a + v, 0)));
  const modelos = [...new Set(c.linha.flatMap((b) => Object.keys(b.modelos)))];
  const rot = (t) => { const d = new Date(t * 1000); return c.passo === "hora" ? `${String(d.getHours()).padStart(2, "0")}h` : d.toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit" }); };
  return `<div class="card"><h3>Linha do tempo <span class="muted small">(${c.passo === "hora" ? "por hora, últimas 24 h" : "por dia, últimos 7 dias"})</span></h3>
    <div class="cs-linha">${c.linha.map((b) => {
      const tot = Object.values(b.modelos).reduce((a, v) => a + v, 0);
      const titulo = `${rot(b.t)}: ${tot ? fmtPct(100 * tot / (c.total_usd || 1)) + " do período" : "sem uso"}${Object.entries(b.modelos).map(([m, v]) => `\n${m}: US$ ${fmtNum(v, 2)}`).join("")}`;
      return `<div class="cs-col" title="${esc(titulo)}"><div class="cs-pilha" style="height:${(100 * tot / max).toFixed(1)}%">${
        Object.entries(b.modelos).map(([m, v]) => `<span style="flex:${v};background:${corModelo(m)}"></span>`).join("")}</div><small>${rot(b.t)}</small></div>`;
    }).join("")}</div>
    <div class="cs-legenda">${modelos.map((m) => `<span><i style="background:${corModelo(m)}"></i>${esc(m)}</span>`).join("")}</div>
  </div>`;
}
function cardSessoes(lista, codex = false) {
  if (!lista.length) return `<div class="hint">Nenhuma conversa no período.</div>`;
  return `<div class="cs-sessoes">${lista.map((s) => `<div class="cs-sessao">
      <div class="cs-s-top">
        ${s.chat_id ? `<a href="#/chat/${esc(s.chat_id)}">${esc(s.titulo)}</a>` : `<span>${esc(s.titulo)}</span>`}
        <span class="grow"></span><b>${fmtPct(s.pct)}</b>
      </div>
      <div class="cs-s-info">
        <span class="tag">${esc(s.origem)}</span>
        ${codex ? "" : `<span>${fmtNum(s.req)} requisições</span><span>pico de contexto ${fmtTokens(s.pico_ctx)}</span>
        ${s.subagentes ? `<span>${s.subagentes} subagente(s) · ${s.pct_sub}% da conversa</span>` : ""}
        ${s.modelos?.length ? `<span>${esc(s.modelos.join(", "))}</span>` : ""}
        ${s.ultimo ? `<span>último uso ${fmtQuando(s.ultimo)}</span>` : ""}`}
        ${s.pasta ? `<span class="cs-pasta" title="${esc(s.pasta)}">📁 ${esc(s.pasta)}</span>` : ""}
      </div></div>`).join("")}</div>`;
}

async function renderConsumo(forcarLimites = false) {
  const body = $("#consumo-body");
  $$("#consumo-periodo button").forEach((b) => {
    b.classList.toggle("on", b.dataset.p === S.consumoPeriodo);
    b.onclick = () => { S.consumoPeriodo = b.dataset.p; localStorageSet("consumoPeriodo", b.dataset.p); renderConsumo(); };
  });
  if (!body.innerHTML.trim()) body.innerHTML = `<div class="muted small">Lendo os históricos do Antigravity, Claude e Codex…</div>`;
  let d;
  try { d = await api("GET", `/api/consumo?periodo=${S.consumoPeriodo}`); }
  catch (e) { body.innerHTML = `<div class="err-box">${esc(e.message)}</div>`; return; }
  if (S.view !== "consumo") return;
  const c = d.claude, x = d.codex, ag = d.antigravity || {};
  const tokEntrada = (c.tokens.in || 0) + (c.tokens.cr || 0) + (c.tokens.cw5 || 0) + (c.tokens.cw1h || 0);
  const nomePeriodo = d.periodo === "dia" ? "Últimas 24 h" : "Últimos 7 dias";
  body.innerHTML = `<div class="cs-grid">
    ${cardLimites(d)}
    <div class="card"><h3>${nomePeriodo} · Claude</h3>
      <div class="cs-kpis">
        <div><b>${fmtNum(c.requisicoes)}</b><span>requisições ao modelo</span></div>
        <div><b>${fmtNum(c.sessoes)}</b><span>conversas</span></div>
        <div><b>${fmtTokens(tokEntrada)}</b><span>tokens lidos (contexto)</span></div>
        <div><b>${fmtTokens(c.tokens.out)}</b><span>tokens gerados</span></div>
        <div><b>${fmtPct(c.cache_pct)}</b><span>do contexto veio do cache</span></div>
        <div title="Estimativa do Claude Code em preço de API. No plano de assinatura não é cobrado, mas é o que consome o limite."><b>US$ ${fmtNum(c.total_usd, 2)}</b><span>equivalente em API</span></div>
      </div>
      <div class="hint">Cache: ${fmtTokens(c.tokens.cr)} lidos do cache (custam ~10%), ${fmtTokens((c.tokens.cw5 || 0) + (c.tokens.cw1h || 0))} gravados no cache, ${fmtTokens(c.tokens.in)} sem cache.</div>
    </div>
    <div class="card cs-insights"><h3>O que mais pesa no seu consumo?</h3>
      <div class="hint">São características independentes do seu uso, não uma divisão (a soma pode passar de 100%).</div>
      ${c.insights.length ? c.insights.map((i) => `<div class="cs-ins"><div><b>${fmtPct(i.pct)}</b> ${esc(i.titulo)}</div><div class="hint">${esc(i.dica)}</div></div>`).join("")
        : `<div class="hint" style="margin-top:8px">Sem uso do Claude no período.</div>`}
    </div>
    ${c.linha.length ? linhaDoTempo(c) : ""}
    <div class="card cs-tabs cs-largo">
      ${tabelaPct("Modelos", c.modelos, [{ titulo: "requisições", valor: (l) => fmtNum(l.req) }, { titulo: "tokens gerados", valor: (l) => fmtTokens(l.tokens.out) }])}
      ${tabelaPct("Origem", c.origens)}
      ${tabelaPct("Subagentes", c.subagentes, [], "Nenhum subagente no período.")}
      ${tabelaPct("Servidores MCP", c.mcps, [{ titulo: "chamadas", valor: (l) => fmtNum(l.chamadas) }, { titulo: "resultados", valor: (l) => fmtTokens(l.tokens_resultado) + " tok" }], "Nenhum MCP usado no período.")}
      ${tabelaPct("Skills", c.skills.map((s) => ({ ...s, rotulo: "/" + s.nome })), [], "Nenhuma skill usada no período.")}
      ${tabelaPct("Pastas", c.pastas.map((p) => ({ ...p, rotulo: p.nome.replace(/^C:\\Users\\[^\\]+/i, "~") })))}
    </div>
    <div class="card"><h3>Conversas que mais consumiram</h3>${cardSessoes(c.top_sessoes)}</div>
    <div class="cs-coluna">
      <div class="card"><h3>Ferramentas mais usadas</h3>
        <div class="cs-ferr">${c.ferramentas.map((f) => `<span class="tag" title="${esc(f.nome)}">${esc(f.nome)} <b>${fmtNum(f.chamadas)}</b></span>`).join("") || `<span class="hint">Nenhuma.</span>`}</div>
      </div>
      <div class="card"><h3>${nomePeriodo} · <span class="badge antigravity">Google Antigravity</span></h3>
        ${ag.sessoes ? `<div class="cs-kpis">
            <div><b>${fmtNum(ag.sessoes)}</b><span>conversas</span></div>
            <div><b>${fmtNum(ag.requisicoes || 0)}</b><span>requisições</span></div>
            <div><b>${fmtTokens(ag.tokens?.in || 0)}</b><span>tokens lidos</span></div>
            <div><b>${fmtTokens(ag.tokens?.thinking || 0)}</b><span>de raciocínio</span></div>
            <div><b>${fmtTokens(ag.tokens?.out || 0)}</b><span>tokens gerados</span></div>
            <div><b>${fmtTokens(ag.tokens?.total || 0)}</b><span>total de tokens</span></div>
          </div>
          <div class="cs-tabs">${tabelaPct("Modelos", ag.modelos || [])}</div>
          <h4 class="cs-sub">Conversas que mais consumiram</h4>${cardSessoes(ag.top_sessoes || [], true)}`
          : `<div class="hint">Sem uso do Antigravity registrado no período.</div>`}
      </div>
      <div class="card"><h3>${nomePeriodo} · <span class="badge codex">Codex</span></h3>
        ${x.sessoes ? `<div class="cs-kpis">
            <div><b>${fmtNum(x.sessoes)}</b><span>conversas</span></div>
            <div><b>${fmtTokens(x.tokens.in)}</b><span>tokens lidos</span></div>
            <div><b>${fmtPct(x.tokens.in ? 100 * x.tokens.cached / x.tokens.in : 0)}</b><span>do contexto veio do cache</span></div>
            <div><b>${fmtTokens(x.tokens.out)}</b><span>tokens gerados</span></div>
            <div><b>${fmtTokens(x.tokens.reasoning)}</b><span>de raciocínio</span></div>
          </div>
          <div class="cs-tabs">${tabelaPct("Modelos", x.modelos)}</div>
          <h4 class="cs-sub">Conversas que mais consumiram</h4>${cardSessoes(x.top_sessoes, true)}`
          : `<div class="hint">Sem uso do Codex no período.</div>`}
      </div>
    </div>
    </div>
    <div class="hint cs-rodape">Aproximado, com base nas conversas deste computador (VS Code e Agent Hub). Não inclui outros dispositivos nem o claude.ai.
      "% do uso" é a participação no custo equivalente em preço de API, que é o que consome o limite.
      Os limites do plano, por outro lado, são os números oficiais da sua conta. Calculado em ${fmtNum(d.duracao_s, 1)} s.</div>`;
  $("#cs-lim-atualizar").onclick = () => atualizarLimites(true);
  const lc = d.limites_claude;
  if (forcarLimites || !lc || Date.now() / 1000 - lc.visto_em > 15 * 60) atualizarLimites(false);
}
async function atualizarLimites(avisar) {
  const btn = $("#cs-lim-atualizar");
  if (btn) { btn.disabled = true; btn.textContent = "Consultando…"; }
  try {
    await api("POST", "/api/consumo/limites");
    if (S.view === "consumo") await renderConsumo();
    atualizarAlertaConsumo();
  } catch (e) { if (avisar) toast(e.message); if (btn) { btn.disabled = false; btn.textContent = "↻ Atualizar agora"; } }
}
/* Selos no botão "Consumo": quanto do limite do plano já foi USADO no Claude e no Codex (o maior entre as
   janelas de cada um, ex.: sessão de 5 h ou semana). A cor muda a partir de 75% e de 90%. */
function seloUso(nome, lim) {
  if (!lim) return "";
  const janelas = lim?.janelas || [];
  if (janelas.length) {
    const pior = janelas.reduce((a, j) => ((j.pct || 0) > (a.pct || 0) ? j : a), janelas[0]);
    const pct = Math.round(pior.pct || 0);
    const nivel = pct >= 90 ? "alto" : pct >= 75 ? "medio" : "";
    const titulo = `${nome}: ` + janelas.map((j) => `${j.nome} ${Math.round(j.pct || 0)}% usado${j.reset ? ` (reinicia ${fmtQuando(j.reset)})` : ""}`).join(" · ");
    return `<span class="uso-selo ${nivel}" title="${esc(titulo)}">${nome} ${pct}%</span>`;
  }
  if (lim.tokens_hoje !== undefined && lim.tokens_hoje > 0) {
    const tok = fmtTokens(lim.tokens_hoje);
    const tit = `${nome}: ${fmtNum(lim.requisicoes_hoje || 0)} requisições hoje (${fmtTokens(lim.tokens_hoje)} tokens)`;
    return `<span class="uso-selo" title="${esc(tit)}">${nome} ${tok}</span>`;
  }
  return "";
}
async function atualizarAlertaConsumo() {
  try {
    const r = await api("GET", "/api/consumo/limites");
    const selos = [seloUso("Claude", r.limites), seloUso("Codex", r.codex), seloUso("Antigravity", r.antigravity)].filter(Boolean);
    // balão que aparece ao passar o mouse no botão; o ponto colorido no botão avisa cota alta mesmo fechado
    $("#consumo-alert").innerHTML = selos.length ? `<span class="uso-tit">Uso das cotas</span>${selos.join("")}` : "";
    const btn = $("#btn-consumo");
    btn.classList.toggle("uso-alto", selos.some((s) => s.includes("uso-selo alto")));
    btn.classList.toggle("uso-medio", !btn.classList.contains("uso-alto") && selos.some((s) => s.includes("uso-selo medio")));
  } catch { /* sem dados ainda */ }
}

function route() {
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/chat\/(\w+)/);
  if (m) return openChat(m[1]);
  const ag = hash.match(/^#\/agents(?:\/(\w+))?/);
  if (ag) {
    show("settings");
    switchSettingsSection("agentes", ag[1] || null);
    return;
  }
  const st = hash.match(/^#\/settings(?:\/(\w+)(?:\/(\w+))?)?/);
  if (st) {
    show("settings");
    const sec = st[1] || "geral";
    const sub = st[2] || null;
    switchSettingsSection(sec, sub);
    return;
  }
  if (hash === "#/consumo") { show("consumo"); renderConsumo(); return; }
  if (hash === "#/import") { show("import"); renderImport(); return; }
  const nv = hash.match(/^#\/novo(?:\/(\w+))?(?:\?p=(claude|codex))?/);
  if (nv) return openStart(nv[1] || null, nv[2] || null);
  openStart(null, null);  // tela inicial = novo chat (como no Claude Desktop)
}

function bind() {
  $("#btn-new-chat").onclick = () => { if (location.hash === "#/novo") openStart(null, null); else location.hash = "#/novo"; };
  $("#agents-toggle").onclick = () => { S.agentsCollapsed = !S.agentsCollapsed; renderSideAgents(); };
  $("#agents-add").onclick = (e) => { e.stopPropagation(); location.hash = "#/settings/agentes/new"; };
  const alternarArquivados = () => { S.archivedCollapsed = !S.archivedCollapsed; renderChatList(); };
  $("#archived-toggle").onclick = alternarArquivados;
  $("#archived-toggle").addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); alternarArquivados(); } });
  $("#side-archived-list").addEventListener("scroll", closeChatMenu);
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
  $("#start-edit-agent").onclick = () => S.start?.agentId && (location.hash = `#/settings/agentes/${S.start.agentId}`);
  if ($("#btn-agents")) $("#btn-agents").onclick = () => (location.hash = "#/settings/agentes");
  $$("#settings-tabs button").forEach((b) => (b.onclick = () => {
    location.hash = `#/settings/${b.dataset.sec}`;
  }));
  $("#btn-import").onclick = () => (location.hash = "#/import");
  $$("#import-provider button").forEach((b) => (b.onclick = () => {
    S.importProvider = b.dataset.p;
    $$("#import-provider button").forEach((x) => x.classList.toggle("on", x === b));
    renderImport();
  }));
  $("#import-search").oninput = () => renderImportList();
  $("#btn-settings").onclick = () => (location.hash = "#/settings");
  $("#btn-consumo").onclick = () => (location.hash = "#/consumo");
  $("#btn-new-agent").onclick = () => editAgent(null);
  $("#chat-search").oninput = () => { renderChatList(); agendarBuscaConteudo(); };
  $("#search-content").onclick = alternarBuscaConteudo;
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
  // só reaplica o ícone se este navegador já tem uma cor escolhida (um navegador "zerado" não volta tudo ao laranja)
  setAccent(localStorageGet("accent") || "orange", !!localStorageGet("accent"));
  setTheme(localStorageGet("theme") || "");
  setFonte(localStorageGet("fonte") || "");
  bind();
  try { await loadMeta(); }
  catch (e) {  // servidor ainda desligado: espera ele subir e recarrega a página
    if (e.message !== MSG_OFFLINE) toast("Falha ao carregar: " + e.message);
    const tentar = () => fetch("/api/meta").then((r) => (r.ok ? location.reload() : setTimeout(tentar, 2000)), () => setTimeout(tentar, 2000));
    setTimeout(tentar, 2000);
    return;
  }
  await Promise.all([loadChats(), loadAgents()]);
  connectWs();
  // A queda do servidor nem sempre fecha o WebSocket na hora: confere de tempos em tempos.
  setInterval(async () => { if (!S.offlineTimer && !document.hidden && !(await servidorNoAr())) mostrarOffline(true); }, 5000);
  route();
  updateMcpAlert();
  atualizarAlertaConsumo();
  setInterval(atualizarAlertaConsumo, 5 * 60 * 1000);
  if (S.meta.mcpIssues) toast(`⚠ ${S.meta.mcpIssues} servidor(es) MCP com problema. Veja em Ambiente.`, 8000);
  try { if (Notification.permission === "default") Notification.requestPermission(); } catch { /* ok */ }
  // o Claude pode demorar alguns segundos para informar modelos/comandos na primeira vez
  if (!S.meta.claude.version) setTimeout(loadMeta, 10000);
})();
