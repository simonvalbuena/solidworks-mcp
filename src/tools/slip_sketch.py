"""Sketch definition: read_sketch / define_sketch.

read_sketch(sketch_name)
    Everything that defines a part sketch: segments (key S<i>, type, construction, geometry in
    sketch mm), points (P<i>), every relation (type + the entities it binds, by key or as an
    external reference such as the part origin, a plane or a model edge), every dimension (value,
    type, attached entities, text position) and the constrained status (fully / under / over).
    Used to learn how a sketch was defined by its author.

define_sketch(sketch_name, relations, dimensions)
    Adds relations and driving dimensions to an existing sketch. Entities are named by location in
    SKETCH coordinates (mm): {"seg": [x, y]} = the segment closest to that point, {"pt": [x, y]} =
    the sketch point closest to it, "origin" = the part origin, {"plane": "Right Plane"}.
    Relations go through ISketchRelationManager.AddRelation (no selection); if that refuses (e.g. an
    external entity), the entities are selected and IModelDoc2.SketchAddConstraints is used.
    Dimensions are placed with AddDimension2 / AddHorizontalDimension2 / AddVerticalDimension2 at a
    text point; the Modify dialog is suppressed (swInputDimValOnCreate off) and restored after.
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

logger = logging.getLogger(__name__)

_MM = 1000.0
_STATUS = {1: "unknown", 2: "under_defined", 3: "fully_defined", 4: "over_defined",
           5: "no_solution", 6: "invalid_solution", 7: "autosolve_off"}
_SEG_TYPES = {0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}
_DIM_TYPES = {0: "unknown", 1: "ordinate", 2: "linear", 3: "angular", 4: "arc_length",
              5: "radius", 6: "diameter", 7: "hor_ordinate", 8: "vert_ordinate",
              11: "horizontal", 12: "vertical"}
# swConstraintType_e (read from swconst.tlb, SW 2025) — the ones a person uses in sketches
_REL = {"distance": 1, "angle": 2, "radius": 3, "horizontal": 4, "vertical": 5, "tangent": 6,
        "parallel": 7, "perpendicular": 8, "coincident": 9, "concentric": 10, "symmetric": 11,
        "midpoint": 12, "intersection": 13, "equal": 14, "diameter": 15, "fixed": 17,
        "horizontal_points": 25, "vertical_points": 26, "collinear": 27, "coradial": 28,
        "pierce": 40, "merge": 42}
# IModelDoc2.SketchAddConstraints names (selection fallback)
_SG = {"horizontal": "sgHORIZONTAL2D", "vertical": "sgVERTICAL2D", "tangent": "sgTANGENT",
       "parallel": "sgPARALLEL", "perpendicular": "sgPERPENDICULAR", "coincident": "sgCOINCIDENT",
       "concentric": "sgCONCENTRIC", "symmetric": "sgSYMMETRIC", "midpoint": "sgATMIDDLE",
       "equal": "sgSAMELENGTH", "fixed": "sgFIXED", "collinear": "sgCOLINEAR",
       "coradial": "sgCORADIAL", "merge": "sgMERGEPOINTS", "intersection": "sgATINTERSECT",
       "pierce": "sgATPIERCE", "horizontal_points": "sgHORIZONTAL2D", "vertical_points": "sgVERTICAL2D"}
_REL_NAMES: dict[int, str] = {}


def _rel_name(t: int) -> str:
    if not _REL_NAMES:
        app = SWConnection.get_instance().get_app()
        slip_tube._load_enums(app)
        for k, v in slip_tube._ENUM_CACHE.get("swConstraintType_e", {}).items():
            _REL_NAMES[int(v)] = k.replace("swConstraintType_", "").lower()
    return _REL_NAMES.get(int(t), str(t))


def _id(obj):
    try:
        v = slip._inv(obj, "GetID")
        return tuple(int(x) for x in v)
    except Exception:  # noqa: BLE001
        return None


def _pt_xy(p):
    return [float(slip._inv(p, "X")) * _MM, float(slip._inv(p, "Y")) * _MM]


def _r(v, n=4):
    return [round(float(x), n) for x in v]


def _sketch(doc, name):
    feat = sw_w._feature(doc, name)
    sk = slip._inv(feat, "GetSpecificFeature2")
    if sk is None:
        raise SWError(f"{name!r} is not a sketch")
    return feat, sk


def _segments(sk):
    segs = list(slip._inv(sk, "GetSketchSegments") or ())
    out = []
    for i, s in enumerate(segs):
        t = int(slip._inv(s, "GetType"))
        row = {"key": f"S{i}", "type": _SEG_TYPES.get(t, str(t)),
               "construction": bool(slip._inv(s, "ConstructionGeometry"))}
        try:
            if t == 0:
                a = slip._inv(s, "GetStartPoint2")
                b = slip._inv(s, "GetEndPoint2")
                row["start"], row["end"] = _r(_pt_xy(a)), _r(_pt_xy(b))
                row["length"] = round(math.dist(row["start"], row["end"]), 4)
            elif t == 1:
                c = slip._inv(s, "GetCenterPoint2")
                a = slip._inv(s, "GetStartPoint2")
                b = slip._inv(s, "GetEndPoint2")
                row["center"] = _r(_pt_xy(c))
                row["radius"] = round(float(slip._inv(s, "GetRadius")) * _MM, 4)
                sa, eb = _pt_xy(a), _pt_xy(b)
                if math.dist(sa, eb) < 1e-6:
                    row["type"] = "circle"
                else:
                    row["start"], row["end"] = _r(sa), _r(eb)
        except Exception as ex:  # noqa: BLE001
            row["geom_error"] = str(ex)[:80]
        out.append((s, row))
    return out


def _points(sk):
    pts = list(slip._inv(sk, "GetSketchPoints2") or ())
    return [(p, {"key": f"P{i}", "xy": _r(_pt_xy(p))}) for i, p in enumerate(pts)]


def _same(a, b) -> bool:
    try:
        return a == b or slip._ole(a) == slip._ole(b)
    except Exception:  # noqa: BLE001
        return False


def _describe_entity(ent, etype, maps, sk):
    """maps = (segment_ids, point_ids). Sketch-point ids and segment ids are separate number
    spaces, and the origin / other sketches reuse the same numbers, so the owner sketch and the
    entity kind are checked before an id is trusted."""
    seg_ids, pt_ids = maps
    is_point = True
    try:
        x = float(slip._inv(ent, "X")) * _MM
        y = float(slip._inv(ent, "Y")) * _MM
    except Exception:  # noqa: BLE001
        is_point = False
    owner = None
    for getter in ("GetSketch",):
        try:
            owner = slip._inv(ent, getter)
        except Exception:  # noqa: BLE001
            owner = None
    local = owner is not None and _same(owner, sk)
    i = _id(ent)
    if local and i is not None:
        if is_point:
            hit = pt_ids.get(i)
        else:
            try:
                hit = seg_ids.get((int(slip._inv(ent, "GetType")),) + i)
            except Exception:  # noqa: BLE001
                hit = None
        if hit:
            return hit
    if is_point:
        if abs(x) < 1e-6 and abs(y) < 1e-6 and not local:
            return "origin"
        return f"ext_point({x:.3f},{y:.3f})"
    for prop in ("GetName", "Name"):
        try:
            n = slip._inv(ent, prop)
            if n:
                return f"ext:{n}" if not local else f"local:{n}"
        except Exception:  # noqa: BLE001
            pass
    try:
        f = slip._inv(ent, "GetFeature")
        if f is not None:
            return f"ext:{slip._inv(f, 'Name')}"
    except Exception:  # noqa: BLE001
        pass
    return f"ext:type{etype}"


def _sketch_summary(sketch_name):
    """Cheap status for big sketches: counts only, no per-entity description (read_sketch on a
    64-slot sketch takes minutes because every relation entity is resolved)."""
    app, doc = sw_w._active_part()
    feat, sk = _sketch(doc, sketch_name)
    seg_types: dict = {}
    loose_segs, loose_pts, n_loose_segs, n_loose_pts = [], [], 0, 0
    for i, s in enumerate(slip._inv(sk, "GetSketchSegments") or ()):
        t = _SEG_TYPES.get(int(slip._inv(s, "GetType")), "other")
        k = t + (" (construction)" if bool(slip._inv(s, "ConstructionGeometry")) else "")
        seg_types[k] = seg_types.get(k, 0) + 1
        try:
            st = int(slip._inv(s, "Status"))            # swConstrainedStatus_e per segment
        except Exception:  # noqa: BLE001
            st = 3
        if st != 3:
            n_loose_segs += 1
            if len(loose_segs) < 24:
                row = {"key": f"S{i}", "type": k, "status": _STATUS.get(st, str(st))}
                try:
                    a = _pt_xy(slip._inv(s, "GetStartPoint2"))
                    b = _pt_xy(slip._inv(s, "GetEndPoint2"))
                    row["mid"] = _r([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])
                except Exception:  # noqa: BLE001
                    pass
                loose_segs.append(row)
    pts = list(slip._inv(sk, "GetSketchPoints2") or ())
    n_pts = len(pts)
    for i, p in enumerate(pts):
        try:
            st = int(slip._inv(p, "Status"))
        except Exception:  # noqa: BLE001
            st = 3
        if st != 3:
            n_loose_pts += 1
            if len(loose_pts) < 24:
                loose_pts.append({"key": f"P{i}", "xy": _r(_pt_xy(p)), "status": _STATUS.get(st, str(st))})
    rel_types: dict = {}
    try:
        rm = slip._inv(sk, "RelationManager")
        for r in slip._inv(rm, "GetRelations", 0) or ():
            n = _rel_name(int(slip._inv(r, "GetRelationType")))
            rel_types[n] = rel_types.get(n, 0) + 1
    except Exception as ex:  # noqa: BLE001
        rel_types["error"] = str(ex)[:120]
    dims = []
    dd = slip._inv(feat, "GetFirstDisplayDimension")
    guard = 0
    while dd is not None and guard < 500:
        guard += 1
        try:
            d = slip._inv(dd, "GetDimension2", 0)
            t = int(slip._inv(dd, "Type2"))
            v = float(slip._inv(d, "SystemValue"))
            dims.append({"name": str(slip._inv(d, "Name")), "type": _DIM_TYPES.get(t, str(t)),
                         "value_mm_or_deg": round(math.degrees(v), 4) if t == 3 else round(v * _MM, 4)})
        except Exception as ex:  # noqa: BLE001
            dims.append({"error": str(ex)[:80]})
        dd = slip._inv(feat, "GetNextDisplayDimension", dd)
    status = int(slip._inv(sk, "GetConstrainedStatus"))
    return {"status": "done", "sketch": sketch_name, "constrained": _STATUS.get(status, str(status)),
            "segments": seg_types, "points": n_pts, "relations": rel_types,
            "not_fully_defined": {"segments": n_loose_segs, "points": n_loose_pts,
                                  "first_segments": loose_segs, "first_points": loose_pts},
            "relation_count": sum(v for k, v in rel_types.items() if k != "error"), "dimensions": dims}


def _read_sketch(sketch_name, summary=False):
    if summary:
        return _sketch_summary(sketch_name)
    app, doc = sw_w._active_part()
    feat, sk = _sketch(doc, sketch_name)
    segs = _segments(sk)
    pts = _points(sk)
    seg_ids, pt_ids = {}, {}
    for s, row in segs:
        i = _id(s)
        if i is not None:
            seg_ids[(int(slip._inv(s, "GetType")),) + i] = row["key"]
    for p, row in pts:
        i = _id(p)
        if i is not None:
            pt_ids[i] = row["key"]
    maps = (seg_ids, pt_ids)
    rels = []
    try:
        rm = slip._inv(sk, "RelationManager")
        for r in slip._inv(rm, "GetRelations", 0) or ():
            t = int(slip._inv(r, "GetRelationType"))
            ents = list(slip._inv(r, "GetEntities") or ())
            etypes = list(slip._inv(r, "GetEntitiesType") or ())
            rels.append({"type": _rel_name(t),
                         "entities": [_describe_entity(e, etypes[k] if k < len(etypes) else 0, maps, sk)
                                      for k, e in enumerate(ents)]})
    except Exception as ex:  # noqa: BLE001
        rels.append({"error": str(ex)[:120]})
    dims = []
    dd = slip._inv(feat, "GetFirstDisplayDimension")
    guard = 0
    while dd is not None and guard < 200:
        guard += 1
        row = {}
        try:
            d = slip._inv(dd, "GetDimension2", 0)
            row["name"] = str(slip._inv(d, "FullName"))
            row["value"] = float(slip._inv(d, "SystemValue"))
            t = int(slip._inv(dd, "Type2"))
            row["type"] = _DIM_TYPES.get(t, str(t))
            row["value_mm_or_deg"] = round(math.degrees(row["value"]), 4) if t == 3 \
                else round(row["value"] * _MM, 4)
            try:
                row["driving"] = int(slip._inv(d, "DrivenState")) == 2   # swDimensionDriving (1 = driven)
            except Exception:  # noqa: BLE001
                pass
            ann = slip._inv(dd, "GetAnnotation")
            ents = list(slip._inv(ann, "GetAttachedEntities3") or ())
            types = list(slip._inv(ann, "GetAttachedEntityTypes") or ())
            row["attached"] = [_describe_entity(e, types[k] if k < len(types) else 0, maps, sk)
                               for k, e in enumerate(ents)]
            pos = slip._inv(ann, "GetPosition")
            row["text_model_mm"] = _r([v * _MM for v in list(pos)[:3]], 2)
        except Exception as ex:  # noqa: BLE001
            row["error"] = str(ex)[:120]
        dims.append(row)
        dd = slip._inv(feat, "GetNextDisplayDimension", dd)
    status = int(slip._inv(sk, "GetConstrainedStatus"))
    return {"status": "done", "sketch": sketch_name,
            "constrained": _STATUS.get(status, str(status)),
            "segments": [row for _s, row in segs], "points": [row for _p, row in pts],
            "relations": rels, "dimensions": dims}


# --------------------------------------------------------------------------- define

def _sketch_to_model(app, sk, xy_mm):
    mu = slip._inv(app, "GetMathUtility")
    xf = slip._inv(sk, "ModelToSketchTransform")
    inv = slip._inv(xf, "Inverse")
    p = slip._inv(mu, "CreatePoint", _doubles([xy_mm[0] / _MM, xy_mm[1] / _MM, 0.0]))
    q = slip._inv(p, "MultiplyTransform", inv)
    a = slip._inv(q, "ArrayData")
    return [float(a[0]), float(a[1]), float(a[2])]


def _doubles(vals):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in vals])


def _seg_distance(row, xy):
    if row["type"] == "line":
        a, b = row["start"], row["end"]
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((xy[0] - ax) * dx + (xy[1] - ay) * dy) / L2))
        return math.dist(xy, [ax + t * dx, ay + t * dy])
    if row["type"] in ("circle", "arc"):
        return abs(math.dist(xy, row["center"]) - row["radius"])
    return float("inf")


def _null_dispatch():
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_DISPATCH, None)   # a plain None -> "Type mismatch"


def _origin_point(doc):
    """The part origin as a sketch-point object (usable in AddRelation without selecting)."""
    for name, ftype in sw_w._feature_types(doc):
        if ftype == "OriginProfileFeature":
            try:
                osk = slip._inv(sw_w._feature(doc, name), "GetSpecificFeature2")
                pts = list(slip._inv(osk, "GetSketchPoints2") or ())
                if pts:
                    return pts[0]
            except Exception as ex:  # noqa: BLE001
                logger.info("origin point: %s", ex)
    return None


def _select_by_id(doc, name, typ, xyz_m, append=False, mark=0):
    ext = slip._inv(doc, "Extension")
    return bool(slip._inv(ext, "SelectByID2", name, typ, float(xyz_m[0]), float(xyz_m[1]),
                          float(xyz_m[2]), append, int(mark), _null_dispatch(), 0))


def _closest_model_edge(doc, xyz_m, tol_m=0.0005):
    """Model edge geometrically closest to a point (IEdge.GetClosestPointOn over every body)."""
    best, bd = None, float("inf")
    try:
        bodies = list(slip._inv(doc, "GetBodies2", 0, True) or ())
    except Exception:  # noqa: BLE001
        return None
    for b in bodies:
        for e in list(slip._inv(b, "GetEdges") or ()):
            try:
                r = slip._inv(e, "GetClosestPointOn", xyz_m[0], xyz_m[1], xyz_m[2])
                d = math.dist([float(r[0]), float(r[1]), float(r[2])], xyz_m)
            except Exception:  # noqa: BLE001
                continue
            if d < bd:
                best, bd = e, d
    return best if bd <= tol_m else None


def _grab_selected(doc, name, typ, xyz_m):
    """Select one entity by name/type/point, return the object, leave the selection empty."""
    slip._inv(doc, "ClearSelection2", True)
    if not _select_by_id(doc, name, typ, xyz_m):
        return None
    selmgr = slip._inv(doc, "SelectionManager")
    obj = slip._inv(selmgr, "GetSelectedObject6", 1, -1)
    slip._inv(doc, "ClearSelection2", True)
    return obj


def _resolve(ref, segs, pts, ctx):
    """-> ('obj', com_object, label). Refs: "origin", {"plane": name}, {"edge": [x,y,z] model mm},
    {"seg": [x,y]}, {"pt": [x,y]} (sketch mm), optionally with "sketch": "<other sketch>" to point
    at an entity of another sketch (its own coordinates)."""
    doc = ctx["doc"]
    if ref == "origin" or (isinstance(ref, dict) and ref.get("origin")):
        o = _origin_point(doc)
        if o is not None:
            return ("obj", o, "origin")
        return ("origin", None, "origin")
    if isinstance(ref, dict) and "plane" in ref:
        o = _grab_selected(doc, ref["plane"], "PLANE", [0, 0, 0])
        if o is None:
            raise SWError(f"plane {ref['plane']!r} not found")
        return ("obj", o, f"plane:{ref['plane']}")
    if isinstance(ref, dict) and "edge" in ref:
        xyz = [v / _MM for v in ref["edge"]]
        o = _closest_model_edge(doc, xyz)          # geometric, not a view ray pick
        if o is None:
            o = _grab_selected(doc, "", "EDGE", xyz)
        if o is None:
            raise SWError(f"no model edge at {ref['edge']} mm")
        return ("obj", o, f"edge@{ref['edge']}")
    if isinstance(ref, dict) and ref.get("sketch"):
        _f, osk = _sketch(doc, ref["sketch"])
        segs, pts = _segments(osk), _points(osk)
        tag = ref["sketch"] + ":"
    else:
        tag = ""
    if isinstance(ref, dict) and "seg" in ref:
        xy = ref["seg"]
        cands = [(sg, r) for sg, r in segs if ref.get("construction") is None
                 or r["construction"] == bool(ref.get("construction"))]
        best = min(cands, key=lambda sr: _seg_distance(sr[1], xy), default=None)
        if best is None or _seg_distance(best[1], xy) > 0.5:
            raise SWError(f"no segment within 0.5 mm of {xy}")
        return ("obj", best[0], tag + best[1]["key"])
    if isinstance(ref, dict) and "pt" in ref:
        xy = ref["pt"]
        best = min(pts, key=lambda pr: math.dist(pr[1]["xy"], xy), default=None)
        if best is None or math.dist(best[1]["xy"], xy) > 0.5:
            raise SWError(f"no sketch point within 0.5 mm of {xy}")
        return ("obj", best[0], tag + best[1]["key"])
    raise SWError(f"bad entity reference {ref!r}")


def _select(doc, app, sk, res, append, mark=0):
    kind, obj, label = res
    if kind == "origin":
        for name in ("Point1@Origin", "Origin"):
            if _select_by_id(doc, name, "EXTSKETCHPOINT", [0, 0, 0], append, mark):
                return True
        return False
    selmgr = slip._inv(doc, "SelectionManager")
    sd = slip._inv(selmgr, "CreateSelectData")
    slip._put(sd, "Mark", int(mark))
    try:
        return bool(slip._inv(obj, "Select4", append, sd))
    except Exception:  # noqa: BLE001
        return bool(slip._inv(obj, "Select2", append, int(mark)))   # features (planes)


def _open_sketch(doc, feat):
    skm = slip._inv(doc, "SketchManager")
    active = slip._inv(skm, "ActiveSketch")
    if active is not None:
        slip._inv(skm, "InsertSketch", True)   # leave whatever sketch is open
    slip._inv(doc, "ClearSelection2", True)
    slip._inv(feat, "Select2", False, 0)
    slip._inv(doc, "EditSketch")


def _remove_dangling_relations(rm) -> int:
    """Delete vertical/horizontal relations left with a single point (what AddRelation makes when
    asked for VERTICAL/HORIZONTAL on two points) — they constrain nothing and clutter the sketch."""
    n = 0
    try:
        for r in list(slip._inv(rm, "GetRelations", 0) or ()):
            t = int(slip._inv(r, "GetRelationType"))
            if t not in (4, 5):
                continue
            ents = list(slip._inv(r, "GetEntities") or ())
            types = list(slip._inv(r, "GetEntitiesType") or ())
            if len(ents) == 1 and types and int(types[0]) == 2:      # swSketchRelationEntityType_Point
                if slip._inv(rm, "DeleteRelation", r):
                    n += 1
    except Exception as ex:  # noqa: BLE001
        logger.info("dangling relation cleanup: %s", ex)
    return n


def _define_sketch(sketch_name, relations, dimensions, report="full"):
    app, doc = sw_w._active_part()
    feat, sk = _sketch(doc, sketch_name)
    _open_sketch(doc, feat)
    feat, sk = _sketch(doc, sketch_name)
    segs = _segments(sk)
    pts = _points(sk)
    rm = slip._inv(sk, "RelationManager")
    ctx = {"doc": doc, "app": app, "sk": sk}
    done, failed = [], []
    for rel in relations or ():
        rtype = rel.get("type")
        try:
            resolved = [_resolve(e, segs, pts, ctx) for e in rel.get("entities", [])]
            labels = [r[2] for r in resolved]
            # vertical/horizontal between POINTS is swConstraintType VERTPOINTS/HORIZPOINTS (26/25);
            # AddRelation(points, VERTICAL) keeps only the first point (a dangling 1-point relation)
            if rtype in ("vertical", "horizontal") and len(resolved) >= 2 and all(
                    isinstance(e, (dict, str)) and (e == "origin" or (isinstance(e, dict) and "pt" in e))
                    for e in rel.get("entities", [])):
                rtype = rtype + "_points"
            ok = False
            if all(r[0] == "obj" for r in resolved) and rtype in _REL:
                try:
                    out = slip._inv(rm, "AddRelation", sw_w._variant_dispatch_array([r[1] for r in resolved]),
                                    _REL[rtype])
                    ok = out is not None
                except Exception as ex:  # noqa: BLE001
                    logger.info("AddRelation %s: %s", rtype, ex)
            how = "AddRelation"
            if not ok:
                slip._inv(doc, "ClearSelection2", True)
                sel = all(_select(doc, app, sk, r, True) for r in resolved)
                if not sel:
                    raise SWError("could not select " + ", ".join(labels))
                slip._inv(doc, "SketchAddConstraints", _SG[rtype])
                slip._inv(doc, "ClearSelection2", True)
                ok, how = True, "SketchAddConstraints"
            done.append({"relation": rtype, "entities": labels, "how": how})
        except Exception as ex:  # noqa: BLE001
            failed.append({"relation": rtype, "entities": rel.get("entities"), "error": str(ex)[:160]})
    # dimensions
    toggle_prev = None
    try:
        toggle_prev = bool(slip._inv(app, "GetUserPreferenceToggle", 10))
        slip._inv(app, "SetUserPreferenceToggle", 10, False)   # swInputDimValOnCreate
    except Exception as ex:  # noqa: BLE001
        logger.info("dim toggle: %s", ex)
    try:
        for dm in dimensions or ():
            kind = dm.get("kind", "distance")
            try:
                ents = dm.get("entities") or ([dm["seg"]] if "seg" in dm else [])
                ents = [e if not isinstance(e, list) else {"seg": e} for e in ents]
                resolved = [_resolve(e, segs, pts, ctx) for e in ents]
                slip._inv(doc, "ClearSelection2", True)
                if not all(_select(doc, app, sk, r, k > 0) for k, r in enumerate(resolved)):
                    raise SWError("could not select " + ", ".join(r[2] for r in resolved))
                tx = dm.get("text")
                if tx is None:
                    raise SWError("text position [x, y] (sketch mm) is required")
                m = _sketch_to_model(app, sk, tx)
                orient = dm.get("orient", "aligned")
                if kind in ("distance", "length") and orient == "horizontal":
                    dd = slip._inv(doc, "AddHorizontalDimension2", m[0], m[1], m[2])
                elif kind in ("distance", "length") and orient == "vertical":
                    dd = slip._inv(doc, "AddVerticalDimension2", m[0], m[1], m[2])
                else:
                    dd = slip._inv(doc, "AddDimension2", m[0], m[1], m[2])
                slip._inv(doc, "ClearSelection2", True)
                if dd is None:
                    raise SWError("SolidWorks returned no dimension")
                d = slip._inv(dd, "GetDimension2", 0)
                if "value_mm" in dm or "value_deg" in dm:
                    v = math.radians(dm["value_deg"]) if "value_deg" in dm else dm["value_mm"] / _MM
                    slip._put(d, "SystemValue", v)
                val = float(slip._inv(d, "SystemValue"))
                done.append({"dimension": kind, "orient": orient,
                             "entities": [r[2] for r in resolved], "name": str(slip._inv(d, "FullName")),
                             "value": round(math.degrees(val), 4) if kind == "angle" else round(val * _MM, 4)})
            except Exception as ex:  # noqa: BLE001
                failed.append({"dimension": kind, "entities": dm.get("entities") or dm.get("seg"),
                               "error": str(ex)[:160]})
    finally:
        if toggle_prev is not None:
            try:
                slip._inv(app, "SetUserPreferenceToggle", 10, toggle_prev)
            except Exception:  # noqa: BLE001
                pass
    removed = _remove_dangling_relations(rm)
    skm = slip._inv(doc, "SketchManager")
    try:
        slip._inv(skm, "InsertSketch", True)
    except Exception:  # noqa: BLE001
        pass
    try:
        slip._inv(doc, "EditRebuild3")
    except Exception:  # noqa: BLE001
        pass
    if removed:
        done.append({"removed_dangling_relations": removed})
    if report == "summary":
        after = _sketch_summary(sketch_name)
        return {"status": "done", "sketch": sketch_name, "constrained": after["constrained"],
                "applied_count": len(done), "failed": failed, "relations": after["relations"],
                "relation_count": after["relation_count"], "dimension_count": len(after["dimensions"]),
                "not_fully_defined": after["not_fully_defined"],
                **sw_w._body_report(doc)}
    after = _read_sketch(sketch_name)
    return {"status": "done", "sketch": sketch_name, "constrained": after["constrained"],
            "applied": done, "failed": failed, "relations": after["relations"],
            "dimensions": after["dimensions"], **sw_w._body_report(doc)}


def _ent_key(obj):
    """(kind, id) for a sketch entity — segment and point ids are separate number spaces."""
    i = _id(obj)
    try:
        slip._inv(obj, "GetType")
        kind = "seg"
    except Exception:  # noqa: BLE001
        kind = "pt"
    try:                                    # sketch points expose X; segments do not
        float(slip._inv(obj, "X"))
        kind = "pt"
    except Exception:  # noqa: BLE001
        pass
    return (kind, i)


def _cleanup_sketch(sketch_name, delete_entities, delete_relations, report="summary"):
    """Delete sketch segments and specific relations from a sketch of the ACTIVE part."""
    app, doc = sw_w._active_part()
    feat, sk = _sketch(doc, sketch_name)
    _open_sketch(doc, feat)
    feat, sk = _sketch(doc, sketch_name)
    segs, pts = _segments(sk), _points(sk)
    ctx = {"doc": doc, "app": app, "sk": sk}
    rm = slip._inv(sk, "RelationManager")
    done, failed = [], []
    # relations first (entity deletion would take its relations with it anyway)
    for spec in delete_relations or ():
        try:
            want_type = str(spec.get("type", "")).lower()
            resolved = [_resolve(e, segs, pts, ctx) for e in spec.get("entities", [])]
            want = sorted(str(_ent_key(r[1])) for r in resolved if r[0] == "obj")
            hit = 0
            for r in list(slip._inv(rm, "GetRelations", 0) or ()):
                if _rel_name(int(slip._inv(r, "GetRelationType"))) != want_type:
                    continue
                ents = list(slip._inv(r, "GetEntities") or ())
                if want and sorted(str(_ent_key(e)) for e in ents) != want:
                    continue
                if slip._inv(rm, "DeleteRelation", r):
                    hit += 1
                    break
            (done if hit else failed).append({"relation": want_type,
                                              "entities": [r[2] for r in resolved], "deleted": hit})
        except Exception as ex:  # noqa: BLE001
            failed.append({"relation": spec, "error": str(ex)[:160]})
    if delete_entities:
        try:
            resolved = [_resolve(e, segs, pts, ctx) for e in delete_entities]
            slip._inv(doc, "ClearSelection2", True)
            ok = all(_select(doc, app, sk, r, True) for r in resolved)
            if not ok:
                raise SWError("could not select " + ", ".join(r[2] for r in resolved))
            slip._inv(doc, "EditDelete")
            done.append({"deleted_entities": [r[2] for r in resolved]})
        except Exception as ex:  # noqa: BLE001
            failed.append({"entities": delete_entities, "error": str(ex)[:160]})
    removed = _remove_dangling_relations(rm)
    if removed:
        done.append({"removed_dangling_relations": removed})
    skm = slip._inv(doc, "SketchManager")
    try:
        slip._inv(skm, "InsertSketch", True)
    except Exception:  # noqa: BLE001
        pass
    try:
        slip._inv(doc, "EditRebuild3")
    except Exception:  # noqa: BLE001
        pass
    after = _read_sketch(sketch_name, summary=(report == "summary"))
    return {"status": "done", "sketch": sketch_name, "applied": done, "failed": failed, "after": after}


def _delete_feature(feature_name, with_children=False):
    app, doc = sw_w._active_part()
    feat = sw_w._feature(doc, feature_name)
    skm = slip._inv(doc, "SketchManager")
    if slip._inv(skm, "ActiveSketch") is not None:
        slip._inv(skm, "InsertSketch", True)
    slip._inv(doc, "ClearSelection2", True)
    if not bool(slip._inv(feat, "Select2", False, 0)):
        raise SWError(f"could not select {feature_name!r}")
    ext = slip._inv(doc, "Extension")
    # swDeleteSelectionOptions_e: swDelete_Absorbed 1, swDelete_Children 2
    ok = bool(slip._inv(ext, "DeleteSelection2", 1 | (2 if with_children else 0)))
    slip._inv(doc, "EditRebuild3")
    return {"status": "done" if ok else "failed", "feature": feature_name, **sw_w._body_report(doc)}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def read_sketch(sketch_name: str, summary: bool = False) -> str:
        """Read how a sketch of the ACTIVE part is defined: segments (S<i>) and points (P<i>) with
        geometry in sketch mm, every relation with the entities it binds (keys, 'origin', planes,
        model edges), every dimension (value, type, attached entities, text position) and the
        constrained status (fully_defined / under_defined / over_defined). Read-only.
        summary=true: only counts (segments by type, points, relations by type), dimension values
        and the constrained status — fast on big sketches (dozens of slots / holes)."""
        return await slip_tube._run(sw, "read_sketch", _read_sketch, sketch_name, summary)

    @mcp.tool()
    async def define_sketch(sketch_name: str, relations: str = "[]", dimensions: str = "[]",
                            report: str = "full") -> str:
        """Add relations and dimensions to a sketch of the ACTIVE part so it becomes fully defined.
        Entity refs (sketch coordinates, mm): {"seg": [x, y]} closest segment (add
        "construction": true/false to restrict), {"pt": [x, y]} closest sketch point, "origin",
        {"plane": "Right Plane"}.
        relations: JSON list of {"type": horizontal|vertical|coincident|midpoint|equal|symmetric|
        collinear|parallel|perpendicular|tangent|concentric|coradial|fixed|pierce, "entities": [refs]}.
        dimensions: JSON list of {"kind": "length"|"distance"|"diameter"|"radius"|"angle",
        "entities": [refs] (or "seg": [x, y] for length/diameter/radius), "orient":
        "horizontal"|"vertical"|"aligned" (linear only), "text": [x, y] text position in sketch mm,
        optional "value_mm" / "value_deg" to drive a new value}.
        report: "full" (every relation and dimension after) or "summary" (counts, failures and
        the constrained status only — use it on big sketches, the full report can take minutes).
        Returns what was applied, what failed and the sketch's constrained status after."""
        return await slip_tube._run(sw, "define_sketch", _define_sketch, sketch_name,
                                    json.loads(relations), json.loads(dimensions), report)

    @mcp.tool()
    async def cleanup_sketch(sketch_name: str, delete_entities: str = "[]",
                             delete_relations: str = "[]", report: str = "summary") -> str:
        """Remove things from a sketch of the ACTIVE part: delete_entities = JSON refs like
        define_sketch's ({"seg": [x, y], "construction": true}, {"pt": [x, y]}); delete_relations =
        JSON list of {"type": name as read_sketch shows it (samelength, vertical, vertpoints, ...),
        "entities": [refs]} — the relation of that type binding exactly those entities is deleted.
        Also removes dangling one-point vertical/horizontal relations. report: summary | full."""
        return await slip_tube._run(sw, "cleanup_sketch", _cleanup_sketch, sketch_name,
                                    json.loads(delete_entities), json.loads(delete_relations), report)

    @mcp.tool()
    async def delete_feature(feature_name: str, with_children: bool = False) -> str:
        """Delete a feature of the ACTIVE part by name (absorbed sketches go with it;
        with_children also deletes dependent features). Returns the body report."""
        return await slip_tube._run(sw, "delete_feature", _delete_feature, feature_name, with_children)
