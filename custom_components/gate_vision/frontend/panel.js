/* gate-vision — панель Home Assistant: зоны, пороги, реагирования, журнал.
 * Плагин самодостаточный: обычный custom element, без внешних зависимостей.
 */

const ROLE_COLORS = { closed: "#22c55e", open: "#f59e0b", ignore: "#64748b" };
// цвет контура зоны по фактическому состоянию: открыто — красный, закрыто — зелёный
const STATE_COLORS = { open: "#ef4444", closed: "#22c55e", unknown: "#f59e0b" };
const STATE_TITLES = { open: "ОТКРЫТО", closed: "ЗАКРЫТО", unknown: "?" };
const ROLE_TITLES = { closed: "Закрыто / Выключено", open: "Открыто / Включено", ignore: "Исключение" };

const THRESHOLD_META = [
  ["street_low_t", "Порог «открыто» днём", 0, 255, 1],
  ["dark_low_t", "Порог «открыто» ночью/ИК", 0, 255, 1],
  ["bright", "«Светло» (пересвет окон)", 100, 255, 1],
  ["dark_band", "Окна ночью (тёмная полоса)", 20, 140, 5],
  ["frame_t", "Зажато тёмным (доля)", 0.2, 0.95, 0.05],
  ["min_band_f", "Мин. высота полосы окон", 0.01, 0.3, 0.01],
  ["max_band_f", "Макс. высота полосы окон", 0.2, 0.9, 0.05],
  ["gray_sat", "Порог цвет/ч-б", 0.001, 0.1, 0.005],
  ["move_diff", "Порог движения полотна", 1, 40, 1],
];

const EVENTS = [
  ["opened", "Ворота открылись"],
  ["closed", "Ворота закрылись"],
  ["left_open", "Оставлены открытыми"],
  ["moving", "Движение полотна"],
  ["unknown", "Состояние не определяется"],
  ["camera_lost", "Камера недоступна"],
  ["camera_back", "Камера снова доступна"],
];

const CHANNELS = [
  ["notify", "Push (notify)"],
  ["persistent", "Уведомление в HA"],
  ["tts", "Озвучка (TTS)"],
  ["script", "Скрипт/служба"],
  ["mqtt", "MQTT"],
  ["webhook", "Webhook"],
];

class GateVisionPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._panel = null;
    this._tab = "cams";
    this._state = null;
    this._settings = null;
    this._interlocks = null;
    this._frameUrl = null;
    this._live = true;
    this._timer = null;
    this._selectedZone = null;
    this._drag = null;
    this._newRole = "open";
    this._dirty = false;
    this._zoneSaveTimer = null;
    this._cams = [];
    this._activeEntry = null;
    this._gridCols = 2;
    this._tileTimers = [];
    this._log = [];
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._boot();
    }
  }

  get hass() {
    return this._hass;
  }

  set panel(panel) {
    this._panel = panel;
  }

  set narrow(narrow) {
    this._narrow = narrow;
  }

  connectedCallback() {
    if (!this._root) this._build();
  }

  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
  }

  /* ------------------------------------------------------------------ каркас */
  _build() {
    this._root = document.createElement("div");
    this._root.className = "gv-root";
    this._root.innerHTML = `
      <style>
        .gv-root { padding: 16px; font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
                   color: var(--primary-text-color); }
        .gv-head { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin-bottom:12px; }
        .gv-badge { padding:6px 14px; border-radius:20px; font-weight:600; font-size:15px; color:#fff; }
        .gv-badge.closed { background:#15803d; }
        .gv-badge.open { background:#b45309; }
        .gv-badge.unknown { background:#64748b; }
        .gv-meta { font-size:13px; opacity:.8; }
        .gv-btn { background: var(--primary-color); color:#fff; border:none; border-radius:6px;
                  padding:7px 14px; cursor:pointer; font-size:14px; }
        .gv-btn.secondary { background: var(--secondary-background-color); color: var(--primary-text-color);
                            border:1px solid var(--divider-color); }
        .gv-btn.danger { background:#b91c1c; }
        .gv-btn:hover { filter: brightness(1.08); }
        .gv-grid { display:grid; grid-template-columns: minmax(320px, 1fr) minmax(340px, 460px); gap:16px; }
        @media (max-width: 1100px) { .gv-grid { grid-template-columns: 1fr; } }
        .gv-card { background: var(--card-background-color); border-radius:10px; padding:12px;
                   box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.2)); }
        .gv-canvas-wrap { position:relative; background:#111; border-radius:8px; overflow:hidden; }
        canvas { display:block; width:100%; cursor:crosshair; touch-action:none; }
        .gv-tools { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:10px 0; font-size:13px; }
        .gv-tools select, .gv-tools input { background: var(--secondary-background-color);
            color: var(--primary-text-color); border:1px solid var(--divider-color); border-radius:6px; padding:5px 8px; }
        .gv-zonelist { display:flex; flex-direction:column; gap:6px; margin-top:8px; }
        .gv-zone { display:flex; align-items:center; gap:8px; font-size:13px; padding:5px 8px; border-radius:6px;
                   background: var(--secondary-background-color); cursor:pointer; }
        .gv-zone.sel { outline:2px solid var(--primary-color); }
        .gv-dot { width:12px; height:12px; border-radius:3px; flex:0 0 auto; }
        .gv-tabs { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px; }
        .gv-tab { padding:7px 12px; border-radius:6px 6px 0 0; cursor:pointer; font-size:14px;
                  background: var(--secondary-background-color); border:1px solid var(--divider-color); border-bottom:none; }
        .gv-tab.active { background: var(--primary-color); color:#fff; border-color: var(--primary-color); }
        .gv-row { display:flex; align-items:center; gap:10px; margin:7px 0; font-size:13px; }
        .gv-row label { flex:1 1 auto; }
        .gv-row input[type=range] { flex: 1 1 140px; }
        .gv-row input[type=text], .gv-row input[type=number] { flex:1 1 140px;
            background: var(--secondary-background-color); color: var(--primary-text-color);
            border:1px solid var(--divider-color); border-radius:6px; padding:5px 8px; }
        .gv-val { min-width:52px; text-align:right; opacity:.85; }
        .gv-ev { border:1px solid var(--divider-color); border-radius:8px; padding:8px; margin:8px 0; }
        .gv-ev h4 { margin:0 0 6px; font-size:14px; }
        .gv-ch { display:flex; flex-wrap:wrap; gap:10px; margin:6px 0; font-size:13px; }
        .gv-ch label { display:flex; align-items:center; gap:4px; }
        .gv-log { max-height:420px; overflow:auto; font-size:13px; }
        .gv-log div { padding:5px 6px; border-bottom:1px solid var(--divider-color); }
        .gv-hint { font-size:12px; opacity:.7; margin-top:6px; }
        .gv-ztools { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:8px 0 6px; font-size:13px; }
        .gv-ztools select { background: var(--secondary-background-color); color: var(--primary-text-color);
                            border:1px solid var(--divider-color); border-radius:6px; padding:5px 8px; }
        .gv-zone input[type=checkbox] { flex:0 0 auto; }
        .gv-zprops { margin-top:10px; padding:10px; border:1px solid var(--divider-color); border-radius:8px;
                     background: var(--secondary-background-color); }
        .gv-zprops h4 { margin:0 0 8px; font-size:13px; }
        .gv-zgrid { display:grid; grid-template-columns: repeat(4, 1fr); gap:8px; }
        .gv-zgrid label { display:flex; flex-direction:column; gap:3px; font-size:12px; opacity:.9; }
        .gv-zgrid input { width:100%; background: var(--card-background-color); color: var(--primary-text-color);
                          border:1px solid var(--divider-color); border-radius:6px; padding:4px 6px; font-size:13px; }
        .gv-ok { color:#15803d; } .gv-warn { color:#b45309; } .gv-err { color:#b91c1c; }
      </style>
      <div class="gv-head">
        <div class="gv-badge unknown" id="gvState">…</div>
        <div class="gv-meta" id="gvMeta"></div>
        <div style="flex:1 1 auto"></div>
        <button class="gv-btn secondary" id="gvBack" title="Вернуться в меню Home Assistant">◀ В меню</button>
        <span class="gv-meta" id="gvDirty" style="display:none;color:#b45309">● не сохранено</span>
        <label class="gv-meta"><input type="checkbox" id="gvLive" checked> живой кадр</label>
        <button class="gv-btn secondary" id="gvRefresh">Обновить</button>
        <button class="gv-btn" id="gvTest">Проверить сейчас</button>
      </div>
      <div class="gv-grid">
        <div class="gv-card">
          <div class="gv-canvas-wrap"><canvas id="gvCanvas" width="1280" height="720"></canvas></div>
          <div class="gv-tools">
            <span>Добавить зону:</span>
            <select id="gvNewRole">
              <option value="open">Открыто / Включено</option>
              <option value="closed">Закрыто / Выключено</option>
              <option value="ignore">Исключение</option>
            </select>
            <span class="gv-hint">ЛКМ по пустому месту — нарисовать; тянуть — сдвинуть; угол — изменить размер</span>
            <div style="flex:1 1 auto"></div>
            <button class="gv-btn secondary" id="gvZonesDefault">Зоны по умолчанию</button>
            <button class="gv-btn" id="gvSaveZones">Сохранить зоны</button>
          </div>
          <div class="gv-zonelist" id="gvZoneList"></div>
        </div>
        <div class="gv-card">
          <div class="gv-tabs">
            <div class="gv-tab active" data-tab="cams">Камеры</div>
            <div class="gv-tab" data-tab="setup">Настройка</div>
            <div class="gv-tab" data-tab="reactions">Реагирования</div>
            <div class="gv-tab" data-tab="control">Управление</div>
            <div class="gv-tab" data-tab="interlocks">Запреты</div>
            <div class="gv-tab" data-tab="sched">Расписания</div>
            <div class="gv-tab" data-tab="log">Журнал</div>
          </div>
          <div id="gvTabBody"></div>
        </div>
      </div>
    `;
    this.appendChild(this._root);

    this._canvas = this._root.querySelector("#gvCanvas");
    this._ctx = this._canvas.getContext("2d");
    this._canvas.addEventListener("pointerdown", (e) => this._onPointerDown(e));
    this._canvas.addEventListener("pointermove", (e) => this._onPointerMove(e));
    this._canvas.addEventListener("pointerup", (e) => this._onPointerUp(e));
    this._canvas.addEventListener("pointerleave", () => { this._drag = null; });
    this._canvas.tabIndex = 0;
    this._canvas.addEventListener("keydown", (e) => this._onKey(e));

    this._root.querySelector("#gvBack").onclick = () => {
      if (window.history.length > 1) window.history.back();
      else window.location.assign("/");
    };
    this._root.querySelector("#gvRefresh").onclick = () => this._load(true);
    this._root.querySelector("#gvTest").onclick = () => this._test();
    this._root.querySelector("#gvLive").onchange = (e) => {
      this._live = e.target.checked;
      this._scheduleLive();
    };
    this._root.querySelector("#gvNewRole").onchange = (e) => { this._newRole = e.target.value; };
    this._root.querySelector("#gvSaveZones").onclick = () => this._save({ zones: this._settings.zones });
    this._root.querySelector("#gvZonesDefault").onclick = () => this._loadDefaults();
    this._root.querySelectorAll(".gv-tab").forEach((el) => {
      el.onclick = () => {
        if (this._tileTimer && el.dataset.tab !== "cams") { clearInterval(this._tileTimer); this._tileTimer = null; }
        this._tab = el.dataset.tab;
        this._root.querySelectorAll(".gv-tab").forEach((t) => t.classList.toggle("active", t === el));
        this._renderTab();
      };
    });

    this._scheduleLive();
  }

  async _boot() {
    await this._loadCameras();
    await this._load();
    this._renderTab();
  }

  async _loadCameras() {
    try {
      const data = await this._hass.callApi("GET", "gate_vision/cameras");
      this._cams = data.cameras || [];
      if (!this._activeEntry && this._cams.length) this._activeEntry = this._cams[0].entry_id;
      const active = this._cams.find((c) => c.entry_id === this._activeEntry);
      this._camTitle = active ? active.title : "";
    } catch (err) {
      this._cams = [];
    }
  }

  /* ------------------------------------------------------------------ сетка камер */
  _renderCameras(host) {
    const bar = document.createElement("div");
    bar.className = "gv-row";
    bar.innerHTML = `<label>Колонок в сетке</label>
      <select id="gvCols">
        ${[1, 2, 3, 4].map((n) => `<option value="${n}" ${n === this._gridCols ? "selected" : ""}>${n}</option>`).join("")}
      </select>
      <button class="gv-btn secondary" id="gvCamsRefresh">Обновить камеры</button>
      <span class="gv-meta">камер: ${this._cams.length} · активная: <b>${this._camTitle || "—"}</b></span>`;
    host.appendChild(bar);
    bar.querySelector("#gvCols").onchange = (e) => { this._gridCols = parseInt(e.target.value, 10); this._renderCameras(host); };
    bar.querySelector("#gvCamsRefresh").onclick = async () => { await this._loadCameras(); this._renderCameras(host); };

    const hint = document.createElement("div");
    hint.className = "gv-hint";
    hint.innerHTML = "Каждая камера — отдельная запись интеграции (Настройки → Устройства и службы → Добавить интеграцию → Обнаружение). " +
      "Клик по плитке делает камеру активной: её настройки и обучение открываются во вкладке «Настройка».";
    host.appendChild(hint);

    if (!this._cams.length) {
      const empty = document.createElement("div");
      empty.className = "gv-hint";
      empty.textContent = "Камер пока нет — добавьте интеграцию для каждой камеры.";
      host.appendChild(empty);
      return;
    }

    const grid = document.createElement("div");
    grid.style.cssText = `display:grid;grid-template-columns:repeat(${this._gridCols},1fr);gap:10px`;
    host.appendChild(grid);

    this._cams.forEach((cam) => {
      const card = document.createElement("div");
      card.className = "gv-ev";
      card.style.cursor = "pointer";
      if (cam.entry_id === this._activeEntry) card.style.outline = "2px solid var(--primary-color)";
      card.innerHTML = `
        <div class="gv-row" style="margin:0 0 6px">
          <span class="gv-badge ${cam.state || "unknown"}" style="font-size:12px;padding:3px 10px">${
            cam.state === "open" ? "ОТКРЫТО" : cam.state === "closed" ? "ЗАКРЫТО" : "?"}</span>
          <b style="font-size:13px">${cam.title}</b>
          <span class="gv-meta" style="margin-left:auto">${cam.camera_ok === false ? "нет связи · " : ""}${cam.frame || ""}</span>
        </div>
        <canvas width="640" height="360" style="width:100%;border-radius:6px;background:#111"></canvas>
        <div class="gv-meta" style="margin-top:4px">${
          cam.camera_ok === false && cam.reason ? "⚠ " + cam.reason.slice(0, 70) : ""}</div>`;
      card.onclick = async (e) => {
        if (e.target.tagName === "CANVAS") {
          this._activeEntry = cam.entry_id;
          this._camTitle = cam.title;
          await this._load(false, true);
          this._tab = "setup";
          this._root.querySelectorAll(".gv-tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === "setup"));
          this._renderTab();
        }
      };
      grid.appendChild(card);
      this._drawTile(cam, card.querySelector("canvas"));
    });

    // живое обновление плиток
    if (this._tileTimer) clearInterval(this._tileTimer);
    this._tileTimer = setInterval(async () => {
      if (this._tab !== "cams") return;
      await this._loadCameras();
      const cards = grid.children;
      for (let i = 0; i < this._cams.length && i < cards.length; i++) {
        this._drawTile(this._cams[i], cards[i].querySelector("canvas"));
      }
    }, 5000);
  }

  async _drawTile(cam, canvas) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    try {
      const resp = await this._hass.fetchWithAuth(`/api/gate_vision/frame?entry_id=${encodeURIComponent(cam.entry_id)}&_=${Date.now()}`);
      if (!resp.ok) return;
      const blob = await resp.blob();
      const img = new Image();
      img.onload = () => {
        canvas.width = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);
        const w = canvas.width, h = canvas.height;
        const line = Math.max(2, Math.round(w / 500));
        (cam.zones || []).forEach((z) => {
          const st = z.state;
          const color = z.role === "ignore" ? "#64748b" : (STATE_COLORS[st] || ROLE_COLORS[z.role] || "#fff");
          ctx.strokeStyle = color;
          ctx.lineWidth = line;
          ctx.strokeRect(z.x * w, z.y * h, z.w * w, z.h * h);
        });
      };
      img.src = URL.createObjectURL(blob);
    } catch (err) { /* кадр недоступен */ }
  }

  _scheduleLive() {
    if (this._timer) clearInterval(this._timer);
    if (!this._live) return;
    // живое обновление: состояние и последний кадр из интеграции, БЕЗ нового запроса к камере
    this._timer = setInterval(() => this._load(false, true), 5000);
  }

  /* ------------------------------------------------------------------ данные */
  _qs(extra) {
    const p = [];
    if (this._activeEntry) p.push("entry_id=" + encodeURIComponent(this._activeEntry));
    if (extra) p.push(extra);
    return p.length ? "?" + p.join("&") : "";
  }

  async _load(fresh = false, quiet = false) {
    try {
      const q = this._qs(fresh ? "fresh=1" : "");
      const data = await this._hass.callApi("GET", `gate_vision/state${q}`);
      this._state = data.analysis || {};
      if (!this._dirty) this._settings = data.settings || this._settings;
      else if (data.settings) this._serverSettings = data.settings;
      this._interlocks = data.interlocks || this._interlocks;
      this._log = data.event_log || this._log;
      await this._loadFrame(fresh);
      this._renderHead();
      if (this._tab === "log") this._renderTab();
      this._draw();
    } catch (err) {
      if (!quiet) this._toast(`Не удалось получить состояние: ${err.message || err}`);
    }
  }

  async _loadFrame(fresh) {
    try {
      const qs = new URLSearchParams();
      if (fresh) qs.set("fresh", "1");
      qs.set("_", String(Date.now()));
      if (this._activeEntry) qs.set("entry_id", this._activeEntry);
      const resp = await this._hass.fetchWithAuth(`/api/gate_vision/frame?${qs.toString()}`);
      if (!resp.ok) return;
      const blob = await resp.blob();
      const img = new Image();
      img.onload = () => {
        this._canvas.width = img.width;
        this._canvas.height = img.height;
        this._img = img;
        this._draw();
      };
      img.src = URL.createObjectURL(blob);
    } catch (err) {
      /* кадр недоступен — не мешаем работе */
    }
  }

  async _test() {
    try {
      const res = await this._hass.callApi("POST", `gate_vision/test${this._qs()}`);
      this._state = res.analysis || this._state;
      this._renderHead();
      this._draw();
      this._toast(`Проверка: ${res.analysis?.state || "?"}`);
    } catch (err) {
      this._toast(`Ошибка проверки: ${err.message || err}`);
    }
  }

  async _save(patch) {
    try {
      const res = await this._hass.callApi("POST", `gate_vision/settings${this._qs("refresh=1")}`, patch);
      this._dirty = false;
      this._settings = res.settings || this._settings;
      this._toast("Сохранено");
      await this._load(false, true);
    } catch (err) {
      this._toast(`Не сохранилось: ${err.message || err}`);
    }
  }

  async _loadDefaults() {
    const z1 = { id: "z1", name: "Открыто (низ проёма)", role: "open", x: 0.57, y: 0.40, w: 0.25, h: 0.15 };
    await this._save({ zones: [z1] });
  }


  /* ------------------------------------------------------------------ списки для выбора */
  _entities(domains) {
    const out = [];
    const states = (this._hass && this._hass.states) || {};
    Object.keys(states).forEach((id) => {
      const dom = id.split(".")[0];
      if (domains.includes(dom)) {
        const name = states[id].attributes?.friendly_name || id;
        out.push({ value: id, label: `${name} (${id})` });
      }
    });
    out.sort((a, b) => a.label.localeCompare(b.label, "ru"));
    return out;
  }

  _notifyServices() {
    const out = [];
    const services = (this._hass && this._hass.services) || {};
    Object.keys(services.notify || {}).forEach((name) => {
      if (["send_message", "send_file", "persistent_notification", "notify"].includes(name)) return;
      out.push({ value: `notify.${name}`, label: `notify.${name}` });
    });
    out.sort((a, b) => a.label.localeCompare(b.label));
    return out;
  }

  _datalist(id, items) {
    return `<datalist id="${id}">` +
      items.map((i) => `<option value="${i.value}">${i.label}</option>`).join("") +
      `</datalist>`;
  }

  _pick(id, items, value, placeholder) {
    return `${this._datalist(id, items)}
      <input type="text" list="${id}" data-f="__FIELD__" value="${value || ""}" placeholder="${placeholder || "выбрать из списка"}">`;
  }

  /* ------------------------------------------------------------------ отрисовка */
  _renderHead() {
    const s = this._state || {};
    const badge = this._root.querySelector("#gvState");
    const state = s.state || "unknown";
    badge.className = `gv-badge ${state}`;
    badge.textContent = state === "open" ? "ОТКРЫТО" : state === "closed" ? "ЗАКРЫТО" : "НЕИЗВЕСТНО";
    const parts = [];
    const zs = s.zones || [];
    const learned = zs.filter((z) => z.samples && ((z.samples.open || 0) + (z.samples.closed || 0)) > 0);
    if (learned.length) {
      learned.forEach((z) => parts.push(`${z.name}: ${z.mean} → ${z.state === "open" ? "ОТКРЫТО" : z.state === "closed" ? "ЗАКРЫТО" : "?"} (${z.conf})`));
    } else {
      zs.forEach((z) => parts.push(`${z.name}: ${z.mean}`));
      parts.push("зоны не обучены — работают пороги");
    }
    if (s.moving) parts.push("движется");
    if (s.stale) parts.push("устаревшее");
    if (s.camera_ok === false) parts.push("камера недоступна");
    if (this._settings?.learn_mode) parts.push("режим обучения");
    this._root.querySelector("#gvMeta").textContent = parts.join(" · ");
    const dirtyEl = this._root.querySelector("#gvDirty");
    if (dirtyEl) dirtyEl.style.display = this._dirty ? "" : "none";
    this._root.querySelector("#gvMeta").title = s.reason || "";
  }

  _draw() {
    const ctx = this._ctx;
    const w = this._canvas.width, h = this._canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (this._img) ctx.drawImage(this._img, 0, 0, w, h);
    else { ctx.fillStyle = "#111"; ctx.fillRect(0, 0, w, h); }
    const zones = this._settings?.zones || [];
    const line = Math.max(2, Math.round(w / 500));
    const stateOf = {};
    ((this._state && this._state.zones) || []).forEach((z) => { stateOf[z.id] = z.state; });
    zones.forEach((z) => {
      const x = z.x * w, y = z.y * h, zw = z.w * w, zh = z.h * h;
      let color = ROLE_COLORS[z.role] || "#fff";
      const st = stateOf[z.id];
      if (z.role !== "ignore" && st) color = STATE_COLORS[st] || color;
      ctx.strokeStyle = color;
      ctx.lineWidth = z.id === this._selectedZone ? line * 2 : line;
      ctx.strokeRect(x, y, zw, zh);
      ctx.fillStyle = color + "22";
      ctx.fillRect(x, y, zw, zh);
      ctx.font = `${Math.round(w / 60)}px sans-serif`;
      ctx.fillStyle = color;
      const stTitle = stateOf[z.id] ? ` — ${STATE_TITLES[stateOf[z.id]] || stateOf[z.id]}` : "";
      const label = `${z.name}${stTitle}`;
      ctx.fillText(label, x + 4, Math.max(14, y - 4));
      // уголок для изменения размера
      ctx.fillStyle = color;
      ctx.fillRect(x + zw - line * 4, y + zh - line * 4, line * 4, line * 4);
    });
  }

  _renderZoneList() {
    const host = this._root.querySelector("#gvZoneList");
    const zones = this._settings?.zones || [];
    const measured = {};
    ((this._state && this._state.zones) || []).forEach((z) => { measured[z.id] = z; });
    const sel = this._zoneSelection || (this._zoneSelection = new Set());
    [...sel].forEach((id) => { if (!zones.some((z) => z.id === id)) sel.delete(id); });
    host.innerHTML = "";

    // --- панель массовых действий ---
    const bar = document.createElement("div");
    bar.className = "gv-ztools";
    bar.innerHTML = `
      <label class="gv-meta"><input type="checkbox" id="gvAll" ${sel.size && sel.size === zones.length ? "checked" : ""}> все зоны</label>
      <span class="gv-meta">выбрано: <b>${sel.size}</b> из ${zones.length}</span>
      <select id="gvBulkRole">
        <option value="">— роль для выбранных —</option>
        ${Object.keys(ROLE_TITLES).map((r) => `<option value="${r}">${ROLE_TITLES[r]}</option>`).join("")}
      </select>
      <button class="gv-btn secondary" id="gvBulkApply">Применить</button>
      <button class="gv-btn danger" id="gvBulkDel">Удалить выбранные</button>
      <button class="gv-btn secondary" id="gvClearSel">Снять выбор</button>`;
    bar.querySelector("#gvAll").onchange = (e) => {
      sel.clear();
      if (e.target.checked) zones.forEach((z) => sel.add(z.id));
      this._renderZoneList(); this._draw();
    };
    bar.querySelector("#gvBulkApply").onclick = () => {
      const role = bar.querySelector("#gvBulkRole").value;
      if (!role || !sel.size) return;
      zones.forEach((z) => { if (sel.has(z.id)) z.role = role; });
      this._dirty = true;
      this._scheduleZoneSave();
      this._renderZoneList(); this._draw();
    };
    bar.querySelector("#gvBulkDel").onclick = () => {
      if (!sel.size) return;
      this._settings.zones = zones.filter((z) => !sel.has(z.id));
      sel.clear();
      this._selectedZone = null;
      this._dirty = true;
      this._scheduleZoneSave();
      this._renderZoneList(); this._draw();
    };
    bar.querySelector("#gvClearSel").onclick = () => { sel.clear(); this._renderZoneList(); this._draw(); };
    host.appendChild(bar);

    // --- строки зон ---
    zones.forEach((z) => {
      const row = document.createElement("div");
      row.className = "gv-zone" + (z.id === this._selectedZone ? " sel" : "");
      const zState = (measured[z.id] || {}).state;
      const dotColor = (z.role !== "ignore" && zState) ? (STATE_COLORS[zState] || ROLE_COLORS[z.role]) : ROLE_COLORS[z.role];
      row.innerHTML = `
        <input type="checkbox" class="gv-zcheck" ${sel.has(z.id) ? "checked" : ""} title="выбрать для массовых действий">
        <span class="gv-dot" style="background:${dotColor}"></span>
        <input type="text" class="gv-zname" value="${z.name}"
               style="flex:1 1 auto;background:transparent;border:none;color:inherit">
        <span class="gv-meta" style="white-space:nowrap">${(z.w * 100).toFixed(1)}×${(z.h * 100).toFixed(1)} %</span>
        <span class="gv-meta" style="white-space:nowrap;color:${dotColor}">${zState ? (STATE_TITLES[zState] || "") : ""}</span>
        <select>${Object.keys(ROLE_TITLES).map((r) =>
          `<option value="${r}" ${r === z.role ? "selected" : ""}>${ROLE_TITLES[r]}</option>`).join("")}</select>
        <button class="gv-btn danger" style="padding:4px 9px" title="удалить зону">✕</button>`;
      row.onclick = (e) => {
        if (["BUTTON", "INPUT", "SELECT"].includes(e.target.tagName)) return;
        this._selectedZone = z.id;
        this._renderZoneList(); this._draw();
      };
      row.querySelector(".gv-zcheck").onchange = (e) => {
        if (e.target.checked) sel.add(z.id); else sel.delete(z.id);
        this._selectedZone = z.id;
        this._renderZoneList(); this._draw();
      };
      row.querySelector(".gv-zname").onchange = (e) => {
        z.name = e.target.value;
        this._dirty = true;
        this._scheduleZoneSave();
      };
      row.querySelector("select").onchange = (e) => {
        z.role = e.target.value;
        this._dirty = true;
        this._scheduleZoneSave();
        this._renderZoneList();
        this._draw();
      };
      row.querySelector("button").onclick = () => {
        this._settings.zones = zones.filter((x) => x.id !== z.id);
        sel.delete(z.id);
        this._selectedZone = null;
        this._dirty = true;
        this._scheduleZoneSave();
        this._renderZoneList(); this._draw();
      };
      host.appendChild(row);
    });

    // --- точные размеры выбранной зоны ---
    const zone = zones.find((z) => z.id === this._selectedZone);
    if (zone) {
      const box = document.createElement("div");
      box.className = "gv-zprops";
      box.innerHTML = `
        <h4>Размер и положение: <span style="color:${ROLE_COLORS[zone.role]}">${zone.name}</span></h4>
        <div class="gv-zgrid">
          <label>X, %<input type="number" step="0.5" min="0" max="100" data-p="x" value="${(zone.x * 100).toFixed(1)}"></label>
          <label>Y, %<input type="number" step="0.5" min="0" max="100" data-p="y" value="${(zone.y * 100).toFixed(1)}"></label>
          <label>Ширина, %<input type="number" step="0.5" min="0.5" max="100" data-p="w" value="${(zone.w * 100).toFixed(1)}"></label>
          <label>Высота, %<input type="number" step="0.5" min="0.5" max="100" data-p="h" value="${(zone.h * 100).toFixed(1)}"></label>
        </div>
        <div class="gv-hint">Можно тянуть мышью, а можно задать точно здесь. Стрелки на кадре двигают выбранную
        зону (с Shift — мелким шагом), «Сохранить зоны» записывает всё в Home Assistant.</div>`;
      box.querySelectorAll("input[data-p]").forEach((inp) => {
        inp.onchange = () => {
          const v = Math.max(0, Math.min(100, parseFloat(inp.value || "0"))) / 100;
          const key = inp.dataset.p;
          if (key === "x") zone.x = Math.min(1 - zone.w, v);
          else if (key === "y") zone.y = Math.min(1 - zone.h, v);
          else if (key === "w") zone.w = Math.max(0.005, Math.min(1 - zone.x, v));
          else zone.h = Math.max(0.005, Math.min(1 - zone.y, v));
          this._dirty = true;
          this._scheduleZoneSave();
          this._renderZoneList(); this._draw();
        };
      });
      host.appendChild(box);
    }
  }

  _renderTab() {
    const body = this._root.querySelector("#gvTabBody");
    body.innerHTML = "";
    if (this._tab === "cams") this._renderCameras(body);
    else if (this._tab === "setup") this._renderSetup(body);
    else if (this._tab === "reactions") this._renderReactions(body);
    else if (this._tab === "control") this._renderControl(body);
    else if (this._tab === "interlocks") this._renderInterlocks(body);
    else if (this._tab === "sched") this._renderSchedules(body);
    else this._renderLog(body);
    this._renderZoneList();
  }

  _renderThresholds(host) {
    const th = this._settings?.thresholds || {};
    const wrap = document.createElement("div");
    const note = document.createElement("div");
    note.className = "gv-hint";
    note.innerHTML = "<b>Пороги нужны только если зоны не обучены.</b> Если ты обучил состояние зоны " +
      "во вкладке «Обучение», интеграция сравнивает замер с обученными значениями и пороги не используются.";
    wrap.appendChild(note);
    const details = document.createElement("details");
    details.innerHTML = "<summary style='cursor:pointer;margin:8px 0'>Дополнительно: пороги для работы без обучения</summary>";
    wrap.appendChild(details);
    THRESHOLD_META.forEach(([key, title, min, max, step]) => {
      const row = document.createElement("div");
      row.className = "gv-row";
      row.innerHTML = `<label title="${key}">${title}</label>
        <input type="range" min="${min}" max="${max}" step="${step}" value="${th[key] ?? min}">
        <span class="gv-val">${th[key] ?? "—"}</span>`;
      const range = row.querySelector("input");
      const val = row.querySelector(".gv-val");
      range.oninput = () => { val.textContent = range.value; };
      range.onchange = () => this._save({ thresholds: { [key]: parseFloat(range.value) } });
      details.appendChild(row);
    });
    const hint = document.createElement("div");
    hint.className = "gv-hint";
    hint.textContent = "Пороги применяются сразу. День: зона «открыто» светлее порога → ОТКРЫТО. Ночь/ИК: темнее порога → ОТКРЫТО.";
    wrap.appendChild(hint);
    const btn = document.createElement("button");
    btn.className = "gv-btn secondary";
    btn.textContent = "Вернуть пороги по умолчанию";
    btn.onclick = () => this._save({ thresholds: {
      street_low_t: 140, dark_low_t: 60, bright: 190, dark_band: 70, frame_t: 0.65,
      min_band_f: 0.08, max_band_f: 0.55, gray_sat: 0.02, frame_min_mean: 15, move_diff: 6 } });
    wrap.appendChild(btn);
    host.appendChild(wrap);
  }

  _renderReactions(host) {
    const reactions = this._settings?.reactions || {};
    EVENTS.forEach(([key, title]) => {
      const cfg = reactions[key] || {};
      const card = document.createElement("div");
      card.className = "gv-ev";
      card.innerHTML = `
        <h4><label><input type="checkbox" data-f="enabled" ${cfg.enabled ? "checked" : ""}> ${title}</label></h4>
        <div class="gv-ch">${CHANNELS.map(([ch, chTitle]) =>
          `<label><input type="checkbox" data-ch="${ch}" ${(cfg.channels || []).includes(ch) ? "checked" : ""}> ${chTitle}</label>`).join("")}</div>
        <div class="gv-row"><label>Кому (push)</label>
          <input type="text" list="gv-notify-${key}" data-f="notify_service" value="${cfg.notify_service || ""}" placeholder="выбрать службу notify">
          ${this._datalist(`gv-notify-${key}`, this._notifyServices())}</div>
        <div class="gv-row"><label>Озвучка (TTS)</label>
          <input type="text" list="gv-tts-${key}" data-f="tts_entity" value="${cfg.tts_entity || ""}" placeholder="выбрать tts.*">
          ${this._datalist(`gv-tts-${key}`, this._entities(["tts"]))}</div>
        <div class="gv-row"><label>Куда говорить</label>
          <input type="text" list="gv-mp-${key}" data-f="tts_media_player" value="${cfg.tts_media_player || ""}" placeholder="выбрать media_player.*">
          ${this._datalist(`gv-mp-${key}`, this._entities(["media_player"]))}</div>
        <div class="gv-row"><label>Скрипт / автоматизация</label>
          <input type="text" list="gv-scr-${key}" data-f="script_entity" value="${cfg.script_entity || ""}" placeholder="выбрать script.* / automation.*">
          ${this._datalist(`gv-scr-${key}`, this._entities(["script", "automation", "scene"]))}</div>
        <div class="gv-row"><label>MQTT-топик</label><input type="text" data-f="mqtt_topic" value="${cfg.mqtt_topic || ""}" placeholder="gate_vision/event"></div>
        <div class="gv-row"><label>Webhook URL</label><input type="text" data-f="webhook_url" value="${cfg.webhook_url || ""}" placeholder="http://..."></div>
        <div class="gv-row"><label>Текст сообщения</label><input type="text" data-f="message" value="${cfg.message || ""}" placeholder="(по умолчанию)"></div>
        <div class="gv-row"><label>Повтор, мин</label><input type="number" data-f="repeat_min" min="0" max="720" value="${cfg.repeat_min || 0}"></div>
        <div class="gv-row"><label>Тихие часы с</label><input type="text" data-f="quiet_from" value="${cfg.quiet_from || ""}" placeholder="23:00">
                         <label>по</label><input type="text" data-f="quiet_to" value="${cfg.quiet_to || ""}" placeholder="07:00"></div>
        <button class="gv-btn" data-save="${key}">Сохранить</button>`;
      card.querySelector("button[data-save]").onclick = () => {
        const patch = {};
        card.querySelectorAll("[data-f]").forEach((el) => {
          const f = el.dataset.f;
          patch[f] = el.type === "checkbox" ? el.checked
            : el.type === "number" ? parseFloat(el.value || "0") : el.value;
        });
        patch.channels = [...card.querySelectorAll("[data-ch]")].filter((c) => c.checked).map((c) => c.dataset.ch);
        this._save({ reactions: { [key]: patch } });
      };
      host.appendChild(card);
    });
  }

  _renderControl(host) {
    const c = this._settings?.control || {};
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <div class="gv-row"><label><input type="checkbox" data-f="enabled" ${c.enabled ? "checked" : ""}> Разрешить управление воротами</label></div>
      <div class="gv-row"><label>Способ</label>
        <select data-f="mode">
          <option value="switch_impulse" ${c.mode !== "mqtt_impulse" ? "selected" : ""}>Сущность реле (switch.*)</option>
          <option value="mqtt_impulse" ${c.mode === "mqtt_impulse" ? "selected" : ""}>MQTT-топик реле</option>
        </select></div>
      <div class="gv-row"><label>Реле ворот</label>
        <input type="text" list="gv-relay" data-f="switch_entity" value="${c.switch_entity || ""}" placeholder="выбрать switch.*">
        ${this._datalist("gv-relay", this._entities(["switch"]))}</div>
      <div class="gv-row"><label>MQTT-топик</label><input type="text" data-f="mqtt_topic" value="${c.mqtt_topic || ""}" placeholder="dingtian/relay8777832/in/r26"></div>
      <div class="gv-row"><label>Тип реле</label>
        <select data-f="relay_type" id="gvRelayType">
          <option value="impulse" ${c.relay_type !== "constant" ? "selected" : ""}>Импульсное (реле само даёт импульс)</option>
          <option value="constant" ${c.relay_type === "constant" ? "selected" : ""}>Постоянное (держим заданное время)</option>
        </select></div>
      <div class="gv-row" id="gvHoldRow" style="${c.relay_type === "constant" ? "" : "display:none"}">
        <label>Длительность нажатия, мс</label><input type="number" data-f="impulse_ms" value="${c.impulse_ms || 800}"></div>
      <div class="gv-row"><label>Ожидание подтверждения, с</label><input type="number" data-f="confirm_timeout" value="${c.confirm_timeout || 45}"></div>
      <div class="gv-row"><label><input type="checkbox" data-f="check_clear_before_close" ${c.check_clear_before_close ? "checked" : ""}> Перед закрытием проверять камеру</label></div>
      <div class="gv-row" style="gap:8px;align-items:center">
        <b>Команды</b>
        <span class="gv-meta" id="gvCtrlState">${this._state ? (this._state.state || "") : ""}</span>
        <button class="gv-btn" id="gvOpen" ${this._state && this._state.control_enabled ? "" : "disabled"}>Открыть</button>
        <button class="gv-btn" id="gvClose" ${this._state && this._state.control_enabled ? "" : "disabled"}>Закрыть</button>
        <button class="gv-btn" id="gvStop" ${this._state && this._state.control_enabled ? "" : "disabled"}>Стоп</button>
      </div>
      <div class="gv-hint">Пока управление выключено, сущность cover не создаётся — интеграция только читает состояние.
      У <b>импульсного</b> реле длительность задаётся в самом реле, поэтому время нажатия не спрашивается.
      У <b>постоянного</b> реле ворот держим его заданное время и отпускаем.</div>
      <button class="gv-btn">Сохранить</button>`;
    const typeSel = wrap.querySelector("#gvRelayType");
    const holdRow = wrap.querySelector("#gvHoldRow");
    if (typeSel && holdRow) {
      typeSel.onchange = () => { holdRow.style.display = typeSel.value === "constant" ? "" : "none"; };
    }
    wrap.querySelector("button").onclick = () => {
      const patch = {};
      wrap.querySelectorAll("[data-f]").forEach((el) => {
        patch[el.dataset.f] = el.type === "checkbox" ? el.checked
          : el.type === "number" ? parseFloat(el.value || "0") : el.value;
      });
      this._save({ control: patch });
    };

    // --- команды воротам (cover этой записи) ---
    const coverEntity = (this._state && this._state.cover_entity) || null;
    const cmd = (service, btn) => {
      if (!coverEntity) { alert("Управление выключено или cover ещё не создан"); return; }
      if (btn) { btn.disabled = true; setTimeout(() => { btn.disabled = false; }, 4000); }
      this._hass.callService("cover", service, { entity_id: coverEntity });
    };
    const bOpen = wrap.querySelector("#gvOpen");
    const bClose = wrap.querySelector("#gvClose");
    const bStop = wrap.querySelector("#gvStop");
    if (bOpen) bOpen.onclick = () => cmd("open_cover", bOpen);
    if (bClose) bClose.onclick = () => cmd("close_cover", bClose);
    if (bStop) bStop.onclick = () => cmd("stop_cover", bStop);
    host.appendChild(wrap);
  }



  /* ------------------------------------------------------------------ быстрая настройка */
  _renderSetup(host) {
    const zones = this._settings?.zones || [];
    const measured = {};
    ((this._state && this._state.zones) || []).forEach((z) => { measured[z.id] = z; });

    const presets = document.createElement("div");
    presets.className = "gv-row";
    presets.innerHTML = `<label>Пресет объекта</label>
      <select id="gvPreset">
        <option value="">— выбрать —</option>
        <option value="gate">Ворота (Открыто/Закрыто)</option>
        <option value="door">Дверь / калитка (Открыто/Закрыто)</option>
        <option value="lamp">Лампа / свет (Включено/Выключено)</option>
        <option value="custom">Свой (ничего не менять)</option>
      </select>`;
    host.appendChild(presets);

    const hint = document.createElement("div");
    hint.className = "gv-hint";
    hint.innerHTML = "<b>Порядок:</b> 1) выбери камеру; 2) нарисуй зону на кадре (мышью); " +
      "3) приведи объект в состояние и нажми «Запомнить ОТКРЫТО», затем «Запомнить ЗАКРЫТО» " +
      "(лучше по разу днём и ночью); 4) включи «создавать сущность» и сохрани. Готово.";
    host.appendChild(hint);

    // --- камера ---
    const cam = document.createElement("details");
    cam.open = !this._settings?.camera_entity && !this._settings?.snapshot_url;
    cam.innerHTML = `<summary style="cursor:pointer;margin:8px 0">Камера и опрос</summary>`;
    host.appendChild(cam);
    this._renderCamera(cam);

    // --- зоны: обучение и сущность ---
    const ztitle = document.createElement("div");
    ztitle.className = "gv-hint";
    ztitle.style.marginTop = "12px";
    ztitle.innerHTML = "<b>Зоны и обучение</b>";
    host.appendChild(ztitle);

    if (!zones.length) {
      const empty = document.createElement("div");
      empty.className = "gv-hint";
      empty.textContent = "Зон нет — нарисуй первую мышью на кадре слева.";
      host.appendChild(empty);
    }
    zones.forEach((z) => {
      const m = measured[z.id] || {};
      const kind = z.kind === "on_off" ? "on_off" : "open_closed";
      const labelOn = kind === "on_off" ? "ВКЛ" : "ОТКРЫТО";
      const labelOff = kind === "on_off" ? "ВЫКЛ" : "ЗАКРЫТО";
      const card = document.createElement("div");
      card.className = "gv-ev";
      card.innerHTML = `
        <h4>${z.name} <span class="gv-meta">(${z.id}, ${ROLE_TITLES[z.role] || z.role})</span></h4>
        <div class="gv-meta">сейчас: <b>${m.mean ?? "—"}</b> · состояние: <b>${m.state === "open" ? labelOn : m.state === "closed" ? labelOff : "—"}</b>
          (уверенность ${m.conf ?? "—"}) · замеров: ${labelOn} ${(z.samples?.open || []).length}, ${labelOff} ${(z.samples?.closed || []).length}</div>
        <div class="gv-ch" style="margin-top:8px">
          <button class="gv-btn" data-learn="open">Запомнить ${labelOn}</button>
          <button class="gv-btn" data-learn="closed">Запомнить ${labelOff}</button>
          <button class="gv-btn secondary" data-auto="open">Авто ${labelOn} (10 замеров)</button>
          <button class="gv-btn secondary" data-auto="closed">Авто ${labelOff} (10 замеров)</button>
          <button class="gv-btn secondary" data-reset="1">Сбросить</button>
        </div>
        <div class="gv-ch">
          <label><input type="checkbox" data-ent="1" ${z.entity ? "checked" : ""}> создавать сущность</label>
          <label>тип:
            <select data-kind="1">
              <option value="open_closed" ${kind === "open_closed" ? "selected" : ""}>Открыто / Закрыто</option>
              <option value="on_off" ${kind === "on_off" ? "selected" : ""}>Включено / Выключено</option>
            </select></label>
          <label>имя: <input type="text" data-name="1" value="${z.name}"></label>
          <button class="gv-btn" data-savez="1">Сохранить зону</button>
        </div>
        <div class="gv-hint">замеры: ${JSON.stringify(z.samples || {})}</div>`;
      card.querySelector('[data-learn="open"]').onclick = () => this._learn(z.id, "open");
      card.querySelector('[data-learn="closed"]').onclick = () => this._learn(z.id, "closed");
      card.querySelector('[data-auto="open"]').onclick = () => this._learn(z.id, "open", null, 10);
      card.querySelector('[data-auto="closed"]').onclick = () => this._learn(z.id, "closed", null, 10);
      card.querySelector("[data-reset]").onclick = () => this._learn(z.id, null, "reset");
      card.querySelector("[data-savez]").onclick = () => {
        const patch = (this._settings.zones || []).map((x) => ({ ...x }));
        const t = patch.find((x) => x.id === z.id);
        t.entity = card.querySelector("[data-ent]").checked;
        t.kind = card.querySelector("[data-kind]").value;
        t.name = card.querySelector("[data-name]").value;
        this._save({ zones: patch });
      };
      host.appendChild(card);
    });

    // --- дополнительно: пороги и прочее ---
    const adv = document.createElement("details");
    adv.innerHTML = `<summary style="cursor:pointer;margin:10px 0">Дополнительно: пороги (нужны только без обучения), режим обучения, «открыто дольше»</summary>`;
    host.appendChild(adv);
    this._renderThresholds(adv);
    const extra = document.createElement("div");
    extra.className = "gv-row";
    extra.innerHTML = `<label>Сообщать, что открыто дольше, мин</label>
      <input type="number" id="gvLeftOpen" value="${this._settings?.left_open_min || 15}">
      <label><input type="checkbox" id="gvLearnMode" ${this._settings?.learn_mode ? "checked" : ""}> режим обучения (только пишет, не сообщает)</label>
      <button class="gv-btn" id="gvExtraSave">Сохранить</button>`;
    extra.querySelector("#gvExtraSave").onclick = () => this._save({
      left_open_min: parseInt(extra.querySelector("#gvLeftOpen").value || "15", 10),
      learn_mode: extra.querySelector("#gvLearnMode").checked,
    });
    adv.appendChild(extra);

    // --- пресеты ---
    presets.querySelector("#gvPreset").onchange = (e) => this._applyPreset(e.target.value);
  }

  _applyPreset(name) {
    if (!name || name === "custom") return;
    const zones = (this._settings.zones || []).map((z) => ({ ...z }));
    if (!zones.length) return;
    const t = zones[0];
    if (name === "gate") { t.kind = "open_closed"; t.role = "closed"; t.name = t.name || "Ворота"; }
    if (name === "door") { t.kind = "open_closed"; t.role = "closed"; t.name = "Дверь"; }
    if (name === "lamp") { t.kind = "on_off"; t.role = "closed"; t.name = "Свет"; }
    t.entity = true;
    this._save({ zones });
    this._toast("Пресет применён");
  }

  _renderLearn(host) {
    const zones = this._settings?.zones || [];
    const measured = {};
    ((this._state && this._state.zones) || []).forEach((z) => { measured[z.id] = z; });
    const info = document.createElement("div");
    info.className = "gv-hint";
    info.innerHTML = "Приведи объект в нужное состояние и нажми «Запомнить». Можно запомнить несколько раз " +
      "(день, ночь, разное освещение) — детектор берёт ближайший замер. " +
      "Галочка «сущность» создаёт отдельную сущность состояния этой области.";
    host.appendChild(info);

    zones.forEach((z) => {
      const m = measured[z.id] || {};
      const kind = z.kind === "on_off" ? "on_off" : "open_closed";
      const labelOn = kind === "on_off" ? "ВКЛ" : "ОТКРЫТО";
      const labelOff = kind === "on_off" ? "ВЫКЛ" : "ЗАКРЫТО";
      const card = document.createElement("div");
      card.className = "gv-ev";
      card.innerHTML = `
        <h4>${z.name} <span class="gv-meta">(${z.id}, ${ROLE_TITLES[z.role] || z.role})</span></h4>
        <div class="gv-meta">сейчас: <b>${m.mean ?? "—"}</b> · состояние: <b>${m.state || "—"}</b>
          (уверенность ${m.conf ?? "—"}) · замеров: ${labelOn} ${(z.samples?.open || []).length},
          ${labelOff} ${(z.samples?.closed || []).length}</div>
        <div class="gv-ch" style="margin-top:8px">
          <button class="gv-btn" data-learn="open">Запомнить ${labelOn}</button>
          <button class="gv-btn" data-learn="closed">Запомнить ${labelOff}</button>
          <button class="gv-btn secondary" data-reset="1">Сбросить обучение</button>
        </div>
        <div class="gv-ch">
          <label><input type="checkbox" data-ent="1" ${z.entity ? "checked" : ""}> создавать сущность</label>
          <label>тип сущности:
            <select data-kind="1">
              <option value="open_closed" ${kind === "open_closed" ? "selected" : ""}>Открыто / Закрыто</option>
              <option value="on_off" ${kind === "on_off" ? "selected" : ""}>Включено / Выключено</option>
            </select></label>
          <button class="gv-btn" data-savez="1">Сохранить настройки зоны</button>
        </div>
        <div class="gv-hint">замеры: ${JSON.stringify(z.samples || {})}</div>`;
      card.querySelector('[data-learn="open"]').onclick = () => this._learn(z.id, "open");
      card.querySelector('[data-learn="closed"]').onclick = () => this._learn(z.id, "closed");
      card.querySelector("[data-reset]").onclick = () => this._learn(z.id, null, "reset");
      card.querySelector("[data-savez]").onclick = () => {
        const patch = (this._settings.zones || []).map((x) => ({ ...x }));
        const t = patch.find((x) => x.id === z.id);
        t.entity = card.querySelector("[data-ent]").checked;
        t.kind = card.querySelector("[data-kind]").value;
        this._save({ zones: patch });
      };
      host.appendChild(card);
    });
  }

  async _learn(zoneId, state, action, count) {
    try {
      if (count > 1) this._toast(`Собираю ${count} замеров…`);
      const body = action === "reset"
        ? { zone_id: zoneId, action: "reset" }
        : { zone_id: zoneId, state, count: count || 1 };
      const res = await this._hass.callApi("POST", `gate_vision/learn${this._qs()}`, body);
      this._toast(res.message || "готово");
      await this._load(true, true);
      this._renderTab();
    } catch (err) {
      this._toast("Не удалось: " + (err.message || err));
    }
  }



  /* ------------------------------------------------------------------ запреты по сенсорам */
  _renderInterlocks(host) {
    const ops = [
      ["below", "меньше"], ["above", "больше"], ["equal", "равно"], ["not_equal", "не равно"],
      ["is_on", "включён"], ["is_off", "выключен"],
    ];
    const acts = [["open", "Открыть"], ["close", "Закрыть"], ["stop", "Стоп"], ["impulse", "Импульс (расписание)"]];
    const numeric = (op) => ["above", "below", "equal", "not_equal"].includes(op);
    const rules = JSON.parse(JSON.stringify(this._settings?.interlocks || []));
    const activeAll = (this._interlocks?.active || []);
    const sensors = this._entities(["sensor", "binary_sensor", "input_number", "input_boolean", "number"]);

    const hint = document.createElement("div");
    hint.className = "gv-hint";
    hint.innerHTML = "<b>Запрет по сенсору:</b> пока условие на сенсоре выполнено, выбранные команды " +
      "не выполняются (<b>запрещать</b>) или выполняются с предупреждением в журнале (<b>только предупреждать</b>). " +
      "Пример: «Уличная температура меньше −25 → запретить закрытие». Недоступный сенсор запретом не считается.";
    host.appendChild(hint);

    const list = document.createElement("div");
    host.appendChild(list);

    rules.forEach((rule, i) => {
      rule.op = rule.op || "below";
      rule.mode = rule.mode || "block";
      rule.actions = Array.isArray(rule.actions) ? rule.actions : [];
      rule.enabled = rule.enabled !== false;
      const card = document.createElement("div");
      card.className = "gv-ev";
      card.style.marginBottom = "10px";
      const st = this._hass?.states?.[rule.entity_id];
      const current = st ? st.state : "—";
      const isActive = activeAll.some((a) => a.id === rule.id);
      card.innerHTML = `
        <div class="gv-row" style="margin:0 0 6px">
          <label style="min-width:auto">Правило ${i + 1}</label>
          <input class="gv-inp gv-name" style="flex:1" value="${(rule.name || "").replace(/"/g, "&quot;")}" placeholder="название (например «Мороз: не закрывать»)">
          ${rule.enabled ? "" : '<span class="gv-badge" style="background:#64748b;color:#fff">выключено</span>'}
          ${isActive ? `<span class="gv-badge" style="background:${rule.mode === "warn" ? "#d97706" : "#b91c1c"};color:#fff">сейчас ${rule.mode === "warn" ? "предупреждает" : "блокирует"}</span>` : ""}
          <label class="gv-chk" title="правило включено"><input type="checkbox" class="gv-en" ${rule.enabled ? "checked" : ""}> вкл</label>
          <button class="gv-btn secondary gv-del" title="удалить правило">✕</button>
        </div>
        <div class="gv-row">
          <label>Сенсор</label>
          <input class="gv-inp gv-ent" style="flex:1" list="gvSensors" value="${rule.entity_id || ""}" placeholder="sensor.temperatura">
          <span class="gv-meta">сейчас: <b>${current}</b></span>
        </div>
        <div class="gv-row">
          <label>Атрибут</label>
          <input class="gv-inp gv-attr" style="width:160px" value="${rule.attribute || ""}" placeholder="(пусто — состояние)">
          <label>Условие</label>
          <select class="gv-inp gv-op">${ops.map(([v, t]) => `<option value="${v}" ${v === rule.op ? "selected" : ""}>${t}</option>`).join("")}</select>
          <input class="gv-inp gv-val" type="number" step="any" style="width:110px" value="${rule.value ?? 0}" ${numeric(rule.op) ? "" : "disabled"}>
          <label>Режим</label>
          <select class="gv-inp gv-mode">
            <option value="block" ${rule.mode === "block" ? "selected" : ""}>запрещать</option>
            <option value="warn" ${rule.mode === "warn" ? "selected" : ""}>только предупреждать</option>
          </select>
        </div>
        <div class="gv-row">
          <label>Запрещать команды</label>
          ${acts.map(([v, t]) => `<label class="gv-chk"><input type="checkbox" class="gv-act" data-act="${v}" ${rule.actions.includes(v) ? "checked" : ""}> ${t}</label>`).join("")}
          <span class="gv-meta">ничего не отмечено = все команды</span>
        </div>`;
      list.appendChild(card);

      const upd = () => { this._dirty = true; };
      card.querySelector(".gv-name").oninput = (e) => { rule.name = e.target.value; upd(); };
      card.querySelector(".gv-ent").onchange = (e) => { rule.entity_id = e.target.value.trim(); upd(); };
      card.querySelector(".gv-attr").oninput = (e) => { rule.attribute = e.target.value.trim(); upd(); };
      card.querySelector(".gv-op").onchange = (e) => {
        rule.op = e.target.value; upd();
        card.querySelector(".gv-val").disabled = !numeric(rule.op);
      };
      card.querySelector(".gv-val").oninput = (e) => { rule.value = parseFloat(e.target.value) || 0; upd(); };
      card.querySelector(".gv-mode").onchange = (e) => { rule.mode = e.target.value; upd(); };
      card.querySelector(".gv-en").onchange = (e) => { rule.enabled = e.target.checked; upd(); };
      card.querySelectorAll(".gv-act").forEach((cb) => {
        cb.onchange = () => {
          rule.actions = Array.from(card.querySelectorAll(".gv-act")).filter((x) => x.checked).map((x) => x.dataset.act);
          upd();
        };
      });
      card.querySelector(".gv-del").onclick = () => {
        rules.splice(i, 1);
        this._save({ interlocks: rules });
      };
    });

    host.appendChild(this._datalistHtml("gvSensors", sensors));

    if (!rules.length) {
      const empty = document.createElement("div");
      empty.className = "gv-hint";
      empty.textContent = "Правил пока нет — нажмите «Добавить правило».";
      host.appendChild(empty);
    }

    const bar = document.createElement("div");
    bar.className = "gv-row";
    bar.style.marginTop = "8px";
    bar.innerHTML = `<button class="gv-btn secondary" id="gvIlAdd">＋ Добавить правило</button>
      <button class="gv-btn" id="gvIlSave">Сохранить запреты</button>
      <span class="gv-meta">правил: ${rules.length} · блокируют сейчас: ${activeAll.filter((a) => a.mode !== "warn").length}</span>`;
    host.appendChild(bar);
    bar.querySelector("#gvIlAdd").onclick = () => {
      rules.push({
        id: "i" + Date.now().toString(36),
        name: "Новое правило",
        entity_id: "",
        attribute: "",
        op: "below",
        value: 0,
        actions: [],
        mode: "block",
        enabled: true,
      });
      this._settings = { ...(this._settings || {}), interlocks: rules };
      this._renderTab();
    };
    bar.querySelector("#gvIlSave").onclick = () => this._save({ interlocks: rules });
  }

  _datalistHtml(id, items) {
    return `<datalist id="${id}">` + items.map((i) => `<option value="${i.value}">${i.label}</option>`).join("") + `</datalist>`;
  }

  _renderSchedules(host) {
    const list = (this._settings?.schedules || []).slice();
    const info = document.createElement("div");
    info.className = "gv-hint";
    info.innerHTML = "Автоматизации запускают реле ворот по расписанию (время местное). " +
      "Можно добавить сколько нужно. Действие: «импульс» — просто нажать; " +
      "«открыть»/«закрыть» — импульс только если ворота не в этом состоянии; «стоп» — если движутся. " +
      "Можно задать условие «выполнять, если состояние» и включить проверку результата по камере — " +
      "если состояние не подтвердится, в журнал попадёт запись.";
    host.appendChild(info);

    const render = () => {
      const box = host.querySelector("#gvSchedList");
      box.innerHTML = "";
      list.forEach((item, idx) => {
        const row = document.createElement("div");
        row.className = "gv-ev";
        row.innerHTML = `
          <div class="gv-row">
            <label><input type="checkbox" data-f="enabled" ${item.enabled ? "checked" : ""}> включено</label>
            <input type="text" data-f="name" value="${item.name || ""}" placeholder="название" style="flex:1 1 120px">
            <button class="gv-btn danger" style="padding:4px 9px" title="удалить">✕</button>
          </div>
          <div class="gv-row">
            <label>время</label><input type="time" data-f="time" value="${item.time || "10:00"}">
            <label>дни</label>
            <select data-days>
              ${[["ежедневно", "[0,1,2,3,4,5,6]"], ["будни (Пн–Пт)", "[0,1,2,3,4]"],
                 ["выходные (Сб, Вс)", "[5,6]"], ["свой набор", "custom"]]
                .map(([t, v]) => `<option value="${v}" ${(v !== "custom" && JSON.stringify(item.days || []) === v) ? "selected" : ""}>${t}</option>`).join("")}
            </select>
            <label>действие</label>
            <select data-f="action">
              <option value="impulse" ${item.action === "impulse" ? "selected" : ""}>импульс</option>
              <option value="open" ${item.action === "open" ? "selected" : ""}>открыть</option>
              <option value="close" ${item.action === "close" ? "selected" : ""}>закрыть</option>
              <option value="stop" ${item.action === "stop" ? "selected" : ""}>стоп</option>
            </select>
          </div>
          <div class="gv-row">
            <label>выполнять, если состояние</label>
            <select data-f="require_state">
              <option value="any" ${(item.require_state || "any") === "any" ? "selected" : ""}>любое</option>
              <option value="closed" ${item.require_state === "closed" ? "selected" : ""}>закрыто</option>
              <option value="open" ${item.require_state === "open" ? "selected" : ""}>открыто</option>
              <option value="moving" ${item.require_state === "moving" ? "selected" : ""}>движется</option>
            </select>
            <label><input type="checkbox" data-f="verify" ${item.verify !== false ? "checked" : ""}> проверять результат по камере</label>
          </div>
          <div class="gv-ch" data-dayscustom style="display:none">
            ${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d, i) =>
              `<label><input type="checkbox" data-day="${i}" ${(item.days || []).includes(i) ? "checked" : ""}> ${d}</label>`).join("")}
          </div>
          <div class="gv-meta">последний запуск: ${item.last_run ? item.last_run.replace("T", " ").slice(0, 19) : "—"}</div>`;
        const daysSel = row.querySelector("[data-days]");
        const custom = row.querySelector("[data-dayscustom]");
        if (daysSel.value === "custom") custom.style.display = "";
        daysSel.onchange = () => { custom.style.display = daysSel.value === "custom" ? "" : "none"; };
        row.querySelector("button").onclick = () => { list.splice(idx, 1); render(); };
        box.appendChild(row);
      });
    };

    const wrap = document.createElement("div");
    wrap.innerHTML = `<div id="gvSchedList"></div>
      <div class="gv-ch" style="margin-top:8px">
        <button class="gv-btn secondary" id="gvSchedAdd">+ Добавить автоматизацию</button>
        <button class="gv-btn" id="gvSchedSave">Сохранить расписания</button>
      </div>`;
    host.appendChild(wrap);
    render();

    wrap.querySelector("#gvSchedAdd").onclick = () => {
      list.push({ id: "s" + Date.now().toString().slice(-6), name: "Автоматизация " + (list.length + 1),
                  enabled: true, time: "10:00", days: [0, 1, 2, 3, 4, 5, 6], action: "impulse",
                  require_state: "any", verify: true });
      render();
    };
    wrap.querySelector("#gvSchedSave").onclick = () => {
      const rows = [...wrap.querySelectorAll("#gvSchedList .gv-ev")];
      const patch = rows.map((row, idx) => {
        const daysSel = row.querySelector("[data-days]").value;
        let days = list[idx].days || [0, 1, 2, 3, 4, 5, 6];
        if (daysSel === "custom") {
          days = [...row.querySelectorAll("[data-day]")].filter((c) => c.checked).map((c) => parseInt(c.dataset.day, 10));
        } else {
          days = JSON.parse(daysSel);
        }
        const get = (f) => { const el = row.querySelector(`[data-f="${f}"]`); return el ? (el.type === "checkbox" ? el.checked : el.value) : undefined; };
        return { id: list[idx].id, name: get("name"), enabled: get("enabled"), time: get("time"),
                 days, action: get("action"), require_state: get("require_state"),
                 verify: get("verify"), last_run: list[idx].last_run || null };
      });
      this._save({ schedules: patch });
    };
  }

  _renderCamera(host) {
    const s = this._settings || {};
    const a = this._state || {};
    const wrap = document.createElement("div");
    wrap.innerHTML = `
      <div class="gv-row"><label>Камера в Home Assistant</label>
        <input type="text" list="gv-cam" data-f="camera_entity" value="${s.camera_entity || ""}" placeholder="выбрать camera.* (рекомендуется)">
        ${this._datalist("gv-cam", this._entities(["camera"]))}</div>
      <div class="gv-row"><label>…или кадр напрямую (RTSP / имя потока / URL)</label>
        <input type="text" data-f="snapshot_url" value="${s.snapshot_url || ""}" placeholder="rtsp://... или имя потока go2rtc"></div>
      <div class="gv-row"><label>go2rtc</label>
        <input type="text" data-f="go2rtc_base" value="${s.go2rtc_base || ""}"></div>
      <div class="gv-row"><label>Интервал опроса, с</label>
        <input type="number" data-f="scan_interval" value="${s.scan_interval || 5}"></div>
      <div class="gv-row"><label>Сообщать, что открыты дольше, мин</label>
        <input type="number" data-f="left_open_min" value="${s.left_open_min || 15}"></div>
      <div class="gv-row"><label><input type="checkbox" data-f="learn_mode" ${s.learn_mode ? "checked" : ""}> Режим обучения (только пишет, не сообщает)</label></div>
      <button class="gv-btn">Сохранить</button>
      <div class="gv-hint">Кадр: ${a.frame || "—"} · режим: ${a.mode || "—"} · средняя насыщенность: ${a.mean_sat ?? "—"}<br>
      Причина решения: ${a.reason || "—"}</div>`;
    wrap.querySelector("button").onclick = () => {
      const patch = {};
      wrap.querySelectorAll("[data-f]").forEach((el) => {
        patch[el.dataset.f] = el.type === "checkbox" ? el.checked
          : el.type === "number" ? parseFloat(el.value || "0") : el.value;
      });
      this._save(patch);
    };
    host.appendChild(wrap);
  }

  /* время события в местной зоне (раньше в журнале был UTC) */

  _fmtTime(ts) {

    if (!ts) return "";

    const d = new Date(ts);

    if (isNaN(d.getTime())) return String(ts).replace("T", " ").slice(0, 19);

    const p = (n) => String(n).padStart(2, "0");

    return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;

  }


  _renderLog(host) {
    const wrap = document.createElement("div");
    wrap.className = "gv-log";
    const events = (this._log || []).slice().reverse();
    if (!events.length) wrap.textContent = "Пока событий нет";
    events.forEach((e) => {
      const div = document.createElement("div");
      const time = this._fmtTime(e.ts);
      div.innerHTML = `<b>${time}</b> — ${e.event || ""} <span class="gv-meta">${e.reason || ""}</span>`;
      wrap.appendChild(div);
    });
    host.appendChild(wrap);
  }

  /* ------------------------------------------------------------------ зоны мышью */
  _pos(e) {
    const rect = this._canvas.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left) / rect.width,
      y: (e.clientY - rect.top) / rect.height,
    };
  }

  _hitZone(p) {
    const zones = (this._settings?.zones || []).slice().reverse();
    for (const z of zones) {
      if (p.x >= z.x && p.x <= z.x + z.w && p.y >= z.y && p.y <= z.y + z.h) {
        const nearCorner =
          p.x > z.x + z.w - 0.04 && p.y > z.y + z.h - 0.04;
        return { zone: z, resize: nearCorner };
      }
    }
    return null;
  }

  _onPointerDown(e) {
    if (!this._settings) return;
    const p = this._pos(e);
    const hit = this._hitZone(p);
    if (hit) {
      this._selectedZone = hit.zone.id;
      if (e.ctrlKey || e.metaKey || e.shiftKey) {
        const sel = this._zoneSelection || (this._zoneSelection = new Set());
        if (sel.has(hit.zone.id)) sel.delete(hit.zone.id); else sel.add(hit.zone.id);
      }
      this._drag = { zone: hit.zone, resize: hit.resize, start: p, orig: { ...hit.zone } };
    } else {
      const id = `z${Date.now().toString().slice(-6)}`;
      const zone = { id, name: `Зона ${id}`, role: this._newRole, x: p.x, y: p.y, w: 0.05, h: 0.05 };
      this._settings.zones = [...(this._settings.zones || []), zone];
      this._selectedZone = id;
      this._drag = { zone, resize: true, start: p, orig: { ...zone } };
      this._dirty = true;
    }
    this._renderZoneList();
    this._draw();
  }

  _onPointerMove(e) {
    if (!this._drag) return;
    const p = this._pos(e);
    const { zone, orig, start, resize } = this._drag;
    const dx = p.x - start.x, dy = p.y - start.y;
    if (resize) {
      zone.w = Math.min(1 - orig.x, Math.max(0.01, orig.w + dx));
      zone.h = Math.min(1 - orig.y, Math.max(0.01, orig.h + dy));
    } else {
      zone.x = Math.min(1 - orig.w, Math.max(0, orig.x + dx));
      zone.y = Math.min(1 - orig.h, Math.max(0, orig.y + dy));
    }
    this._draw();
  }

  _onKey(e) {
    const zone = (this._settings?.zones || []).find((z) => z.id === this._selectedZone);
    if (!zone) return;
    const step = e.shiftKey ? 0.001 : 0.005;
    const map = { ArrowLeft: ["x", -1], ArrowRight: ["x", 1], ArrowUp: ["y", -1], ArrowDown: ["y", 1] };
    const act = map[e.key];
    if (!act) return;
    e.preventDefault();
    if (act[0] === "x") zone.x = Math.max(0, Math.min(1 - zone.w, zone.x + act[1] * step));
    else zone.y = Math.max(0, Math.min(1 - zone.h, zone.y + act[1] * step));
    this._renderZoneList();
    this._draw();
  }

  _onPointerUp() {
    if (this._drag) {
      const { zone } = this._drag;
      ["x", "y", "w", "h"].forEach((k) => { zone[k] = Math.round(zone[k] * 10000) / 10000; });
      this._dirty = true;
      this._scheduleZoneSave();
      this._renderZoneList();
    }
    this._drag = null;
  }

  /** Автосохранение зон через 0.7 с после правки (чтобы не дёргать HA на каждый пиксель). */
  _scheduleZoneSave() {
    if (this._zoneSaveTimer) clearTimeout(this._zoneSaveTimer);
    this._zoneSaveTimer = setTimeout(() => {
      const zones = (this._settings?.zones || []).map((z) => ({ ...z }));
      this._save({ zones });
    }, 700);
  }

  _toast(message) {
    const el = document.createElement("div");
    el.textContent = message;
    el.style.cssText = "position:fixed;bottom:22px;left:50%;transform:translateX(-50%);" +
      "background:#111;color:#fff;padding:10px 18px;border-radius:8px;z-index:9999;font-size:14px";
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2600);
  }
}

if (!customElements.get("gate-vision-panel")) {
  customElements.define("gate-vision-panel", GateVisionPanel);
}
