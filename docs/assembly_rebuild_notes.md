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

- ~~Tab came out as a second body~~ → `sheet_metal_tab` (fork). A Tab is
  `InsertSheetMetalBaseFlange2(..., Merge=True, UseFeatScope=False, UseAutoSelect=True)` on a closed
  sketch on a face of the sheet-metal body (shows as "Tab<n>", type SMBaseFlange); solidpilot's
  base_flange leaves Merge off. Thickness / radius / K come from the part's Sheet-Metal feature.
  Swapping the RB gusset's boss-extrude for Tab1 kept all 15 assembly mates resolved.
- Sheet-metal parameters must be copied too, not only geometry: the originals use bend radius
  1.3208 mm, K 0.45 (read with `dump_feature`); `set_feature_properties` edits them in place.
- ~~No weldment structural members~~ → `insert_structural_member` (fork). Second run: tube rebuilt
  as a real weldment (Weldment feature + TS5x5x0.25 member on a 4000.5 mm path line + 3 cuts, which
  SolidWorks makes as weldment cuts "ICE"); config `DEFAULT<As Machined>` like the original;
  `compare_parts` PASS exact; assembly re-mated, all 6 transforms identical to the original.
- `add_edge_feature` with `edges_json` coordinates failed on the 4th of 4 edges; `edge_indices` works.
  `edge_indices` must be a JSON array string ("[0]").
- `add_assembly_mate` (solidpilot) has only coincident / concentric / distance → `add_mate_faces`
  (fork) adds parallel, perpendicular and width, and picks faces per component (no confusion
  between coincident faces of different components).

## Weldment API facts (read from sldworks.tlb / a SolidWorks-made member, SW 2025)

- `IFeatureManager.InsertStructuralWeldment5(Path, ConnectedSegmentsOption, AllowProtrusion, Groups,
  ConfigurationName)`; Groups = VARIANT array of `IFeatureManager.CreateStructuralMemberGroup()`
  objects whose `Segments` = VARIANT array of sketch segments. The tool inserts the Weldment feature
  first if the tree has none. The path sketch must not be in edit mode.
- Original member settings: profile `...\weldment profiles\ANSI eq\Tube (square)\TS5x5x0.25.sldlfp`,
  ConnectedSegmentsOption 1, AllowProtrusion true, group Angle 0, ApplyCornerTreatment true,
  CornerTreatmentType 1, MirrorProfileAxis 1, AlignAxis 1.
- Feature-definition objects often expose no typeinfo (GetTypeInfo → "Invalid index"): read them by
  interface name from sldworks.tlb (`dump_feature` does this; `sw_api` lists any interface).
- Default profile placement on a vertical line in the Right Plane gives the profile sides aligned
  with X/Z (no rotation needed for a square tube).
