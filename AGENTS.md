# AGENTS.md

How to work on Hue-Lite: for AI coding agents, and for anyone else. The
README covers what Hue-Lite does for its users.

Hue-Lite is a Home Assistant custom integration (domain `hue_lite`),
installed through HACS. It gives any lights Philips Hue's scenes and
routines. It has no dependencies beyond Home Assistant, and its dashboard is
one plain JavaScript file with no build step.

## Layout

| Path | What's there |
| ---- | ------------ |
| `custom_components/hue_lite/engine.py` | The core: settings, which scene each light is in, Auto, the clock, stored data (`_stored` checks everything loaded) |
| `routines.py` | The evening routine, wake up, coming and going |
| `timeline.py` | Presets, colour scenes and the two timelines, as plain functions with no Home Assistant imports |
| `lights.py` | `Sender`: every light command goes through it, so Hue-Lite knows its own changes and commands don't queue up behind a slow bulb |
| `targets.py` | Which lights a target means, and which are excluded |
| `dimmers.py` | Hue dimmer presses (ZHA events) |
| `dashboard.py` | Serves the script; the websocket commands the dashboard uses |
| `services.py`, `services.yaml` | The actions, which check the caller's permissions |
| `switch.py`, `time.py`, `number.py`, `sensor.py`, `entity.py` | The entities: settings, and the routines' state |
| `config_flow.py`, `__init__.py` | Setup, options, migrations |
| `frontend/hue-lite.js` | The Lights dashboard: a strategy and its cards |
| `translations/en.json`, `icons.json` | Entity, option and action text; icons |
| `tests/` | pytest tests; `tests/harness/index.html` renders the dashboard with a mock Home Assistant |
| `docs/images/` | README screenshots |

## Checking a change

```bash
pip install -r requirements_test.txt   # pins Home Assistant's test tooling, so tests run against a known version
pytest
```

- **CI:** every push runs the tests, hassfest and HACS's checks (see
  `.github/workflows/validate.yml`). The tests take about 10 seconds on
  Linux. Under WSL, keep the virtual environment on the Linux filesystem;
  on a Windows drive the tests take minutes.
- **Dashboard script:** there are no JavaScript tests. Serve the repository
  root (`python3 -m http.server --bind 127.0.0.1`) and open
  `/tests/harness/`. It takes `?dark`, `?user` (not an admin), `?unshared`
  and `?existing`, and exposes `window.dash` and `window.calls`.
- **Real bulbs:** say plainly what hasn't been tried on real bulbs or a real
  Home Assistant.

## Conventions

- **Keep it like Hue: simple and decoupled.** Prefer fewer settings, and one
  clear behaviour over options.
- **Behaviour lives in Python.** The dashboard shows state and calls actions
  and websocket commands; it decides nothing itself.
- **Every behaviour change has a test.** Tests describe what happens from
  the user's side, one scenario per test, with comments narrating the steps.
  - Use conftest's `home` (a few lights, areas and labels) and its helpers:
    `tick`, `settle`, `on`, `ours`, `action`, `switch`, `after_dark`.
  - Light actions are recorded, not run, so a light's state only changes
    when a test sets it.
- **Comments and docstrings** are plain English and say what happens, not
  how the code does it. Use British spelling (colour, behaviour).
- **Distrust stored data and the dashboard.** Check everything loaded from
  storage, and bound everything a websocket command accepts.
  - A command that controls lights for other people is admin-only.
  - Commands use `_with_hue_lite`.
- **Send light commands only through `Sender`.**
- **`manifest.json` has no requirements.** Don't add any.

## Adding things

- **A setting:**
  1. Add it to `SWITCHES`, `TIMES` or `NUMBERS` in `const.py`.
  2. Add its name to `translations/en.json` and its icon to `icons.json`.
  3. Put it on the dashboard: `routinesCard` or `settingsView` in
     `hue-lite.js`.
- **A colour scene:**
  1. Add it to `PALETTES` in `timeline.py`. Each colour is an xy value
     inside Hue's gamut C and a brightness.
  2. Add it to `COLOUR_SCENES` and `howItWorks` in `hue-lite.js`.
  3. Add it to the options in `services.yaml`, and to the selector and the
     `apply_preset` description in `en.json`.
  4. Update the README, the harness's `presets`, and the dimmer default in
     `tests/test_shared.py`.
- **Stored data:** add it to `_stored` and `_data` in `engine.py`, with a
  test that damaged data is left out.

## Releasing

- **Version:** bump `version` in `manifest.json` for every release.
  Browsers pick up a new script by itself: it's served versioned by its
  content.
- **Minimum Home Assistant version:** it's in `hacs.json` and the README.
  Change both together, along with the pin in `requirements_test.txt`.
- **README:** keep it matching what Hue-Lite does.
- **Commits:** sign them, and never skip hooks or signing.
