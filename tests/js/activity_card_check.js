// Self-check of the activity card's logic, run by tests/unit/test_card.py when node is available.
const assert = require("assert");
const path = require("path");

global.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = { innerHTML: "" };
    return this.shadowRoot;
  }
};
global.window = {
  customElements: {
    get() {
      return this.card;
    },
    define(_name, card) {
      this.card = card;
    },
  },
};
global.setInterval = () => 0;
global.setTimeout = () => 0;
require(path.join(__dirname, "../../custom_components/aegis_ajax/frontend/aegis-activity-card.js"));
const Card = window.customElements.card;

(async () => {
  const entity = "event.hub";
  const t1 = "2026-11-18T10:00:00.000+00:00";
  const t2 = "2026-11-19T09:00:00.000+00:00";
  const t3 = new Date().toISOString();
  let reads = 0;
  const hass = (state, eventType) => ({
    states: { [entity]: { state, attributes: { event_type: eventType, device_name: "<Ana>", device_type: "ACCESS_CARD" } } },
    locale: { language: "es", time_zone: "server" },
    config: { time_zone: "Europe/Madrid" },
    callWS: async () => {
      reads++;
      return {
        [entity]: [
          { s: t1, a: { event_type: "arm" }, lc: 1 },
          { s: t2, a: { event_type: "disarm" }, lc: 2 },
          { s: t2, a: { event_type: "disarm" }, lc: 3 }, // written again by a restart
          { s: "unknown", a: {}, lc: 4 },
        ],
      };
    },
  });

  const card = new Card();
  card.setConfig({ entity });
  card.hass = hass(t2, "disarm");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(card._rows.length, 2, "the restart duplicate and the unknown state are dropped");

  card.hass = hass(t3, "alarm");
  assert.equal(card._rows[0].event_type, "alarm", "a new event shows without waiting for the recorder");
  card.hass = hass(t3, "alarm");
  assert.equal(card._rows.length, 3, "the same event is not added twice");
  assert.equal(reads, 1, "history is read once");

  const html = card.shadowRoot.innerHTML;
  assert(html.includes("&lt;Ana&gt; (tarjeta o tag)"), "names are escaped and the method is translated");
  assert(!html.includes("<Ana>"));
  assert(html.includes("Hoy"));
})();
