/* open-masking web UI */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    tab: "playground",
    messages: [],
    streaming: false,
    keys: [],
    connect: null,
    browser: null,
    statusTimer: null,
  };

  const MODEL_LABELS = {
    "deepseek-chat": "DeepSeek Chat (Web)",
  };

  /* ---------------- utilities ---------------- */

  function toast(message, bad = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("bad", Boolean(bad));
    el.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  /* Markdown: marked + DOMPurify dari CDN. Jika CDN gagal, pakai renderer sederhana. */

  function hasMarkdownLibs() {
    return typeof window.marked !== "undefined" && typeof window.DOMPurify !== "undefined";
  }

  function inlineFormat(value) {
    return value
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }

  function fallbackMarkdown(text) {
    const parts = [];
    const re = /```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g;
    let last = 0;
    let match;
    while ((match = re.exec(text)) !== null) {
      if (match.index > last) {
        parts.push({ type: "text", value: text.slice(last, match.index) });
      }
      parts.push({ type: "code", value: match[2].replace(/\n$/, "") });
      last = re.lastIndex;
    }
    if (last < text.length) {
      parts.push({ type: "text", value: text.slice(last) });
    }
    return parts
      .map((part) => {
        if (part.type === "code") {
          return `<pre><code>${escapeHtml(part.value)}</code></pre>`;
        }
        const trimmed = part.value.trim();
        if (!trimmed) return "";
        return `<p>${inlineFormat(escapeHtml(trimmed)).replace(/\n/g, "<br>")}</p>`;
      })
      .join("");
  }

  function renderMarkdown(text) {
    if (!hasMarkdownLibs()) return fallbackMarkdown(text);
    const raw = window.marked.parse(text, { gfm: true, breaks: true });
    return window.DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  }

  function formatTime(seconds) {
    if (!seconds) return "—";
    const date = new Date(seconds * 1000);
    return date.toLocaleString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  async function api(path, options = {}) {
    const { headers, ...rest } = options;
    const response = await fetch(path, {
      ...rest,
      headers: { "Content-Type": "application/json", ...(headers || {}) },
    });
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { raw: text };
    }
    if (!response.ok) {
      const message =
        (data && data.error && data.error.message) ||
        (data && data.detail) ||
        response.statusText ||
        "Request gagal";
      throw new Error(message);
    }
    return data;
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast("Disalin");
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        toast("Disalin");
      } catch {
        toast("Gagal menyalin", true);
      }
      area.remove();
    }
  }

  /* ---------------- navigation ---------------- */

  function showTab(name) {
    if (!name || !$(`#panel-${name}`)) name = "playground";
    state.tab = name;
    $$(".nav-item").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.tab === name);
    });
    $$(".panel").forEach((panel) => {
      panel.classList.toggle("is-active", panel.id === `panel-${name}`);
    });
    if (location.hash.slice(1) !== name) {
      history.replaceState(null, "", `#${name}`);
    }
    if (name === "keys") loadKeys();
    if (name === "connect") loadConnect();
    if (name === "browser") refreshStatus();
  }

  $$(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
  });

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-tab]");
    if (trigger && !trigger.classList.contains("nav-item")) {
      showTab(trigger.dataset.tab);
    }
  });

  /* ---------------- status ---------------- */

  function setHealth(kind, text) {
    const dot = $("#health-dot");
    dot.className = `dot ${kind}`;
    $("#health-text").textContent = text;
  }

  const BROWSER_LABELS = {
    stopped: ["berhenti", ""],
    starting: ["memulai", "warn"],
    waiting_login: ["menunggu login", "warn"],
    ready: ["siap", "ok"],
    error: ["error", "bad"],
  };

  function renderBrowser(browser) {
    if (!browser) return;
    state.browser = browser;
    const [label, tone] = BROWSER_LABELS[browser.state] || [browser.state || "—", ""];
    const badge = $("#br-state");
    badge.textContent = label;
    badge.className = `badge ${tone}`.trim();

    $("#br-running").textContent = browser.running ? "ya" : "tidak";
    $("#br-login-state").textContent = browser.logged_in ? "sudah login" : "belum login";
    $("#br-mode").textContent =
      browser.headless === null || browser.headless === undefined
        ? "—"
        : browser.headless
        ? "headless"
        : "jendela terlihat";
    $("#br-busy").textContent = browser.busy ? "sedang dipakai" : "idle";
    $("#br-profile").textContent = browser.profile_dir || "—";
    $("#br-profile-exists").textContent = browser.profile_exists ? "ya" : "tidak";
    $("#br-cookies").textContent = browser.cookies_file || "—";
    $("#br-cookies-exists").textContent = browser.cookies_exists ? "ya" : "tidak";

    const error = $("#br-error");
    if (browser.last_error) {
      error.textContent = browser.last_error;
      error.classList.remove("hidden");
    } else {
      error.classList.add("hidden");
      error.textContent = "";
    }
  }

  async function refreshStatus() {
    try {
      const data = await api("/api/status");
      setHealth("ok", "gateway siap");
      $("#app-version").textContent = `v${data.version} · lokal`;
      if (data.browser) renderBrowser(data.browser);
      if (state.tab === "connect") renderConnectWarning(data.keys);
    } catch (error) {
      setHealth("bad", "gateway tidak merespons");
    }
  }

  function startStatusPolling() {
    refreshStatus();
    if (state.statusTimer) clearInterval(state.statusTimer);
    state.statusTimer = setInterval(refreshStatus, 4000);
  }

  /* ---------------- playground ---------------- */

  const chatEl = () => $("#pg-chat");
  const messagesEl = () => $("#pg-messages");

  function scrollChat(force = false) {
    const box = chatEl();
    if (!box) return;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    if (force || nearBottom) box.scrollTop = box.scrollHeight;
  }

  function createMessageElement(message) {
    const wrapper = document.createElement("div");
    wrapper.className = `msg msg-${message.role}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = message.role === "user" ? "YOU" : "DS";

    const bubble = document.createElement("div");
    bubble.className = "bubble";

    let thinking = null;
    if (message.role === "assistant") {
      thinking = document.createElement("details");
      thinking.className = "thinking hidden";
      thinking.innerHTML =
        '<summary>Proses berpikir</summary><div class="thinking-body"></div>';
      bubble.appendChild(thinking);
    }

    const content = document.createElement("div");
    content.className = "content";
    bubble.appendChild(content);

    wrapper.append(avatar, bubble);
    return { wrapper, bubble, content, thinking };
  }

  /* Teks yang tampil. Saat streaming, tampilan mengejar isi penuh secara bertahap. */
  function visibleText(message) {
    const text = message.content || "";
    if (typeof message.shown === "number") return text.slice(0, message.shown);
    return text;
  }

  /* Membungkus setiap blok kode dengan tombol salin. Mengembalikan jumlah blok baru. */
  function enhanceCodeBlocks(root) {
    let added = 0;
    root.querySelectorAll("pre").forEach((pre) => {
      if (pre.parentElement && pre.parentElement.classList.contains("code-wrap")) return;
      const wrap = document.createElement("div");
      wrap.className = "code-wrap";
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "code-copy";
      button.setAttribute("aria-label", "Salin kode");
      button.title = "Salin kode";
      button.innerHTML = '<i data-lucide="copy"></i>';
      wrap.appendChild(button);
      added += 1;
    });
    return added;
  }

  function renderMessage(message) {
    if (!message.el) message.el = createMessageElement(message);
    const { content, thinking } = message.el;

    const text = visibleText(message);
    content.innerHTML = text ? renderMarkdown(text) : "";

    if (state.streaming && message.role === "assistant" && !message.done) {
      const caret = document.createElement("span");
      caret.className = "caret";
      content.appendChild(caret);
    }

    if (enhanceCodeBlocks(content) > 0) refreshIcons();

    if (thinking) {
      if (message.reasoning) {
        thinking.classList.remove("hidden");
        $(".thinking-body", thinking).textContent = message.reasoning;
      } else {
        thinking.classList.add("hidden");
      }
    }
    return message.el.wrapper;
  }

  function addMessage(message, forceScroll = true) {
    const rendered = renderMessage(message);
    if (!message.el.wrapper.parentElement) {
      messagesEl().appendChild(rendered);
    }
    $("#pg-empty").classList.add("hidden");
    if (forceScroll) scrollChat(true);
    return message;
  }

  /* Animasi mengetik: setiap frame, tampilan maju beberapa karakter menuju isi terbaru. */
  const typing = { queue: new Set(), frame: 0 };

  function queueRender(message) {
    typing.queue.add(message);
    if (!typing.frame) typing.frame = requestAnimationFrame(runTyping);
  }

  function runTyping() {
    typing.frame = 0;
    for (const message of typing.queue) {
      const total = (message.content || "").length;
      const shown = typeof message.shown === "number" ? message.shown : 0;
      if (shown < total) {
        const gap = total - shown;
        message.shown = shown + Math.min(60, Math.max(1, Math.ceil(gap * 0.2)));
      }
      renderMessage(message);
      if ((message.shown || 0) >= total) typing.queue.delete(message);
    }
    if (typing.queue.size) typing.frame = requestAnimationFrame(runTyping);
    scrollChat();
  }

  function clearConversationUI() {
    state.messages = [];
    typing.queue.clear();
    messagesEl().innerHTML = "";
    $("#pg-empty").classList.remove("hidden");
    $("#pg-status").textContent = "";
  }

  async function resetConversation() {
    try {
      await api("/api/playground/reset", { method: "POST" });
    } catch (error) {
      toast(error.message, true);
    }
    clearConversationUI();
    if (state.streaming) return;
    $("#pg-input").focus();
  }

  function setComposerEnabled(enabled) {
    $("#pg-send").disabled = !enabled;
    $("#pg-input").disabled = !enabled;
    $("#pg-reset").disabled = !enabled;
    $("#pg-model").disabled = !enabled;
    $("#pg-thinking").disabled = !enabled;
    $("#pg-search").disabled = !enabled;
  }

  function handleStreamEvent(event, assistant) {
    if (!event || !event.type) return;
    if (event.type === "content") {
      assistant.content += event.text || "";
      queueRender(assistant);
    } else if (event.type === "reasoning") {
      assistant.reasoning += event.text || "";
      renderMessage(assistant);
      scrollChat();
    } else if (event.type === "error") {
      assistant.error = event.message || "Terjadi kesalahan.";
    } else if (event.type === "done") {
      assistant.finish = event.finish_reason || "stop";
    }
  }

  async function sendMessage(text) {
    if (state.streaming || !text.trim()) return;

    const userMessage = { role: "user", content: text.trim() };
    state.messages.push(userMessage);
    addMessage(userMessage);

    const assistant = {
      role: "assistant",
      content: "",
      reasoning: "",
      error: null,
      finish: null,
      done: false,
      shown: 0,
    };
    addMessage(assistant);

    const thinkingValue = $("#pg-thinking").value;
    const payload = {
      model: $("#pg-model").value,
      messages: state.messages.map((message) => ({
        role: message.role,
        content: message.content,
      })),
      thinking: thinkingValue === "auto" ? null : thinkingValue === "on",
      search: $("#pg-search").value === "on",
    };

    state.streaming = true;
    setComposerEnabled(false);
    $("#pg-status").textContent = "menunggu DeepSeek…";

    let completed = false;
    try {
      const response = await fetch("/api/playground/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        let message = response.statusText;
        try {
          const data = await response.json();
          message = (data.error && data.error.message) || data.detail || message;
        } catch {
          /* ignore */
        }
        throw new Error(message || "Request gagal");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let index;
        while ((index = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          for (const line of block.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const data = line.slice(5).trim();
            if (!data || data === "[DONE]") continue;
            let event;
            try {
              event = JSON.parse(data);
            } catch {
              continue;
            }
            handleStreamEvent(event, assistant);
          }
        }
      }

      if (assistant.error) throw new Error(assistant.error);

      completed = true;
      assistant.done = true;
      if (assistant.content || assistant.reasoning) {
        state.messages.push({
          role: "assistant",
          content: assistant.content,
        });
      }
      $("#pg-status").textContent =
        assistant.finish === "tool_calls"
          ? "selesai (tool call)"
          : "selesai";
    } catch (error) {
      assistant.done = true;
      assistant.error = error.message;
      const content = assistant.el.content;
      content.innerHTML = `<p style="color:var(--danger)">${escapeHtml(error.message)}</p>`;
      $("#pg-status").textContent = "gagal";
      toast(error.message, true);
    } finally {
      typing.queue.delete(assistant);
      state.streaming = false;
      setComposerEnabled(true);
      if (completed) {
        delete assistant.shown;
        renderMessage(assistant);
      }
      if (!completed && !assistant.content) {
        // Biarkan pesan error terlihat, tetapi jangan simpan ke riwayat.
        assistant.done = true;
      }
      scrollChat(true);
      autoGrow();
      $("#pg-input").focus();
    }
  }

  function autoGrow() {
    const input = $("#pg-input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 190)}px`;
  }

  $("#pg-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#pg-input");
    const text = input.value;
    if (!text.trim()) return;
    input.value = "";
    autoGrow();
    sendMessage(text);
  });

  $("#pg-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#pg-form").requestSubmit();
    }
  });

  $("#pg-input").addEventListener("input", autoGrow);
  $("#pg-reset").addEventListener("click", resetConversation);

  $$(".suggestions .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const prompt = chip.dataset.prompt || chip.textContent;
      $("#pg-input").value = prompt;
      autoGrow();
      $("#pg-input").focus();
    });
  });

  messagesEl().addEventListener("click", (event) => {
    const button = event.target.closest(".code-copy");
    if (!button || !button.parentElement) return;
    const pre = button.parentElement.querySelector("pre");
    copyText(pre ? pre.textContent || "" : "");
  });

  /* ---------------- api keys ---------------- */

  function renderKeys() {
    const body = $("#key-rows");
    body.innerHTML = "";
    if (!state.keys.length) {
      body.innerHTML =
        '<tr><td colspan="6" class="muted">Belum ada API key. Buat satu untuk mulai menghubungkan klien.</td></tr>';
      return;
    }
    for (const key of state.keys) {
      const row = document.createElement("tr");
      const status = key.revoked
        ? '<span class="badge bad">revoked</span>'
        : '<span class="badge ok">aktif</span>';
      row.innerHTML = `
        <td>${escapeHtml(key.name || "—")}</td>
        <td class="mono">${escapeHtml(key.prefix || "—")}</td>
        <td>${formatTime(key.created_at)}</td>
        <td>${formatTime(key.last_used_at)}</td>
        <td>${status}</td>
        <td class="right"></td>
      `;
      if (!key.revoked) {
        const button = document.createElement("button");
        button.className = "btn danger ghost small";
        button.textContent = "Revoke";
        button.addEventListener("click", () => revokeKey(key));
        row.lastElementChild.appendChild(button);
      }
      body.appendChild(row);
    }
  }

  async function loadKeys() {
    try {
      const data = await api("/api/keys");
      state.keys = data.keys || [];
      renderKeys();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function createKey() {
    const input = $("#key-name");
    const name = (input.value || "opencode").trim() || "opencode";
    const button = $("#key-create");
    button.disabled = true;
    try {
      const data = await api("/api/keys", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      input.value = "";
      $("#key-modal-value").textContent = data.key;
      $("#key-modal").classList.remove("hidden");
      await loadKeys();
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function revokeKey(key) {
    if (!confirm(`Revoke API key "${key.name}"? Klien yang memakainya akan berhenti.`)) {
      return;
    }
    try {
      await api(`/api/keys/${encodeURIComponent(key.id)}/revoke`, { method: "POST" });
      toast("Key di-revoke");
      await loadKeys();
      refreshStatus();
    } catch (error) {
      toast(error.message, true);
    }
  }

  $("#key-create").addEventListener("click", createKey);
  $("#key-name").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createKey();
    }
  });
  $("#key-modal-done").addEventListener("click", () => {
    $("#key-modal").classList.add("hidden");
    $("#key-modal-value").textContent = "";
  });
  $("#key-modal").addEventListener("click", (event) => {
    if (event.target.id === "key-modal") {
      $("#key-modal").classList.add("hidden");
      $("#key-modal-value").textContent = "";
    }
  });

  /* ---------------- browser ---------------- */

  async function browserAction(action, label, body) {
    const buttons = $$("#panel-browser .btn");
    buttons.forEach((button) => (button.disabled = true));
    try {
      const data = await api(`/api/browser/${action}`, {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      });
      if (data && data.browser) renderBrowser(data.browser);
      toast(label);
      refreshStatus();
    } catch (error) {
      toast(error.message, true);
      refreshStatus();
    } finally {
      buttons.forEach((button) => (button.disabled = false));
    }
  }

  $("#br-login").addEventListener("click", () => {
    browserAction("login", "Login dimulai — selesaikan di jendela Chrome");
  });
  $("#br-restart").addEventListener("click", () => {
    browserAction("restart", "Browser dijalankan ulang");
  });
  $("#br-close").addEventListener("click", () => {
    browserAction("close", "Browser ditutup");
  });
  $("#br-reset").addEventListener("click", () => {
    if (
      !confirm(
        "Hapus profil Chrome dan cookies? Semua sesi login DeepSeek akan hilang."
      )
    ) {
      return;
    }
    browserAction("reset", "Profil direset", { confirm: true });
  });

  /* ---------------- connect ---------------- */

  function buildSnippets(connect) {
    const base = connect.base_url;
    const models = connect.models || [];
    const opencodeModels = models
      .map((model) => {
        const variants = `,
          "variants": [
            { "id": "deepthink", "body": { "thinking": true, "search": false } },
            { "id": "search", "body": { "thinking": false, "search": true } },
            { "id": "deepthink-search", "body": { "thinking": true, "search": true } }
          ]`;
        return `        "${model}": {
          "name": "${MODEL_LABELS[model] || model}",
          "capabilities": {
            "tools": true,
            "input": ["text"],
            "output": ["text"],
            "reasoning": { "supported": true }
          },
          "compatibility": { "reasoningField": "reasoning_content" },
          "limit": { "context": 24000, "output": 8000 }${variants}
        }`;
      })
      .join(",\n");

    const opencode = `{
  "$schema": "https://opencode.ai/config.json",
  "model": "open-masking/deepseek-chat",
  "providers": {
    "open-masking": {
      "name": "open-masking",
      "env": ["OPEN_MASKING_KEY"],
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "${base}"
      },
      "models": {
${opencodeModels}
      }
    }
  }
}`;

    const vscodeModels = models
      .map(
        (model) => `      {
        "id": "${model}",
        "name": "${MODEL_LABELS[model] || model}",
        "url": "${base}/chat/completions",
        "toolCalling": true,
        "vision": false,
        "maxInputTokens": 24000,
        "maxOutputTokens": 8000,
        "modelOptions": { "thinking": false, "search": false }
      }`
      )
      .join(",\n");

    const vscode = `[
  {
    "name": "open-masking",
    "vendor": "customendpoint",
    "apiKey": "sk-ds-GANTI_DENGAN_KEY",
    "apiType": "chat-completions",
    "models": [
${vscodeModels}
    ]
  }
]`;

    const curl = `curl ${base}/chat/completions \\
  -H "Authorization: Bearer sk-ds-GANTI_DENGAN_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "deepseek-chat",
    "thinking": false,
    "search": false,
    "messages": [{"role": "user", "content": "Halo"}]
  }'`;

    return { opencode, vscode, curl };
  }

  function renderConnectWarning(keys) {
    const warning = $("#cn-warning");
    if (!warning) return;
    const hasActive = keys && keys.active > 0;
    warning.classList.toggle("hidden", Boolean(hasActive));
  }

  async function loadConnect() {
    try {
      const data = await api("/api/connect");
      state.connect = data;
      $("#cn-base").textContent = data.base_url;
      const chips = $("#cn-models");
      chips.innerHTML = "";
      for (const model of data.models || []) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = model;
        chips.appendChild(chip);
      }
      $("#cn-warning").classList.toggle("hidden", (data.active_keys || 0) > 0);
      const snippets = buildSnippets(data);
      $("#cn-opencode").textContent = snippets.opencode;
      $("#cn-vscode").textContent = snippets.vscode;
      $("#cn-curl").textContent = snippets.curl;
    } catch (error) {
      toast(error.message, true);
    }
  }

  /* ---------------- copy buttons ---------------- */

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const target = document.getElementById(button.dataset.copy);
    if (!target) return;
    copyText(target.textContent || "");
  });

  /* ---------------- boot ---------------- */

  if (hasMarkdownLibs() && typeof window.DOMPurify.addHook === "function") {
    window.DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A") {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    });
  }

  $("#app-version").textContent = "v0.2.0 · lokal";
  showTab(location.hash.slice(1) || "playground");
  autoGrow();
  refreshIcons();
  startStatusPolling();
  $("#pg-input").focus();
})();
