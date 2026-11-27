// Self-check of the devices card and the security view, run by tests/unit/test_card.py when node is available.
const assert = require("assert");
const path = require("path");

global.HTMLElement = class {
  attachShadow() {
    this.shadowRoot = { innerHTML: "", addEventListener() {}, querySelectorAll: () => [] };
    return this.shadowRoot;
  }
};
const defs = {};
global.window = { customElements: { get: (name) => defs[name], define: (name, cls) => (defs[name] = cls) } };
global.setInterval = () => 0;
global.setTimeout = () => 0;
require(path.join(__dirname, "../../custom_components/aegis_ajax/frontend/aegis-devices-card.js"));

const st = (id, state, attributes = {}) => ({ entity_id: id, state, attributes, last_updated: "t" });
const reg = (id, device_id, extra = {}) => ({ entity_id: id, device_id, platform: "aegis_ajax", ...extra });
const hass = {
  language: "en",
  // The attribute names come from translations the view has to ask for.
  loadBackendTranslation: async (category, integration) => (key) =>
    category === "entity" && integration === "aegis_ajax" && key.endsWith("space.state_attributes.exit_delay_seconds.name") ? "Exit delay (loaded)" : "",
  entities: Object.fromEntries(
    [
      reg("alarm_control_panel.hub", "hub"),
      reg("event.hub_security", "hub", { translation_key: "security_event" }),
      reg("binary_sensor.hub_ethernet", "hub", { entity_category: "diagnostic" }),
      reg("binary_sensor.door", "d1"),
      reg("binary_sensor.door_tamper", "d1"),
      reg("binary_sensor.door_tilt", "d1"),
      reg("sensor.door_battery", "d1", { entity_category: "diagnostic" }),
      reg("sensor.door_temperature", "d1"),
      reg("camera.cam", "c1"),
      reg("button.cam_shot", "c1", { translation_key: "capture_photo" }),
      reg("sensor.pad_battery", "k1", { entity_category: "diagnostic" }),
      reg("binary_sensor.fire_smoke", "f1"),
      reg("binary_sensor.fire_co", "f1"),
      reg("binary_sensor.gone", "g1"),
      { entity_id: "binary_sensor.other", device_id: "x", platform: "zha" },
    ].map((r) => [r.entity_id, r]),
  ),
  states: Object.fromEntries(
    [
      st("alarm_control_panel.hub", "disarmed", { exit_delay_seconds: 30 }),
      st("event.hub_security", "2026-11-27T08:00:00+00:00"),
      st("binary_sensor.hub_ethernet", "on", { device_class: "connectivity", friendly_name: "Hub Ethernet" }),
      st("binary_sensor.door", "on", { device_class: "door" }),
      st("binary_sensor.door_tamper", "on", { device_class: "tamper", friendly_name: "Front door Lid" }),
      st("binary_sensor.door_tilt", "off", { device_class: "tamper", friendly_name: "Front door Tilt" }),
      st("sensor.door_battery", "12", { device_class: "battery" }),
      st("sensor.door_temperature", "21.5", { device_class: "temperature" }),
      st("camera.cam", "idle", { entity_picture: "/api/camera_proxy/camera.cam?token=x" }),
      st("button.cam_shot", "unknown", { friendly_name: "Cam <b> Capture photo" }),
      st("sensor.pad_battery", "unknown", { device_class: "battery" }),
      st("binary_sensor.fire_smoke", "off", { device_class: "smoke" }),
      st("binary_sensor.fire_co", "on", { device_class: "carbon_monoxide" }),
      st("binary_sensor.gone", "unavailable", { device_class: "door" }),
      st("binary_sensor.other", "on", { device_class: "door" }),
    ].map((s) => [s.entity_id, s]),
  ),
  devices: {
    hub: { name: "Hub" },
    d1: { name: "Front door", area_id: "a", via_device_id: "hub" },
    c1: { name: "Cam <b>", via_device_id: "hub" },
    k1: { name: "Pad", model: "Keypad Combi", via_device_id: "hub" },
    f1: { name: "Fire", via_device_id: "hub" },
    g1: { name: "Gone", via_device_id: "hub" },
  },
  areas: { a: { name: "Hall" } },
};

const card = new defs["aegis-devices-card"]();
card.setConfig({});
card._hass = hass;
let model = card._model();
// The panel first, then what needs attention, then the rest by name; other integrations are left out.
assert.deepStrictEqual(model.devices.map((d) => d.name), ["Hub", "Front door", "Gone", "Fire", "Cam <b>", "Pad"]);
// A detector with smoke off and CO on shows the CO, not the quiet smoke sensor.
assert.strictEqual(model.devices[3].iconEntity, "binary_sensor.fire_co");
assert.deepStrictEqual(model.cameras.map((c) => [c.capture, c.captureLabel]), [["button.cam_shot", "Capture photo"]]);
const door = model.devices[1];
assert.strictEqual(door.iconEntity, "binary_sensor.door");
assert.strictEqual(door.area, "Hall");
// A battery that reports nothing shows no chip instead of 0 %.
assert.deepStrictEqual(door.chips.map((c) => c.icon || c.text), ["mdi:shield-off", "mdi:battery-10", "21.5"]);
assert.deepStrictEqual([model.devices[5].icon, model.devices[5].chips.length], ["mdi:dialpad", 0]);

// Names reach the page escaped.
card.hass = hass;
assert.ok(card.shadowRoot.innerHTML.includes("Cam &lt;b&gt;"));
assert.ok(!card.shadowRoot.innerHTML.includes("Cam <b>"));

card.setConfig({ only_issues: true, cameras: false });
card._hass = hass;
model = card._model();
// A disarmed panel with nothing wrong is not something to attend to.
assert.deepStrictEqual(model.devices.map((d) => d.name), ["Front door", "Gone", "Fire"]);
assert.deepStrictEqual(model.cameras, []);

// Pills: one per sensor of the kind, the ones to look at first; a battery that reports nothing is left out.
const pillsOf = (sensors) => {
  card.setConfig({ sensors });
  card._hass = hass;
  return card._model().pills.map((p) => [p.name, p.active, p.text]);
};
assert.deepStrictEqual(pillsOf("door"), [["Front door", true, ""], ["Gone", false, ""]]);
// Lids and batteries list only what is wrong and count the rest.
assert.deepStrictEqual(pillsOf("tamper"), [["Front door · Lid", true, ""], [undefined, undefined, "1 OK"]]);
assert.deepStrictEqual(pillsOf("battery"), [["Front door", true, "12%"]]);
hass.states["sensor.door_battery"].state = "80";
assert.deepStrictEqual(pillsOf("battery"), [[undefined, undefined, "1 OK"]]);
hass.states["sensor.door_battery"].state = "12";
assert.throws(() => card.setConfig({ sensors: "nope" }));
card.hass = hass;
assert.ok(card.shadowRoot.innerHTML.includes('<button type="button" class="pill active"'));

card.setConfig({ hub: "nope" });
assert.deepStrictEqual(card._model().devices, []);

(async () => {
  const view = await defs["ll-strategy-view-aegis-security"].generate({}, hass);
  const cards = view.sections.flatMap((s) => s.cards);
  assert.strictEqual(view.type, "sections");
  assert.ok(cards.some((c) => c.type === "alarm-panel" && c.entity === "alarm_control_panel.hub"));
  assert.strictEqual(cards.filter((c) => c.type === "custom:aegis-devices-card" && !c.sensors).length, 2);
  assert.ok(cards.some((c) => c.type === "custom:aegis-activity-card" && c.entity === "event.hub_security"));
  assert.deepStrictEqual(cards.find((c) => c.type === "history-graph" && c.title === "Door").entities, [
    { entity: "binary_sensor.door", name: "Front door" },
    { entity: "binary_sensor.gone", name: "Gone" },
  ]);
  // Doors, lid sensors and batteries each get a block of pills.
  assert.deepStrictEqual(cards.filter((c) => c.sensors).map((c) => c.sensors), ["door", "tamper", "battery"]);
  // The delays rows come only with the attribute; hub entities lose the device name in front.
  const lists = cards.filter((c) => c.type === "entities");
  assert.strictEqual(lists[0].entities[1].attribute, "exit_delay_seconds");
  assert.strictEqual(lists[0].entities[1].name, "Exit delay (loaded)");
  assert.deepStrictEqual(lists[1].entities, [{ entity: "binary_sensor.hub_ethernet", name: "Ethernet" }]);

  // A house with nothing of ours gets an empty view, not empty sections.
  const empty = await defs["ll-strategy-view-aegis-security"].generate({}, { ...hass, entities: {} });
  assert.deepStrictEqual(empty.sections.flatMap((s) => s.cards.map((c) => c.type)), ["heading", "custom:aegis-devices-card", "heading", "custom:aegis-devices-card"]);

  // A failed translation request still gives the whole view.
  const offline = await defs["ll-strategy-view-aegis-security"].generate(
    {},
    { ...hass, loadBackendTranslation: async () => Promise.reject(new Error("offline")) },
  );
  assert.strictEqual(offline.sections.length, view.sections.length);

  const dashboard = await defs["ll-strategy-dashboard-aegis-security"].generate({ type: "custom:aegis-security", hub: "hub" }, hass);
  assert.deepStrictEqual(dashboard.views[0].strategy, { type: "custom:aegis-security", hub: "hub" });
  assert.deepStrictEqual(window.customStrategies.map((s) => [s.type, s.strategyType]), [["aegis-security", "dashboard"]]);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
