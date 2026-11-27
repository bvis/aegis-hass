/* Aegis devices card and the security view built around it.
 * Both find the alarm's entities by themselves (every entity of this
 * integration, grouped by device) and read only what the frontend already
 * has: they never ask Ajax for anything. */

const RED = "var(--error-color, #db4437)";
const AMBER = "var(--warning-color, #ffa600)";
const GREEN = "var(--success-color, #43a047)";
const BLUE = "var(--info-color, #039be5)";
const GREY = "var(--secondary-text-color)";
const PLATFORM = "aegis_ajax";

// Binary sensors that say what a device is doing, in order of preference.
const MAIN_CLASSES = ["door", "window", "garage_door", "opening", "motion", "occupancy", "smoke", "moisture", "carbon_monoxide", "gas", "heat"];
const DANGER_CLASSES = ["smoke", "moisture", "carbon_monoxide", "gas", "heat"];
// Other things a device can be, when it has none of those.
const MAIN_DOMAINS = ["lock", "valve", "siren", "light", "switch"];
const ACTIVE_STATES = ["on", "open", "opening", "unlocked"];
// device_class of a binary sensor that is worth a warning chip when on
const WARN = { tamper: "mdi:shield-off", problem: "mdi:alert", safety: "mdi:alert-octagon", vibration: "mdi:vibrate" };
const PANEL_COLOUR = { disarmed: GREY, triggered: RED, arming: AMBER, pending: AMBER, unavailable: GREY, unknown: GREY };
// Devices with no state of their own, by model name.
const MODEL_ICONS = [
  [/key ?pad|keyboard/i, "mdi:dialpad"],
  [/spacecontrol|keyfob|button/i, "mdi:remote"],
];
// `sensors: door | tamper | battery` turns the card into one small pill per sensor of that kind.
const PILL_KINDS = {
  door: { classes: ["door", "window", "garage_door", "opening"], colour: BLUE },
  // Lids and batteries only matter when something is wrong: the ones that are fine are counted, not listed.
  tamper: { classes: ["tamper"], colour: AMBER, quiet: true },
  battery: { classes: ["battery"], colour: AMBER, quiet: true },
};
const CARD = "aegis-devices-card";
const STRATEGY = "aegis-security";

// The one word Home Assistant's own translations don't give a dashboard.
const DEVICES_WORD = {
  en: "Devices", ca: "Dispositius", cs: "Zařízení", da: "Enheder", de: "Geräte", es: "Dispositivos", fr: "Appareils",
  it: "Dispositivi", nl: "Apparaten", pl: "Urządzenia", pt: "Dispositivos", ro: "Dispozitive", tr: "Cihazlar", uk: "Пристрої",
};

const esc = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const batteryIcon = (pct) => `mdi:battery${pct >= 95 ? "" : pct < 10 ? "-outline" : `-${Math.floor(pct / 10) * 10}`}`;

const deviceName = (device, fallback) => device.name_by_user || device.name || fallback;

// The entity's name without the device in front ("Capture photo"), as Home Assistant translated it.
const entityName = (stateObj, device) => {
  const full = stateObj.attributes.friendly_name || stateObj.entity_id;
  const prefix = deviceName(device, "");
  return (prefix && full.startsWith(prefix) && full.slice(prefix.length).trim()) || full;
};

// Words in the user's language: Home Assistant's own where it has them.
const words = (hass) => {
  const lang = (hass.locale?.language || hass.language || "en").split("-")[0];
  const own = (key, fallback) => (hass.localize && hass.localize(key)) || fallback;
  return {
    alarm: own("component.alarm_control_panel.entity_component._.name", "Alarm"),
    camera: own("component.camera.entity_component._.name", "Camera"),
    devices: DEVICES_WORD[lang] || DEVICES_WORD.en,
    problem: own("component.binary_sensor.entity_component.problem.name", "Problem"),
    temperature: own("component.sensor.entity_component.temperature.name", "Temperature"),
    history: own("panel.history", "History"),
    activity: own("panel.logbook", "Activity"),
    door: own("component.binary_sensor.entity_component.door.name", "Door"),
    window: own("component.binary_sensor.entity_component.window.name", "Window"),
    battery: own("component.sensor.entity_component.battery.name", "Battery"),
    tamper: own("component.binary_sensor.entity_component.tamper.name", "Tamper"),
    motion: own("component.binary_sensor.entity_component.motion.name", "Motion"),
    title: own("ui.panel.lovelace.editor.card.generic.title", "Title"),
  };
};

// Every entity of the integration that has a state, with its registry entry and device.
const aegisEntities = (hass, hub) =>
  Object.values(hass.entities || {})
    .filter((reg) => reg.platform === PLATFORM && !reg.hidden && reg.device_id && hass.states[reg.entity_id])
    .map((reg) => ({ id: reg.entity_id, reg, stateObj: hass.states[reg.entity_id], device: (hass.devices || {})[reg.device_id] || {} }))
    .filter((e) => !hub || e.reg.device_id === hub || e.device.via_device_id === hub);

class AegisDevicesCard extends HTMLElement {
  setConfig(config) {
    if (config.sensors && !PILL_KINDS[config.sensors]) throw new Error(`sensors must be one of: ${Object.keys(PILL_KINDS).join(", ")}`);
    this._config = { cameras: true, devices: true, only_issues: false, ...config };
    this._sig = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    const model = this._model();
    // Re-draw only when something shown changed: hass is set on every state update of the whole house.
    const sig = JSON.stringify(model);
    if (sig === this._sig) return;
    this._sig = sig;
    this._render(model);
  }

  // Everything the card shows, as plain data.
  _model() {
    const hass = this._hass;
    const cfg = this._config;
    if (cfg.sensors) return { cameras: [], devices: [], pills: this._pills(PILL_KINDS[cfg.sensors]) };
    const byDevice = {};
    for (const e of aegisEntities(hass, cfg.hub)) (byDevice[e.reg.device_id] = byDevice[e.reg.device_id] || []).push(e);
    const cameras = [];
    const devices = [];
    for (const [deviceId, entities] of Object.entries(byDevice)) {
      const device = entities[0].device;
      const name = deviceName(device, deviceId);
      const area = ((hass.areas || {})[device.area_id] || {}).name || "";
      const of = (domain) => entities.filter((e) => e.id.startsWith(`${domain}.`));
      const cls = (e) => e.stateObj.attributes.device_class;
      const plain = (e) => !e.reg.entity_category;
      const fmt = (e) => (hass.formatEntityState ? hass.formatEntityState(e.stateObj) : e.stateObj.state);

      const camera = of("camera")[0];
      if (camera) {
        const capture = of("button").find((e) => e.reg.translation_key === "capture_photo");
        const picture = camera.stateObj.attributes.entity_picture;
        cameras.push({
          name,
          entity: camera.id,
          // A new photo keeps the same address until its token rotates: the update time makes the browser fetch it.
          picture: picture ? `${picture}&u=${encodeURIComponent(camera.stateObj.last_updated || "")}` : "",
          capture: capture ? capture.id : "",
          captureLabel: capture ? entityName(capture.stateObj, device) : "",
        });
      }

      const panel = of("alarm_control_panel")[0];
      const sensors = of("binary_sensor").filter(plain);
      // A detector can have several of these (smoke, CO, heat): one that is on wins, a dangerous one first.
      const mains = MAIN_CLASSES.flatMap((c) => sensors.filter((e) => cls(e) === c));
      const on = mains.filter((e) => e.stateObj.state === "on");
      const main =
        panel ||
        on.find((e) => DANGER_CLASSES.includes(cls(e))) ||
        on[0] ||
        mains[0] ||
        MAIN_DOMAINS.map((d) => of(d).find(plain)).find(Boolean);
      const row = { name, area, entity: (main || camera || entities[0]).id, iconEntity: "", icon: "", state: "", text: "", colour: GREY, chips: [], rank: 3, attention: false };
      if (main) {
        const state = main.stateObj.state;
        Object.assign(row, { iconEntity: main.id, state, text: fmt(main) });
        // The panel always comes first, but only an alarm makes it something to attend to.
        if (panel) Object.assign(row, { colour: PANEL_COLOUR[state] || GREEN, rank: 0, attention: state === "triggered" });
        // Not reporting at all is worth a look too, whether or not the device has a connectivity entity.
        else if (state === "unavailable") Object.assign(row, { rank: 1, attention: true });
        else if (ACTIVE_STATES.includes(state))
          Object.assign(row, { colour: DANGER_CLASSES.includes(cls(main)) ? RED : BLUE, rank: 2, attention: true });
      } else {
        const byModel = MODEL_ICONS.find(([pattern]) => pattern.test(device.model || ""));
        row.icon = camera ? "mdi:cctv" : byModel ? byModel[1] : "mdi:shield-outline";
      }

      for (const e of of("binary_sensor")) {
        const offline = e.reg.translation_key === "connectivity" && e.stateObj.state === "off";
        if (!(e.stateObj.state === "on" && WARN[cls(e)]) && !offline) continue;
        row.chips.push({
          icon: offline ? "mdi:lan-disconnect" : WARN[cls(e)],
          colour: offline || cls(e) === "safety" ? RED : AMBER,
          entity: e.id,
          title: e.stateObj.attributes.friendly_name || e.id,
        });
        row.rank = Math.min(row.rank, 1);
        row.attention = true;
      }
      for (const e of of("sensor")) {
        const value = Number(e.stateObj.state);
        if (e.stateObj.state === "" || Number.isNaN(value)) continue;
        if (cls(e) === "temperature") row.chips.push({ text: fmt(e), entity: e.id });
        if (cls(e) === "battery")
          row.chips.push({ icon: batteryIcon(value), text: `${Math.round(value)}%`, colour: value < 20 ? AMBER : "", entity: e.id });
      }
      if (!cfg.only_issues || row.attention) devices.push(row);
    }
    devices.sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name));
    cameras.sort((a, b) => a.name.localeCompare(b.name));
    return { cameras: cfg.cameras ? cameras : [], devices: cfg.devices ? devices : [] };
  }

  // One entry per sensor of a kind, across every device: the ones to look at first.
  _pills(kind) {
    const hass = this._hass;
    const battery = kind === PILL_KINDS.battery;
    const list = aegisEntities(hass, this._config.hub)
      .filter((e) => kind.classes.includes(e.stateObj.attributes.device_class) && (battery || !e.reg.entity_category))
      .filter((e) => !battery || (e.stateObj.state !== "" && !Number.isNaN(Number(e.stateObj.state))));
    const perDevice = {};
    for (const e of list) perDevice[e.reg.device_id] = (perDevice[e.reg.device_id] || 0) + 1;
    const pills = list
      .map((e) => {
        const state = e.stateObj.state;
        const active = battery ? Number(state) < 20 : state === "on";
        return {
          entity: e.id,
          // A device with two of the kind (lid and tilt) says which one each pill is.
          name: perDevice[e.reg.device_id] > 1 ? `${deviceName(e.device, e.id)} · ${entityName(e.stateObj, e.device)}` : deviceName(e.device, e.id),
          text: battery ? `${Math.round(Number(state))}%` : "",
          title: hass.formatEntityState ? hass.formatEntityState(e.stateObj) : state,
          state,
          colour: active ? kind.colour : GREY,
          active,
        };
      })
      .sort((a, b) => b.active - a.active || a.name.localeCompare(b.name));
    if (!kind.quiet) return pills;
    const shown = pills.filter((p) => p.active || p.state === "unavailable");
    const fine = pills.length - shown.length;
    return fine ? [...shown, { summary: true, text: `${fine} OK` }] : shown;
  }

  _render({ cameras, devices, pills = [] }) {
    const cfg = this._config;
    const pillHtml = pills
      .map((p) =>
        p.summary
          ? `<div class="pill fine" style="--c:${GREEN}"><ha-icon icon="mdi:check-circle"></ha-icon><span class="pname">${esc(p.text)}</span></div>`
          : `
        <button type="button" class="pill${p.active ? " active" : ""}" data-entity="${esc(p.entity)}" title="${esc(p.title)}" style="--c:${p.colour}">
          <ha-state-icon data-icon-entity="${esc(p.entity)}"></ha-state-icon>
          <span class="pname">${esc(p.name)}</span>${p.text ? `<span class="pval">${esc(p.text)}</span>` : ""}
        </button>`,
      )
      .join("");
    const chip = (c) =>
      `<span class="chip" data-entity="${esc(c.entity)}" title="${esc(c.title || "")}" style="${c.colour ? `color:${c.colour}` : ""}">
         ${c.icon ? `<ha-icon icon="${c.icon}"></ha-icon>` : ""}${c.text ? esc(c.text) : ""}</span>`;
    const camHtml = cameras
      .map(
        (c) => `
        <div class="cam">
          <div class="pic" data-entity="${esc(c.entity)}">
            ${c.picture ? `<img src="${esc(c.picture)}" alt="${esc(c.name)}" loading="lazy">` : `<div class="noimg"><ha-icon icon="mdi:cctv-off"></ha-icon></div>`}
            <div class="camname">${esc(c.name)}</div>
          </div>
          ${c.capture ? `<button class="shot" data-press="${esc(c.capture)}"><ha-icon icon="mdi:camera"></ha-icon>${esc(c.captureLabel)}</button>` : ""}
        </div>`,
      )
      .join("");
    const devHtml = devices
      .map(
        (d) => `
        <div class="row" data-entity="${esc(d.entity)}" style="--c:${d.colour}">
          <div class="badge">${d.iconEntity ? `<ha-state-icon data-icon-entity="${esc(d.iconEntity)}"></ha-state-icon>` : `<ha-icon icon="${d.icon}"></ha-icon>`}</div>
          <div class="text">
            <div class="what">${esc(d.name)}</div>
            <div class="sub">${esc([d.text, d.area].filter(Boolean).join(" · "))}</div>
          </div>
          <div class="chips">${d.chips.map(chip).join("")}</div>
        </div>`,
      )
      .join("");

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
    }
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { overflow: hidden; padding-bottom: 8px; }
        .cams { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; padding: 12px 12px 4px; }
        .cam { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
        .pic { position: relative; border-radius: 12px; overflow: hidden; cursor: pointer; aspect-ratio: 4 / 3;
               background: var(--secondary-background-color); }
        .pic img { width: 100%; height: 100%; object-fit: cover; display: block; }
        .noimg { height: 100%; display: grid; place-items: center; color: var(--secondary-text-color); }
        .camname { position: absolute; left: 0; right: 0; bottom: 0; padding: 16px 10px 8px; color: #fff; font-size: 13px;
                   font-weight: 500; background: linear-gradient(transparent, rgba(0,0,0,.65)); }
        .shot { display: flex; align-items: center; justify-content: center; gap: 6px; height: 36px; border: 0;
                border-radius: 18px; cursor: pointer; font: inherit; font-size: 13px; font-weight: 500; color: var(--primary-color);
                background: color-mix(in srgb, var(--primary-color) 14%, transparent); --mdc-icon-size: 18px; }
        .shot:active, .shot[disabled] { opacity: .55; }
        .row { display: flex; align-items: center; gap: 12px; padding: 6px 16px; cursor: pointer; }
        .row:hover { background: color-mix(in srgb, var(--primary-text-color) 4%, transparent); }
        .badge { flex: none; width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center;
                 color: var(--c); background: color-mix(in srgb, var(--c) 18%, transparent); --mdc-icon-size: 20px; }
        .text { flex: 1; min-width: 0; }
        .what { font-weight: 500; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .sub { font-size: 13px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .chips { flex: none; display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--secondary-text-color);
                 font-variant-numeric: tabular-nums; }
        .chip { display: inline-flex; align-items: center; gap: 2px; --mdc-icon-size: 16px; }
        .pills { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 6px; padding: 10px 12px 2px; }
        .pill { display: flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px; border: 0; border-radius: 16px;
                min-width: 0; text-align: left; cursor: pointer; font: inherit; font-size: 13px; color: var(--c); background: color-mix(in srgb, var(--c) 12%, transparent);
                --mdc-icon-size: 16px; }
        .pill.fine { cursor: default; }
        .pill.active { background: color-mix(in srgb, var(--c) 24%, transparent); font-weight: 500; }
        .pname { flex: 1; min-width: 0; color: var(--primary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .pval { flex: none; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
        .empty { padding: 16px; display: grid; place-items: center; color: var(--success-color, #43a047); }
      </style>
      <ha-card ${cfg.title ? `header="${esc(cfg.title)}"` : ""}>
        ${camHtml ? `<div class="cams">${camHtml}</div>` : ""}
        ${devHtml}
        ${pillHtml ? `<div class="pills">${pillHtml}</div>` : ""}
        ${camHtml || devHtml || pillHtml ? "" : `<div class="empty"><ha-icon icon="mdi:shield-check"></ha-icon></div>`}
      </ha-card>`;
    // Home Assistant picks the icon of an entity from its state; that element takes objects, not attributes.
    for (const el of this.shadowRoot.querySelectorAll("[data-icon-entity]")) {
      el.hass = this._hass;
      el.stateObj = this._hass.states[el.dataset.iconEntity];
    }
  }

  _click(ev) {
    const press = ev.target.closest("[data-press]");
    if (press) {
      this._hass.callService("button", "press", { entity_id: press.dataset.press });
      // Shows the press was taken, until the next re-draw or a few seconds pass.
      press.disabled = true;
      press.querySelector("ha-icon").setAttribute("icon", "mdi:check");
      setTimeout(() => {
        if (!press.isConnected) return;
        press.disabled = false;
        press.querySelector("ha-icon").setAttribute("icon", "mdi:camera");
      }, 4000);
      return;
    }
    const target = ev.target.closest("[data-entity]");
    if (!target) return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId: target.dataset.entity }, bubbles: true, composed: true }),
    );
  }

  getCardSize() {
    return this._config.sensors ? 2 : 6;
  }

  // The switches of the editor read the config as it is, so the picker writes the defaults out.
  static getStubConfig() {
    return { cameras: true, devices: true };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "hub", selector: { device: { filter: { integration: PLATFORM } } } },
        { name: "cameras", selector: { boolean: {} } },
        { name: "devices", selector: { boolean: {} } },
      ],
      computeLabel: (schema, localize) => {
        const w = words({ localize, language: document.documentElement.lang });
        return { title: w.title, hub: "Hub", cameras: w.camera, devices: w.devices }[schema.name];
      },
    };
  }
}

/* `strategy: { type: custom:aegis-security }` on a view returns a sections view
 * built from the alarm's entities: Home Assistant's cards for what it already
 * does well (arming, graphs, logbook), the Aegis cards for the rest. */
class AegisSecurityViewStrategy {
  static async generate(config, hass) {
    const w = words(hass);
    const all = aegisEntities(hass, config.hub);
    const domain = (d) => all.filter((e) => e.id.startsWith(`${d}.`));
    const cls = (...classes) => all.filter((e) => classes.includes(e.stateObj.attributes.device_class) && !e.reg.entity_category);
    const byDevice = (list) => list.map((e) => ({ entity: e.id, name: deviceName(e.device, e.id) }));
    const heading = (text, icon) => ({ type: "heading", heading: text, icon });
    const section = (cards) => (cards.length > 1 ? [{ type: "grid", cards }] : []);

    const panels = domain("alarm_control_panel");
    const events = domain("event").filter((e) => e.reg.translation_key === "security_event");
    const doors = cls("door", "window", "garage_door", "opening");
    const motion = cls("motion", "occupancy");
    const temps = cls("temperature");
    const tampers = cls("tamper");
    const pills = (sensors) => ({ type: `custom:${CARD}`, hub: config.hub, sensors });
    const batteries = all.filter((e) => e.stateObj.attributes.device_class === "battery");
    const hubIds = new Set(panels.map((p) => p.reg.device_id));
    const hub = all.filter((e) => hubIds.has(e.reg.device_id) && /^(binary_sensor|sensor)\./.test(e.id));
    // The names of the panel's attributes are among the integration's entity translations, which the
    // frontend loads on demand: ask for them, or the names depend on what happened to be loaded.
    // A failed request must not cost the whole view: the names fall back to what is already loaded.
    let localize = hass.localize;
    try {
      if (hass.loadBackendTranslation) localize = await hass.loadBackendTranslation("entity", PLATFORM);
    } catch (err) {
      console.warn("aegis-security: could not load the attribute names", err);
    }
    const attribute = (panel, name, extra) => ({
      type: "attribute",
      entity: panel.id,
      attribute: name,
      name:
        (localize && localize(`component.${PLATFORM}.entity.alarm_control_panel.space.state_attributes.${name}.name`)) ||
        (hass.formatEntityAttributeName ? hass.formatEntityAttributeName(panel.stateObj, name) : name),
      ...extra,
    });
    // Present only with the option that shows exit and entry delays on the panel.
    const delays = panels
      .filter((p) => "exit_delay_seconds" in p.stateObj.attributes)
      .map((p) => ({
        type: "entities",
        entities: [
          attribute(p, "hub_state", { icon: "mdi:shield-home-outline" }),
          attribute(p, "exit_delay_seconds", { suffix: "s", icon: "mdi:exit-run" }),
          // Only while a delay runs: with none the attribute is empty and a relative time of nothing reads as 1970.
          {
            type: "conditional",
            conditions: [{ entity: p.id, state: ["arming", "pending"] }],
            row: attribute(p, "delay_ends_at", { format: "relative", icon: "mdi:timer-outline" }),
          },
        ],
      }));

    return {
      type: "sections",
      max_columns: config.max_columns || 3,
      sections: [
        ...section([
          heading(w.alarm, "mdi:shield-home"),
          ...panels.map((p) => ({ type: "alarm-panel", entity: p.id, states: ["arm_away", "arm_night"] })),
          ...delays,
          ...(hub.length ? [{ type: "entities", entities: hub.map((e) => ({ entity: e.id, name: entityName(e.stateObj, e.device) })) }] : []),
        ]),
        ...section([heading(`${w.camera} · ${w.devices}`, "mdi:cctv"), { type: `custom:${CARD}`, hub: config.hub }]),
        // Every door, lid sensor and battery at a glance, one small pill each.
        ...section([
          ...(doors.length ? [heading(`${w.door} · ${w.window}`, "mdi:door"), pills("door")] : []),
          ...(tampers.length ? [heading(w.tamper, "mdi:shield-off"), pills("tamper")] : []),
          ...(batteries.length ? [heading(w.battery, "mdi:battery"), pills("battery")] : []),
        ]),
        ...section([
          heading(w.problem, "mdi:alert"),
          { type: `custom:${CARD}`, hub: config.hub, cameras: false, only_issues: true },
          ...(temps.length ? [heading(w.temperature, "mdi:thermometer")] : []),
          ...byDevice(temps).map((t) => ({ type: "sensor", ...t, graph: "line", hours_to_show: 24, grid_options: { columns: 6 } })),
        ]),
        ...section([
          heading(w.history, "mdi:chart-timeline"),
          ...(panels.length ? [{ type: "history-graph", title: w.alarm, hours_to_show: 48, entities: byDevice(panels) }] : []),
          ...(doors.length ? [{ type: "history-graph", title: w.door, hours_to_show: 24, entities: byDevice(doors) }] : []),
          ...(motion.length ? [{ type: "history-graph", title: w.motion, hours_to_show: 24, entities: byDevice(motion) }] : []),
        ]),
        ...section([
          heading(w.activity, "mdi:history"),
          ...events.map((e) => ({ type: "custom:aegis-activity-card", entity: e.id, days: 7, max: 15 })),
          ...(doors.length + motion.length ? [{ type: "logbook", hours_to_show: 24, entities: [...doors, ...motion].map((e) => e.id) }] : []),
        ]),
      ],
    };
  }
}

// A whole dashboard with that one view, for the "new dashboard" dialog.
class AegisSecurityDashboardStrategy {
  static async generate(config, hass) {
    return { views: [{ title: words(hass).alarm, path: "security", icon: "mdi:shield-home", strategy: { ...config, type: `custom:${STRATEGY}` } }] };
  }
}

// The integration loads this file from the page head, often before the frontend
// swaps window.customElements for its scoped-registry polyfill, which does not
// carry over earlier definitions ("Custom element doesn't exist"). So define now
// and again on any registry that replaces it while the page boots.
const ELEMENTS = {
  [CARD]: AegisDevicesCard,
  [`ll-strategy-view-${STRATEGY}`]: AegisSecurityViewStrategy,
  [`ll-strategy-dashboard-${STRATEGY}`]: AegisSecurityDashboardStrategy,
};
const define = () => {
  for (const [tag, cls] of Object.entries(ELEMENTS)) {
    if (!window.customElements.get(tag)) window.customElements.define(tag, cls);
  }
};
define();
const watchRegistry = setInterval(define, 100);
setTimeout(() => clearInterval(watchRegistry), 30000);
window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD,
  name: "Aegis devices",
  description: "Cameras with their capture button and one row per device of the alarm, found by themselves",
  preview: true,
  documentationURL: "https://github.com/bvis/aegis-hass#devices-card-and-security-view",
});
window.customStrategies = window.customStrategies || [];
window.customStrategies.push({
  type: STRATEGY,
  strategyType: "dashboard",
  name: "Aegis security",
  description: "The alarm panel, cameras, devices, history and activity, built from your Ajax devices",
  documentationURL: "https://github.com/bvis/aegis-hass#devices-card-and-security-view",
});
