# Spec: your own scenes (Hue-Lite 2.0)

## Goal

Users can make their own scenes:
- **Static:** one colour and brightness, like a preset.
- **Dynamic:** several colours, each with its own brightness, drifting at
  the scene's own speed.

They're added to the master list of scenes, so every room, light and dimmer
can use them.
- **Making one:** a **+ tile**, shown at the end of the tiles while editing
  scenes.
- **Starting from a built-in:** any scene can be duplicated and the copy
  edited.
- **Deleting:** your own scenes can be deleted; built-in ones can't.

Natural light and the routines are unchanged.

## Decisions

1. **Who:** anyone signed in can create, edit and delete custom scenes,
   like arranging tiles. Scenes only hold colours, and every input is
   bounded (see Data), so this isn't a security risk. Built-in scenes can't
   be edited or deleted.
2. **Where a new scene shows:**
   - It's shown in the room it was made in: that room's block, or every
     light's block if the room has several lights.
   - It's shown in any dimmer in the same area.
   - Everywhere else it starts **hidden**. Any room can show it with Edit
     scenes.
   - Made from Whole home or a label group, it's shown just there. Made
     from a dimmer, it's shown just in that dimmer.
3. **Drift speed** is set for each dynamic scene: 1–60 minutes per step,
   3 by default.
4. **Duplicate:** a pencil on any tile, built-in or custom, while editing.
   On a built-in it opens the editor with a copy; on a custom scene it
   edits it.

## How it works today (what changes)

The scene list is fixed in the code, in four places that must agree:

| Where | What | Change |
| ----- | ---- | ------ |
| `timeline.py`: `PRESETS`, `PALETTES` | The built-in scenes as `Light` and `Palette` | Unchanged; they become the built-in half of the library |
| `engine.py`: `TILE_SCENES`, `SCENES`, `DIMMER_DEFAULT`, `DRIFT_MINUTES` | Sets used for validation and stored data; one drift speed | Built-ins plus custom scenes, worked out at runtime; drift speed per scene |
| `dashboard.py`: `SCENE_COLOURS`, `SCENE_IDS` schema | The tiles' colours; which scene ids websocket commands accept | A function of the library; validated against it at call time |
| `hue-lite.js`: `PRESETS`, `COLOUR_SCENES`, `sceneOrder` | The tiles' names and icons; unseen scenes shown | Built-ins plus `shared.data.custom`; unseen custom scenes hidden |

Also fixed today: `services.yaml` (the preset options), `services.py` (the
`vol.In` check) and `en.json` (the option names).

## Data

- **Where:** in the Store, under a new key `custom_scenes`. Scenes are
  shared, so every dashboard and dimmer sees the same list.
  ```json
  "custom_scenes": {
    "custom_a1b2c3": {
      "name": "Disco",
      "icon": "mdi:party-popper",
      "colours": [[0.68, 0.31, 60], [0.17, 0.05, 40]],
      "white": [50, 2700],
      "drift_minutes": 3
    }
  }
  ```
- **What each part is:**
  - The id is `custom_` plus a short random part, so it never clashes with
    a built-in.
  - Each colour is `[x, y, brightness %]`. One colour means static; two to
    eight mean dynamic.
  - `white` is `[brightness %, kelvin]`, for bulbs without colour.
  - `drift_minutes` is only used by dynamic scenes.
- **Validation:** `_stored` in `engine.py` drops anything malformed.
  - Names are 1–30 characters.
  - x and y are within 0–1, clamped to Hue's gamut C.
  - Brightness is 1–100, kelvin is 2000–6500, and drift is 1–60 minutes.
  - At most 50 scenes.

## Backend changes

### `timeline.py`
- `Palette` gains `drift_minutes`, which defaults to 3, so the built-ins are
  unchanged.
- Add `scene_from_stored(data) -> Light | Palette`.
- Add `kelvin_for(xy)`, a McCamy approximation clamped to 2000–6500 K,
  for the default `white` the editor offers.

### `engine.py`
- **The library:** `self.custom` holds the stored dict, and
  `self.library()` returns the presets, palettes and custom scenes, keyed
  by id.
  - These read the library instead of `PRESETS[...]` and `PALETTES[...]`
    directly: `async_preset`, `send_palette`, `_scene_light`, `_drift`,
    and `TILE_SCENES` and `SCENES`.
  - A custom static scene behaves like a preset, and a dynamic one like a
    palette.
- **Drift per scene:** `_drift_step(palette)` uses the palette's
  `drift_minutes`. `_drifted` becomes a dict from scene to step, and each
  drift's transition is that scene's step length.
- **Load order:** load `custom_scenes` before filtering the stored scenes
  and layouts, so lights in a custom scene keep it across restarts.
- **`save_custom_scene(id, data, show)`:** adds or edits a scene, then calls
  `_shared_changed()`. Built-in ids are refused.
  - `show` is `{layouts: [keys], dimmers: [device ids]}`, only for a new
    scene: where it starts shown (Decision 2).
  - For each layout key, the id goes at the end of that layout's order. A
    key with no saved layout gets one: the default order, plus the id,
    shown. Each listed dimmer's `scenes` get the same.
  - Lights already in an edited scene are sent its new colours, like a
    drift.
- **`delete_custom_scene(id)`:** built-in ids are refused.
  - Lights in the scene leave it (no scene, staying as they are).
  - Its id is removed from every layout and dimmer `order`/`hidden`.
- **`shared()`:** add `"custom": {...}` with each scene's `rgb_colors`,
  `name`, `icon`, `colours`, `white` and `drift_minutes`, so tiles and the
  editor update live everywhere.
- **`DEFAULT_ORDER`:** the built-in tiles' order (the presets, Natural
  light, the colour scenes), matching `sceneTiles` in the script. It's used
  when a layout is first created for `show`.

### `dashboard.py`
- **New websocket commands**, both using `_with_hue_lite` and open to any
  signed-in user (not `require_admin`):
  - `hue_lite/save_scene` takes `{id?, name, icon, colours, white,
    drift_minutes, show?}`. With no id, it's a new scene and the reply is
    the new id.
  - `hue_lite/delete_scene` takes `{id}`.
  - Bound the sizes as in Data, and accept at most 100 `show` keys.
- **Validation:** `SCENE_IDS` checks ids against `library()` when a
  command is called, not against a fixed set.
- **Colours:** `SCENE_COLOURS` becomes `_scene_colours(hue_lite)`, used by
  `_config`.
- **Colour conversion:** the editor sends RGB, and this converts it to xy
  with `color_RGB_to_xy` on the way in, so the browser never does colour
  maths.

### `services.py` and `services.yaml`
- `apply_preset` checks the preset against the library in the handler,
  raising `ServiceValidationError` with a new `unknown_scene` message.
- In the YAML, the preset selector becomes `select` with
  `custom_value: true`, so custom ids are accepted.

## Frontend changes (`hue-lite.js`)

- **Scene list:** `allScenes()` returns the built-ins plus
  `shared.data.custom`. This replaces direct use of `PRESETS` and
  `COLOUR_SCENES` in:
  - `sceneTiles`;
  - `HueLiteSceneTile.setConfig` (whose config now carries `name`, `icon`
    and `rgb_colors`);
  - `howItWorks`.
- **Ordering:** `sceneOrder` puts unseen *custom* ids at the end, hidden.
  Unseen built-in ids stay shown, so a future built-in still appears.
- **Scene sets** (`SceneSet`):
  - **Rebuilding:** when the list of custom ids changes, `_sharedChanged`
    in the Rooms and Dimmers cards rebuilds the affected sets. Today the
    sets are built once in `_render`.
  - **The + tile:** a last cell, shown only while editing, for everyone.
    It isn't draggable or affected by `sorter`. It opens the editor empty.
  - **The pencil:** next to the eye on every tile while editing. On a
    built-in it opens the editor with a copy ("Copy of Relax"); on a
    custom scene it edits it.
- **Where new scenes show:** the editor works out `show` from where it was
  opened.
  - From a room's block: every block in that area (keys `area` and
    `area|light`), plus every dimmer whose `area_id` is that area.
  - From Whole home or a label group: just that block's key.
  - From a dimmer: just that dimmer.
  - This needs each set to know its area, so `room()` and `areaBlocks()`
    pass it.
- **Editor dialog** (new `hue-lite-scene-editor`):
  - Built with `ha-form`: name (text), icon (`icon` selector), and a list
    of 1–8 colours (`color_rgb` selector plus a 1–100 brightness slider for
    each).
  - Add and remove colour rows. One row makes a static scene; more make a
    dynamic one, which shows a drift slider (1–60 minutes).
  - A live preview swatch uses `sceneLook`.
  - Save calls `save_scene`. Delete appears for custom scenes only, asks to
    confirm, and calls `delete_scene`.

## Tests (`tests/test_custom_scenes.py`, new)

1. Saving a static scene and a dynamic one; `apply_preset` with each (a
   static scene sends one colour, a dynamic one spreads and drifts at its
   own speed); both kept across a reload.
2. `show`: a new scene is shown in the layouts and dimmers given, and is
   absent (so hidden) elsewhere. A layout that didn't exist is created
   with the default order.
3. Validation: bad colours, names, drift and sizes; the 50-scene limit; a
   non-admin can save and delete; built-in ids can't be saved over or
   deleted.
4. Editing a scene resends its colours to the lights in it. Deleting one
   frees those lights and removes its id from layouts and dimmers.
5. Damaged `custom_scenes` in storage is left out.
6. In `test_shared`, `shared()` includes the custom scenes.

The harness needs mocks for `save_scene` and `delete_scene`, and one custom
scene in `sharedData`.

## Docs and release

- **README:** a "Your own scenes" section, plus a new screenshot of the
  editor.
- **AGENTS.md:** under "Adding things", note that built-in scenes still go
  in `timeline.py`, and custom ones live in the Store.
- **Version:** 2.0.0, because `apply_preset`'s options and the tile config
  change. No storage migration is needed, since the change is additive.
