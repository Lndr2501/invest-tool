/* Seiten: Überblick, Start-Assistent, Bestand, Ziele, Märkte, Einstellungen, Methodik. */
"use strict";

const PURPOSE_ICON = { emergency: "🛟", purchase: "🚗", home: "🏠", education: "🎓", retirement: "👵", wealth: "🌱", other: "✨" };
const PURPOSE_NAME = { emergency: "Notgroschen", purchase: "Anschaffung", home: "Eigenes Zuhause", education: "Ausbildung",
  retirement: "Altersvorsorge", wealth: "Vermögen aufbauen", other: "Mein Ziel" };
const PURPOSE_YEARS = { emergency: 1, purchase: 3, home: 8, education: 10, retirement: 30, wealth: 15, other: 5 };

function freshWizard() {
  return { step: 1, helpIncome: false, res: null, busy: false, saved: false,
    a: { purpose: "", name: "", years: 10, amount: null, monthly: null, income: null, expenses: null, cash: null, debt: null,
      reaction: "", experience: "none" } };
}

part(() => ({
  // ---------- Überblick ----------
  ov: null,
  askChips: { ueberblick: ["Was heißt das alles für mich?", "Wie fange ich mit 50 € im Monat an?",
    "Was ist ein ETF?", "Muss ich auf Gewinne Steuern zahlen?"] },
  ovNext() {
    if (!this.ov) return [];
    // Ist der Bestand leer, zeigt die Bestand-Karte schon den Einstieg – Doppeltes weglassen
    const dup = this.ov.portfolio.empty ? ["#/start", "#/bestand", "#/ziele", "#/einstellungen"] : [];
    return this.ov.next.filter((n) => !dup.includes(n.link));
  },
  async loadOverview() {
    const ov = await this.guard(() => api("/api/overview"));
    if (ov) this.ov = ov;
  },

  // ---------- Start-Assistent ----------
  wz: freshWizard(),
  horizons: [[2, "in 1–2 Jahren", "z. B. Urlaub, Auto, Umzug"], [4, "in 3–5 Jahren", "z. B. Führerschein, Möbel, Hochzeit"],
    [8, "in 5–10 Jahren", "z. B. Anzahlung für eine Wohnung"], [15, "in mehr als 10 Jahren", "Vermögen aufbauen"],
    [30, "erst im Alter", "Geld für die Rente"]],
  reactions: [["sell", "Alles verkaufen", "Ich will nicht noch mehr verlieren."],
    ["worry", "Schlecht schlafen, aber behalten", "Unangenehm, ich würde wohl nichts tun."],
    ["wait", "Ruhig abwarten", "Das gehört dazu, es erholt sich meist wieder."],
    ["buy", "Weiter sparen wie geplant", "Jetzt bekomme ich mehr für mein Geld."],
    ["buy_more", "Extra dazukaufen", "Eine gute Gelegenheit!"]],
  purposeList() {
    return Object.keys((this.meta && this.meta.goal_purposes) || {}).map((k) =>
      [k, this.meta.goal_purposes[k], PURPOSE_NAME[k], PURPOSE_ICON[k] || "•"]);
  },
  openStart() {
    if (this.wz.step === 6 && this.wz.res) return;
    const f = (this.me.settings.finance) || {};
    const a = this.wz.a;
    for (const k of ["income", "expenses", "cash", "debt"]) if (a[k] === null && f[k] !== null && f[k] !== undefined) a[k] = f[k];
    if (a.monthly === null && f.saving) a.monthly = f.saving;
    if (f.experience) a.experience = f.experience;
  },
  wzNext() {
    if (this.wz.step === 1 && PURPOSE_YEARS[this.wz.a.purpose] && !this.wz._yearsSet) {
      this.wz.a.years = PURPOSE_YEARS[this.wz.a.purpose]; this.wz._yearsSet = true;
    }
    if (this.wz.step === 3 && !this.wz.a.monthly && this.wz.a.income) this.wz.a.monthly = this.suggestSaving();
    this.wz.step += 1;
    window.scrollTo(0, 0);
  },
  suggestSaving() {
    const inc = Number(this.wz.a.income) || 0, free = inc - (Number(this.wz.a.expenses) || 0);
    if (free <= 0) return 0;
    const s = free < inc * 0.2 ? free * 0.7 : Math.min(free * 0.7, inc * 0.2);
    return Math.round(s / 5) * 5;
  },
  async wzFinish() {
    this.wz.busy = true;
    const r = await this.guard(() => api("/api/start", { method: "POST", body: this.wz.a }));
    this.wz.busy = false;
    if (r) { this.wz.res = r; this.wz.step = 6; this.wz.saved = false; window.scrollTo(0, 0); }
  },
  mixCol(m) { return m.asset ? this.col(m.asset) : this.palette().invest; },
  wzCalcLink() {
    const r = this.wz.res;
    if (!r) return "#/rechner";
    if (r.horizon === "short") return `#/rechner/compound?start=0&monthly=${r.monthly || 100}&years=${Math.round(r.years)}&rate=2`;
    const w = Object.entries(r.weights).map(([a, v]) => a + ":" + v).join(",");
    return `#/rechner/future?initial=0&monthly=${r.monthly || 100}&years=${Math.round(r.years)}&w=${w}`;
  },
  async wzSave() {
    const a = this.wz.a, r = this.wz.res;
    const goals = ((this.me.settings.finance || {}).goals || []).slice(0, 7);
    goals.push({ name: a.name || PURPOSE_NAME[a.purpose] || "Mein Ziel", purpose: a.purpose || "other",
      amount: a.amount || null, years: r.years });
    const finance = Object.assign({}, r.save, { goals });
    const me = await this.guard(() => api("/api/me", { method: "PUT", body: { settings: { finance } } }));
    if (me) { this.me = me; this.wz.saved = true; this.ov = null; this.fin = null; this.flash("Plan gespeichert ✓"); }
  },
  wzReset() { this.wz = freshWizard(); this.openStart(); },

  // ---------- Bestand ----------
  pf: null, hist: null, histMode: "total", pfTab: "list", hf: null, hfInfo: null, plf: null,
  async loadPortfolio() {
    if (this.params.tab) this.pfTab = this.params.tab;
    const pf = await this.guard(() => api("/api/portfolio"));
    if (pf) this.pf = pf;
    if (this.pfTab === "history" && pf && !pf.empty) await this.loadHistory();
    if (this.params.add) { this.openHolding(); }
  },
  pfTotal() { return (this.pf && this.pf.total) || (this.ov && this.ov.portfolio.total) || 0; },
  async setPfTab(t) { this.pfTab = t; if (t === "history") await this.loadHistory(); },
  async loadHistory() {
    const h = await this.guard(() => api("/api/portfolio/history"));
    if (h) { this.hist = h; this.later(() => this.drawHistory()); }
  },
  groupedPositions() {
    if (!this.pf) return [];
    const out = {};
    for (const p of this.pf.positions) {
      const g = out[p.asset] || (out[p.asset] = { asset: p.asset, name: this.assetName(p.asset), value: 0, items: [] });
      g.items.push(p); g.value += p.value;
    }
    for (const a of this.pf.alloc) {
      if (out[a.asset]) out[a.asset].extra = (a.fine_g ? this.grams(a.fine_g) + " reines Metall · " : "") + this.num(a.share * 100, 0) + " % deines Bestands";
    }
    return Object.values(out).sort((a, b) => b.value - a.value);
  },
  openHolding(raw) {
    this.hfInfo = null;
    if (raw) {
      this.hf = Object.assign({ err: "", q: "" }, raw, { buy_price: raw.buy_price === null ? "" : raw.buy_price, buy_date: raw.buy_date || "" });
      this.doHfPreview();
    } else {
      this.hf = { id: null, asset: null, kind: "metal", q: "", err: "" };
    }
  },
  hfDefaults() {
    const a = this.hf.asset, metal = this.isMetal(a);
    const coins = (this.meta.coins[a] || []);
    const fin = (this.meta.fineness[a] || [{ value: 999.9 }])[0].value;
    Object.assign(this.hf, { form: metal ? (coins.length ? "coin" : "bar") : "units", count: 1, unit: "g", weight: 1,
      fineness: fin, coin: coins.length ? coins[0].id : null, quantity: null, buy_date: "", buy_price: "", label: "" });
    this.hfInfo = null;
    this.doHfPreview();
  },
  hfPreview() { clearTimeout(this._hp); this._hp = setTimeout(() => this.doHfPreview(), 300); },
  coinHasSizes() { const c = (this.meta.coins[this.hf.asset] || []).find((x) => x.id === this.hf.coin); return c && c.sizes; },
  hfBody() {
    const h = this.hf;
    const b = { asset: h.asset, form: h.form, count: h.count, weight: h.weight, unit: h.unit, fineness: h.fineness,
      coin: h.form === "coin" ? h.coin : null, quantity: h.quantity, buy_date: h.buy_date || null,
      buy_price: h.buy_price === "" || h.buy_price === null ? null : Number(h.buy_price), buy_cur: this.cur(), label: h.label };
    if (h.form === "coin" && !this.coinHasSizes()) b.weight = 1;
    return b;
  },
  async doHfPreview() {
    if (!this.hf || !this.hf.asset) return;
    try {
      const now = await api("/api/price-at", { method: "POST", body: Object.assign(this.hfBody(), { buy_date: null }) });
      let then = null;
      if (this.hf.buy_date) {
        try { then = (await api("/api/price-at", { method: "POST", body: this.hfBody() })).value; } catch (e) { then = null; }
      }
      let text = "Heute etwa " + this.money(now.value) + " wert";
      if (now.fine_g) text = this.grams(now.fine_g) + " reines " + this.assetName(this.hf.asset) + " · " + text;
      this.hfInfo = { now: now.value, then, text };
    } catch (e) { this.hfInfo = null; }
  },
  async saveHolding() {
    try {
      const body = this.hfBody();
      if (this.hf.id) await api("/api/holdings/" + this.hf.id, { method: "PUT", body });
      else await api("/api/holdings", { method: "POST", body });
      this.hf = null; this.flash("Gespeichert");
      if (this.params.add) { history.replaceState(null, "", "#/bestand"); this.params = {}; }
      this.hist = null; this.ov = null;
      if (this.route === "bestand") await this.loadPortfolio(); else this.go("#/bestand");
    } catch (e) { this.hf.err = e.message; }
  },
  async delHolding(id) {
    if (!confirm("Diesen Eintrag wirklich löschen?")) return;
    await this.guard(() => api("/api/holdings/" + id, { method: "DELETE" }));
    this.hist = null; this.ov = null; await this.loadPortfolio();
  },
  openPlan(id, preset) {
    const p = id && this.pf && this.pf.plans.find((x) => x.id === id);
    this.plf = p ? Object.assign({ err: "" }, p, { end: p.end || "" })
      : Object.assign({ id: null, asset: "MSCIW", amount: 50, cur: this.cur(), start: new Date().toISOString().slice(0, 10), end: "", err: "" }, preset || {});
  },
  async savePlan() {
    const p = this.plf;
    const body = { asset: p.asset, amount: p.amount, cur: p.cur, start: p.start, end: p.end || null };
    try {
      if (p.id) await api("/api/plans/" + p.id, { method: "PUT", body });
      else await api("/api/plans", { method: "POST", body });
      this.plf = null; this.flash("Sparplan gespeichert"); this.hist = null; this.ov = null;
      if (this.route === "bestand") await this.loadPortfolio(); else this.go("#/bestand");
    } catch (e) { this.plf.err = e.message; }
  },
  async delPlan(id) {
    if (!confirm("Diesen Sparplan wirklich löschen?")) return;
    await this.guard(() => api("/api/plans/" + id, { method: "DELETE" }));
    this.hist = null; this.ov = null; await this.loadPortfolio();
  },

  // ---------- Ziele & Finanzprofil ----------
  fin: null, finEdit: {}, gf: null,
  riskLevels: [[1, "sehr vorsichtig"], [2, "vorsichtig"], [3, "ausgewogen"], [4, "mutig"], [5, "sehr mutig"]],
  riskText(r) {
    return { 1: "Schwankungen machen dir Angst. Passend: viel Tagesgeld und Anleihen (Geld an Staaten oder Firmen verliehen, gegen Zinsen), wenig Aktien.",
      2: "Du magst es ruhig. Passend: mehr Anleihen (verliehenes Geld gegen Zinsen) als Aktien.",
      3: "Etwa halb Aktien, halb Sicheres. Hast du viele Jahre Zeit, auch mehr Aktien.",
      4: "Du hältst ein Minus von 30 % aus, aus 10.000 € werden also zeitweise 7.000 €. Passend: vor allem Aktien-ETFs (Körbe mit vielen Aktien).",
      5: "Abstürze sind für dich eine Chance zum Nachkaufen. Passend: fast nur Aktien, dazu wenig anderes." }[r]
      || "Wähle, wie gut du es aushältst, wenn dein Geld zeitweise weniger wert ist.";
  },
  async loadFinance() {
    const f = await this.guard(() => api("/api/finance"));
    if (f) {
      this.fin = f;
      const e = Object.assign({}, f.finance); delete e.goals;
      for (const k of Object.keys(e)) if (e[k] === null) e[k] = k === "experience" ? "" : null;
      this.finEdit = e;
    }
  },
  saveFin() { clearTimeout(this._sf); this._sf = setTimeout(() => this.doSaveFin(), 600); },
  async doSaveFin() {
    const f = {};
    for (const [k, v] of Object.entries(this.finEdit)) f[k] = v === "" || (typeof v === "number" && Number.isNaN(v)) ? null : v;
    const me = await this.guard(() => api("/api/me", { method: "PUT", body: { settings: { finance: f } } }));
    if (me) {
      this.me = me; this.ov = null; this.flash("Gespeichert ✓");
      const fr = await this.guard(() => api("/api/finance"));
      if (fr) this.fin = fr;
    }
  },
  openGoal(g) {
    this.gf = g ? { id: g.id, name: g.name, purpose: g.purpose, amount: g.amount, years: g.years, err: "" }
      : { id: null, name: "", purpose: "purchase", amount: 5000, years: 3, err: "" };
  },
  async putGoals(goals) {
    const me = await this.guard(() => api("/api/me", { method: "PUT", body: { settings: { finance: { goals } } } }));
    if (me) { this.me = me; this.ov = null; await this.loadFinance(); }
    return me;
  },
  async saveGoal() {
    const g = this.gf;
    if (!g.name.trim()) { g.err = "Bitte gib deinem Ziel einen Namen."; return; }
    const goals = this.fin.goals.map((x) => ({ id: x.id, name: x.name, purpose: x.purpose, amount: x.amount, years: x.years }));
    const item = { id: g.id, name: g.name, purpose: g.purpose, amount: g.amount || null, years: g.years || null };
    const i = goals.findIndex((x) => x.id === g.id);
    if (i >= 0) goals[i] = item; else goals.push(item);
    if (await this.putGoals(goals)) { this.gf = null; this.flash("Ziel gespeichert"); }
  },
  async delGoal(id) {
    if (!confirm("Dieses Ziel wirklich löschen?")) return;
    await this.putGoals(this.fin.goals.filter((x) => x.id !== id).map((x) => ({ id: x.id, name: x.name, purpose: x.purpose, amount: x.amount, years: x.years })));
  },
  goalNeed() { return this.fin ? this.fin.goals.reduce((s, g) => s + (g.plan ? g.plan.monthly : 0), 0) : 0; },
  goalNeedText() {
    const need = this.goalNeed(), have = this.finEdit.saving || this.fin.planned || 0;
    if (have >= need * 0.95) return `Für alle Ziele zusammen brauchst du etwa ${this.money(need)} im Monat. Mit ${this.money(have)} im Monat schaffst du das.`;
    return `Für alle Ziele zusammen brauchst du etwa ${this.money(need)} im Monat` + (have ? `. Bisher eingeplant: ${this.money(have)}.` : ". Trag unten ein, wie viel du im Monat sparen kannst.");
  },

  // ---------- Märkte ----------
  mkAll: null, mkKinds: [], mkKind: "", mkq: "", mkq2: "", mkResults: [], mkSearched: false, mkBusy: false, mkBusyText: "",
  mkAsset: "", mk: null, mkRange: "1y", showMa: false,
  ranges: [["1d", "Tag"], ["1w", "Woche"], ["1m", "Monat"], ["1y", "1 Jahr"], ["5y", "5 Jahre"], ["10y", "10 Jahre"], ["max", "Alles"]],
  openMarkets() {
    if (this.sub && this.meta.assets[this.sub]) {
      if (this.mkAsset !== this.sub) this.mk = null;
      this.mkAsset = this.sub; this.loadMarket();
    } else { this.mkAsset = ""; this.loadMarketList(); }
  },
  async loadMarketList() {
    const r = await this.guard(() => api("/api/markets"));
    if (r) { this.mkAll = r.markets; this.mkKinds = r.kinds; }
  },
  mkCount(kind) { return (this.mkAll || []).filter((m) => m.kind === kind).length; },
  mkFiltered() {
    let l = this.mkAll || [];
    if (this.mkKind === "watch") l = l.filter((m) => m.watched);
    else if (this.mkKind) l = l.filter((m) => m.kind === this.mkKind);
    const q = this.mkq.trim().toLowerCase();
    if (q) l = l.filter((m) => [m.name, m.short, m.symbol, m.isin || "", m.kind_label, m.id].join(" ").toLowerCase().includes(q));
    return l;
  },
  mkSearchClear() { if (this.mkq.length >= 2) this.mkq2 = this.mkq; },
  isWatched(a) { return this.me.settings.assets.includes(a); },
  async toggleWatch(m) {
    const r = await this.guard(() => api("/api/watch/" + m.id, { method: "PUT", body: { watch: !m.watched } }));
    if (r) {
      this.me.settings.assets = r.watch; this.ov = null;
      if (this.mkAll) for (const x of this.mkAll) x.watched = r.watch.includes(x.id);
      this.flash(r.watch.includes(m.id) ? "Wird beobachtet ★" : "Nicht mehr beobachtet");
    }
  },
  async mkSearch() {
    if (this.mkq2.trim().length < 2) return;
    this.mkBusy = true; this.mkBusyText = "Suche …";
    const r = await this.guard(() => api("/api/markets/search?q=" + encodeURIComponent(this.mkq2.trim())));
    this.mkBusy = false; this.mkSearched = true;
    this.mkResults = r ? r.results : [];
  },
  async mkAdd(r) {
    this.mkBusy = true; this.mkBusyText = "Lade alle bisherigen Kurse von " + r.symbol + " … Das dauert bis zu einer Minute.";
    const res = await this.guard(() => api("/api/markets", { method: "POST", body: { symbol: r.symbol } }));
    this.mkBusy = false;
    if (res) {
      try { this.meta = await api("/api/meta"); } catch (e) { /* bleibt */ }
      await this.refreshMe();
      this.mkAll = null; this.calcList = null; this._pal = null;
      this.flash(res.market.name + " hinzugefügt ★");
      this.go("#/maerkte/" + res.market.id);
    }
  },
  mkInfo() { return (this.mk && this.meta.assets[this.mk.asset]) || {}; },
  async loadMarket() {
    const m = await this.guard(() => api(`/api/market/${this.mkAsset}?range=${this.mkRange}`));
    if (m) { this.mk = m; this.later(() => { this.drawMarket(); this.drawYears(); }); }
  },
  corrText() {
    if (!this.mk || !this.mk.corr) return "";
    const parts = [];
    for (const [a, v] of Object.entries(this.mk.corr)) {
      if (v === null || !this.meta.assets[a]) continue;
      const how = v > 0.6 ? "oft gemeinsam" : v > 0.3 ? "teilweise gemeinsam" : v > -0.1 ? "weitgehend unabhängig" : "eher entgegengesetzt";
      parts.push(`${this.shortName(a)} ${how} (${this.num(v, 2)})`);
    }
    if (!parts.length) return "";
    return `Im letzten Jahr bewegte sich ${this.shortName(this.mk.asset)} mit ${parts.slice(0, 5).join(", ")}. Die Zahl reicht von 1 (immer gleich) bis −1 (immer entgegengesetzt). Je unabhängiger zwei Anlagen sind, desto weniger schwankt eine Mischung aus beiden.`;
  },
  yearsText() {
    if (!this.mk) return "";
    const full = this.mk.years.filter((y) => !y.partial);
    const up = full.filter((y) => y.ret > 0).length;
    if (!full.length) return "";
    const best = full.reduce((a, b) => (b.ret > a.ret ? b : a));
    const worst = full.reduce((a, b) => (b.ret < a.ret ? b : a));
    return `In ${up} von ${full.length} vollen Kalenderjahren stieg der Kurs. Bestes Jahr: ${best.year} (${this.pct(best.ret, 0)}), schlechtestes: ${worst.year} (${this.pct(worst.ret, 0)}). In Euro gerechnet.`;
  },

  // ---------- Einstellungen ----------
  st: null, stName: "", pinForm: { old: "", new: "" }, delForm: { confirm: "", pin: "" },
  tgUrl: "", tgPreview: "", tgLog: [], tgSchedule: "",
  ai: null, aiOwn: {}, aiGlob: {}, aiModels: { own: [], global: [] }, aiTest: { busy: false, res: null },
  aiEfforts: [["", "Standard"], ["none", "Kein Nachdenken"], ["low", "Wenig"], ["medium", "Mittel"],
    ["high", "Viel"], ["xhigh", "Sehr viel"]],
  async loadSettingsPage() {
    const me = await this.refreshMe();
    if (!me) return;
    this.stName = me.name;
    this.loadTgPreview(); this.loadTgStatus(); this.loadAi();
  },
  unitExample(a) {
    const key = this.meta.metals[a], m = this.st[key];
    const w = { g: "1 Gramm", oz: "1 Feinunze (31,1 g)", kg: "1 Kilo" }[m.unit];
    return `${w} ${this.assetName(a)} (Feinheit ${String(m.fineness).replace(".", ",")}) in ${this.st.currency}`;
  },
  saveSettings() { clearTimeout(this._ss); this._ss = setTimeout(() => this.doSaveSettings(), 500); },
  async doSaveSettings() {
    const s = JSON.parse(JSON.stringify(this.st)); delete s.ai; delete s.finance;
    const me = await this.guard(() => api("/api/me", { method: "PUT", body: { settings: s } }));
    if (me) {
      this.me = me; this.st = JSON.parse(JSON.stringify(me.settings));
      this.flash("Gespeichert ✓"); this.ov = null; this.pf = null; this.res = null; this.gres = null; this.mk = null; this.mkAll = null; this.calcList = null;
      this.loadTgPreview(); this.loadTgStatus();
    }
  },
  async saveName() {
    const me = await this.guard(() => api("/api/me", { method: "PUT", body: { name: this.stName } }));
    if (me) { this.me = me; this.flash("Name gespeichert"); } else { this.stName = this.me.name; }
  },
  async savePin() {
    const me = await this.guard(() => api("/api/me", { method: "PUT", body: { pin: this.pinForm.new || null, old_pin: this.pinForm.old } }));
    if (me) { this.me = me; this.pinForm = { old: "", new: "" }; this.flash(me.has_pin ? "PIN gespeichert" : "PIN entfernt"); }
  },
  async deleteProfile() {
    if (!confirm("Profil wirklich endgültig löschen? Bestand, Ziele und Einstellungen sind dann weg.")) return;
    const r = await this.guard(() => api("/api/me", { method: "DELETE", body: this.delForm }));
    if (r) { this.me = null; this.profiles = await api("/api/profiles"); location.hash = ""; }
  },
  async loadAi() {
    const a = await this.guard(() => api("/api/ai"));
    if (a) this.setAi(a);
  },
  setAi(a) {
    this.ai = a;
    this.aiOwn = Object.assign({}, a.own, { key: "" });
    this.aiGlob = Object.assign({}, a.global, { key: "" });
  },
  async saveAiOwn(withKey, clearKey) {
    const o = this.aiOwn;
    const body = { enabled: o.enabled, mode: o.mode, consent: !!o.consent, api: o.api, base_url: o.base_url || "", model: o.model || "",
      effort: o.effort || "", max_tokens: o.max_tokens || 8000 };
    if (withKey && o.key) body.key = o.key;
    if (clearKey) body.clear_key = true;
    const a = await this.guard(() => api("/api/ai/own", { method: "PUT", body }));
    if (a) { this.setAi(a); this.me.settings.ai = Object.assign({}, this.me.settings.ai, body); delete this.me.settings.ai.key; this.flash("Gespeichert ✓"); this.loadChatState(); }
    return a;
  },
  async saveAiGlobal(clearKey) {
    const g = this.aiGlob;
    const body = { enabled: !!g.enabled, base_url: g.base_url || "", model: g.model || "", api: g.api,
      effort: g.effort || "", max_tokens: g.max_tokens || 8000 };
    if (g.key) body.key = g.key;
    if (clearKey) body.clear_key = true;
    const a = await this.guard(() => api("/api/ai/global", { method: "PUT", body }));
    if (a) { this.setAi(a); this.flash("Gemeinsamer Zugang gespeichert ✓"); this.loadChatState(); }
    return a;
  },
  async testAi(scope) {
    const saved = scope === "global" ? await this.saveAiGlobal() : await this.saveAiOwn(true);
    if (!saved) return;
    this.aiTest = { busy: true, res: null };
    const r = await this.guard(() => api("/api/ai/test", { method: "POST", body: { scope } }));
    this.aiTest = { busy: false, res: r };
    if (r && r.models) this.aiModels[scope] = r.models;
  },
  aiTestText() {
    const r = this.aiTest.res;
    if (!r) return "";
    if (!r.ok) return "Klappt noch nicht: " + (r.error || "unbekannter Fehler") + (r.models && r.models.length ? " · Verfügbare Modelle: " + r.models.slice(0, 8).join(", ") : "");
    return `✓ Verbindung klappt (${this.num(r.ms / 1000, 1)} s). ` + (r.tools ? "Das Modell kann Werkzeuge nutzen: rechnen, deinen Bestand lesen, sich Dinge merken." : "Achtung: Das Modell hat kein Werkzeug benutzt. Der Ratgeber kann dann nicht rechnen und kennt nur eine Kurzfassung deiner Daten.") +
      (r.models.length ? " Verfügbare Modelle: " + r.models.slice(0, 8).join(", ") : "");
  },
  aiNeedsConsent() {
    if (!this.ai) return false;
    if (this.aiOwn.mode === "own") return !!this.aiOwn.base_url && !this.localUrl(this.aiOwn.base_url);
    return !!this.ai.global.base_url && !this.ai.global.local;
  },
  localUrl(u) {
    try {
      const h = new URL(u).hostname;
      return /^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) || /\.(local|lan|home)$/.test(h);
    } catch (e) { return false; }
  },
  async loadTgPreview() {
    const r = await this.guard(() => api("/api/telegram/preview"));
    if (r) this.tgPreview = escapeHtml(r.text).replace(/&lt;(\/?)(b|i)&gt;/g, "<$1$2>");
  },
  async loadTgStatus() {
    const r = await this.guard(() => api("/api/telegram/status"));
    if (r) { this.tgLog = r.log; this.tgSchedule = r.schedule; if (r.linked !== this.me.telegram) this.me.telegram = r.linked; }
  },
  async tgLink() {
    const r = await this.guard(() => api("/api/telegram/link", { method: "POST" }));
    if (r) {
      this.tgUrl = r.url; window.open(r.url, "_blank", "noopener");
      clearInterval(this._tgPoll);
      let n = 0;
      this._tgPoll = setInterval(async () => {
        n += 1;
        await this.loadTgStatus();
        if (this.me.telegram || n > 60) { clearInterval(this._tgPoll); if (this.me.telegram) this.flash("Telegram verbunden ✓"); }
      }, 3000);
    }
  },
  async tgTest() { const r = await this.guard(() => api("/api/telegram/test", { method: "POST" })); if (r) { this.flash("Bericht gesendet"); this.loadTgStatus(); } },
  async tgUnlink() {
    if (!confirm("Telegram wirklich trennen? Dann kommen dort keine Berichte mehr an.")) return;
    const r = await this.guard(() => api("/api/telegram", { method: "DELETE" }));
    if (r) { this.me.telegram = false; this.flash("Telegram getrennt"); this.loadTgStatus(); }
  },
  kindName(k) { return { report: "Bericht", report_manual: "Bericht (von Hand)", alert: "Alarm (starke Bewegung)" }[k] || k; },
  firstLine(t) { return (t || "").split("\n")[0].replace(/<[^>]+>/g, ""); },

  // ---------- Methodik ----------
  meth: null,
  async loadMethodik() { const m = await this.guard(() => api("/api/methodik")); if (m) this.meth = m; },
  covText(m, d) { const c = m.coverage.find((x) => x.days === d); return c ? this.num(c.cov90 * 100, 0) + " %" : "–"; },
  dirRows() {
    const out = [];
    for (const [a, m] of Object.entries(this.meth || {})) {
      for (const d of m.direction) out.push({ k: a + d.days, name: m.name, days: d.days, base: d.base, best: d.best || "–", best_hit: d.best_hit, approved: d.approved });
    }
    return out;
  },
}));
