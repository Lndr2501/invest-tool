/* KI-Ratgeber: Seitenleiste mit Streaming (Server-Sent Events über fetch), sicheres Mini-Markdown. */
"use strict";

function freshChat() {
  return { open: false, msgs: [], input: "", busy: false, status: "", ready: false, reason: "", model: "", loaded: false };
}

// Mini-Markdown: erst alles escapen, dann wenige Formate erlauben. Links nur auf eigene Seiten (#/…) oder https.
function renderMd(src) {
  const inline = (s) => escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((#\/[\w\-/?=&.%:,]*|https:\/\/[^\s)"<>]+)\)/g, (m, t, u) =>
      `<a href="${u}"${u.startsWith("https:") ? ' target="_blank" rel="noopener noreferrer"' : ""}>${t}</a>`);
  const out = [];
  let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of String(src || "").split("\n")) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^#{1,4}\s+(.*)$/))) {
      close(); out.push(`<p><b>${inline(m[1])}</b></p>`);
    } else if (!line.trim()) {
      close();
    } else {
      close(); out.push(`<p>${inline(line)}</p>`);
    }
  }
  close();
  return out.join("");
}

part(() => ({
  chat: freshChat(),
  freshChat() { return freshChat(); },
  md(s) { return renderMd(s); },
  aiVisible() {
    if (!this.me) return false;
    const ai = this.me.settings.ai || {};
    return ai.enabled !== false && (this.chat.ready || this.me.is_admin);
  },
  async loadChatState() {
    const r = await this.guard(() => api("/api/ai/history"));
    if (!r) return;
    Object.assign(this.chat, { ready: r.ready, reason: r.reason, model: r.model, loaded: true });
    if (!this.chat.busy) this.chat.msgs = r.messages.map((m) => ({ role: m.role, content: m.content }));
  },
  openChat() {
    this.chat.open = true;
    if (!this.chat.loaded) this.loadChatState();
    this.$nextTick(() => { this.chatScroll(); if (this.$refs.chatInput && window.innerWidth > 760) this.$refs.chatInput.focus(); });
    this.$nextTick(() => {
      const body = this.$refs.chatBody;
      if (body && !body._linkHook) {
        body._linkHook = true;
        body.addEventListener("click", (e) => {
          const a = e.target.closest("a[href^='#/']");
          if (a && window.innerWidth < 760) this.chat.open = false;
        });
      }
    });
  },
  chatScroll() { const b = this.$refs.chatBody; if (b) b.scrollTop = b.scrollHeight; },
  async clearChat() {
    if (this.chat.busy) return;
    await this.guard(() => api("/api/ai/history", { method: "DELETE" }));
    this.chat.msgs = [];
  },
  ask(text) { this.openChat(); this.send(text); },
  chatSuggestions() {
    const f = (this.me && this.me.settings.finance) || {};
    if (!f.income && !(f.goals || []).length) {
      return ["Wie fange ich am besten an?", "Wie viel sollte ich im Monat sparen?", "Welchen Steuersatz habe ich?", "Was ist ein ETF?"];
    }
    return ["Bin ich auf Kurs für meine Ziele?", "Wie lange sollte ich mein Geld anlegen?", "Was bedeutet die Marktlage für mich?", "Wie viel Krypto ist zu viel?"];
  },
  pageContext() {
    const r = this.route, parts = [];
    try {
      if (r === "ueberblick" && this.ov) {
        parts.push("Überblick. Lage: " + this.ov.headline.join(" "));
        if (this.ov.next.length) parts.push("Vorgeschlagener nächster Schritt: " + this.ov.next[0].title + " – " + this.ov.next[0].text);
      } else if (r === "rechner" && this.calcDef) {
        parts.push("Rechner „" + this.calcDef.title + "“ (id " + this.calcDef.id + ").");
        if (this.calcDef.ui === "generic" && this.gres) {
          parts.push("Eingaben: " + JSON.stringify(this.gv));
          parts.push("Ergebnis: " + (this.gres.tiles || []).map((x) => x.label + " " + x.value).join("; ") + ". " + (this.gres.text || []).join(" "));
        } else if (this.res) {
          parts.push("Eingaben: Start " + this.calc.initial + ", monatlich " + this.calc.monthly + ", " + this.calc.years + " Jahre, Aufteilung " + JSON.stringify(this.calc.weights) + ".");
          parts.push("Ergebnis: " + (this.res.text || []).join(" "));
        }
      } else if (r === "maerkte" && this.mk) {
        parts.push("Markt " + this.mk.name + " (" + this.mk.asset + "): " + this.mk.price.text + " (" + this.mk.label + "). " + (this.mk.story || []).join(" "));
      } else if (r === "bestand") {
        parts.push("Seite Mein Bestand" + (this.pf ? (this.pf.empty ? " (leer)." : ": Wert " + this.money(this.pf.total) + ".") : "."));
      } else if (r === "start" && this.wz.res) {
        parts.push("Start-Assistent. Antworten: " + JSON.stringify(this.wz.a) + ". Vorschlag: Mischung " +
          this.wz.res.mix.map((m) => m.pct + " % " + m.name).join(", ") + ", Sparrate " + this.money(this.wz.res.monthly) + ". " +
          this.wz.res.steps.map((s) => s.title).join(" → ") + ". " + ((this.wz.res.forecast && this.wz.res.forecast.text) || ""));
      } else {
        parts.push("Seite " + r + ".");
      }
    } catch (e) { parts.push("Seite " + r + "."); }
    return parts.join(" ").slice(0, 1500);
  },
  async send(text) {
    const msg = (text || this.chat.input || "").trim();
    if (!msg || this.chat.busy || !this.chat.ready) return;
    this.chat.input = "";
    this.chat.msgs.push({ role: "user", content: msg });
    this.chat.msgs.push({ role: "assistant", content: "", events: [] });
    const cur = this.chat.msgs[this.chat.msgs.length - 1];
    this.chat.busy = true; this.chat.status = "Denke nach …";
    this.$nextTick(() => this.chatScroll());
    let changed = false;
    try {
      const r = await fetch("/api/ai/chat", { method: "POST", credentials: "same-origin",
        headers: { "X-Invest": "1", "Content-Type": "application/json" }, body: JSON.stringify({ message: msg, page: this.pageContext() }) });
      if (!r.ok || !r.body) throw new Error("Fehler " + r.status);
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          const line = chunk.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          let ev;
          try { ev = JSON.parse(line.slice(5)); } catch (e) { continue; }
          if (ev.type === "delta") { cur.content += ev.text; this.chat.status = ""; }
          else if (ev.type === "status") this.chat.status = ev.text;
          else if (ev.type === "saved") { cur.events.push("✓ Gemerkt: " + ev.fields.map((f) => FIELD_NAMES[f] || f).join(", ")); changed = true; }
          else if (ev.type === "calc") cur.events.push("🧮 Gerechnet: " + (ev.title || ev.id));
          else if (ev.type === "error") { cur.content += (cur.content ? "\n\n" : "") + "⚠️ " + ev.text; }
          this.chatScroll();
        }
      }
    } catch (e) {
      cur.content += (cur.content ? "\n\n" : "") + "⚠️ Verbindung abgebrochen (" + e.message + ").";
    } finally {
      this.chat.busy = false; this.chat.status = "";
      if (!cur.content.trim()) cur.content = "⚠️ Keine Antwort erhalten.";
      this.$nextTick(() => this.chatScroll());
      if (changed) { await this.refreshMe(); this.ov = null; this.fin = null; if (this.route === "ziele") this.loadFinance(); if (this.route === "ueberblick") this.loadOverview(); }
    }
  },
}));

const FIELD_NAMES = { income: "Einkommen", expenses: "Ausgaben", cash: "Erspartes", debt: "Schulden", saving: "Sparrate",
  risk: "Risiko", experience: "Erfahrung", age: "Alter", married: "Familienstand", church: "Kirchensteuer", zve: "Steuer-Einkommen",
  goals: "Ziele", notes: "Notiz", assets: "Beobachtete Märkte" };
