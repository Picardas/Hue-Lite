// Hue-Lite's Lights dashboard, loaded on every page while Hue-Lite is set up
// (which is also how the dashboard gets added: see the end).
//
// A dashboard strategy (`strategy: {type: custom:hue-lite}`) that builds the
// Lights dashboard from the area and label registries every time it loads, so
// new lights, rooms and labels appear without editing anything. The
// integration tells it which labels drive the routines and its own entity ids
// (websocket `hue_lite/config`). Also defines its cards:
//   custom:hue-lite-light-bar   a light (slide to dim) or a set of them: tap to toggle,
//                               Auto at the end, hold a single light for its colour dialog
//   custom:hue-lite-scene-tile  a scene button (Natural light, a preset, a colour scene
//                               or one of your own) that lights up while its lights are in it
//   custom:hue-lite-rooms       the rooms' lights as collapsible, reorderable blocks,
//                               each headed by a bar over its scenes
//   custom:hue-lite-routines    Wake up, Evening, Coming & Going and Away as collapsible blocks
//   custom:hue-lite-dimmers     each Hue dimmer: the lights it controls and its scenes
//   custom:hue-lite-light-card  one block of the Lights dashboard (a light, room or
//                               label group) for any dashboard, in the card picker
//   hue-lite-scene-editor       the dialog for making or changing one of your own scenes
//
// Each room's and light's scenes, your own scenes and the dimmers' settings
// are kept by Hue-Lite and shared by every dashboard (websocket
// `hue_lite/subscribe`); the rest of the layout is each person's own.
//
// Layout: Whole home (fixed at the top, a block that collapses like a room),
// then the rooms card with a block for each room with one light and each
// light of a room with several (ordered by floor unless you've reordered
// them) and one per label you create yourself, then the routines card and,
// with Hue dimmers, the dimmers card. The role labels drive the routines and
// don't get blocks of their own.

(() => {
  const DOMAIN = "hue_lite";
  const HOLD_MS = 500;
  // How long a tap shows before the bulbs report it. A bulb that doesn't
  // answer takes a few seconds to fail; after this the card shows it as it is.
  const PENDING_MS = 5000;
  // The built-in scenes' names and icons (their colours come from Hue-Lite):
  // the presets, then Natural light and the colour scenes, which change over
  // time. Your own scenes come with the shared settings.
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
    { id: "ember", name: "Ember", icon: "mdi:fire" },
    { id: "forest", name: "Forest", icon: "mdi:pine-tree" },
    { id: "moonlight", name: "Moonlight", icon: "mdi:moon-waning-crescent" },
  ];
  const BUILT_IN = [...PRESETS, ...COLOUR_SCENES];
  const COLOUR_SCENE_IDS = new Set(COLOUR_SCENES.map((s) => s.id));
  // Scene tiles to a row.
  const PER_ROW = 4;
  // The colour modes a bulb that can show colour has (others are white only).
  const COLOUR_MODES = ["hs", "xy", "rgb", "rgbw", "rgbww"];

  // Whether any of these lights can show colour; one that hasn't said (it's
  // unavailable, say) counts as if it can, as when Hue-Lite sends it a scene.
  function mayShowColour(hass, lights) {
    return lights.some((id) => {
      const modes = hass?.states[id]?.attributes.supported_color_modes;
      return !Array.isArray(modes) || modes.some((m) => COLOUR_MODES.includes(m));
    });
  }
  // Your own scenes (kept by Hue-Lite, in the shared settings) have ids like this.
  const isCustom = (id) => id.startsWith("custom_");

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

  // The built-in scenes, four per row, in their usual order as Hue-Lite gives
  // it (each room can be set to its own; see the rooms card): the presets,
  // then Natural light (coloured as it is now) and the colour scenes
  // (coloured by their lights). `target` is the action target: {area_id},
  // {label_id}, {entity_id} or {} for every light.
  function sceneTiles(setup, lights, target) {
    const tile = (extra) => ({
      type: "custom:hue-lite-scene-tile", entities: lights, target, scenes_entity: setup.entities.light_scenes, ...extra,
    });
    const order = setup.scenes || [...PRESETS, NATURAL, ...COLOUR_SCENES].map((s) => s.id);
    return order
      .filter((id) => id === NATURAL.id || BUILT_IN.some((s) => s.id === id))
      .map((id) => (id === NATURAL.id
        ? tile({ natural: true, natural_entity: setup.entities.natural_light })
        : tile({ preset: id, light: setup.presets?.[id] })));
  }

  // A block for the rooms card: a bar for its lights, with its icon (and
  // name, or the light's own), heading the block, over their scenes. `show`
  // says where a scene made from it is shown ({layouts, dimmers}): by
  // default, just here.
  function room(setup, id, name, icon, lights, target, show = { layouts: [id], dimmers: [] }) {
    return {
      id,
      show,
      head: lightBar(setup, lights, { name, icon, expandable: true, heading: true }),
      scenes: sceneTiles(setup, lights, target),
    };
  }

  // Whole home's block: every light. Its Auto button only shows on when every
  // light in the house is on Auto, not when some are.
  function wholeHome(setup, lights) {
    const block = room(setup, "home", "Whole home", "mdi:home", lights, {});
    block.head.auto_all_only = true;
    return block;
  }

  // Where a scene made in an area is shown: every block of the area (the
  // room's, and each of its lights') and its dimmers.
  function areaShow(setup, area, lights) {
    return {
      layouts: [area, ...lights.map((light) => `${area}|${light}`)],
      dimmers: (setup.dimmers || []).filter((d) => d.area_id && d.area_id === area).map((d) => d.id),
    };
  }

  // An area's blocks: one for the room when it has a single light (the room
  // is that light); with several, one per light, each with the room's icon
  // and its own name and scenes, rather than one for them all.
  function areaBlocks(setup, id, name, icon, lights, target) {
    const show = areaShow(setup, id, lights);
    if (lights.length === 1) return [room(setup, id, name, icon, lights, target, show)];
    return lights.map((light) => room(setup, `${id}|${light}`, undefined, icon, [light], { entity_id: [light] }, show));
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
        rooms: [wholeHome(setup, everything)],
        toolbar: false,
        layout_key: "hue_lite_home",
      }],
    }] : [];

    // Rooms (each light of their own when they have several) and your own
    // labels go in one collapsible, reorderable card; Whole home above stays put.
    const rooms = [];
    for (const area of sortedAreas(hass)) {
      const inArea = ids(lights.filter((x) => areaOf(hass, x) === area.area_id));
      if (inArea.length) rooms.push(...areaBlocks(setup, area.area_id, area.name, area.icon || "mdi:texture-box", inArea, { area_id: area.area_id }));
    }

    // Not "other": an area called Other would have that id too.
    const noArea = ids(lights.filter((x) => !areaOf(hass, x)));
    if (noArea.length) rooms.push(...areaBlocks(setup, "no-area", "Other", "mdi:lightbulb-question-outline", noArea, { entity_id: noArea }));

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
    }, {
      id: "away",
      name: "Away",
      icon: "mdi:shield-home-outline",
      cards: [{
        type: "entities",
        entities: [{ entity: e.mimic_presence, name: "Lights on and off in the evening while nobody's home" }],
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
      "**Colour scenes**: Golden hour, Nebula, Aurora, Ember, Forest and Moonlight spread their colours across a room's colour bulbs, each at its own brightness, and drift between them at their own pace: a step every minute for Ember, every two for Nebula and Aurora, every three for Golden hour and Forest, and every four for Moonlight.",
      "",
      "**Your own scenes**: Edit scenes under any room's scenes, then the + tile at the end, makes one: one colour (static) or up to eight, each at its own brightness, drifting at their own speed. Try on the lights shows the colours on the room's lights as you pick them. The pencil on a tile changes one of yours, or starts a copy of a built-in scene. A new scene shows in the room it was made in and on its dimmers; elsewhere Edit scenes shows it.",
      "",
      "**Auto**: the button at the end of a light's or room's bar. On Auto, a light follows wake up and the evening routine whether or not it has their labels, until you pick a scene for it or change it. Put on Auto, it matches them straight away: on with whichever is running, off if neither is. Lights a routine has show as on Auto too.",
      "",
      `**Wake up**: on the days it's on (Mon–Fri and Sat & Sun each have a switch and a time), lights labelled *${label("wake_up")}* that are off get a sunrise: from Nightlight's dim red through orange and warm white to Energize, reaching full brightness *at* your wake-up time.`,
      "",
      `**Evening routine**: on the nights it's on (Sun–Thu and Fri & Sat each have a switch and a bedtime), before sunset, lights labelled *${label("evening")}* come on dim (if someone's home), brighten to Cool bright, wind down through Relax to Rest before bed (*${label("nightlight")}* lights go to Nightlight for the last stretch) and fade out at bedtime. Picking a scene for a light, Natural light included, takes it out of the routine for the night.`,
      "",
      `**Coming & Going**: when someone gets home (optionally only after dark), lights labelled *${label("coming_home")}* that are off come on, and so do lights labelled *${label("first_home")}* if the house was empty. Evening lights join the evening routine if it's on, except in its last half hour or so before bed, so they don't go off again soon after; the rest come on in Natural light. When the last person leaves, every light can go off.`,
      "",
      `**Away**: while nobody's home in the evening (from the evening routine's start until bedtime), one of the *${label("evening")}* lights switches on or off every 10 to 30 minutes, so the house looks lived in. It stops when someone gets home and turns its lights off at bedtime.`,
      "",
      "**Dimming** a light (its slider, or a dimmer's Up and Down) keeps it in its scene at that level: Natural light keeps following the sun, a colour scene keeps drifting, until you pick a scene again. Dimming a light during its wake-up stops its sunrise there.",
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

  // One bar for a single light or a set (Whole home, a label group): tap to
  // toggle; a single light's slider dims it and holding it opens Home
  // Assistant's light dialog (colour wheel, colour temperature). A set has no
  // slider: one brightness for lights in different scenes would flatten
  // them. The icon (`icon`, or a bulb) and the slider take the
  // lights' own colour, so they follow what Auto and the scenes do to them. With
  // `scenes_entity`, an Auto button at the end puts the lights on Auto
  // (following the routines) or takes them off it; on a set it shows when only
  // some are on Auto (unless `auto_all_only`, as Whole home is), and a tap
  // sets them all.
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
          .set .text { flex: 1; }
          .auto { cursor: pointer; --mdc-icon-size: 22px; }
          .auto.some { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.1); color: var(--primary-color); }
          .auto.all { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.25); color: var(--primary-color); }
        </style>
        <ha-card class="${this._config.heading ? "heading" : ""} ${this._single ? "" : "set"}" role="button" tabindex="0">
          <div class="circle light"${expandable ? ` role="button" tabindex="0"` : ""}><ha-icon></ha-icon></div>
          <div class="text">
            <div class="name"><span></span>${expandable ? `<ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>` : ""}</div>
            <div class="state"></div>
          </div>
          ${this._single ? `<input type="range" min="1" max="100" step="1">` : ""}
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
      card.title = expandable ? `Tap to show or hide ${this._single ? "its" : "the room's"} scenes${hold}`
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

      // A single light's slider dims it (turning it on), keeping it in its
      // scene at that level; it handles its own input.
      if (slider) {
        slider.addEventListener("pointerdown", (ev) => ev.stopPropagation());
        slider.addEventListener("click", (ev) => ev.stopPropagation());
        slider.addEventListener("input", () => slider.style.setProperty("--pct", `${slider.value}%`));
        slider.addEventListener("change", () => {
          this._hass.callService(DOMAIN, "set_brightness", { brightness_pct: Number(slider.value) }, { entity_id: this._config.entities });
        });
      }
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
        : on.length ? `${on.length} of ${states.length} on` : `All ${states.length} off`;

      if (slider) {
        slider.disabled = unavailable;
        slider.setAttribute("aria-label", `Brightness of ${name.textContent}`);
        if (this._root.activeElement !== slider) {
          const shown = pending?.on === false ? 0 : on.length ? pct : 0;
          slider.value = shown || 1;
          slider.style.setProperty("--pct", `${shown}%`);
        }
      }

      if (this._el.auto) {
        const { on: onAuto, of, all } = this._auto();
        const some = onAuto > 0 && !all && !this._config.auto_all_only;
        this._el.auto.classList.toggle("all", all);
        this._el.auto.classList.toggle("some", some);
        this._el.auto.setAttribute("aria-pressed", all ? "true" : some ? "mixed" : "false");
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
  //   {preset: id, name, icon, light, ...}                               one of your own
  // Natural light's tile takes its colour and phase from `natural_entity`, as
  // it is now; the others' colours are their `light` ({rgb_colors}).
  // `scenes_entity` says which scene each light is in (attributes `scenes` and
  // `auto`); `target` is passed to the actions: {area_id}, {label_id},
  // {entity_id} or {} for all. With `preview` it only shows the scene (a
  // dimmer's): a tap does nothing, and it's never ticked.
  class HueLiteSceneTile extends HueLiteCard {
    setConfig(config) {
      this._scene = config.natural ? NATURAL : BUILT_IN.find((p) => p.id === config.preset)
        || (config.name && { id: config.preset, name: config.name, icon: config.icon || "mdi:palette" });
      if (!this._scene) throw new Error(`unknown preset ${config.preset}`);
      super.setConfig(config);
    }

    getCardSize() {
      return 2;
    }

    getGridOptions() {
      return { columns: 3, rows: 2, min_columns: 3 };
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
                    align-items: center; justify-content: center; gap: 4px; padding: 8px 4px; text-align: center;
                    cursor: pointer; user-select: none; -webkit-tap-highlight-color: transparent; overflow: hidden;
                    background: var(--tint, none), var(--card-bg); }
          .circle { background: var(--swatch); color: var(--ink); }
          .text { max-width: 100%; }
          .name, .now { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .name { font-size: 12px; font-weight: 500; line-height: 16px; color: var(--primary-text-color); }
          .now { font-size: 11px; line-height: 14px; color: var(--secondary-text-color); }
          .now:empty { display: none; }
          .check { display: none; position: absolute; top: 6px; right: 6px; --mdc-icon-size: 18px; }
          ha-card.on { background: var(--fill), var(--card-bg); }
          ha-card.on .circle { background: rgba(255, 255, 255, 0.3); }
          ha-card.on :is(.name, .now, .check) { color: var(--ink); }
          ha-card.on .check { display: block; }
        </style>
        <ha-card role="button" tabindex="0">
          <div class="circle"><ha-icon></ha-icon></div>
          <div class="text"><div class="name"></div><div class="now"></div></div>
          <ha-icon class="check" icon="mdi:check-circle"></ha-icon>
        </ha-card>`;
      // Your own scenes' names are anyone's text: never HTML.
      this._root.querySelector(".circle ha-icon").setAttribute("icon", this._scene.icon);
      this._root.querySelector(".name").textContent = this._scene.name;
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
  // falling back to this browser: the blocks' order and which are collapsed.
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
    const layout = { order: ids(saved?.order), collapsed: ids(saved?.collapsed) };
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
  // entities they're `_watching` (`_update`). One with `_sharedChanged` is
  // told whenever the shared settings change.
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
      const first = !this._hass;
      this._hass = hass;
      if (!this._loaded) this._load();
      if (first && this._sharedChanged) watchShared(hass, this);
      // The cards inside skip redrawing themselves when their own states haven't changed.
      for (const child of this._children) child.hass = hass;
      if (statesChanged(this, hass, this._watching())) this._update();
    }

    connectedCallback() {
      if (this._hass && this._sharedChanged) watchShared(this._hass, this);
    }

    disconnectedCallback() {
      shared.cards.delete(this);
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    // One of the Hue-Lite cards inside, handed hass along with the rest.
    _card(config) {
      const card = document.createElement(config.type.replace(/^custom:/, ""));
      card.setConfig(config);
      if (this._hass) card.hass = this._hass;
      this._children.push(card);
      return card;
    }

    // Stop handing hass to a card that's gone (one of your own scenes, deleted).
    _forget(card) {
      this._children = this._children.filter((c) => c !== card);
    }

    get _layoutKey() {
      return this._config.layout_key || this.constructor.layoutKey;
    }

    async _load() {
      this._loaded = true;
      this._layout = await loadLayout(this._hass, this._layoutKey);
      this._layoutLoaded = true;
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

    // Whether a block is folded away (each starts open).
    _isCollapsed(id) {
      return this._layout.collapsed.includes(id);
    }

    // Applies the collapsed state, then redraws.
    _arrange() {
      for (const [id, el] of this._blocks) {
        const collapsed = this._isCollapsed(id);
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
  // scenes (`layouts`, by key), each dimmer's settings (`dimmers`, by device
  // id), with a dimmer's scenes until they're set (`dimmer_default`), and
  // your own scenes (`custom`, by id, with the colours bulbs report for them).
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
  // tells every dashboard, this one included). If Hue-Lite turns it down
  // (only admins set dimmers, say), it's put back and a message says why.
  function changeShared(hass, { type, ...message }, apply) {
    // The settings this changes (replaced whenever Hue-Lite sends them), and a copy from before.
    const before = shared.data && { current: shared.data, data: structuredClone(shared.data) };
    const changed = () => { for (const c of shared.cards) c._sharedChanged(); };
    if (shared.data) {
      apply(shared.data);
      changed();
    }
    hass.callWS({ type: `${DOMAIN}/${type}`, ...message }).catch((err) => {
      // Put back as it was, unless Hue-Lite has sent the settings since
      // (which never had this change in them, and may have others').
      if (before && shared.data === before.current) {
        shared.data = before.data;
        changed();
      }
      toast(`Couldn't save that: ${err?.message || err?.code || err}`);
    });
  }

  // A message at the bottom of the screen, as Home Assistant shows its own.
  function toast(message) {
    document.querySelector("home-assistant")?.dispatchEvent(
      new CustomEvent("hass-notification", { detail: { message }, bubbles: true, composed: true }));
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
  // then any it hasn't seen yet in the default one, or `fallback` when
  // nothing's saved. Unseen ones are hidden when `hideUnseen` says so: by
  // default your own scenes (shown only where they were made), while a new
  // built-in one shows; a dimmer's cycle hides them all, as it doesn't
  // change by itself.
  function sceneOrder(all, saved, fallback, hideUnseen = isCustom) {
    const s = saved || fallback || { order: [], hidden: [] };
    const unseen = all.filter((id) => !s.order.includes(id));
    return {
      order: [...s.order.filter((id) => all.includes(id)), ...unseen],
      hidden: [...s.hidden.filter((id) => all.includes(id)), ...unseen.filter(hideUnseen)],
    };
  }

  const SCENES_CSS = `
    button { border: none; background: none; color: var(--secondary-text-color); font: inherit; font-size: 13px; cursor: pointer;
             display: inline-flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 18px; --mdc-icon-size: 20px; }
    button:hover { background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.06); }
    button.active { color: var(--primary-color); background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.12); }
    .scenes { display: grid; grid-template-columns: repeat(12, 1fr); gap: 8px; }
    .cell { position: relative; grid-column: span 3; height: 88px; }
    .cell > hue-lite-scene-tile { display: block; height: 100%; }
    .scenes:not(.editing) > .cell.off { display: none; }
    .scenes.editing > .cell.off > hue-lite-scene-tile { opacity: 0.35; }
    :is(.eye, .pen) { display: none; position: absolute; top: 3px; left: 3px; width: 26px; height: 26px; padding: 0; justify-content: center;
           --mdc-icon-size: 16px; color: var(--primary-text-color); box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
           background: var(--ha-card-background, var(--card-background-color, #fff)); }
    :is(.eye, .pen):hover { background: var(--ha-card-background, var(--card-background-color, #fff)); }
    .pen { left: auto; right: 3px; }
    .scenes.editing > .cell > :is(.eye, .pen) { display: inline-flex; }
    .add { display: none; grid-column: span 3; height: 88px; box-sizing: border-box; flex-direction: column; justify-content: center;
           border: 2px dashed var(--divider-color, rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12));
           border-radius: var(--ha-card-border-radius, 12px); --mdc-icon-size: 24px; }
    .scenes.editing > .add { display: inline-flex; }
    .scenes > .cell.gone, .expander[hidden] { display: none; }
    .scenes:not(.editing):not(.expanded) > .cell.beyond { display: none; }
    .expander { grid-column: span 3; height: 88px; box-sizing: border-box; flex-direction: column; justify-content: center; gap: 0;
                border-radius: var(--ha-card-border-radius, 12px); --mdc-icon-size: 22px; color: var(--secondary-text-color);
                background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.06); }
    .expander:hover { background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.1); }
    .expander .what { font-weight: 500; color: var(--primary-text-color); }
    .expander small { font-size: 11px; line-height: 14px; }
    .scenes.expanded > .expander { background: rgba(var(--rgb-primary-color, 3, 169, 244), 0.14); color: var(--primary-color); }
    .scenes.expanded > .expander .what { color: var(--primary-color); }
    .scenes.editing > .expander { cursor: default; border: 2px dashed var(--divider-color, rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.12)); }
    .edit-row { grid-column: span 12; display: flex; align-items: center; justify-content: flex-end; gap: 4px; }
    .edit-row .hint { flex: 1; padding-left: 4px; }
    .scenes:not(.editing) > .edit-row :is(.hint, .reset), .edit-row[hidden] { display: none; }`;

  // A set of scene tiles (four to a row), with a row under them: while it's
  // being edited the tiles drag into another order, their eye shows or hides
  // them, and Reset puts the built-in ones back as `usual()` has them
  // ({order, hidden}; by default all, in the order they came), leaving your
  // own as they were.
  // `holder` makes the tile cards (`_card`, so it hands them hass) and
  // forgets ones that go (`_forget`). `configs` are the built-in scenes'
  // tiles; your own scenes join them (`sync`) with the same lights and target.
  // `save(scenes)` keeps a new {order, hidden}, or null for the default;
  // `edit()`, if given, starts or stops editing from an Edit scenes button;
  // `editor(config)`, if given, opens the scene editor while editing: from a
  // tile's pencil (its config: one of your own to change, or a built-in one
  // to copy), or from the + tile at the end (null: a new one).
  // With `more`, it shows two rows at most: past that, the last place is a
  // More tile that opens (and closes) the rest. Which scenes are behind it
  // is just their order, so they're moved in front of it or behind it by
  // dragging; More itself stays put. Built-in colour scenes aren't drawn
  // while `noColour` (none of its lights can show colour).
  class SceneSet {
    constructor(holder, configs, { save, edit, editor, hint, resetTitle, usual, more }) {
      this.holder = holder;
      this.save = save;
      this.editor = editor;
      this.editing = false;
      this.more = Boolean(more);
      this.expanded = false;
      this.noColour = false;
      this.grid = document.createElement("div");
      this.grid.className = "scenes";
      const { preset, light, ...base } = configs.find((c) => c.preset) || {};
      this.base = base;
      // The lights its scenes are for.
      this.lights = base.entities || [];
      this.custom = null;
      this.tiles = [];
      this.grid.innerHTML = `${editor
        ? `<button class="add" title="Make a scene of your own"><ha-icon icon="mdi:plus"></ha-icon><span>New scene</span></button>` : ""}
        <button class="expander" hidden><ha-icon></ha-icon><span class="what"></span><small></small></button>`;
      if (editor) onActivate(this.grid.querySelector(".add"), () => editor(null));
      onActivate(this.grid.querySelector(".expander"), () => {
        if (this.editing) return;
        this.expanded = !this.expanded;
        this.arrange(this.scenes, this.editing);
      });
      this.grid.insertAdjacentHTML("beforeend", `
        <div class="edit-row">
          <span class="hint"></span>
          <button class="reset"><ha-icon icon="mdi:restore"></ha-icon><span>Reset</span></button>
          ${edit ? `<button class="edit" title="Put the scenes in another order or hide some"><ha-icon></ha-icon><span></span></button>` : ""}
        </div>`);
      this.grid.querySelector(".hint").textContent = hint
        || "Drag the scenes into the order you want (those after More are behind it); the eye shows or hides one, the pencil changes or copies one.";
      const reset = this.grid.querySelector(".reset");
      reset.title = resetTitle || "Back to the usual order, with every built-in scene shown; your own stay as they are";
      onActivate(reset, () => {
        const mine = this.scenes.order.filter((id) => isCustom(id) && !this.scenes.hidden.includes(id));
        const back = usual ? usual() : { order: builtIn, hidden: [] };
        save(mine.length ? { order: [...back.order, ...mine], hidden: back.hidden } : null);
      });
      if (edit) onActivate(this.grid.querySelector(".edit"), edit);
      for (const config of configs) this._add(config);
      const builtIn = this.ids;
      this.scenes = sceneOrder(this.ids);
      this.sorter = sortable(this.grid, () => [...this.grid.querySelectorAll(":scope > .cell")], () => this.editing, (ids) => {
        save({ order: ids, hidden: this.scenes.hidden });
      });
    }

    get ids() {
      return this.tiles.map((t) => t.id);
    }

    // Where tiles go: before the + tile, or the row under them.
    get _end() {
      return this.grid.querySelector(":scope > .add") || this.grid.querySelector(".edit-row");
    }

    // A tile, with its eye (and pencil, in a set with the editor; Natural
    // light has none, as it isn't made of colours).
    _add(config) {
      const cell = document.createElement("div");
      const card = this.holder._card(config);
      const tile = { id: card.scene.id, cell, card, config };
      cell.className = "cell";
      cell.dataset.id = tile.id;
      cell.innerHTML = `<button class="eye"><ha-icon></ha-icon></button>${this.editor && !config.natural
        ? `<button class="pen"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>` : ""}`;
      cell.prepend(card);
      const eye = cell.querySelector(".eye");
      onActivate(eye, () => {
        const { order, hidden } = this.scenes;
        this.save({ order, hidden: hidden.includes(tile.id) ? hidden.filter((s) => s !== tile.id) : [...hidden, tile.id] });
        eye.focus();
      });
      const pen = cell.querySelector(".pen");
      if (pen) onActivate(pen, () => this.editor(tile.config));
      this._end.before(cell);
      this.tiles.push(tile);
    }

    // Your own scenes as they are now (by id, from the shared settings): a
    // new one gets a tile, a changed one's is redrawn, a deleted one's goes.
    sync(custom = {}) {
      // Hue-Lite sends them afresh with every change to the shared settings.
      if (custom === this.custom) return;
      this.custom = custom;
      for (const tile of this.tiles.filter((t) => isCustom(t.id) && !custom[t.id])) {
        tile.cell.remove();
        this.holder._forget(tile.card);
      }
      this.tiles = this.tiles.filter((t) => !isCustom(t.id) || custom[t.id]);
      for (const [id, scene] of Object.entries(custom)) {
        const config = { ...this.base, preset: id, name: scene.name, icon: scene.icon, light: { rgb_colors: scene.rgb_colors } };
        const tile = this.tiles.find((t) => t.id === id);
        if (!tile) this._add(config);
        else if (JSON.stringify(config) !== JSON.stringify(tile.config)) {
          tile.config = config;
          tile.card.setConfig(config);
        }
      }
    }

    // Shows `scenes` (completed, as sceneOrder makes them), editing or not.
    // Tiles are only moved when they're out of order, so what has the
    // keyboard's focus keeps it.
    arrange(scenes, editing) {
      this.scenes = scenes;
      this.editing = editing;
      const cells = new Map(this.tiles.map((t) => [t.id, t.cell]));
      const row = this.grid.querySelector(".edit-row");
      const end = this._end;
      const shown = [...this.grid.querySelectorAll(":scope > .cell")].map((c) => c.dataset.id);
      if (shown.join() !== scenes.order.join()) for (const id of scenes.order) end.before(cells.get(id));
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
        const pen = cell.querySelector(".pen");
        if (pen) pen.title = isCustom(id) ? `Change ${card.scene.name}` : `Make a scene from ${card.scene.name}`;
      }
      this._placeMore(scenes, cells);
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

    // Whether any of its lights can show colour (one that hasn't said counts
    // as if it can); when none can, the built-in colour scenes go.
    colour(can) {
      if (this.noColour === !can) return;
      this.noColour = !can;
      this.arrange(this.scenes, this.editing);
    }

    // Leaves out colour scenes no light can show, and puts More after the
    // seventh scene shown when there are more than two rows of them: the
    // scenes after it are behind it until it's opened (while editing,
    // they're all shown, More marking where the second row ends).
    _placeMore(scenes, cells) {
      const gone = (id) => this.noColour && COLOUR_SCENE_IDS.has(id);
      const shown = scenes.order.filter((id) => !scenes.hidden.includes(id) && !gone(id));
      const cut = this.more && shown.length > 2 * PER_ROW ? shown[2 * PER_ROW - 2] : null;
      const last = cut ? scenes.order.indexOf(cut) : Infinity;
      scenes.order.forEach((id, i) => {
        cells.get(id).classList.toggle("gone", gone(id));
        cells.get(id).classList.toggle("beyond", i > last);
      });
      const more = this.grid.querySelector(".expander");
      more.hidden = !cut;
      this.grid.classList.toggle("expanded", Boolean(cut) && this.expanded && !this.editing);
      if (!cut) return;
      if (cells.get(cut).nextElementSibling !== more) cells.get(cut).after(more);
      const open = this.expanded && !this.editing;
      more.disabled = this.editing;
      more.setAttribute("aria-expanded", String(open));
      more.querySelector("ha-icon").setAttribute("icon", open ? "mdi:chevron-up" : "mdi:chevron-down");
      more.querySelector(".what").textContent = open ? "Less" : "More";
      more.querySelector("small").textContent = this.editing ? "the rest go here" : open ? "" : `${shown.length - (2 * PER_ROW - 1)} more`;
      more.title = this.editing ? "Scenes after More are behind it: drag one in front to show it straight away"
        : open ? "Show just the first two rows" : "Show the rest of the scenes";
    }
  }

  // ---- Rooms card ----------------------------------------------------------

  // Each block (a room with one light, each light of a room with several, a
  // label group) headed by a light bar for its lights (`head`): its icon
  // switches them on or off, the slider dims them, Auto puts them on Auto,
  // and tapping the rest of it shows or hides its scenes (`scenes`). Reorder
  // shows just the bars, to drag into another order; new blocks join after
  // the one before them. With `toolbar: false` there's no Rooms heading,
  // Collapse all or Reorder (the Whole home block uses that, with a
  // `layout_key` of its own). Like Hue, each has its own scenes: Edit scenes
  // under them lets you drag the tiles into another order and hide some
  // with their eye.
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

    // The saved order, with any blocks it doesn't know yet (a new room or
    // light) after the block before them in the usual order.
    _orderedIds() {
      const usual = this._config.rooms.map((r) => r.id);
      const order = this._layout.order.filter((id) => usual.includes(id));
      usual.forEach((id, i) => {
        if (!order.includes(id)) order.splice(i ? order.indexOf(usual[i - 1]) + 1 : 0, 0, id);
      });
      return order;
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
          .head > hue-lite-light-bar { display: block; }
          .reordering .body { display: none; }
          .body { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
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
      // Each block's scene tiles, with their own Edit scenes, by its id. A
      // home with many lights has hundreds, so each block's are only made
      // the first time it's open (`_unbuilt` until then).
      this._sets = new Map();
      this._unbuilt = new Map();
      for (const r of this._config.rooms) {
        const head = document.createElement("div");
        head.className = "head";
        const bar = this._card(r.head);
        bar.addEventListener("hue-lite-expand", () => this._toggleCollapsed(r.id));
        head.appendChild(bar);
        const el = this._block(r.id, head);
        list.appendChild(el);
        if (r.scenes?.length) this._unbuilt.set(r.id, () => this._sceneSet(r.id, r.scenes, el.querySelector(".body"), r.show));
      }

      if (this._config.toolbar === false) return;
      onActivate(this._root.querySelector(".fold"), () => {
        const ids = this._config.rooms.map((r) => r.id);
        const allCollapsed = ids.every((id) => this._isCollapsed(id));
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

    // A block's scene tiles (shared by everyone under its id), added to
    // `container`. A scene made from them is shown where `show` says.
    _sceneSet(key, configs, container, show = { layouts: [key], dimmers: [] }) {
      const set = new SceneSet(this, configs, {
        save: (scenes) => setScenes(this._hass, key, scenes),
        edit: () => {
          this._editing = this._editing === key ? null : key;
          this._arrange();
        },
        editor: (config) => openSceneEditor(this._hass, config, show, configs[0]?.entities),
        more: true,
      });
      set.key = key;
      container.appendChild(set.grid);
      this._sets.set(key, set);
    }

    // Every light in its blocks: a bulb that comes back, or reports what
    // it can do, can change whether a block's colour scenes show.
    _watching() {
      return [...new Set(this._config.rooms.flatMap((r) => r.scenes?.[0]?.entities || []))];
    }

    _update() {
      for (const set of this._sets.values()) set.colour(mayShowColour(this._hass, set.lights));
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
      // Once it's known which blocks this person has folded away, the open
      // ones get their tiles.
      if (this._layoutLoaded) {
        for (const [id, build] of this._unbuilt) {
          if (!this._isCollapsed(id)) {
            this._unbuilt.delete(id);
            build();
          }
        }
      }
      for (const set of this._sets.values()) {
        set.sync(shared.data?.custom);
        set.arrange(sceneOrder(set.ids, shared.data?.layouts[set.key]), this._editing === set.key);
      }
      this._root.querySelector(".rooms").classList.toggle("reordering", this._reordering);
      const reorder = this._root.querySelector(".reorder");
      const fold = this._root.querySelector(".fold");
      if (reorder && fold) {
        reorder.classList.toggle("active", this._reordering);
        reorder.querySelector("span").textContent = this._reordering ? "Done" : "Reorder";
        const allCollapsed = order.every((id) => this._isCollapsed(id));
        fold.querySelector("span").textContent = allCollapsed ? "Expand all" : "Collapse all";
        fold.querySelector("ha-icon").setAttribute("icon", allCollapsed ? "mdi:unfold-more-horizontal" : "mdi:unfold-less-horizontal");
      }
      super._arrange();
    }
  }

  // ---- Routines card -------------------------------------------------------

  // Wake up, Evening, Coming & Going and Away as blocks holding Home Assistant's own
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
    away: (hass, e) => (isOn(hass, e.mimic_presence) ? "Mimicking presence in the evening" : "Off"),
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
      const set = new SceneSet(this, this._config.scenes, {
        save: (scenes) => setDimmer(this._hass, d.id, { scenes }),
        editor: (config) => openSceneEditor(this._hass, config, { layouts: [], dimmers: [d.id] }),
        hint: "Drag the scenes into the order On steps through; the eye leaves one out, the pencil changes or copies one.",
        resetTitle: "Back to Natural light and the presets; your own scenes stay as they are",
        usual: () => shared.data?.dimmer_default || { order: [], hidden: [] },
      });
      body.appendChild(set.grid);
      this._sets.set(d.id, set);
    }

    _summary(id) {
      const d = this._config.dimmers.find((x) => x.id === id);
      const lights = this._settings(id).lights;
      const set = this._sets.get(id);
      const scenes = set ? set.scenes.order.filter((s) => !set.scenes.hidden.includes(s)).length : 0;
      const controls = lights ? `${lights.length} light${lights.length === 1 ? "" : "s"}` : d?.area_name || "No area";
      return `${controls} · ${scenes} scene${scenes === 1 ? "" : "s"}`;
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
        set.sync(shared.data?.custom);
        // The lights it controls: the ones chosen, or its area's.
        const area = this._config.dimmers.find((d) => d.id === id)?.area_id;
        const controls = chosen.length ? chosen
          : this._config.lights.filter((l) => area && this._hass?.entities?.[l] && areaOf(this._hass, this._hass.entities[l]) === area);
        set.noColour = !mayShowColour(this._hass, controls);
        set.arrange(sceneOrder(set.ids, settings.scenes, shared.data?.dimmer_default, () => true), this._admin);
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

  // ---- Scene editor --------------------------------------------------------

  const hex = (rgb) => `#${rgb.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
  // The drift slider's steps, in minutes.
  const DRIFT_STEPS = [1, 2, 3, 4, 5, 10, 15, 30, 60];
  const nearestStep = (minutes) =>
    DRIFT_STEPS.reduce((best, m, i) => (Math.abs(m - minutes) < Math.abs(DRIFT_STEPS[best] - minutes) ? i : best), 0);
  const rgbOf = (value) => [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));

  // Opens the scene editor for a tile's config: one of your own scenes to
  // change, a copy of a built-in one, or (null) a new one, which is shown
  // where `show` says ({layouts, dimmers}: where it was made). Opened from
  // a block's scenes, its `lights` can try the colours (not a dimmer's).
  function openSceneEditor(hass, config, show, lights = null) {
    const id = config && isCustom(config.preset) ? config.preset : null;
    const mine = id && shared.data?.custom?.[id];
    const builtIn = config && BUILT_IN.find((p) => p.id === config.preset);
    let scene = { name: "", icon: "mdi:palette", drift_minutes: 3, colours: [{ rgb: [255, 170, 90], brightness: 70 }] };
    if (mine) {
      scene = { ...mine, colours: mine.rgb_colors.map((rgb, i) => ({ rgb, brightness: mine.colours[i][2] })) };
    } else if (builtIn) {
      const { rgb_colors: rgbs = [], brightness = [], drift_minutes: drift = 3 } = config.light || {};
      scene = {
        name: `Copy of ${builtIn.name}`.slice(0, 30),
        icon: builtIn.icon,
        drift_minutes: drift,
        colours: rgbs.map((rgb, i) => ({ rgb, brightness: brightness[i] || 70 })),
      };
    }
    if (config && !mine && !builtIn) return;
    const editor = document.createElement("hue-lite-scene-editor");
    document.body.appendChild(editor);
    editor.open(hass, id, scene, id ? null : show, lights);
  }

  // A dialog for one of your own scenes: its name and icon, then one to
  // eight colours, each with its brightness (one colour: a static scene;
  // more: a dynamic one, drifting between them at its own speed), with a
  // swatch of how its tile looks. With `lights`, Try on the lights shows
  // the colours on them as they're picked (taking them out of their
  // scene), and Save then puts them in the scene. Save sends it to
  // Hue-Lite, which tells every dashboard; Delete (tapped twice, to be
  // sure) deletes it.
  class HueLiteSceneEditor extends HTMLElement {
    open(hass, id, scene, show, lights) {
      this._hass = hass;
      this._id = id;
      this._show = show;
      this._lights = lights?.length ? lights : null;
      this._trying = false;
      this._value = { name: scene.name, icon: scene.icon };
      this._drift = scene.drift_minutes || 3;
      this._colours = scene.colours.slice(0, 8).map((c) => ({ rgb: [...c.rgb], brightness: c.brightness }));
      if (!this._colours.length) this._colours.push({ rgb: [255, 170, 90], brightness: 70 });
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          ${FOCUS_CSS}
          dialog { border: none; border-radius: var(--ha-dialog-border-radius, 24px); padding: 24px; box-sizing: border-box;
                   width: min(440px, calc(100vw - 32px)); max-height: calc(100vh - 32px);
                   background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color);
                   font-family: var(--paper-font-body1_-_font-family, inherit); }
          dialog::backdrop { background: rgba(0, 0, 0, 0.5); }
          header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
          h2 { flex: 1; margin: 0; font-size: 20px; font-weight: 500; }
          .swatch { width: 40px; height: 40px; border-radius: 50%; background: var(--swatch); flex: none; }
          .label { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); margin: 16px 0 4px; }
          .row { display: flex; align-items: center; gap: 8px; height: 40px; }
          input[type="color"] { width: 40px; height: 32px; padding: 0; border: none; background: none; cursor: pointer; flex: none; }
          input[type="range"] { flex: 1; min-width: 0; accent-color: var(--primary-color); }
          .value { width: 52px; font-size: 13px; color: var(--secondary-text-color); text-align: right; flex: none; }
          button { border: none; background: none; font: inherit; font-size: 14px; cursor: pointer; color: var(--primary-color);
                   display: inline-flex; align-items: center; gap: 4px; padding: 8px 12px; border-radius: 18px; --mdc-icon-size: 20px; }
          button:hover { background: rgba(var(--rgb-primary-text-color, 33, 33, 33), 0.06); }
          button[hidden], .drift[hidden], .try[hidden] { display: none; }
          button:disabled { opacity: 0.5; cursor: default; }
          .try { display: flex; align-items: center; gap: 8px; margin-top: 16px; font-size: 14px; cursor: pointer; }
          .try input { width: 18px; height: 18px; margin: 0; accent-color: var(--primary-color); }
          .try span { font-size: 12px; color: var(--secondary-text-color); }
          .remove { padding: 6px; color: var(--secondary-text-color); }
          .error { color: var(--error-color, #db4437); font-size: 13px; min-height: 18px; margin-top: 8px; }
          .actions { display: flex; align-items: center; gap: 4px; margin-top: 8px; }
          .actions .space { flex: 1; }
          .delete { color: var(--error-color, #db4437); }
          .save { background: var(--primary-color); color: var(--text-primary-color, #fff); }
          .save:hover { background: var(--primary-color); filter: brightness(1.08); }
        </style>
        <dialog>
          <header><div class="swatch"></div><h2></h2></header>
          <ha-form></ha-form>
          <div class="label">Colours</div>
          <div class="colours"></div>
          <button class="more"><ha-icon icon="mdi:plus"></ha-icon><span>Add a colour</span></button>
          <div class="label">Brightness</div>
          <div class="row"><input class="all" type="range" min="1" max="100" step="1" aria-label="Brightness of every colour"><span class="value all-value"></span></div>
          <div class="drift">
            <div class="label">Drift between them</div>
            <div class="row"><input type="range" min="0" max="${DRIFT_STEPS.length - 1}" step="1" aria-label="Minutes per step"><span class="value"></span></div>
          </div>
          <label class="try"><input type="checkbox">Try on the lights<span>· they leave their scene</span></label>
          <div class="error" role="alert"></div>
          <div class="actions">
            <button class="delete"></button><span class="space"></span>
            <button class="cancel">Cancel</button><button class="save">Save</button>
          </div>
        </dialog>`;
      const $ = (sel) => root.querySelector(sel);
      this._dialog = $("dialog");
      $("h2").textContent = id ? "Change scene" : "New scene";
      const form = $("ha-form");
      form.hass = hass;
      form.schema = [
        { name: "name", required: true, selector: { text: {} } },
        { name: "icon", selector: { icon: {} } },
      ];
      form.computeLabel = (field) => ({ name: "Name", icon: "Icon" })[field.name];
      form.data = this._value;
      form.addEventListener("value-changed", (ev) => {
        this._value = { ...this._value, ...ev.detail.value };
        form.data = this._value;
      });
      // The drift moves in steps (a scene's own pace stays until it's moved).
      const drift = $(".drift input");
      drift.value = nearestStep(this._drift);
      drift.addEventListener("input", () => {
        this._drift = DRIFT_STEPS[Number(drift.value)];
        this._showValues();
      });
      // Brightness scales every colour together, from how they were when
      // the drag began, keeping the brightest at the slider's level.
      const all = $(".all");
      all.addEventListener("input", () => {
        this._scaling = this._scaling || this._colours.map((c) => c.brightness);
        const top = Math.max(...this._scaling);
        this._colours.forEach((c, i) => {
          c.brightness = Math.min(Math.max(Math.round((this._scaling[i] * Number(all.value)) / top), 1), 100);
        });
        this._rows();
      });
      all.addEventListener("change", () => { this._scaling = null; });
      onActivate($(".more"), () => {
        const last = this._colours[this._colours.length - 1];
        this._colours.push({ rgb: [...last.rgb], brightness: last.brightness });
        this._rows();
      });
      const remove = $(".delete");
      remove.hidden = !id;
      remove.textContent = "Delete";
      onActivate(remove, () => {
        if (remove.textContent === "Delete") remove.textContent = "Tap again to delete";
        else this._send({ type: `${DOMAIN}/delete_scene`, scene: id });
      });
      const trying = $(".try");
      trying.hidden = !this._lights;
      trying.querySelector("input").addEventListener("change", (ev) => {
        this._trying = ev.target.checked;
        this._try();
      });
      // Cancel, or Esc, asks first if anything's been changed.
      onActivate($(".cancel"), () => {
        if (this._mayDiscard()) this._dialog.close();
      });
      this._dialog.addEventListener("cancel", (ev) => {
        if (!this._mayDiscard()) ev.preventDefault();
      });
      onActivate($(".save"), () => this._save());
      this._dialog.addEventListener("close", () => {
        clearTimeout(this._trial);
        this.remove();
      });
      this._rows();
      this._opened = this._snapshot();
      this._dialog.showModal();
    }

    _snapshot() {
      return JSON.stringify([this._value.name, this._value.icon, this._colours, this._drift]);
    }

    // Whether the editor can close: nothing's changed, or they say so.
    _mayDiscard() {
      return this._snapshot() === this._opened || window.confirm("Discard your changes to this scene?");
    }

    // A row for each colour: its colour, its brightness and (with more than one) a cross.
    _rows() {
      const list = this.shadowRoot.querySelector(".colours");
      list.replaceChildren();
      this._colours.forEach((colour, i) => {
        const row = document.createElement("div");
        row.className = "row";
        row.innerHTML = `<input type="color"><input type="range" min="1" max="100" step="1"><span class="value"></span>
          <button class="remove"><ha-icon icon="mdi:close"></ha-icon></button>`;
        const [picker, slider] = row.querySelectorAll("input");
        picker.value = hex(colour.rgb);
        picker.setAttribute("aria-label", `Colour ${i + 1}`);
        picker.addEventListener("input", () => {
          colour.rgb = rgbOf(picker.value);
          this._showValues();
        });
        slider.value = colour.brightness;
        slider.setAttribute("aria-label", `Brightness of colour ${i + 1}`);
        slider.addEventListener("input", () => {
          colour.brightness = Number(slider.value);
          this._showValues();
        });
        const remove = row.querySelector(".remove");
        remove.title = "Remove this colour";
        remove.hidden = this._colours.length === 1;
        onActivate(remove, () => {
          this._colours.splice(i, 1);
          this._rows();
        });
        list.appendChild(row);
      });
      this.shadowRoot.querySelector(".more").hidden = this._colours.length >= 8;
      this._showValues();
    }

    // The swatch, the brightnesses (the scene's is its brightest colour's)
    // and the drift (only for more than one colour) as they are now.
    _showValues() {
      const root = this.shadowRoot;
      for (const [name, value] of Object.entries(sceneLook(this._colours.map((c) => c.rgb)))) this._dialog.style.setProperty(name, value);
      root.querySelectorAll(".colours .value").forEach((el, i) => { el.textContent = `${this._colours[i].brightness}%`; });
      const top = Math.max(...this._colours.map((c) => c.brightness));
      const all = root.querySelector(".all");
      if (!this._scaling) all.value = top;
      root.querySelector(".all-value").textContent = `${this._scaling ? all.value : top}%`;
      root.querySelector(".drift").hidden = this._colours.length === 1;
      root.querySelector(".drift .value").textContent = `${this._drift} min`;
      this._try();
    }

    // While trying them on the lights, shows the colours there once they've
    // stopped changing for a moment (not at every step of a drag), and only
    // if they have changed.
    _try() {
      clearTimeout(this._trial);
      if (!this._trying || this._sending) {
        if (!this._trying) this._tried = null;
        return;
      }
      this._trial = setTimeout(() => {
        const colours = JSON.stringify(this._colours);
        if (colours === this._tried) return;
        this._tried = colours;
        this._hass.callWS({ type: `${DOMAIN}/preview_scene`, lights: this._lights, colours: this._colours }).catch(() => {});
      }, 300);
    }

    _save() {
      const name = String(this._value.name || "").trim();
      if (!name || name.length > 30) {
        this.shadowRoot.querySelector(".error").textContent = name ? "Keep the name to 30 characters." : "Give it a name.";
        return;
      }
      const message = {
        type: `${DOMAIN}/save_scene`, name, icon: this._value.icon || "mdi:palette", colours: this._colours, drift_minutes: this._drift,
      };
      if (this._id) message.scene = this._id;
      else if (this._show) message.show = this._show;
      this._send(message);
    }

    // Sends it to Hue-Lite; closes once it's done, or says why not. Saved
    // after trying it on the lights, they're put in the scene. One at a time
    // (a second tap on Save mustn't make a second scene), with no more
    // tries on the lights meanwhile.
    async _send(message) {
      if (this._sending) return;
      this._sending = true;
      clearTimeout(this._trial);
      const buttons = this.shadowRoot.querySelectorAll(".save, .delete");
      for (const b of buttons) b.disabled = true;
      try {
        const result = await this._hass.callWS(message);
        const id = result?.id || this._id;
        if (this._trying && message.type === `${DOMAIN}/save_scene` && id) {
          this._hass.callService(DOMAIN, "apply_preset", { preset: id }, { entity_id: this._lights });
        }
        this._dialog.close();
      } catch (err) {
        this.shadowRoot.querySelector(".error").textContent = err?.code === "too_many"
          ? "There are too many scenes of your own: delete one first." : `Couldn't do that: ${err?.message || err}`;
      } finally {
        this._sending = false;
        for (const b of buttons) b.disabled = false;
      }
    }
  }

  // ---- Light card, for any dashboard ---------------------------------------

  // Hue-Lite's setup, asked for once per page (again if that failed).
  let setupAsked = null;
  function loadSetup(hass) {
    setupAsked = setupAsked || hass.callWS({ type: `${DOMAIN}/config` }).catch((err) => {
      setupAsked = null;
      throw err;
    });
    return setupAsked;
  }

  // The Lights dashboard's block for a light, a room (all its lights) or a
  // label group, with the same id, so it has the same scenes; null if there
  // are no such lights.
  function cardBlock(hass, setup, { entity, area, label }) {
    const lights = allLights(hass, setup.roles.exclude);
    const ids = (entries) => entries.map((x) => x.entity_id).sort(byName(hass));
    if (entity) {
      const entry = hass.entities?.[entity];
      if (!entry || !hass.states[entity]) return null;
      const areaId = areaOf(hass, entry);
      const inArea = ids(lights.filter((x) => areaOf(hass, x) === areaId));
      const id = `${areaId || "no-area"}${inArea.length <= 1 ? "" : `|${entity}`}`;
      const icon = hass.areas?.[areaId]?.icon || (areaId ? "mdi:texture-box" : "mdi:lightbulb-question-outline");
      return room(setup, id, undefined, icon, [entity], { entity_id: [entity] }, areaShow(setup, areaId || "no-area", inArea));
    }
    if (area) {
      const inArea = ids(lights.filter((x) => areaOf(hass, x) === area));
      const a = hass.areas?.[area];
      return inArea.length
        ? room(setup, area, a?.name || area, a?.icon || "mdi:texture-box", inArea, { area_id: area }, areaShow(setup, area, inArea))
        : null;
    }
    const tagged = ids(lights.filter((x) => labelsOf(hass, x).has(label)));
    const l = setup.labels.find((x) => x.label_id === label);
    return tagged.length ? room(setup, `label:${label}`, l?.name || label, l?.icon || "mdi:label-outline", tagged, { label_id: label }) : null;
  }

  // One block of the Lights dashboard on any dashboard: a light, a room or a
  // label group, with its bar (switch, dim, Auto) over its scenes, which are
  // the Lights dashboard's for the same one.
  //   {entity: light.x} or {area: living_room} or {label: lamps}
  class HueLiteLightCard extends HTMLElement {
    setConfig(config) {
      if (!config.entity && !config.area && !config.label) throw new Error("Choose a light, a room or a label");
      this._config = config;
      this._inner = null;
      this._token = null;
      this._build();
    }

    set hass(hass) {
      this._hass = hass;
      if (this._inner) this._inner.hass = hass;
      else if (!this._token) this._build();
    }

    getCardSize() {
      return 6;
    }

    getGridOptions() {
      return { columns: "full", rows: "auto" };
    }

    async _build() {
      if (!this._hass || !this._config) return;
      const token = (this._token = {});
      const setup = await loadSetup(this._hass).catch(() => null);
      if (token !== this._token) return;
      const block = setup && cardBlock(this._hass, setup, this._config);
      if (!block) {
        const note = document.createElement("ha-card");
        note.style.padding = "16px";
        note.textContent = setup ? "No Hue-Lite lights there." : "Hue-Lite isn't set up.";
        this.replaceChildren(note);
        return;
      }
      // Folded or not is kept for each block (two cards sharing one key
      // would each save over the other's).
      this._inner = document.createElement("hue-lite-rooms");
      this._inner.setConfig({ type: "custom:hue-lite-rooms", rooms: [block], toolbar: false, layout_key: `hue_lite_card:${block.id}` });
      this._inner.hass = this._hass;
      this.replaceChildren(this._inner);
    }

    // A room with lights, or else a light, for the card picker's preview.
    static getStubConfig(hass) {
      const lights = Object.values(hass.entities || {}).filter((e) => e.entity_id.startsWith("light.") && hass.states[e.entity_id]);
      const area = sortedAreas(hass).find((a) => lights.some((e) => areaOf(hass, e) === a.area_id));
      return area ? { area: area.area_id } : { entity: lights[0]?.entity_id || "" };
    }

    static getConfigElement() {
      return document.createElement("hue-lite-light-card-editor");
    }
  }

  // The card's editor: a light, or a room, or a label (whichever was picked last).
  const CARD_TARGETS = ["entity", "area", "label"];

  class HueLiteLightCardEditor extends HTMLElement {
    setConfig(config) {
      this._config = config;
      this._render();
    }

    set hass(hass) {
      this._hass = hass;
      this._render();
    }

    _render() {
      if (!this._hass || !this._config) return;
      if (!this._form) {
        this._form = document.createElement("ha-form");
        const labels = { entity: "A light", area: "Or a room (all its lights)", label: "Or a label group" };
        this._form.computeLabel = (field) => labels[field.name];
        this._form.schema = [
          { name: "entity", selector: { entity: { filter: { domain: "light" } } } },
          { name: "area", selector: { area: {} } },
          { name: "label", selector: { label: {} } },
        ];
        this._form.addEventListener("value-changed", (ev) => {
          const value = ev.detail.value;
          const key = CARD_TARGETS.find((k) => value[k] && value[k] !== this._config[k]) || CARD_TARGETS.find((k) => value[k]);
          const config = { type: this._config.type, ...(key ? { [key]: value[key] } : {}) };
          this.dispatchEvent(new CustomEvent("config-changed", { detail: { config }, bubbles: true, composed: true }));
        });
        this.appendChild(this._form);
      }
      this._form.hass = this._hass;
      this._form.data = this._config;
    }
  }

  for (const [tag, cls] of [
    ["ll-strategy-dashboard-hue-lite", HueLiteStrategy],
    ["hue-lite-light-card", HueLiteLightCard],
    ["hue-lite-light-card-editor", HueLiteLightCardEditor],
    ["hue-lite-light-bar", HueLiteLightBar],
    ["hue-lite-scene-tile", HueLiteSceneTile],
    ["hue-lite-rooms", HueLiteRooms],
    ["hue-lite-routines", HueLiteRoutines],
    ["hue-lite-dimmers", HueLiteDimmers],
    ["hue-lite-scene-editor", HueLiteSceneEditor],
  ]) {
    if (!customElements.get(tag)) customElements.define(tag, cls);
  }
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === "hue-lite-light-card")) {
    window.customCards.push({
      type: "hue-lite-light-card",
      name: "Hue-Lite light",
      description: "A light, room or label group as on the Lights dashboard: switch, dim, Auto and its scenes.",
      preview: true,
    });
  }

  // ---- Adding the dashboard ------------------------------------------------

  const STRATEGY = "custom:hue-lite";

  // The page's hass, once Home Assistant has it (or null after two minutes).
  async function pageHass() {
    for (let i = 0; i < 120; i++) {
      const hass = document.querySelector("home-assistant")?.hass;
      if (hass?.connection && hass.user) return hass;
      await new Promise((r) => setTimeout(r, 1000));
    }
    return null;
  }

  // Hue-Lite's Lights dashboard is added the first time an admin opens Home
  // Assistant after setting Hue-Lite up (unless its option's off), as
  // Settings → Dashboards → Add would, at /lights; a dashboard of Hue-Lite's
  // already there is found instead. Hue-Lite remembers, so it's only added
  // once, and only by one browser.
  async function addDashboard() {
    const hass = await pageHass();
    if (!hass?.user.is_admin) return;
    // A page opened while Home Assistant's starting can get here before
    // Hue-Lite's set up: keep asking for a while.
    let setup = null;
    for (let i = 0; i < 20 && !setup; i++) {
      setup = await hass.callWS({ type: `${DOMAIN}/config` }).catch(() => null);
      if (!setup) await new Promise((r) => setTimeout(r, 15000));
    }
    if (!setup?.add_dashboard) return;
    const { claimed } = await hass.callWS({ type: `${DOMAIN}/claim_dashboard` }).catch(() => ({}));
    if (!claimed) return;
    try {
      const dashboards = await hass.callWS({ type: "lovelace/dashboards/list" });
      for (const d of dashboards) {
        const config = await hass.callWS({ type: "lovelace/config", url_path: d.url_path }).catch(() => null);
        if (config?.strategy?.type === STRATEGY) return;
      }
      const taken = dashboards.some((d) => d.url_path === "lights") || hass.panels?.lights;
      const urlPath = taken ? "hue-lite-lights" : "lights";
      await hass.callWS({
        type: "lovelace/dashboards/create", url_path: urlPath, allow_single_word: true, mode: "storage",
        title: "Lights", icon: "mdi:lightbulb-group", show_in_sidebar: true, require_admin: false,
      });
      await hass.callWS({ type: "lovelace/config/save", url_path: urlPath, config: { strategy: { type: STRATEGY } } });
    } catch (err) {
      // Give the claim back, so the next admin page load tries again, and
      // say why in Home Assistant's log.
      const error = String(err?.message || err?.code || err).slice(0, 300);
      hass.callWS({ type: `${DOMAIN}/claim_dashboard`, release: true, error }).catch(() => {});
    }
  }

  if (!window.hueLiteAddingDashboard) {
    window.hueLiteAddingDashboard = true;
    addDashboard();
  }
})();
