/* Diagramme (ECharts, SVG). Farben kommen aus den CSS-Variablen, damit hell/dunkel stimmen. */
"use strict";

part(() => ({
  later(fn) { this.$nextTick(() => requestAnimationFrame(() => requestAnimationFrame(fn))); },
  theme() {
    const cs = getComputedStyle(document.documentElement);
    const g = (n) => cs.getPropertyValue(n).trim();
    return { ink: g("--ink"), ink2: g("--ink-2"), muted: g("--muted"), line: g("--line"), surface: g("--surface"),
      depot: g("--c-DEPOT"), invest: g("--c-INVEST"), band1: g("--band-1"), band2: g("--band-2"), asset: (a) => this.col(a) };
  },
  chart(id) {
    const el = document.getElementById(id);
    if (!el || !el.offsetWidth) return null;
    return echarts.getInstanceByDom(el) || echarts.init(el, null, { renderer: "svg", locale: "DE" });
  },
  eachChart(fn) {
    for (const el of document.querySelectorAll(".chart")) { const c = echarts.getInstanceByDom(el); if (c) fn(c); }
  },
  disposeCharts() { this.eachChart((c) => c.dispose()); },
  resizeCharts() { this.eachChart((c) => c.resize()); },
  redraw() {
    if (this.route === "rechner" && this.res && this.calcDef && this.calcDef.ui === "custom") this.drawCalc();
    if (this.route === "rechner" && this.gres) this.drawGeneric();
    if (this.route === "maerkte" && this.mk) { this.drawMarket(); this.drawYears(); }
    if (this.route === "bestand" && this.hist) this.drawHistory();
  },
  compact(v) {
    const a = Math.abs(v);
    if (a === 0) return "0";
    if (a >= 1e6) return this.num(v / 1e6, a >= 1e7 ? 0 : 1) + " Mio.";
    if (a >= 1e4) return this.num(v / 1e3, 0) + " Tsd.";
    return this.num(v, a < 10 ? 2 : 0);
  },
  tip(t, extra = {}) {
    return Object.assign({ trigger: "axis", backgroundColor: t.surface, borderColor: t.line, textStyle: { color: t.ink, fontSize: 13 },
      axisPointer: { type: "line", lineStyle: { color: t.muted, type: "dashed" } }, confine: true }, extra);
  },
  base(t, extra = {}) {
    const narrow = window.innerWidth < 640;
    return Object.assign({
      animationDuration: 300,
      textStyle: { fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color: t.ink2 },
      grid: { left: 6, right: narrow ? 82 : 110, top: narrow ? 62 : 44, bottom: 8, containLabel: true },
      tooltip: this.tip(t),
      legend: { top: 0, left: 0, textStyle: { color: t.ink2 }, itemWidth: 14, itemHeight: 10, icon: "roundRect" },
    }, extra);
  },
  timeAxis(t) {
    return { type: "time", axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false },
      axisLabel: { color: t.muted, hideOverlap: true }, splitLine: { show: false } };
  },
  valueAxis(t, extra = {}) {
    return Object.assign({ type: "value", scale: false, axisLabel: { color: t.muted, formatter: (v) => this.compact(v) },
      splitLine: { lineStyle: { color: t.line, opacity: 0.7 } } }, extra);
  },
  endLabel(color, text) { return { show: true, formatter: text, color, fontWeight: 600, fontSize: 12, distance: 6 }; },

  // ---------- generische Rechner-Diagramme ----------
  fmtUnit(v, unit) {
    if (v === null || v === undefined) return "–";
    if (unit === "money") return this.money(v);
    if (unit === "pct") return this.num(v, 1) + " %";
    return this.num(v, Math.abs(v) < 10 ? 2 : 0);
  },
  roleColor(t, s, i) {
    if (s.role === "depot") return t.depot;
    if (s.role === "invest") return t.invest;
    if (s.role && this.meta.assets[s.role]) return this.col(s.role);
    const p = this.palette();
    return p[[1, 2, 3, 5, 6, 7, 4][i % 7]];
  },
  drawGeneric() {
    if (!this.gres) return;
    (this.gres.charts || []).forEach((spec, i) => this.drawSpec("ch-g" + i, spec));
  },
  drawSpec(id, spec) {
    const c = this.chart(id); if (!c) return;
    const t = this.theme(), unit = spec.unit || "num", narrow = c.getWidth() < 520;
    const fmt = (v) => this.fmtUnit(v, unit);
    const axisFmt = (v) => (unit === "pct" ? this.num(v, 0) + " %" : this.compact(v));
    const many = spec.series.length > 1;
    const tooltip = this.tip(t, { valueFormatter: fmt, axisPointer: { type: spec.type === "line" ? "line" : "shadow", lineStyle: { color: t.muted, type: "dashed" } } });
    if (spec.type === "hbar") {
      const s = spec.series[0];
      c.setOption({
        animationDuration: 300, textStyle: { fontFamily: "system-ui, sans-serif", color: t.ink2 },
        grid: { left: 10, right: 70, top: 6, bottom: 6, containLabel: true }, tooltip: Object.assign(tooltip, { trigger: "item" }),
        xAxis: { type: "value", show: false },
        yAxis: { type: "category", inverse: true, data: spec.x, axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { color: t.ink2, width: narrow ? 110 : 180, overflow: "break" } },
        series: [{ name: s.name, type: "bar", data: s.data, barMaxWidth: 20, itemStyle: { color: this.roleColor(t, s, 0), borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: "right", color: t.ink, formatter: (p) => fmt(p.value) } }],
      }, true);
      c.getDom().style.height = Math.max(160, spec.x.length * 40 + 20) + "px";
      c.resize();
      return;
    }
    const isBar = spec.type === "bar" || spec.type === "stack";
    const last = spec.x.length - 1;
    const series = spec.series.map((s, i) => {
      const color = this.roleColor(t, s, i);
      const o = { name: s.name, type: isBar ? "bar" : "line", data: s.data, itemStyle: { color } };
      if (isBar) {
        Object.assign(o, { barMaxWidth: 26, stack: spec.type === "stack" ? "s" : undefined });
        o.itemStyle = { color, borderColor: t.surface, borderWidth: spec.type === "stack" ? 1 : 0,
          borderRadius: spec.type === "stack" ? (i === spec.series.length - 1 ? [4, 4, 0, 0] : 0) : [4, 4, 0, 0] };
        if (spec.type === "bar" && spec.x.length <= 12) o.label = { show: true, position: "top", color: t.ink2, fontSize: 11, formatter: (p) => (unit === "pct" ? this.num(p.value, 0) + " %" : this.compact(p.value)) };
      } else {
        Object.assign(o, { symbol: spec.x.length > 30 ? "none" : "circle", symbolSize: 8, showSymbol: spec.x.length <= 12,
          lineStyle: { color, width: 2, type: s.role === "invest" ? "dashed" : "solid" } });
        if (spec.series.length <= 3) o.endLabel = this.endLabel(t.ink2, () => (narrow ? "" : s.name + "\n") + (unit === "pct" ? this.num(s.data[last], 1) + " %" : this.compact(s.data[last])));
      }
      return o;
    });
    const hasEnd = !isBar && spec.series.length <= 3;
    c.setOption(this.base(t, {
      legend: { show: many, top: 0, left: 0, textStyle: { color: t.ink2 }, icon: "roundRect", itemWidth: 14, itemHeight: 10 },
      grid: { left: 6, right: hasEnd ? (narrow ? 60 : 110) : 12, top: many ? (narrow ? 54 : 40) : 16, bottom: 8, containLabel: true },
      tooltip,
      xAxis: { type: "category", data: spec.x, boundaryGap: isBar, axisLabel: { color: t.muted, hideOverlap: true }, axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false } },
      yAxis: this.valueAxis(t, { axisLabel: { color: t.muted, formatter: axisFmt } }),
      series,
    }), true);
  },

  // ---------- Simulationen ----------
  drawCalc() {
    if (!this.res) return;
    if (this.res.q === "future") this.drawFuture();
    if (this.res.q === "past") { this.drawPast(); this.drawCompare(); }
  },
  drawFuture() {
    const c = this.chart("ch-calc"); if (!c) return;
    const t = this.theme(), r = this.res, f = r.fan, d = f.dates;
    if (this.futView === "bars") {
      const years = r.rows.map((x) => String(x.year));
      const inv = r.rows.map((x) => Math.min(x.invested, x.p50));
      const gain = r.rows.map((x) => Math.max(x.p50 - x.invested, 0));
      c.setOption(this.base(t, {
        legend: { top: 0, left: 0, data: ["Eingesetzt", "Zuwachs (mittlerer Fall)"], textStyle: { color: t.ink2 }, icon: "roundRect" },
        grid: { left: 6, right: 12, top: 44, bottom: 8, containLabel: true },
        tooltip: this.tip(t, { axisPointer: { type: "shadow" },
          formatter: (ps) => { const x = r.rows[ps[0].dataIndex]; return `<b>${x.year}</b> (nach ${x.n} J.)<br>Eingesetzt: ${this.money(x.invested)}<br>Mittlerer Fall: <b>${this.money(x.p50)}</b><br>9 von 10 Fällen: ${this.money(x.p5)} – ${this.money(x.p95)}`; } }),
        xAxis: { type: "category", data: years, axisLabel: { color: t.muted }, axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false } },
        yAxis: this.valueAxis(t),
        series: [
          { name: "Eingesetzt", type: "bar", stack: "a", data: inv, itemStyle: { color: t.invest, borderColor: t.surface, borderWidth: 1 }, barMaxWidth: 26 },
          { name: "Zuwachs (mittlerer Fall)", type: "bar", stack: "a", data: gain, itemStyle: { color: t.depot, borderRadius: [4, 4, 0, 0], borderColor: t.surface, borderWidth: 1 }, barMaxWidth: 26 },
        ],
      }), true);
      return;
    }
    const z = (arr) => d.map((x, i) => [x, arr[i]]);
    const diff = (a, b) => d.map((x, i) => [x, a[i] - b[i]]);
    const cap = Math.max(...f.p75) * 2.5, top = Math.max(...f.p95);
    const capped = top > cap;
    this.fanNote = capped ? `Die besten Fälle reichen bis ${this.money(top)}. Die Fläche ist oben abgeschnitten, damit der Rest lesbar bleibt.`
      : "Dunkle Fläche: die mittlere Hälfte aller Verläufe. Helle Fläche: 9 von 10 Verläufen.";
    const last = f.p50.length - 1;
    const hidden = { lineStyle: { opacity: 0 }, symbol: "none", silent: true, tooltip: { show: false } };
    c.setOption(this.base(t, {
      legend: { top: 0, left: 0, textStyle: { color: t.ink2 }, icon: "roundRect", itemWidth: 14, itemHeight: 10,
        data: ["Mittlerer Fall", "Hälfte der Fälle", "9 von 10 Fällen", "Eingesetzt"] },
      tooltip: this.tip(t, {
        formatter: (ps) => {
          const i = ps[0].dataIndex;
          const dt = new Date(d[i]);
          return `<b>${dt.toLocaleDateString("de-DE", { month: "long", year: "numeric" })}</b><br>Mittlerer Fall: <b>${this.money(f.p50[i])}</b><br>` +
            `Hälfte der Fälle: ${this.money(f.p25[i])} – ${this.money(f.p75[i])}<br>9 von 10 Fällen: ${this.money(f.p5[i])} – ${this.money(f.p95[i])}<br>Eingesetzt: ${this.money(f.invested[i])}`;
        } }),
      xAxis: this.timeAxis(t),
      yAxis: this.valueAxis(t, capped ? { max: Math.ceil(cap / Math.pow(10, Math.floor(Math.log10(cap)))) * Math.pow(10, Math.floor(Math.log10(cap))) } : {}),
      series: [
        Object.assign({ name: "_p5", type: "line", stack: "o", data: z(f.p5) }, hidden),
        { name: "9 von 10 Fällen", type: "line", stack: "o", data: diff(f.p95, f.p5), symbol: "none", lineStyle: { opacity: 0 },
          areaStyle: { color: t.band1 }, itemStyle: { color: t.band1 }, tooltip: { show: false } },
        Object.assign({ name: "_p25", type: "line", stack: "i", data: z(f.p25) }, hidden),
        { name: "Hälfte der Fälle", type: "line", stack: "i", data: diff(f.p75, f.p25), symbol: "none", lineStyle: { opacity: 0 },
          areaStyle: { color: t.band2 }, itemStyle: { color: t.band2 }, tooltip: { show: false } },
        { name: "Mittlerer Fall", type: "line", data: z(f.p50), symbol: "none", lineStyle: { color: t.depot, width: 2.5 }, itemStyle: { color: t.depot },
          endLabel: this.endLabel(t.ink, () => "Mittel\n" + this.compact(f.p50[last])) },
        { name: "Eingesetzt", type: "line", data: z(f.invested), symbol: "none", step: "end", lineStyle: { color: t.invest, width: 2, type: "dashed" },
          itemStyle: { color: t.invest }, endLabel: this.endLabel(t.ink2, () => "Eingesetzt\n" + this.compact(f.invested[last])) },
      ],
    }), true);
  },
  drawPast() {
    const c = this.chart("ch-calc"); if (!c) return;
    const t = this.theme(), s = this.res.series, d = s.dates;
    const z = (arr) => d.map((x, i) => [x, arr[i]]);
    const last = s.value.length - 1;
    c.setOption(this.base(t, {
      legend: { top: 0, left: 0, data: ["Wert", "Eingezahlt"], textStyle: { color: t.ink2 }, icon: "roundRect", itemWidth: 14, itemHeight: 10 },
      tooltip: this.tip(t, { formatter: (ps) => { const i = ps[0].dataIndex; return `<b>${this.dateStr(d[i])}</b><br>Wert: <b>${this.money(s.value[i])}</b><br>Eingezahlt: ${this.money(s.invested[i])}<br>${this.signMoney(s.value[i] - s.invested[i])}`; } }),
      xAxis: this.timeAxis(t), yAxis: this.valueAxis(t),
      series: [
        { name: "Wert", type: "line", data: z(s.value), symbol: "none", lineStyle: { color: t.depot, width: 2 }, itemStyle: { color: t.depot },
          areaStyle: { color: t.band1 }, endLabel: this.endLabel(t.ink, () => "Wert\n" + this.compact(s.value[last])) },
        { name: "Eingezahlt", type: "line", data: z(s.invested), symbol: "none", step: "end", lineStyle: { color: t.invest, width: 2, type: "dashed" },
          itemStyle: { color: t.invest }, endLabel: this.endLabel(t.ink2, () => "Eingezahlt\n" + this.compact(s.invested[last])) },
      ],
    }), true);
  },
  drawCompare() {
    const c = this.chart("ch-compare"); if (!c) return;
    const t = this.theme(), r = this.res;
    const cols = this.seriesColors(r.compare.map((x) => x.asset));
    const rows = [{ name: "Deine Aufteilung", v: r.final, color: t.depot }];
    for (const x of r.compare) if (x.final !== null) rows.push({ name: "Nur " + this.shortName(x.asset), v: x.final, color: cols[x.asset] });
    rows.push({ name: "Nur gespart (0 %)", v: r.invested, color: t.invest });
    if (r.real_needed) rows.push({ name: "Kaufkraft erhalten", v: r.real_needed, color: t.invest });
    rows.reverse();
    const narrow = c.getWidth() < 520;
    c.setOption({
      animationDuration: 300,
      textStyle: { fontFamily: "system-ui, sans-serif", color: t.ink2 },
      grid: { left: 10, right: narrow ? 86 : 100, top: 6, bottom: 6, containLabel: true },
      tooltip: { trigger: "item", backgroundColor: t.surface, borderColor: t.line, textStyle: { color: t.ink }, formatter: (p) => `${p.name}: <b>${this.money(p.value)}</b>` },
      xAxis: { type: "value", show: false },
      yAxis: { type: "category", data: rows.map((x) => x.name), axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: t.ink2, width: narrow ? 84 : 170, overflow: "break", lineHeight: 14 } },
      series: [{ type: "bar", data: rows.map((x) => ({ value: x.v, itemStyle: { color: x.color, borderRadius: [0, 4, 4, 0] } })), barMaxWidth: 18,
        label: { show: true, position: "right", color: t.ink, formatter: (p) => this.money(p.value) } }],
    }, true);
    c.getDom().style.height = Math.max(180, rows.length * 34 + 20) + "px";
    c.resize();
  },
  drawHistory() {
    const c = this.chart("ch-history"); if (!c || !this.hist || this.hist.empty) return;
    const t = this.theme(), h = this.hist, d = h.dates;
    const z = (arr) => d.map((x, i) => [x, arr[i]]);
    const last = d.length - 1;
    const invested = { name: "Bezahlt", type: "line", data: z(h.invested), symbol: "none", step: "end",
      lineStyle: { color: t.invest, width: 2, type: "dashed" }, itemStyle: { color: t.invest },
      endLabel: this.endLabel(t.ink2, () => "Bezahlt\n" + this.compact(h.invested[last])) };
    let series;
    if (this.histMode === "assets") {
      const cols = this.seriesColors(Object.keys(h.by_asset));
      series = Object.entries(h.by_asset).map(([a, arr]) => ({ name: this.assetName(a), type: "line", stack: "v", data: z(arr), symbol: "none",
        lineStyle: { width: 2, color: t.surface }, areaStyle: { color: cols[a], opacity: 0.9 }, itemStyle: { color: cols[a] } }));
      series.push(invested);
    } else {
      series = [{ name: "Wert", type: "line", data: z(h.value), symbol: "none", lineStyle: { color: t.depot, width: 2 }, itemStyle: { color: t.depot },
        areaStyle: { color: t.band1 }, endLabel: this.endLabel(t.ink, () => "Wert\n" + this.compact(h.value[last])) }, invested];
    }
    c.setOption(this.base(t, {
      tooltip: this.tip(t, {
        formatter: (ps) => { const i = ps[0].dataIndex; let s = `<b>${this.dateStr(d[i])}</b><br>Wert: <b>${this.money(h.value[i])}</b><br>Bezahlt: ${this.money(h.invested[i])}`;
          if (this.histMode === "assets") for (const [a, arr] of Object.entries(h.by_asset)) s += `<br>${this.assetName(a)}: ${this.money(arr[i])}`; return s; } }),
      xAxis: this.timeAxis(t), yAxis: this.valueAxis(t), series,
    }), true);
  },
  drawMarket() {
    const c = this.chart("ch-market"); if (!c || !this.mk) return;
    const t = this.theme(), m = this.mk, color = t.asset(m.asset);
    const data = m.dates.map((x, i) => [x.replace(" ", "T"), m.values[i]]);
    const series = [{ name: m.name, type: "line", data, symbol: "none", lineStyle: { color, width: 2 }, itemStyle: { color },
      areaStyle: { color, opacity: 0.08 }, endLabel: this.endLabel(t.ink, () => this.compact(m.last)) }];
    if (this.showMa && m.ma200) series.push({ name: "200-Tage-Linie", type: "line", data: m.dates.map((x, i) => [x, m.ma200[i]]), symbol: "none",
      lineStyle: { color: t.muted, width: 1.5, type: "dashed" }, itemStyle: { color: t.muted }, endLabel: this.endLabel(t.muted, () => "200 Tage") });
    const intraday = ["1d", "1w", "1m"].includes(m.range);
    c.setOption(this.base(t, {
      legend: { show: false },
      grid: { left: 6, right: window.innerWidth < 640 ? 60 : 80, top: 14, bottom: 8, containLabel: true },
      tooltip: this.tip(t, {
        formatter: (ps) => { const i = ps[0].dataIndex; const dt = new Date(data[i][0]);
          const when = intraday ? dt.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : dt.toLocaleDateString("de-DE");
          let s = `<b>${when}</b><br>${m.label}: <b>${this.money(m.values[i])}</b>`;
          if (this.showMa && m.ma200 && m.ma200[i]) s += `<br>200-Tage-Linie: ${this.money(m.ma200[i])}`; return s; } }),
      xAxis: this.timeAxis(t), yAxis: this.valueAxis(t, { scale: true }), series,
    }), true);
  },
  drawYears() {
    const c = this.chart("ch-years"); if (!c || !this.mk) return;
    const t = this.theme(), y = this.mk.years, color = t.asset(this.mk.asset);
    const many = y.length > (window.innerWidth < 640 ? 8 : 16);
    c.setOption({
      animationDuration: 300,
      textStyle: { fontFamily: "system-ui, sans-serif", color: t.ink2 },
      grid: { left: 6, right: 12, top: 22, bottom: 6, containLabel: true },
      tooltip: this.tip(t, { axisPointer: { type: "shadow" }, formatter: (ps) => { const x = y[ps[0].dataIndex]; return `<b>${x.year}${x.partial ? " (bis heute)" : ""}</b>: ${this.pct(x.ret)}`; } }),
      xAxis: { type: "category", data: y.map((x) => (many && window.innerWidth < 640 ? "'" + String(x.year).slice(2) : String(x.year))), axisLabel: { color: t.muted, interval: many ? "auto" : 0, hideOverlap: true },
        axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false } },
      yAxis: { type: "value", axisLabel: { color: t.muted, formatter: (v) => this.num(v * 100, 0) + " %" }, splitLine: { lineStyle: { color: t.line, opacity: 0.7 } } },
      series: [{ type: "bar", data: y.map((x) => ({ value: x.ret, itemStyle: { color, opacity: x.partial ? 0.45 : 1, borderRadius: x.ret >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4] } })),
        barMaxWidth: 28, label: { show: !many, position: "top", color: t.ink2, fontSize: 11, formatter: (p) => this.num(p.value * 100, 0) + " %" } }],
    }, true);
  },
}));
