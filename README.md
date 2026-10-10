# Satisfactory Printer

Turns a 3D model (`.stl` / `.obj`) into the keystrokes that build it out of
**beams** in Satisfactory, then types those keys into the game for you.

```
model.stl ──► flat areas  → parallel beams in the plane (sloped planes → diagonal beams) ─┐
          ├─► curved areas → contour slices every metre, straightened into long beams ───┤
          ├─► sharp edges  → one beam chain per edge ─────────────────────────────────────┼─► merge ─► keystrokes ─► game
          └─► (or) voxels / mesh edges ───────────────────────────────────────────────────┘
```

Beams can be anything from 1 m to 40 m long, point in any direction (any whole-number
vector, e.g. `(17, 0, 9)` for a sloped roof beam), and overlap freely. That's what keeps
slopes and curves from turning into staircases.

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
# 3D model -> plan + browser preview. --size = largest dimension in metres
python -m satisfactory_printer build model.stl --size 30 -o out --open

# live preview: sliders for size / mode / tolerance / spacing, typing speed, and
# a "Build in game" panel that types the plan and shows which beams are placed
python -m satisfactory_printer serve model.stl

# try every mode side by side (one folder per mode, each with preview.html)
python -m satisfactory_printer compare model.stl --size 30 -o cmp

# check it, then build it in the game
python -m satisfactory_printer run out/plan.json --dry-run
python -m satisfactory_printer run out/plan.json            # 5 s countdown, switch to the game
python -m satisfactory_printer run out/plan.json --start-at 120   # resume after an abort
python -m satisfactory_printer run out/plan.json --simulate       # real timing, no key presses
```

`build` writes these files:

| file           | contents                                                        |
|----------------|-----------------------------------------------------------------|
| `preview.html` | **3D preview in the browser**: rotate/zoom, build-order slider and playback, the keys for every beam, original model as a ghost, colour by method/length/order, accuracy stats |
| `plan.json`    | the keystrokes per beam (input for `run`)                       |
| `keys.txt`     | readable key list, e.g. `H LEFTx9 CLICK H RIGHTx5 UPx2 CLICK`   |
| `preview.obj`  | the beams as boxes; open it in Blender / Windows 3D Viewer      |
| `layers.txt`   | ASCII slice of each voxel layer (voxel mode)                    |

The preview loads three.js from a CDN, so it needs an internet connection.

### Live preview (`serve`)

`serve` starts a small local web server (127.0.0.1 only) and opens the preview with controls:

- **Size, mode, tolerance, spacing sliders.** Every change rebuilds the beams and redraws
  them. Accuracy numbers follow a moment later.
- **Typing speed (keystrokes per second).** Updates the build-time estimate instantly and
  sets the key timing of saved and started plans. Also available as `--kps` on `build`.
- **Save plan.** Writes `plan.json`, `keys.txt`, `preview.html` etc. to
  `out/<model>_<mode>_<size>m/`.
- **Build in game.** Pick the start beam and countdown, press **Start**, then switch to
  Satisfactory. The 3D view shows placed beams solid, the next beam red and the remaining
  beams faint, with progress and time left. **Pause** and **Stop** take effect after the
  current beam, so no beam is left half-placed. In-game, F8 pauses and F9 aborts at once.
  After stopping, "Start at beam" is already set to where you left off. Tick **Simulate**
  to rehearse the timing without pressing any keys.

Run `serve` on the gaming PC itself so it can send the keys.

The build-progress slider uses the same view: solid = placed, red = next, faint = still to come.

### Modes

| mode               | what you get                                                    | best for |
|--------------------|-----------------------------------------------------------------|----------|
| `hybrid` (default) | flat areas → `planes`, curved areas → `contour`, plus a beam along every sharp edge | almost everything |
| `planes`           | every flat area (any orientation) filled with parallel beams lying *in* that plane. Tilted planes (roofs, ramps) get beams running **down the slope**, so they're smooth diagonals instead of steps | buildings, vehicles, low-poly |
| `contour`          | the surface sliced every metre. Each face is cut along the axis that suits its slope: steep walls → horizontal rings, flatter tops → vertical slices that follow the curve as arcs. Each slice is straightened into long beams within `--tolerance` | organic shapes, statues |
| `voxel`            | 1 m cubes, hollowed to a shell (`--solid` keeps the inside). Covered greedily with the fewest beams, mixing all 3 axes and diagonals (`--directions 6` = axes only) | a blocky look |
| `wireframe`        | one beam per mesh edge (`--directions 26` = cube directions only, default) | low-poly sculptures |

Example: the bundled models at 20 m (`python examples/make_examples.py`):

| model  | mode      | beams | build time | surface coverage | mean deviation |
|--------|-----------|------:|-----------:|-----------------:|---------------:|
| house  | hybrid    |   113 |     11 min |             100% |         0.27 m |
| house  | voxel     |    73 |      7 min |              89% |         0.59 m |
| house  | wireframe |    17 |      2 min |              18% |         1.96 m |
| sphere | hybrid    |  1079 |     75 min |             100% |         0.30 m |
| sphere | hybrid, `--ring-spacing 2 --tolerance 1` | 231 | 18 min | 83% | 0.49 m |
| sphere | voxel     |   236 |     17 min |              87% |         0.53 m |

### Realism vs. build time

| option              | default | effect |
|---------------------|---------|--------|
| `--size`            | 20      | bigger models look much better, since detail under ~2 m is lost to the 1 m beam thickness |
| `--tolerance`       | 0.5     | how far (m) a straightened beam may stray from a curve. Higher = fewer, longer beams |
| `--spacing`         | 1       | gap between parallel beams on flat areas. 1 = closed surface, 2+ = open lattice |
| `--ring-spacing`    | 1       | the same for contour slices |
| `--hatch`           | auto    | beam direction on flat areas: `auto`, `level` (always horizontal), `slope` (always down the slope) |
| `--rib-spacing`     | 0       | add ribs: beams running up the surface around the centre, this far apart |
| `--edge-angle`      | 30      | faces meeting at a sharper angle than this get an edge beam |
| `--min-plane-area`  | 6       | smaller flat areas are treated as curved |
| `--no-merge`        | off     | keep collinear overlapping beams separate |

The summary after each build shows these numbers:
- **surface coverage:** the share of the model surface touched by a beam.
- **mean deviation:** how far, on average, the surface is from the nearest beam.
- **stray beam length:** how much of the beams sits more than 1 m off the surface.

Use them to compare settings.

### The 1 m grid and diagonals

Every beam starts and ends on the 1 m grid, because nudges are whole metres. Long beams can
still point in almost any direction: a 40 m beam's end can be any grid point, so its angle
is accurate to about 1.5°.

`--directions 26` (voxel and wireframe) restricts beams to the 26 directions of a 1×1×1 cube:

| step              | example vector | beam length per step |
|-------------------|----------------|----------------------|
| cube edge         | `(1, 0, 0)`    | 1.00 m               |
| face diagonal     | `(1, 1, 0)`    | 1.41 m               |
| space diagonal    | `(1, 1, 1)`    | 1.73 m               |

## Calibration (do this first, in a test world)

1. Build a small test plan:
   `python -m satisfactory_printer build examples/cube.stl --size 3 --mode wireframe -o out --open`
2. Stand on a foundation. Select the beam, aim at a fixed spot on the floor (the anchor),
   and **don't move** while the script runs.
3. Check these and fix them in a copy of `config.example.json`, then pass it with `-c`:
   - **Axis directions.** Nudge keys follow the camera. If the model comes out mirrored or
     rotated, swap the entries in `keys`.
   - **Macro.** If your game needs a different key order, edit `beam_macro`. Tokens:
     `key:lock`, `nudge:shape`, `nudge:place`, `click`, `wait:0.5`.
   - **Timing.** If key presses get lost, lower the typing speed (`--kps`, or the box in the
     preview) or raise `click_delay` / `beam_delay`.
   - **Limits.** `max_beam_length` (default 40 m, the in-game maximum) splits longer beams.
4. **Keep the line of sight clear.** If a built beam ends up between your camera and the
   anchor, the crosshair hits that beam and the anchor moves. By default the model starts 2 m
   *beyond* the anchor (`auto_offset_gap`). You can also set `model_offset` to `[x, y, z]`.

## Tests

```
python -m pytest tests
python examples/make_examples.py   # regenerates examples/*.stl (cube, pyramid, torus, house, ramp, sphere)
```
