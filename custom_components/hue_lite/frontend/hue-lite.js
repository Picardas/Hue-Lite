// Hue-Lite's Lights dashboard, loaded on every page while Hue-Lite is set up.
//
// A dashboard strategy (`strategy: {type: custom:hue-lite}`) that builds the
// Lights dashboard from the area and label registries every time it loads, so
// new lights, rooms and labels appear without editing anything. The
// integration tells it which labels drive the routines and its own entity ids
// (websocket `hue_lite/config`). Also defines four cards:
//   custom:hue-lite-light-bar   a light or a room: tap to toggle, slide to dim,
//                               Auto at the end, hold a single light for its colour dialog
//   custom:hue-lite-scene-tile  a scene button (Natural light, a preset or a colour
//                               scene) that lights up while its lights are in it
//   custom:hue-lite-rooms       every room as a collapsible, reorderable block
//                               headed by a bar for all its lights
//   custom:hue-lite-routines    Wake up, Evening and Coming & Going as collapsible blocks
//   custom:hue-lite-dimmers     each Hue dimmer: the lights it controls and its scenes
//
// Each room's and light's scenes, and the dimmers' settings, are kept by
// Hue-Lite and shared by every dashboard (websocket `hue_lite/subscribe`);
// the rest of the layout is each person's own.
//
// Layout: Whole home (fixed at the top, a block that collapses like a room),
// then the rooms card with one block per area with
// lights (ordered by floor unless you've reordered them) and one per label you
// create yourself, then the routines card. The role labels drive the routines
// and don't get blocks of their own.

(() => {
  const DOMAIN = "hue_lite";
  const HOLD_MS = 500;
  // How long a tap shows before the bulbs report it. A bulb that doesn't
  // answer takes a few seconds to fail; after this the card shows it as it is.
  const PENDING_MS = 5000;
  // The scenes' names and icons (their colours come from Hue-Lite): the
  // presets, then Natural light and the colour scenes, which change over time.
  const NATURAL = { id: "natural", name: "Natural", icon: "mdi:theme-light-dark" };
  const PRESETS = [
    { id: "energize", name: "Energize", icon: "mdi:lightning-bolt" },
    { id: "cool_bright", name: "Cool bright", icon: "mdi:white-balance-sunny" },
    { id: "read", name: "Read", icon: "mdi:book-open-variant" },
    { id: "relax", name: "Relax", icon: "mdi:sofa" },
    { id: "rest", name: "Rest", icon: "mdi:candle" },
    { id: "nightlight", name: "Nightlight", icon: "mdi:weather-night" },
  ];
  const COLOUR_SCENES = [
    { id: "golden_hour", name: "Golden hour", icon: "mdi:weather-sunset" },
    { id: "nebula", name: "Nebula", icon: "mdi:creation" },
    { id: "aurora", name: "Aurora", icon: "mdi:aurora" },
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

  // The scenes, three per row, in their default order (each room can be set
  // to its own; see the rooms card): the presets, then Natural light
  // (coloured as it is now) and the colour scenes (coloured by their
  // lights). `target` is the action target: {area_id}, {label_id},
  // {entity_id} or {} for every light.
  function sceneTiles(setup, lights, target) {
    const tile = (extra) => ({
      type: "custom:hue-lite-scene-tile", entities: lights, target, scenes_entity: setup.entities.light_scenes,
      grid_options: { columns: 4, rows: 2 }, ...extra,
    });
    const preset = (p) => tile({ preset: p.id, light: setup.presets?.[p.id] });
    return [
      ...PRESETS.map(preset),
      tile({ natural: true, natural_entity: setup.entities.natural_light }),
      ...COLOUR_SCENES.map(preset),
    ];
  }

  // A room (or label group, or the whole home) for the rooms card: a bar for
  // all its lights, with its icon, heading the block; inside, the room's
  // scenes, then (when it has several lights, unless `eachLight` is false)
  // each light's bar, opening onto that light's own scenes.
  function room(setup, id, name, icon, lights, target, eachLight = true) {
    const light = (l) => ({ id: l, bar: lightBar(setup, [l], { expandable: true }), scenes: sceneTiles(setup, [l], { entity_id: [l] }) });
    return {
      id,
      name,
      head: lightBar(setup, lights, { name, icon, expandable: true, heading: true }),
      scenes: sceneTiles(setup, lights, target),
      lights: eachLight && lights.length > 1 ? lights.map(light) : [],
    };
  }

  function whenOn(toggle, row) {
    return { type: "conditional", conditions: [{ entity: toggle, state: "on" }], row };
  }

  // ---- Views ---------------------------------------------------------------

  function mainView(hass, setup, path) {
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
    sections.push({ type: "grid", column_span: 3, cards: [routinesCard(setup.entities, path)] });
    // Hue dimmers (when Hue-Lite's using them): which lights each controls, and its scenes.
    if (setup.dimmers?.length && everything.length) {
      sections.push({
        type: "grid",
        column_span: 3,
        cards: [{
          type: "custom:hue-lite-dimmers",
          dimmers: setup.dimmers.map((d) => ({ ...d, area_name: hass.areas?.[d.area_id]?.name })),
          lights: everything,
          scenes: sceneTiles(setup, everything, {}).map((tile) => ({ ...tile, preview: true })),
        }],
      });
    }

    return { title: "Lights", path: "lights", icon: "mdi:lightbulb-group", type: "sections", max_columns: 3, sections };
  }

  // The routines, in one card of collapsible blocks like the rooms, with the
  // Timings & fades link at the end. `e` are Hue-Lite's entity ids.
  function routinesCard(e, path) {
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
        // Where it is now, and tonight's times.
        { type: "tile", entity: e.evening_routine, name: "Now", state_content: ["state", "summary"] },
        {
          type: "entities",
          entities: [
            { entity: e.evening_weeknights, name: "Sun–Thu" },
            whenOn(e.evening_weeknights, { entity: e.bedtime_weeknight, name: "Bedtime" }),
            { entity: e.evening_weekends, name: "Fri & Sat" },
            whenOn(e.evening_weekends, { entity: e.bedtime_weekend, name: "Bedtime" }),
          ],
        },
      ],
    }, {
      id: "coming_going",
      name: "Coming & Going",
      icon: "mdi:home-import-outline",
      cards: [{
        type: "entities",
        title: "Coming",
        entities: [
          { entity: e.coming_home_enabled, name: "Lights on when I arrive" },
          whenOn(e.coming_home_enabled, { entity: e.coming_home_only_dark, name: "Only if I arrive after dark" }),
        ],
      }, {
        type: "entities",
        title: "Going",
        entities: [{ entity: e.leaving_home_enabled, name: "Lights off when everyone leaves" }],
      }],
    }];
    const footer = [{
      type: "heading",
      heading: "Timings & fades",
      heading_style: "subtitle",
      icon: "mdi:tune-variant",
      tap_action: { action: "navigate", navigation_path: `/${path}/settings` },
    }];
    return { type: "custom:hue-lite-routines", routines, footer, entities: e };
  }

  // Text as it is in Markdown: its punctuation can't make links or formatting.
  const markdownText = (text) => text.replace(/[\\`*_{}[\]()<>#+\-.!|~]/g, "\\$&");

  function howItWorks(setup) {
    const label = (role) => markdownText(setup.labels.find((l) => l.label_id === setup.roles[role])?.name || role);
    return [
      `**Natural light** is a scene that follows the sun: Energize from dawn, Energize to Cool bright through the morning, Cool bright from midday, Cool bright to Relax over the last 90 minutes before sunset, Relax to Rest after sunset, and Rest overnight (lights labelled *${label("nightlight")}*: Nightlight). Lights stay in it until you pick another scene, change them yourself or from an automation, or use one of your own scenes on them.`,
      "",
      "**Colour scenes**: Golden hour, Nebula and Aurora spread their colours across a room's colour bulbs and drift slowly between them, a step every three minutes.",
      "",
      "**Auto**: the button at the end of a light's or room's bar. On Auto, a light follows wake up and the evening routine whether or not it has their labels, until you pick a scene for it or change it. Put on Auto, it matches them straight away: on with whichever is running, off if neither is. Lights a routine has show as on Auto too.",
      "",
      `**Wake up**: on the days it's on (Mon–Fri and Sat & Sun each have a switch and a time), lights labelled *${label("wake_up")}* that are off fade from a dim warm glow to Energize, reaching full brightness *at* your wake-up time.`,
      "",
      `**Evening routine**: on the nights it's on (Sun–Thu and Fri & Sat each have a switch and a bedtime), before sunset, lights labelled *${label("evening")}* come on dim (if someone's home), brighten to Cool bright, wind down through Relax to Rest before bed (*${label("nightlight")}* lights go to Nightlight for the last stretch) and fade out at bedtime. Picking a scene for a light, Natural light included, takes it out of the routine for the night.`,
      "",
      `**Coming & Going**: when someone gets home (optionally only after dark), lights labelled *${label("coming_home")}* that are off come on, and so do lights labelled *${label("first_home")}* if the house was empty. Evening lights join the evening routine if it's on, except in its last half hour or so before bed, so they don't go off again soon after; the rest come on in Natural light. When the last person leaves, every light can go off.`,
      "",
      "**Hue dimmer switches** control the lights in their area, or the ones chosen on the Dimmers card: On steps through their scenes (set on the same card), Off turns them off, Up and Down dim them.",
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
  // reads on the fill and swatch (white on dark ones like Nebula's).
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

  // ---- Shared card bits ----------------------------------------------------

  // The round icon used by the light bars and the scene tiles.
  const CIRCLE_CSS = `
    .circle { --mdc-icon-size: 24px; width: 36px; height: 36px; border-radius: 50%; flex: none;
              display: flex; align-items: center; justify-content: center; transition: background-color 180ms, color 180ms;
              background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.05); color: var(--state-inactive-color, var(--secondary-text-color)); }`;

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
  // With `expandable` the bar opens onto what's under it (a room's scenes and
  // lights, or a light's scenes): the icon toggles, and tapping the rest of
  // it (the name has a chevron) sends `hue-lite-expand`, for the card holding
  // it to open or close; setting `expanded` turns the chevron. `heading`
  // gives a room's bar its larger name.
  class HueLiteLightBar extends HueLiteCard {
    getCardSize() {
      return 2;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    get _single() {
      return this._config.entities.length === 1;
    }

    _watching() {
      return [...this._config.entities, this._config.scenes_entity].filter(Boolean);
    }

    _call(service, data, entities = this._config.entities) {
      this._hass.callService("light", service, data, { entity_id: entities });
    }

    // How many of the bar's lights are on Auto, of how many could be: an
    // unavailable light can't be put on it, so it would leave a room's Auto
    // never all on, and a tap never taking the room off it.
    _auto() {
      const auto = this._hass?.states[this._config.scenes_entity]?.attributes.auto || [];
      const lights = this._states().filter((s) => s.state !== "unavailable").map((s) => s.entity_id);
      const on = lights.filter((id) => auto.includes(id)).length;
      return { on, of: lights.length, all: on > 0 && on === lights.length };
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
          .heading .name { font-size: 16px; }
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
        <ha-card class="${this._config.heading ? "heading" : ""}" role="button" tabindex="0">
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
      const which = this._single ? "on or off" : "all on or off";
      const hold = this._single ? ", hold for colour" : "";
      card.title = expandable ? `Tap to show or hide ${this._config.heading ? "the room's scenes and lights" : "its scenes"}${hold}`
        : `Tap to switch ${which}${hold}`;
      if (expandable) light.title = `Switch ${which}`;

      // Auto: on for all the bar's lights unless they're all on it already.
      if (auto) {
        auto.addEventListener("pointerdown", (ev) => ev.stopPropagation());
        onActivate(auto, () => {
          this._hass.callService(DOMAIN, "set_auto", { auto: !this._auto().all }, { entity_id: this._config.entities });
        });
      }

      // Tap toggles (heading a block, only the icon does, and the rest opens
      // or closes the block); holding a single light opens its dialog instead.
      const wasHold = onHold(card, () => {
        if (!this._single) return;
        this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: this._config.entities[0] }, bubbles: true, composed: true }));
      });
      const toggle = () => {
        if (!wasHold()) this._toggle();
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

    // Switch the lights on or off, showing it straight away: until they
    // report it (or PENDING_MS passes) the bar shows where they're going, and
    // another tap goes from there rather than sending the same command again.
    _toggle() {
      const states = this._states();
      if (this._unavailable(states)) return;
      const on = !(this._pending?.on ?? states.some((s) => s.state === "on"));
      this._settle();
      const seen = new Map(states.map((s) => [s.entity_id, s]));
      this._pending = { on, seen, timer: setTimeout(() => { this._settle(); this._update(); }, PENDING_MS) };
      this._call(on ? "turn_on" : "turn_off", {});
      this._update();
    }

    // Stop showing a tap.
    _settle() {
      clearTimeout(this._pending?.timer);
      this._pending = null;
    }

    // None of the lights can be reached (or there aren't any).
    _unavailable(states) {
      return states.every((s) => s.state === "unavailable");
    }

    _update() {
      if (!this._el || !this._hass) return;
      const { card, icon, name, state, slider } = this._el;
      const states = this._states();
      const on = states.filter((s) => s.state === "on");
      const brightness = on.map((s) => s.attributes.brightness).filter((b) => typeof b === "number");
      const pct = brightness.length ? Math.max(1, Math.round((brightness.reduce((a, b) => a + b, 0) / brightness.length / 255) * 100)) : 0;
      const single = this._single ? states[0] : undefined;
      const unavailable = this._unavailable(states);
      // Settled once the lights report where the tap was taking them (a
      // report since the tap, not the state they were already in).
      const reported = this._pending && states.some((s) => s !== this._pending.seen.get(s.entity_id));
      if (reported && this._pending.on === on.length > 0) this._settle();
      const pending = this._pending;

      card.classList.toggle("on", pending ? pending.on : on.length > 0);
      card.classList.toggle("unavailable", unavailable);
      // The icon and the slider show the colour of the lights that are on.
      card.style.setProperty("--light-rgb", lightsColour(on).join(", "));
      icon.setAttribute("icon", this._config.icon || (!this._single ? "mdi:lightbulb-group"
        : single?.attributes.icon || (on.length ? "mdi:lightbulb" : "mdi:lightbulb-outline")));
      name.textContent = this._config.name || single?.attributes.friendly_name || "Lights";
      state.textContent = unavailable ? "Unavailable"
        : pending ? (pending.on ? "Turning on…" : "Turning off…")
        : this._single ? (on.length ? `${pct}%` : "Off")
        : on.length ? `${on.length} of ${states.length} on · ${pct}%` : `All ${states.length} off`;

      slider.disabled = unavailable;
      slider.setAttribute("aria-label", `Brightness of ${name.textContent}`);
      if (this._root.activeElement !== slider) {
        const shown = pending?.on === false ? 0 : on.length ? pct : 0;
        slider.value = shown || 1;
        slider.style.setProperty("--pct", `${shown}%`);
      }

      if (this._el.auto) {
        const { on: onAuto, of, all } = this._auto();
        this._el.auto.classList.toggle("all", all);
        this._el.auto.classList.toggle("some", onAuto > 0 && !all);
        this._el.auto.setAttribute("aria-pressed", all ? "true" : onAuto ? "mixed" : "false");
        this._el.auto.title = (all ? "On Auto" : onAuto ? `${onAuto} of ${of} on Auto` : "Not on Auto")
          + ": following wake up and the evening routine. Tap to " + (all ? "stop." : "start.");
      }
    }
  }

  // ---- Scene tile ----------------------------------------------------------

  // Natural light's phases, as its tile shows them.
  const PHASES = { night: "Night", sunrise: "Sunrise", morning: "Morning", day: "Day", sunset: "Sunset", evening: "Evening" };

  // The tile last tapped for each target (as JSON), shown as picked until
  // its lights report the scene; tapping another for the same lights clears it.
  const pickedTiles = new Map();

  // A scene button: a small upright tile (the scene's swatch above its name),
  // tinted with the scene's colours. Tap to put the lights in that scene.
  // Filled with them and ticked while every light that's on is in it and not
  // on Auto (which shows on the Auto button instead), and straight away when
  // tapped, until the lights report it (or PENDING_MS passes).
  //   {natural: true, natural_entity, entities, target, scenes_entity}   Natural light
  //   {preset: id, light, entities, target, scenes_entity}               a preset or colour scene
  // Natural light's tile takes its colour and phase from `natural_entity`, as
  // it is now; the others' colours are their `light` ({rgb_colors}).
  // `scenes_entity` says which scene each light is in (attributes `scenes` and
  // `auto`); `target` is passed to the actions: {area_id}, {label_id},
  // {entity_id} or {} for all. With `preview` it only shows the scene (a
  // dimmer's): a tap does nothing, and it's never ticked.
  class HueLiteSceneTile extends HueLiteCard {
    setConfig(config) {
      this._scene = config.natural ? NATURAL : [...PRESETS, ...COLOUR_SCENES].find((p) => p.id === config.preset);
      if (!this._scene) throw new Error(`unknown preset ${config.preset}`);
      super.setConfig(config);
    }

    getCardSize() {
      return 2;
    }

    getGridOptions() {
      return { columns: 4, rows: 2, min_columns: 3 };
    }

    // The scene's id, name and icon.
    get scene() {
      return this._scene;
    }

    // The scene's colours (Natural light's as it is now), as sceneLook takes them.
    sceneColours() {
      if (!this._config.natural) return this._config.light?.rgb_colors;
      return [this._hass?.states[this._config.natural_entity]?.attributes.rgb_color].filter(Array.isArray);
    }

    _watching() {
      return [...this._config.entities, this._config.scenes_entity, this._config.natural_entity].filter(Boolean);
    }

    _selected() {
      if (this._config.preview) return false;
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
          ha-card { height: 100%; box-sizing: border-box; position: relative; display: flex; flex-direction: column;
                    align-items: center; justify-content: center; gap: 4px; padding: 8px 6px; text-align: center;
                    cursor: pointer; user-select: none; -webkit-tap-highlight-color: transparent; overflow: hidden;
                    background: var(--tint, none), var(--card-bg); }
          .circle { background: var(--swatch); color: var(--ink); }
          .text { max-width: 100%; }
          .name, .now { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .name { font-size: 13px; font-weight: 500; line-height: 18px; letter-spacing: 0.1px; color: var(--primary-text-color); }
          .now { font-size: 11px; line-height: 14px; color: var(--secondary-text-color); }
          .now:empty { display: none; }
          .check { display: none; position: absolute; top: 6px; right: 6px; --mdc-icon-size: 18px; }
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
      this._colours = null;
      onActivate(this._card, () => {
        if (this._config.preview) return;
        const target = this._config.target || {};
        if (this._config.natural) this._hass.callService(DOMAIN, "natural_light", {}, target);
        else this._hass.callService(DOMAIN, "apply_preset", { preset: this._scene.id }, target);
        const key = JSON.stringify(target);
        const last = pickedTiles.get(key);
        last?._settle();
        last?._update();
        pickedTiles.set(key, this);
        this._picked = setTimeout(() => { this._settle(); this._update(); }, PENDING_MS);
        this._update();
      });
    }

    // Stop showing a tap: the tile shows the lights as they are.
    _settle() {
      clearTimeout(this._picked);
      this._picked = null;
    }

    _update() {
      if (!this._card || !this._hass) return;
      // Natural light's tile shows where it is now: its colour and phase. The
      // look is only worked out again when the colours change (not whenever
      // the sensor's written, which is on every scene change).
      const now = this._config.natural ? this._hass.states[this._config.natural_entity] : undefined;
      const colours = String(now ? now.attributes.rgb_color : this._config.light?.rgb_colors);
      if (colours !== this._colours) {
        this._colours = colours;
        for (const [name, value] of Object.entries(sceneLook(this.sceneColours()))) this._card.style.setProperty(name, value);
      }
      this._now.textContent = (now && PHASES[now.state]) || "";
      const actual = this._selected();
      if (actual) this._settle();
      const selected = actual || Boolean(this._picked);
      this._card.classList.toggle("on", selected);
      this._card.setAttribute("aria-pressed", String(selected));
      this._card.title = selected ? `${this._scene.name} (current)` : this._scene.name;
    }
  }

  // ---- Collapsible blocks (rooms and routines) -----------------------------

  // How each person has their dashboard, saved per signed-in user in Home
  // Assistant's frontend storage (so it follows you across devices),
  // falling back to this browser: the blocks' order, which are collapsed,
  // and which lights inside them have their scenes open (by block id|light).
  // Scenes ({order, hidden} by block id) were kept here before Hue-Lite
  // shared them, and are only read to hand them over. Lists of ids only:
  // anything else (a damaged layout) is dropped.
  async function loadLayout(hass, key) {
    let saved;
    try {
      saved = (await hass.callWS({ type: "frontend/get_user_data", key }))?.value;
    } catch (err) { /* fall through */ }
    if (!saved) {
      try { saved = JSON.parse(localStorage.getItem(key)); } catch (err) { /* none */ }
    }
    const ids = (list) => (Array.isArray(list) ? list.filter((id) => typeof id === "string") : []);
    const scenes = {};
    if (saved?.scenes && typeof saved.scenes === "object") {
      for (const [block, s] of Object.entries(saved.scenes)) scenes[block] = { order: ids(s?.order), hidden: ids(s?.hidden) };
    }
    const layout = { order: ids(saved?.order), collapsed: ids(saved?.collapsed), open: ids(saved?.open) };
    return Object.keys(scenes).length ? { ...layout, scenes } : layout;
  }

  function saveLayout(hass, key, layout) {
    try { localStorage.setItem(key, JSON.stringify(layout)); } catch (err) { /* private mode */ }
    hass.callWS({ type: "frontend/set_user_data", key, value: layout }).catch(() => {});
  }

  const BLOCKS_CSS = `
    :host { display: block; }
    .blocks { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(320px, 100%), 1fr)); gap: 16px; align-items: start; }
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
      this._layout = this._layout || { order: [], collapsed: [], open: [] };
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
      this._layout = await loadLayout(this._hass, this._layoutKey);
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
      el.dataset.id = id;
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

  // ---- Drag to reorder -----------------------------------------------------

  // How long a finger holds still before it picks up what's under it, so a
  // swipe still scrolls the page.
  const PICK_UP_MS = 250;

  const under = (el, x, y) => {
    const r = el.getBoundingClientRect();
    return x >= r.left && x < r.right && y >= r.top && y < r.bottom;
  };

  // Drag to reorder in `list`, by mouse, pen or finger: while `active()`, the
  // elements `items()` gives (in their order on the page) can be dragged
  // among themselves, taking each one's place as they pass over it, or moved
  // with the arrow keys when focused. Then `moved(ids)` gets their new order,
  // by `data-id`. Buttons inside them stay buttons. Call `sync()` on what it
  // returns whenever `active()` may have changed.
  function sortable(list, items, active, moved) {
    let drag = null;
    const ids = () => items().map((el) => el.dataset.id);
    // Keep the dragged item under the pointer, wherever the layout has put it.
    const follow = () => {
      const { el, grab, x, y } = drag;
      el.style.transform = "";
      const at = el.getBoundingClientRect();
      el.style.transform = `translate(${x - grab.x - at.left}px, ${y - grab.y - at.top}px)`;
    };
    const pickUp = () => {
      clearTimeout(drag.timer);
      drag.timer = null;
      drag.dragging = true;
      drag.el.classList.add("dragging");
      try { list.setPointerCapture(drag.pointer); } catch (err) { /* already up */ }
      follow();
    };
    const drop = (ev) => {
      if (!drag || ev.pointerId !== drag.pointer) return;
      const { el, timer, dragging } = drag;
      drag = null;
      clearTimeout(timer);
      el.classList.remove("dragging");
      el.style.transform = "";
      if (dragging) moved(ids());
    };
    list.addEventListener("pointerdown", (ev) => {
      const el = active() && ev.button === 0 && !ev.target.closest("button") && items().find((item) => item.contains(ev.target));
      if (!el) return;
      const at = el.getBoundingClientRect();
      drag = {
        el, pointer: ev.pointerId, x: ev.clientX, y: ev.clientY, from: [ev.clientX, ev.clientY],
        grab: { x: ev.clientX - at.left, y: ev.clientY - at.top }, dragging: false,
        timer: ev.pointerType === "touch" ? setTimeout(pickUp, PICK_UP_MS) : null,
      };
    });
    list.addEventListener("pointermove", (ev) => {
      if (!drag || ev.pointerId !== drag.pointer) return;
      drag.x = ev.clientX;
      drag.y = ev.clientY;
      if (!drag.dragging) {
        if (Math.hypot(drag.x - drag.from[0], drag.y - drag.from[1]) < 6) return;
        // A finger moving before it's picked anything up is scrolling.
        if (drag.timer) return drop(ev);
        pickUp();
      }
      const all = items();
      const over = all.find((item) => item !== drag.el && under(item, drag.x, drag.y));
      if (over) over[all.indexOf(over) > all.indexOf(drag.el) ? "after" : "before"](drag.el);
      follow();
    });
    list.addEventListener("pointerup", drop);
    list.addEventListener("pointercancel", drop);
    list.addEventListener("contextmenu", (ev) => { if (active()) ev.preventDefault(); });
    list.addEventListener("keydown", (ev) => {
      const step = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[ev.key];
      const all = items();
      const from = all.indexOf(ev.target);
      if (!active() || !step || from < 0) return;
      ev.preventDefault();
      const to = all[from + step];
      if (!to) return;
      to[step > 0 ? "after" : "before"](ev.target);
      moved(ids());
      ev.target.focus();
    });
    // Once something's picked up, the finger moves it instead of the page.
    // A touch listener that can do that makes the browser wait for the
    // page's script before every scroll that starts over it, so it's only
    // there while dragging's on.
    const hold = (ev) => { if (drag?.dragging) ev.preventDefault(); };
    let holding = false;
    return {
      sync() {
        if (active() === holding) return;
        holding = active();
        if (holding) list.addEventListener("touchmove", hold, { passive: false });
        else list.removeEventListener("touchmove", hold);
      },
    };
  }

  // How something being dragged looks, and what's draggable while it can be.
  const DRAG_CSS = `
    .dragging { position: relative; z-index: 2; cursor: grabbing; }
    .dragging > :not(button) { scale: 1.03; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3); border-radius: 12px; }
    .draggable { cursor: grab; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; }
    .hint { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); }`;

  // ---- Shared settings -----------------------------------------------------

  // What every dashboard shares, kept by Hue-Lite: each room's and light's
  // scenes (`layouts`, by key) and each dimmer's settings (`dimmers`, by
  // device id), with a dimmer's scenes until they're set (`dimmer_default`).
  // One subscription per page tells every card that's watching (through its
  // `_sharedChanged`) whenever they change, from any dashboard.
  const shared = { data: null, cards: new Set(), subscribed: false };

  function watchShared(hass, card) {
    shared.cards.add(card);
    if (shared.data) card._sharedChanged();
    if (shared.subscribed) return;
    shared.subscribed = true;
    hass.connection.subscribeMessage((data) => {
      shared.data = data;
      for (const c of shared.cards) c._sharedChanged();
    }, { type: `${DOMAIN}/subscribe` }).catch(() => { shared.subscribed = false; });
  }

  // Change a shared setting here straight away, then in Hue-Lite (which
  // tells every dashboard, this one included).
  function changeShared(hass, { type, ...message }, apply) {
    if (shared.data) {
      apply(shared.data);
      for (const c of shared.cards) c._sharedChanged();
    }
    hass.callWS({ type: `${DOMAIN}/${type}`, ...message }).catch(() => {});
  }

  function setScenes(hass, key, scenes) {
    changeShared(hass, { type: "set_scenes", key, scenes }, (data) => {
      if (scenes) data.layouts[key] = scenes;
      else delete data.layouts[key];
    });
  }

  function setDimmer(hass, deviceId, changes) {
    changeShared(hass, { type: "set_dimmer", device_id: deviceId, ...changes }, (data) => {
      const settings = { ...data.dimmers[deviceId], ...changes };
      for (const key of Object.keys(settings)) if (!settings[key] || settings[key].length === 0) delete settings[key];
      if (Object.keys(settings).length) data.dimmers[deviceId] = settings;
      else delete data.dimmers[deviceId];
    });
  }

  // ---- Scene sets ----------------------------------------------------------

  // A set's scenes as saved ({order, hidden}), completed: in the saved order
  // then any it hasn't seen yet (a new scene) in the default one, or
  // `fallback` when nothing's saved.
  function sceneOrder(all, saved, fallback) {
    const s = saved || fallback || { order: [], hidden: [] };
    return {
      order: [...s.order.filter((id) => all.includes(id)), ...all.filter((id) => !s.order.includes(id))],
      hidden: s.hidden.filter((id) => all.includes(id)),
    };
  }

  const SCENES_CSS = `
    button { border: none; background: none; color: var(--secondary-text-color); font: inherit; font-size: 13px; cursor: pointer;
             display: inline-flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 18px; --mdc-icon-size: 20px; }
    button:hover { background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.06); }
    button.active { color: var(--primary-color); background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
    .scenes { display: grid; grid-template-columns: repeat(12, 1fr); gap: 8px; }
    .cell { position: relative; grid-column: span 4; height: 88px; }
    .cell > hue-lite-scene-tile { display: block; height: 100%; }
    .scenes:not(.editing) > .cell.off { display: none; }
    .scenes.editing > .cell.off > hue-lite-scene-tile { opacity: 0.35; }
    .eye { display: none; position: absolute; top: 4px; left: 4px; width: 30px; height: 30px; padding: 0; justify-content: center;
           --mdc-icon-size: 18px; color: var(--primary-text-color); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
           background: var(--ha-card-background, var(--card-background-color, #fff)); }
    .eye:hover { background: var(--ha-card-background, var(--card-background-color, #fff)); }
    .scenes.editing > .cell > .eye { display: inline-flex; }
    .edit-row { grid-column: span 12; display: flex; align-items: center; justify-content: flex-end; gap: 4px; }
    .edit-row .hint { flex: 1; padding-left: 4px; }
    .scenes:not(.editing) > .edit-row :is(.hint, .reset), .edit-row[hidden] { display: none; }`;

  // A set of scene tiles (each a third of the grid's width), with a row
  // under them: while it's being edited the tiles drag into another order,
  // their eye shows or hides them, and Reset goes back to the default.
  // `make(config)` makes a tile card (so its holder hands it hass);
  // `save(scenes)` keeps a new {order, hidden}, or null for the default;
  // `edit()`, if given, starts or stops editing from an Edit scenes button.
  class SceneSet {
    constructor(make, configs, { save, edit, hint, resetTitle }) {
      this.editing = false;
      this.grid = document.createElement("div");
      this.grid.className = "scenes";
      this.tiles = configs.map((config) => {
        const cell = document.createElement("div");
        const card = make(config);
        cell.className = "cell";
        cell.dataset.id = card.scene.id;
        cell.innerHTML = `<button class="eye"><ha-icon></ha-icon></button>`;
        cell.prepend(card);
        const eye = cell.querySelector(".eye");
        onActivate(eye, () => {
          const { order, hidden } = this.scenes;
          const id = card.scene.id;
          save({ order, hidden: hidden.includes(id) ? hidden.filter((s) => s !== id) : [...hidden, id] });
          eye.focus();
        });
        this.grid.appendChild(cell);
        return { id: card.scene.id, cell, card };
      });
      this.ids = this.tiles.map((t) => t.id);
      this.scenes = sceneOrder(this.ids);
      this.grid.insertAdjacentHTML("beforeend", `
        <div class="edit-row">
          <span class="hint"></span>
          <button class="reset"><ha-icon icon="mdi:restore"></ha-icon><span>Reset</span></button>
          ${edit ? `<button class="edit" title="Put the scenes in another order or hide some"><ha-icon></ha-icon><span></span></button>` : ""}
        </div>`);
      this.grid.querySelector(".hint").textContent = hint || "Drag the scenes into the order you want; the eye shows or hides one.";
      const reset = this.grid.querySelector(".reset");
      reset.title = resetTitle || "Back to the usual order, with every scene shown";
      onActivate(reset, () => save(null));
      if (edit) onActivate(this.grid.querySelector(".edit"), edit);
      this.sorter = sortable(this.grid, () => [...this.grid.querySelectorAll(":scope > .cell")], () => this.editing, (ids) => {
        save({ order: ids, hidden: this.scenes.hidden });
      });
    }

    // Shows `scenes` (completed, as sceneOrder makes them), editing or not.
    // Tiles are only moved when they're out of order, so what has the
    // keyboard's focus keeps it.
    arrange(scenes, editing) {
      this.scenes = scenes;
      this.editing = editing;
      const cells = new Map(this.tiles.map((t) => [t.id, t.cell]));
      const row = this.grid.querySelector(".edit-row");
      const shown = [...this.grid.querySelectorAll(":scope > .cell")].map((c) => c.dataset.id);
      if (shown.join() !== scenes.order.join()) for (const id of scenes.order) row.before(cells.get(id));
      for (const { id, cell, card } of this.tiles) {
        const off = scenes.hidden.includes(id);
        cell.classList.toggle("off", off);
        cell.classList.toggle("draggable", editing);
        // While editing, the cell takes the taps and the keyboard, not the tile.
        card.inert = editing;
        if (editing) cell.tabIndex = 0;
        else cell.removeAttribute("tabindex");
        const eye = cell.querySelector(".eye");
        eye.title = `${off ? "Show" : "Hide"} ${card.scene.name}`;
        eye.setAttribute("aria-label", `Show ${card.scene.name}`);
        eye.setAttribute("aria-pressed", String(!off));
        eye.querySelector("ha-icon").setAttribute("icon", off ? "mdi:eye-off-outline" : "mdi:eye-outline");
      }
      this.grid.classList.toggle("editing", editing);
      const edit = this.grid.querySelector(".edit");
      // With nothing in it (a set that can't be edited, not being edited), no row.
      row.hidden = !edit && !editing;
      if (edit) {
        edit.classList.toggle("active", editing);
        edit.querySelector("span").textContent = editing ? "Done" : "Edit scenes";
        edit.querySelector("ha-icon").setAttribute("icon", editing ? "mdi:check" : "mdi:pencil-outline");
      }
      this.sorter.sync();
    }
  }

  // ---- Rooms card ----------------------------------------------------------

  // Every room (and label group) as a block headed by a light bar for all its
  // lights (`head`): its icon switches them on or off, the slider dims them,
  // Auto puts them on Auto, and tapping the rest of it shows or hides the
  // room's scenes (`scenes`) and, under them, its lights (`lights`: each
  // {id, bar, scenes}), each a bar opening onto that light's own scenes.
  // Reorder shows just the rooms' bars, to drag into another order; new rooms
  // join at the end. With `toolbar: false` there's no Rooms heading,
  // Collapse all or Reorder (the Whole home block uses that, with a
  // `layout_key` of its own). Like Hue, each room has its own scenes, and so
  // does each light: Edit scenes under them lets you drag the tiles into
  // another order and hide some with their eye, saved with the layout. A
  // light has its room's until you edit its own.
  class HueLiteRooms extends CollapsibleBlocks {
    static layoutKey = "hue_lite_rooms";

    _validate(config) {
      if (!Array.isArray(config.rooms)) throw new Error("rooms is required");
      this._reordering = false;
      this._editing = null;
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
          ${DRAG_CSS}
          .toolbar { display: flex; align-items: center; gap: 4px; padding: 0 4px 8px; }
          .toolbar h2 { flex: 1; margin: 0; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
          .toolbar .hint { display: none; flex: 1; }
          .reordering .toolbar :is(h2, .hint) { display: block; flex: none; }
          .reordering .toolbar .hint { flex: 1; padding-left: 8px; }
          .reordering .fold { display: none; }
          ${SCENES_CSS}
          .head > hue-lite-light-bar, .light > hue-lite-light-bar { display: block; }
          .reordering .body { display: none; }
          .body { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
          /* The room's lights sit under it, set in with a rule down their side. */
          .lights { display: flex; flex-direction: column; gap: 8px; margin: 4px 0 0 6px; padding-left: 10px;
                    border-left: 2px solid var(--divider-color, rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12)); }
          .lights-title { font-size: 13px; font-weight: 500; line-height: 20px; color: var(--secondary-text-color); padding: 0 4px; }
          .light > .scenes { margin-top: 8px; }
          .light.collapsed > .scenes { display: none; }
        </style>
        <div class="rooms">
        ${this._config.toolbar === false ? "" : `
        <div class="toolbar">
          <h2>Rooms</h2>
          <span class="hint">Drag the rooms into the order you want</span>
          <button class="fold" title="Collapse or expand every room"><ha-icon icon="mdi:unfold-less-horizontal"></ha-icon><span>Collapse all</span></button>
          <button class="reorder" title="Drag the rooms into another order"><ha-icon icon="mdi:swap-vertical"></ha-icon><span>Reorder</span></button>
        </div>`}
        <div class="blocks"></div>
        </div>`;

      const list = this._root.querySelector(".blocks");
      this._sorter = sortable(list, () => [...list.children], () => this._reordering, (ids) => {
        this._layout.order = ids;
        this._save();
        this._arrange();
      });
      // The sets of scene tiles, each with its own Edit scenes (by key: a
      // room's id, or room id|light for one of its lights), and the rooms'
      // lights (by the same key), whose scenes are built when first opened.
      this._sets = new Map();
      this._lights = new Map();
      for (const r of this._config.rooms) {
        const head = document.createElement("div");
        head.className = "head";
        const bar = this._card(r.head);
        bar.addEventListener("hue-lite-expand", () => this._toggleCollapsed(r.id));
        head.appendChild(bar);
        const el = this._block(r.id, head);
        const body = el.querySelector(".body");
        if (r.scenes?.length) this._sceneSet(r.id, null, r.scenes, body);
        // Its lights, under it: each a bar opening onto that light's own scenes.
        if (r.lights?.length) {
          const lights = document.createElement("div");
          lights.className = "lights";
          lights.innerHTML = `<div class="lights-title"></div>`;
          lights.firstChild.textContent = `Lights in ${r.name}`;
          for (const l of r.lights) {
            const key = `${r.id}|${l.id}`;
            const light = document.createElement("div");
            light.className = "light";
            const lightBar = this._card(l.bar);
            lightBar.addEventListener("hue-lite-expand", () => this._toggleLight(key));
            light.appendChild(lightBar);
            lights.appendChild(light);
            this._lights.set(key, { key, room: r.id, el: light, bar: lightBar, scenes: l.scenes });
          }
          body.appendChild(lights);
        }
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
        if (this._reordering) this._editing = null;
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

    // The scene tiles of a room (key: its id) or of one of its lights (key:
    // room id|light, `room` its room), added to `container`.
    _sceneSet(key, room, configs, container) {
      const set = new SceneSet((config) => this._card(config), configs, {
        save: (scenes) => setScenes(this._hass, key, scenes),
        edit: () => {
          this._editing = this._editing === key ? null : key;
          this._arrange();
        },
        resetTitle: room ? "Back to the room's scenes" : undefined,
      });
      Object.assign(set, { key, room });
      container.appendChild(set.grid);
      this._sets.set(key, set);
    }

    connectedCallback() {
      if (this._hass) watchShared(this._hass, this);
    }

    disconnectedCallback() {
      shared.cards.delete(this);
    }

    set hass(hass) {
      if (!this._hass) watchShared(hass, this);
      super.hass = hass;
    }

    // Everyone's scenes changed. The first time, scenes this user arranged
    // before they were shared are handed to Hue-Lite, unless there are some
    // there already.
    _sharedChanged() {
      if (this._unshared && shared.data) {
        if (!Object.keys(shared.data.layouts).length) {
          for (const [key, scenes] of Object.entries(this._unshared)) setScenes(this._hass, key, scenes);
        }
        this._unshared = null;
        this._save();
      }
      this._arrange();
    }

    async _load() {
      await super._load();
      if (this._layout.scenes) this._unshared = this._layout.scenes;
      delete this._layout.scenes;
      if (this._unshared && shared.data) this._sharedChanged();
    }

    _showOpen(el, open) {
      el.querySelector(".head > hue-lite-light-bar").expanded = open;
    }

    // Show or hide a light's scenes (by its key, block id|light).
    _toggleLight(key) {
      const open = this._layout.open;
      this._layout.open = open.includes(key) ? open.filter((x) => x !== key) : [...open, key];
      this._save();
      this._arrange();
    }

    // A room's or light's scenes, shared by everyone; a light that's never
    // been edited has its room's.
    _scenesOf(key) {
      const set = this._sets.get(key);
      return sceneOrder(set.ids, shared.data?.layouts[key], set.room ? this._scenesOf(set.room) : undefined);
    }

    // Puts the rooms in order and their scenes in each room's order (hidden
    // ones left out, or dimmed while editing), and applies the collapsed,
    // reorder and editing states. Rooms are only moved when they're out of
    // order, so what has the keyboard's focus keeps it.
    _arrange() {
      const list = this._root.querySelector(".blocks");
      const order = this._orderedIds();
      const ids = (els) => els.map((x) => x.dataset.id).join();
      if (ids([...list.children]) !== order.join()) for (const id of order) list.appendChild(this._blocks.get(id));
      // While dragging's on, what's being dragged takes the taps and the
      // keyboard's focus, not the bars and tiles in it.
      for (const el of list.children) {
        el.classList.toggle("draggable", this._reordering);
        el.querySelector(".head").inert = this._reordering;
        if (this._reordering) el.tabIndex = 0;
        else el.removeAttribute("tabindex");
      }
      this._sorter.sync();
      // An open light shows its own scenes, built the first time it's opened.
      for (const light of this._lights.values()) {
        const open = this._layout.open.includes(light.key);
        if (open && !this._sets.has(light.key)) this._sceneSet(light.key, light.room, light.scenes, light.el);
        light.el.classList.toggle("collapsed", !open);
        light.bar.expanded = open;
      }
      for (const set of this._sets.values()) set.arrange(this._scenesOf(set.key), this._editing === set.key);
      this._root.querySelector(".rooms").classList.toggle("reordering", this._reordering);
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

  // Wake up, Evening and Coming & Going as blocks holding Home Assistant's own
  // cards (entities, tile, heading). The summary goes on its own line under
  // the title, so it isn't cut off on a phone. Tapping the "Routines"
  // heading folds the whole card away. `footer` cards (the Timings & fades
  // link) come after the blocks.
  //   {routines: [{id, name, icon, cards}], footer, entities}
  // `entities` are Hue-Lite's entity ids, for the summaries.
  const isOn = (hass, id) => hass.states[id]?.state === "on";
  const clock = (hass, id) => (hass.states[id]?.state || "").slice(0, 5);
  const SUMMARIES = {
    wake_up: (hass, e) => {
      const days = (toggle, time, label) => `${label} ${isOn(hass, toggle) ? clock(hass, time) : "off"}`;
      return `${days(e.wake_up_weekdays, e.wake_up_weekday, "Mon–Fri")} · ${days(e.wake_up_weekends, e.wake_up_weekend, "Sat & Sun")}`;
    },
    evening: (hass, e) => {
      const nights = (toggle, time, label) => `${label} ${isOn(hass, toggle) ? `till ${clock(hass, time)}` : "off"}`;
      return `${nights(e.evening_weeknights, e.bedtime_weeknight, "Sun–Thu")} · ${nights(e.evening_weekends, e.bedtime_weekend, "Fri & Sat")}`;
    },
    coming_going: (hass, e) => {
      const arrive = !isOn(hass, e.coming_home_enabled) ? "nothing" : isOn(hass, e.coming_home_only_dark) ? "lights on after dark" : "lights on";
      return `Coming: ${arrive} · Going: ${isOn(hass, e.leaving_home_enabled) ? "lights off" : "nothing"}`;
    },
  };
  // Home Assistant's own cards come from its card helpers, loaded once.
  let cardHelpers;
  const loadCardHelpers = () => (cardHelpers = cardHelpers || window.loadCardHelpers());
  // The whole card's folded state, kept with the blocks' under this id.
  const ALL_ROUTINES = "all";

  // Its blocks come from `_blocksConfig()` ({id, name, icon}), filled by
  // `_fillBody` and summed up by `_summary`; the Dimmers card is one too.
  class HueLiteRoutines extends CollapsibleBlocks {
    static layoutKey = "hue_lite_routines";
    static title = "Routines";

    _validate(config) {
      if (!Array.isArray(config.routines)) throw new Error("routines is required");
    }

    _blocksConfig() {
      return this._config.routines;
    }

    _fillBody(render, body, r) {
      this._fill(render, body, r.cards);
    }

    _summary(id) {
      return SUMMARIES[id]?.(this._hass, this._config.entities || {}) || "";
    }

    _css() {
      return "";
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
          .card-head { display: inline-flex; align-items: center; gap: 2px; min-height: 32px; padding: 0 4px; margin-bottom: 8px;
                       cursor: pointer; user-select: none; -webkit-tap-highlight-color: transparent; border-radius: 8px; }
          h2 { margin: 0; font-size: 16px; font-weight: 500; color: var(--primary-text-color); }
          .card-head .chev { --mdc-icon-size: 20px; }
          .folded .card-head .chev { transform: rotate(-90deg); }
          .folded :is(.blocks, .footer) { display: none; }
          .footer:not(:empty) { margin-top: 8px; }
          .head { display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 0 4px; cursor: pointer; user-select: none;
                  color: var(--primary-text-color); -webkit-tap-highlight-color: transparent; border-radius: 8px; }
          .block-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); flex: none; }
          .text { flex: 1; min-width: 0; display: flex; flex-direction: column; padding: 4px 0; }
          .title { font-size: 16px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .summary { font-size: 13px; color: var(--secondary-text-color); }
          .chev { --mdc-icon-size: 22px; color: var(--secondary-text-color); transition: transform 180ms; flex: none; }
          .collapsed .chev { transform: rotate(-90deg); }
          .body { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
          ${this._css()}
        </style>
        <div class="card">
          <div class="card-head" role="button" tabindex="0">
            <h2></h2><ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
          </div>
          <div class="blocks"></div>
          <div class="footer"></div>
        </div>`;
      const title = this.constructor.title;
      this._root.querySelector("h2").textContent = title;
      this._root.querySelector(".card-head").title = `Show or hide the ${title.toLowerCase()}`;
      onActivate(this._root.querySelector(".card-head"), () => this._toggleCollapsed(ALL_ROUTINES));
      const list = this._root.querySelector(".blocks");
      const render = (this._rendering = {});
      for (const r of this._blocksConfig()) {
        const el = this._block(r.id, this._heading(r));
        this._fillBody(render, el.querySelector(".body"), r);
        list.appendChild(el);
      }
      this._fill(render, this._root.querySelector(".footer"), this._config.footer || []);
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

    // Folds the whole card or not, then the blocks.
    _arrange() {
      const folded = this._layout.collapsed.includes(ALL_ROUTINES);
      this._root.querySelector(".card").classList.toggle("folded", folded);
      this._root.querySelector(".card-head").setAttribute("aria-expanded", String(!folded));
      super._arrange();
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
      for (const [id, el] of this._blocks) el.querySelector(".summary").textContent = this._summary(id);
    }
  }

  // ---- Dimmers card --------------------------------------------------------

  // Each Hue dimmer as a block, like the routines: the lights it controls
  // (its area's, or ones chosen as chips) and the scenes its On button steps
  // through, set like a room's (drag the tiles, the eye leaves one out).
  // Kept by Hue-Lite for everyone; only admins can change them.
  //   {dimmers: [{id, name, area_id, area_name}], lights, scenes}
  // `lights` are every light it could control, `scenes` the scene tiles.
  class HueLiteDimmers extends HueLiteRoutines {
    static layoutKey = "hue_lite_dimmers";
    static title = "Dimmers";

    _validate(config) {
      if (!Array.isArray(config.dimmers)) throw new Error("dimmers is required");
      this._sets = new Map();
    }

    _blocksConfig() {
      return this._config.dimmers.map((d) => ({ ...d, icon: "mdi:remote" }));
    }

    get _admin() {
      return Boolean(this._hass?.user?.is_admin);
    }

    _settings(id) {
      return shared.data?.dimmers[id] || {};
    }

    // Its lights as chips (the first one for its area), then its scenes.
    _fillBody(render, body, d) {
      body.innerHTML = `
        <div class="label">Controls</div>
        <div class="chips"><button class="chip area"><ha-icon icon="mdi:check"></ha-icon><span></span></button></div>
        <div class="label">On steps through</div>`;
      body.querySelector(".area span").textContent = d.area_name ? `Its area · ${d.area_name}` : "Its area (none)";
      onActivate(body.querySelector(".area"), () => setDimmer(this._hass, d.id, { lights: null }));
      const chips = body.querySelector(".chips");
      for (const light of this._config.lights) {
        const chip = document.createElement("button");
        chip.className = "chip";
        chip.dataset.id = light;
        chip.innerHTML = `<ha-icon icon="mdi:check"></ha-icon><span></span>`;
        onActivate(chip, () => {
          const chosen = this._settings(d.id).lights || [];
          const lights = chosen.includes(light) ? chosen.filter((l) => l !== light) : [...chosen, light];
          setDimmer(this._hass, d.id, { lights: lights.length ? lights : null });
        });
        chips.appendChild(chip);
      }
      const set = new SceneSet((config) => this._card(config), this._config.scenes, {
        save: (scenes) => setDimmer(this._hass, d.id, { scenes }),
        hint: "Drag the scenes into the order On steps through; the eye leaves one out.",
        resetTitle: "Back to Natural light and the presets",
      });
      body.appendChild(set.grid);
      this._sets.set(d.id, set);
    }

    // One of the Hue-Lite cards inside (its scene tiles), handed hass along with the rest.
    _card(config) {
      const card = document.createElement(config.type.replace(/^custom:/, ""));
      card.setConfig(config);
      if (this._hass) card.hass = this._hass;
      this._children.push(card);
      return card;
    }

    _summary(id) {
      const d = this._config.dimmers.find((x) => x.id === id);
      const lights = this._settings(id).lights;
      const set = this._sets.get(id);
      const scenes = set ? set.scenes.order.filter((s) => !set.scenes.hidden.includes(s)).length : 0;
      const controls = lights ? `${lights.length} light${lights.length === 1 ? "" : "s"}` : d?.area_name || "No area";
      return `${controls} · ${scenes} scene${scenes === 1 ? "" : "s"}`;
    }

    connectedCallback() {
      if (this._hass) watchShared(this._hass, this);
    }

    disconnectedCallback() {
      shared.cards.delete(this);
    }

    set hass(hass) {
      const first = !this._hass;
      super.hass = hass;
      if (first) {
        watchShared(hass, this);
        this._arrange();
      }
    }

    _sharedChanged() {
      this._arrange();
    }

    // Shows each dimmer's lights and scenes as set (editable for admins).
    _arrange() {
      for (const [id, el] of this._blocks) {
        const settings = this._settings(id);
        const chosen = settings.lights || [];
        el.querySelector(".chip.area").classList.toggle("on", !chosen.length);
        for (const chip of el.querySelectorAll(".chip[data-id]")) {
          chip.classList.toggle("on", chosen.includes(chip.dataset.id));
          chip.querySelector("span").textContent = this._hass?.states[chip.dataset.id]?.attributes.friendly_name || chip.dataset.id;
        }
        for (const chip of el.querySelectorAll(".chip")) {
          chip.disabled = !this._admin;
          chip.setAttribute("aria-pressed", String(chip.classList.contains("on")));
        }
        const set = this._sets.get(id);
        if (!set) continue;
        set.arrange(sceneOrder(set.ids, settings.scenes, shared.data?.dimmer_default), this._admin);
        // They only show the scenes: a tap here mustn't set any lights.
        for (const { card } of set.tiles) card.inert = true;
      }
      super._arrange();
    }

    _css() {
      return `
        ${DRAG_CSS}
        ${SCENES_CSS}
        .label { font-size: 13px; font-weight: 500; line-height: 20px; color: var(--secondary-text-color); padding: 0 4px; }
        .chips { display: flex; flex-wrap: wrap; gap: 6px; }
        .chips .chip { border: 1px solid var(--divider-color, rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12)); padding: 5px 12px 5px 10px;
                       color: var(--primary-text-color); --mdc-icon-size: 16px; }
        .chips .chip ha-icon { display: none; }
        .chips .chip.on { color: var(--primary-color); border-color: transparent; background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
        .chips .chip.on ha-icon { display: inline-flex; }
        .chips .chip:disabled { cursor: default; }`;
    }
  }

  for (const [tag, cls] of [
    ["ll-strategy-dashboard-hue-lite", HueLiteStrategy],
    ["hue-lite-light-bar", HueLiteLightBar],
    ["hue-lite-scene-tile", HueLiteSceneTile],
    ["hue-lite-rooms", HueLiteRooms],
    ["hue-lite-routines", HueLiteRoutines],
    ["hue-lite-dimmers", HueLiteDimmers],
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
