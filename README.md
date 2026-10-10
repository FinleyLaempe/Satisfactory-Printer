# Satisfactory Printer

Turns a 3D model (`.stl` / `.obj`) or slicer G-code into the keystrokes that build it out of
**beams** in Satisfactory, then types those keys into the game for you.

```
model.stl ──► voxels (1 m) ──► beams (1×1×L) ──► keystroke plan ──► game
print.gcode ─────────────────► beams (any direction) ─┘
```

## The placement technique

The player stands still with the beam tool selected and the crosshair resting on a fixed
point: the **anchor**. Every beam is then built from the anchor using only keys:

1. `H` locks the hologram. Nudge the beam **start** by `start − end` (arrows = horizontal,
   Page Up/Down = vertical, 1 press = 1 m).
2. Left click without moving the mouse. The beam is drawn from the nudged start to the
   anchor, so step 1's nudge sets its **length and direction**.
3. `H` again, then nudge the whole beam by `end` (its end point relative to the anchor).
4. Left click to build.

The sequence is the `beam_macro` setting in the config, so you can change it if the game
behaves differently (see *Calibration*).

## Install

```
pip install -r requirements.txt
```

`pydirectinput` (Windows) sends DirectInput scan codes, which games register. `pynput` is the
fallback, and it also provides the F8 (pause) and F9 (abort) hotkeys.

## Usage

```bash
# 3D model -> plan. --size = largest dimension in metres
python -m satisfactory_printer build model.stl --size 20 -o out

# only the edges, diagonal beams allowed (good for low-poly models)
python -m satisfactory_printer build model.stl --size 20 --mode wireframe -o out

# slicer G-code -> plan (every printed line becomes a beam). --scale = metres per mm
python -m satisfactory_printer build print.gcode --scale 0.5 -o out

# check it, then build it in the game
python -m satisfactory_printer run out/plan.json --dry-run
python -m satisfactory_printer run out/plan.json            # 5 s countdown, switch to the game
python -m satisfactory_printer run out/plan.json --start-at 120   # resume after an abort
```

`build` writes these files:

| file          | contents                                                        |
|---------------|-----------------------------------------------------------------|
| `plan.json`   | the keystrokes per beam (input for `run`)                       |
| `keys.txt`    | readable key list, e.g. `H LEFTx9 CLICK H RIGHTx5 UPx2 CLICK`   |
| `preview.obj` | the beams as boxes; open it in Blender / Windows 3D Viewer      |
| `layers.txt`  | ASCII slice of each voxel layer (voxel modes)                   |

### Modes

| mode               | what you get                                                    |
|--------------------|-----------------------------------------------------------------|
| `voxel` (default)  | the model filled with 1×1 beams, hollowed to a 1 m shell (`--solid` keeps the inside). Beams run along the axis that needs the fewest, or the one set by `--axis` |
| `surface`          | a shell from meshes that aren't watertight                      |
| `wireframe`        | one beam per mesh edge. Flat-face diagonals are skipped         |
| G-code input       | the slicer's toolpaths as beams                                 |

### Using a slicer

Any slicer can act as the path planner. Slice the model normally (Cura, PrusaSlicer,
OrcaSlicer), then feed the `.gcode` to `build`. A good starting point is a layer height and
line width of `1 / scale` mm, so one extruded line is one beam wide. Use 0–15 % infill or
vase mode, or you'll get thousands of beams.

A **non-planar** ("unlayered") slicer also works: its moves change Z mid-layer, and those
moves become sloped beams. That suits this approach, because a nudged beam can point in any
direction.

## Calibration (do this first, in a test world)

1. Build a small test plan:
   `python -m satisfactory_printer build examples/cube.stl --size 3 --mode wireframe -o out`
2. Stand on a foundation. Select the beam, aim at a fixed spot on the floor (the anchor),
   and **don't move** while the script runs.
3. Check these and fix them in a copy of `config.example.json`, then pass it with `-c`:
   - **Axis directions.** Nudge keys follow the camera. If the model comes out mirrored or
     rotated, swap the entries in `keys`.
   - **Macro.** If your game needs a different key order, edit `beam_macro`. Tokens:
     `key:lock`, `nudge:shape`, `nudge:place`, `click`, `wait:0.5`.
   - **Timing.** If key presses get lost, raise `key_delay`, `key_interval` and `click_delay`.
   - **Limits.** `max_beam_length` splits long beams. `max_nudge` warns when an offset needs
     more presses than the game allows.
4. **Keep the line of sight clear.** If a built beam ends up between your camera and the
   anchor, the crosshair hits that beam and the anchor moves. By default the model starts 2 m
   *beyond* the anchor (`auto_offset_gap`). You can also set `model_offset` to `[x, y, z]`.

## Tests

```
python -m pytest tests
python examples/make_examples.py   # regenerates examples/*.stl
```
