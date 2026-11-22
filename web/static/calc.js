/* Rechner: Galerie, generische Rechner (Formular aus Feld-Beschreibung) und die drei Simulationen mit echten Kursen. */
"use strict";

part(() => ({
  calcList: null, calcCats: [], calcId: "", calcDef: null,
  gv: {}, gres: null, busy: false, calcErr: "", _calcSeq: 0,
  calc: {
    q: "future", initial: 1000, monthly: 100, years: 15, goal: 50000, start: "", use_holdings: false,
    weights: { MSCIW: 80, GOLD: 20 }, drift: "careful", costs: true, tax: true, inflation: true,
    rebalance: "none", wd_monthly: 0, wd_from: 20,
  },
  res: null, futView: "fan", fanNote: "",

  loadCalcState() {
    this.calc.start = this.calc.start || this.yearsAgo(10);
    const saved = store("calc:" + this.me.id);
    if (saved) Object.assign(this.calc, saved, { weights: saved.weights && Object.keys(saved.weights).length ? saved.weights : this.calc.weights });
    this.calc.weights = this.cleanWeights(this.calc.weights);
  },
  cleanWeights(w) {
    const out = {};
    for (const [a, v] of Object.entries(w || {})) if (this.meta && this.meta.assets[a]) out[a] = Number(v) || 0;
    return Object.keys(out).length ? out : { MSCIW: 100 };
  },
  async openCalc() {
    if (!this.calcList) {
      const r = await this.guard(() => api("/api/calcs"));
      if (!r) return;
      this.calcList = r.calcs; this.calcCats = r.categories;
    }
    this.calcId = this.sub;
    this.calcDef = this.calcList.find((c) => c.id === this.calcId) || null;
    if (this.calcId && !this.calcDef) { this.go("#/rechner"); return; }
    if (!this.calcDef) return;
    this.calcErr = "";
    if (this.calcDef.ui === "generic") this.openGeneric();
    else this.openCustom();
  },
  calcsIn(cat) { return (this.calcList || []).filter((c) => c.category === cat); },

  // ---------- generische Rechner ----------
  openGeneric() {
    const d = this.calcDef, saved = store(`gcalc:${this.me.id}:${d.id}`) || {};
    const v = {};
    for (const f of d.fields) {
      let x = f.key in saved ? saved[f.key] : f.default;
      if (this.params[f.key] !== undefined) x = f.type === "select" || f.type === "date" || f.type === "market" ? this.params[f.key]
        : f.type === "toggle" ? ["1", "true"].includes(this.params[f.key]) : Number(this.params[f.key]);
      v[f.key] = x;
    }
    this.gv = v; this.gres = null;
    this.runG();
  },
  fieldVisible(f) {
    if (!f.show_if) return true;
    return Object.entries(f.show_if).every(([k, v]) => String(this.gv[k]) === String(v));
  },
  fieldSuffix(f) { return { money: this.curSym(), percent: "%", years: "Jahre" }[f.type] || f.unit || ""; },
  recalcG() { clearTimeout(this._rg); this._rg = setTimeout(() => this.runG(), 350); },
  async runG() {
    const d = this.calcDef;
    if (!d || d.ui !== "generic") return;
    store(`gcalc:${this.me.id}:${d.id}`, this.gv);
    const seq = ++this._calcSeq;
    this.busy = true; this.calcErr = "";
    try {
      const r = await api("/api/calc/" + d.id, { method: "POST", body: this.gv });
      if (seq !== this._calcSeq) return;
      this.gres = r;
      this.later(() => this.drawGeneric());
    } catch (e) {
      if (seq !== this._calcSeq) return;
      if (e.status === 401) { this.me = null; return; }
      this.calcErr = e.message; this.gres = null;
    } finally { if (seq === this._calcSeq) this.busy = false; }
  },
  async calcAction(a) {
    if (a.type === "link") { this.go(a.href); return; }
    if (a.type === "save_finance") {
      const me = await this.guard(() => api("/api/me", { method: "PUT", body: { settings: { finance: a.data } } }));
      if (me) { this.me = me; this.fin = null; this.ov = null; this.flash("In deinem Finanzprofil gespeichert ✓"); }
    }
  },

  // ---------- Simulationen (echte Kurse) ----------
  openCustom() {
    const p = this.params, c = this.calc;
    c.q = this.calcId;
    for (const k of ["initial", "monthly", "years", "goal"]) if (p[k] !== undefined && p[k] !== "") c[k] = Number(p[k]);
    if (p.use_holdings) c.use_holdings = ["1", "true"].includes(p.use_holdings);
    if (p.w) {
      const w = {};
      for (const part of p.w.split(",")) { const [a, v] = part.split(":"); if (this.meta.assets[a]) w[a] = v === undefined ? 100 : Number(v); }
      if (Object.keys(w).length) c.weights = w;
    }
    if (c.q === "past" && !c.start) c.start = this.yearsAgo(10);
    this.res = null;
    if (!this.pf) this.guard(() => api("/api/portfolio")).then((pf) => { if (pf) this.pf = pf; });
    this.runCalc();
  },
  recalc() { clearTimeout(this._rc); this._rc = setTimeout(() => this.runCalc(), 450); },
  wKeys() { return Object.keys(this.calc.weights); },
  wSum() { return this.wKeys().reduce((s, a) => s + (Number(this.calc.weights[a]) || 0), 0); },
  wAdd(a) { if (!a || a in this.calc.weights) return; this.calc.weights = Object.assign({}, this.calc.weights, { [a]: 10 }); this.recalc(); },
  wRemove(a) { const w = Object.assign({}, this.calc.weights); delete w[a]; this.calc.weights = Object.keys(w).length ? w : { MSCIW: 100 }; this.recalc(); },
  preset(w) { this.calc.weights = this.cleanWeights(w); this.recalc(); },
  presetWatch() {
    const a = this.me.settings.assets.filter((x) => this.meta.assets[x]).slice(0, 6);
    const w = {}; for (const x of a) w[x] = Math.round(100 / a.length);
    this.preset(w);
  },
  async runCalc() {
    const c = this.calc;
    if (!["future", "goal", "past"].includes(c.q)) return;
    store("calc:" + this.me.id, c);
    const weights = {};
    for (const a of this.wKeys()) weights[a] = Number(c.weights[a]) || 0;
    const body = { initial: c.initial, monthly: c.monthly, years: c.years, weights, use_holdings: c.use_holdings,
      drift: c.drift, costs: c.costs, tax: c.tax, inflation: c.inflation, rebalance: c.rebalance, goal: c.goal };
    if (c.q === "future" && c.wd_monthly > 0) body.withdraw = { monthly: c.wd_monthly, from_year: c.wd_from };
    if (c.q === "past") body.start = c.start + "-01";
    const seq = ++this._calcSeq;
    this.busy = true; this.calcErr = "";
    try {
      const r = await api("/api/calc/" + c.q, { method: "POST", body });
      if (seq !== this._calcSeq) return;
      r.q = c.q;
      this.res = r;
      this.later(() => this.drawCalc());
    } catch (e) {
      if (seq !== this._calcSeq) return;
      if (e.status === 401) { this.me = null; return; }
      this.calcErr = e.message; this.res = null;
    } finally { if (seq === this._calcSeq) this.busy = false; }
  },
  stressText() {
    const s = this.res && this.res.stress;
    if (!s) return "";
    let t = `Angenommen, du hättest am ${this.dateStr(s.peak)} begonnen, kurz vor dem größten Absturz von ${s.lead_name}. Dann wäre dein Geld zwischendurch auf ${this.money(s.low)} geschrumpft (am ${this.dateStr(s.low_date)}). Eingezahlt hattest du bis dahin ${this.money(s.low_invested)}.`;
    t += ` Nach ${this.num(s.years, 1)} Jahren${s.full ? "" : " (weiter reichen die Daten nicht)"} hättest du ${this.money(s.final)}, eingezahlt ${this.money(s.invested)}.`;
    return t;
  },
  assumptionText() {
    if (!this.res || !this.res.assumptions || !this.res.assumptions.weights) return "";
    const a = this.res.assumptions;
    const w = Object.entries(a.weights).filter(([, v]) => v > 0).map(([k, v]) => this.shortName(k) + " " + this.num(v * 100, 0) + " %").join(", ");
    return `So wurde gerechnet: ${w}; Wertzuwachs ${a.drift}; ${a.costs ? "mit Kosten" : "ohne Kosten"}; ${a.tax}; ` +
      (a.inflation_pa ? `Inflation ${this.num(a.inflation_pa * 100, 1)} % pro Jahr; ` : "") +
      `${a.n_sims} durchgespielte Verläufe aus echten Kursen seit ${this.dateStr(a.history_from)}. Wechselkurse (z. B. Dollar zu Euro) bleiben wie heute.`;
  },
  goalToFutureLink() {
    if (!this.res || !this.res.monthly_50) return "#/rechner/future";
    return `#/rechner/future?monthly=${Math.ceil(this.res.monthly_50 / 5) * 5}&years=${this.calc.years}&initial=${this.calc.initial || 0}`;
  },
}));
