/**
 * Veliora — Assistant IA (Ollama).
 * Streaming NDJSON token-by-token + UI animée, mémoire longue,
 * exécution d'actions proposées par le modèle après validation explicite.
 */
(function () {
  "use strict";

  const state = {
    deps: null,
    bound: false,
    initialized: false,
    conversationId: null,
    sending: false,
    abortCtl: null,
    conversations: [],
    memories: [],
    health: null,
    healthTimer: null,
  };
  const CHAT_REQUEST_TIMEOUT_MS = 95000;

  function deps() {
    const globalEscapeHtml = (typeof window !== "undefined" && typeof window.escapeHtml === "function")
      ? window.escapeHtml
      : null;
    return {
      api: typeof api === "function" ? api : null,
      API: typeof API === "string" ? API : "/api",
      getAuthHeaders: typeof getAuthHeaders === "function" ? getAuthHeaders : () => ({}),
      showToast: typeof showToast === "function" ? showToast : () => {},
      escapeHtml: globalEscapeHtml || ((s) => String(s ?? "")),
      openDrawer: typeof openDrawer === "function" ? openDrawer : null,
    };
  }

  function el(id) {
    return document.getElementById(id);
  }

  function escapeHtml(s) {
    return deps().escapeHtml(s ?? "");
  }

  function formatTimeShort(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "short" });
  }

  // ── Réparation UTF-8 mal interprété (ex. analysÃ© → analysé, â¬ → €) ──
  function repairMojibake(text) {
    const s = String(text || "");
    if (!/[ÃÂâ€â¬â‰¥]/.test(s)) return s;
    try {
      const bytes = Uint8Array.from(s, (ch) => ch.charCodeAt(0) & 0xff);
      const fixed = new TextDecoder("utf-8").decode(bytes);
      if (fixed && !fixed.includes("\uFFFD")) return fixed;
    } catch {
      /* ignore */
    }
    return s
      .replace(/â‚¬/g, "€")
      .replace(/â¬/g, "€")
      .replace(/â‰¥/g, "≥")
      .replace(/Ã©/g, "é")
      .replace(/Ã¨/g, "è")
      .replace(/Ãª/g, "ê")
      .replace(/Ã«/g, "ë")
      .replace(/Ã /g, "à")
      .replace(/Ã¢/g, "â")
      .replace(/Ã®/g, "î")
      .replace(/Ã´/g, "ô")
      .replace(/Ã»/g, "û")
      .replace(/Ã¹/g, "ù")
      .replace(/Ã§/g, "ç")
      .replace(/Å“/g, "œ")
      .replace(/Ã‰/g, "É")
      .replace(/Ã€/g, "À");
  }

  function renderInline(text) {
    let out = escapeHtml(repairMojibake(text));
    // Liens markdown [label](url)
    out = out.replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/gi,
      '<a href="$2" target="_blank" rel="noopener noreferrer" class="ai-md-link">$1</a>',
    );
    out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");
    out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
    // Annonces #123 → lien fiche
    out = out.replace(
      /#(\d{1,6})\b/g,
      '<a href="#" class="ai-lead-link" data-lead-id="$1">#$1</a>',
    );
    // Montants en euros
    out = out.replace(
      /(\d[\d\s]*)\s*€/g,
      '<span class="ai-md-price">$1 €</span>',
    );
    return out;
  }

  function renderMarkdownLite(text) {
    const safe = repairMojibake(stripActionBlocks(String(text || "")));
    const lines = safe.split(/\r?\n/);
    const html = [];
    let inList = false;
    const closeList = () => {
      if (inList) {
        html.push("</ul>");
        inList = false;
      }
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      const trimmed = line.trim();
      if (/^---+$/.test(trimmed)) {
        closeList();
        html.push('<hr class="ai-md-hr" />');
        continue;
      }
      const h3 = trimmed.match(/^###\s+(.+)$/);
      if (h3) {
        closeList();
        html.push(`<h3 class="ai-md-h3">${renderInline(h3[1])}</h3>`);
        continue;
      }
      const h2 = trimmed.match(/^##\s+(.+)$/);
      if (h2) {
        closeList();
        html.push(`<h2 class="ai-md-h2">${renderInline(h2[1])}</h2>`);
        continue;
      }
      const quote = trimmed.match(/^>\s+(.+)$/);
      if (quote) {
        closeList();
        html.push(`<blockquote class="ai-md-quote">${renderInline(quote[1])}</blockquote>`);
        continue;
      }
      const bullet = line.match(/^\s*(?:[-*•]|\d+\.)\s+(.+)$/);
      if (bullet) {
        if (!inList) {
          html.push('<ul class="ai-md-list">');
          inList = true;
        }
        html.push(`<li>${renderInline(bullet[1])}</li>`);
        continue;
      }
      closeList();
      if (!trimmed) {
        html.push('<div class="ai-msg-spacer"></div>');
        continue;
      }
      html.push(`<p class="ai-md-p">${renderInline(line)}</p>`);
    }
    closeList();
    return html.join("");
  }

  function bindLeadLinks(root) {
    if (!root) return;
    root.querySelectorAll(".ai-lead-link:not([data-bound])").forEach((a) => {
      a.dataset.bound = "1";
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const id = parseInt(a.dataset.leadId, 10);
        if (Number.isFinite(id) && typeof openDrawer === "function") openDrawer(id);
      });
    });
  }

  // ── Détection d'un bloc ACTION_JSON dans la réponse ──
  function extractActions(text) {
    if (!text) return [];
    const results = [];
    const re = /ACTION_JSON\s*```json\s*([\s\S]*?)```/gi;
    let m;
    while ((m = re.exec(text)) !== null) {
      try {
        const action = JSON.parse(m[1]);
        if (action && typeof action === "object") results.push(action);
      } catch {
        /* ignore parse error */
      }
    }
    return results;
  }

  const VOIR_TOUT_MARKER = /\[\[VOIR_TOUT_PROSPECTS\]\]/gi;

  function stripActionBlocks(text) {
    let s = String(text || "");
    s = s.replace(/ACTION_JSON\s*```json\s*[\s\S]*?```/gi, "");
    s = s.replace(/ACTION_JSON\s*```json[\s\S]*$/i, "");
    s = s.replace(/```json\s*\{[\s\S]*$/i, "");
    s = s.replace(/ACTION_JSON\s*$/i, "");
    s = s.replace(VOIR_TOUT_MARKER, "");
    return s.trim();
  }

  function parseLeadId(raw) {
    if (raw == null || raw === "") return null;
    const n = parseInt(String(raw).trim().replace(/^#/, ""), 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  function extractProspectIdsFromText(text) {
    const ids = [];
    const seen = new Set();
    const re = /#(\d{1,6})\b/g;
    let m;
    while ((m = re.exec(String(text || ""))) !== null) {
      const id = parseInt(m[1], 10);
      if (!seen.has(id)) {
        seen.add(id);
        ids.push(id);
      }
    }
    return ids;
  }

  function resolveLeadIdFromAction(action, contextText) {
    const a = action || {};
    for (const key of ["lead_id", "prospect_id", "id"]) {
      const id = parseLeadId(a[key]);
      if (id) return id;
    }
    for (const field of ["note", "content", "message", "label", "title"]) {
      const text = a[field];
      if (!text) continue;
      const hash = String(text).match(/#(\d{1,6})\b/);
      if (hash) return parseInt(hash[1], 10);
      const explicit = String(text).match(/\blead[_\s-]?id\s*[:=]\s*(\d{1,6})\b/i);
      if (explicit) return parseInt(explicit[1], 10);
    }
    const ids = extractProspectIdsFromText(contextText);
    if (ids.length === 1) return ids[0];
    return null;
  }

  function actionNeedsLeadId(action) {
    const n = (action?.action || "").toLowerCase();
    return n === "update_pipeline" || n === "add_note" || n === "set_followup";
  }

  function normalizeActions(actions, contextText) {
    return (actions || []).map((raw) => {
      const a = { ...raw };
      if (!actionNeedsLeadId(a)) return a;
      const leadId = resolveLeadIdFromAction(a, contextText);
      if (leadId) a.lead_id = leadId;
      return a;
    });
  }

  function actionLabel(action) {
    const a = action || {};
    const lid = resolveLeadIdFromAction(a, "");
    const id = lid ? ` #${lid}` : "";
    switch (a.action) {
      case "update_pipeline":
        return `Pipeline${id} → ${a.pipeline || "—"}`;
      case "add_note":
        return `Ajouter une note${id}`;
      case "set_followup":
        return `Programmer relance${id} le ${a.date || a.when || "?"}`;
      case "remember":
        return `Mémoriser un fait`;
      default:
        return a.action || "Action proposée";
    }
  }

  function renderActionsHtml(actions, contextText) {
    const normalized = normalizeActions(actions, contextText);
    if (!normalized.length) return "";
    const rows = normalized
      .map((a, i) => {
        const needs = actionNeedsLeadId(a);
        const lid = resolveLeadIdFromAction(a, contextText);
        if (needs && !lid) {
          return `<button type="button" class="ai-action-btn ai-action-disabled" disabled title="Précisez l'id prospect (#60) dans l'action IA">
            ${escapeHtml(actionLabel(a))} — id manquant
          </button>`;
        }
        return `<button type="button" class="ai-action-btn" data-action-idx="${i}">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M5 13l4 4L19 7"/></svg>
            ${escapeHtml(actionLabel(a))}
          </button>`;
      })
      .join("");
    return `<div class="ai-actions"><span class="ai-actions-label">Actions proposées</span>${rows}</div>`;
  }

  function shouldShowVoirToutProspects(visibleText) {
    if (/\[\[VOIR_TOUT_PROSPECTS\]\]/i.test(String(visibleText || ""))) return true;
    const t = String(visibleText || "");
    const listIntent = /prospect|annonces|liste|priorit|briefing|portefeuille/i.test(t);
    const nIds = extractProspectIdsFromText(t).length;
    return nIds >= 6 || (listIntent && nIds >= 3);
  }

  function renderVoirToutProspectsCta() {
    return `<div class="ai-cta-row">
      <button type="button" class="ai-cta-btn ai-cta-voir-tout">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
        Voir tous les prospects
      </button>
    </div>`;
  }

  function bindVoirToutProspects(root) {
    if (!root) return;
    root.querySelectorAll(".ai-cta-voir-tout:not([data-bound])").forEach((btn) => {
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        if (typeof switchView === "function") switchView("leads");
      });
    });
  }

  function ensureMessagesContainer() {
    const wrap = el("ai-messages");
    if (!wrap) return null;
    const welcome = wrap.querySelector(".ai-welcome");
    if (welcome) welcome.remove();
    return wrap;
  }

  function scrollToBottom() {
    const wrap = el("ai-messages");
    if (!wrap) return;
    wrap.scrollTo({ top: wrap.scrollHeight, behavior: "smooth" });
  }

  function addUserMessage(text) {
    const wrap = ensureMessagesContainer();
    if (!wrap) return;
    const node = document.createElement("article");
    node.className = "ai-msg ai-msg-user";
    node.innerHTML = `
      <div class="ai-msg-bubble">${renderInline(text)}</div>
      <div class="ai-msg-meta"><span>Vous</span><span>${escapeHtml(formatTimeShort(new Date().toISOString()))}</span></div>`;
    wrap.appendChild(node);
    scrollToBottom();
  }

  function addAssistantPlaceholder() {
    const wrap = ensureMessagesContainer();
    if (!wrap) return null;
    const node = document.createElement("article");
    node.className = "ai-msg ai-msg-assistant ai-msg-streaming";
    node.innerHTML = `
      <div class="ai-msg-avatar" aria-hidden="true">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2.5l1.7 3.6 3.8.5-2.8 2.6.7 3.8L12 11.2 8.6 13l.7-3.8L6.5 6.6l3.8-.5L12 2.5z"/></svg>
      </div>
      <div class="ai-msg-body">
        <div class="ai-msg-bubble"><span class="ai-typing"><span></span><span></span><span></span></span></div>
        <div class="ai-msg-meta"><span>Veliora IA</span></div>
      </div>`;
    wrap.appendChild(node);
    scrollToBottom();
    return node;
  }

  function appendAssistantText(node, fullText) {
    if (!node) return;
    const bubble = node.querySelector(".ai-msg-bubble");
    if (!bubble) return;
    const visible = stripActionBlocks(fullText) || "…";
    bubble.innerHTML = renderMarkdownLite(visible);
    bindLeadLinks(bubble);
    scrollToBottom();
  }

  function finalizeAssistantNode(node, fullText) {
    if (!node) return;
    node.classList.remove("ai-msg-streaming");
    const actions = extractActions(fullText);
    const visible = stripActionBlocks(fullText) || "…";
    const normalized = normalizeActions(actions, visible);
    const bubble = node.querySelector(".ai-msg-bubble");
    if (bubble) {
      bubble.innerHTML = renderMarkdownLite(visible);
      bindLeadLinks(bubble);
    }

    const body = node.querySelector(".ai-msg-body");
    if (body) {
      if (shouldShowVoirToutProspects(fullText) || shouldShowVoirToutProspects(visible)) {
        body.insertAdjacentHTML("beforeend", renderVoirToutProspectsCta());
        bindVoirToutProspects(body);
      }
      if (normalized.length) {
        const actionsHtml = renderActionsHtml(normalized, visible);
        body.insertAdjacentHTML("beforeend", actionsHtml);
        const btns = body.querySelectorAll(".ai-action-btn:not(.ai-action-disabled)");
        btns.forEach((btn) => {
          const idx = parseInt(btn.dataset.actionIdx, 10);
          btn.addEventListener("click", () => runAction(btn, normalized[idx], visible));
        });
      }
    }
    scrollToBottom();
  }

  function showErrorMessage(node, text) {
    if (!node) return;
    node.classList.remove("ai-msg-streaming");
    node.classList.add("ai-msg-error");
    const bubble = node.querySelector(".ai-msg-bubble");
    if (bubble) bubble.innerHTML = `⚠️ ${escapeHtml(text || "Une erreur est survenue.")}`;
  }

  // ── Exécution d'une action validée ──
  async function runAction(btn, action, contextText) {
    if (!btn || !action) return;
    if (btn.dataset.running === "1") return;
    const payload = { ...action };
    if (actionNeedsLeadId(payload)) {
      const lid = resolveLeadIdFromAction(payload, contextText || "");
      if (!lid) {
        deps().showToast("Id prospect manquant — mentionnez #60 dans la réponse ou relancez l'IA", "error");
        return;
      }
      payload.lead_id = lid;
    }
    btn.dataset.running = "1";
    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = `<span class="ai-action-spinner"></span> En cours…`;
    try {
      const res = await fetch(`${deps().API}/ai/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...deps().getAuthHeaders() },
        body: JSON.stringify({ action: payload }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok || !body.ok) throw new Error(body.error || `HTTP ${res.status}`);
      btn.classList.add("ai-action-done");
      btn.innerHTML = `✓ ${escapeHtml(body.detail || "Fait")}`;
      deps().showToast(body.detail || "Action exécutée", "success");
    } catch (err) {
      btn.classList.add("ai-action-failed");
      btn.disabled = false;
      btn.dataset.running = "0";
      btn.innerHTML = original;
      deps().showToast(err.message || "Action impossible", "error");
    }
  }

  // ── Streaming NDJSON ──
  async function sendMessage(text) {
    if (state.sending) return;
    const userText = (text || "").trim();
    if (!userText) return;
    state.sending = true;
    state.abortCtl = new AbortController();
    updateSendButton();
    addUserMessage(userText);
    const node = addAssistantPlaceholder();
    let fullText = "";
    const applyAssistantText = (text, { replace = false } = {}) => {
      fullText = replace ? String(text || "") : fullText + String(text || "");
      appendAssistantText(node, fullText);
    };

    const payload = {
      message: userText,
      conversation_id: state.conversationId || undefined,
    };
    let timeoutId = null;
    try {
      timeoutId = setTimeout(() => {
        try {
          state.abortCtl?.abort("timeout");
        } catch {
          /* noop */
        }
      }, CHAT_REQUEST_TIMEOUT_MS);
      const res = await fetch(`${deps().API}/ai/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...deps().getAuthHeaders() },
        body: JSON.stringify(payload),
        signal: state.abortCtl.signal,
      });
      if (res.status === 404) {
        throw new Error(
          "Endpoint /api/ai/chat introuvable — Scalingo n'a pas déployé le code multi-provider. " +
          "Dashboard → Deploy → Manual deploy → main.",
        );
      }
      if (res.status === 401) {
        throw new Error("Session expirée — recharge la page et reconnecte-toi.");
      }
      if (!res.ok) {
        // Le backend met l'erreur en JSON quand il peut (`{error: "..."}`),
        // sinon on remonte le texte brut.
        let detail = "";
        try {
          const ct = res.headers.get("content-type") || "";
          if (ct.includes("application/json")) {
            const body = await res.json();
            detail = body.error || body.detail || JSON.stringify(body).slice(0, 200);
          } else {
            detail = (await res.text()).slice(0, 250);
          }
        } catch { /* ignore */ }
        throw new Error(`Serveur IA HTTP ${res.status} — ${detail || "réponse vide"}`);
      }
      if (!res.body || !res.body.getReader) {
        const all = await res.text();
        all.split(/\n/).forEach((line) => handleEvent(node, line, applyAssistantText));
        finalizeAssistantNode(node, fullText);
        await refreshConversationsList();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n")) !== -1) {
          const line = buffer.slice(0, idx).trim();
          buffer = buffer.slice(idx + 1);
          if (line) handleEvent(node, line, applyAssistantText);
        }
      }
      const tail = buffer.trim();
      if (tail) handleEvent(node, tail, applyAssistantText);
      // Si rien n'est sorti du tout, on affiche un message clair plutôt qu'une
      // bulle vide qui laisse l'agent dans le flou.
      if (!fullText.trim()) {
        showErrorMessage(
          node,
          "Le fournisseur IA a fermé la connexion sans répondre. " +
          "Vérifie que AI_PROVIDER, AI_API_KEY et AI_MODEL sont bien définis dans Scalingo " +
          "et que Scalingo a redéployé après le dernier env-set.",
        );
      } else {
        finalizeAssistantNode(node, fullText);
      }
      await refreshConversationsList();
    } catch (err) {
      if (err.name === "AbortError") {
        const timedOut = state.abortCtl?.signal?.reason === "timeout";
        showErrorMessage(
          node,
          timedOut
            ? "Le fournisseur IA met trop de temps a repondre. Reessaie dans quelques secondes."
            : "Interrompu",
        );
      } else {
        showErrorMessage(node, err.message || "Erreur réseau");
      }
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
      state.sending = false;
      state.abortCtl = null;
      updateSendButton();
    }
  }

  function handleEvent(node, line, onText) {
    let evt;
    try {
      evt = JSON.parse(line);
    } catch {
      return;
    }
    if (!evt || typeof evt !== "object") return;
    if (evt.type === "meta" && evt.conversation) {
      state.conversationId = evt.conversation.id;
      highlightActiveConversation();
    } else if (evt.type === "token") {
      onText(evt.delta || "");
    } else if (evt.type === "error") {
      showErrorMessage(node, evt.error || "Erreur IA");
    } else if (evt.type === "final" && typeof evt.content === "string") {
      onText(evt.content, { replace: true });
    }
  }

  // ── Conversations ──
  async function refreshConversationsList() {
    try {
      const res = await deps().api("/ai/conversations");
      state.conversations = res.conversations || [];
      renderConversations();
    } catch {
      /* ignore */
    }
  }

  function renderConversations() {
    const wrap = el("ai-conversations");
    if (!wrap) return;
    if (!state.conversations.length) {
      wrap.innerHTML = `<p class="ai-empty-list">Aucune conversation pour l'instant.</p>`;
      return;
    }
    wrap.innerHTML = state.conversations
      .map((c) => {
        const active = c.id === state.conversationId ? " active" : "";
        return `<button type="button" class="ai-conversation${active}" data-conv="${escapeHtml(c.id)}">
            <span class="ai-conversation-title">${escapeHtml(c.title || "Conversation")}</span>
            <span class="ai-conversation-meta">${escapeHtml(formatTimeShort(c.updated_at))}</span>
            <span class="ai-conversation-delete" data-conv-del="${escapeHtml(c.id)}" title="Supprimer">×</span>
          </button>`;
      })
      .join("");
    wrap.querySelectorAll("[data-conv]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        if (e.target?.dataset?.convDel) return;
        loadConversation(btn.dataset.conv);
      });
    });
    wrap.querySelectorAll("[data-conv-del]").forEach((x) => {
      x.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Supprimer cette conversation ?")) return;
        try {
          await deps().api(`/ai/conversations/${x.dataset.convDel}`, { method: "DELETE" });
          if (state.conversationId === x.dataset.convDel) startNewConversation();
          await refreshConversationsList();
        } catch (err) {
          deps().showToast(err.message || "Suppression impossible", "error");
        }
      });
    });
  }

  function highlightActiveConversation() {
    document.querySelectorAll("[data-conv]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.conv === state.conversationId);
    });
  }

  async function loadConversation(convId) {
    if (!convId) return;
    state.conversationId = convId;
    highlightActiveConversation();
    const wrap = el("ai-messages");
    if (wrap) wrap.innerHTML = `<p class="ai-empty-list">Chargement…</p>`;
    try {
      const res = await deps().api(`/ai/conversations/${convId}`);
      renderMessages(res.messages || []);
    } catch (err) {
      if (wrap) wrap.innerHTML = `<p class="ai-empty-list">${escapeHtml(err.message || "Conversation introuvable")}</p>`;
    }
  }

  function renderMessages(messages) {
    const wrap = el("ai-messages");
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!messages.length) {
      wrap.innerHTML = `<p class="ai-empty-list">Conversation vide.</p>`;
      return;
    }
    messages.forEach((m) => {
      if (m.role === "user") {
        addUserMessage(m.content);
      } else if (m.role === "assistant") {
        const node = addAssistantPlaceholder();
        finalizeAssistantNode(node, m.content);
      }
    });
  }

  function startNewConversation() {
    state.conversationId = null;
    const wrap = el("ai-messages");
    if (wrap) {
      wrap.innerHTML = `
        <div class="ai-welcome">
          <div class="ai-welcome-glow" aria-hidden="true"></div>
          <h2>Nouvelle conversation</h2>
          <p>Posez votre question — je repars d'une page blanche mais je garde la mémoire longue de l'agence.</p>
        </div>`;
    }
    highlightActiveConversation();
    el("ai-input")?.focus();
  }

  // ── Mémoire longue ──
  async function refreshMemories() {
    try {
      const res = await deps().api("/ai/memory");
      state.memories = res.memories || [];
      renderMemories();
    } catch {
      /* ignore */
    }
  }

  function renderMemories() {
    const ul = el("ai-memory-list");
    if (!ul) return;
    if (!state.memories.length) {
      ul.innerHTML = `<li class="ai-empty-list">Rien à retenir pour l'instant.</li>`;
      return;
    }
    ul.innerHTML = state.memories
      .slice(0, 10)
      .map(
        (m) => `<li>
          <span>${escapeHtml(m.content)}</span>
          <button type="button" class="ai-memory-del" data-mem="${escapeHtml(m.id)}" title="Oublier">×</button>
        </li>`,
      )
      .join("");
    ul.querySelectorAll("[data-mem]").forEach((b) => {
      b.addEventListener("click", async () => {
        try {
          await deps().api(`/ai/memory/${b.dataset.mem}`, { method: "DELETE" });
          refreshMemories();
        } catch (err) {
          deps().showToast(err.message || "Suppression impossible", "error");
        }
      });
    });
  }

  async function addMemoryPrompt() {
    const content = prompt("Quel fait dois-je retenir pour toujours ?");
    if (!content || !content.trim()) return;
    try {
      await deps().api("/ai/memory", {
        method: "POST",
        body: JSON.stringify({ content: content.trim(), scope: "general", source: "user" }),
      });
      refreshMemories();
      deps().showToast("Mémorisé", "success");
    } catch (err) {
      deps().showToast(err.message || "Mémoire impossible", "error");
    }
  }

  // ── Health badge ──
  async function refreshHealth() {
    const dot = el("ai-status-dot");
    const label = el("ai-status-label");
    try {
      const res = await deps().api("/ai/health");
      state.health = res;
      const provider = res.provider || "ollama";
      const providerLabel = res.label || (provider === "ollama" ? "Ollama" : provider);
      const model = res.configured_model || "";

      if (!res.reachable) {
        if (dot) dot.dataset.state = "off";
        if (label) {
          if (provider === "ollama") {
            const isRemote = (res.base_url || "").startsWith("https://");
            label.innerHTML = isRemote
              ? `Ollama injoignable (<code>${escapeHtml(res.base_url)}</code>) — voir <code>ORACLE_CLOUD.md</code>`
              : `Ollama local non démarré — <code>ollama serve</code>`;
          } else if (res.error && /AI_API_KEY/.test(res.error)) {
            label.innerHTML = `${escapeHtml(providerLabel)} — clé manquante. ${res.key_url ? `<a href="${escapeHtml(res.key_url)}" target="_blank" rel="noopener">Créer ma clé</a>` : ""}`;
          } else {
            label.textContent = res.error || `${providerLabel} injoignable`;
          }
        }
        return;
      }
      if (res.needs_auth) {
        if (dot) dot.dataset.state = "warn";
        if (label) {
          label.innerHTML = `${escapeHtml(providerLabel)} : clé API refusée. ${res.key_url ? `<a href="${escapeHtml(res.key_url)}" target="_blank" rel="noopener">Générer une nouvelle clé</a>` : ""}`;
        }
        return;
      }
      const ok = (provider !== "ollama") || res.has_primary_model || res.has_fallback_model;
      if (dot) dot.dataset.state = ok ? "on" : "warn";
      if (label) {
        if (ok) {
          label.textContent = `${providerLabel} · ${model}`;
        } else {
          label.innerHTML = `Aucun modèle installé — <code>ollama pull ${escapeHtml(model)}</code>`;
        }
      }
    } catch (err) {
      if (dot) dot.dataset.state = "off";
      if (label) {
        const msg = (err && err.message) ? err.message : "inconnu";
        // On affiche directement la raison réelle pour ne pas laisser l'agent
        // deviner — typiquement « Route API introuvable » signifie que
        // Scalingo doit redéployer avec le code multi-provider.
        label.innerHTML = /route api introuvable|404/i.test(msg)
          ? "Endpoint IA absent — Scalingo doit redéployer (push main + manual deploy)"
          : `Statut IA indisponible : ${escapeHtml(msg)}`;
      }
    }
  }

  // ── Composer ──
  function updateSendButton() {
    const btn = el("ai-send");
    const input = el("ai-input");
    if (!btn || !input) return;
    const hasText = input.value.trim().length > 0;
    btn.disabled = !hasText || state.sending;
    btn.classList.toggle("loading", state.sending);
  }

  function autosizeTextarea() {
    const ta = el("ai-input");
    if (!ta) return;
    ta.style.height = "auto";
    const max = 180;
    ta.style.height = Math.min(ta.scrollHeight, max) + "px";
  }

  function bindUi() {
    if (state.bound) return;
    state.bound = true;

    el("ai-btn-new")?.addEventListener("click", () => startNewConversation());
    el("ai-clear")?.addEventListener("click", () => {
      if (state.sending && state.abortCtl) {
        state.abortCtl.abort();
        return;
      }
      startNewConversation();
    });
    el("ai-memory-add")?.addEventListener("click", () => addMemoryPrompt());

    const composer = el("ai-composer");
    composer?.addEventListener("submit", (e) => {
      e.preventDefault();
      const ta = el("ai-input");
      const text = ta?.value.trim();
      if (!text) return;
      ta.value = "";
      autosizeTextarea();
      updateSendButton();
      sendMessage(text);
    });

    const ta = el("ai-input");
    if (ta) {
      ta.addEventListener("input", () => {
        autosizeTextarea();
        updateSendButton();
      });
      ta.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          composer?.dispatchEvent(new Event("submit", { cancelable: true }));
        }
      });
    }

    document.querySelectorAll(".ai-suggestion").forEach((btn) => {
      btn.addEventListener("click", () => {
        const text = btn.dataset.suggestion || btn.textContent || "";
        sendMessage(text);
      });
    });
  }

  function init() {
    if (state.initialized) return;
    state.initialized = true;
    bindUi();
    refreshConversationsList();
    refreshMemories();
    refreshHealth();
    if (state.healthTimer) clearInterval(state.healthTimer);
    state.healthTimer = setInterval(refreshHealth, 60000);
  }

  // Expose pour app.js (qui pourra appeler VelioraAI.enter() au switch d'onglet)
  window.VelioraAI = {
    enter: () => {
      init();
      setTimeout(() => el("ai-input")?.focus(), 60);
    },
    refresh: () => {
      refreshConversationsList();
      refreshMemories();
      refreshHealth();
    },
  };

  // Auto-init léger si l'onglet est déjà visible au chargement
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      if (document.getElementById("view-ai")?.classList.contains("active")) {
        init();
      }
    });
  } else if (document.getElementById("view-ai")?.classList.contains("active")) {
    init();
  }
})();
