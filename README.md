# Hue-Lite

Philips Hue's lighting behaviour for any lights in Home Assistant, without a
Hue Bridge: Hue's scenes, a **Natural light** scene that follows the sun, an
**evening routine**, **wake up**, **coming and leaving home**, **Hue dimmer
switches**, and a **Lights dashboard** that builds itself.

Lights take part by **area** and **label**, with no per-light YAML.

## Install

Needs Home Assistant 2026.10 or later.

- **HACS:** HACS → ⋮ → Custom repositories → add
  `https://github.com/Picardas/Hue-Lite` as an *Integration* → download
  **Hue-Lite** → restart Home Assistant.
- **By hand:** copy `custom_components/hue_lite` into your config's
  `custom_components/` folder and restart.

Then **Settings → Devices & services → Add integration → Hue-Lite**. It asks
for your home zone and whether to use Hue dimmers, and finds or creates its
labels. **Configure** changes these later.

## Labels

| Label | Effect |
| ----- | ------ |
| Evening | Taken by the evening routine: on before sunset, through the scenes, off at bedtime |
| Wake up | Fades up to Energize at the wake-up time |
| Nightlight | Gets Nightlight instead of Rest overnight and before bed |
| Coming home | Comes on in Natural light when someone arrives home |
| Exclude from lighting | Left out of everything |

A label counts on the light, its device or its area. Hidden lights are left
out too. Any other label becomes a group on the dashboard.

## Dashboard

Reload the browser after setting up Hue-Lite. Then add a dashboard (Settings
→ Dashboards → Add dashboard → New dashboard from scratch), open it, choose ✎ →
⋮ → Raw configuration editor, and replace everything with:

```yaml
strategy:
  type: custom:hue-lite
```

It builds itself from your areas and labels on every load: the whole home and
each room (reorderable), and the routines with their settings. Each room is a
bar for all its lights: tap its icon to switch them on or off, slide to dim,
**Auto** at the end, and tap its name to show or hide each light's bar and
the scenes. Icons and sliders take the colour the bulbs report; each
scene shows the colour it gives (Natural light's as it is now), filled and
ticked while the lights are in it.

## How it works

- **Scenes:** Natural light, Energize, Cool bright, Read, Relax, Rest and
  Nightlight. Colour bulbs get Hue's colours, white bulbs a colour
  temperature, and dimmable bulbs the brightness.
- **Colour scenes:** Golden hour, Nebula and Aurora, from colours in Hue's own
  scenes. Each room's colour bulbs get different colours from the scene,
  drifting slowly from one to the next; white bulbs get a warm white.
- **Natural light** follows the sun: Energize from dawn, Cool bright by
  midday, Relax at sunset, Rest at night. A light stays in it, on or off,
  until you pick another scene, change the light yourself or from an
  automation, or use one of your own Home Assistant scenes on it.
- **Evening routine:** before sunset, if someone's home, the Evening lights
  come on dim, brighten, wind down through Relax and Rest, and fade out at
  bedtime. Sun–Thu and Fri & Sat nights each have their own switch and
  bedtime, like wake up's days.
- **Wake up:** the Wake up lights fade from a warm glow to Energize by your
  wake-up time, and can turn off again afterwards.
- **Coming & going:** the Coming home lights come on in Natural light when
  someone arrives (optionally only after dark), and every light can go off
  when everyone has left.
- **Auto:** the button at the end of each light's and room's bar. A light is
  on Auto while wake up or the evening routine has it, or once you put it on
  Auto: then it follows both routines whether or not it has their labels,
  and stays on Auto (on or off) until you pick a scene for it or change it.
  Taking a light off Auto lets the routine holding it go; its labels still
  count next time.
- **Hue dimmers** (RWL02x on ZHA) control their area: On steps through the
  scenes, Off turns the lights off, Up and Down dim.

The settings are switch, time and number entities on the **Hue-Lite**
device, so they work in automations too. `sensor.hue_lite_light_scenes` shows
which scene each light is in.

## Actions

```yaml
action: hue_lite.apply_preset   # energize, cool_bright, read, relax, rest,
target: {area_id: living_room}  # nightlight, golden_hour, nebula, aurora
data: {preset: relax}
---
action: hue_lite.natural_light  # follow: false takes the lights out instead
target: {label_id: lamps}
---
action: hue_lite.next_scene     # what a dimmer's On button does
target: {area_id: bedroom}
```

With no target, they use every light. A non-admin user can only use lights
they're allowed to control.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

`tests/harness/index.html` renders the dashboard with a mock Home Assistant:
serve the repository root (e.g. `python3 -m http.server --bind 127.0.0.1`) and
open `/tests/harness/`.

## Licence

[MIT](LICENSE)
