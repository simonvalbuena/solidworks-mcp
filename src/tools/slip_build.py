"""Part/assembly building blocks found missing while replicating SlipCell sub-assemblies.

feature_faces(feature)            faces a feature owns (plane / cylinder parameters, boxes, mm) — e.g.
                                  where a fillet sits, so the same edges can be picked in a rebuild
sketch_extras(sketch)             sketch text (string, position, text format) and straight slots
sheet_metal_base_flange(...)      base flange from an OPEN or closed profile: thickness, radius, K,
                                  depth with blind / mid-plane end condition, optional gauge table
sketch_slots(sketch, slots)       straight slots (centre-to-centre) + optional "same slots" relations
sketch_text(sketch, text, ...)    sketch text with height / font
extrude_cut(sketch, end, ...)     cut with blind / through all / through next / mid plane, normal cut
fillet_edges(radius, points)      constant-radius fillet on the edges closest to model points
mirror_components(...)            assembly Mirror Components (same parts, aligned to component origin)
All coordinates in mm (sketch mm for sketch tools, model mm for edges/points).
"""

from __future__ import annotations

import json
import logging
import math

from mcp.server.fastmcp import FastMCP

from errors import SWError
from sw_connection import SWConnection
from tools import slip
from tools import slip_tube
from tools import slip_weldment as sw_w
from tools import slip_sketch as sw_s

logger = logging.getLogger(__name__)
_MM = 1000.0
_END = {"blind": 0, "through_all": 1, "through_next": 2, "up_to_vertex": 3, "up_to_surface": 4,
        "offset_from_surface": 5, "mid_plane": 6, "up_to_body": 7, "through_all_both": 9}


def _ints(vals):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I4, [int(v) for v in vals])


def _r(v, n=4):
    return [round(float(x), n) for x in v]


def _close_sketch(doc):
    skm = slip._inv(doc, "SketchManager")
    try:
        if slip._inv(skm, "ActiveSketch") is not None:
            slip._inv(skm, "InsertSketch", True)
    except Exception:  # noqa: BLE001
        pass


def _rebuild(doc):
    try:
        slip._inv(doc, "EditRebuild3")
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- reading

def _face_info(face):
    row = {}
    try:
        surf = slip._inv(face, "GetSurface")
        if bool(slip._inv(surf, "IsCylinder")):
            p = list(slip._inv(surf, "CylinderParams"))
            row.update(kind="cylinder", origin=_r([v * _MM for v in p[0:3]]), axis=_r(p[3:6], 6),
                       radius=round(p[6] * _MM, 4))
        elif bool(slip._inv(surf, "IsPlane")):
            p = list(slip._inv(surf, "PlaneParams"))
            row.update(kind="plane", normal=_r(p[0:3], 6), root=_r([v * _MM for v in p[3:6]]))
        else:
            row["kind"] = "other"
    except Exception as ex:  # noqa: BLE001
        row["surface_error"] = str(ex)[:80]
    try:
        b = list(slip._inv(face, "GetBox"))
        row["box"] = _r([v * _MM for v in b], 3)
    except Exception:  # noqa: BLE001
        pass
    return row


def _feature_faces(feature_name):
    app = SWConnection.get_instance().get_app()
    doc = slip._inv(app, "ActiveDoc")
    feat = sw_w._feature(doc, feature_name)
    faces = list(slip._inv(feat, "GetFaces") or ())
    return {"status": "done", "feature": feature_name, "count": len(faces),
            "faces": [_face_info(f) for f in faces]}


def _sketch_extras(sketch_name):
    app, doc = sw_w._active_part()
    _f, sk = sw_s._sketch(doc, sketch_name)
    texts, slots = [], []
    for seg in list(slip._inv(sk, "GetSketchSegments") or ()):
        try:
            if int(slip._inv(seg, "GetType")) != 4:   # swSketchTEXT
                continue
            row = {"text": str(slip._inv(seg, "Text"))}
            try:
                c = list(slip._inv(seg, "GetCoordinates"))
                row["coordinates_mm"] = _r([v * _MM for v in c[:3]])
            except Exception as ex:  # noqa: BLE001
                row["coord_error"] = str(ex)[:60]
            try:
                row["use_doc_format"] = bool(slip._inv(seg, "GetUseDocTextFormat"))
                tf = slip._inv(seg, "GetTextFormat")
                fmt = {}
                for k in ("CharHeight", "CharHeightInPts", "TypeFaceName", "Bold", "Italic",
                          "Underline", "WidthFactor", "CharSpacingFactor", "Escapement",
                          "ObliqueAngle", "LineSpacing", "Vertical", "BackWards", "UpsideDown"):
                    try:
                        v = slip._inv(tf, k)
                        fmt[k] = round(v * _MM, 4) if k == "CharHeight" else v
                    except Exception:  # noqa: BLE001
                        pass
                row["format"] = fmt
            except Exception as ex:  # noqa: BLE001
                row["format_error"] = str(ex)[:60]
            texts.append(row)
        except Exception:  # noqa: BLE001
            continue
    try:
        for s in list(slip._inv(sk, "GetSketchSlots") or ()):
            row = {}
            for k in ("CreationType", "LengthType", "Width", "Length", "CenterArcDirection"):
                try:
                    v = slip._inv(s, k)
                    row[k] = round(float(v) * _MM, 4) if k in ("Width", "Length") else v
                except Exception:  # noqa: BLE001
                    pass
            try:
                c = slip._inv(s, "GetCenterPoint")
                row["center"] = sw_s._r(sw_s._pt_xy(c))
            except Exception:  # noqa: BLE001
                pass
            slots.append(row)
    except Exception as ex:  # noqa: BLE001
        slots.append({"error": str(ex)[:80]})
    return {"status": "done", "sketch": sketch_name, "texts": texts, "slots": slots}


# --------------------------------------------------------------------------- sheet metal

def _base_flange(sketch_name, thickness_mm, bend_radius_mm, k_factor, depth_mm, end_condition,
                 reverse_thickness, flip_direction, gauge_table_path, gauge_table_name,
                 relief_ratio):
    app, doc = sw_w._active_part()
    _close_sketch(doc)
    feat = sw_w._feature(doc, sketch_name)
    slip._inv(doc, "ClearSelection2", True)
    if not bool(slip._inv(feat, "Select2", False, 0)):
        raise SWError(f"could not select {sketch_name!r}")
    fm = slip._inv(doc, "FeatureManager")
    cba = slip._inv(fm, "CreateCustomBendAllowance")
    slip._put(cba, "Type", 2)
    slip._put(cba, "KFactor", float(k_factor))
    end = _END.get(end_condition, end_condition) if isinstance(end_condition, str) else int(end_condition)
    res = slip._inv(fm, "InsertSheetMetalBaseFlange2",
                    thickness_mm / _MM, bool(reverse_thickness), bend_radius_mm / _MM,
                    depth_mm / _MM, 0.0, bool(flip_direction), int(end), 0, 0, cba,
                    True, 1, thickness_mm / _MM / 2, thickness_mm / _MM / 2, float(relief_ratio), True,
                    False, False, True)
    if res is None:
        raise SWError("InsertSheetMetalBaseFlange2 returned None")
    _rebuild(doc)
    out = {"status": "done", "feature": str(slip._inv(res, "Name")), **sw_w._body_report(doc)}
    if gauge_table_path:
        try:
            out["gauge_table"] = sw_w._set_feature_properties(
                out["feature"], {"UseGaugeTable": True, "GaugeTablePath": gauge_table_path,
                                 "ThicknessTableName": gauge_table_name})
        except Exception as ex:  # noqa: BLE001
            out["gauge_table_error"] = str(ex)[:160]
    return out


# --------------------------------------------------------------------------- sketch content

def _slot_tool(doc, skm, slots):
    made, failed = [], []
    for k, s in enumerate(slots):
        try:
            (x1, y1), (x2, y2) = s["p1"], s["p2"]
            obj = slip._inv(skm, "CreateSketchSlot", 0, 0, float(s["width"]) / _MM,
                            x1 / _MM, y1 / _MM, 0.0, x2 / _MM, y2 / _MM, 0.0, 0.0, 0.0, 0.0, 1, False)
            if obj is None:
                raise SWError("CreateSketchSlot returned None")
            made.append(obj)
        except Exception as ex:  # noqa: BLE001
            failed.append({"slot": k, "error": str(ex)[:120]})
    return made, failed


def _slot_primitives(doc, skm, rm, slots, master):
    """Straight slot as 2 lines + 2 arcs + construction centre line (the entities the slot tool
    makes), with tangent relations and — when master is given — equal radius / equal centre-line
    length to the master slot, so one R and one length dimension size every slot."""
    made, failed, rels = [], [], 0
    for k, s in enumerate(slots):
        try:
            (x1, y1), (x2, y2) = s["p1"], s["p2"]
            r = float(s["width"]) / 2.0
            L = math.hypot(x2 - x1, y2 - y1)
            ux, uy = (x2 - x1) / L, (y2 - y1) / L          # along the slot
            nx, ny = -uy, ux                                # side normal
            m = lambda v: v / _MM  # noqa: E731
            cl = slip._inv(skm, "CreateCenterLine", m(x1), m(y1), 0.0, m(x2), m(y2), 0.0)
            a2 = slip._inv(skm, "CreateArc", m(x2), m(y2), 0.0, m(x2 - nx * r), m(y2 - ny * r), 0.0,
                           m(x2 + nx * r), m(y2 + ny * r), 0.0, 1)
            a1 = slip._inv(skm, "CreateArc", m(x1), m(y1), 0.0, m(x1 + nx * r), m(y1 + ny * r), 0.0,
                           m(x1 - nx * r), m(y1 - ny * r), 0.0, 1)
            l1 = slip._inv(skm, "CreateLine", m(x1 - nx * r), m(y1 - ny * r), 0.0,
                           m(x2 - nx * r), m(y2 - ny * r), 0.0)
            l2 = slip._inv(skm, "CreateLine", m(x1 + nx * r), m(y1 + ny * r), 0.0,
                           m(x2 + nx * r), m(y2 + ny * r), 0.0)
            if None in (cl, a1, a2, l1, l2):
                raise SWError("an entity was not created")
            pairs = [((a1, l1), 6), ((a1, l2), 6), ((a2, l1), 6), ((a2, l2), 6)]
            if master is not None and master is not (a1, a2, cl):
                pairs += [((master[0], a1), 14), ((master[0], a2), 14), ((master[2], cl), 14)]
            elif master is None:
                pairs += [((a1, a2), 14)]
            for (e1, e2), t in pairs:
                try:
                    if slip._inv(rm, "AddRelation", sw_w._variant_dispatch_array([e1, e2]), t) is not None:
                        rels += 1
                except Exception as ex:  # noqa: BLE001
                    logger.info("slot relation %s: %s", t, ex)
            if master is None:
                master = (a1, a2, cl)
            made.append((a1, a2, cl))
        except Exception as ex:  # noqa: BLE001
            failed.append({"slot": k, "error": str(ex)[:120]})
    return made, failed, rels, master


def _sketch_slots(sketch_name, slots, equal, mode, master_xy):
    app, doc = sw_w._active_part()
    feat, sk = sw_s._sketch(doc, sketch_name)
    sw_s._open_sketch(doc, feat)
    skm = slip._inv(doc, "SketchManager")
    _f, sk = sw_s._sketch(doc, sketch_name)
    rm = slip._inv(sk, "RelationManager")
    rel_ok, rel_fail = 0, 0
    if mode == "slot_tool":
        made, failed = _slot_tool(doc, skm, slots)
        if equal and len(made) > 1:
            for o in made[1:]:
                try:
                    r = slip._inv(rm, "AddRelation", sw_w._variant_dispatch_array([made[0], o]), 75)
                    rel_ok += 1 if r is not None else 0
                except Exception as ex:  # noqa: BLE001
                    rel_fail += 1
                    logger.info("sameslots: %s", ex)
        n = len(made)
    else:
        master = None
        if master_xy:   # equal to a slot made by an earlier call: its lower arc / centre line
            segs = sw_s._segments(sk)
            arcs = [(o, r) for o, r in segs if r["type"] == "arc"
                    and math.dist(r["center"], master_xy[0]) < 0.01]
            cls = [(o, r) for o, r in segs if r["type"] == "line" and r["construction"]
                   and min(math.dist(r["start"], master_xy[0]), math.dist(r["end"], master_xy[0])) < 0.01]
            if arcs and cls:
                master = (arcs[0][0], None, cls[0][0])
        made, failed, rel_ok, _m = _slot_primitives(doc, skm, rm, slots, master if equal else None)
        n = len(made)
    _close_sketch(doc)
    _rebuild(doc)
    return {"status": "done", "sketch": sketch_name, "mode": mode, "slots_made": n, "failed": failed,
            "relations": rel_ok, "relations_failed": rel_fail}


def _sketch_text(sketch_name, text, x_mm, y_mm, height_mm, font, alignment, bold):
    app, doc = sw_w._active_part()
    feat, sk = sw_s._sketch(doc, sketch_name)
    sw_s._open_sketch(doc, feat)
    st = slip._inv(doc, "InsertSketchText", x_mm / _MM, y_mm / _MM, 0.0, text, int(alignment), 0, 0, 100, 0)
    if st is None:
        _close_sketch(doc)
        raise SWError("InsertSketchText returned None")
    log = []
    try:
        tf = slip._inv(st, "GetTextFormat")
        slip._put(tf, "CharHeight", height_mm / _MM)
        if font:
            slip._put(tf, "TypeFaceName", font)
        slip._put(tf, "Bold", bool(bold))
        slip._inv(st, "SetTextFormat", False, tf)
    except Exception as ex:  # noqa: BLE001
        log.append(f"format: {str(ex)[:80]}")
    _close_sketch(doc)
    _rebuild(doc)
    return {"status": "done", "sketch": sketch_name, "log": log, **_sketch_extras(sketch_name)}


# --------------------------------------------------------------------------- features

def _extrude_cut(sketch_name, end_condition, depth_mm, reverse, flip_side, normal_cut, both):
    app, doc = sw_w._active_part()
    _close_sketch(doc)
    feat = sw_w._feature(doc, sketch_name)
    slip._inv(doc, "ClearSelection2", True)
    if not bool(slip._inv(feat, "Select2", False, 0)):
        raise SWError(f"could not select {sketch_name!r}")
    fm = slip._inv(doc, "FeatureManager")
    t1 = _END[end_condition]
    t2 = _END[end_condition] if both else 0
    res = slip._inv(fm, "FeatureCut4", not both, bool(flip_side), bool(reverse), t1, t2,
                    depth_mm / _MM, depth_mm / _MM if both else 0.0, False, False, False, False,
                    0.0, 0.0, False, False, False, False, bool(normal_cut), False, True,
                    False, False, False, 0, 0.0, False, False)
    if res is None:
        raise SWError("FeatureCut4 returned None (direction? try reverse=true)")
    _rebuild(doc)
    return {"status": "done", "feature": str(slip._inv(res, "Name")), **sw_w._body_report(doc)}


def _closest_edges(doc, points_mm):
    bodies = list(slip._inv(doc, "GetBodies2", 0, True) or ())
    edges = []
    for b in bodies:
        edges.extend(list(slip._inv(b, "GetEdges") or ()))
    out = []
    for p in points_mm:
        q = [v / _MM for v in p]
        best, bd = None, float("inf")
        for e in edges:
            try:
                r = slip._inv(e, "GetClosestPointOn", q[0], q[1], q[2])
                d = math.dist([float(r[0]), float(r[1]), float(r[2])], q)
            except Exception:  # noqa: BLE001
                continue
            if d < bd:
                best, bd = e, d
        if best is None or bd > 0.0005:
            raise SWError(f"no edge within 0.5 mm of {p} (closest {bd * _MM:.3f} mm)")
        out.append(best)
    return out


def _fillet_edges(radius_mm, points):
    app, doc = sw_w._active_part()
    _close_sketch(doc)
    edges = _closest_edges(doc, points)
    fm = slip._inv(doc, "FeatureManager")
    d = slip._inv(fm, "CreateDefinition", 1)       # swFmFillet
    slip._inv(d, "Initialize", 0)                  # constant radius
    slip._put(d, "DefaultRadius", radius_mm / _MM)
    slip._put(d, "Edges", sw_w._variant_dispatch_array(edges))
    res = slip._inv(fm, "CreateFeature", d)
    if res is None:
        raise SWError("CreateFeature(fillet) returned None")
    _rebuild(doc)
    return {"status": "done", "feature": str(slip._inv(res, "Name")), "edges": len(edges),
            **sw_w._body_report(doc)}


def _mirror_components(components, plane, orientation):
    from tools import slip_mates
    app, doc = slip_mates._assembly()
    comps = [slip_mates._find_component(doc, c) for c in components]
    pf = slip._inv(doc, "FeatureByName", plane)
    if pf is None:
        raise SWError(f"plane {plane!r} not found in the assembly")
    fm = slip._inv(doc, "FeatureManager")
    d = slip._inv(fm, "CreateDefinition", 116)     # swFmMirrorComponent
    slip._put(d, "MirrorPlane", pf)
    slip._put(d, "ComponentsToInstanceAlignToComponentOrigin", sw_w._variant_dispatch_array(comps))
    slip._put(d, "ComponentOrientationsAlignToComponentOrigin", _ints([orientation] * len(comps)))
    res = slip._inv(fm, "CreateFeature", d)
    if res is None:
        raise SWError("CreateFeature(mirror components) returned None")
    _rebuild(doc)
    return {"status": "done", "feature": str(slip._inv(res, "Name")),
            **slip_mates._component_positions()}


def _closest_face(doc, point_mm, tol_mm=0.01):
    q = [v / _MM for v in point_mm]
    best, bd = None, float("inf")
    for b in list(slip._inv(doc, "GetBodies2", 0, True) or ()):
        for f in list(slip._inv(b, "GetFaces") or ()):
            try:
                r = slip._inv(f, "GetClosestPointOn", q[0], q[1], q[2])
                d = math.dist([float(r[0]), float(r[1]), float(r[2])], q)
            except Exception:  # noqa: BLE001
                continue
            if d < bd:
                best, bd = f, d
    if best is None or bd > tol_mm / _MM:
        raise SWError(f"no face within {tol_mm} mm of {point_mm} (closest {bd * _MM:.3f} mm)")
    return best


def _last_sketch_name(doc):
    name = None
    feat = slip._inv(doc, "FirstFeature")
    guard = 0
    while feat is not None and guard < 2000:
        guard += 1
        if str(slip._inv(feat, "GetTypeName2")) == "ProfileFeature":
            name = str(slip._inv(feat, "Name"))
        feat = slip._inv(feat, "GetNextFeature")
    return name


def _sketch_on_face(point_mm, circles):
    """Open a new sketch on the planar face through point_mm (model mm, picked geometrically — no
    view-ray pick, so thin faces such as a 3 mm flange edge work) and add circles so the sketch is
    not discarded when another tool closes it. Leaves the sketch OPEN (solidpilot add_sketch_entity
    keeps drawing into it). circles: [{"c": [x, y], "d": dia}] in sketch mm."""
    app, doc = sw_w._active_part()
    _close_sketch(doc)
    face = _closest_face(doc, point_mm)
    slip._inv(doc, "ClearSelection2", True)
    try:
        selmgr = slip._inv(doc, "SelectionManager")
        sd = slip._inv(selmgr, "CreateSelectData")
        ok = bool(slip._inv(face, "Select4", False, sd))
    except Exception:  # noqa: BLE001
        ok = bool(slip._inv(face, "Select2", False, 0))
    if not ok:
        raise SWError("could not select the face")
    skm = slip._inv(doc, "SketchManager")
    slip._inv(skm, "InsertSketch", True)
    sk = slip._inv(skm, "ActiveSketch")
    if sk is None:
        raise SWError("no sketch opened on the face")
    slip._put(skm, "AddToDB", True)
    made = 0
    try:
        for c in circles or ():
            x, y = c["c"]
            r = c["d"] / 2.0
            if slip._inv(skm, "CreateCircleByRadius", x / _MM, y / _MM, 0.0, r / _MM) is not None:
                made += 1
    finally:
        slip._put(skm, "AddToDB", False)
    # frame: sketch origin and axes in model mm
    mu = slip._inv(app, "GetMathUtility")
    inv = slip._inv(slip._inv(sk, "ModelToSketchTransform"), "Inverse")
    def to_model(xy):
        pnt = slip._inv(mu, "CreatePoint", sw_s._doubles([xy[0], xy[1], 0.0]))
        a = slip._inv(slip._inv(pnt, "MultiplyTransform", inv), "ArrayData")
        return [float(a[0]), float(a[1]), float(a[2])]
    o, ex, ey = to_model([0, 0]), to_model([1, 0]), to_model([0, 1])
    return {"status": "done", "sketch": _last_sketch_name(doc), "circles": made,
            "frame": {"origin_mm": _r([v * _MM for v in o]),
                      "x_dir": _r([ex[i] - o[i] for i in range(3)], 6),
                      "y_dir": _r([ey[i] - o[i] for i in range(3)], 6)}}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def feature_faces(feature_name: str) -> str:
        """Faces a feature of the ACTIVE document owns: plane (normal, root) or cylinder (origin,
        axis, radius) parameters and face boxes in mm — e.g. to find which edges a fillet rounds."""
        return await slip_tube._run(sw, "feature_faces", _feature_faces, feature_name)

    @mcp.tool()
    async def sketch_extras(sketch_name: str) -> str:
        """Sketch text (string, position, height, font, flags) and straight slots (width, length,
        centre) of a sketch in the ACTIVE part. Complements read_sketch. Read-only."""
        return await slip_tube._run(sw, "sketch_extras", _sketch_extras, sketch_name)

    @mcp.tool()
    async def sheet_metal_base_flange(sketch_name: str, thickness_mm: float, bend_radius_mm: float,
                                      k_factor: float = 0.45, depth_mm: float = 0.0,
                                      end_condition: str = "blind", reverse_thickness: bool = False,
                                      flip_direction: bool = False, gauge_table_path: str = "",
                                      gauge_table_name: str = "", relief_ratio: float = 0.5) -> str:
        """Sheet-metal base flange in the ACTIVE part from a sketch (open profile = extruded by
        depth_mm with end_condition blind|mid_plane; closed profile = plate). Optional gauge table
        (path + thickness name, e.g. '0.12" (11 Ga.)') is applied after creation."""
        return await slip_tube._run(sw, "sheet_metal_base_flange", _base_flange, sketch_name,
                                    thickness_mm, bend_radius_mm, k_factor, depth_mm, end_condition,
                                    reverse_thickness, flip_direction, gauge_table_path,
                                    gauge_table_name, relief_ratio)

    @mcp.tool()
    async def sketch_slots(sketch_name: str, slots: str, equal: bool = True,
                           mode: str = "primitives", master_xy: str = "") -> str:
        """Add straight slots to a sketch of the ACTIVE part. slots: JSON list of
        {"p1": [x, y], "p2": [x, y], "width": w} — arc centres, sketch mm (centre-to-centre).
        mode "primitives" (default): 2 lines + 2 arcs + construction centre line per slot with tangent
        relations, and equal arc radius / centre-line length to the first slot (or to the slot whose
        p1 is master_xy = [[x, y]] from an earlier call). mode "slot_tool": ISketchManager.
        CreateSketchSlot + 'same slots' relations — it froze SolidWorks once on a long sheet-metal part,
        use with care. Batch ≤ 16 slots per call."""
        return await slip_tube._run(sw, "sketch_slots", _sketch_slots, sketch_name,
                                    json.loads(slots), equal, mode,
                                    json.loads(master_xy) if master_xy else None)

    @mcp.tool()
    async def sketch_text(sketch_name: str, text: str, x_mm: float, y_mm: float, height_mm: float,
                          font: str = "", alignment: int = 1, bold: bool = False) -> str:
        """Insert sketch text into a sketch of the ACTIVE part at (x, y) sketch mm with a character
        height and font; returns the text as read back."""
        return await slip_tube._run(sw, "sketch_text", _sketch_text, sketch_name, text, x_mm, y_mm,
                                    height_mm, font, alignment, bold)

    @mcp.tool()
    async def extrude_cut(sketch_name: str, end_condition: str = "through_all", depth_mm: float = 0.0,
                          reverse: bool = False, flip_side: bool = False, normal_cut: bool = True,
                          both_directions: bool = False) -> str:
        """Cut-extrude a sketch of the ACTIVE part. end_condition: blind | through_all | through_next |
        mid_plane | up_to_next... ; normal_cut for sheet metal. Returns body report."""
        return await slip_tube._run(sw, "extrude_cut", _extrude_cut, sketch_name, end_condition,
                                    depth_mm, reverse, flip_side, normal_cut, both_directions)

    @mcp.tool()
    async def fillet_edges(radius_mm: float, points: str) -> str:
        """Constant-radius fillet in the ACTIVE part on the edges closest to the given model points
        (JSON list of [x, y, z] mm, each within 0.5 mm of its edge)."""
        return await slip_tube._run(sw, "fillet_edges", _fillet_edges, radius_mm, json.loads(points))

    @mcp.tool()
    async def mirror_components(components: str, plane: str, orientation: int = 2) -> str:
        """Assembly Mirror Components: instances of the same parts mirrored about an ASSEMBLY plane
        (e.g. "Right Plane"), aligned to component origin with the given orientation code (2 = as the
        SlipCell originals). components: JSON list of instance names. Returns all transforms."""
        return await slip_tube._run(sw, "mirror_components", _mirror_components,
                                    json.loads(components), plane, orientation)

    @mcp.tool()
    async def sketch_on_face(point_mm: str, circles: str = "[]") -> str:
        """Open a new sketch on the planar face of the ACTIVE part through point_mm ([x, y, z] model
        mm; the face is found geometrically, so thin faces like a flange edge work where a view pick
        fails). circles: optional [{"c": [x, y], "d": dia}] in sketch mm added right away. The sketch
        stays open for more entities. Returns the sketch name and its frame (origin, x/y axes)."""
        return await slip_tube._run(sw, "sketch_on_face", _sketch_on_face, json.loads(point_mm),
                                    json.loads(circles))

