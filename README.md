# Hue-Lite

Philips Hue's lighting behaviour for any lights in Home Assistant, without a
Hue Bridge: Hue's scenes, a **Natural light** scene that follows the sun, an
**evening routine**, **wake up**, **coming and leaving home**, **Hue dimmer
switches**, and a **Lights dashboard** that builds itself.

Nothing is configured per light in YAML. Lights take part through their
**area** and a few **labels**, so a new light only needs an area and the labels
of the routines it should join.

## Install

Hue-Lite needs Home Assistant 2025.1 or later. It works with colour bulbs,
white-ambiance (tunable white) bulbs, which get a colour temperature for each
scene, and dimmable-only bulbs, which get the brightness. Rooms can mix them.

**With HACS:** HACS → ⋮ → Custom repositories → add this repository's URL as
an *Integration* → download **Hue-Lite** → restart Home Assistant.

**By hand:** copy `custom_components/hue_lite` into your config's
`custom_components/` folder and restart.

Then **Settings → Devices & services → Add integration → Hue-Lite**. It asks
two things:

- **Home zone** (default `zone.home`): its number of people decides coming
  home, leaving home and whether the evening routine starts.
- **Hue dimmer switches**: let Hue Dimmer Switches (RWL02x) paired with ZHA
  control the lights in their area.

Setup also finds or creates the five role labels below. **Configure** on the
integration changes these answers, or points a role at another label.

## Adding a light

Give it an **area** and any of these **labels** (Settings → Devices & services
→ Entities → the light → Settings):

| Label | Effect |
| ----- | ------ |
| Evening | Taken by the evening routine: on before sunset, through the scenes, off at bedtime |
| Wake up | Fades up to Energize at the wake-up time |
| Nightlight | Gets Nightlight instead of Rest overnight (Natural light) and before bed (evening routine) |
| Coming home | Comes on in Natural light when someone arrives home |
| Exclude from lighting | Left out of everything: the dashboard, scenes, routines and dimmers |

Hidden lights are left out too (e.g. one run by its own motion sensor). Any
other label you create becomes a group on the dashboard, e.g. "Lamps".

## The Lights dashboard

Hue-Lite serves a dashboard strategy. To add the dashboard:

1. Reload the browser after setting up Hue-Lite.
2. Settings → Dashboards → **Add dashboard** → **New dashboard from scratch**,
   name it (e.g. *Lights*) and open it.
3. ✎ Edit dashboard → ⋮ → **Raw configuration editor**, replace everything
   with the following and save:

   ```yaml
   strategy:
     type: custom:hue-lite
   ```

It's rebuilt on every load: **Whole home** (All lights and the scenes), then
**Rooms**, one collapsible block per area with lights (by floor), "Other" for
lights without an area and one per label of your own, each with a bar per light
(tap to toggle, slide to dim, hold for the colour dialog) and the six scenes.
The scene a room is in is highlighted. Room order and collapsed rooms are saved
per user (**Reorder**, **Collapse all**). Below are the **Routines**, Wake up,
Evening and Coming & going, as blocks that collapse the same way; collapsed,
each says what it will do (e.g. "Mon–Fri 07:00 · Sat & Sun off"). Fades and the
evening timeline are on a **Timings & fades** page.

The cards can be used on other dashboards too: `custom:hue-lite-light-bar`,
`custom:hue-lite-scene-tile`, `custom:hue-lite-rooms`, `custom:hue-lite-routines`.

## Scenes

| Scene | Colour bulbs (xy) | White bulbs | Brightness |
| ----- | ----------------- | ----------- | ---------- |
| Natural light | follows the sun (below) | | |
| Energize | 0.3143, 0.3301 | 6410 K | 100% |
| Cool bright | 0.3812, 0.3794 | 4000 K | 100% |
| Relax | 0.5019, 0.4152 | 2237 K | 56% |
| Rest | 0.561, 0.4042 | 2000 K | 35% |
| Nightlight | 0.67, 0.32 | 2000 K | 1% |

The colours are Hue's colour-bulb values (Nightlight is redder than Hue's).
White bulbs get their own colour temperatures rather than ones converted from
the colours, which would make Nightlight a cool 4100 K. Blends (Natural light,
the evening routine) blend both. Bulbs that go no warmer than e.g. 2200 K
stop there.

Hue-Lite remembers which scene each light is in rather than recognising it
from its colour, so the dashboard highlights and the dimmer cycle work for
every kind of bulb. `sensor.hue_lite_light_scenes` lists them (`scenes`).
Changing a light by hand forgets its scene.

**Natural light** follows the sun from your Home Assistant location:

| Phase | When | Light |
| ----- | ---- | ----- |
| Night | an hour after dusk until dawn | Rest (Nightlight-labelled lights: Nightlight) |
| Sunrise | dawn until an hour after sunrise | Rest → Energize over 5 minutes, then Energize |
| Morning | until solar noon | Energize → Cool bright |
| Day | solar noon until 90 min before sunset | Cool bright |
| Sunset | the last 90 min before sunset | Cool bright → Relax |
| Evening | sunset until an hour after dusk | Relax → Rest |

A light joins Natural light when it's picked for it (dashboard, dimmer, coming
home, the `hue_lite.natural_light` action) and stays in it, on or off, until
another scene is picked or it's changed by hand (brightness or colour from the
dashboard or a light dialog). Lights in it that are on move every minute; a
light that's switched on picks up the current light straight away.
`sensor.hue_lite_natural_light` shows the phase, the light it wants, and the
lights in it (`lights`).

## Routines

**Evening routine** (switch *Evening routine*). Before sunset, if someone's
home, it takes the Evening-labelled lights and moves them through the evening:

| Phase | When | Routine's lights |
| ----- | ---- | ---------------- |
| Evening | from *start before sunset* | 5%, brightening to Cool bright over *brighten over* |
| Wind down | *wind down* before bedtime | Cool bright → Relax → Rest |
| Nightlight | last *nightlight before bed* minutes | Rest (Nightlight-labelled lights: Nightlight) |
| Night | bedtime (Sun–Thu or Fri & Sat) until 05:00 | fade out over *bedtime fade-out* |

A bedtime after midnight belongs to the evening before. Picking a scene for a
light (Natural light included) or changing it by hand takes it out of the
routine for the night. `sensor.hue_lite_evening_routine` shows the phase,
tonight's start and bedtime, and the routine's lights.

**Wake up** (switches *Wake up on weekdays* / *on weekends*, times *Wake up
Mon–Fri* / *Sat & Sun*): the Wake up lights go from 1% at 2200 K to Energize
over *wake-up fade-in*, reaching it at the wake-up time. Skipped if one of them
is already on. With *Wake-up lights turn off*, they go off *wake-up lights off
after* the wake-up time.

**Coming home** (switch *Coming home*, optionally *only after dark*): when the
number of people in the home zone goes up, the Coming home lights that are off
come on in Natural light.

**Leaving home** (switch *Lights off when everyone leaves*, off by default):
when the home zone has been empty for 2 minutes, every light goes off.

**Hue dimmer switches** control the lights in the dimmer's area: On steps
through Natural light, Energize, Cool bright, Relax, Rest and Nightlight (off
lights start at Natural light); Off turns them off; Up and Down change
brightness by 10%, with Down stopping at about 10%. Dimming counts as a change
by hand.

## Entities

All on one **Hue-Lite** device, so they can go on any dashboard or in your own
automations:

| Entity | Default |
| ------ | ------- |
| `switch.hue_lite_evening_routine` | on |
| `switch.hue_lite_coming_home`, `…_coming_home_only_after_dark` | on, on |
| `switch.hue_lite_lights_off_when_everyone_leaves` | off |
| `switch.hue_lite_wake_up_on_weekdays`, `…_on_weekends` | off, off |
| `switch.hue_lite_wake_up_lights_turn_off` | off |
| `time.hue_lite_wake_up_mon_fri`, `…_sat_sun` | 07:00, 09:00 |
| `time.hue_lite_bedtime_sun_thu_nights`, `…_fri_sat_nights` | 23:00, 00:00 |
| `number.hue_lite_wake_up_fade_in` / `…_lights_off_after` (min) | 30 / 60 |
| `number.hue_lite_bedtime_fade_out` (min) | 15 |
| `number.hue_lite_evening_start_before_sunset` / `…_brighten_over` / `…_wind_down` / `…_nightlight_before_bed` (min) | 90 / 60 / 120 / 30 |
| `sensor.hue_lite_natural_light`, `sensor.hue_lite_evening_routine`, `sensor.hue_lite_light_scenes` | |

Settings and which lights are in which scene survive restarts.

## Actions

Each takes a light target (entities, areas, floors, devices or labels); with
none, every light (except hidden, excluded and light groups).

```yaml
action: hue_lite.apply_preset   # energize, cool_bright, relax, rest, nightlight
target:
  area_id: living_room
data:
  preset: relax
---
action: hue_lite.natural_light  # follow: false takes the lights out instead
target:
  label_id: lamps
---
action: hue_lite.next_scene     # what a dimmer's On button does
target:
  area_id: bedroom
```

## Coming from the YAML setup

Hue-Lite grew out of a YAML package (helpers, Jinja macros, scripts,
automations and a Lovelace resource). Before setting it up on the same Home
Assistant, remove those (`packages/lighting.yaml`, `custom_templates/lighting.jinja`,
the lighting scripts and automations, and the `/local/lights-dashboard.js`
resource), or both will drive the lights. The labels are reused as they are
(`evening`, `wake_up`, `nightlight`, `coming_home`, `lighting_exclude`). Times,
fades and switches start at Hue-Lite's defaults, and the scene lists start
empty. What changed:

- Settings are Hue-Lite's own entities instead of `input_*` helpers.
- Sun times come from the location directly, so the sun sensors needn't be enabled.
- Wake up takes its lights out of Natural light and the evening routine, so
  neither moves them during the fade.
- "Every light" leaves out light groups.
- The scene a light is in is remembered instead of recognised from its
  colour, and white bulbs get colour temperatures of their own.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

The tests run against real Home Assistant. `tests/harness/index.html` renders
the dashboard script with a mock `hass` (serve the repository root, e.g.
`python3 -m http.server --bind 127.0.0.1`, and open `/tests/harness/`).
