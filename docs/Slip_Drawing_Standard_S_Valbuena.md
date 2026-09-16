# Slip Robotics — Fabrication Drawing Standard (S. Valbuena conventions)

Derived from two sources: (a) 241 released/prototype PDFs authored or approved by S. Valbuena in the SlipLift (2025–2026) and Chassis (2021–2025) manufacturing packages, and (b) a SolidWorks-level inspection (`inspect_drawing`, Sept 2026) of all 167 drawings in the PDM vault folders `PROGRAMS\TRAY AND T-BOT SOLUTION\MODULES\{CHASSIS, MOTOR MODULE, UI - COMMUNICATIONS MODULE, EXTERNAL COVERS}` and `PROGRAMS\Arch 3.0\MODULES\{CHASSIS, MOTOR MODULE, EXTERNAL COVERS}` — view types, referenced configurations, orientations, scales, tangent-edge settings, dimension types, notes, tables, welds. Per-drawing findings are in `docs/learning_ledger.md` (fork) / `C:\Drawings\_learning\ledger.md`.

Authority order: Simón's own 2026 drawings (EOR/APPROVED S. VALBUENA, May–Aug 2026) define the standard. His 2024–2025 drawings show the lineage. Other authors' dialects (S. Kovar covers/kits, D. Dziak, C. Cheedalla, Y. Won, J. Jakomin) are recorded in §10 as context only — do not adopt them.

## 1. Sheet format and title block

- Sheet size D (34 × 22 in, 863.6 × 558.8 mm) for every current drawing, including tiny brackets. Template `FORMAT SR SHT1 D 2025.slddrt` on sheet 1 and `format sr sht2+d 2025.slddrt` on every continuation sheet (reduced title block: DWG NO / REV / SCALE: NONE / SHEET n OF m). Third-angle projection.
- The SHEET scale is set per drawing (2:1 for tiny parts up to 1:16 for the chassis weldment) but is never printed: the title block reads SCALE: NONE and every view carries its own scale (1:1, 1:2, 1:3.25, 1:4 …). Only detail/section views print a "SCALE 1 : n" label.
- Slip Robotics title block (2025+ template), every field property-linked (`$PRPSHEET`): LEGACY P/N ("-" or "N/A-N/A"), MODULE code, COMPONENT code, VARIANT (N/A or "-"), ENGINEER OF RECORD + date, APPROVED BY + date, TITLE, DWG NO. (6-digit P/N), REV, WEIGHT (LBS), SHEET n OF m, MATERIAL / THICKNESS / FINISH from the model's custom properties. Fill the MODEL properties, not the drawing text.
- Drawing type banner above the title block (`TYPE` property): FABRICATION DRAWING (parts), WELDED ASSEMBLY DRAWING (weldments), ASSEMBLY DRAWING (bolted/top-level assemblies), INSEPARABLE ASSEMBLY DRAWING (PEM-nut / hardware-install sub-assemblies). COTS, KIT and DECAL types exist in the vocabulary (Kovar) — not part of Simón's own output.
- MODULE codes: CC chassis, AA actuation, CM comms/UI, CE external covers, MM motor module, SM safety module, PW power. COMPONENT: M = machined/fabricated part, W = weldment, A = assembly (K = kit, D = decal in Kovar's drawings).
- Revision block top-right is a SolidWorks **General Table** (not a Revision Table): DESCRIPTION | DATE | APPROVED BY (DRAWN BY on the newest R00 drawings) | REV; one row per revision. Rev scheme: R00 = initial release (convention since May 2026, 108143); R01, R02 … follow. Older drawings show P01… prototypes, "1"/"1.1"/"X" — do not reuse. Release row reads "INITIAL RELEASE" or "ENG RELEASE"; later rows are short plain-English change summaries ("ADDED COVER HOLES", "INCREASED THRU HOLE SIZE TO .655").
- Default tolerance block (never edited): 3 PLACE DECIMAL ± .005 / 2 PLACE DECIMAL ± .02 / 1 PLACE DECIMAL ± .1 / ANGLES ± 0.5°. "ALL SHEETS ARE THE SAME REVISION STATUS." Copyright line bottom-left.
- Legacy Arch 3.0 template (SolidWorks stock B-size title block, real Revision Table with revs 1/2/3, material as a free note, ±1° / ±.06 / ±.02 / ±.005 block, ASME Y14.5-2009, CC-M/CC-W part numbers) is obsolete — do not reuse. Its view grammar is the ancestor of this standard (§10).

## 2. Notes block (top-left, sheet 1 only)

The Slip template's notes block is ONE note with SolidWorks paragraph markup (auto-numbered `<PARA number=on>` paragraphs separated by blank `number=off` paragraphs); the header "NOTES:" is a separate note. Some drawings carry the older two-note variant (a "1. 2. … 7." numbering note beside a text note) — new drawings use the single auto-numbered note. Numbering is continuous; part-specific notes go after the boilerplate. EVERY note ends with a period, including FINISH ("FINISH: NONE.", "FINISH: POWDER COAT, JET BLACK, WRINKLE GLOSS, RAL 9005.") — the template's FINISH line has none; `finish_slip_r00` adds it (Simón, 108538 review 2026-09-09).

1. INTERPRET ALL DIMENSIONS AND TOLERANCES PER ASME Y14.5-2018. ALL DIMENSIONS ARE IN INCHES UNLESS OTHERWISE SPECIFIED.
2. MATERIAL: <spec>, <thk> IN THK, (<bend radius> IN BEND RADIUS).   — template form `$PRPSHEET:"MATERIAL", $PRPSHEET:"THICKNESS" IN THK, ($PRPSHEET:"BEND_RADIUS" IN BEND RADIUS).`; the bend-radius fragment only on formed parts, always in parentheses; flat parts end at "IN THK."; tubes replace the THICKNESS fragment with the section (§5c); round stock names the stock form (§5d); 3D prints / plastics name the material and colour only.
3. FINISH: NONE.   — `$PRPSHEET:"FINISH"` only (never "NONE, NONE" — drop the FINISH_COLOR link). Powder coat wording: POWDER COAT, JET BLACK, WRINKLE GLOSS, RAL 9005 / POWDER COAT, JET BLACK, MATTE, RAL 9005 / POWDER COAT, SAFETY YELLOW, GLOSS, RAL 1007.
4. BREAK AND DEBURR ALL SHARP EDGES AND CORNERS.
5. INFORMATION ENCLOSED IN PARENTHESIS IS FOR REFERENCE ONLY.
6. WHERE APPLICABLE, PROVIDED CAD FILES FOR <PN>-<REV> SHALL SUPPLEMENT FOR NON-DIMENSIONED AREAS.   — bold the PN-REV.
7. (only when REV > initial) WHERE SHOWN "⟨7⟩" DENOTED LOCATIONS WHERE MODIFICATION HAVE TAKEN PLACE UNDER LATEST RELEASED REVISION NUMBER <REV>.   — the note number sits inside a hexagon balloon note (balloon style 3) with a leader to every changed feature; a plain "nX" note beside the flag when a pattern changed. Delete this paragraph on R00.

Optional part-specific notes, in this style:
- MASK AREAS SHOWN FROM FINISH. (only when the part is finished — delete the template paragraph otherwise) / MASK ALL TAPPED HOLES IN ITEMS … DURING COATING. / MASK A 2" DIAMETER AREA AROUND THE INDICATED KEYED HOLE TO PREVENT POWDER COATING.
- PRESS-FIT NUTS SHALL BE INSTALLED IN ACCORDANCE WITH THE MANUFACTURER'S SPECIFICATIONS. / ALL RIVET NUTS SHALL BE INSTALLED PER MANUFACTURER'S SPECIFICATIONS, THERE IS A TOTAL OF n RIVET NUTS. / ALL RIVET NUTS TO BE MASKED PRIOR TO FINISH.
- FOR REFERENCE - ITEM n - PEM NUT (MCMASTER P/N: xxxxx).
- "SEE NOTE n" leader notes and a "TOP" label on views are used freely on weldments.

Material call-out wording as used:
- ASTM A572 GR50 STEEL, 0.25 IN THK, (0.25 IN BEND RADIUS).   [structural chassis parts, 0.25 / 0.375 / 0.75 IN]
- ASTM A36 STEEL, 0.188 IN THK, (0.251 IN BEND RADIUS).   [brackets, prototypes]
- 5052-H32, 0.063 IN THK, (0.0389 IN BEND RADIUS). / 5052-H32, 0.125 IN THK, (0.102 IN BEND RADIUS). / 5052-H32, 0.1019 (10 GAUGE) IN THK, (0.153 IN BEND RADIUS).   [aluminum brackets, covers]
- STEEL SHEET, A1008 COLD ROLL, 16 GAUGE (0.0598 IN THK), (0.1365 IN BEND RADIUS).   [thin covers]
- Bend radius convention: steel ≈ 1 × thickness (0.25 → R.25, 0.375 → R.375, 0.188 → R.251); aluminum uses the tooling radius (0.063 → R.0389, 0.125 → R.102).
- Plates/machined: ASTM A572 GR50 STEEL, 0.75 IN THK. / 1.375" AISI 1045, COLD WORKED ROD / 1" TURNED, GROUND, POLISHED … KEYED SHAFT / 5052-H32, 0.375 IN THK. / DELRIN, ACETAL (POM), WHITE / PETG, WHITE, 100% INFILL (3D prints).

## 3. View grammar and dimensioning philosophy (all part types)

View structure (confirmed on all 167 vault drawings):
- ONE base **model view** (SolidWorks `AbsoluteView`, orientation `*Front`/`*Back`/`*Left`/`*Right`/`*Top`/`*Bottom`, rotated 90° when a long part should lie along the sheet). EVERY other orthographic view — including the thin edge view that carries (thk) — is a **projected view** (`UnfoldedView`) from it, so the views stay aligned in third angle. Never place a second independent model view for an ortho face. (Some drawings have the edge view as the base and the main view projected from it — still all linked.) A far-face view may itself be projected from a projected view.
- Isometric = a separate small model view (`*Isometric`, occasionally `*Trimetric`), 1:4–1:8 relative to the main view, top-left or right of the sheet; unlabeled. On some drawings it is parked off-sheet (x < 0) — it then does not print; on-sheet is preferred.
- Display: Hidden Lines Removed on every view. Tangent edges REMOVED on all orthographic views, VISIBLE (solid, not "with font") on the isometric only.
- Section and detail views appear on machined round parts (Section A-A) and on weldment region sheets; not on ordinary plates/brackets.
- Views centered on the sheet, dimensions preferably above and to the left of each view (§7b).

Dimensioning:
- The CAD model is the master. Note 6 makes the STEP/DXF the authority for anything not dimensioned. The drawing therefore carries a deliberately minimal set of dimensions — only what the fabricator must inspect: overall envelope, formed flange heights, the critical holes, and anything tolerance-tightened.
- Decimal inches, no leading zero on values < 1 (".375", "Ø.339"). Three decimals on inspected/critical dims, two decimals on non-critical or loose dims (title-block ±.02 then applies), and reference values in parentheses.
- One feature per face (Simón's stated rule for laser-cut parts): on every face that carries cut features, dimension only one of them — typically one hole, located from two edges with its Ø. If that one feature is correct, the rest of the laser-cut pattern on that face is almost certainly correct too. A flat plate with 40 holes gets one located hole; a bent bracket gets one located hole on each face that has holes; a tube gets one located feature on each dimensioned face. The rest is "per CAD" via note 6. Confirmed on 108543 (2026-09-09): do NOT add lone Ø callouts for the other hole sizes. Trivial plates (2 × 2 reinforcement) and big hole-field plates (107172/107173) may carry the overall only.
- Exception — many mating holes on one face (107096 bumper plate, 107143/44 lateral beam plates, motor-module plates): switch that face to **ordinate chains from one corner** (horizontal + vertical, 0 at the corner) plus one hole callout per hole size ("6X Ø.266"). Ordinate chains are the standard answer whenever a hole field must be inspected as a whole.
- Hole-wizard holes ALWAYS get the full hole callout ("2X Ø.266 THRU ALL", "⌵ Ø.507 X 82°" countersink, "10-32 UNF THRU ALL" / "2X Ø.159 THRU ALL" tapped two-line, counterbore with depth symbol on machined plates). Plain cut holes get "Ø.xxx" (with "nX" prefix when a pattern is meant). Clearance sizes used: Ø.159 (#6), Ø.201 (1/4-20 tap drill), Ø.25/.266 (1/4 clr), Ø.313/.339 (5/16 clr), Ø.422 (3/8 clr), Ø.531/.655 (1/2 clr), Ø.945 (connector).
- Reference overall in parentheses when the size is driven by a mating part or derived from the bend geometry: (52.99), (7.4375), (4.875), long bent brackets' overall on the long view. Chamfers get a chamfer dimension ("0.125 X 60°"); non-90° flanges get the flange length along the flange plus the angle.
- Explicit tolerances used, in order of frequency: ±.032 (formed flange heights and bend-to-hole dims), ±.010 (fit-critical hole spacing on weldments; Jakomin's rev-2 hole callouts), ±.005/±.003 (bearing/shaft fits), ±.063 / ±.125 (large weldment envelope dims), ±.5 / ±.06 on angles and loose features. Anything without an explicit tolerance rides the title block.
- Thickness is shown once, in parentheses, on the projected edge view: (.250), (.125), (.063). Bend radius also in parentheses on the edge view: (R.250). Thickness parentheses are a Simón rule — Dziak/Cheedalla leave it plain; do not copy that.
- Revision flags: hexagon balloon note (style 3) containing the note number, leader to the changed feature; "nX" note beside it when a whole pattern changed.

## 4. Sheet-metal / bent parts — the two-sheet convention

Sheet 1 (formed state):
- Notes, rev block, title block. Views of the FORMED part: main face model view, projected face views for every face that carries features, the projected end/edge view showing the flanges, plus the small isometric.
- Main view: overall envelope (length × height, 3-place) + ONE located hole (X, Y, Ø). Each projected face view: ONE located hole on that face (X, Y, Ø). End view: flange length(s) — from the outside face to the flange tip, ±.032 when it mates — (R.25) bend radius in parentheses, (thk) in parentheses. Slots and remaining holes are not dimensioned. Small mating brackets (camera brackets, 0.063 5052) may carry a few more mating features per face when every one of them mates.
- Bend angle is not dimensioned when 90°; non-90° bends carry the angle (45°, 128.23°).

Sheet 2 (flat pattern):
- Single flat-pattern view: in SolidWorks this is a **model view of orientation `Flat pattern` referencing the configuration `<cfg>SM-FLAT-PATTERN`** (e.g. `DefaultSM-FLAT-PATTERN`) — the part must have its flat-pattern configuration created (Insert → Flat Pattern / the Flatten toggle once), otherwise the view silently reverts to the formed configuration on reopen (108544 lesson: check `view_geometry` height and circle count against the first pass). Same orientation as the face view on sheet 1 (rotate 90° / 180° as needed so it matches), no notes, no iso, continuation-sheet title block. Dziak/Cheedalla put the flat on sheet 1 — Simón always on its own sheet.
- Bend lines as SolidWorks bend notes: "UP 90° R .25" / "DOWN 90° R .04" — direction, angle, inside radius; oriented along the bend line.
- Dimensions: overall flat length and width, and the distance from the blank edge to each bend line — either linear dims (symmetric pairs on symmetric brackets) or an ordinate chain from one edge. **2026 form: NOT in parentheses** (the 2024–25 drawings put flat and bend-line dims in parentheses — dropped). Nothing else — holes are per CAD/DXF. Two-decimal form on thin aluminum brackets (5.00, 1.88) is acceptable where ±.02 is fine; steel structural parts stay 3-place.

Three-sheet variant (iso sheet → formed → flat) belongs to Kovar's covers; use the two-sheet form.

### 4b. Inclined faces — the FACE A convention (Simón's own, since Jan 2024, confirmed in SolidWorks on 107496, 108292, 108438, 108440, 108544)

- Features on an INCLINED (non-90°) face project foreshortened in the orthographic views (a Ø1.250 hole came out as Ø.625 on 108544). Never dimension size or the along-slope position of such a feature in the formed ortho views.
- On the ortho/projected view that shows the inclination: dimension the bend angle (pick the two edges whose selection midpoints span the acute wedge — vertical leg + diagonal → 45°, not 135°) and attach a leader note **"FACE A"** to the inclined face's edge.
- Add a **normal view**: a model view of orientation `Current Model View` (an `AbsoluteView`, NOT a SolidWorks auxiliary view), same scale as the main view, rotated (0° / 270°) so the face sits upright, tangent edges as on other orthos (removed; 108440), labelled with a plain note **"FACE A - NORMAL VIEW"** below it. It carries the one located feature of that face (X, Y, Ø) at true size. Origin: CE-M240119-15/16 (Jan 2024) used "NORMAL VIEW TO FACE A - DETAIL"; by Jul 2026 it is the direct labelled normal view.
- Two inclined faces → FACE A and FACE B, one normal view each (108440).
- Formed-view profile stays: overall width and height, (thk), (R), the bend angle, flange/leg lengths to the virtual sharp.
- Tooling: `normal_to_face_view` creates a named view `FACE_A_NORMAL` in the part and inserts it with `CreateDrawViewFromModelView3` (works — "Drawing View8" on 108544); the along-flange dimension to a bend tangent line is not selectable through the API (the 2.478 on 108544 is a projected distance — flag for review).
- A mirrored part (MirrorStock of another) may show its feature face on the BACK: use the back view as the main view and rotate the projected top view 180° so the two stay aligned.

## 5. Flat laser-cut plates (no bends)

- One sheet. Face model view + projected thin edge view with (thk) in parentheses + small isometric.
- Dimensions: overall length and width (3-place), ONE hole located from a corner (.378 / 3.750, Ø.255) — even when the plate has dozens of holes and several hole sizes — plus hole-wizard callouts ("9X Ø.201 THRU ALL / 1/4-20 UNC THRU ALL") and any threaded, reamed, counterbored or countersunk features. Everything else per CAD. Trivial plates: overall only. Hole-field plates that must be inspected as a field: ordinate chains + one callout per size (§3).
- Heavy plates (0.75 IN A572) same scheme; machined post-ops (tapped holes, counterbores with depth symbol, "2X R.020 MAX" leaders) get explicit callouts, a Section for bores.
- 3D-printed parts (PETG/PET, "PETG, WHITE" material note) are drawn exactly like plates: iso + main model view + projected faces, overall + one hole per face, wall sizes as needed, **no thickness dim** and no THICKNESS fragment in the material note.
- Machined plastics (Delrin spacers): iso + main + projected + Section A-A, countersink callouts.

## 5b. Laser-cut structural tube (HSS / square and rectangular tube) — from CC-M240711-11 R4 (legacy) and 107276-P01 (current template)

- One sheet, FABRICATION DRAWING. Views: one long side view of the tube per featured face plus an end view of the tube section; break lines (zig-zag) are used freely so a 200 in beam fits with a break in the middle. Small iso.
- Material note wording — current: MATERIAL: A500 SQUARE STEEL TUBE, 2 IN X 2 IN X 0.25 IN (title-block material ASTM A500 GR B or GR C). Legacy: MATERIAL TO BE 5" X 3" X 0.120" THK STEEL RECTANGULAR HOLLOW TUBING. RADIUS TO BE LESS THAN 0.5" — carry the corner-radius limit over as "(CORNER RADIUS 0.5 IN MAX)" when a mating flat part sits on the tube face.
- Tube stock section is reference only: end view carries (3.000) and (5.000), or (□2.00), in parentheses — the mill controls it.
- Long view: overall cut length, then one feature per face — a single hole located along the length from one end and across the face with its Ø. On a symmetric part the location is taken from the end to the feature nearest the centerline, the CENTERLINE is drawn and labeled "PART IS SYMMETRIC ABOUT CENTERLINE". A simple in-line hole row may be an ordinate chain from the cut end.
- Standard tube note: ALL FEATURES ARE THRU BOTH FACES OF THE TUBE STOCK (only when true; otherwise say so per feature).
- In weldments, tubes are welded ALL-AROUND at all joints and located by tab-and-slot features; tube ends are dimensioned to the weldment envelope, not on the tube part drawing.

### 5c. Tube rules confirmed by Simón on 108538 (TUBE, MAIN 5×3×.125, 204.75 in — 2026-09-09)

- One long face view for every face that has features, none for a bare face. Stack the face views vertically sharing the length axis, end (section) view to the right at a larger scale (1:2), iso small (1:16) at the bottom.
- Break View, always, on long tubes — 0.125 in gap, vertical zig-zag break lines, the same break positions on every face view; pick the break span where no dimensioned feature lies; after the break raise the scale (1:8 → 1:6); the overall length spans the break.
- Dimension end cuts / chamfers to the VIRTUAL SHARP: angle (45°) + end height to the intersection of the diagonal and the end edge (Find Intersection), never to the relief arc or its centre. Offset small texts off their lines.
- One feature per face, located from the END and from the BOTTOM edge. Round hole → X, Y, Ø. Rounded slot → X, Y of its near edges plus length × width; slot corner radii per CAD.
- End view: (5.000), (3.000) and the wall (.125) in parentheses.
- Material note: `MATERIAL: <material>, 5" X 3" X .125" WALL RECTANGULAR TUBE (HSS).`
- Placement: side-view dims above/left; Ø leader text below the view; bottom-view slot dims above (X inner, length outer) and left (Y, then width further out).

### 5d. Machined round parts — pins, bushing housings, shafts (Motor Module 2025)

- Views: end model view + **Section A-A** (through the axis) that carries all the dims; long shafts add a Detail view (4:1) for the end chamfer; small iso.
- Section dims: every Ø feature, (Ø stock) in parentheses, chamfer angles as reference (135°/45°), "4X" prefixes for repeated features, one centerline; overall length with chamfers in parentheses when derived.
- Material note names the stock form: "1.375" AISI 1045, COLD WORKED ROD", "1" TURNED, GROUND, POLISHED … KEYED SHAFT".

## 6. Weldments (WELDED ASSEMBLY DRAWING) — 106181-R06, 107095, 107140, 107165 (current) and CC-W240711-11 R6 (legacy)

Sheet 1: notes, General-Table rev block, BOM table (`ITEM NO. | PART NUMBER | TITLE | MATERIAL | [THICKNESS] | QTY. | REVISION`, top-right), large isometric (tangent edges visible) with split balloons, side labels (LEFT SIDE / RIGHT SIDE / FRONT SIDE / BACK SIDE) when asymmetry matters. No weld symbols on the iso.
- Balloons are split circles (balloon style 7): top number = item, bottom = quantity ("ITEM IDENTIFICATION BALLOONS: TOP NUMBER DENOTES ITEM NUMBER, BOTTOM NUMBER DENOTES QUANTITY."). 2025-era drawings used plain circle balloons (style 1) — dropped.
- Rivnuts / PEM nuts appear as BOM items with the McMaster P/N in the PART NUMBER cell.
- **Every dimension on a weldment view is a reference dimension in parentheses** — (16.0625), (20.75), (91.55) — except the envelope/interface dims that carry an explicit tolerance (164.000±.125, 53.000±.063, 25.875±.010).
- Weldment notes (current wording): WELD PATTERN HAS QUARTER SYMMETRY. WELDMENT GEOMETRY IS NOT SYMMETRICAL. / CHASSIS ASYMMETRY IS DEFINED BY ITEMS … REFER TO LEFT AND RIGHT TAGS. / MATERIAL: SEE CUTLIST ON SHEET [1]. / FINISH: POWDER COAT, JET BLACK, WRINKLE GLOSS, RAL 9005. / WELD PER AWS [D8.1 (0 - 0.125 IN THK STEEL & AL), D8.3 (CHASSIS FRAME RAILS), D8.4 (BRACKETS, NON-BODY STRUCTURAL)] (simpler: WELD PER AWS [D1.1 (STEEL) OR D1.2 (ALUMINUM)]). / BREAK AND DEBURR ALL SHARP EDGES AND CORNERS. / ITEM n TO BE WELDED TO ITEMS x, y AND z. / PLUG WELD AND GRIND FLUSH. / GRIND FLUSH AND BLEND TO SMOOTH RADIUS. NO SHARP EDGES. SEE SHEET [n]. / ALL RIVET NUTS SHALL BE INSTALLED PER MANUFACTURER'S SPECIFICATIONS, THERE IS A TOTAL OF n RIVET NUTS. / MASK ALL TAPPED HOLES IN ITEMS … DURING COATING. / MASK A 2" DIAMETER AREA AROUND THE INDICATED KEYED HOLE TO PREVENT POWDER COATING. / NO TACK WELDS OR WELD BEADS PERMITTED ON ITEM n AT THE ARROWED LOCATION (nX). / FOR REFERENCE - ITEM n - WELD NUT / PEM NUT (MCMASTER P/N: …). / WHERE APPLICABLE, PROVIDED CAD FILES FOR <PN>-<REV> SHALL SUPPLEMENT FOR NON-DIMENSIONED AREAS.
- Legacy notes (still valid intent): ALL WELDS ARE TO BE MIG OR FLUX CORE WELDS. / ALL RECTANGULAR TUBE IS TO BE WELDED ALL-AROUND AT ALL JOINTS. / ITEMS … ARE TO BE LOCATED WITH THE TAB AND SLOT FEATURE. / CENTER THE WELDS WITHIN 1/2" OF THE TAB AND SLOT FEATURE. / ALL TUBES MUST BE CLEAN AND FREE FROM EXTRANEOUS PARTS. / AFTER WELDING, SURFACE PREP AND POWDER COAT BLACK.

Sheet 2: overall dimensioned TOP VIEW (base model view) + SECTION A-A. Envelope dims with explicit tolerance scaled to size; locating dims for mating interfaces ±.010; side labels; datum-establishing details (DETAIL B/C/D, 1:2 / 1:1) for flush conditions.

Sheets 3–n: one weld REGION per sheet — base model view → section views (1:8 typical) → detail views (1:4 … 1:1) for each joint; weld symbols live on the projected, section and detail views only. Item balloons repeated on every detail so each weld is item-to-item traceable.
- Fillet weld, AWS symbol, size left of the triangle in decimal inches: 0.188 on 0.25 and 0.188 plate, 0.25 on heavier joints, 0.125 on thin. Intermittent: "0.188 ▽ 2-4", "4X 0.188 ▽ 2". Bevel/groove: angle over the symbol (60°), "G" contour, "GRIND FLUSH" in the tail; plug welds via note ("SEE NOTE 10").
- Tail notes in caps: WELD INSIDE THE SLOT FEATURE / FILL RELIEF CUT - 4 WELDS AS SHOWN / .33 SLOT MUST REMAIN FREE OF WELD / CUT SLIT AND GRIND FLUSH / SEE NOTE n.
- "NO WELD" zones: a dimension with the suffix "NO WELD" plus a "NO WELD PAST THIS POINT" note.
- Flush/coplanar conditions: view notes ("NOTE: PARTS 1 AND 4 ARE FLUSH…"), parallelism FCF (// .06) legacy, profile of a surface with datum (⌓ 0.25 | A) on machine-critical faces (LIDAR mounts) on dedicated sheets.
- Dedicated sheets for masked-hole locations ("THIS SHEET IS PROVIDED TO LOCATE MASKED HOLES IN THE CHASSIS.") or rivet-nut locations ("SEE NOTE 11" details).
- Envelope tolerances: ±.125 on >150 in, ±.063 on 20–100 in, ±.010 on hole-to-hole interfaces, 90°±1° legacy squareness.

## 7. Assemblies

- Top-level bolted assemblies (TYPE ASSEMBLY, "ASSEMBLY DRAWING", 107292/107298 style): iso + BOM only on sheet 1 (MATERIAL: SEE BOM TABLE ON SHEET [1]), split balloons item/qty, exploded or iso views on later sheets, torque/fastener notes when needed. 3–5 sheets.
- Hardware-install (PEM-nut / rivnut) sub-assemblies — INSEPARABLE ASSEMBLY DRAWING: one sheet per hardware type re-using the child part's views, overall dims in parentheses, leader note "INSTALL nX <hardware> PEM NUTS IN THE CENTER MARKED HOLES" pointing at the holes, balloons for the hardware items.
- COTS spec drawings (TYPE COTS, e.g. mecanum wheel): envelope + interface dims, section + detail, a separate SPECIFICATIONS note block, rev table header DRAWN BY. Context only — Simón rarely authors these.

## 7b. Review feedback from Simón (108664, 108543, 108666, 108538 — 2026-09-08/09) — apply to every drawing

- Dimension only what controls the part: overall ENVELOPE + hole locations + hole Ø + thickness. Envelope means the full bounding box including tabs/ears (108666: 2.130 across the tab, NOT the 2.000 body width). Do NOT dimension minor tabs/notches that fall out of the DXF. One feature per face still applies to holes; a tab is not that feature.
- Exactly one fully dimensioned hole per face (X + Y + Ø). No extra Ø callouts for the other hole sizes on that face (108543).
- Placement: dimensions on the UPPER side and the LEFT side of the view's bounding box whenever clean (overall length above, overall width left, hole location above/left, Ø leader up-left); go elsewhere only when crowded.
- Keep dimension lines OUTSIDE the part's bounding box; a small location dim goes beside the part end, not through the body.
- Spacing: never touching. Inner (hole location) ≈ 22–25 mm off the view, outer (overall) ≈ 15–18 mm further; Ø leader text ≈ 25–40 mm clear of the view and any dimension line; thickness text offset so leader/text do not overlap the arrows; a tiny (.125) must have its text offset off its own line.
- Never cross dimension lines. When the top/left slot would cause a crossing, move that dimension to the opposite side (108666: 2.130 went to the BOTTOM). No-crossing outranks top/left.
- Extension lines must not cross the part: dimension to the edge segment on the same side as the dimension.
- Layout: views centered on the sheet — main face view centre around y ≈ 0.28–0.30 m on the D sheet with the edge view below and iso to the right.
- Located hole: pick the hole nearest the top-left corner.
- Display: HLR everywhere; tangent edges removed on orthos, visible (solid) on the iso.
- Delete unused template sheets (Sheet2) on single-sheet drawings.
- Sheet-metal thickness is a REFERENCE dimension, always in parentheses — set it with `set_dimension_reference`.
- Notes: no masking note unless the part is finished; drop the rev-flag note on an initial release; material line for flat parts ends at "IN THK."; FINISH: NONE.
- Edges that end in a radius are dimensioned to the virtual sharp (Find Intersection) — chamfers/end cuts into a relief fillet, tab edges, non-90° flange lengths. Never to an arc centre or tangent point.
- Check the break gap on EVERY broken view; keep chained small dims from colliding.
- Working/review copies go to C:\Drawings\<PN>.SLDDRW with <PN>-R00.pdf beside it; the release copy goes to the manufacturing package folder later.

## 8. Checklist for a new Slip drawing (Simón style)

1. D-size, `FORMAT SR SHT1 D 2025` template, MODULE/COMPONENT/TYPE properties on the MODEL, banner matches type, General-Table rev block with the R00 "INITIAL RELEASE" row.
2. Notes 1–6 verbatim (7 only for rev > initial), material line with thk and (bend radius) if formed, no THK fragment on prints/tubes-as-section, every note ends with a period ("FINISH: NONE.").
3. Sheet 1: ONE base model view + PROJECTED views for every featured face and the edge view + small iso, centered on the sheet; overall ENVELOPE 3-place dims (incl. tabs); flange heights ±.032 where they mate; (thk) and (R) in parentheses; exactly ONE located feature per face that has cut features (X + Y + its Ø) and no other Ø callouts; hole-wizard holes with full callouts; hole fields that must be inspected → ordinate chains + one callout per size. Inclined faces → angle + "FACE A" leader note + `Current Model View` normal view labelled "FACE A - NORMAL VIEW" with the face's one located feature. Tubes per §5c. Round parts per §5d.
4. Sheet 2 flat pattern: model view, orientation Flat pattern, configuration `<cfg>SM-FLAT-PATTERN`, oriented like sheet 1, UP/DOWN bend notes, overall flat dims + edge-to-bend-line dims (linear or ordinate, NOT in parentheses), nothing else.
5. Weldments: iso+BOM sheet with split balloons, every dim reference except toleranced envelope/interfaces, region sheets base → section → detail carrying the AWS fillet symbols with decimal size and length-pitch, per-joint notes, grind/mask/rivet-nut notes.
6. Rev table row added; hexagon flags (balloon style 3) on changed features with "nX" notes.
7. HLR everywhere; tangent edges removed on orthographic views, visible on the iso; delete unused Sheet2; notes cleaned; thickness in parentheses; location dims outside the part outline, above/left where clean, no crossing lines.
8. Export PDF (+ STEP + DXF for flat parts) — review copy in C:\Drawings, release copy in the manufacturing package folder "<PN>/<PN>-<REV>.pdf". Look at the PDF before declaring done.

## 9. Tooling notes — creating drawings through the MCP servers (state as of 2026-09-16)

Two local MCP servers are used together (both on Simón's PC via Claude Desktop):

- solidpilot — creates the sheet from the Slip D-size .drwdot (must live in the stock SolidWorks templates folder), places views (`add_drawing_view`, scale, positions in sheet METERS), flat-pattern views, `save_document` (SaveAs — use this for saving; the fork's `save_drawing` has a type-mismatch bug), `export_document` PDF/DXF/STEP, `analyze_drawing`. Its `add_drawing_dimension` measures only single edges with 2-place precision — avoid; never use `auto_dimension_drawing`. `create_drawing` instantiates all template sheets (Sheet2 must be deleted afterwards).
- solidworks (Slip fork of haunchen/solidworks-mcp, github.com/simonvalbuena/solidworks-mcp, branch slip-units-precision; `upstream` = haunchen; stdio server via src/server_stdio.py; requires mcp<2). Verified on SolidWorks 2025 SP5:
  - `probe_drawing_edges` → `add_dimension` (edge1/edge2 by index; linear/diameter/radius/angle; document units, 3-place precision)
  - `set_view_display` (display_mode; tangent_edges removed | fonted | visible — verified enum 0 = removed, 1 = with font, 2 = visible)
  - `list_dimensions` / `delete_dimension` / `move_dimension_text` (names like "RD3@Drawing View1@<PN>.Drawing"; positions in sheet mm); `set_dimension_reference(dimension_name, reference=True)` — IDisplayDimension.ShowParenthesis.
  - `delete_view`, `list_sheets` / `delete_sheet`; `list_notes` / `replace_in_note` / `set_note_text` / `delete_note` / `remove_note_paragraph(note_name, contains)` — the notes block is ONE note with `<PARA>` markup; `replace_in_note` accepts literal `\r\n`. Standard R00 flat-part clean-up = 2× replace_in_note (bend-radius fragment, FINISH_COLOR) + 2× remove_note_paragraph ("MASK AREAS SHOWN FROM FINISH.", "WHERE SHOWN"); or `finish_slip_r00(iso_view, finish_text?, delete_sheet2?)` does display + Sheet2 + notes in one call (tolerant of already-clean input; `delete_sheet2=False` for formed parts).
  - BATCH (src/tools/slip_batch.py): `view_geometry(view_name?, include_edges?)` — edges in SHEET mm via IView.ModelToViewTransform, outline, scale, summary {bbox_mm, envelope_edges, width_in/height_in, circles grouped by Ø with top_left index}; `add_dimensions(view, dims=[{type, e1, e2, text:[x,y], reference}])` — all dims of a view in one call.
  - VIEWS (src/tools/slip_views.py, 2026-09-10/15): `project_view(base_view, direction)` — inserts a true PROJECTED view (UnfoldedView) from the base, matching the vault grammar (use this for edge/face views, not a second model view); `normal_to_face_view(view, edge/face, label)` — creates the named model view `FACE_A_NORMAL` in the part and inserts it with `CreateDrawViewFromModelView3`, adds the "FACE A" leader note and "FACE A - NORMAL VIEW" label (works; 108544). `add_bend_dimensions(flat_view)` — bend-line selection via sketch→model→view transform; written, NOT yet verified (Simón stopped the debug 2026-09-15). `flat_bend_lines` lists bend lines.
  - INSPECT (src/tools/slip_inspect.py, 2026-09-15): `inspect_drawing(file_path?, out_path?, include_model_features=True, quiet=False)` — opens (or uses the active) drawing and writes a JSON of sheets (template, size, scale), views (feature type AbsoluteView/UnfoldedView/DetailView/SectionView, orientation, referenced model + configuration, base view, scale, rotation, position, flat-pattern flag + bend-line count, tangent-edge setting), display dimensions (type, value, prefix/suffix/callout text, parentheses, attached entity types), notes (text, leader, balloon style), tables (BOM/general/revision with cells), weld symbols, custom properties, model feature-type counts. `quiet=True` returns a one-line summary (use it — the full JSON in the tool result is too costly); big weldments exceed the 60 s device timeout but the JSON is still written. `tools_local/digest.py [--brief] <json…>` condenses a JSON for reading. Calibration: tangent map is correct (JSON carries `"tangent_map": "calibrated"`; older JSON is remapped by digest); the display-mode getter returns a constant under late binding — do NOT trust `display_mode`; break-line detection does not work (broken views report none).
  - TUBE tools (slip_tube.py): `rotate_view(view, deg)`, `break_view(view, pos1_mm, pos2_mm, orientation, gap_in, style)` (offsets from the VIEW CENTRE; sets gap → InsertBreak → BreakView), `set_break_gap`, `set_view_scale(view, decimal)`, `set_view_position(view, x, y)`, `sw_enum(name)`, `unbreak_view`. Dense views need HLR before probing and the 90 s COM timeout.
  - `add_dimension_to_intersection(view, e_line1, e_line2, e_ref, text, reference?)` — Find Intersection (sketch point at the theoretical intersection + AddDimension2). Value is right but SolidWorks pops the Modify dialog and blocks COM until ✓ is clicked — PARKED; never call it unattended. The missing hole X-locations on 108539/108548 View3/View4 wait on it.
  - `insert_detail_view(parent, center, radius, label, scale, position)` — circle lands right but the view ignores scale/position and cannot be calibrated — PARKED; apply the one-feature rule instead.
- COM binding rule (root cause of every "Member not found"): EnsureDispatch fails on this install, so pywin32 is late-bound; zero-argument SolidWorks members return their VALUE when read (`doc.GetSheetNames` is already the tuple) — use `_val()`/`_inv()` in src/tools/slip.py. Methods with arguments work as normal calls. ByRef VARIANTs are needed for `OpenDoc6`/`ActivateDoc3` errors/warnings; `Select4`/`SelectByID2` Callout arguments need `VARIANT(VT_DISPATCH, None)`, not `None`. Always ACTIVATE the target document before operating (multi-document sessions: the fork operates on `ActiveDoc`).
- Test without restarting Claude Desktop: `test_slip_live.py`, `notes_tool.py`, `dim_tool.py`, `batch_tool.py`, `diag_drawingdoc.py` run in the venv against live SolidWorks. Restart Claude Desktop (tray → Quit) only when tool signatures change; the config must be BOM-free.
- Coordinate frames: solidpilot view positions are sheet meters (D sheet = 0.8636 × 0.5588). solidworks `text_position` is sheet MILLIMETRES from the bottom-left corner. `probe_drawing_edges` returns model X and Y (mm) — pass each edge's own midpoint with its index; edge indices change on every rebuild — re-probe. `IView.Position` needs a VT_R8 SAFEARRAY.
- BROKEN-VIEW COORDINATES: `ModelToViewTransform` and `AddDimension` text points are in UNBROKEN view space; `IAnnotation.SetPosition/GetPosition` are ACTUAL sheet coordinates. `view_geometry` records the shift per broken view and `add_dimensions` converts — always think in actual sheet mm. Re-run `view_geometry` on EVERY view after a restart, move, rescale, break or rotate, before `add_dimensions`. Position views FIRST, then dimension.
- Sheet switching: `list_notes(sheet_name="Sheet2")` activates Sheet2 as a side effect. `add_flat_pattern_view` / `insert_detail_view` act on the ACTIVE sheet. Flat-pattern views come in with the length VERTICAL — `rotate_view(view, -90)` (or 180 for mirrored parts) and re-run `view_geometry`; make sure the view references `<cfg>SM-FLAT-PATTERN` (inspect_drawing shows `referenced_config`).
- The iso view can export BLANK although it lists edges: poke it with `set_view_scale`/`set_view_display` and re-export; always check the iso region of the render.
- `delete_dimension` makes SolidWorks slide the remaining outer dimensions inward into the freed slot — `list_dimensions` afterwards and `move_dimension_text` them back out. Two identical circles give identical probe data — `view_geometry` (sheet coordinates) resolves it; with probe data confirm on the PDF. A failed `add_dimension_to_intersection` can leave a stray SKETCHPOINT that prints as a dot — `delete_view_sketch_points(view)` removes it; a deleted detail view leaves its circle in the parent sketch (delete and re-create the parent view). Text nudging of a tiny (thk) dim: offset the text ALONG the dimension direction (~20 mm) so it clears the arrows.
- Verify the ENVELOPE numerically: `view_geometry` bbox/width_in/height_in must equal the dimensioned values; model-space probes cannot tell a protruding step from a recessed one.
- Sequence that worked (108538, 108635/108636): place base view → `project_view` for edge/face views → HLR → (tubes: break, gap, scale) → set positions → `view_geometry` per view → `add_dimensions` → `set_dimension_reference` on (thk)/(R) → `finish_slip_r00` → material fragment edits → save (solidpilot) → export → ONE PDF check → text nudges → re-export. ≈ 12 min per simple bracket, ≈ 35 min with an inclined face.
- Always verify by exporting the PDF and looking at cropped views; API "ok" is not proof. Verify ONCE per drawing, at the end.
- Longer-term direction (agreed 2026-09-08, "Track B"): evolve the fork toward a plan → validate → execute → verify pipeline (declarative dimension list with semantic references, expected values, save/reopen verification). `view_geometry` + `add_dimensions` + `inspect_drawing` are the first stages.

## 10. Lineage and team dialects (context — not to adopt)

- **Arch 3.0 (2023–2025)** = the previous template generation: stock B-size title block, Revision Table (revs 1/2/3), free material note, ±1°/±.06/±.02/±.005 block, CC-M/CC-W numbering. The view grammar was already the same (model view + projected views, small iso, flat-pattern sheet with overall + bend-line dims, one located hole per face, (section) in parentheses on tubes, rev-flag leaders). The 2026 standard added: D-size SR template with property-linked title block/notes, General-Table revision block, R00/R01 revs, hexagon rev-flag balloons, (thk) parentheses, hole-wizard callouts, unparenthesised flat-pattern dims, direct "FACE A - NORMAL VIEW".
- **2025 Simón drawings** (Motor Module, early SlipLift) still show: flat-pattern and bend-line dims in parentheses, circle balloons (style 1), plain "3/8-16 Tapped Hole" leader notes, iso alone on sheet 1, revs "1"/"1.1"/"X", `<T#-n>` glitches in the numbering note, "FINISH: NONE, NONE". All dropped in 2026.
- **S. Kovar (External Covers, kits, decals)**: everything ordinate (hor/vert chains from a centerline `<MOD-CL>` origin), thickness as a `0 / 0.0598 STOCK` ordinate pair, "ISOMETRIC VIEW"/"FLAT PATTERN" view labels, iso alone on sheet 1, balloon-note flags "8", gauge-based material notes, P01…P11 prototype revs then "ENG RELEASE Pnn = R01", KIT drawings with hardware items ≥100 and "n PL" quantity balloons, gasket/label-placement sheets, vinyl decal notes, PLM "auto bump" rev rows. Categories KIT / DECAL / PEM ASSY / INSEPARABLE ASSEMBLY exist in the title-block vocabulary.
- **D. Dziak / C. Cheedalla**: Simón's grammar but tangent edges left visible on orthos, flat pattern on sheet 1, more dims (seven Ø callouts on one view), thickness unparenthesised, template `<T#-5>` glitches. **Y. Won**: mixed. **J. Jakomin (Oct 2025 revisions)**: adds basic dims and ±0.010 tolerances on critical hole callouts.
- **M. Safaei** is EOR on several 2026 chassis plates approved by Simón — those follow Simón's pattern and count as standard.
