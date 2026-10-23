// Hue-Lite's Lights dashboard, loaded on every page while Hue-Lite is set up.
//
// A dashboard strategy (`strategy: {type: custom:hue-lite}`) that builds the
// Lights dashboard from the area and label registries every time it loads, so
// new lights, rooms and labels appear without editing anything. The
// integration tells it which labels drive the routines and its own entity ids
// (websocket `hue_lite/config`). Also defines four cards:
//   custom:hue-lite-light-bar   a light, or "All lights": tap to toggle, slide
//                               to dim, hold a single light for its colour dialog
//   custom:hue-lite-scene-tile  a scene button (Natural or a preset) that lights
//                               up while its lights are in that scene
//   custom:hue-lite-rooms       every room as a collapsible, reorderable block
//   custom:hue-lite-routines    Wake up, Evening and Coming & going as collapsible blocks
//
// Layout: Whole home (fixed), then the rooms card with one block per area with
// lights (ordered by floor unless you've reordered them) and one per label you
// create yourself, then the routines card. The role labels drive the routines
// and don't get blocks of their own.

(() => {
  const DOMAIN = "hue_lite";
  const HOLD_MS = 500;
  // How each scene looks; `rgb` is its highlight colour. Preset values come
  // from the integration.
  const NATURAL = { name: "Natural", icon: "mdi:theme-light-dark", rgb: "255, 193, 7" };
  const PRESETS = [
    { id: "energize", name: "Energize", icon: "mdi:lightning-bolt", rgb: "90, 150, 255" },
    { id: "cool_bright", name: "Cool bright", icon: "mdi:white-balance-sunny", rgb: "130, 190, 255" },
    { id: "relax", name: "Relax", icon: "mdi:sofa", rgb: "255, 170, 60" },
    { id: "rest", name: "Rest", icon: "mdi:book-open-page-variant", rgb: "255, 130, 50" },
    { id: "nightlight", name: "Nightlight", icon: "mdi:weather-night", rgb: "229, 57, 53" },
  ];

  // ---- Registry helpers ----------------------------------------------------

  // Lights that take part: not hidden, not labelled Exclude from lighting and
  // not light groups (which would double up with their members).
  function allLights(hass, exclude) {
    return Object.values(hass.entities || {}).filter((e) =>
      e.entity_id.startsWith("light.") && !e.hidden && !e.entity_category && hass.states[e.entity_id] &&
      !(e.labels || []).includes(exclude) &&
      !Array.isArray(hass.states[e.entity_id].attributes.entity_id));
  }

  function areaOf(hass, entity) {
    return entity.area_id || hass.devices?.[entity.device_id]?.area_id || null;
  }

  function byName(hass) {
    const name = (id) => hass.states[id]?.attributes.friendly_name || id;
    return (a, b) => name(a).localeCompare(name(b));
  }

  function sortedAreas(hass) {
    const level = (area) => hass.floors?.[area.floor_id]?.level ?? Number.MAX_SAFE_INTEGER;
    return Object.values(hass.areas || {}).sort((a, b) => level(a) - level(b) || a.name.localeCompare(b.name));
  }

  // ---- Card builders -------------------------------------------------------

  function lightBar(entities, extra = {}) {
    return { type: "custom:hue-lite-light-bar", entities, ...extra };
  }

  // The scenes, two per row: Natural light first, then the presets. `target`
  // is the action target: {area_id}, {label_id}, {entity_id} or {} for every light.
  function sceneTiles(setup, lights, target) {
    const tile = (extra) => ({
      type: "custom:hue-lite-scene-tile", entities: lights, target, scenes_entity: setup.entities.light_scenes,
      grid_options: { columns: 6, rows: 1 }, ...extra,
    });
    return [tile({ natural: true }), ...PRESETS.map((p) => tile({ preset: p.id }))];
  }

  // A room (or label group) for the rooms card, with an "All lights" bar when
  // it has several lights.
  function room(setup, id, name, icon, lights, target) {
    return {
      id,
      name,
      icon,
      lights,
      cards: [
        ...(lights.length > 1 ? [lightBar(lights, { name: "All lights" })] : []),
        ...lights.map((l) => lightBar([l])),
        ...sceneTiles(setup, lights, target),
      ],
    };
  }

  function whenOn(toggle, row) {
    return { type: "conditional", conditions: [{ entity: toggle, state: "on" }], row };
  }

  // ---- Views ---------------------------------------------------------------

  function mainView(hass, setup, path) {
    const e = setup.entities;
    const roleLabels = Object.values(setup.roles);
    const lights = allLights(hass, setup.roles.exclude);
    const ids = (entries) => entries.map((x) => x.entity_id).sort(byName(hass));
    const everything = ids(lights);
    const sections = [{
      type: "grid",
      cards: [
        { type: "heading", heading: "Whole home", icon: "mdi:home" },
        lightBar(everything, { name: "All lights" }),
        ...sceneTiles(setup, everything, {}),
      ],
    }];

    // Rooms (and your own labels) go in one collapsible, reorderable card;
    // Whole home above stays put.
    const rooms = [];
    for (const area of sortedAreas(hass)) {
      const inArea = ids(lights.filter((x) => areaOf(hass, x) === area.area_id));
      if (inArea.length) rooms.push(room(setup, area.area_id, area.name, area.icon || "mdi:texture-box", inArea, { area_id: area.area_id }));
    }

    // Not "other": an area called Other would have that id too.
    const noArea = ids(lights.filter((x) => !areaOf(hass, x)));
    if (noArea.length) rooms.push(room(setup, "no-area", "Other", "mdi:lightbulb-question-outline", noArea, { entity_id: noArea }));

    const labels = setup.labels
      .filter((l) => !roleLabels.includes(l.label_id))
      .sort((a, b) => a.name.localeCompare(b.name));
    for (const label of labels) {
      const tagged = ids(lights.filter((x) => (x.labels || []).includes(label.label_id)));
      if (tagged.length) rooms.push(room(setup, `label:${label.label_id}`, label.name, label.icon || "mdi:label-outline", tagged, { label_id: label.label_id }));
    }
    if (rooms.length) {
      sections.push({ type: "grid", column_span: 2, cards: [{ type: "custom:hue-lite-rooms", rooms }] });
    }

    // The routines go in one card of collapsible blocks, like the rooms.
    const routines = [{
      id: "wake_up",
      name: "Wake up",
      icon: "mdi:weather-sunset-up",
      cards: [{
        type: "entities",
        entities: [
          { entity: e.wake_up_weekdays, name: "Mon–Fri" },
          whenOn(e.wake_up_weekdays, { entity: e.wake_up_weekday, name: "Wake at" }),
          { entity: e.wake_up_weekends, name: "Sat & Sun" },
          whenOn(e.wake_up_weekends, { entity: e.wake_up_weekend, name: "Wake at" }),
        ],
      }],
    }, {
      id: "evening",
      name: "Evening",
      icon: "mdi:weather-sunset",
      cards: [
        { type: "tile", entity: e.evening_routine, name: "Tonight", state_content: ["state", "summary"] },
        {
          type: "entities",
          entities: [
            { entity: e.evening_routine_enabled, name: "Evening routine" },
            { entity: e.bedtime_weeknight, name: "Bedtime Sun–Thu" },
            { entity: e.bedtime_weekend, name: "Bedtime Fri & Sat" },
          ],
        },
        {
          type: "heading",
          heading: "Timings & fades",
          heading_style: "subtitle",
          icon: "mdi:tune-variant",
          tap_action: { action: "navigate", navigation_path: `/${path}/settings` },
        },
      ],
    }, {
      id: "coming_going",
      name: "Coming & going",
      icon: "mdi:home-import-outline",
      cards: [{
        type: "entities",
        title: "Coming home",
        entities: [
          { entity: e.coming_home_enabled, name: "Lights on when I arrive" },
          whenOn(e.coming_home_enabled, { entity: e.coming_home_only_dark, name: "Only if I arrive after dark" }),
        ],
      }, {
        type: "entities",
        title: "Leaving",
        entities: [{ entity: e.leaving_home_enabled, name: "Lights off when everyone leaves" }],
      }],
    }];
    sections.push({ type: "grid", column_span: 3, cards: [{ type: "custom:hue-lite-routines", routines, entities: e }] });

    return { title: "Lights", path: "lights", icon: "mdi:lightbulb-group", type: "sections", max_columns: 3, sections };
  }

  function howItWorks(setup) {
    const label = (role) => setup.labels.find((l) => l.label_id === setup.roles[role])?.name || role;
    return [
      `**Wake up**: lights labelled *${label("wake_up")}* fade from a dim warm glow to Energize, reaching full brightness *at* your wake-up time.`,
      "",
      `**Natural light** is a scene that follows the sun: Energize from dawn, Energize to Cool bright through the morning, Cool bright from midday, Cool bright to Relax over the last 90 minutes before sunset, Relax to Rest after sunset, and Rest overnight (lights labelled *${label("nightlight")}*: Nightlight). Lights stay in it until you pick another scene or change them by hand.`,
      "",
      `**Evening routine**: before sunset, lights labelled *${label("evening")}* come on dim (if someone's home), brighten to Cool bright, wind down through Relax to Rest before bed (*${label("nightlight")}* lights go to Nightlight for the last stretch) and fade out at bedtime. Picking a scene for a light, Natural light included, takes it out of the routine for the night.`,
      "",
      `**Coming home**: when someone arrives home, lights labelled *${label("coming_home")}* that are off come on in Natural light, optionally only if it's after dark. When the last person leaves, every light can go off.`,
      "",
      `**Adding a light**: give it an area, and the labels for the routines it should join (Settings → Devices & services → Entities). It appears here on the next reload. To keep a light out of all of this (e.g. one run by its own motion sensor), give it the *${label("exclude")}* label.`,
    ].join("\n");
  }

  function settingsSection(heading, icon, entities) {
    return { type: "grid", cards: [{ type: "heading", heading, icon }, { type: "entities", entities }] };
  }

  function settingsView(setup, path) {
    const e = setup.entities;
    return {
      title: "Timings & fades",
      path: "settings",
      icon: "mdi:tune-variant",
      type: "sections",
      max_columns: 3,
      subview: true,
      back_path: `/${path}/lights`,
      sections: [
        settingsSection("Wake up", "mdi:weather-sunset-up", [
          { entity: e.wake_up_fade_minutes, name: "Fade in" },
          { entity: e.wake_up_auto_off, name: "Turn wake-up lights off" },
          whenOn(e.wake_up_auto_off, { entity: e.wake_up_off_after_minutes, name: "After wake-up time" }),
        ]),
        settingsSection("Evening routine", "mdi:weather-sunset", [
          { entity: e.evening_start_before_sunset, name: "Start before sunset" },
          { entity: e.evening_brighten_minutes, name: "Brighten over" },
          { entity: e.evening_wind_down_minutes, name: "Wind down before bed" },
          { entity: e.evening_nightlight_minutes, name: "Nightlight before bed" },
          { entity: e.bedtime_fade_minutes, name: "Fade out at bedtime" },
        ]),
        {
          type: "grid",
          cards: [
            { type: "heading", heading: "How it works", icon: "mdi:information-outline" },
            { type: "markdown", content: howItWorks(setup) },
          ],
        },
      ],
    };
  }

  class HueLiteStrategy extends HTMLElement {
    static async generate(config, hass) {
      let setup;
      try {
        setup = await hass.callWS({ type: `${DOMAIN}/config` });
      } catch (err) {
        return {
          views: [{
            title: "Lights",
            cards: [{ type: "markdown", content: "Hue-Lite isn't set up. Add it in **Settings → Devices & services**, then reload this page." }],
          }],
        };
      }
      // The dashboard's own path, for the links between its views.
      const path = window.location.pathname.split("/")[1] || "lovelace";
      return { views: [mainView(hass, setup, path), settingsView(setup, path)] };
    }
  }

  // ---- Shared card bits ----------------------------------------------------

  // The round icon used by the light bars and the scene tiles.
  const CIRCLE_CSS = `
    .circle { --mdc-icon-size: 24px; width: 36px; height: 36px; border-radius: 50%; flex: none;
              display: flex; align-items: center; justify-content: center; transition: background-color 180ms, color 180ms;
              background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.05); color: var(--state-inactive-color, var(--secondary-text-color)); }`;

  // Click, Enter or Space.
  function onActivate(el, fn) {
    el.addEventListener("click", (ev) => { ev.stopPropagation(); fn(); });
    el.addEventListener("keydown", (ev) => {
      if (ev.target === el && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); ev.stopPropagation(); fn(); }
    });
  }

  // Calls `onHold` after HOLD_MS without moving; returns whether the last
  // press was a hold, so the click that follows it can be ignored.
  function onHold(el, onHold) {
    let timer = null;
    let start = null;
    let held = false;
    const cancel = () => { clearTimeout(timer); timer = null; };
    el.addEventListener("pointerdown", (ev) => {
      held = false;
      start = { x: ev.clientX, y: ev.clientY };
      cancel();
      timer = setTimeout(() => { held = true; timer = null; onHold(); }, HOLD_MS);
    });
    el.addEventListener("pointermove", (ev) => {
      if (start && Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 10) cancel();
    });
    for (const type of ["pointerup", "pointerleave", "pointercancel"]) el.addEventListener(type, cancel);
    el.addEventListener("contextmenu", (ev) => ev.preventDefault());
    return () => { const was = held; held = false; return was; };
  }

  // Whether any of these entities' states changed since the card last looked.
  // Home Assistant hands out a new hass on every state change in the house,
  // but replaces a state object only when that entity changes, so comparing
  // the objects is enough to skip redrawing for everything else.
  function statesChanged(card, hass, ids) {
    const states = ids.map((id) => hass.states[id]);
    const changed = !card._seen || states.some((state, i) => state !== card._seen[i]);
    card._seen = states;
    return changed;
  }

  // Base for both cards: config, hass and one-off rendering into a shadow root.
  class HueLiteCard extends HTMLElement {
    setConfig(config) {
      if (!Array.isArray(config.entities) || !config.entities.length) throw new Error("entities must be a non-empty list of lights");
      this._config = config;
      this._seen = null;
      this._root = this._root || this.attachShadow({ mode: "open" });
      this._render();
      this._update();
    }

    set hass(hass) {
      this._hass = hass;
      if (statesChanged(this, hass, this._watching())) this._update();
    }

    // The entities this card shows.
    _watching() {
      return this._config.entities;
    }

    _states() {
      return this._config.entities.map((id) => this._hass?.states[id]).filter(Boolean);
    }
  }

  // ---- Light bar -----------------------------------------------------------

  // One bar for a single light or a set ("All lights"): tap to toggle, slide to
  // dim, hold a single light to open Home Assistant's light dialog (colour
  // wheel, colour temperature).
  class HueLiteLightBar extends HueLiteCard {
    getCardSize() {
      return 2;
    }

    // A little taller than the scene buttons (56px), so lights stand apart.
    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    get _single() {
      return this._config.entities.length === 1;
    }

    _call(service, data, entities = this._config.entities) {
      this._hass.callService("light", service, data, { entity_id: entities });
    }

    _render() {
      this._root.innerHTML = `
        <style>
          ${CIRCLE_CSS}
          :host { --icon-rgb: 255, 193, 7; }
          ha-card { height: 72px; box-sizing: border-box; padding: 0 12px; display: flex; align-items: center; gap: 12px;
                    cursor: pointer; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
                    -webkit-tap-highlight-color: transparent; }
          ha-card.unavailable { opacity: 0.5; }
          .light { width: 40px; height: 40px; }
          ha-card.on .light { background: rgba(var(--icon-rgb), 0.25); color: rgb(var(--icon-rgb)); }
          .text { flex: 0 1 38%; min-width: 0; }
          .name, .state { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .name { font-size: 14px; font-weight: 500; line-height: 20px; color: var(--primary-text-color); }
          .state { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
          input { -webkit-appearance: none; appearance: none; flex: 1; min-width: 60px; height: 36px; margin: 0;
                  background: transparent; cursor: pointer; --pct: 0%; --fill: 255, 193, 7; }
          ha-card:not(.on) input { --fill: 150, 150, 150; }
          input::-webkit-slider-runnable-track { height: 12px; border-radius: 6px;
            background: linear-gradient(to right, rgb(var(--fill)) var(--pct), rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12) var(--pct)); }
          input::-moz-range-track { height: 12px; border-radius: 6px; background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12); }
          input::-moz-range-progress { height: 12px; border-radius: 6px; background: rgb(var(--fill)); }
          input::-webkit-slider-thumb { -webkit-appearance: none; width: 20px; height: 20px; margin-top: -4px; border-radius: 50%;
            background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35); }
          input::-moz-range-thumb { width: 20px; height: 20px; border: none; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35); }
        </style>
        <ha-card role="button" tabindex="0">
          <div class="circle light"><ha-icon></ha-icon></div>
          <div class="text"><div class="name"></div><div class="state"></div></div>
          <input type="range" min="1" max="100" step="1">
        </ha-card>`;

      const $ = (sel) => this._root.querySelector(sel);
      this._el = { card: $("ha-card"), icon: $(".light ha-icon"), name: $(".name"), state: $(".state"), slider: $("input") };
      const { card, slider } = this._el;

      // Tap toggles; holding a single light opens its dialog instead.
      const wasHold = onHold(card, () => {
        if (!this._single) return;
        this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: this._config.entities[0] }, bubbles: true, composed: true }));
      });
      onActivate(card, () => {
        if (wasHold()) return;
        this._call(this._states().some((s) => s.state === "on") ? "turn_off" : "turn_on", {});
      });

      // The slider handles its own input.
      slider.addEventListener("pointerdown", (ev) => ev.stopPropagation());
      slider.addEventListener("click", (ev) => ev.stopPropagation());
      slider.addEventListener("input", () => slider.style.setProperty("--pct", `${slider.value}%`));
      slider.addEventListener("change", () => {
        // A set dims the lights that are on; with none on (or a single light) it turns them on.
        const on = this._states().filter((s) => s.state === "on").map((s) => s.entity_id);
        this._call("turn_on", { brightness_pct: Number(slider.value) }, on.length && !this._single ? on : this._config.entities);
      });
    }

    _update() {
      if (!this._el || !this._hass) return;
      const { card, icon, name, state, slider } = this._el;
      const states = this._states();
      const on = states.filter((s) => s.state === "on");
      const brightness = on.map((s) => s.attributes.brightness).filter((b) => typeof b === "number");
      const pct = brightness.length ? Math.max(1, Math.round((brightness.reduce((a, b) => a + b, 0) / brightness.length / 255) * 100)) : 0;
      const single = this._single ? states[0] : undefined;
      const unavailable = this._single && (!single || single.state === "unavailable");

      card.classList.toggle("on", on.length > 0);
      card.classList.toggle("unavailable", Boolean(unavailable));
      card.title = this._single ? "Tap to switch on or off, hold for colour" : "Tap to switch all on or off";
      // A single light's icon shows its own colour when it's on.
      const rgb = single?.attributes.rgb_color;
      card.style.setProperty("--icon-rgb", on.length && Array.isArray(rgb) ? rgb.join(", ") : "255, 193, 7");
      icon.setAttribute("icon", !this._single ? "mdi:lightbulb-group"
        : single?.attributes.icon || (on.length ? "mdi:lightbulb" : "mdi:lightbulb-outline"));
      name.textContent = this._config.name || single?.attributes.friendly_name || "Lights";
      state.textContent = unavailable ? "Unavailable"
        : this._single ? (on.length ? `${pct}%` : "Off")
        : on.length ? `${on.length} of ${states.length} on · ${pct}%` : `All ${states.length} off`;

      slider.disabled = Boolean(unavailable);
      slider.setAttribute("aria-label", `Brightness of ${name.textContent}`);
      if (this._root.activeElement !== slider) {
        slider.value = on.length ? pct : 1;
        slider.style.setProperty("--pct", on.length ? `${pct}%` : "0%");
      }
    }
  }

  // ---- Scene tile ----------------------------------------------------------

  // A scene button styled like a tile: tap to put the lights in that scene; lit
  // while every light that's on is in it.
  //   {natural: true, entities, target, scenes_entity}   the Natural light scene
  //   {preset: id, entities, target, scenes_entity}      a preset
  // `scenes_entity` says which scene each light is in (attribute `scenes`);
  // `target` is passed to the actions: {area_id}, {label_id}, {entity_id} or {} for all.
  class HueLiteSceneTile extends HueLiteCard {
    setConfig(config) {
      this._scene = config.natural ? NATURAL : PRESETS.find((p) => p.id === config.preset);
      if (!this._scene) throw new Error(`unknown preset ${config.preset}`);
      super.setConfig(config);
    }

    getCardSize() {
      return 1;
    }

    getGridOptions() {
      return { columns: 6, rows: 1, min_columns: 2 };
    }

    _watching() {
      return [...this._config.entities, this._config.scenes_entity];
    }

    _selected() {
      const on = this._states().filter((s) => s.state === "on");
      const scenes = this._hass.states[this._config.scenes_entity]?.attributes.scenes || {};
      const scene = this._config.natural ? "natural" : this._config.preset;
      return on.length > 0 && on.every((s) => scenes[s.entity_id] === scene);
    }

    _render() {
      this._root.innerHTML = `
        <style>
          ${CIRCLE_CSS}
          :host { --scene-rgb: ${this._scene.rgb}; }
          ha-card { height: 100%; box-sizing: border-box; display: flex; align-items: center; gap: 10px;
                    padding: 0 10px; cursor: pointer; user-select: none; -webkit-tap-highlight-color: transparent;
                    transition: background-color 180ms, opacity 180ms; }
          .name { font-size: 14px; font-weight: 500; line-height: 20px; letter-spacing: 0.1px; color: var(--primary-text-color);
                  min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          ha-card.on { background: rgba(var(--scene-rgb), 0.12); }
          ha-card.on .circle { background: rgba(var(--scene-rgb), 0.25); color: rgb(var(--scene-rgb)); }
        </style>
        <ha-card role="button" tabindex="0">
          <div class="circle"><ha-icon icon="${this._scene.icon}"></ha-icon></div><span class="name">${this._scene.name}</span>
        </ha-card>`;
      this._card = this._root.querySelector("ha-card");
      onActivate(this._card, () => {
        const target = this._config.target || {};
        if (this._config.natural) this._hass.callService(DOMAIN, "natural_light", {}, target);
        else this._hass.callService(DOMAIN, "apply_preset", { preset: this._scene.id }, target);
      });
    }

    _update() {
      if (!this._card || !this._hass) return;
      const selected = this._selected();
      this._card.classList.toggle("on", selected);
      this._card.setAttribute("aria-pressed", String(selected));
      this._card.title = selected ? `${this._scene.name} (current)` : this._scene.name;
    }
  }

  // ---- Rooms card ----------------------------------------------------------

  // Remembers the room order and which rooms and routines are collapsed for
  // the signed-in user (Home Assistant's per-user frontend storage, so it
  // follows you across devices), falling back to this browser if that isn't
  // available.
  const ROOMS_KEY = "hue_lite_rooms";
  const ROUTINES_KEY = "hue_lite_routines";

  async function loadLayout(hass, key) {
    try {
      const res = await hass.callWS({ type: "frontend/get_user_data", key });
      if (res && res.value) return res.value;
    } catch (err) { /* fall through */ }
    try { return JSON.parse(localStorage.getItem(key)) || {}; } catch (err) { return {}; }
  }

  function saveLayout(hass, key, layout) {
    try { localStorage.setItem(key, JSON.stringify(layout)); } catch (err) { /* private mode */ }
    hass.callWS({ type: "frontend/set_user_data", key, value: layout }).catch(() => {});
  }

  // The look of a collapsible block's heading, shared by rooms and routines.
  const BLOCK_CSS = `
    .head { display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 0 4px; cursor: pointer; user-select: none;
            color: var(--primary-text-color); -webkit-tap-highlight-color: transparent; border-radius: 8px; }
    .head:focus { outline: none; }
    .head:focus-visible { outline: 2px solid var(--primary-color); }
    .head .block-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
    .title { font-size: 16px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .summary { flex: 1; min-width: 0; font-size: 13px; color: var(--secondary-text-color); white-space: nowrap;
               overflow: hidden; text-overflow: ellipsis; }
    .chev { --mdc-icon-size: 22px; color: var(--secondary-text-color); transition: transform 180ms; }
    .collapsed .chev { transform: rotate(-90deg); }
    .collapsed .body { display: none; }`;

  // Every room (and label group) as a collapsible block: tap the heading to fold
  // it away (it still shows how many lights are on, with a power button), and
  // use Reorder to move rooms up or down. New rooms join at the end.
  class HueLiteRooms extends HTMLElement {
    setConfig(config) {
      if (!Array.isArray(config.rooms)) throw new Error("rooms is required");
      this._config = config;
      this._lights = [...new Set(config.rooms.flatMap((r) => r.lights))];
      this._seen = null;
      this._layout = this._layout || { order: [], collapsed: [] };
      this._reordering = false;
      this._render();
      if (this._hass && !this._loaded) this._load();
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._loaded) this._load();
      // The cards inside skip redrawing themselves when their lights haven't changed.
      for (const child of this._children || []) child.hass = hass;
      if (statesChanged(this, hass, this._lights)) this._update();
    }

    getCardSize() {
      return 10;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    async _load() {
      this._loaded = true;
      const saved = await loadLayout(this._hass, ROOMS_KEY);
      this._layout = { order: saved.order || [], collapsed: saved.collapsed || [] };
      this._arrange();
    }

    _save() {
      saveLayout(this._hass, ROOMS_KEY, this._layout);
    }

    // Saved order first, then any rooms the saved order doesn't know yet.
    _orderedIds() {
      const ids = this._config.rooms.map((r) => r.id);
      return [...this._layout.order.filter((id) => ids.includes(id)), ...ids.filter((id) => !this._layout.order.includes(id))];
    }

    _render() {
      this._root = this._root || this.attachShadow({ mode: "open" });
      this._root.innerHTML = `
        <style>
          :host { display: block; }
          .toolbar { display: flex; align-items: center; gap: 4px; padding: 0 4px 8px; }
          .toolbar h2 { flex: 1; margin: 0; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
          button { border: none; background: none; color: var(--secondary-text-color); font: inherit; font-size: 13px; cursor: pointer;
                   display: inline-flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 18px; --mdc-icon-size: 20px; }
          button:hover { background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.06); }
          button.active { color: var(--primary-color); background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
          .rooms { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; align-items: start; }
          ${BLOCK_CSS}
          .power.on { color: var(--amber-color, #ffc107); }
          .move { display: none; }
          .reordering .move { display: inline-flex; }
          .reordering .power { display: none; }
          .move button:disabled { opacity: 0.3; cursor: default; }
          .body { display: grid; grid-template-columns: repeat(12, 1fr); gap: 8px; margin-top: 8px; }
          .body > .cell { grid-column: span 12; }
          .cell > * { display: block; height: 100%; }
          .body > .cell.half { grid-column: span 6; }
          .body > .cell.tile { height: 56px; }
        </style>
        <div class="toolbar">
          <h2>Rooms</h2>
          <button class="fold" title="Collapse or expand every room"><ha-icon icon="mdi:unfold-less-horizontal"></ha-icon><span>Collapse all</span></button>
          <button class="reorder" title="Move rooms up or down"><ha-icon icon="mdi:swap-vertical"></ha-icon><span>Reorder</span></button>
        </div>
        <div class="rooms"></div>`;

      const list = this._root.querySelector(".rooms");
      this._rooms = new Map();
      this._children = [];
      for (const r of this._config.rooms) {
        const el = document.createElement("div");
        el.className = "room";
        el.innerHTML = `
          <div class="head" role="button" tabindex="0" aria-expanded="true">
            <ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
            <ha-icon class="block-icon" icon="${r.icon}"></ha-icon>
            <span class="title"></span>
            <span class="summary"></span>
            <button class="power" title="Turn ${r.lights.length > 1 ? "all " : ""}on or off"><ha-icon icon="mdi:power"></ha-icon></button>
            <span class="move">
              <button class="up" title="Move up"><ha-icon icon="mdi:arrow-up"></ha-icon></button>
              <button class="down" title="Move down"><ha-icon icon="mdi:arrow-down"></ha-icon></button>
            </span>
          </div>
          <div class="body"></div>`;
        el.querySelector(".title").textContent = r.name;
        const body = el.querySelector(".body");
        for (const cfg of r.cards) {
          const card = document.createElement(cfg.type.replace(/^custom:/, ""));
          card.setConfig(cfg);
          if (this._hass) card.hass = this._hass;
          const cell = document.createElement("div");
          cell.className = `cell${cfg.type === "custom:hue-lite-scene-tile" ? " tile" : ""}${cfg.grid_options?.columns === 6 ? " half" : ""}`;
          cell.appendChild(card);
          body.appendChild(cell);
          this._children.push(card);
        }
        onActivate(el.querySelector(".head"), () => {
          if (!this._reordering) this._toggleCollapsed(r.id);
        });
        onActivate(el.querySelector(".power"), () => {
          const anyOn = r.lights.some((id) => this._hass?.states[id]?.state === "on");
          this._hass.callService("light", anyOn ? "turn_off" : "turn_on", {}, { entity_id: r.lights });
        });
        onActivate(el.querySelector(".up"), () => this._move(r.id, -1));
        onActivate(el.querySelector(".down"), () => this._move(r.id, 1));
        this._rooms.set(r.id, el);
        list.appendChild(el);
      }

      onActivate(this._root.querySelector(".fold"), () => {
        const ids = this._config.rooms.map((r) => r.id);
        const allCollapsed = ids.every((id) => this._layout.collapsed.includes(id));
        this._layout.collapsed = allCollapsed ? [] : ids;
        this._save();
        this._arrange();
      });
      onActivate(this._root.querySelector(".reorder"), () => {
        this._reordering = !this._reordering;
        this._arrange();
      });
      this._arrange();
    }

    _toggleCollapsed(id) {
      const c = this._layout.collapsed;
      this._layout.collapsed = c.includes(id) ? c.filter((x) => x !== id) : [...c, id];
      this._save();
      this._arrange();
    }

    _move(id, step) {
      const order = this._orderedIds();
      const from = order.indexOf(id);
      const to = from + step;
      if (to < 0 || to >= order.length) return;
      [order[from], order[to]] = [order[to], order[from]];
      this._layout.order = order;
      this._save();
      this._arrange();
    }

    // Puts the rooms in order and applies collapsed / reorder state.
    _arrange() {
      if (!this._rooms) return;
      const list = this._root.querySelector(".rooms");
      const order = this._orderedIds();
      order.forEach((id, i) => {
        const el = this._rooms.get(id);
        list.appendChild(el);
        const collapsed = this._layout.collapsed.includes(id);
        el.classList.toggle("collapsed", collapsed);
        el.querySelector(".head").setAttribute("aria-expanded", String(!collapsed));
        el.querySelector(".up").disabled = i === 0;
        el.querySelector(".down").disabled = i === order.length - 1;
      });
      list.classList.toggle("reordering", this._reordering);
      const reorder = this._root.querySelector(".reorder");
      reorder.classList.toggle("active", this._reordering);
      reorder.querySelector("span").textContent = this._reordering ? "Done" : "Reorder";
      const allCollapsed = order.every((id) => this._layout.collapsed.includes(id));
      const fold = this._root.querySelector(".fold");
      fold.querySelector("span").textContent = allCollapsed ? "Expand all" : "Collapse all";
      fold.querySelector("ha-icon").setAttribute("icon", allCollapsed ? "mdi:unfold-more-horizontal" : "mdi:unfold-less-horizontal");
      this._update();
    }

    _update() {
      if (!this._rooms || !this._hass) return;
      for (const r of this._config.rooms) {
        const el = this._rooms.get(r.id);
        const on = r.lights.filter((id) => this._hass.states[id]?.state === "on").length;
        el.querySelector(".summary").textContent = r.lights.length === 1 ? (on ? "On" : "Off")
          : on ? `${on} of ${r.lights.length} on` : "All off";
        el.querySelector(".power").classList.toggle("on", on > 0);
      }
    }
  }

  // ---- Routines card -------------------------------------------------------

  // Wake up, Evening and Coming & going as collapsible blocks like the rooms;
  // collapsed, each still says what it will do. The blocks hold Home
  // Assistant's own cards (entities, tile, heading).
  //   {routines: [{id, name, icon, cards}], entities}
  // `entities` are Hue-Lite's entity ids, for the summaries.
  const isOn = (hass, id) => hass.states[id]?.state === "on";
  const clock = (hass, id) => (hass.states[id]?.state || "").slice(0, 5);
  // Home Assistant's own cards come from its card helpers, loaded once.
  let cardHelpers;
  const loadCardHelpers = () => (cardHelpers = cardHelpers || window.loadCardHelpers());
  const SUMMARIES = {
    wake_up: (hass, e) => {
      const days = (toggle, time, label) => `${label} ${isOn(hass, toggle) ? clock(hass, time) : "off"}`;
      return `${days(e.wake_up_weekdays, e.wake_up_weekday, "Mon–Fri")} · ${days(e.wake_up_weekends, e.wake_up_weekend, "Sat & Sun")}`;
    },
    evening: (hass, e) => (isOn(hass, e.evening_routine_enabled) ? hass.states[e.evening_routine]?.attributes.summary || "On" : "Off"),
    coming_going: (hass, e) => {
      const arrive = !isOn(hass, e.coming_home_enabled) ? "nothing" : isOn(hass, e.coming_home_only_dark) ? "lights on after dark" : "lights on";
      return `Arrive: ${arrive} · Leave: ${isOn(hass, e.leaving_home_enabled) ? "lights off" : "nothing"}`;
    },
  };

  class HueLiteRoutines extends HTMLElement {
    setConfig(config) {
      if (!Array.isArray(config.routines)) throw new Error("routines is required");
      this._config = config;
      this._seen = null;
      this._collapsed = this._collapsed || [];
      this._render();
      if (this._hass && !this._loaded) this._load();
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._loaded) this._load();
      for (const child of this._children || []) child.hass = hass;
      if (statesChanged(this, hass, Object.values(this._config.entities || {}))) this._update();
    }

    getCardSize() {
      return 6;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    async _load() {
      this._loaded = true;
      this._collapsed = (await loadLayout(this._hass, ROUTINES_KEY)).collapsed || [];
      this._arrange();
    }

    _render() {
      this._root = this._root || this.attachShadow({ mode: "open" });
      this._root.innerHTML = `
        <style>
          :host { display: block; }
          h2 { margin: 0; padding: 0 4px 8px; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
          .routines { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; align-items: start; }
          ${BLOCK_CSS}
          .body { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
        </style>
        <h2>Routines</h2>
        <div class="routines"></div>`;
      const list = this._root.querySelector(".routines");
      this._blocks = new Map();
      this._children = [];
      const render = (this._rendering = {});
      for (const r of this._config.routines) {
        const el = document.createElement("div");
        el.innerHTML = `
          <div class="head" role="button" tabindex="0" aria-expanded="true">
            <ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
            <ha-icon class="block-icon" icon="${r.icon}"></ha-icon>
            <span class="title"></span>
            <span class="summary"></span>
          </div>
          <div class="body"></div>`;
        el.querySelector(".title").textContent = r.name;
        onActivate(el.querySelector(".head"), () => {
          const c = this._collapsed;
          this._collapsed = c.includes(r.id) ? c.filter((x) => x !== r.id) : [...c, r.id];
          saveLayout(this._hass, ROUTINES_KEY, { collapsed: this._collapsed });
          this._arrange();
        });
        this._fill(render, el.querySelector(".body"), r.cards);
        this._blocks.set(r.id, el);
        list.appendChild(el);
      }
      this._arrange();
    }

    // Fills a block once the card helpers have loaded, unless the card has
    // been rendered again in the meantime.
    async _fill(render, body, cards) {
      const helpers = await loadCardHelpers();
      if (render !== this._rendering) return;
      for (const config of cards) {
        const card = helpers.createCardElement(config);
        if (this._hass) card.hass = this._hass;
        body.appendChild(card);
        this._children.push(card);
      }
    }

    _arrange() {
      if (!this._blocks) return;
      for (const [id, el] of this._blocks) {
        const collapsed = this._collapsed.includes(id);
        el.classList.toggle("collapsed", collapsed);
        el.querySelector(".head").setAttribute("aria-expanded", String(!collapsed));
      }
      this._update();
    }

    _update() {
      if (!this._blocks || !this._hass) return;
      for (const [id, el] of this._blocks) {
        el.querySelector(".summary").textContent = SUMMARIES[id]?.(this._hass, this._config.entities || {}) || "";
      }
    }
  }

  for (const [tag, cls] of [
    ["ll-strategy-dashboard-hue-lite", HueLiteStrategy],
    ["hue-lite-routines", HueLiteRoutines],
    ["hue-lite-light-bar", HueLiteLightBar],
    ["hue-lite-scene-tile", HueLiteSceneTile],
    ["hue-lite-rooms", HueLiteRooms],
  ]) {
    if (!customElements.get(tag)) customElements.define(tag, cls);
  }
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "hue-lite-light-bar",
    name: "Hue-Lite light bar",
    description: "Tap to toggle, slide to dim, hold for the light dialog; one light or several.",
  });
})();
