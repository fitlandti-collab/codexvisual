const API = "";

let currentSessionId = null;
let currentThreadId = null;
let sending = false;

const el = (id) => document.getElementById(id);

const statusPill = el("status-pill");
const statusLabel = el("status-label");
const configToggle = el("config-toggle");
const configDrawer = el("config-drawer");
const configClose = el("config-close");
const sessionList = el("session-list");
const newThreadBtn = el("new-thread-btn");
const transcript = el("transcript");
const currentThreadLabel = el("current-thread");
const chatForm = el("chat-form");
const chatInput = el("chat-input");
const sendBtn = el("send-btn");
const errorBanner = el("error-banner");

const cfgExecFlags = el("cfg-exec-flags");
const cfgTimeout = el("cfg-timeout");
const cfgWorkspace = el("cfg-workspace");
const cfgBin = el("cfg-bin");
const cfgSave = el("cfg-save");
const cfgReset = el("cfg-reset");
const configSavedMsg = el("config-saved-msg");

function showError(msg) {
  errorBanner.textContent = msg;
  errorBanner.classList.remove("hidden");
}
function clearError() {
  errorBanner.classList.add("hidden");
  errorBanner.textContent = "";
}

// ---------- STATUS ----------
async function refreshHealth() {
  try {
    const res = await fetch(`${API}/health`);
    const data = await res.json();
    const loggedIn = !!data.login?.logged_in;
    const installed = !!data.codex?.codex_installed;

    statusPill.classList.remove("pill-ok", "pill-bad", "pill-unknown");
    if (installed && loggedIn) {
      statusPill.classList.add("pill-ok");
      statusLabel.textContent = "codex autenticado";
    } else if (installed && !loggedIn) {
      statusPill.classList.add("pill-bad");
      statusLabel.textContent = "sem login — rode 'codex login'";
    } else {
      statusPill.classList.add("pill-bad");
      statusLabel.textContent = "codex indisponível";
    }
  } catch (e) {
    statusPill.classList.remove("pill-ok", "pill-bad", "pill-unknown");
    statusPill.classList.add("pill-bad");
    statusLabel.textContent = "api offline";
  }
}

// ---------- SESSIONS / LEDGER ----------
function shortId(id) {
  if (!id) return "————————";
  return id.slice(0, 8);
}

async function refreshSessionList() {
  try {
    const res = await fetch(`${API}/sessions`);
    const sessions = await res.json();

    sessionList.innerHTML = "";
    if (sessions.length === 0) {
      sessionList.innerHTML = `<div class="empty-hint">nenhuma thread ainda</div>`;
      return;
    }

    for (const s of sessions) {
      const row = document.createElement("div");
      row.className = "session-row " + (s.thread_id ? "has-thread" : "pending");
      if (s.session_id === currentSessionId) row.classList.add("active");

      row.innerHTML = `
        <span class="session-dot"></span>
        <div class="session-meta">
          <span class="session-id mono">${shortId(s.thread_id || s.session_id)}</span>
          <span class="session-title">${escapeHtml(s.title)}</span>
        </div>
        <button class="session-delete" title="apagar">×</button>
      `;

      row.addEventListener("click", (ev) => {
        if (ev.target.closest(".session-delete")) return;
        selectSession(s.session_id);
      });

      row.querySelector(".session-delete").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        if (!confirm("Apagar esta thread?")) return;
        await fetch(`${API}/sessions/${s.session_id}`, { method: "DELETE" });
        if (currentSessionId === s.session_id) {
          currentSessionId = null;
          currentThreadId = null;
          renderEmptyTranscript();
        }
        refreshSessionList();
      });

      sessionList.appendChild(row);
    }
  } catch (e) {
    // silencioso — não é crítico
  }
}

function escapeHtml(str) {
  const d = document.createElement("div");
  d.textContent = str ?? "";
  return d.innerHTML;
}

async function selectSession(sessionId) {
  clearError();
  try {
    const res = await fetch(`${API}/sessions/${sessionId}`);
    if (!res.ok) throw new Error("sessão não encontrada");
    const data = await res.json();

    currentSessionId = data.session_id;
    currentThreadId = data.thread_id;
    currentThreadLabel.textContent = data.thread_id || "(ainda sem thread no codex)";
    currentThreadLabel.classList.remove("muted");

    transcript.innerHTML = "";
    if (data.messages.length === 0) {
      renderEmptyTranscript();
    } else {
      for (const m of data.messages) appendLine(m.role, m.content);
    }
    scrollTranscriptToEnd();
    refreshSessionList();
  } catch (e) {
    showError("Não foi possível carregar essa thread.");
  }
}

function renderEmptyTranscript() {
  currentThreadLabel.textContent = "— nenhuma thread selecionada —";
  currentThreadLabel.classList.add("muted");
  transcript.innerHTML = `<div class="transcript-empty">Envie uma instrução abaixo para abrir uma nova thread com o Codex.</div>`;
}

newThreadBtn.addEventListener("click", () => {
  currentSessionId = null;
  currentThreadId = null;
  clearError();
  renderEmptyTranscript();
  refreshSessionList();
  chatInput.focus();
});

// ---------- TRANSCRIPT ----------
function appendLine(role, content, { pending = false } = {}) {
  const emptyMsg = transcript.querySelector(".transcript-empty");
  if (emptyMsg) emptyMsg.remove();

  const line = document.createElement("div");
  line.className = `line ${role}` + (pending ? " pending" : "");

  const roleLabel = { user: "user ›", assistant: "codex ›", error: "erro ›" }[role] || role;

  line.innerHTML = `
    <span class="line-role">${roleLabel}</span>
    <span class="line-content"></span>
  `;
  line.querySelector(".line-content").textContent = content;
  transcript.appendChild(line);
  return line;
}

function scrollTranscriptToEnd() {
  transcript.scrollTop = transcript.scrollHeight;
}

// ---------- CHAT ----------
chatForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (sending) return;

  const message = chatInput.value.trim();
  if (!message) return;

  clearError();
  sending = true;
  sendBtn.disabled = true;
  sendBtn.textContent = "executando…";

  appendLine("user", message);
  const pendingLine = appendLine("assistant", "aguardando o codex…", { pending: true });
  scrollTranscriptToEnd();
  chatInput.value = "";

  try {
    const res = await fetch(`${API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: currentSessionId, message }),
    });
    const data = await res.json();

    if (!res.ok) {
      pendingLine.remove();
      appendLine("error", data.detail || "Erro ao chamar o Codex.");
    } else {
      currentSessionId = data.session_id;
      currentThreadId = data.thread_id;
      currentThreadLabel.textContent = data.thread_id;
      currentThreadLabel.classList.remove("muted");

      pendingLine.classList.remove("pending");
      pendingLine.querySelector(".line-content").textContent = data.reply;
    }
  } catch (e) {
    pendingLine.remove();
    appendLine("error", "Falha de rede ao chamar a API.");
  } finally {
    sending = false;
    sendBtn.disabled = false;
    sendBtn.textContent = "executar";
    scrollTranscriptToEnd();
    refreshSessionList();
  }
});

chatInput.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    chatForm.requestSubmit();
  }
});

// ---------- CONFIG DRAWER ----------
async function loadConfig() {
  try {
    const res = await fetch(`${API}/config`);
    const data = await res.json();
    cfgExecFlags.value = data.exec_flags;
    cfgTimeout.value = data.exec_timeout_seconds;
    cfgWorkspace.value = data.workspace_dir;
    cfgBin.value = data.codex_bin;
  } catch (e) {
    // silencioso
  }
}

configToggle.addEventListener("click", () => {
  configDrawer.classList.toggle("hidden");
  if (!configDrawer.classList.contains("hidden")) loadConfig();
});
configClose.addEventListener("click", () => configDrawer.classList.add("hidden"));

cfgSave.addEventListener("click", async () => {
  configSavedMsg.classList.add("hidden");
  try {
    const res = await fetch(`${API}/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        exec_flags: cfgExecFlags.value,
        exec_timeout_seconds: parseInt(cfgTimeout.value, 10),
      }),
    });
    if (!res.ok) throw new Error();
    configSavedMsg.classList.remove("hidden");
    setTimeout(() => configSavedMsg.classList.add("hidden"), 2500);
  } catch (e) {
    showError("Não foi possível salvar a configuração.");
  }
});

cfgReset.addEventListener("click", async () => {
  if (!confirm("Restaurar as configurações padrão?")) return;
  await fetch(`${API}/config/reset`, { method: "POST" });
  loadConfig();
});

// ---------- INIT ----------
refreshHealth();
refreshSessionList();
setInterval(refreshHealth, 20000);
