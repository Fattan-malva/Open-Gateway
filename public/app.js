/* Open Gateway web UI (multi-provider, multi-gateway) */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    tab: "gateway",
    view: { gateway: "list", provider: "list" },
    selectedGateway: null,
    selectedProvider: null,
    providers: [],
    providerTypes: [],
    gateways: [],
    gatewayTypes: [],
    models: [],
    catalog: {},
    provider: null,
    gateway: null,
    status: null,
    busy: false,
    statusTimer: null,
    logTimer: null,
  };

  const numberFmt = new Intl.NumberFormat("id-ID");

  /* ---------------- utilities ---------------- */
  function toast(message, bad = false) {
    const el = $("#toast");
    el.textContent = message;
    el.classList.toggle("bad", Boolean(bad));
    el.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.remove("show"), 3000);
  }
  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  /* ---------------- modal helpers (pengganti alert/prompt/confirm bawaan browser) ---------------- */
  function openModal({ title = "", bodyHtml = "", fields = null, confirmText = "OK",
                       cancelText = "Batal", danger = false, hideCancel = false, onMount = null } = {}) {
    return new Promise((resolve) => {
      const backdrop = $("#modal-backdrop");
      const titleEl = $("#modal-title");
      const body = $("#modal-body");
      const confirmBtn = $("#modal-confirm");
      const cancelBtn = $("#modal-cancel");

      titleEl.textContent = title;
      if (fields) {
        body.innerHTML = fields.map((f) => {
          const label = escapeHtml(f.label || f.name);
          const value = f.value == null ? "" : String(f.value);
          if (f.type === "textarea") {
            return `<label class="field span-2"><span>${label}</span><textarea name="${escapeHtml(f.name)}" rows="${Number(f.rows) || 3}" placeholder="${escapeHtml(f.placeholder || "")}">${escapeHtml(value)}</textarea></label>`;
          }
          if (f.type === "select") {
            const opts = (f.options || []).map((o) => {
              const ov = typeof o === "object" ? o.value : o;
              const ol = typeof o === "object" ? (o.label ?? o.value) : o;
              return `<option value="${escapeHtml(ov)}"${String(ov) === value ? " selected" : ""}>${escapeHtml(ol)}</option>`;
            }).join("");
            return `<label class="field span-2"><span>${label}</span><select name="${escapeHtml(f.name)}">${opts}</select></label>`;
          }
          return `<label class="field span-2"><span>${label}</span><input type="${escapeHtml(f.type || "text")}" name="${escapeHtml(f.name)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(f.placeholder || "")}" autocomplete="off" /></label>`;
        }).join("");
      } else {
        body.innerHTML = bodyHtml;
      }
      if (typeof onMount === "function") onMount(body);
      confirmBtn.textContent = confirmText;
      cancelBtn.textContent = cancelText;
      confirmBtn.classList.toggle("danger", Boolean(danger));
      cancelBtn.classList.toggle("hidden", Boolean(hideCancel));
      backdrop.classList.remove("hidden");
      createIcons();

      const done = (result) => {
        backdrop.classList.add("hidden");
        confirmBtn.removeEventListener("click", onConfirm);
        cancelBtn.removeEventListener("click", onCancel);
        backdrop.removeEventListener("click", onBackdrop);
        document.removeEventListener("keydown", onKey);
        resolve(result);
      };
      const onConfirm = () => {
        const out = {};
        body.querySelectorAll("[name]").forEach((el) => {
          if (el.type === "checkbox") out[el.name] = el.checked ? el.value : "";
          else out[el.name] = el.value;
        });
        done(out);
      };
      const onCancel = () => done(null);
      const onBackdrop = (event) => { if (event.target === backdrop) onCancel(); };
      const onKey = (event) => {
        if (event.key === "Escape") onCancel();
        else if (event.key === "Enter" && event.target.tagName !== "TEXTAREA") {
          event.preventDefault();
          onConfirm();
        }
      };
      confirmBtn.addEventListener("click", onConfirm);
      cancelBtn.addEventListener("click", onCancel);
      backdrop.addEventListener("click", onBackdrop);
      document.addEventListener("keydown", onKey);
      setTimeout(() => {
        const first = body.querySelector("input, textarea, select");
        (first || confirmBtn).focus();
        if (first && first.select) first.select();
      }, 40);
    });
  }

  function confirmModal(title, message, { danger = false, confirmText = "Lanjutkan", cancelText = "Batal" } = {}) {
    return openModal({
      title,
      bodyHtml: `<p class="muted">${escapeHtml(message)}</p>`,
      confirmText,
      cancelText,
      danger,
    }).then((ok) => Boolean(ok));
  }
  function formatNumber(value) { const number = Number(value) || 0; return numberFmt.format(number); }
  function formatTokens(value) {
    const number = Number(value) || 0;
    if (!number) return "—";
    if (number >= 1000000) return `${(number / 1000000).toFixed(number % 1000000 ? 1 : 0)}jt`;
    if (number >= 1000) return `${Math.round(number / 1000)}rb`;
    return formatNumber(number);
  }
  function formatTime(seconds) {
    if (!seconds) return "—";
    const total = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours) return `${hours}j ${minutes}m`;
    if (minutes) return `${minutes}m ${secs}s`;
    return `${secs}s`;
  }
  function formatDate(timestamp) {
    if (!timestamp) return "—";
    const date = new Date(Number(timestamp) * 1000);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("id-ID", { dateStyle: "medium", timeStyle: "short" });
  }
  function clock(timestamp) {
    if (!timestamp) return "--:--:--";
    const date = new Date(Number(timestamp) * 1000);
    if (Number.isNaN(date.getTime())) return "--:--:--";
    return date.toLocaleTimeString("id-ID", { hour12: false });
  }
  function setText(sel, value) { const el = $(sel); if (el) el.textContent = value; }

  /* ---------------- busy state + animasi loading tombol ---------------- */
  let busyStartedAt = 0;
  let busyToken = 0;
  let busyTimer = 0;
  let busyButton = null;
  let lastActionButton = null;

  // tombol mana yang baru saja diklik (dipakai untuk menandai loading)
  document.addEventListener("click", (event) => {
    const el = event.target instanceof Element ? event.target.closest("button.btn") : null;
    lastActionButton = el || null;
  }, true);

  function pickActionButton(trigger) {
    if (trigger instanceof Element) return trigger.closest("button.btn");
    const active = document.activeElement;
    if (active instanceof Element && active !== document.body) {
      const btn = active.closest("button.btn");
      if (btn) return btn;
    }
    return lastActionButton;
  }

  function setLoading(btn, on) {
    if (!btn) return;
    if (on) {
      if (!btn.classList.contains("is-loading")) {
        btn.setAttribute("data-pre-disabled", btn.disabled ? "1" : "0");
      }
      btn.classList.add("is-loading");
      btn.setAttribute("aria-busy", "true");
      btn.disabled = true;
      return;
    }
    const was = btn.getAttribute("data-pre-disabled") === "1";
    btn.classList.remove("is-loading");
    btn.removeAttribute("aria-busy");
    btn.removeAttribute("data-pre-disabled");
    btn.disabled = was;
  }

  function setBusy(flag, trigger) {
    const next = Boolean(flag);
    if (next) {
      if (!state.busy) {
        state.busy = true;
        document.body.classList.add("is-busy");
      }
      clearTimeout(busyTimer);
      busyToken += 1;
      busyStartedAt = Date.now();
      const btn = pickActionButton(trigger);
      if (btn) { busyButton = btn; setLoading(btn, true); }
      return;
    }
    state.busy = false;
    document.body.classList.remove("is-busy");
    const token = busyToken;
    const btn = busyButton;
    busyButton = null;
    const release = () => {
      if (token !== busyToken) return; // ada proses baru dimulai
      setLoading(btn, false);
      $$(".btn.is-loading").forEach((el) => setLoading(el, false));
      lastActionButton = null;
      // tombol start/stop dikelola status bot, sinkronkan lagi setelah loading
      paintBotStatus(paintBotStatus.last);
    };
    // tampilkan spinner minimal sebentar supaya tidak hanya berkedip
    const wait = Math.max(0, 420 - (Date.now() - busyStartedAt));
    clearTimeout(busyTimer);
    if (wait > 0) busyTimer = window.setTimeout(release, wait);
    else release();
  }
  function showNotice(sel, message) {
    const el = $(sel);
    if (!el) return;
    if (!message) { el.classList.add("hidden"); el.textContent = ""; return; }
    el.textContent = message;
    el.classList.remove("hidden");
  }
  function createIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") window.lucide.createIcons();
  }
  async function api(path, options = {}) {
    const init = { method: options.method || "GET", headers: { Accept: "application/json" }, cache: "no-store" };
    if (options.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(options.body); }
    let response;
    try { response = await fetch(path, init); }
    catch (error) { throw new Error("Tidak bisa menghubungi server lokal."); }
    let payload = null;
    try { payload = await response.json(); } catch (error) { payload = null; }
    if (!response.ok) {
      const message = payload && payload.error && payload.error.message ? payload.error.message : `Permintaan gagal (HTTP ${response.status}).`;
      throw new Error(message);
    }
    return payload || {};
  }
  /* ---------------- searchable selects ---------------- */
  // Mengubah <select> menjadi combobox: klik field -> panel opsi + kotak search di bawahnya.
  // Panel ditempel ke <body> (position: fixed) supaya tidak terpotong overflow panel/card.
  function enhanceSelect(select, placeholder) {
    if (!select || select._combo) return;
    select._combo = true;
    select.classList.add("combo-native");
    const wrap = document.createElement("div");
    wrap.className = "combo";
    wrap.innerHTML = `
      <button type="button" class="combo-toggle" aria-haspopup="listbox" aria-expanded="false">
        <span class="combo-value"></span>
        <i data-lucide="chevron-down" class="combo-caret"></i>
      </button>`;
    select.insertAdjacentElement("afterend", wrap);
    const toggle = wrap.querySelector(".combo-toggle");
    const valueEl = wrap.querySelector(".combo-value");

    const panel = document.createElement("div");
    panel.className = "combo-panel";
    panel.setAttribute("role", "listbox");
    panel.hidden = true;
    const search = document.createElement("input");
    search.type = "text";
    search.className = "combo-search";
    search.placeholder = placeholder || "Cari...";
    search.autocomplete = "off";
    const list = document.createElement("div");
    list.className = "combo-options";
    panel.append(search, list);
    document.body.appendChild(panel);

    const SEARCH_CAP = 200;   // batas hasil saat mengetik
    const DEFAULT_SHOW = 10;  // tanpa ketik: hanya 10 opsi (catalog bisa 500+)

    // posisikan panel mengikuti field; buka ke atas bila ruang bawah kurang
    const place = () => {
      const rect = toggle.getBoundingClientRect();
      const gap = 6;
      const width = Math.max(rect.width, 280);
      const below = window.innerHeight - rect.bottom - gap;
      const above = rect.top - gap;
      const openUp = below < 220 && above > below;
      const available = Math.max(160, Math.min(320, openUp ? above : below));
      panel.style.width = `${Math.min(width, window.innerWidth - 16)}px`;
      panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - Math.min(width, window.innerWidth - 16) - 8))}px`;
      panel.style.maxHeight = `${available}px`;
      if (openUp) {
        panel.style.top = "auto";
        panel.style.bottom = `${window.innerHeight - rect.top + gap}px`;
        panel.classList.add("is-up");
      } else {
        panel.style.bottom = "auto";
        panel.style.top = `${rect.bottom + gap}px`;
        panel.classList.remove("is-up");
      }
    };

    const updateToggle = () => {
      const opt = select.selectedOptions[0];
      const text = (opt && opt.textContent) || placeholder || "Pilih…";
      valueEl.textContent = text;
      valueEl.title = text;
      valueEl.classList.toggle("is-placeholder", !select.value);
    };
    const renderOptions = () => {
      const q = search.value.toLowerCase().trim();
      list.innerHTML = "";
      const matches = Array.from(select.options).filter((o) => !q || !o.value || o.textContent.toLowerCase().includes(q));
      const limit = q ? SEARCH_CAP : DEFAULT_SHOW;
      let shown = matches.slice(0, limit);
      // tanpa query: pastikan opsi yang sedang dipilih tetap terlihat
      if (!q && select.value) {
        const active = matches.find((o) => o.value === select.value);
        if (active && !shown.includes(active)) shown = [active, ...shown.slice(0, limit - 1)];
      }
      const realMatches = shown.filter((o) => o.value).length;
      if (!shown.length || (q && !realMatches)) {
        const empty = document.createElement("div");
        empty.className = "combo-empty";
        empty.textContent = "Tidak ada yang cocok.";
        list.appendChild(empty);
      }
      shown.forEach((o) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "combo-option" + (o.value === select.value ? " is-active" : "");
        item.setAttribute("role", "option");
        // "model.id · owner" -> dua baris supaya nama panjang tidak terpotong
        const text = o.textContent;
        const cut = text.lastIndexOf(" · ");
        const main = document.createElement("span");
        main.className = "combo-option-main";
        main.textContent = cut > 0 ? text.slice(0, cut) : text;
        item.appendChild(main);
        if (cut > 0) {
          const sub = document.createElement("span");
          sub.className = "combo-option-sub";
          sub.textContent = text.slice(cut + 3);
          item.appendChild(sub);
        }
        item.title = text;
        item.addEventListener("click", () => {
          select.value = o.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          updateToggle();
          close();
        });
        list.appendChild(item);
      });
      if (matches.length > shown.length) {
        const more = document.createElement("div");
        more.className = "combo-empty";
        more.textContent = q
          ? `Menampilkan ${shown.length} dari ${matches.length} — persempit pencarian.`
          : `Menampilkan ${shown.length} dari ${matches.length} — ketik untuk mencari sisanya.`;
        list.appendChild(more);
      }
    };
    const isOpen = () => !panel.hidden;
    const open = () => {
      panel.hidden = false;
      wrap.classList.add("is-open");
      toggle.setAttribute("aria-expanded", "true");
      search.value = "";
      place();
      renderOptions();
      search.focus({ preventScroll: true });
    };
    const close = () => {
      if (panel.hidden) return;
      panel.hidden = true;
      wrap.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    };
    const reposition = () => { if (isOpen()) place(); };
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);

    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      if (isOpen()) close(); else open();
    });
    search.addEventListener("input", () => { renderOptions(); list.scrollTop = 0; });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { close(); toggle.focus(); }
      else if (event.key === "ArrowDown") { event.preventDefault(); list.querySelector(".combo-option")?.focus(); }
      else if (event.key === "Enter") {
        const first = list.querySelector(".combo-option");
        if (first) { event.preventDefault(); first.click(); }
      }
    });
    list.addEventListener("keydown", (event) => {
      const items = Array.from(list.querySelectorAll(".combo-option"));
      const idx = items.indexOf(document.activeElement);
      if (event.key === "ArrowDown") { event.preventDefault(); (items[idx + 1] || items[0])?.focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); (items[idx - 1] || search).focus(); }
      else if (event.key === "Escape") { close(); toggle.focus(); }
    });
    // klik di luar field & panel menutup panel (panel ada di <body>, bukan di dalam wrap)
    document.addEventListener("click", (event) => {
      if (wrap.contains(event.target) || panel.contains(event.target)) return;
      close();
    });

    // dipanggil ulang setelah options di-rebuild (fillModelSelect / renderGatewayModels)
    select._comboRefresh = () => {
      updateToggle();
      if (!toggle.offsetParent) close(); // field sedang disembunyikan
      else if (isOpen()) renderOptions();
    };
    updateToggle();
    createIcons();
  }

  function reapplySelectFilters() {
    $$("select.combo-native").forEach((sel) => {
      if (typeof sel._comboRefresh === "function") sel._comboRefresh();
    });
  }

  /* ---------------- tabs & hash ---------------- */
  const TAB_TITLES = { gateway: "Gateway", provider: "AI Provider" };

  function switchTab(tab, updateHash = true) {
    if (!TAB_TITLES[tab]) tab = "gateway";
    state.tab = tab;
    $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.tab === tab));
    $$(".panel").forEach((panel) => panel.classList.toggle("is-active", panel.id === `panel-${tab}`));
    document.title = `${TAB_TITLES[tab]} · Open Gateway`;
    // reset ke list view saat pindah tab via menu
    if (tab === "gateway") {
      state.view.gateway = "list";
      state.selectedGateway = null;
      $("#gateway-detail-view")?.classList.add("hidden");
      $("#gateway-list-view")?.classList.remove("hidden");
    } else {
      state.view.provider = "list";
      state.selectedProvider = null;
      $("#provider-detail-view")?.classList.add("hidden");
      $("#provider-list-view")?.classList.remove("hidden");
    }
    if (updateHash) updateHashUrl();
  }

  function updateHashUrl() {
    let hash = state.tab;
    if (state.tab === "gateway" && state.view.gateway === "detail" && state.selectedGateway) hash = `gateway/${state.selectedGateway}`;
    if (state.tab === "provider" && state.view.provider === "detail" && state.selectedProvider) hash = `provider/${state.selectedProvider}`;
    try { history.replaceState(null, "", `#${hash}`); } catch (e) {}
  }

  function parseHash() {
    const raw = (location.hash || "").replace("#", "").trim();
    const parts = raw.split("/");
    const tab = TAB_TITLES[parts[0]] ? parts[0] : "gateway";
    const id = parts[1] || null;
    return { tab, id };
  }

  /* ---------------- health & status ---------------- */
  function paintHealth(data) {
    const dot = $("#health-dot");
    const text = $("#health-text");
    if (!dot || !text) return;
    const gateways = Array.isArray(data.gateways) ? data.gateways : [];
    const gw = gateways[0] || {};
    const providerOk = data.provider && data.provider.configured;
    dot.classList.remove("ok", "bad", "wait");
    if (gw.state === "running" && gw.ready) { dot.classList.add("ok"); text.textContent = "bot online"; }
    else if (gw.state === "error") { dot.classList.add("bad"); text.textContent = "bot error"; }
    else if (gw.state === "starting" || gw.state === "stopping") { dot.classList.add("wait"); text.textContent = gw.state === "starting" ? "menyambung…" : "berhenti…"; }
    else if (!providerOk) { dot.classList.add("wait"); text.textContent = "provider belum diatur"; }
    else { text.textContent = "bot berhenti"; }
    setText("#app-version", `v${data.version || ""}`);
  }

  function stateLabel(status) {
    if (!status) return "—";
    if (status.ready && status.state === "running") return "online";
    return ({ stopped: "berhenti", starting: "menyambung", running: "online", stopping: "berhenti", error: "error" })[status.state] || status.state || "—";
  }

  function paintBotStatus(status) {
    if (status) paintBotStatus.last = status;
    if (!status) return;
    const label = stateLabel(status);
    const tone = status.state === "error" ? "bad" : status.state === "running" ? "ok" : (status.state === "starting" || status.state === "stopping") ? "warn" : "";
    ["#gw-state", "#gw-state-pill"].forEach((sel) => {
      const el = $(sel);
      if (!el) return;
      el.textContent = label;
      el.className = `badge${tone ? ` ${tone}` : ""}`;
    });
    setText("#gw-user", status.user ? `${status.user}` : "—");
    setText("#gw-guilds", status.user ? formatNumber(status.guilds) : "—");
    setText("#gw-latency", status.latency_ms != null ? `${formatNumber(status.latency_ms)} ms` : "—");
    const since = status.ready_at || status.started_at;
    const uptime = since ? Math.floor(Date.now() / 1000 - Number(since)) : null;
    setText("#gw-uptime", uptime && status.running ? formatTime(uptime) : "—");
    setText("#gw-handled", formatNumber(status.handled || 0));
    setText("#gw-active-model", status.model || "—");
    setText("#gw-internet-state", status.internet ? "aktif" : "nonaktif");
    setText("#gw-image-state", status.image ? "aktif" : "nonaktif");
    const voice = status.voice || {};
    setText("#gw-voice-state", voice.connected ? `#${voice.channel || "voice"}${voice.listening ? " · mendengar" : ""}` : "tidak tersambung");
    setText("#gw-last-error", status.last_error || (voice.last_error ? voice.last_error : "—"));
    const start = $("#gw-start"), stop = $("#gw-stop");
    if (start) start.disabled = state.busy || status.state === "running" || status.state === "starting";
    if (stop) stop.disabled = state.busy || status.state === "stopped";
  }

  async function refreshStatus() {
    try {
      const data = await api("/api/status");
      state.status = data;
      paintHealth(data);
      if (state.selectedGateway) {
        // refresh selected gateway status
        try {
          const gw = await api(`/api/gateways/${state.selectedGateway}`);
          if (gw.status) paintBotStatus(gw.status);
        } catch (e) {}
      }
    } catch (error) {
      const dot = $("#health-dot");
      if (dot) { dot.classList.remove("ok", "wait"); dot.classList.add("bad"); }
      setText("#health-text", "server terputus");
    }
  }

  /* ---------------- gateway list ---------------- */
  function iconFor(type) { return `assets/icon-${type === "nine_router" ? "9router" : type}.svg`; }

  function gatewayForType(typeId) {
    return state.gateways.find((g) => g.type === typeId);
  }

  // icon monokrom (mis. OpenAI) ikut warna tema, sisanya warna brand
  const MONO_ICONS = new Set(["openai"]);
  function iconTag(typeId, src) {
    const mono = MONO_ICONS.has(typeId) ? " icon-mono" : "";
    return `<img class="card-icon${mono}" src="${escapeHtml(src || iconFor(typeId))}" alt="" width="28" height="28" />`;
  }

  function factsHtml(items) {
    const rows = (items || []).filter(Boolean);
    if (!rows.length) return "";
    return `<ul class="card-facts">${rows.map((row) => `<li><span>${row}</span></li>`).join("")}</ul>`;
  }

  const TRIGGER_TEXT = {
    mention: "dipanggil @mention",
    prefix: (g) => `diawali prefix ${escapeHtml(g.prefix || "!ai")}`,
    all: "setiap pesan di channel",
  };

  function gatewayFacts(g) {
    if (!g) return ["Klik card untuk memilih provider, mengisi token, dan menentukan model."];
    const provider = (state.providers || []).find((p) => p.id === g.provider_id);
    const triggerFn = TRIGGER_TEXT[g.trigger] || TRIGGER_TEXT.mention;
    const trigger = typeof triggerFn === "function" ? triggerFn(g) : triggerFn;
    const features = [];
    if (g.internet_enabled) features.push("internet");
    if (g.image_enabled) features.push("gambar");
    if (g.voice_enabled) features.push("suara");
    return [
      `Provider: <b>${provider ? escapeHtml(provider.name) : "belum dipilih"}</b>`,
      `Model aktif: <b>${g.model ? escapeHtml(g.model) : "belum dipilih"}</b>`,
      `Bot menjawab saat ${trigger}`,
      `Token: <b>${g.discord_token_set ? "tersimpan" : "belum diisi"}</b> · Fitur: ${features.length ? escapeHtml(features.join(", ")) : "dasar saja"}`,
    ];
  }

  function renderGatewayCards() {
    const box = $("#gateway-cards");
    if (!box) return;
    // satu card per jenis; status diambil dari instance yang ada (kalau sudah dibuat)
    const cards = (state.gatewayTypes || []).map((t) => {
      const g = gatewayForType(t.id);
      const soon = t.coming_soon || t.available === false;
      const st = (g && g.status) || {};
      const badge = soon
        ? '<span class="badge">segera hadir</span>'
        : g
          ? `<span class="badge ${st.state === "running" ? "ok" : st.state === "error" ? "bad" : ""}">${escapeHtml(stateLabel(st))}</span>`
          : '<span class="badge">belum diatur</span>';
      const desc = t.description || "";
      return `
        <div class="card clickable card-instance${soon ? " is-soon" : ""}" data-gateway-type="${escapeHtml(t.id)}">
          <div class="card-head">
            ${iconTag(t.id, t.icon)}
            <div class="card-title">${escapeHtml((g && g.name) || t.name || t.id)}</div>
          </div>
          <p class="card-desc">${escapeHtml(desc)}</p>
          ${factsHtml(gatewayFacts(soon ? null : g))}
          <div class="card-meta">${badge}</div>
        </div>
      `;
    });
    box.innerHTML = cards.join("");
    box.querySelectorAll("[data-gateway-type]").forEach((el) => {
      el.addEventListener("click", () => openGatewayType(el.dataset.gatewayType));
    });
    createIcons();
  }

  async function openGatewayType(typeId) {
    const t = (state.gatewayTypes || []).find((x) => x.id === typeId);
    if (t && (t.coming_soon || t.available === false)) { toast("Gateway ini segera hadir.", true); return; }
    const existing = gatewayForType(typeId);
    if (existing) { await openGateway(existing.id); return; }
    setBusy(true);
    try {
      const data = await api("/api/gateways", { method: "POST", body: { type: typeId } });
      await loadGatewayList();
      await openGateway(data.settings.id);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(false); }
  }

  async function loadGatewayList() {
    const data = await api("/api/gateways");
    state.gatewayTypes = data.types || [];
    // API mengembalikan {settings, status} per gateway: ratakan agar kartu bisa render.
    state.gateways = (Array.isArray(data.gateways) ? data.gateways : []).map((item) => {
      const settings = item && item.settings ? item.settings : item;
      return { ...(settings || {}), status: item && item.status ? item.status : null };
    });
    renderGatewayCards();
  }

  /* ---------------- gateway detail ---------------- */
  async function openGateway(id) {
    state.selectedGateway = id;
    state.view.gateway = "detail";
    updateHashUrl();
    $("#gateway-list-view").classList.add("hidden");
    $("#gateway-detail-view").classList.remove("hidden");
    try {
      const data = await api(`/api/gateways/${id}`);
      fillGateway(data);
    } catch (error) { toast(error.message, true); }
    initCollapsibles($("#gateway-detail-view"), "gateway");
    loadCronJobs();
    createIcons();
  }

  function showGatewayList() {
    state.selectedGateway = null;
    state.view.gateway = "list";
    updateHashUrl();
    $("#gateway-detail-view").classList.add("hidden");
    $("#gateway-list-view").classList.remove("hidden");
    loadGatewayList().catch(() => {});
  }

  function ensureOption(select, value) {
    if (!value) return;
    const exists = Array.from(select.options).some((option) => option.value === value);
    if (!exists) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = `${value} (di luar daftar)`;
      select.appendChild(option);
    }
  }

  function catalogModels(kind) {
    const list = Array.isArray(state.catalog?.[kind]) ? state.catalog[kind] : [];
    return list.map((item) => (typeof item === "string" ? { id: item, owned_by: "" } : item)).filter((item) => item && item.id);
  }

  function fillModelSelect(selector, kind, saved, emptyLabel) {
    const select = $(selector);
    if (!select) return;
    const current = saved !== undefined && saved !== null ? String(saved) : "";
    const models = catalogModels(kind);
    select.innerHTML = "";
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = emptyLabel;
    select.appendChild(empty);
    models.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.owned_by ? `${model.id} · ${model.owned_by}` : model.id;
      select.appendChild(option);
    });
    if (current && !models.some((model) => model.id === current)) {
      const option = document.createElement("option");
      option.value = current;
      option.textContent = `${current} (di luar daftar)`;
      select.appendChild(option);
    }
    select.value = current;
    reapplySelectFilters();
  }

  function renderFeatureModels(settings) {
    const s = settings || state.gateway || {};
    fillModelSelect("#gw-image-model", "image", s.image_model || "", "Otomatis / ikuti pengaturan provider");
    fillModelSelect("#gw-tts-model", "tts", s.tts_model || "", "Nonaktifkan suara");
    fillModelSelect("#gw-stt-model", "stt", s.stt_model || "", "Nonaktifkan transkripsi");
    fillModelSelect("#gw-web-fetch-model", "web", s.web_fetch_model || "", "Otomatis (pembaca bawaan)");
    reapplySelectFilters();
  }

  function renderGatewayModels(preferred) {
    const select = $("#gw-model");
    if (!select) return;
    const saved = (state.gateway && state.gateway.model) || "";
    const current = preferred !== undefined && preferred !== null ? preferred : (select.value || saved);
    const models = state.models.slice();
    select.innerHTML = "";
    if (!models.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "Belum ada model — buka tab AI Provider";
      select.appendChild(option);
      select.disabled = true;
    } else {
      select.disabled = false;
      models.forEach((model) => {
        const option = document.createElement("option");
        option.value = model.id;
        option.textContent = model.owned_by ? `${model.id} · ${model.owned_by}` : model.id;
        select.appendChild(option);
      });
    }
    if (current) { ensureOption(select, current); select.value = current; }
    else if (models.length) select.value = models[0].id;
    const hint = $("#gw-model-hint");
    if (hint) {
      if (!state.models.length) hint.textContent = "Belum ada model. Buka tab AI Provider untuk memuat daftar model.";
      else hint.textContent = `${formatNumber(state.models.length)} model tersedia dari provider.`;
    }
    reapplySelectFilters();
  }

  async function loadProviderForGateway(providerId) {
    if (!providerId) { state.models = []; state.catalog = {}; return; }
    try {
      const data = await api(`/api/providers/${providerId}`);
      state.models = Array.isArray(data.models) ? data.models : [];
      state.catalog = data.catalog && typeof data.catalog === "object" ? data.catalog : {};
    } catch (e) { state.models = []; state.catalog = {}; }
  }

  function paintTriggerFields() {
    const trigger = $("#gw-trigger")?.value || "mention";
    $("#gw-channel-field")?.classList.toggle("hidden", trigger !== "all");
    $("#gw-prefix-field")?.classList.toggle("hidden", trigger !== "prefix");
  }

  function fillGateway(data) {
    const settings = data.settings || {};
    state.gateway = settings;
    const pid = settings.provider_id || "";
    // populate provider select
    const pselect = $("#gw-provider");
    if (pselect) {
      pselect.innerHTML = "";
      state.providers.forEach((p) => {
        const opt = document.createElement("option");
        opt.value = p.id;
        opt.textContent = `${p.name} (${p.type_info?.name || p.type})`;
        pselect.appendChild(opt);
      });
      if (pid) pselect.value = pid;
      reapplySelectFilters();
    }
    // load models from selected provider
    loadProviderForGateway(pid || (state.providers[0]?.id)).then(() => {
      renderGatewayModels(settings.model);
      renderFeatureModels(settings);
    });

    const token = $("#gw-token");
    if (token) {
      token.value = "";
      token.placeholder = settings.discord_token_set ? `Token tersimpan (${settings.discord_token_hint || "••••"})` : "Masukkan bot token";
    }
    const tokenHint = $("#gw-token-hint");
    if (tokenHint) tokenHint.textContent = settings.discord_token_set ? "Token sudah tersimpan. Kosongkan kolom untuk mempertahankan token lama." : "Token disimpan lokal di folder data.";
    if ($("#gw-trigger")) $("#gw-trigger").value = settings.trigger || "mention";
    if ($("#gw-prefix")) $("#gw-prefix").value = settings.prefix || "!ai";
    if ($("#gw-channel")) $("#gw-channel").value = settings.channel_id || "";
    if ($("#gw-history")) $("#gw-history").value = settings.max_history || 12;
    if ($("#gw-max-tokens")) $("#gw-max-tokens").value = settings.max_tokens || 2048;
    if ($("#gw-system")) $("#gw-system").value = settings.system_prompt || "";
    const check = (selector, value) => { const el = $(selector); if (el) el.checked = Boolean(value); };
    check("#gw-internet", settings.internet_enabled);
    check("#gw-image", settings.image_enabled);
    check("#gw-image-fallback", settings.image_fallback);
    check("#gw-voice", settings.voice_enabled);
    check("#gw-voice-autojoin", settings.voice_autojoin);
    check("#gw-voice-listen", settings.voice_listen);
    if ($("#gw-web-results")) $("#gw-web-results").value = settings.web_max_results || 5;
    if ($("#gw-web-search-model")) $("#gw-web-search-model").value = settings.web_search_model || "";
    if ($("#gw-image-size")) $("#gw-image-size").value = settings.image_size || "1024x1024";
    if ($("#gw-voice-channel")) $("#gw-voice-channel").value = settings.voice_channel_id || "";
    if ($("#gw-voice-language")) $("#gw-voice-language").value = settings.voice_language || "id";
    if ($("#gw-image-token")) { $("#gw-image-token").value = ""; $("#gw-image-token").placeholder = settings.image_token_set ? "Token tersimpan" : "Kosongkan untuk batas gratis"; }
    paintTriggerFields();
    renderModelsForGateway(settings.model);
    if (data.status) paintBotStatus(data.status);
    // coming soon for whatsapp
    const coming = $("#gw-coming-soon");
    const configArea = $("#gw-config-area");
    if (settings.type === "whatsapp") {
      showNotice("#gw-coming-soon", "Gateway WhatsApp segera hadir. Konfigurasi belum tersedia.");
      if (configArea) configArea.classList.add("hidden");
    } else {
      showNotice("#gw-coming-soon", "");
      if (configArea) configArea.classList.remove("hidden");
    }
    $("#gw-detail-title").textContent = settings.name || "Gateway";
  }

  function renderModelsForGateway(model) {
    renderGatewayModels(model);
    renderFeatureModels(state.gateway);
  }

  async function saveGateway(event) {
    if (event) event.preventDefault();
    if (!state.selectedGateway) return;
    const button = $("#gw-save");
    const token = ($("#gw-token")?.value || "").trim();
    const model = $("#gw-model")?.value || "";
    if (state.gateway?.type !== "whatsapp" && !model) { toast("Pilih model AI terlebih dahulu.", true); return; }
    const check = (selector) => Boolean($(selector)?.checked);
    const body = {
      name: state.gateway?.name,
      provider_id: $("#gw-provider")?.value || "",
      model,
      system_prompt: $("#gw-system")?.value || "",
      trigger: $("#gw-trigger")?.value || "mention",
      prefix: $("#gw-prefix")?.value || "!ai",
      channel_id: $("#gw-channel")?.value || "",
      max_history: Number($("#gw-history")?.value || 12),
      max_tokens: Number($("#gw-max-tokens")?.value || 2048),
      internet_enabled: check("#gw-internet"),
      web_search_model: ($("#gw-web-search-model")?.value || "").trim(),
      web_fetch_model: $("#gw-web-fetch-model")?.value || "",
      web_max_results: Number($("#gw-web-results")?.value || 5),
      image_enabled: check("#gw-image"),
      image_model: $("#gw-image-model")?.value || "",
      image_size: $("#gw-image-size")?.value || "1024x1024",
      image_fallback: check("#gw-image-fallback"),
      voice_enabled: check("#gw-voice"),
      voice_channel_id: ($("#gw-voice-channel")?.value || "").trim(),
      voice_autojoin: check("#gw-voice-autojoin"),
      voice_listen: check("#gw-voice-listen"),
      voice_language: ($("#gw-voice-language")?.value || "id").trim(),
      tts_model: $("#gw-tts-model")?.value || "",
      stt_model: $("#gw-stt-model")?.value || "",
    };
    const imageToken = ($("#gw-image-token")?.value || "").trim();
    if (imageToken) body.image_token = imageToken;
    if (token) body.discord_token = token;
    setBusy(true);
    if (button) button.disabled = true;
    showNotice("#gw-alert", "");
    try {
      const data = await api(`/api/gateways/${state.selectedGateway}`, { method: "POST", body });
      fillGateway(data);
      paintBotStatus(data.status);
      toast("Konfigurasi gateway disimpan.");
      await refreshLogs();
    } catch (error) {
      showNotice("#gw-alert", error.message);
      toast(error.message, true);
    } finally {
      setBusy(false);
      if (button) button.disabled = false;
      paintBotStatus(state.status?.gateway || data?.status);
    }
  }

  async function botAction(action) {
    if (state.busy || !state.selectedGateway) return;
    const buttons = [$("#gw-start"), $("#gw-stop"), $("#gw-restart")];
    setBusy(true);
    buttons.forEach((el) => { if (el) el.disabled = true; });
    showNotice("#gw-alert", "");
    try {
      const data = await api(`/api/gateways/${state.selectedGateway}/${action}`, { method: "POST" });
      if (data.status) { if (state.status) state.status.gateway = data.status; paintBotStatus(data.status); }
      const labels = { start: "Bot Discord dijalankan.", stop: "Bot Discord dihentikan.", restart: "Bot Discord dijalankan ulang." };
      toast(labels[action] || "Perintah dijalankan.");
    } catch (error) {
      showNotice("#gw-alert", error.message);
      toast(error.message, true);
    } finally {
      setBusy(false);
      paintBotStatus(state.status?.gateway);
      await refreshStatus();
      await refreshLogs();
    }
  }

  async function deleteGateway() {
    if (!state.selectedGateway) return;
    if (!(await confirmModal("Hapus gateway ini?", "Gateway dan semua konfigurasinya akan dihapus. Lanjutkan?", { danger: true }))) return;
    setBusy(true);
    try {
      await api(`/api/gateways/${state.selectedGateway}`, { method: "DELETE" });
      toast("Gateway dihapus.");
      showGatewayList();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(false); }
  }

  /* ---------------- collapsible cards & cron ---------------- */
  const COLLAPSE_STORAGE = "open-gateway-card-state";

  function cardKey(view, card) {
    const title = (card.querySelector(".card-title") || card).textContent.trim();
    return `collapsed:${view}:${title || "card"}`;
  }

  function loadCollapseMap(view) {
    try {
      const raw = localStorage.getItem(COLLAPSE_STORAGE);
      return (raw ? JSON.parse(raw) : {})[view] || {};
    } catch (_) { return {}; }
  }

  function saveCollapseMap(view, map) {
    let all = {};
    try { all = JSON.parse(localStorage.getItem(COLLAPSE_STORAGE) || "{}"); } catch (_) {}
    all[view] = map;
    localStorage.setItem(COLLAPSE_STORAGE, JSON.stringify(all));
  }

  function buildToggle() {
    const btn = document.createElement("button");
    btn.className = "card-toggle";
    btn.type = "button";
    btn.setAttribute("aria-label", "Lipat card");
    btn.setAttribute("aria-expanded", "true");
    btn.innerHTML = '<i data-lucide="chevron-down"></i>';
    return btn;
  }

  function makeCollapsible(card, view) {
    if (card._collapsible) return;
    card._collapsible = true;
    // reuse existing head bar if present, else synthesize from first .card-title/.row or first child
    let bar = card.querySelector(".card-head-bar");
    if (!bar) {
      const src = card.querySelector(".card-title") || card.querySelector(".row");
      bar = document.createElement("div");
      bar.className = "card-head-bar";
      if (src && src.parentElement === card) {
        src.before(bar);
        bar.appendChild(src);
      } else {
        const span = document.createElement("span");
        span.textContent = src ? src.textContent.trim() : "Detail";
        bar.appendChild(span);
        card.insertBefore(bar, card.firstElementChild);
      }
    }
    let toggle = bar.querySelector(".card-toggle");
    if (!toggle) {
      toggle = buildToggle();
      bar.appendChild(toggle);
    }
    const wire = (event) => {
      event.stopPropagation();
      const collapsed = !card.classList.contains("collapsed");
      card.classList.toggle("collapsed", collapsed);
      toggle.setAttribute("aria-expanded", String(!collapsed));
      const map = loadCollapseMap(view);
      map[cardKey(view, card)] = collapsed;
      saveCollapseMap(view, map);
      createIcons();
    };
    toggle.replaceWith(toggle); // no-op safety
    toggle.onclick = null;
    toggle.addEventListener("click", wire);
    // ensure a body wraps non-head content
    let body = card.querySelector(".card-body");
    if (!body) {
      body = document.createElement("div");
      body.className = "card-body";
      const kids = [...card.children].filter((child) => !child.classList.contains("card-head-bar"));
      kids.forEach((child) => body.appendChild(child));
      card.appendChild(body);
    }
    // restore persisted state
    const map = loadCollapseMap(view);
    if (map[cardKey(view, card)]) {
      card.classList.add("collapsed");
      toggle.setAttribute("aria-expanded", "false");
    } else {
      card.classList.remove("collapsed");
      toggle.setAttribute("aria-expanded", "true");
    }
  }

  function initCollapsibles(scope, view) {
    if (!scope) return;
    scope.querySelectorAll(".card").forEach((card) => makeCollapsible(card, view));
    // form-section toggles (head already has .card-toggle in markup)
    scope.querySelectorAll(".form-section").forEach((section) => {
      if (section._collapsible) return;
      section._collapsible = true;
      const toggle = section.querySelector(".form-section-head .card-toggle");
      if (!toggle) return;
      if (!toggle._wired) {
        toggle._wired = true;
        toggle.addEventListener("click", (event) => {
          event.stopPropagation();
          section.classList.toggle("collapsed");
          createIcons();
        });
      }
      // initialize collapsed state to expanded
      section.classList.remove("collapsed");
    });
    createIcons();
  }

  /* ---------------- cron jobs (UI management) ---------------- */
  function renderCronJobs(jobs) {
    state.cronJobs = jobs || [];
    const box = $("#gw-cron-list");
    if (!box) return;
    if (!state.cronJobs.length) {
      box.innerHTML = '<p class="muted small">Belum ada cron job. Tekan "Tambah" untuk menjadwalkan pesan otomatis.</p>';
      return;
    }
    const rows = state.cronJobs.map((j) => {
      const enabled = j.enabled !== false;
      const next = j.next_run ? formatDate(j.next_run) : "—";
      return `<tr data-job-id="${escapeHtml(j.id)}">
        <td class="mono break" title="ID">${escapeHtml(j.id)}</td>
        <td><span class="badge ${enabled ? "ok" : ""}">${enabled ? "aktif" : "mati"}</span></td>
        <td class="mono break">${escapeHtml(j.schedule)}</td>
        <td class="muted nowrap">${j.created_at ? formatDate(j.created_at) : "—"}</td>
        <td class="muted nowrap">${next}</td>
        <td class="muted small">${j.last_status ? escapeHtml(String(j.last_status).slice(0, 100)) : "—"}</td>
        <td class="nowrap">
          <button class="btn ghost small icon-only" title="${enabled ? "Nonaktifkan" : "Aktifkan"}"><i data-lucide="${enabled ? "pause-circle" : "play"}"></i></button>
          <button class="btn ghost small icon-only" title="Edit"><i data-lucide="edit"></i></button>
          <button class="btn ghost small icon-only" title="Jalankan sekarang"><i data-lucide="play"></i></button>
          <button class="btn ghost small icon-only danger" title="Hapus"><i data-lucide="trash-2"></i></button>
        </td>
      </tr>`;
    }).join("");
    box.innerHTML = `<table class="cron-table">
      <thead><tr><th>ID</th><th>Status</th><th>Jadwal</th><th>Dibuat</th><th>Berikutnya</th><th>Status log</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>`;
    box.querySelectorAll("tr[data-job-id]").forEach((row) => {
      const id = row.dataset.jobId;
      const cells = row.querySelectorAll("button");
      cells[0]?.addEventListener("click", () => toggleCron(id));
      cells[1]?.addEventListener("click", () => editCron(id));
      cells[2]?.addEventListener("click", () => runCron(id));
      cells[3]?.addEventListener("click", () => deleteCron(id));
    });
    createIcons();
  }

  async function loadCronJobs() {
    if (!state.selectedGateway) return;
    try {
      const data = await api(`/api/gateways/${state.selectedGateway}/cron`);
      renderCronJobs(data.cron_jobs || []);
    } catch (error) {
      showNotice("#gw-cron-alert", error.message);
    }
  }

  async function addCron() {
    const result = await openCronModal();
    if (!result) return;
    showNotice("#gw-cron-alert", "");
    try {
      await api(`/api/gateways/${state.selectedGateway}/cron`, { method: "POST", body: result });
      loadCronJobs();
      toast("Cron job ditambahkan.");
    } catch (error) {
      showNotice("#gw-cron-alert", error.message);
      toast(error.message, true);
    }
  }

  async function editCron(id) {
    const job = (state.cronJobs || []).find((j) => j.id === id);
    if (!job) return;
    const result = await openCronModal(job);
    if (!result) return;
    showNotice("#gw-cron-alert", "");
    try {
      await api(`/api/gateways/${state.selectedGateway}/cron/${encodeURIComponent(id)}`, {
        method: "PUT", body: { schedule: result.schedule, prompt: result.prompt, channel_id: result.channel_id },
      });
      loadCronJobs();
    } catch (error) { showNotice("#gw-cron-alert", error.message); }
  }

  // day-of-week: 0=Minggu … 6=Sabtu (sesuai cron)
  const DAY_NAMES = ["Minggu", "Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu"];

  function cronDowSelected(dowField, idx) {
    if (dowField == null || dowField === "*" || dowField === "*/1" || dowField === "?") return true;
    return String(dowField).split(",").map((s) => s.trim()).filter(Boolean).includes(String(idx));
  }

  function parseCron(expr) {
    const parts = (expr || "").trim().split(/\s+/);
    if (parts.length < 5) return { minute: 0, hour: 9, dow: null };
    return { minute: Number(parts[0]) || 0, hour: Number(parts[1]) || 0, dow: parts[4] };
  }

  async function openCronModal(job = null) {
    const isEdit = Boolean(job);
    const def = isEdit ? parseCron(job.schedule) : { minute: 0, hour: 9, dow: null };
    const defEvery = def.dow == null || ["*", "*/1", "?"].includes(def.dow);

    const bodyHtml = `
      <div class="cron-form">
        <label class="field span-2"><span>Waktu</span><input type="time" name="time" value="${String(def.hour).padStart(2, "0")}:${String(def.minute).padStart(2, "0")}" /></label>
        <label class="field span-2"><span>Hari</span>
          <div class="cron-days">
            <label class="row gap"><input type="checkbox" name="every" value="1" ${defEvery ? "checked" : ""} /><span>Setiap hari</span></label>
            ${DAY_NAMES.map((d, i) => `<label class="row gap"><input type="checkbox" name="dow" value="${i}" ${cronDowSelected(def.dow, i) ? "checked" : ""} />${d}</label>`).join("")}
          </div>
        </label>
        <label class="field span-2"><span>Prompt / perintah</span><textarea name="prompt" rows="3" placeholder="Apa yang akan dikirim bot?">${escapeHtml((job && job.prompt) || "")}</textarea></label>
        <label class="field span-2"><span>Channel ID</span><input type="text" name="channel_id" value="${escapeHtml((job && job.channel_id) || "")}" placeholder="mis. 123456789012345678" /></label>
      </div>`;

    const form = await openModal({
      title: isEdit ? "Edit cron job" : "Tambah cron job",
      bodyHtml,
      confirmText: "Simpan",
      onMount: (body) => {
        const everyBox = body.querySelector('input[name="every"]');
        const dowBoxes = () => body.querySelectorAll('input[name="dow"]');
        if (everyBox) {
          const sync = () => {
            const on = everyBox.checked;
            dowBoxes().forEach((b) => {
              b.disabled = on;
              b.closest("label").classList.toggle("muted", on);
            });
          };
          everyBox.addEventListener("change", sync);
          sync();
        }
      },
    });
    if (!form) return null;

    const timeParts = String(form.time || "").split(":");
    const hour = Number(timeParts[0] || 0);
    const minute = Number(timeParts[1] || 0);
    if (Number.isNaN(hour) || Number.isNaN(minute)) return null;

    const every = form.every && form.every !== "";
    const bodyEl = $("#modal-body");
    const checked = [...(bodyEl ? bodyEl.querySelectorAll('input[name="dow"]:checked') : [])].map((el) => el.value);
    let schedule;
    if (every || checked.length === 0 || checked.length === 7) {
      schedule = `${minute} ${hour} * * *`;
    } else {
      schedule = `${minute} ${hour} * * ${checked.join(",")}`;
    }

    return {
      schedule,
      prompt: (form.prompt || "").trim(),
      channel_id: (form.channel_id || "").replace(/[<#> >]/g, "").trim(),
    };
  }

  async function toggleCron(id) {
    const job = (state.cronJobs || []).find((j) => j.id === id);
    if (!job) return;
    try {
      await api(`/api/gateways/${state.selectedGateway}/cron/${encodeURIComponent(id)}`, {
        method: "PUT", body: { enabled: !job.enabled },
      });
      loadCronJobs();
    } catch (error) { showNotice("#gw-cron-alert", error.message); }
  }

  async function deleteCron(id) {
    if (!(await confirmModal("Hapus cron job ini?", `Job ${id} akan dihapus secara permanen. Lanjutkan?`, { danger: true }))) return;
    try {
      await api(`/api/gateways/${state.selectedGateway}/cron/${encodeURIComponent(id)}`, { method: "DELETE" });
      loadCronJobs();
      toast("Cron job dihapus.");
    } catch (error) { showNotice("#gw-cron-alert", error.message); }
  }

  async function runCron(id) {
    try {
      const data = await api(`/api/gateways/${state.selectedGateway}/cron/${encodeURIComponent(id)}/run`, { method: "POST" });
      toast(data.ok ? "Cron dijalankan." : "Cron gagal.", !data.ok);
      showNotice("#gw-cron-alert", (data && data.detail) || "");
      loadCronJobs();
    } catch (error) { showNotice("#gw-cron-alert", error.message); }
  }

  /* ---------------- logs ---------------- */
  function paintLogs(logs) {
    const box = $("#gw-logs");
    if (!box) return;
    if (!logs.length) { box.innerHTML = '<p class="muted small">Belum ada aktivitas.</p>'; return; }
    box.innerHTML = logs.map((log) => {
      const level = String(log.level || "info").toLowerCase();
      return `<div class="log-row log-${escapeHtml(level)}">
        <span class="log-time mono">${clock(log.time)}</span>
        <span class="log-level">${escapeHtml(level)}</span>
        <span class="log-message">${escapeHtml(log.message)}</span>
      </div>`;
    }).join("");
  }
  async function refreshLogs() {
    if (!state.selectedGateway) return;
    try {
      const data = await api(`/api/gateways/${state.selectedGateway}/logs`);
      paintLogs(data.logs || []);
    } catch (error) {}
  }
  async function clearLogs() {
    if (!state.selectedGateway) return;
    setBusy(true);
    try { await api(`/api/gateways/${state.selectedGateway}/logs/clear`, { method: "POST" }); paintLogs([]); toast("Log dibersihkan."); }
    catch (error) { toast(error.message, true); }
    finally { setBusy(false); }
  }

  /* ---------------- provider list ---------------- */
  function providerForType(typeId) {
    return state.providers.find((p) => p.type === typeId);
  }

  function providerFacts(p, t) {
    if (!p) {
      const facts = ["Klik card untuk mengisi Base URL dan API key."];
      if (t && t.default_base_url) facts.push(`Base URL bawaan: <b>${escapeHtml(t.default_base_url)}</b>`);
      return facts;
    }
    const kinds = ((t && t.kinds) || ["chat"]).join(", ");
    const keyState = p.api_key_set
      ? `tersimpan${p.api_key_hint ? ` (${escapeHtml(p.api_key_hint)})` : ""}`
      : "belum diisi";
    const facts = [
      `Base URL: <b>${escapeHtml(p.base_url || "belum diisi")}</b>`,
      `API key: <b>${keyState}</b>`,
      `Dukungan endpoint: ${escapeHtml(kinds)}`,
    ];
    if (p.updated_at) facts.push(`Sinkronisasi model terakhir: ${escapeHtml(formatDate(p.updated_at))}`);
    else facts.push("Belum pernah sinkronisasi model — simpan untuk menarik katalog.");
    return facts;
  }

  function renderProviderCards() {
    const box = $("#provider-cards");
    if (!box) return;
    // satu card per jenis provider
    const cards = (state.providerTypes || []).map((t) => {
      const p = providerForType(t.id);
      const badge = p
        ? `<span class="badge">${formatNumber(p.model_count || 0)} model</span>`
        : '<span class="badge">belum diatur</span>';
      const configNote = p
        ? `<span class="muted small">${p.configured ? "terkonfigurasi" : "belum lengkap"}</span>`
        : "";
      return `
        <div class="card clickable card-instance" data-provider-type="${escapeHtml(t.id)}">
          <div class="card-head">
            ${iconTag(t.id, t.icon)}
            <div class="card-title">${escapeHtml((p && p.name) || t.name || t.id)}</div>
          </div>
          <p class="card-desc">${escapeHtml(t.description || "")}</p>
          ${factsHtml(providerFacts(p, t))}
          <div class="card-meta">${badge}${configNote}</div>
        </div>
      `;
    });
    box.innerHTML = cards.join("");
    box.querySelectorAll("[data-provider-type]").forEach((el) => {
      el.addEventListener("click", () => openProviderType(el.dataset.providerType));
    });
    createIcons();
  }

  async function openProviderType(typeId) {
    const existing = providerForType(typeId);
    if (existing) { await openProvider(existing.id); return; }
    setBusy(true);
    try {
      const data = await api("/api/providers", { method: "POST", body: { type: typeId, fetch: false } });
      await loadProviderList();
      await openProvider(data.provider.id);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(false); }
  }

  async function loadProviderList() {
    const data = await api("/api/providers");
    state.providerTypes = data.types || [];
    state.providers = data.providers || [];
    renderProviderCards();
  }

  /* ---------------- provider detail ---------------- */
  function fillProviderForm(data) {
    state.provider = data;
    state.models = Array.isArray(data.models) ? data.models : [];
    state.catalog = data.catalog && typeof data.catalog === "object" ? data.catalog : {};
    const base = $("#pv-base"), key = $("#pv-key");
    if (base && document.activeElement !== base) base.value = data.base_url || "";
    if (key && document.activeElement !== key) key.value = "";
    if (key) key.placeholder = data.api_key_set ? `Tersimpan (${data.api_key_hint || "••••"})` : "sk-...";
    updateProviderMeta(data);
    renderModels();
    renderGatewayModelsForProvider();
  }

  function updateProviderMeta(provider) {
    if (!provider) return;
    const bits = [];
    if (provider.model_count || provider.models) bits.push(`${formatNumber(provider.model_count || (provider.models?.length || 0))} model`);
    bits.push(provider.updated_at ? `diperbarui ${formatDate(provider.updated_at)}` : "belum ada sinkronisasi");
    setText("#pv-meta", bits.join(" • "));
    setText("#pv-count", provider.model_count || (provider.models?.length || 0) ? `(${formatNumber(provider.model_count || provider.models?.length || 0)})` : "");
  }

  function renderModels() {
    const tbody = $("#pv-models");
    if (!tbody) return;
    const query = ($("#pv-filter")?.value || "").trim().toLowerCase();
    const models = state.models.filter((model) => {
      if (!query) return true;
      return `${model.id} ${model.owned_by}`.toLowerCase().includes(query);
    });
    setText("#pv-count", state.models.length ? `(${formatNumber(models.length)}/${formatNumber(state.models.length)})` : "");
    if (!state.models.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">Belum ada model. Simpan Base URL dan API key untuk memuat daftar model.</td></tr>`;
      return;
    }
    if (!models.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">Tidak ada model yang cocok dengan "${escapeHtml(query)}".</td></tr>`;
      return;
    }
    const activeGatewayModel = state.gateway ? state.gateway.model : "";
    // daftar model bisa 500+: batasi render supaya tampilan tetap rapi
    const rowCap = query ? 200 : 40;
    const rows = models.slice(0, rowCap);
    tbody.innerHTML = rows.map((model) => {
      const features = [];
      if (model.vision) features.push('<span class="badge">vision</span>');
      if (model.tools) features.push('<span class="badge">tools</span>');
      if (model.reasoning) features.push('<span class="badge">reasoning</span>');
      const used = model.id === activeGatewayModel;
      return `<tr${used ? ' class="is-used"' : ""}>
        <td class="mono break">${escapeHtml(model.id)}</td>
        <td class="muted">${escapeHtml(model.owned_by || "—")}</td>
        <td class="muted nowrap">${formatTokens(model.context_length)}</td>
        <td><div class="chips">${features.join("") || '<span class="muted">—</span>'}</div></td>
        <td class="right"><button class="btn ghost small" type="button" data-pick="${escapeHtml(model.id)}">${used ? "Dipakai" : "Pakai"}</button></td>
      </tr>`;
    }).join("") + (models.length > rows.length
      ? `<tr><td colspan="5" class="muted small">Menampilkan ${rows.length} dari ${formatNumber(models.length)} model — ${query ? "persempit pencarian" : "ketik di kolom cari"} untuk melihat sisanya.</td></tr>`
      : "");
  }

  function renderGatewayModelsForProvider() {
    // if a gateway is open, refresh its model select
    if (state.selectedGateway && state.gateway) {
      // reload models from provider
      const pid = state.gateway.provider_id || state.provider?.id;
      loadProviderForGateway(pid).then(() => {
        renderGatewayModels(state.gateway.model);
        renderFeatureModels(state.gateway);
      });
    }
  }

  async function openProvider(id) {
    state.selectedProvider = id;
    state.view.provider = "detail";
    updateHashUrl();
    $("#provider-list-view").classList.add("hidden");
    $("#provider-detail-view").classList.remove("hidden");
    try {
      const data = await api(`/api/providers/${id}`);
      fillProviderForm(data);
    } catch (error) { toast(error.message, true); }
    initCollapsibles($("#provider-detail-view"), "provider");
    createIcons();
  }

  function showProviderList() {
    state.selectedProvider = null;
    state.view.provider = "list";
    updateHashUrl();
    $("#provider-detail-view").classList.add("hidden");
    $("#provider-list-view").classList.remove("hidden");
    loadProviderList().catch(() => {});
  }

  async function saveProvider(event) {
    if (event) event.preventDefault();
    if (!state.selectedProvider) return;
    const baseUrl = ($("#pv-base")?.value || "").trim();
    const apiKey = ($("#pv-key")?.value || "").trim();
    if (!baseUrl) { toast("Base URL provider wajib diisi.", true); $("#pv-base")?.focus(); return; }
    const button = $("#pv-save");
    const formButton = $("#pv-form button[type=submit]");
    setBusy(true);
    [button, formButton].forEach((el) => { if (el) el.disabled = true; });
    showNotice("#pv-warning", "");
    try {
      const data = await api(`/api/providers/${state.selectedProvider}`, {
        method: "POST",
        body: { base_url: baseUrl, api_key: apiKey, fetch: true },
      });
      fillProviderForm(data.provider);
      toast(data.warning ? "Koneksi tersimpan, tetapi model gagal dimuat." : `${formatNumber(state.models.length)} model dimuat.`);
      if (data.warning) showNotice("#pv-warning", data.warning);
      await loadProviderList();
      await refreshStatus();
    } catch (error) {
      toast(error.message, true);
      showNotice("#pv-warning", error.message);
    } finally {
      setBusy(false);
      [button, formButton].forEach((el) => { if (el) el.disabled = false; });
    }
  }

  async function refreshModels() {
    if (!state.selectedProvider) return;
    const button = $("#pv-refresh");
    setBusy(true);
    if (button) button.disabled = true;
    showNotice("#pv-warning", "");
    try {
      const data = await api(`/api/providers/${state.selectedProvider}/refresh`, { method: "POST" });
      fillProviderForm(data.provider);
      if (data.warning) { showNotice("#pv-warning", data.warning); toast("Model gagal dimuat ulang.", true); }
      else toast(`${formatNumber(state.models.length)} model dimuat ulang.`);
      await loadProviderList();
      await refreshStatus();
    } catch (error) {
      showNotice("#pv-warning", error.message);
      toast(error.message, true);
    } finally {
      setBusy(false);
      if (button) button.disabled = false;
    }
  }

  async function pickModel(modelId) {
    if (!modelId || !state.selectedGateway) return;
    const select = $("#gw-model");
    if (select) { ensureOption(select, modelId); select.value = modelId; }
    if (state.gateway) state.gateway.model = modelId;
    setBusy(true);
    try {
      const data = await api(`/api/gateways/${state.selectedGateway}`, { method: "POST", body: { model: modelId } });
      state.gateway = data.settings;
      toast(`Model aktif: ${modelId}`);
    } catch (error) { toast(error.message, true); }
    finally { setBusy(false); renderModels(); }
  }

  async function deleteProvider() {
    if (!state.selectedProvider) return;
    if (!(await confirmModal("Hapus provider ini?", "Semua model terhubung akan diputus. Lanjutkan?", { danger: true }))) return;
    setBusy(true);
    try {
      await api(`/api/providers/${state.selectedProvider}`, { method: "DELETE" });
      toast("Provider dihapus.");
      showProviderList();
      await loadProviderList();
    } catch (error) { toast(error.message, true); }
    finally { setBusy(false); }
  }

  /* ---------------- wiring ---------------- */
  function wire() {
    $$(".nav-item").forEach((item) => item.addEventListener("click", () => switchTab(item.dataset.tab)));
    window.addEventListener("hashchange", handleHash);

    // gateway
    $("#gw-back")?.addEventListener("click", showGatewayList);
    $("#gw-form")?.addEventListener("submit", saveGateway);
    $("#gw-trigger")?.addEventListener("change", paintTriggerFields);
    $("#gw-start")?.addEventListener("click", () => botAction("start"));
    $("#gw-stop")?.addEventListener("click", () => botAction("stop"));
    $("#gw-restart")?.addEventListener("click", () => botAction("restart"));
    $("#gw-clear-logs")?.addEventListener("click", clearLogs);
    $("#gw-delete")?.addEventListener("click", deleteGateway);
    $("#gw-cron-add")?.addEventListener("click", addCron);
    $("#gw-provider")?.addEventListener("change", async (e) => {
      const pid = e.target.value;
      await loadProviderForGateway(pid);
      renderGatewayModels("");
      renderFeatureModels(state.gateway);
    });

    // searchable comboboxes (select + search jadi satu)
    [["#gw-provider", "Cari provider..."],
     ["#gw-model", "Cari model (mis. gpt, claude, deepseek)"],
     ["#gw-web-fetch-model", "Cari model..."],
     ["#gw-image-model", "Cari model..."],
     ["#gw-tts-model", "Cari model..."],
     ["#gw-stt-model", "Cari model..."]].forEach(([sel, ph]) => {
      const el = $(sel);
      if (el) enhanceSelect(el, ph);
    });

    // provider
    $("#pv-back")?.addEventListener("click", showProviderList);
    $("#pv-form")?.addEventListener("submit", saveProvider);
    $("#pv-save")?.addEventListener("click", saveProvider);
    $("#pv-refresh")?.addEventListener("click", refreshModels);
    $("#pv-filter")?.addEventListener("input", renderModels);
    $("#pv-delete")?.addEventListener("click", deleteProvider);
    $("#pv-models")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-pick]");
      if (button) pickModel(button.dataset.pick);
    });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { refreshStatus(); refreshLogs(); }
    });
  }

  function handleHash() {
    const { tab, id } = parseHash();
    switchTab(tab, false);
    if (tab === "gateway") {
      if (id && state.view.gateway !== "detail") openGateway(id);
      else if (!id && state.view.gateway !== "list") showGatewayList();
    } else if (tab === "provider") {
      if (id && state.view.provider !== "detail") openProvider(id);
      else if (!id && state.view.provider !== "list") showProviderList();
    }
  }

  function startTimers() {
    clearInterval(state.statusTimer);
    clearInterval(state.logTimer);
    state.statusTimer = setInterval(() => { if (!document.hidden) refreshStatus(); }, 7000);
    state.logTimer = setInterval(() => { if (!document.hidden) refreshLogs(); }, 7000);
  }

  async function boot() {
    wire();
    const { tab, id } = parseHash();
    switchTab(tab, false);
    createIcons();

    await Promise.allSettled([loadProviderList(), loadGatewayList(), refreshStatus()]);
    createIcons();
    // if hash has id, open detail
    if (tab === "gateway" && id) { await openGateway(id); }
    else if (tab === "provider" && id) { await openProvider(id); }
    startTimers();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
