/* Aegis activity card.
 * Timeline of the hub's Security event entity, read from Home Assistant's own
 * recorder: it never asks Ajax for anything. */

const RED = "var(--error-color, #db4437)";
const AMBER = "var(--warning-color, #ffa600)";
const GREEN = "var(--success-color, #43a047)";
const BLUE = "var(--info-color, #039be5)";
const GREY = "var(--secondary-text-color)";

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
    this._loadedFor = undefined;
    this._rows = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = hass.states[this._config.entity];
    const stamp = stateObj ? stateObj.state : "missing";
    // One history read per new event, none on a timer.
    if (stamp !== this._loadedFor) {
      this._loadedFor = stamp;
      this._load();
    }
  }

  async _load() {
    const { entity, days } = this._config;
    const end = new Date();
    const start = new Date(end - days * 86400000);
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
      this._rows = (res[entity] || [])
        .filter((r) => r.a && r.a.event_type)
        .map((r) => ({ when: new Date((r.lc ?? r.lu) * 1000), ...r.a }))
        .reverse();
      this._error = false;
    } catch (err) {
      console.error("aegis-activity-card", err);
      this._error = true;
    }
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
    const dayFmt = new Intl.DateTimeFormat(lang, { weekday: "long", day: "numeric", month: "long" });
    const timeFmt = new Intl.DateTimeFormat(lang, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: hass.locale?.time_format === "12" ? true : hass.locale?.time_format === "24" ? false : undefined,
    });
    const rel = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
    const midnight = new Date().setHours(0, 0, 0, 0);
    const dayLabel = (d) => {
      const diff = Math.round((new Date(d).setHours(0, 0, 0, 0) - midnight) / 86400000);
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
