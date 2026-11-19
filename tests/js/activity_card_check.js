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
  const during = "2026-11-19T09:30:00.000+00:00";
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
  card.hass = hass(during, "arm");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(
    card._rows.map((r) => r.key),
    [during, t2, t1],
    "restart duplicate and unknown state dropped, an event during the load kept",
  );

  card.hass = hass(t3, "alarm");
  assert.equal(card._rows[0].event_type, "alarm", "a new event shows without waiting for the recorder");
  card.hass = hass(t3, "alarm");
  assert.equal(card._rows.length, 4, "the same event is not added twice");
  assert.equal(reads, 1, "history is read once");

  const html = card.shadowRoot.innerHTML;
  assert(html.includes("&lt;Ana&gt; (tarjeta o tag)"), "names are escaped and the method is translated");
  assert(!html.includes("<Ana>"));
  assert(html.includes("Hoy"));

  // A failed history read: tried again after the wait, not on every state update.
  let fail = true;
  let tries = 0;
  const flaky = () => ({
    ...hass(t2, "disarm"),
    callWS: async () => {
      tries++;
      if (fail) throw new Error("connection lost");
      return { [entity]: [{ s: t1, a: { event_type: "arm" }, lc: 1 }] };
    },
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const broken = new Card();
  broken.setConfig({ entity });
  broken.hass = flaky();
  await settle();
  assert(broken.shadowRoot.innerHTML.includes("No se pudo cargar el historial"));
  broken.hass = flaky();
  await settle();
  assert.equal(tries, 1, "no retry on the next state update");
  fail = false;
  broken._failedAt -= 30000;
  broken.hass = flaky();
  await settle();
  assert.equal(tries, 2, "retried once the wait is over");
  assert(!broken.shadowRoot.innerHTML.includes("No se pudo cargar el historial"));
  assert.deepEqual(broken._rows.map((r) => r.key), [t2, t1]);
})();
