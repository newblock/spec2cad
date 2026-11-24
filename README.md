# spec2cad — One spec, deterministic part

English | [简体中文](README.zh-CN.md)

Compile a **human- or LLM-written specification (spec)** into a **STEP** file, and cross-validate it on the spot against two independent sources (the L1 gate).

```
spec.toml ──compile──▶ solidcraft command script ──run──▶ part.step
             │                        │
             │ analytic expectations  └── kernel self-check ──┐
             │ (volume/bbox/hole                              │
             │  positions derived by                          ├── L1 assertions (consistency gate)
             │  formula, never copied)                        │
             └────────────────────  FreeCAD/OCCT independent read-back ┘
                                                             ▼
                                              report.json (itemized PASS/FAIL + deviations)
```

If an LLM is involved at all, it only handles "plain language → spec"; geometry is computed by the kernel, never guessed by a model.

## Why this path

| Path | Deterministic | Time | Reproducible | Testable | Verdict |
|---|---|---|---|---|---|
| **spec → command script → `solidcraft-cli run`** | Fully | **0.1–0.4 s/part** | Byte-level | CI-friendly | **Main path** |
| LLM emitting STEP text directly | — | — | — | Syntax/topology always breaks (both kernels rejected it in tests) | Rejected |

The division of labor: **the LLM only writes the spec (parameterized text); the kernel computes the geometry**. Expected values in the spec are derived analytically (`V = L*W*T − n·π·(D/2)²·T`), never copied from measured results — otherwise the gate degenerates into "copying the answer key".

## Quick start

```bash
# 1) Install the kernel (once; prebuilt for Linux x86_64 / macOS universal)
./tools/install-solidcraft.sh

# 2) Build + dual-source verification
PYTHONPATH=src python3 -m spec2cad build spec/examples/plate3h.toml -o out/

# Artifacts
out/plate3h.step           # Geometry source of truth (AP242)
out/plate3h.commands.json  # Compiled kernel script (diffable / replayable)
out/plate3h.report.json    # Itemized L1 assertion report
```

Measured on this machine (macOS arm64, solidcraft-cli 0.47.0 + FreeCAD 1.1.3 oracle):

```
spec   : spec/examples/plate3h.toml  (7 kernel commands)
STEP   : out/plate3h.step  (18190 bytes, 0.187s)
L1 gate: 24/24 passed
```

24 assertions = 13 kernel-side (volume / six bbox faces / hole count / closed / body count) + 8 oracle-side (volume / three sizes / hole count / valid / solid count) + one hole-position assertion per hole (L1.5) + one cross-source check.

## The L1 gate

Three sources must agree; missing evidence does not count as passing:

| Source | What it is | Tolerances |
|---|---|---|
| expected | Derived analytically from the spec (never copied) | — |
| kernel | solidcraft measures its own output | volume rel 1e-6; bbox abs 1e-6 mm; cylinder count exact |
| oracle | FreeCAD/OCCT reads the same STEP back and measures independently | same; hole-axis position abs 1e-6 mm (L1.5) |

Assertion list (each item lands in report.json):

| Assertion | Checks |
|---|---|
| `volume@kernel` / `volume@oracle` | volume vs analytic expectation (rel 1e-6) |
| `size_x/y/z@*`, `bbox_min/max_*@kernel` | bounding box, face by face (abs 1e-6 mm) |
| `cylinders@kernel` / `cylinders@oracle` | cylindrical-face count == expected hole count (+ outer rim), exact |
| `closed@kernel` / `valid@oracle` | solid is closed / valid |
| `bodies@kernel` / `solids@oracle` | exactly 1 solid |
| `hole@(x,y)` (L1.5) | each hole: OCCT cylinder axis coincides with the spec-declared position (1e-6 mm) |
| `volume@kernel==oracle` | the two sources agree with each other |

**Why L1.5 is a separate assertion**: if a hole moves but stays inside the solid, both volume and hole count stay unchanged — only the position assertion catches a part that is "right by volume, wrong by holes".

### Negative example (the gate's teeth)

`spec/examples/plate3h-broken.toml`: a hole center is written outside the plate boundary; the volume is off by only a quarter of one hole — looks fine to the eye, but the gate catches it. Measured:

| Assertion | Expected | Actual | Result |
|---|---|---|---|
| volume@kernel | 10821.902755 | 11607.300918 | FAIL |
| cylinders@kernel | 3 | 2 | FAIL |
| volume@oracle | 10821.902755 | 11607.300918 | FAIL |
| hole@(-15.000,0.000) | axis coincides | no matching cylinder face | FAIL |

Exit code 1. **The gate does not go soft**: geometry that looks normal but measures wrong still fails.

## Benchmark set (11 specs / 10 positive + 1 negative)

```bash
PYTHONPATH=src python3 -m spec2cad bench spec/examples -o out/bench
```

Measured on this machine (2026-11; all three sources agree; the blocked negative counts as expected):

| spec | Negative | Expected volume | Kernel | OCCT | Cyl. faces | Time | Result |
|---|---|---|---|---|---|---|---|
| blind-plate | no | 23660.708 | 23660.708 | 23660.708 | 1 | 98 ms | PASS |
| boss-plate | no | 17428.672 | 17428.672 | 17428.672 | 1 | 140 ms | PASS |
| bracket-l | no | 21600.000 | 21600.000 | 21600.000 | 0 | 79 ms | PASS |
| fillet-plate | no | 11845.487 | 11845.487 | 11845.487 | 4 | 337 ms | PASS |
| flange | no | 45553.093 | 45553.093 | 45553.093 | 7 | 385 ms | PASS |
| plate-boss-holes | no | 18251.593 | 18251.593 | 18251.593 | 5 | 332 ms | PASS |
| plate-grid | no | 22190.443 | 22190.443 | 22190.443 | 6 | 309 ms | PASS |
| plate3h-broken | yes | 10821.903 | 11607.301 | 11607.301 | 2 | 130 ms | Blocked (as expected) |
| plate3h | no | 10821.903 | 10821.903 | 10821.903 | 3 | 179 ms | PASS |
| stepped-shaft | no | 17954.202 | 17954.202 | 17954.202 | 2 | 160 ms | PASS |
| washer | no | 12723.450 | 12723.450 | 12723.450 | 2 | 144 ms | PASS |

**11/11 as expected**: 10 parts agree across all three sources (analytic = kernel = OCCT, within the 1e-6 gate), 1 negative is blocked. Output STEP is AP242 (`FILE_SCHEMA AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`).

## Analytic model (calibrated by measurement)

| op | Volume | Bounding box | Cyl. faces |
|---|---|---|---|
| `box` | ±L·W·H | corner `at..at+(L,W,H)` / centered `at±(L/2,W/2,H/2)` | - |
| `cylinder` | ±π·r²·h | **base is the bottom-circle center**: xy ±r, z from `base_z..+h` | +1 |
| `holes` | −n·π·r²·d (d = plate thickness or blind depth) | unchanged | +n |
| `fillet` (vertical edges) | −4·r²·(1−π/4)·H | unchanged | +4 |

## Spec format (v1)

```toml
[part]
name = "plate3h"
units = "mm"

[params]                 # dimensions: the only place numbers may appear in a spec
L = 60.0
W = 40.0
T = 5.0
D = 10.0

[[build]]
op = "box"               # box; anchor="corner" pins the corner at `at` / "center" centers it
length = "L"
width = "W"
height = "T"

[[build]]
op = "holes"             # holes: explicit coords / circular / grid; through or blind depth
face = "XY"
diameter = "D"
through = "T"            # through must equal the total height in that direction; blind uses depth
at = [[15.0, 20.0], [45.0, 20.0], [30.0, 10.0]]

# Other available ops
#   [[build]] op = "cylinder"  base = [x,y,z]  diameter/radius  height  operation = "join"|"cut"
#   [[build]] op = "fillet"    edges = "vertical"  radius = "R"
#   [build.pattern]            type = "circular"|"grid"  ...

[checks]                 # expectations are derived analytically from the spec, never copied
volume = "auto"
bbox = "auto"
cylinders = "auto"
```

Design rules:

1. **The spec is the single source of truth.** The compiler reads no environment, no previous artifacts, and guesses no defaults. Ambiguities (anchor, orientation, through vs blind) must be explicit; missing ones are errors — no silent filling.
2. **Expected values must be derived analytically**, never copied from measurements into the spec. If you find yourself tuning expectations to match reality, suspect the geometry first, not the expectation.
3. **Hole positions get their own assertion** (L1.5): volume right and hole count right is still wrong if a hole moved.
4. **A missing oracle is a FAIL, not a skip** — no evidence does not count as passing (`--no-oracle` builds without producing evidence).
5. **Zero runtime dependencies**: pure Python standard library + external binaries (solidcraft-cli; FreeCAD used only as the oracle).
6. **Negative examples must stay**. `spec/examples/*-broken.toml` are the gate's teeth; after any change to verify, re-run and confirm they are still blocked.

## Tests

```bash
python3 -m unittest discover -s tests -t . -v   # 30 tests; e2e auto-skips when the kernel is absent — never fakes green
```

Coverage: expression evaluation / spec validation (anchors, through-depth consistency, fillet preconditions, negative-example semantics) / analytic expectations / compiled command scripts / solidcraft end-to-end / L1.5 hole-position assertions / negative-example blocking.

## Known pitfalls (learned by measurement)

- `solid.box` anchors its **corner at the origin, not its center**: centering requires an explicit `anchor = "center"` (the compiler emits the translation). This is the first class of ambiguity L1 must catch.
- `solid.cylinder`'s `base` is the **bottom-circle center**, not a corner: bbox spans xy ±r. Computing it from a corner yields "volume all correct, bbox all wrong" — a correct volume does not mean a correct model.
- A hole on/over the plate boundary may change the volume by only a quarter of one hole — looks fine, measures wrong; L1 only catches this via the analytic expectation.
- Conda-installed FreeCAD is not on the default `sys.path`: the oracle probe automatically inserts `<env>/lib` and retries; you can also set `FREECAD_PYTHON` explicitly.

## Layout

```
spec/examples/     Benchmark specs (10 positive + 1 negative; the negative must be blocked by the gate)
src/spec2cad/      Spec parsing / compilation / execution / L1 verification
  spec.py            Spec parsing + analytic expectation derivation (never copied)
  emit.py            spec -> solidcraft command script (JSON, diffable / replayable)
  kernel.py          solidcraft-cli build + FreeCAD/OCCT oracle read-back
  verify.py          L1 / L1.5 assertions
  cli.py             build / bench / report
tests/             Pipeline tests (e2e auto-skips without a kernel — never fakes green)
tools/             Kernel install script
out/               Artifacts (gitignored)
```

## License

MIT (see LICENSE). The solidcraft-cli kernel is MIT OR Apache-2.0; the FreeCAD oracle (LGPL-2.1) is invoked as a separate verification process only and is not distributed with the artifacts.

---

Keywords: solidcraft, solidcraft-cli, CAD kernel, STEP export, AP242, parametric CAD, spec-driven geometry, FreeCAD OCCT verification, deterministic part generation.
