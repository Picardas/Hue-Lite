// Hue-Lite's Lights dashboard, loaded on every page while Hue-Lite is set up.
//
// A dashboard strategy (`strategy: {type: custom:hue-lite}`) that builds the
// Lights dashboard from the area and label registries every time it loads, so
// new lights, rooms and labels appear without editing anything. The
// integration tells it which labels drive the routines and its own entity ids
// (websocket `hue_lite/config`). Also defines four cards:
//   custom:hue-lite-light-bar   a light or a room: tap to toggle, slide to dim,
//                               Auto at the end, hold a single light for its colour dialog
//   custom:hue-lite-scene-tile  a scene button (Natural or a preset) that lights
//                               up while its lights are in that scene
//   custom:hue-lite-rooms       every room as a collapsible, reorderable block
//                               headed by a bar for all its lights
//   custom:hue-lite-routines    Wake up, Evening and Coming & going as collapsible blocks
//
// Layout: Whole home (fixed at the top, a block that collapses like a room),
// then the rooms card with one block per area with
// lights (ordered by floor unless you've reordered them) and one per label you
// create yourself, then the routines card. The role labels drive the routines
// and don't get blocks of their own.

(() => {
  const DOMAIN = "hue_lite";
  const HOLD_MS = 500;
  // The scenes' names and icons (their colours come from Hue-Lite): Sleep and
  // Natural light, the presets, then the colour scenes, in the tiles' order.
  const SLEEP = { id: "sleep", name: "Sleep", icon: "mdi:power-sleep" };
  const NATURAL = { id: "natural", name: "Natural", icon: "mdi:theme-light-dark" };
  const PRESETS = [
    { id: "energize", name: "Energize", icon: "mdi:lightning-bolt" },
    { id: "cool_bright", name: "Cool bright", icon: "mdi:white-balance-sunny" },
    { id: "read", name: "Read", icon: "mdi:book-open-variant" },
    { id: "relax", name: "Relax", icon: "mdi:sofa" },
    { id: "rest", name: "Rest", icon: "mdi:candle" },
    { id: "nightlight", name: "Nightlight", icon: "mdi:weather-night" },
    { id: "golden_hour", name: "Golden hour", icon: "mdi:weather-sunset" },
    { id: "movie", name: "Movie", icon: "mdi:movie-open-outline" },
  ];

  // ---- Registry helpers ----------------------------------------------------

  function areaOf(hass, entity) {
    return entity.area_id || hass.devices?.[entity.device_id]?.area_id || null;
  }

  // A light's labels with its device's and its area's, which Home Assistant
  // (and so Hue-Lite) counts too.
  function labelsOf(hass, entity) {
    return new Set([
      ...(entity.labels || []),
      ...(hass.devices?.[entity.device_id]?.labels || []),
      ...(hass.areas?.[areaOf(hass, entity)]?.labels || []),
    ]);
  }

  // Lights that take part: not hidden, not labelled Exclude from lighting and
  // not light groups (which would double up with their members).
  function allLights(hass, exclude) {
    return Object.values(hass.entities || {}).filter((e) =>
      e.entity_id.startsWith("light.") && !e.hidden && !e.entity_category && hass.states[e.entity_id] &&
      !labelsOf(hass, e).has(exclude) &&
      !Array.isArray(hass.states[e.entity_id].attributes.entity_id));
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

  // A light bar, with an Auto button (Hue-Lite's scenes sensor says which
  // lights are on Auto).
  function lightBar(setup, entities, extra = {}) {
    return { type: "custom:hue-lite-light-bar", entities, scenes_entity: setup.entities.light_scenes, ...extra };
  }

  // The scenes, two per row: Sleep (coloured as Rest, which it fades out
  // from) and Natural light (coloured as it is now), then the presets and
  // colour scenes (coloured by their lights). `target` is the action target:
  // {area_id}, {label_id}, {entity_id} or {} for every light.
  function sceneTiles(setup, lights, target) {
    const tile = (extra) => ({
      type: "custom:hue-lite-scene-tile", entities: lights, target, scenes_entity: setup.entities.light_scenes,
      grid_options: { columns: 6, rows: 1 }, ...extra,
    });
    return [
      tile({ sleep: true, light: setup.presets?.rest }),
      tile({ natural: true, natural_entity: setup.entities.natural_light }),
      ...PRESETS.map((p) => tile({ preset: p.id, light: setup.presets?.[p.id] })),
    ];
  }

  // A room (or label group, or the whole home) for the rooms card: a bar for
  // all its lights, with its icon, heading the block; inside, a bar per light
  // when it has several (unless `eachLight` is false) and the scenes.
  function room(setup, id, name, icon, lights, target, eachLight = true) {
    return {
      id,
      head: lightBar(setup, lights, { name, icon, expandable: true }),
      cards: [
        ...(eachLight && lights.length > 1 ? lights.map((l) => lightBar(setup, [l])) : []),
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
    // Whole home stays put at the top: a single collapsible block, like a
    // room, with its own saved state and no Rooms toolbar.
    const sections = everything.length ? [{
      type: "grid",
      cards: [{
        type: "custom:hue-lite-rooms",
        rooms: [room(setup, "home", "Whole home", "mdi:home", everything, {}, false)],
        toolbar: false,
        layout_key: "hue_lite_home",
      }],
    }] : [];

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
      const tagged = ids(lights.filter((x) => labelsOf(hass, x).has(label.label_id)));
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
        // The times are in the block's heading already.
        { type: "tile", entity: e.evening_routine, name: "Now", state_content: ["state"] },
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
      `**Natural light** is a scene that follows the sun: Energize from dawn, Energize to Cool bright through the morning, Cool bright from midday, Cool bright to Relax over the last 90 minutes before sunset, Relax to Rest after sunset, and Rest overnight (lights labelled *${label("nightlight")}*: Nightlight). Lights stay in it until you pick another scene, change them yourself or from an automation, or use one of your own scenes on them.`,
      "",
      "**Scenes**: Golden hour and Movie spread their colours across a room's colour bulbs and drift slowly between them. Sleep takes a room to Rest and fades it out over the bedtime fade below; lights on Auto stay on it.",
      "",
      `**Evening routine**: before sunset, lights labelled *${label("evening")}* come on dim (if someone's home), brighten to Cool bright, wind down through Relax to Rest before bed (*${label("nightlight")}* lights go to Nightlight for the last stretch) and fade out at bedtime. Picking a scene for a light, Natural light included, takes it out of the routine for the night.`,
      "",
      `**Coming home**: when someone arrives home, lights labelled *${label("coming_home")}* that are off come on in Natural light, optionally only if it's after dark. When the last person leaves, every light can go off.`,
      "",
      `**Adding a light**: give it an area, and the labels for the routines it should join (on the light, its device or its area). It appears here on the next reload. To keep a light out of all of this (e.g. one run by its own motion sensor), give it the *${label("exclude")}* label.`,
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

  // ---- Colours -------------------------------------------------------------

  // How a light's colour shows on its icon, its slider and its scene's tile:
  // the RGB colour the bulb reports (Hue-Lite works out a scene's the same
  // way), halfway to the fully saturated colour of its hue so whites still
  // show on the card. Pure white, and lights with no colour, show as amber.
  const AMBER = [255, 193, 7];

  function shownColour(rgb) {
    if (!Array.isArray(rgb)) return AMBER;
    const max = Math.max(...rgb);
    const min = Math.min(...rgb);
    if (max - min < 8) return AMBER;
    return rgb.map((c) => Math.round((c + (255 * (c - min)) / (max - min)) / 2));
  }

  const average = (rgbs) => [0, 1, 2].map((i) => rgbs.reduce((sum, c) => sum + c[i], 0) / rgbs.length);

  // The colour of these lights (their average), as shown.
  function lightsColour(states) {
    const rgbs = states.map((s) => s.attributes.rgb_color).filter(Array.isArray);
    return shownColour(rgbs.length ? average(rgbs) : null);
  }

  // How a scene tile looks, from its scene's colours (as shown): a tint that
  // runs through them, a fill for while it's on, a swatch, and the ink that
  // reads on the fill and swatch (white on dark ones like Movie's).
  function sceneLook(rgbs) {
    const colours = (rgbs?.length ? rgbs : [null]).map(shownColour);
    if (colours.length === 1) colours.push(colours[0]);
    const last = colours.length - 1;
    const stops = (alpha, upTo) =>
      colours.map((c, i) => `rgba(${c}, ${alpha(i / last).toFixed(2)}) ${Math.round((i / last) * upTo)}%`).join(", ");
    const luminance = average(colours).reduce((sum, c, i) => sum + [0.2126, 0.7152, 0.0722][i] * (c / 255) ** 2.2, 0);
    return {
      "--tint": `linear-gradient(115deg, ${stops((f) => 0.32 - 0.28 * f, 85)})`,
      "--fill": `linear-gradient(115deg, ${stops((f) => 1 - 0.25 * f, 100)})`,
      "--swatch": `radial-gradient(circle at 35% 30%, rgba(255, 255, 255, 0.55), transparent 60%),
        conic-gradient(from 200deg, ${[...colours, colours[0]].map((c) => `rgb(${c})`).join(", ")})`,
      "--ink": luminance < 0.18 ? "#fff" : "rgba(0, 0, 0, 0.87)",
    };
  }

  // A focus ring for keyboard use only, not left behind by a tap.
  const FOCUS_CSS = `
    [tabindex]:focus { outline: none; }
    [tabindex]:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }`;

  // Click, Enter or Space (not bubbling up to whatever holds `el`).
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

  // Base for the light bar and scene tile: config, hass and one-off rendering
  // into a shadow root.
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

  // One bar for a single light or a set (a room): tap to toggle, slide to dim,
  // hold a single light to open Home Assistant's light dialog (colour wheel,
  // colour temperature). The icon (`icon`, or a bulb) and the slider take the
  // lights' own colour, so they follow what Auto and the scenes do to them. With
  // `scenes_entity`, an Auto button at the end puts the lights on Auto
  // (following the routines) or takes them off it; on a set it shows when only
  // some are on Auto, and a tap sets them all.
  // With `expandable` the bar heads a block: the icon toggles, and tapping the
  // rest of it (the name has a chevron) sends `hue-lite-expand`, for the block
  // to open or close; setting `expanded` turns the chevron.
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

    _watching() {
      return this._config.scenes_entity ? [...this._config.entities, this._config.scenes_entity] : this._config.entities;
    }

    _call(service, data, entities = this._config.entities) {
      this._hass.callService("light", service, data, { entity_id: entities });
    }

    // How many of the bar's lights are on Auto.
    _onAuto() {
      const auto = this._hass?.states[this._config.scenes_entity]?.attributes.auto || [];
      return this._config.entities.filter((id) => auto.includes(id)).length;
    }

    set expanded(open) {
      this._open = open;
      if (!this._el) return;
      this._el.card.classList.toggle("collapsed", !open);
      this._el.card.setAttribute("aria-expanded", String(open));
    }

    _render() {
      const expandable = Boolean(this._config.expandable);
      this._root.innerHTML = `
        <style>
          ${CIRCLE_CSS}
          ${FOCUS_CSS}
          :host { --light-rgb: ${AMBER}; }
          ha-card { height: 72px; box-sizing: border-box; padding: 0 12px; display: flex; align-items: center; gap: 12px;
                    cursor: pointer; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none;
                    -webkit-tap-highlight-color: transparent; }
          ha-card.unavailable { opacity: 0.5; }
          .light { width: 40px; height: 40px; }
          ha-card.on .light { background: rgba(var(--light-rgb), 0.25); color: rgb(var(--light-rgb)); }
          .text { flex: 0 1 38%; min-width: 0; }
          .name { display: flex; align-items: center; gap: 2px; font-size: 14px; font-weight: 500; line-height: 20px;
                  color: var(--primary-text-color); }
          .expandable .name { font-size: 16px; }
          .name span, .state { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .state { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
          .chev { --mdc-icon-size: 20px; flex: none; color: var(--secondary-text-color); transition: transform 180ms; }
          .collapsed .chev { transform: rotate(-90deg); }
          input { -webkit-appearance: none; appearance: none; flex: 1; min-width: 60px; height: 36px; margin: 0;
                  background: transparent; cursor: pointer; --pct: 0%; --fill: var(--light-rgb); }
          ha-card:not(.on) input { --fill: 150, 150, 150; }
          input::-webkit-slider-runnable-track { height: 12px; border-radius: 6px;
            background: linear-gradient(to right, rgb(var(--fill)) var(--pct), rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12) var(--pct)); }
          input::-moz-range-track { height: 12px; border-radius: 6px; background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12); }
          input::-moz-range-progress { height: 12px; border-radius: 6px; background: rgb(var(--fill)); }
          input::-webkit-slider-thumb { -webkit-appearance: none; width: 20px; height: 20px; margin-top: -4px; border-radius: 50%;
            background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35); }
          input::-moz-range-thumb { width: 20px; height: 20px; border: none; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35); }
          .auto { cursor: pointer; --mdc-icon-size: 22px; }
          .auto.some { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.1); color: var(--primary-color); }
          .auto.all { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.25); color: var(--primary-color); }
        </style>
        <ha-card class="${expandable ? "expandable" : ""}" role="button" tabindex="0">
          <div class="circle light"${expandable ? ` role="button" tabindex="0"` : ""}><ha-icon></ha-icon></div>
          <div class="text">
            <div class="name"><span></span>${expandable ? `<ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>` : ""}</div>
            <div class="state"></div>
          </div>
          <input type="range" min="1" max="100" step="1">
          ${this._config.scenes_entity ? `<div class="circle auto" role="button" tabindex="0"><ha-icon icon="mdi:brightness-auto"></ha-icon></div>` : ""}
        </ha-card>`;

      const $ = (sel) => this._root.querySelector(sel);
      this._el = {
        card: $("ha-card"), light: $(".light"), icon: $(".light ha-icon"), name: $(".name span"), state: $(".state"),
        slider: $("input"), auto: $(".auto"),
      };
      const { card, light, slider, auto } = this._el;
      const all = this._single ? "on or off" : "all on or off";
      card.title = expandable ? "Tap to show or hide the lights and scenes" : `Tap to switch ${all}${this._single ? ", hold for colour" : ""}`;
      if (expandable) light.title = `Switch ${all}`;

      // Auto: on for all the bar's lights unless they're all on it already.
      if (auto) {
        auto.addEventListener("pointerdown", (ev) => ev.stopPropagation());
        onActivate(auto, () => {
          const all = this._onAuto() === this._config.entities.length;
          this._hass.callService(DOMAIN, "set_auto", { auto: !all }, { entity_id: this._config.entities });
        });
      }

      // Tap toggles (heading a block, only the icon does, and the rest opens
      // or closes the block); holding a single light opens its dialog instead.
      const wasHold = onHold(card, () => {
        if (!this._single) return;
        this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: this._config.entities[0] }, bubbles: true, composed: true }));
      });
      const toggle = () => {
        if (!wasHold()) this._call(this._states().some((s) => s.state === "on") ? "turn_off" : "turn_on", {});
      };
      if (expandable) {
        onActivate(light, toggle);
        onActivate(card, () => {
          if (!wasHold()) this.dispatchEvent(new CustomEvent("hue-lite-expand", { bubbles: true, composed: true }));
        });
        this.expanded = this._open ?? true;
      } else {
        onActivate(card, toggle);
      }

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
      // The icon and the slider show the colour of the lights that are on.
      card.style.setProperty("--light-rgb", lightsColour(on).join(", "));
      icon.setAttribute("icon", this._config.icon || (!this._single ? "mdi:lightbulb-group"
        : single?.attributes.icon || (on.length ? "mdi:lightbulb" : "mdi:lightbulb-outline")));
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

      if (this._el.auto) {
        const onAuto = this._onAuto();
        const all = onAuto === states.length && onAuto > 0;
        this._el.auto.classList.toggle("all", all);
        this._el.auto.classList.toggle("some", onAuto > 0 && !all);
        this._el.auto.setAttribute("aria-pressed", all ? "true" : onAuto ? "mixed" : "false");
        this._el.auto.title = (all ? "On Auto" : onAuto ? `${onAuto} of ${states.length} on Auto` : "Not on Auto")
          + ": following wake up and the evening routine. Tap to " + (all ? "stop." : "start.");
      }
    }
  }

  // ---- Scene tile ----------------------------------------------------------

  // Natural light's phases, as its tile shows them.
  const PHASES = { night: "Night", sunrise: "Sunrise", morning: "Morning", day: "Day", sunset: "Sunset", evening: "Evening" };

  // A scene button styled like a tile, tinted with the scene's colours: tap to
  // put the lights in that scene. Filled with them and ticked while every
  // light that's on is in it and not on Auto (which shows on the Auto button
  // instead).
  //   {sleep: true, light, entities, target, scenes_entity}              Sleep
  //   {natural: true, natural_entity, entities, target, scenes_entity}   Natural light
  //   {preset: id, light, entities, target, scenes_entity}               a preset or colour scene
  // Natural light's tile takes its colour and phase from `natural_entity`, as
  // it is now; the others' colours are their `light` ({rgb_colors}).
  // `scenes_entity` says which scene each light is in (attributes `scenes` and
  // `auto`); `target` is passed to the actions: {area_id}, {label_id},
  // {entity_id} or {} for all.
  class HueLiteSceneTile extends HueLiteCard {
    setConfig(config) {
      this._scene = config.sleep ? SLEEP : config.natural ? NATURAL : PRESETS.find((p) => p.id === config.preset);
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
      return [...this._config.entities, this._config.scenes_entity, ...(this._config.natural ? [this._config.natural_entity] : [])];
    }

    _selected() {
      const on = this._states().filter((s) => s.state === "on");
      const { scenes = {}, auto = [] } = this._hass.states[this._config.scenes_entity]?.attributes || {};
      return on.length > 0 && on.every((s) => scenes[s.entity_id] === this._scene.id && !auto.includes(s.entity_id));
    }

    _render() {
      this._root.innerHTML = `
        <style>
          ${CIRCLE_CSS}
          ${FOCUS_CSS}
          :host { --card-bg: var(--ha-card-background, var(--card-background-color, #fff)); }
          ha-card { height: 100%; box-sizing: border-box; display: flex; align-items: center; gap: 10px; padding: 0 10px;
                    cursor: pointer; user-select: none; -webkit-tap-highlight-color: transparent; overflow: hidden;
                    background: var(--tint, none), var(--card-bg); }
          .circle { background: var(--swatch); color: var(--ink); }
          .text { flex: 1; min-width: 0; }
          .name, .now { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .name { font-size: 14px; font-weight: 500; line-height: 20px; letter-spacing: 0.1px; color: var(--primary-text-color); }
          .now { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }
          .now:empty { display: none; }
          .check { display: none; flex: none; --mdc-icon-size: 20px; }
          ha-card.on { background: var(--fill), var(--card-bg); }
          ha-card.on .circle { background: rgba(255, 255, 255, 0.3); }
          ha-card.on :is(.name, .now, .check) { color: var(--ink); }
          ha-card.on .check { display: block; }
        </style>
        <ha-card role="button" tabindex="0">
          <div class="circle"><ha-icon icon="${this._scene.icon}"></ha-icon></div>
          <div class="text"><div class="name">${this._scene.name}</div><div class="now"></div></div>
          <ha-icon class="check" icon="mdi:check-circle"></ha-icon>
        </ha-card>`;
      this._card = this._root.querySelector("ha-card");
      this._now = this._root.querySelector(".now");
      onActivate(this._card, () => {
        const target = this._config.target || {};
        if (this._config.sleep) this._hass.callService(DOMAIN, "sleep", {}, target);
        else if (this._config.natural) this._hass.callService(DOMAIN, "natural_light", {}, target);
        else this._hass.callService(DOMAIN, "apply_preset", { preset: this._scene.id }, target);
      });
    }

    _update() {
      if (!this._card || !this._hass) return;
      // Natural light's tile shows where it is now: its colour and phase.
      const now = this._config.natural ? this._hass.states[this._config.natural_entity] : undefined;
      const rgbs = now ? [now.attributes.rgb_color].filter(Array.isArray) : this._config.light?.rgb_colors;
      for (const [name, value] of Object.entries(sceneLook(rgbs))) this._card.style.setProperty(name, value);
      this._now.textContent = (now && PHASES[now.state]) || "";
      const selected = this._selected();
      this._card.classList.toggle("on", selected);
      this._card.setAttribute("aria-pressed", String(selected));
      this._card.title = selected ? `${this._scene.name} (current)` : this._scene.name;
    }
  }

  // ---- Collapsible blocks (rooms and routines) -----------------------------

  // Saved per signed-in user in Home Assistant's frontend storage (so it
  // follows you across devices), falling back to this browser.
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

  const BLOCKS_CSS = `
    :host { display: block; }
    .blocks { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; align-items: start; }
    .collapsed .body { display: none; }
    ${FOCUS_CSS}`;

  // A card of collapsible blocks, each a heading above a body that folds away.
  // Which blocks are collapsed is saved per user under the card's
  // `layout_key`, or the class's `layoutKey`. Subclasses render the blocks
  // (their headings calling `_toggleCollapsed`), show on a heading whether
  // its block is open (`_showOpen`), and can redraw from the states of the
  // entities they're `_watching` (`_update`).
  class CollapsibleBlocks extends HTMLElement {
    setConfig(config) {
      this._validate(config);
      this._config = config;
      this._seen = null;
      this._layout = this._layout || { order: [], collapsed: [] };
      this._root = this._root || this.attachShadow({ mode: "open" });
      this._blocks = new Map();
      this._children = [];
      this._render();
      this._arrange();
      if (this._hass && !this._loaded) this._load();
    }

    set hass(hass) {
      this._hass = hass;
      if (!this._loaded) this._load();
      // The cards inside skip redrawing themselves when their own states haven't changed.
      for (const child of this._children) child.hass = hass;
      if (statesChanged(this, hass, this._watching())) this._update();
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    get _layoutKey() {
      return this._config.layout_key || this.constructor.layoutKey;
    }

    async _load() {
      this._loaded = true;
      const saved = await loadLayout(this._hass, this._layoutKey);
      this._layout = { order: saved.order || [], collapsed: saved.collapsed || [] };
      this._arrange();
    }

    _save() {
      saveLayout(this._hass, this._layoutKey, this._layout);
    }

    _watching() {
      return [];
    }

    _update() {}

    // A block: `head` above an empty body.
    _block(id, head) {
      const el = document.createElement("div");
      el.className = "block";
      const body = document.createElement("div");
      body.className = "body";
      el.append(head, body);
      this._blocks.set(id, el);
      return el;
    }

    _toggleCollapsed(id) {
      if (this._reordering) return;
      const c = this._layout.collapsed;
      this._layout.collapsed = c.includes(id) ? c.filter((x) => x !== id) : [...c, id];
      this._save();
      this._arrange();
    }

    // Applies the collapsed state, then redraws.
    _arrange() {
      for (const [id, el] of this._blocks) {
        const collapsed = this._layout.collapsed.includes(id);
        el.classList.toggle("collapsed", collapsed);
        this._showOpen(el, !collapsed);
      }
      this._update();
    }
  }

  // ---- Rooms card ----------------------------------------------------------

  // Every room (and label group) as a block headed by a light bar for all its
  // lights (`head`): its icon switches them on or off, the slider dims them,
  // Auto puts them on Auto, and tapping the rest of it shows or hides the
  // room's own light bars and scenes (`cards`). Reorder moves rooms up or
  // down; new rooms join at the end. With `toolbar: false` there's no Rooms
  // heading, Collapse all or Reorder (the Whole home block uses that, with a
  // `layout_key` of its own).
  class HueLiteRooms extends CollapsibleBlocks {
    static layoutKey = "hue_lite_rooms";

    _validate(config) {
      if (!Array.isArray(config.rooms)) throw new Error("rooms is required");
      this._reordering = false;
    }

    getCardSize() {
      return 10;
    }

    // Saved order first, then any rooms the saved order doesn't know yet.
    _orderedIds() {
      const ids = this._config.rooms.map((r) => r.id);
      return [...this._layout.order.filter((id) => ids.includes(id)), ...ids.filter((id) => !this._layout.order.includes(id))];
    }

    _render() {
      this._root.innerHTML = `
        <style>
          ${BLOCKS_CSS}
          .toolbar { display: flex; align-items: center; gap: 4px; padding: 0 4px 8px; }
          .toolbar h2 { flex: 1; margin: 0; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
          button { border: none; background: none; color: var(--secondary-text-color); font: inherit; font-size: 13px; cursor: pointer;
                   display: inline-flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 18px; --mdc-icon-size: 20px; }
          button:hover { background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.06); }
          button.active { color: var(--primary-color); background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
          .head { display: flex; align-items: center; gap: 4px; }
          .head > hue-lite-light-bar { flex: 1; min-width: 0; }
          .move { display: none; }
          .reordering .move { display: inline-flex; }
          .move button:disabled { opacity: 0.3; cursor: default; }
          .body { display: grid; grid-template-columns: repeat(12, 1fr); gap: 8px; margin-top: 8px; }
          .body > .cell { grid-column: span 12; }
          .cell > * { display: block; height: 100%; }
          .body > .cell.half { grid-column: span 6; }
          .body > .cell.tile { height: 56px; }
        </style>
        ${this._config.toolbar === false ? "" : `
        <div class="toolbar">
          <h2>Rooms</h2>
          <button class="fold" title="Collapse or expand every room"><ha-icon icon="mdi:unfold-less-horizontal"></ha-icon><span>Collapse all</span></button>
          <button class="reorder" title="Move rooms up or down"><ha-icon icon="mdi:swap-vertical"></ha-icon><span>Reorder</span></button>
        </div>`}
        <div class="blocks"></div>`;

      const list = this._root.querySelector(".blocks");
      for (const r of this._config.rooms) {
        const head = document.createElement("div");
        head.className = "head";
        head.innerHTML = `
          <span class="move">
            <button class="up" title="Move up"><ha-icon icon="mdi:arrow-up"></ha-icon></button>
            <button class="down" title="Move down"><ha-icon icon="mdi:arrow-down"></ha-icon></button>
          </span>`;
        const bar = this._card(r.head);
        bar.addEventListener("hue-lite-expand", () => this._toggleCollapsed(r.id));
        head.prepend(bar);
        const el = this._block(r.id, head);
        const body = el.querySelector(".body");
        for (const cfg of r.cards) {
          const cell = document.createElement("div");
          cell.className = `cell${cfg.type === "custom:hue-lite-scene-tile" ? " tile" : ""}${cfg.grid_options?.columns === 6 ? " half" : ""}`;
          cell.appendChild(this._card(cfg));
          body.appendChild(cell);
        }
        onActivate(el.querySelector(".up"), () => this._move(r.id, -1));
        onActivate(el.querySelector(".down"), () => this._move(r.id, 1));
        list.appendChild(el);
      }

      if (this._config.toolbar === false) return;
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
    }

    // One of the Hue-Lite cards inside, handed hass along with the rest.
    _card(config) {
      const card = document.createElement(config.type.replace(/^custom:/, ""));
      card.setConfig(config);
      if (this._hass) card.hass = this._hass;
      this._children.push(card);
      return card;
    }

    _showOpen(el, open) {
      el.querySelector(".head > hue-lite-light-bar").expanded = open;
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

    // Puts the rooms in order, and applies the collapsed and reorder states.
    _arrange() {
      const list = this._root.querySelector(".blocks");
      const order = this._orderedIds();
      order.forEach((id, i) => {
        const el = this._blocks.get(id);
        list.appendChild(el);
        el.querySelector(".up").disabled = i === 0;
        el.querySelector(".down").disabled = i === order.length - 1;
      });
      list.classList.toggle("reordering", this._reordering);
      const reorder = this._root.querySelector(".reorder");
      const fold = this._root.querySelector(".fold");
      if (reorder && fold) {
        reorder.classList.toggle("active", this._reordering);
        reorder.querySelector("span").textContent = this._reordering ? "Done" : "Reorder";
        const allCollapsed = order.every((id) => this._layout.collapsed.includes(id));
        fold.querySelector("span").textContent = allCollapsed ? "Expand all" : "Collapse all";
        fold.querySelector("ha-icon").setAttribute("icon", allCollapsed ? "mdi:unfold-more-horizontal" : "mdi:unfold-less-horizontal");
      }
      super._arrange();
    }
  }

  // ---- Routines card -------------------------------------------------------

  // Wake up, Evening and Coming & going as blocks holding Home Assistant's own
  // cards (entities, tile, heading). The summary goes on its own line under
  // the title, so it isn't cut off on a phone.
  //   {routines: [{id, name, icon, cards}], entities}
  // `entities` are Hue-Lite's entity ids, for the summaries.
  const isOn = (hass, id) => hass.states[id]?.state === "on";
  const clock = (hass, id) => (hass.states[id]?.state || "").slice(0, 5);
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
  // Home Assistant's own cards come from its card helpers, loaded once.
  let cardHelpers;
  const loadCardHelpers = () => (cardHelpers = cardHelpers || window.loadCardHelpers());

  class HueLiteRoutines extends CollapsibleBlocks {
    static layoutKey = "hue_lite_routines";

    _validate(config) {
      if (!Array.isArray(config.routines)) throw new Error("routines is required");
    }

    _watching() {
      return Object.values(this._config.entities || {});
    }

    getCardSize() {
      return 6;
    }

    _render() {
      this._root.innerHTML = `
        <style>
          ${BLOCKS_CSS}
          h2 { margin: 0; padding: 0 4px 8px; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
          .head { display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 0 4px; cursor: pointer; user-select: none;
                  color: var(--primary-text-color); -webkit-tap-highlight-color: transparent; border-radius: 8px; }
          .block-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); flex: none; }
          .text { flex: 1; min-width: 0; display: flex; flex-direction: column; padding: 4px 0; }
          .title { font-size: 16px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .summary { font-size: 13px; color: var(--secondary-text-color); }
          .chev { --mdc-icon-size: 22px; color: var(--secondary-text-color); transition: transform 180ms; flex: none; }
          .collapsed .chev { transform: rotate(-90deg); }
          .body { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
        </style>
        <h2>Routines</h2>
        <div class="blocks"></div>`;
      const list = this._root.querySelector(".blocks");
      const render = (this._rendering = {});
      for (const r of this._config.routines) {
        const el = this._block(r.id, this._heading(r));
        this._fill(render, el.querySelector(".body"), r.cards);
        list.appendChild(el);
      }
    }

    // A routine's heading: chevron, icon, title and summary; tapping it opens
    // or closes the block.
    _heading(r) {
      const head = document.createElement("div");
      head.className = "head";
      head.setAttribute("role", "button");
      head.tabIndex = 0;
      head.innerHTML = `
        <ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
        <ha-icon class="block-icon"></ha-icon>
        <span class="text"><span class="title"></span><span class="summary"></span></span>`;
      head.querySelector(".title").textContent = r.name;
      head.querySelector(".block-icon").setAttribute("icon", r.icon);
      onActivate(head, () => this._toggleCollapsed(r.id));
      return head;
    }

    _showOpen(el, open) {
      el.querySelector(".head").setAttribute("aria-expanded", String(open));
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

    _update() {
      if (!this._hass) return;
      for (const [id, el] of this._blocks) {
        el.querySelector(".summary").textContent = SUMMARIES[id]?.(this._hass, this._config.entities || {}) || "";
      }
    }
  }

  for (const [tag, cls] of [
    ["ll-strategy-dashboard-hue-lite", HueLiteStrategy],
    ["hue-lite-light-bar", HueLiteLightBar],
    ["hue-lite-scene-tile", HueLiteSceneTile],
    ["hue-lite-rooms", HueLiteRooms],
    ["hue-lite-routines", HueLiteRoutines],
  ]) {
    if (!customElements.get(tag)) customElements.define(tag, cls);
  }
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "hue-lite-light-bar",
    name: "Hue-Lite light bar",
    description: "Tap to toggle, slide to dim, Auto, hold for the light dialog; one light or several.",
  });
})();
