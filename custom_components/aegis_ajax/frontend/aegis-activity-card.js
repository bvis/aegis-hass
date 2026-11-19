/* Aegis activity card.
 * Timeline of the hub's Security event entity, read from Home Assistant's own
 * recorder, read once when the card opens: it never asks Ajax for anything. */

const RED = "var(--error-color, #db4437)";
const AMBER = "var(--warning-color, #ffa600)";
const GREEN = "var(--success-color, #43a047)";
const BLUE = "var(--info-color, #039be5)";
const GREY = "var(--secondary-text-color)";
// Wait between tries of a history read that failed.
const RETRY_MS = 30000;

// event_type -> [icon, colour]
const STYLE = {
  alarm: ["mdi:shield-alert", RED],
  panic: ["mdi:alarm-light", RED],
  fire: ["mdi:fire", RED],
  co_alarm: ["mdi:molecule-co", RED],
  flood: ["mdi:water-alert", RED],
  glass_break: ["mdi:window-shutter-alert", RED],
  tamper: ["mdi:shield-off", AMBER],
  malfunction: ["mdi:alert", AMBER],
  battery_low: ["mdi:battery-alert", AMBER],
  connection_lost: ["mdi:lan-disconnect", AMBER],
  arm: ["mdi:shield-lock", GREEN],
  arm_night: ["mdi:shield-moon", GREEN],
  disarm: ["mdi:shield-off-outline", GREY],
  disarm_night: ["mdi:shield-off-outline", GREY],
  door_open: ["mdi:door-open", BLUE],
  motion: ["mdi:motion-sensor", BLUE],
  doorbell_pressed: ["mdi:doorbell", BLUE],
};

// How an arm/disarm was done, from the source type prefix (same list as the logbook).
const METHODS = [
  ["SPACE_MEMBER", "app"],
  ["USER", "app"],
  ["ACCESS_CARD", "card"],
  ["ACCESS_CODE", "code"],
  ["ACCESS_RRU_CODE", "code"],
  ["KEYPAD", "keypad"],
  ["KEYBOARD", "keypad"],
  ["SPACE_CONTROL", "keyfob"],
  ["SCENARIO", "scenario"],
  ["SPACE_SCENARIO", "scenario"],
  ["SCHEDULE", "schedule"],
];
// [app, card or tag, code, keypad, keyfob, scenario, schedule, restored, no events, load error]
const WORD_KEYS = ["app", "card", "code", "keypad", "keyfob", "scenario", "schedule", "restored", "empty", "error"];
const WORDS = {
  en: ["app", "card or tag", "code", "keypad", "keyfob", "scenario", "schedule", "restored", "No events in this period", "Could not load the history"],
  ca: ["app", "targeta o tag", "codi", "teclat", "comandament", "escenari", "programació", "restaurat", "Cap esdeveniment en aquest període", "No s'ha pogut carregar l'historial"],
  cs: ["aplikace", "karta nebo přívěsek", "kód", "klávesnice", "ovladač", "scénář", "plán", "obnoveno", "V tomto období nejsou žádné události", "Historii se nepodařilo načíst"],
  da: ["app", "kort eller brik", "kode", "tastatur", "fjernbetjening", "scenarie", "tidsplan", "genoprettet", "Ingen hændelser i denne periode", "Historikken kunne ikke indlæses"],
  de: ["App", "Karte oder Tag", "Code", "Bedienteil", "Handsender", "Szenario", "Zeitplan", "wiederhergestellt", "Keine Ereignisse in diesem Zeitraum", "Verlauf konnte nicht geladen werden"],
  es: ["app", "tarjeta o tag", "código", "teclado", "mando", "escenario", "programación", "restaurado", "Sin eventos en este periodo", "No se pudo cargar el historial"],
  fr: ["appli", "carte ou badge", "code", "clavier", "télécommande", "scénario", "planning", "rétabli", "Aucun événement sur cette période", "Impossible de charger l'historique"],
  it: ["app", "tessera o tag", "codice", "tastiera", "telecomando", "scenario", "programmazione", "ripristinato", "Nessun evento in questo periodo", "Impossibile caricare la cronologia"],
  nl: ["app", "kaart of tag", "code", "bediendeel", "afstandsbediening", "scenario", "schema", "hersteld", "Geen gebeurtenissen in deze periode", "Geschiedenis kon niet worden geladen"],
  pl: ["aplikacja", "karta lub brelok", "kod", "klawiatura", "pilot", "scenariusz", "harmonogram", "przywrócono", "Brak zdarzeń w tym okresie", "Nie udało się wczytać historii"],
  pt: ["app", "cartão ou tag", "código", "teclado", "comando", "cenário", "agendamento", "restaurado", "Sem eventos neste período", "Não foi possível carregar o histórico"],
  ro: ["aplicație", "card sau tag", "cod", "tastatură", "telecomandă", "scenariu", "programare", "restabilit", "Niciun eveniment în această perioadă", "Istoricul nu a putut fi încărcat"],
  tr: ["uygulama", "kart veya etiket", "kod", "tuş takımı", "kumanda", "senaryo", "zamanlama", "düzeldi", "Bu dönemde olay yok", "Geçmiş yüklenemedi"],
  uk: ["застосунок", "картка або брелок", "код", "клавіатура", "брелок", "сценарій", "розклад", "відновлено", "Немає подій за цей період", "Не вдалося завантажити історію"],
};

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const isSecurityEvent = (s) =>
  s.entity_id.startsWith("event.") && (s.attributes.event_types || []).includes("arm_night");

class AegisActivityCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("entity is required (the hub's Security event entity)");
    this._config = { days: 7, max: 30, ...config };
    this._rows = [];
    this._ready = false;
    this._loading = false;
    this._error = false;
  }

  set hass(hass) {
    this._hass = hass;
    // New events come from the live state, also while the one history read is
    // in flight: the recorder may not have written them yet.
    const added = this._addLive();
    if (!this._ready) {
      if (!this._loading) this._load();
      return;
    }
    // A failed read is tried again on the next event, or after RETRY_MS on
    // any state update (the frontend sets hass on every one of them).
    const retry = this._error && !this._loading && (added || Date.now() - this._failedAt >= RETRY_MS);
    if (retry) this._load();
    else if (added || this._dayKey.format(new Date()) !== this._day) this._render();
  }

  // The entity's state is the time of its last event; attributes describe it.
  _addLive() {
    const stateObj = this._hass.states[this._config.entity];
    if (!stateObj || !stateObj.attributes.event_type || Number.isNaN(Date.parse(stateObj.state))) return false;
    if (this._rows.some((r) => r.key === stateObj.state)) return false;
    this._rows.unshift({ key: stateObj.state, when: new Date(stateObj.state), ...stateObj.attributes });
    return true;
  }

  async _load() {
    const config = this._config;
    const { entity, days } = config;
    const end = new Date();
    const start = new Date(end - days * 86400000);
    this._loading = true;
    let rows = [];
    let error = false;
    const seen = new Set();
    try {
      const res = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: start.toISOString(),
        end_time: end.toISOString(),
        entity_ids: [entity],
        include_start_time_state: false,
        significant_changes_only: false,
        minimal_response: false,
        no_attributes: false,
      });
      // Keyed on the state (the event's own time), not on when the row was
      // written: a restart writes the last event again under a new time.
      rows = (res[entity] || [])
        .filter((r) => r.a && r.a.event_type && !Number.isNaN(Date.parse(r.s)) && !seen.has(r.s) && seen.add(r.s))
        .map((r) => ({ key: r.s, when: new Date(r.s), ...r.a }))
        .reverse();
    } catch (err) {
      console.error("aegis-activity-card", err);
      error = true;
      this._failedAt = Date.now();
    }
    // The card was pointed at another entity while this was in flight.
    if (config !== this._config) return;
    this._loading = false;
    this._ready = true;
    this._rows = [...this._rows.filter((r) => !seen.has(r.key)), ...rows];
    this._error = error;
    this._render();
  }

  _word(key) {
    const lang = (this._hass.locale?.language || this._hass.language || "en").split("-")[0];
    return (WORDS[lang] || WORDS.en)[WORD_KEYS.indexOf(key)];
  }

  _who(row) {
    const method = METHODS.find(([prefix]) => (row.device_type || "").startsWith(prefix));
    const parts = [];
    if (row.device_name) parts.push(method ? `${row.device_name} (${this._word(method[1])})` : row.device_name);
    if (row.room_name) parts.push(row.room_name);
    if (row.transition && row.transition !== "triggered")
      parts.push(row.transition === "restored" ? this._word("restored") : row.transition);
    return parts.join(" · ");
  }

  _render() {
    const hass = this._hass;
    const cfg = this._config;
    const stateObj = hass.states[cfg.entity];
    const lang = hass.locale?.language || hass.language;
    // Home Assistant lets the user choose between the browser's and the server's time zone.
    const timeZone = hass.locale?.time_zone === "server" ? hass.config?.time_zone : undefined;
    const dayFmt = new Intl.DateTimeFormat(lang, { weekday: "long", day: "numeric", month: "long", timeZone });
    const timeFmt = new Intl.DateTimeFormat(lang, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: hass.locale?.time_format === "12" ? true : hass.locale?.time_format === "24" ? false : undefined,
      timeZone,
    });
    const rel = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    // en-CA prints YYYY-MM-DD, which parses as that calendar day at UTC midnight.
    this._dayKey = new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone });
    this._day = this._dayKey.format(new Date());
    const dayNumber = (d) => Date.parse(this._dayKey.format(d)) / 86400000;
    const dayLabel = (d) => {
      const diff = dayNumber(d) - dayNumber(new Date());
      const label = diff >= -1 ? rel.format(diff, "day") : dayFmt.format(d);
      return label.charAt(0).toUpperCase() + label.slice(1);
    };

    let rows = this._rows || [];
    if (cfg.event_types) rows = rows.filter((r) => cfg.event_types.includes(r.event_type));
    rows = rows.slice(0, cfg.max);

    let body = "";
    if (this._error) body = `<div class="empty">${esc(this._word("error"))}</div>`;
    else if (!rows.length) body = `<div class="empty">${esc(this._word("empty"))}</div>`;
    let lastDay;
    for (const row of rows) {
      const day = dayLabel(row.when);
      if (day !== lastDay) {
        body += `<div class="day">${esc(day)}</div>`;
        lastDay = day;
      }
      const [icon, colour] = STYLE[row.event_type] || ["mdi:shield-outline", GREY];
      const label =
        stateObj && hass.formatEntityAttributeValue
          ? hass.formatEntityAttributeValue(stateObj, "event_type", row.event_type)
          : row.event_type;
      const who = this._who(row);
      body += `
        <div class="row" style="--c:${colour}" title="${esc(row.raw_tag || "")}">
          <div class="badge"><ha-icon icon="${icon}"></ha-icon></div>
          <div class="text">
            <div class="what">${esc(label)}</div>
            ${who ? `<div class="who">${esc(who)}</div>` : ""}
          </div>
          <div class="time">${esc(timeFmt.format(row.when))}</div>
        </div>`;
    }

    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; padding-bottom: 8px; }
        .day { padding: 12px 16px 4px; font-size: 12px; font-weight: 500; letter-spacing: .04em;
               text-transform: uppercase; color: var(--secondary-text-color); }
        .row { display: flex; align-items: center; gap: 12px; padding: 6px 16px; }
        .badge { flex: none; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center;
                 color: var(--c); background: color-mix(in srgb, var(--c) 18%, transparent); --mdc-icon-size: 20px; }
        .text { flex: 1; min-width: 0; }
        .what { font-weight: 500; color: var(--primary-text-color); }
        .who, .time { font-size: 13px; color: var(--secondary-text-color); }
        .who { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .time { flex: none; font-variant-numeric: tabular-nums; }
        .empty { padding: 16px; color: var(--secondary-text-color); }
      </style>
      <ha-card ${cfg.title ? `header="${esc(cfg.title)}"` : ""}>${body}</ha-card>`;
  }

  getCardSize() {
    return Math.min(this._config.max, 8);
  }

  static getStubConfig(hass) {
    const found = Object.values(hass.states).find(isSecurityEvent);
    return { entity: found ? found.entity_id : "" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", required: true, selector: { entity: { filter: { domain: "event", integration: "aegis_ajax" } } } },
        { name: "title", selector: { text: {} } },
        { name: "days", selector: { number: { min: 1, max: 30, mode: "box" } } },
        { name: "max", selector: { number: { min: 1, max: 200, mode: "box" } } },
      ],
    };
  }
}

// The integration loads this file from the page head, often before the frontend
// swaps window.customElements for its scoped-registry polyfill, which does not
// carry over earlier definitions ("Custom element doesn't exist"). So define now
// and again on any registry that replaces it while the page boots.
const define = () => {
  if (!window.customElements.get("aegis-activity-card")) {
    window.customElements.define("aegis-activity-card", AegisActivityCard);
  }
};
define();
const watchRegistry = setInterval(define, 100);
setTimeout(() => clearInterval(watchRegistry), 30000);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "aegis-activity-card",
  name: "Aegis activity",
  description: "Timeline of arm, disarm, alarm and other security events",
  preview: true,
  documentationURL: "https://github.com/bvis/aegis-hass#activity-card",
});
