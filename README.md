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
| `wireframe`        | one beam per mesh edge, snapped to the 26 cube directions (`--directions free` for exact angles). Flat-face diagonals are skipped |
| G-code input       | the slicer's toolpaths as beams on the 1 m grid (see below)     |

### Using a slicer (e.g. a non-planar slicer like Unlayered)

Treat the slicer as the path planner and this tool as the "printer firmware" for
Satisfactory. Every extruding `G1` move becomes a beam; travel moves (`G0`, or no extrusion)
are skipped.

By default (`--directions 26`) every path is restricted to the **1×1×1 m grid** and to
**straight lines in the 26 directions of a cube**:

| step              | example vector | beam length per step |
|-------------------|----------------|----------------------|
| cube edge         | `(1, 0, 0)`    | 1.00 m               |
| face diagonal     | `(1, 1, 0)`    | 1.41 m               |
| space diagonal    | `(1, 1, 1)`    | 1.73 m               |

Each path is traced through the grid (3D DDA), then consecutive steps in the same direction
are merged into one long beam (split at `max_beam_length`). Overlapping paths are only built
once. Lines that don't fit a cube direction become a staircase of allowed steps, and curves
become chains of short pieces. That's why a non-planar slicer helps: paths that climb in
X, Y and Z at the same time come out as true 3D diagonals instead of layer steps.
`--directions free` allows any integer vector instead.

Slicer settings that suit this (1 grid cell = `1 / scale` mm):

- **Line width ≈ layer height ≈ 1 / scale mm.** With `--scale 0.1`, slice with 10 mm lines,
  or scale the model in the slicer so 1 mm = 1 beam and use `--scale 1`.
- **One wall/perimeter** and **0 % infill**, or a sparse straight-line pattern (rectilinear,
  grid, or 3D "cubic"-like infill) at a large spacing. Gyroid or other curved infill
  becomes lots of tiny pieces.
- **No top/bottom solid layers**, no brim/skirt/support (or delete them from the G-code).
- **Absolute XYZ** (`G90`). Absolute or relative E (`M82`/`M83`) both work.

Try it with the bundled synthetic non-planar spiral:

```
python examples/make_examples.py
python -m satisfactory_printer build examples/spiral.gcode --scale 1 -o out
```

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
