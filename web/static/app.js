/* Invest-Ratgeber: Kern (Alpine.js + ECharts). Alles Deutsch, Zahlen im de-DE-Format.
 *
 * Aufbau (modular): Jede Datei registriert mit part(() => ({ … })) Zustand und Methoden.
 *   app.js    Kern: Anmeldung, Navigation, Formatierung, Farben, Markt-Helfer
 *   pages.js  Überblick, Start-Assistent, Bestand, Ziele, Märkte, Einstellungen, Methodik
 *   calc.js   Rechner-Galerie, generische Rechner, Simulationen mit echten Kursen
 *   charts.js alle Diagramme
 *   chat.js   KI-Ratgeber
 * Neue Seite: Abschnitt in index.html, Route in ROUTES, Lade-Funktion in einem Teil.
 */
"use strict";

const PARTS = [];
function part(fn) { PARTS.push(fn); }
function app() {
  const o = {};
  for (const f of PARTS) Object.defineProperties(o, Object.getOwnPropertyDescriptors(f()));
  return o;
}

const NF = {};
function nf(d) { return NF[d] || (NF[d] = new Intl.NumberFormat("de-DE", { minimumFractionDigits: d, maximumFractionDigits: d })); }
const MINUS = "−";
const WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
const PERIOD = { "24h": "24 Std.", "7d": "7 Tage", "30d": "30 Tage", "1y": "1 Jahr", "5y": "5 Jahre" };
// Farben: feste Plätze für die Kernmärkte, alle anderen bekommen der Reihe nach einen der übrigen Plätze
const FIXED_SLOT = { BTC: 1, GOLD: 2, SILVER: 3, MSCIW: 4 };
const FREE_SLOTS = [5, 6, 7];
const ROUTES = ["ueberblick", "start", "bestand", "ziele", "rechner", "maerkte", "einstellungen", "methodik"];

async function api(path, opts = {}) {
  const o = { method: opts.method || "GET", headers: { "X-Invest": "1" }, credentials: "same-origin" };
  if (opts.body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
  const r = await fetch(path, o);
  let j = null;
  try { j = await r.json(); } catch (e) { j = null; }
  if (!r.ok) {
    const err = new Error((j && j.detail) || ("Fehler " + r.status));
    err.status = r.status;
    throw err;
  }
  return j;
}

function store(key, val) {
  try {
    if (val === undefined) { const v = localStorage.getItem(key); return v ? JSON.parse(v) : null; }
    localStorage.setItem(key, JSON.stringify(val));
  } catch (e) { return null; }
  return null;
}

function debounce(fn, ms) {
  let t = null;
  return function (...args) { clearTimeout(t); t = setTimeout(() => fn.apply(this, args), ms); };
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// Deutsche Monatsnamen für die Zeitachsen
echarts.registerLocale("DE", {
  time: {
    month: ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    monthAbbr: ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"],
    dayOfWeek: ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"],
    dayOfWeekAbbr: ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"],
  },
  legend: { selector: { all: "Alle", inverse: "Umkehren" } },
  toolbox: {},
  series: { typeNames: {} },
  aria: {},
});

part(() => ({
  ready: false, meta: null, me: null, profiles: [], route: "ueberblick", sub: "", params: {}, toast: "", statusText: "",
  login: { sel: null, pin: "", err: "", creating: false, name: "", newpin: "", currency: "EUR" },
  nav: [["ueberblick", "Überblick"], ["bestand", "Mein Bestand"], ["ziele", "Ziele"], ["rechner", "Rechner"],
    ["maerkte", "Märkte"], ["einstellungen", "Einstellungen"]],
  help: {}, weekdays: WEEKDAYS, _pal: null,

  async init() {
    window.addEventListener("hashchange", () => this.onHash());
    window.addEventListener("resize", debounce(() => this.resizeCharts(), 150));
    try {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { this._pal = null; this.redraw(); });
    } catch (e) { /* alte Browser */ }
    try { this.meta = await api("/api/meta"); } catch (e) { this.meta = null; }
    try { const s = await api("/api/session"); this.me = s.profile; } catch (e) { this.me = null; }
    if (!this.me) { this.profiles = await api("/api/profiles"); }
    this.ready = true;
    if (this.me) { this.afterLogin(); }
    this.loadStatus();
    setInterval(() => this.loadStatus(), 60000);
  },

  // ---------- Anmeldung ----------
  initials(n) { return (n || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase(); },
  pickProfile(p) {
    this.login.sel = p; this.login.err = ""; this.login.pin = "";
    if (!p.has_pin) { this.doLogin(); } else { this.$nextTick(() => this.$refs.pin && this.$refs.pin.focus()); }
  },
  async doLogin() {
    try {
      this.me = await api("/api/login", { method: "POST", body: { id: this.login.sel.id, pin: this.login.pin } });
      this.afterLogin();
    } catch (e) { this.login.err = e.message; }
  },
  async createProfile() {
    try {
      this.me = await api("/api/profiles", {
        method: "POST",
        body: { name: this.login.name, pin: this.login.newpin || null, settings: { currency: this.login.currency } },
      });
      this.login.creating = false;
      location.hash = "#/start";
      this.afterLogin();
    } catch (e) { this.login.err = e.message; }
  },
  async logout() {
    await api("/api/logout", { method: "POST" });
    this.me = null;
    this.resetData();
    this.disposeCharts();
    this.login = { sel: null, pin: "", err: "", creating: false, name: "", newpin: "", currency: "EUR" };
    this.profiles = await api("/api/profiles");
  },
  resetData() {
    Object.assign(this, { ov: null, pf: null, hist: null, res: null, gres: null, st: null, fin: null, mk: null, mkAll: null,
      ai: null, calcList: null });
    this.chat = this.freshChat();
  },
  afterLogin() {
    this.resetData();
    this.loadCalcState();
    this.st = JSON.parse(JSON.stringify(this.me.settings));
    this.stName = this.me.name;
    if (!location.hash || location.hash === "#" || location.hash === "#/") { location.hash = "#/ueberblick"; }
    this.onHash();
    this.loadChatState();
  },

  // ---------- Navigation ----------
  go(h) { location.hash = h; },
  navOn(r) { return this.route === r || (r === "ziele" && this.route === "start"); },
  onHash() {
    if (!this.me) return;
    const h = location.hash.replace(/^#\/?/, "");
    const [path, query] = h.split("?");
    const parts = path.split("/");
    let route = parts[0] || "ueberblick";
    this.params = Object.fromEntries(new URLSearchParams(query || ""));
    if (route === "assistent") {  // Link aus Vorschlägen: Ratgeber öffnen, Seite bleibt
      this.openChat();
      history.replaceState(null, "", "#/" + (this.route || "ueberblick"));
      if (this.ov || this.route !== "ueberblick") return;
      route = "ueberblick";
    }
    if (!ROUTES.includes(route)) route = "ueberblick";
    this.route = route;
    this.sub = decodeURIComponent(parts[1] || "");
    this.disposeCharts();
    window.scrollTo(0, 0);
    this.$nextTick(() => { const a = document.querySelector("nav.main a.on"); if (a) a.scrollIntoView({ inline: "center", block: "nearest" }); });
    const loaders = { ueberblick: "loadOverview", start: "openStart", bestand: "loadPortfolio", ziele: "loadFinance",
      rechner: "openCalc", maerkte: "openMarkets", einstellungen: "loadSettingsPage", methodik: "loadMethodik" };
    this[loaders[route]]();
  },
  async guard(fn) {
    try { return await fn(); } catch (e) {
      if (e.status === 401) { this.me = null; this.profiles = await api("/api/profiles"); return null; }
      this.flash(e.message); return null;
    }
  },
  flash(t) { this.toast = t; clearTimeout(this._toastT); this._toastT = setTimeout(() => { this.toast = ""; }, 3200); },
  async loadStatus() {
    try {
      const s = await api("/api/status");
      this.statusText = s.collect ? "Kurse aktualisiert " + this.timeStr(s.collect) : "";
    } catch (e) { this.statusText = ""; }
  },
  async refreshMe() {
    const me = await this.guard(() => api("/api/me"));
    if (me) { this.me = me; this.st = JSON.parse(JSON.stringify(me.settings)); }
    return me;
  },

  // ---------- Formatierung ----------
  cur() { return (this.me && this.me.settings.currency) || "EUR"; },
  curSym(c) { const k = c || this.cur(); return (this.meta && this.meta.currencies[k] && this.meta.currencies[k].symbol) || k; },
  grams(x) {
    if (x === null || x === undefined || Number.isNaN(x)) return "–";
    return new Intl.NumberFormat("de-DE", { maximumFractionDigits: x >= 100 ? 0 : 2 }).format(x) + " g";
  },
  num(x, d = 0) { if (x === null || x === undefined || Number.isNaN(x)) return "–"; return nf(d).format(x).replace("-", MINUS); },
  money(x, c) {
    if (x === null || x === undefined || Number.isNaN(x)) return "–";
    const a = Math.abs(x);
    const d = a >= 1000 ? 0 : a < 1 && a > 0 ? 4 : 2;
    return nf(d).format(x).replace("-", MINUS) + " " + this.curSym(c);
  },
  signMoney(x) { if (x === null || x === undefined) return "–"; return (x >= 0 ? "+" : MINUS) + this.money(Math.abs(x)); },
  pct(x, d = 1) {
    if (x === null || x === undefined || Number.isNaN(x)) return "–";
    const v = x * 100;
    return (v > 0 ? "+" : v < 0 ? MINUS : "±") + nf(d).format(Math.abs(v)) + " %";
  },
  cls(x) { if (x === null || x === undefined) return ""; return x > 0 ? "up" : x < 0 ? "down" : ""; },
  wordIcon(w) { return { steigt: "↗", "fällt": "↘" }[w] || "→"; },
  wordCls(w) { return { steigt: "up-t", "fällt": "down-t" }[w] || "muted"; },
  periodName(k) { return PERIOD[k] || k; },
  timeStr(ts) {
    if (!ts) return "–";
    const d = new Date(ts * 1000);
    const today = new Date();
    const t = d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === today.toDateString() ? "heute " + t : d.toLocaleDateString("de-DE") + " " + t;
  },
  dateStr(s) { if (!s) return "–"; const [y, m, d] = s.slice(0, 10).split("-"); return d ? `${d}.${m}.${y}` : `${m}/${y}`; },
  yearsAgo(y) { const d = new Date(); d.setFullYear(d.getFullYear() - y); return d.toISOString().slice(0, 7); },
  toggleHelp(k) { this.help[k] = !this.help[k]; },
  fineShort(f) { return f.label.split(" (")[0]; },
  escapeHtml(s) { return escapeHtml(s); },

  // ---------- Farben ----------
  palette() {
    if (this._pal) return this._pal;
    const cs = getComputedStyle(document.documentElement);
    const p = {};
    for (let i = 1; i <= 7; i++) p[i] = cs.getPropertyValue("--cat-" + i).trim();
    p.invest = cs.getPropertyValue("--c-INVEST").trim();
    this._pal = p;
    return p;
  },
  col(a) {
    const p = this.palette();
    if (FIXED_SLOT[a]) return p[FIXED_SLOT[a]];
    const keys = this.meta ? Object.keys(this.meta.assets).filter((k) => !FIXED_SLOT[k]) : [];
    const i = Math.max(keys.indexOf(a), 0);
    return p[FREE_SLOTS[i % FREE_SLOTS.length]];
  },
  seriesColors(assets) {
    // Für Diagramme mit mehreren Anlagen: keine doppelten Farben (bis 7), Kernmärkte behalten ihre Farbe
    const p = this.palette(), out = {}, used = new Set();
    for (const a of assets) if (FIXED_SLOT[a]) { out[a] = p[FIXED_SLOT[a]]; used.add(FIXED_SLOT[a]); }
    const free = [5, 6, 7, 1, 2, 3, 4].filter((s) => !used.has(s));
    let k = 0;
    for (const a of assets) if (!out[a]) { out[a] = p[free[k % free.length]]; k += 1; }
    return out;
  },

  // ---------- Märkte (Stammdaten) ----------
  assetName(a) { return (this.meta && this.meta.assets[a] && this.meta.assets[a].name) || a; },
  shortName(a) { return (this.meta && this.meta.assets[a] && this.meta.assets[a].short) || this.assetName(a); },
  isMetal(a) { return !!(this.meta && this.meta.assets[a] && this.meta.assets[a].metal); },
  marketGroups() {
    if (!this.meta) return [];
    return this.meta.kinds.map((k) => ({ id: k.id, label: k.label,
      items: Object.keys(this.meta.assets).filter((a) => this.meta.assets[a].kind === k.id) })).filter((g) => g.items.length);
  },
  kindTabs() { return this.marketGroups().map((g) => ({ id: g.id, label: g.label })); },
  assetsOfKind(kind, q) {
    const g = this.marketGroups().find((x) => x.id === kind);
    let items = g ? g.items : [];
    if (q) { const s = q.toLowerCase(); items = items.filter((a) => (this.assetName(a) + " " + this.meta.assets[a].symbol).toLowerCase().includes(s)); }
    return items;
  },
  taxName() {
    const s = this.me.settings;
    return { de: "Deutschland, vereinfacht", none: "keine", flat: "pauschal " + Math.round(s.flat_rate * 100) + " %" }[s.tax];
  },
  purposeName(k) { return (this.meta && this.meta.goal_purposes[k]) || k; },
}));
