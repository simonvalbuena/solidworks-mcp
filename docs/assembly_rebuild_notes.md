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

## Sketch definition — Simón's conventions (read with `read_sketch` from the MAIN COLUMN originals)

Every sketch fully defined, with as few dimensions as possible; relations carry the intent.

- **Origin first.** A vertex on the origin (gusset, tube path), or the profile centred on it
  (plate: side midpoints vertical/horizontal-aligned with the origin; a center rectangle's center
  point coincident with the origin is the equivalent used in the RB rebuild).
- **Point-to-point dimensions** between vertices / hole centres, not line-length dimensions.
- **Equal + one size.** Repeated holes / notches: equal relations and ONE Ø or width/depth dimension.
- **Symmetry / midpoints instead of position dims.** Construction lines carry the layout: plate holes
  sit at the midpoint of construction diagonals from a 152.4 construction square to the plate
  corners (no hole-position dimension at all); hole triplets use a cross construction line with the
  centre hole at its midpoint and a 63.5 spacing on one triplet, the middle triplet at the midpoint
  of an axis construction line.
- **Features on an edge reference the edge.** Tab corners coincident with model edges (bottom edge,
  hypotenuse); notch outer lines collinear with the outline sketch's lines; only width/depth dimensioned.
- **Hole rows**: centres aligned (vertical/horizontal point relations) with each other / the origin;
  spacings baseline from the first hole (111.125, 698.5 from hole A); end holes from the tube END
  EDGE (76.2) or from the origin (457.2).
- In-context originals: some tube circles are converted edges (useedge) from outside references;
  in a standalone rebuild they become dimensions from the origin / the tube end.

`define_sketch` reproduces all of this: relations via ISketchRelationManager.AddRelation (no
selection needed; the origin is the OriginProfileFeature's sketch point), refs to model edges,
planes and other sketches, dimensions with the Modify dialog suppressed. RB result: all 9 sketches
fully defined, all 3 parts PASS exact vs the originals, assembly transforms unchanged.
Open: K-factor 0.45 does not apply through ModifyDefinition (custom bend allowance) — RB parts
keep 0.5 (no bends, geometry unaffected).

## Third run: `RAIL - HAT SECTION ASSY - LONG` (rail + 4 brackets, 6 mates, 2 mirrors)

Rebuilt into `C:\Drawings\_rebuild\RAIL - HAT SECTION ASSY - LONG\`. Rail RB: `compare_parts` PASS
exact (topology 1-407-1133-678, dV 0.0000 %), 26 features with the original names and order, every
sketch fully defined, A36. Bracket RB PASS exact. Assembly RB: same 6 mates (types, alignments,
entity kinds, names), MirrorComponent1 (Right Plane) + MirrorComponent2 (Front Plane), all 5
component transforms identical to the original.

Lessons / tool changes:
- **Point alignment**: `AddRelation([pt, pt], VERTICAL)` keeps only the first point (a dangling
  one-point relation) — rows/columns of slots stayed free. Use VERTPOINTS/HORIZPOINTS (26/25);
  define_sketch now maps point-pair vertical/horizontal automatically and deletes dangling relations.
- **Big sketches** (64 slots, 640 relations): each AddRelation re-solves the whole sketch (~2 s);
  calls exceed the 60 s device link timeout but finish server-side — wait, then check with
  `read_sketch(summary=true)` (counts + not-fully-defined entities, seconds instead of minutes).
- **Slots**: primitives (centre line + 2 arcs + 2 lines) via solidpilot add_sketch_entity, then
  tangents ×4, vertical centre line, equal arcs/centre lines to one master slot with R + length.
- **Sketch on thin faces** (3 mm flange tips): solidpilot create_sketch's view-pick fails →
  `sketch_on_face` picks the face geometrically and returns the sketch frame.
- An empty sketch is discarded when another tool closes it — add the first entity in the same
  session (sketch_on_face / solidpilot add_sketch_entity) before calling fork tools on it.
- solidpilot `rectangle` adds construction diagonals + centre point (fine for a window located by
  its centre; `cleanup_sketch` removes them where the original has plain lines).
- Edge refs (`{"edge": [x,y,z]}`) scan every body edge — slow on a part with 1000+ edges.
- In-context originals (converted edges, external points) become dimensions from the origin or
  relations to the part's own edges (wall end edge midpoints, bend tangent edges) in the rebuild.
- Feature/mate/instance names: rename at the end (temp names first to avoid collisions);
  mirrored instance numbers are renamed to match.
