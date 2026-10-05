# Assembly rebuild — method and gaps

First run: SlipCell `ASSEMBLY - MAIN COLUMN` (anchor plate, TS5x5x0.25 tube, 4 gussets, 15 mates),
rebuilt by hand into `C:\Drawings\_rebuild\ASSEMBLY - MAIN COLUMN\` with " RB" file names.
Result: all 3 parts PASS `compare_parts` (exact topology, dV 0.0000 %); all 6 component transforms
identical to the original (`component_positions` on both).

## Method (general)

1. Learn read-only: `analyze_assembly`, `read_assembly_mates`, per-part feature tree + sketches +
   mass properties. Never write into the vault (no `save_analysis` / `rebuild_from_ir` on vault files).
2. Rebuild each part in a new document, save under a distinct file name (SolidWorks cannot open two
   documents with the same file name while the originals are open), verify with `compare_parts`.
3. New assembly: insert the base part fixed at the origin, the rest anywhere (insert x/y/z is the
   component ORIGIN, not the bounding-box centre).
4. Mate with `add_mate_faces`: each face = component instance + a point ON the face in assembly mm,
   interior (away from edges and holes). Read `component_positions` after every mate and compute the
   next face points from the current transform (row-vector convention: assembly = local · R + t,
   R rows = images of local X, Y, Z).
5. Contact faces: coincident, `anti_aligned`. Order: the floor contact first (fixes the up axis),
   then the wall contact (the solver then rotates about the up axis only — 90°/180° flips solved
   cleanly), then width.
6. Width: faces 1–2 = the outer pair (e.g. tube sides), faces 3–4 = the centred pair (gusset sides).
7. Compare `component_positions` of the rebuild with the original.

## Gaps found in the tools

- `sheet_metal_feature(base_flange)` on an existing sheet-metal body makes a second body instead of
  a Tab. Workaround: merged boss extrude of the same profile (into the material).
- No weldment structural members: tube rebuilt as an extruded rounded-square profile (geometry
  exact, but no cut list / weldment properties).
- `add_edge_feature` with `edges_json` coordinates failed on the 4th of 4 edges; `edge_indices` works.
  `edge_indices` must be a JSON array string ("[0]").
- `add_assembly_mate` (solidpilot) has only coincident / concentric / distance → `add_mate_faces`
  (fork) adds parallel, perpendicular and width, and picks faces per component (no confusion
  between coincident faces of different components).
