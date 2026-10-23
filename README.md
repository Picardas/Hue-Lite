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

It builds itself from your areas and labels on every load: the whole home,
each room (collapsible and reorderable) with a bar per light and the six
scenes, and the routines with their settings.

## How it works

- **Scenes:** Natural light, Energize, Cool bright, Relax, Rest and
  Nightlight. Colour bulbs get Hue's colours, white bulbs a colour
  temperature, and dimmable bulbs the brightness.
- **Natural light** follows the sun: Energize from dawn, Cool bright by
  midday, Relax at sunset, Rest at night. A light stays in it, on or off,
  until you pick another scene, change the light yourself or from an
  automation, or use one of your own Home Assistant scenes on it.
- **Evening routine:** before sunset, if someone's home, the Evening lights
  come on dim, brighten, wind down through Relax and Rest, and fade out at
  bedtime.
- **Wake up:** the Wake up lights fade from a warm glow to Energize by your
  wake-up time, and can turn off again afterwards.
- **Coming & going:** the Coming home lights come on in Natural light when
  someone arrives (optionally only after dark), and every light can go off
  when everyone has left.
- **Hue dimmers** (RWL02x on ZHA) control their area: On steps through the
  scenes, Off turns the lights off, Up and Down dim.

The settings are switch, time and number entities on the **Hue-Lite**
device, so they work in automations too. `sensor.hue_lite_light_scenes` shows
which scene each light is in.

## Actions

```yaml
action: hue_lite.apply_preset   # energize, cool_bright, relax, rest, nightlight
target: {area_id: living_room}
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
