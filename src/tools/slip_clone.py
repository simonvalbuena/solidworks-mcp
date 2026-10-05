"""Clone parts feature by feature from an open SOURCE part into the ACTIVE (target) part.

The rebuild method of docs/assembly_rebuild_notes.md, automated: the target part is built in the
same model coordinates as the source, so every sketch, relation, dimension and model reference
maps by geometry.

clone_sketch(source, sketch)   sketch on the same plane / face, same segments (lines, arcs,
                               circles, points, text), shared endpoints merged, every relation and
                               dimension of the source re-applied to the mapped entities. Model
                               edges / faces / vertices / planes / the origin / other sketches map
                               by geometry or name; references to OTHER documents (in-context) can
                               not be mapped and are reported.
clone_feature(source, feature) the feature that consumes a cloned sketch (extrude / cut, sheet-
                               metal base flange / tab, weldment structural member) or a fillet /
                               linear pattern, with the source definition's parameters.
clone_part(source, ...)        walks the source tree in order and does both; renames at the end.

Run through slip_dev.fork_call (background=true for long parts).
"""

from __future__ import annotations

import logging
import math

from errors import SWError
from sw_connection import SWConnection
from tools import slip
from tools import slip_build as sw_b
from tools import slip_sketch as sw_s
from tools import slip_weldment as sw_w

logger = logging.getLogger(__name__)
_MM = 1000.0
_TOL = 2e-6                      # m — coincidence tolerance in model space

# relation types re-created by dimensions (skip) and the slot bookkeeping ones
_DIM_RELS = {1, 2, 3, 15, 41, 43, 44, 84}
_SKIP_RELS = {73, 74, 32}        # FAKESLOT / FIXEDSLOT / USEEDGE (handled separately)


# --------------------------------------------------------------------------- small helpers

def _app():
    return SWConnection.get_instance().get_app()


def _doc(name):
    app = _app()
    for cand in (name, name + ".SLDPRT", name + ".sldprt", name + ".SLDASM"):
        try:
            d = slip._inv(app, "GetOpenDocumentByName", cand)
            if d is not None:
                return d
        except Exception:  # noqa: BLE001
            pass
    for d in list(slip._inv(app, "GetDocuments") or ()):
        try:
            t = str(slip._inv(d, "GetTitle"))
            p = str(slip._inv(d, "GetPathName"))
            if name in (t, p) or t.rsplit(".", 1)[0] == name:
                return d
        except Exception:  # noqa: BLE001
            continue
    raise SWError(f"source document {name!r} is not open")


def _vec(a):
    return [float(v) for v in a]


def _xf_apply(app, xf, p):
    mu = slip._inv(app, "GetMathUtility")
    pt = slip._inv(mu, "CreatePoint", sw_s._doubles([p[0], p[1], p[2]]))
    a = slip._inv(slip._inv(pt, "MultiplyTransform", xf), "ArrayData")
    return [float(a[0]), float(a[1]), float(a[2])]


def _s2m(app, sk):
    return slip._inv(slip._inv(sk, "ModelToSketchTransform"), "Inverse")


def _m2s(sk):
    return slip._inv(sk, "ModelToSketchTransform")


def _pt_model(app, s2m, p):
    return _xf_apply(app, s2m, [float(slip._inv(p, "X")), float(slip._inv(p, "Y")), 0.0])


def _try(f, default=None):
    try:
        return f()
    except Exception:  # noqa: BLE001
        return default


def _ftype(feat):
    return str(slip._inv(feat, "GetTypeName2"))


def _fname(feat):
    return str(slip._inv(feat, "Name"))


def _features(doc):
    out, feat, guard = [], slip._inv(doc, "FirstFeature"), 0
    while feat is not None and guard < 3000:
        guard += 1
        out.append(feat)
        feat = slip._inv(feat, "GetNextFeature")
    return out


# --------------------------------------------------------------------------- model geometry index

class _ModelIndex:
    """Edges / faces / vertices of a part with cheap pre-filtering for geometric lookups."""

    def __init__(self, doc):
        self.doc = doc
        self.edges = None
        self.faces = None

    def _bodies(self):
        return list(slip._inv(self.doc, "GetBodies2", 0, True) or ())

    def edge(self, xyz, tol=5e-4):
        if self.edges is None:
            self.edges = []
            for b in self._bodies():
                for e in list(slip._inv(b, "GetEdges") or ()):
                    cp = _try(lambda e=e: list(slip._inv(e, "GetCurveParams2")))
                    if cp:
                        lo = [min(cp[i], cp[i + 3]) for i in range(3)]
                        hi = [max(cp[i], cp[i + 3]) for i in range(3)]
                    else:
                        lo, hi = None, None
                    self.edges.append((e, lo, hi))
        best, bd = None, float("inf")
        for e, lo, hi in self.edges:
            # straight-edge pre-filter: skip edges whose end-point box is far (arcs bulge: no filter)
            if lo is not None and hi is not None:
                far = any(xyz[i] < lo[i] - 0.05 or xyz[i] > hi[i] + 0.05 for i in range(3))
                if far:
                    continue
            r = _try(lambda e=e: slip._inv(e, "GetClosestPointOn", xyz[0], xyz[1], xyz[2]))
            if r is None:
                continue
            d = math.dist([float(r[0]), float(r[1]), float(r[2])], xyz)
            if d < bd:
                best, bd = e, d
        return best if bd <= tol else None

    def face(self, xyz, tol=5e-5):
        if self.faces is None:
            self.faces = []
            for b in self._bodies():
                self.faces.extend(list(slip._inv(b, "GetFaces") or ()))
        best, bd = None, float("inf")
        for f in self.faces:
            r = _try(lambda f=f: slip._inv(f, "GetClosestPointOn", xyz[0], xyz[1], xyz[2]))
            if r is None:
                continue
            d = math.dist([float(r[0]), float(r[1]), float(r[2])], xyz)
            if d < bd:
                best, bd = f, d
        return best if bd <= tol else None

    def vertex(self, xyz, tol=5e-6):
        best, bd = None, float("inf")
        for b in self._bodies():
            for v in list(slip._inv(b, "GetVertices") or ()):
                p = _try(lambda v=v: list(slip._inv(v, "GetPoint")))
                if p:
                    d = math.dist(p[:3], xyz)
                    if d < bd:
                        best, bd = v, d
        return best if bd <= tol else None


def _point_on_edge(e):
    cp = list(slip._inv(e, "GetCurveParams2"))
    mid = [(cp[i] + cp[i + 3]) / 2 for i in range(3)]
    r = slip._inv(e, "GetClosestPointOn", mid[0], mid[1], mid[2])
    return [float(r[0]), float(r[1]), float(r[2])]


def _point_on_face(f):
    box = list(slip._inv(f, "GetBox"))
    c = [(box[i] + box[i + 3]) / 2 for i in range(3)]
    r = slip._inv(f, "GetClosestPointOn", c[0], c[1], c[2])
    return [float(r[0]), float(r[1]), float(r[2])]


# --------------------------------------------------------------------------- source sketch reading

def _classify(ent, sk, seg_ids, pt_ids):
    """-> ('seg', i) | ('pt', i) | ('origin',) | ('edge', xyz) | ('face', xyz) | ('vertex', xyz) |
    ('plane', name) | ('sketchpt', sketch_name, xyz) | ('sketchseg', sketch_name, xyz) | ('ext', why)"""
    is_point = _try(lambda: float(slip._inv(ent, "X")) is not None, False)
    owner = _try(lambda: slip._inv(ent, "GetSketch"))
    if owner is not None:
        local = sw_s._same(owner, sk)
        i = sw_s._id(ent)
        if local and i is not None:
            if is_point and i in pt_ids:
                return ("pt", pt_ids[i])
            if not is_point:
                t = _try(lambda: int(slip._inv(ent, "GetType")))
                if t is not None and (t,) + i in seg_ids:
                    return ("seg", seg_ids[(t,) + i])
        # a point / segment of another sketch of this part (origin sketch included)
        of = _try(lambda: slip._inv(owner, "QueryInterface"))  # noqa: F841  (late binding probe)
        oname, otype = None, None
        try:
            ofeat = owner                                     # ISketch is also an IFeature
            oname = str(slip._inv(ofeat, "Name"))
            otype = str(slip._inv(ofeat, "GetTypeName2"))
        except Exception:  # noqa: BLE001
            pass
        if is_point:
            x, y = float(slip._inv(ent, "X")), float(slip._inv(ent, "Y"))
            if otype == "OriginProfileFeature" or (oname is None and abs(x) < 1e-9 and abs(y) < 1e-9):
                return ("origin",)
            if oname:
                return ("sketchpt", oname, [x, y])
        elif oname:
            return ("sketchseg", oname, None)
        return ("ext", "foreign sketch entity")
    # model entities: edge / face / vertex / plane
    if _try(lambda: slip._inv(ent, "GetCurveParams2")) is not None:
        return ("edge", _point_on_edge(ent))
    if _try(lambda: slip._inv(ent, "GetSurface")) is not None:
        return ("face", _point_on_face(ent))
    p = _try(lambda: list(slip._inv(ent, "GetPoint")))
    if p:
        return ("vertex", p[:3])
    n = _try(lambda: str(slip._inv(ent, "Name")))
    if n:
        return ("plane", n)
    return ("ext", "unknown entity")


def _read_sketch(app, sdoc, sketch_name):
    feat = sw_w._feature(sdoc, sketch_name)
    sk = slip._inv(feat, "GetSpecificFeature2")
    s2m = _s2m(app, sk)
    segs = list(slip._inv(sk, "GetSketchSegments") or ())
    pts = list(slip._inv(sk, "GetSketchPoints2") or ())
    seg_ids, pt_ids = {}, {}
    for i, s in enumerate(segs):
        sid = sw_s._id(s)
        if sid is not None:
            seg_ids[(int(slip._inv(s, "GetType")),) + sid] = i
    for i, p in enumerate(pts):
        pid = sw_s._id(p)
        if pid is not None:
            pt_ids[pid] = i
    P = [_pt_model(app, s2m, p) for p in pts]

    def pidx(p):
        pid = sw_s._id(p)
        if pid in pt_ids:
            return pt_ids[pid]
        xyz = _pt_model(app, s2m, p)
        return min(range(len(P)), key=lambda k: math.dist(P[k], xyz)) if P else None

    rows = []
    for i, s in enumerate(segs):
        t = int(slip._inv(s, "GetType"))
        row = {"i": i, "type": t, "constr": bool(slip._inv(s, "ConstructionGeometry"))}
        if t == 0:
            row["a"] = pidx(slip._inv(s, "GetStartPoint2"))
            row["b"] = pidx(slip._inv(s, "GetEndPoint2"))
        elif t == 1:
            row["c"] = pidx(slip._inv(s, "GetCenterPoint2"))
            row["a"] = pidx(slip._inv(s, "GetStartPoint2"))
            row["b"] = pidx(slip._inv(s, "GetEndPoint2"))
            row["r"] = float(slip._inv(s, "GetRadius"))
            row["circle"] = row["a"] == row["b"] or math.dist(P[row["a"]], P[row["b"]]) < 1e-9
            row["dir"] = int(_try(lambda s=s: slip._inv(s, "GetRotationDir"), 1) or 1)
        elif t == 4:
            c = list(slip._inv(s, "GetCoordinates"))[:3]
            row["xyz"] = _xf_apply(app, s2m, c)
            row["text"] = str(slip._inv(s, "Text"))
            row["fmt"] = slip._inv(s, "GetTextFormat")
            row["doc_fmt"] = bool(_try(lambda s=s: slip._inv(s, "GetUseDocTextFormat"), False))
        else:
            row["unsupported"] = True
        rows.append(row)
    used = set()
    for r in rows:
        for k in ("a", "b", "c"):
            if r.get(k) is not None:
                used.add(r[k])
    lone = [k for k in range(len(pts)) if k not in used]
    rels = []
    rm = slip._inv(sk, "RelationManager")
    for r in list(slip._inv(rm, "GetRelations", 0) or ()):
        t = int(slip._inv(r, "GetRelationType"))
        ents = list(slip._inv(r, "GetEntities") or ())
        rels.append({"type": t, "name": sw_s._rel_name(t),
                     "ents": [_classify(e, sk, seg_ids, pt_ids) for e in ents]})
    dims = []
    dd = slip._inv(feat, "GetFirstDisplayDimension")
    guard = 0
    while dd is not None and guard < 2000:
        guard += 1
        d = slip._inv(dd, "GetDimension2", 0)
        ann = slip._inv(dd, "GetAnnotation")
        ents = list(slip._inv(ann, "GetAttachedEntities3") or ())
        dims.append({"type": int(slip._inv(dd, "Type2")), "value": float(slip._inv(d, "SystemValue")),
                     "driven": int(_try(lambda d=d: slip._inv(d, "DrivenState"), 2) or 2) == 1,
                     "pos": _vec(list(slip._inv(ann, "GetPosition"))[:3]),
                     "name": str(slip._inv(d, "Name")),
                     "ents": [_classify(e, sk, seg_ids, pt_ids) for e in ents]})
        dd = slip._inv(feat, "GetNextDisplayDimension", dd)
    slots = []
    for sl in list(_try(lambda: slip._inv(sk, "GetSketchSlots")) or ()):
        c = _try(lambda sl=sl: slip._inv(sl, "GetCenterPoint"))
        slots.append({"center": pidx(c) if c is not None else None})
    # plane / face the sketch lies on
    ref = _try(lambda: slip._inv(sk, "GetReferenceEntity", 0))
    if isinstance(ref, tuple):
        ref = ref[0]
    plane = None
    if ref is not None:
        n = _try(lambda: str(slip._inv(ref, "Name")))
        if n:
            plane = ("plane", n)
        elif _try(lambda: slip._inv(ref, "GetSurface")) is not None:
            plane = ("face", _point_on_face(ref))
        else:
            # IRefPlane: find its feature by comparing with the planes of the document
            plane = ("refplane", None)
    return {"name": sketch_name, "feat": feat, "sk": sk, "s2m": s2m, "P": P, "rows": rows,
            "lone": lone, "rels": rels, "dims": dims, "slots": slots, "plane": plane,
            "x_axis": _sub(_xf_apply(app, s2m, [1, 0, 0]), _xf_apply(app, s2m, [0, 0, 0])),
            "y_axis": _sub(_xf_apply(app, s2m, [0, 1, 0]), _xf_apply(app, s2m, [0, 0, 0]))}


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


# --------------------------------------------------------------------------- target sketch writing

def _open_target_sketch(app, doc, plane, idx):
    sw_b._close_sketch(doc)
    slip._inv(doc, "ClearSelection2", True)
    kind = plane[0] if plane else None
    if kind == "plane":
        f = sw_w._feature(doc, plane[1])
        if not bool(slip._inv(f, "Select2", False, 0)):
            raise SWError(f"could not select plane {plane[1]!r}")
    elif kind == "face":
        face = idx.face(plane[1])
        if face is None:
            raise SWError(f"no target face at {[round(v * _MM, 3) for v in plane[1]]} mm")
        selmgr = slip._inv(doc, "SelectionManager")
        sd = slip._inv(selmgr, "CreateSelectData")
        if not bool(slip._inv(face, "Select4", False, sd)):
            raise SWError("could not select the sketch face")
    else:
        raise SWError(f"sketch plane not resolved: {plane}")
    skm = slip._inv(doc, "SketchManager")
    slip._inv(skm, "InsertSketch", True)
    sk = slip._inv(skm, "ActiveSketch")
    if sk is None:
        raise SWError("sketch did not open")
    return skm, sk


def _resolve_target(ref, tdoc, idx, tseg, tpt, cache):
    k = ref[0]
    if k == "seg":
        return tseg.get(ref[1])
    if k == "pt":
        return tpt.get(ref[1])
    if k == "origin":
        if "origin" not in cache:
            cache["origin"] = sw_s._origin_point(tdoc)
        return cache["origin"]
    if k == "edge":
        return idx.edge(ref[1])
    if k == "face":
        return idx.face(ref[1])
    if k == "vertex":
        return idx.vertex(ref[1])
    if k == "plane":
        return _try(lambda: sw_w._feature(tdoc, ref[1]))
    if k == "sketchpt":
        key = ("sk", ref[1])
        if key not in cache:
            f = _try(lambda: sw_w._feature(tdoc, cache.get("names", {}).get(ref[1], ref[1])))
            osk = _try(lambda: slip._inv(f, "GetSpecificFeature2")) if f is not None else None
            cache[key] = list(slip._inv(osk, "GetSketchPoints2") or ()) if osk is not None else []
        best, bd = None, float("inf")
        for p in cache[key]:
            d = math.hypot(float(slip._inv(p, "X")) - ref[2][0], float(slip._inv(p, "Y")) - ref[2][1])
            if d < bd:
                best, bd = p, d
        return best if bd < 1e-6 else None
    return None


def _clone_sketch_into(app, tdoc, src, idx, cache):
    """Create the sketch in the target; returns (target sketch feature name, report)."""
    skm, sk = _open_target_sketch(app, tdoc, src["plane"], idx)
    m2s = _m2s(sk)
    t_x = _sub(_xf_apply(app, _s2m(app, sk), [1, 0, 0]), _xf_apply(app, _s2m(app, sk), [0, 0, 0]))
    t_y = _sub(_xf_apply(app, _s2m(app, sk), [0, 1, 0]), _xf_apply(app, _s2m(app, sk), [0, 0, 0]))
    # 2-D map source sketch axes -> target sketch axes (rotation by multiples of 90 deg, or mirror)
    sx, sy = src["x_axis"], src["y_axis"]
    det = _dot(sx, t_x) * _dot(sy, t_y) - _dot(sx, t_y) * _dot(sy, t_x)
    swap_hv = abs(_dot(sx, t_x)) < 0.5                    # source horizontal is target vertical
    P = src["P"]
    TP = [_xf_apply(app, m2s, p) for p in P]              # target sketch coords (m)
    slip._put(skm, "AddToDB", True)
    tseg, report = {}, {"unsupported": [], "unmapped": [], "failed": []}
    try:
        for r in src["rows"]:
            t = r["type"]
            obj = None
            if t == 0:
                a, b = TP[r["a"]], TP[r["b"]]
                obj = slip._inv(skm, "CreateLine", a[0], a[1], 0.0, b[0], b[1], 0.0)
            elif t == 1 and r["circle"]:
                c = TP[r["c"]]
                obj = slip._inv(skm, "CreateCircleByRadius", c[0], c[1], 0.0, r["r"])
            elif t == 1:
                c, a, b = TP[r["c"]], TP[r["a"]], TP[r["b"]]
                d = r["dir"] if det > 0 else -r["dir"]
                obj = slip._inv(skm, "CreateArc", c[0], c[1], 0.0, a[0], a[1], 0.0, b[0], b[1], 0.0, int(d))
            elif t == 4:
                c = _xf_apply(app, m2s, r["xyz"])
                obj = slip._inv(tdoc, "InsertSketchText", c[0], c[1], 0.0, r["text"], 1, 0, 0, 100, 0)
                if obj is not None and not r["doc_fmt"]:
                    _try(lambda: slip._inv(obj, "SetTextFormat", False, r["fmt"]))
            else:
                report["unsupported"].append({"segment": r["i"], "type": t})
                continue
            if obj is None:
                report["failed"].append({"segment": r["i"], "type": t})
                continue
            if r["constr"]:
                _try(lambda obj=obj: slip._put(obj, "ConstructionGeometry", True))
            tseg[r["i"]] = obj
        for k in src["lone"]:
            p = TP[k]
            slip._inv(skm, "CreatePoint", p[0], p[1], 0.0)
    finally:
        slip._put(skm, "AddToDB", False)
    rm = slip._inv(sk, "RelationManager")
    # merge shared end / centre points: target segments made their own copies
    tpts_all = list(slip._inv(sk, "GetSketchPoints2") or ())

    def tpts_at(p):
        return [q for q in tpts_all
                if math.hypot(float(slip._inv(q, "X")) - p[0], float(slip._inv(q, "Y")) - p[1]) < _TOL]
    merged = 0
    for k in range(len(P)):
        group = tpts_at(TP[k])
        for q in group[1:]:
            if slip._inv(rm, "AddRelation", sw_w._variant_dispatch_array([group[0], q]), 42) is not None:
                merged += 1
    tpts_all = list(slip._inv(sk, "GetSketchPoints2") or ())
    tpt = {}
    for k in range(len(P)):
        g = tpts_at(TP[k])
        if g:
            tpt[k] = g[0]
    report["merged_points"] = merged
    # relations
    applied = 0
    for rel in src["rels"]:
        t = rel["type"]
        if t in _DIM_RELS or t in _SKIP_RELS:
            continue
        if t == 75:                                        # same slots: equal arcs / centre lines
            continue
        objs = [_resolve_target(e, tdoc, idx, tseg, tpt, cache) for e in rel["ents"]]
        if any(o is None for o in objs):
            report["unmapped"].append({"relation": rel["name"], "entities": [str(e)[:60] for e in rel["ents"]]})
            continue
        tt = t
        if swap_hv:
            tt = {4: 5, 5: 4, 25: 26, 26: 25}.get(t, t)
        ok = _try(lambda: slip._inv(rm, "AddRelation", sw_w._variant_dispatch_array(objs), tt))
        if ok is None:
            report["failed"].append({"relation": rel["name"], "entities": [str(e)[:60] for e in rel["ents"]]})
        else:
            applied += 1
    report["relations_applied"] = applied
    # slot internals (implicit in the slot entity): tangents, equal arcs, centre point at middle
    if src["slots"]:
        report["slot_relations"] = _slot_internals(src, tseg, tpt, rm)
    # dimensions
    applied, toggle = 0, None
    try:
        toggle = bool(slip._inv(app, "GetUserPreferenceToggle", 10))
        slip._inv(app, "SetUserPreferenceToggle", 10, False)
    except Exception:  # noqa: BLE001
        pass
    try:
        for dm in src["dims"]:
            objs = [_resolve_target(e, tdoc, idx, tseg, tpt, cache) for e in dm["ents"]]
            if not objs or any(o is None for o in objs):
                report["unmapped"].append({"dimension": dm["name"], "value": dm["value"],
                                           "entities": [str(e)[:60] for e in dm["ents"]]})
                continue
            slip._inv(tdoc, "ClearSelection2", True)
            sel_ok = True
            for k, o in enumerate(objs):
                if not sw_s._select(tdoc, app, sk, ("obj", o, ""), k > 0):
                    sel_ok = False
            if not sel_ok:
                report["failed"].append({"dimension": dm["name"], "why": "select"})
                continue
            x, y, z = dm["pos"]
            dt = dm["type"]
            if swap_hv:
                dt = {11: 12, 12: 11}.get(dt, dt)
            if dt == 11:
                dd = slip._inv(tdoc, "AddHorizontalDimension2", x, y, z)
            elif dt == 12:
                dd = slip._inv(tdoc, "AddVerticalDimension2", x, y, z)
            else:
                dd = slip._inv(tdoc, "AddDimension2", x, y, z)
            slip._inv(tdoc, "ClearSelection2", True)
            if dd is None:
                report["failed"].append({"dimension": dm["name"], "why": "AddDimension returned None"})
                continue
            d = slip._inv(dd, "GetDimension2", 0)
            if dm["driven"]:
                _try(lambda: slip._put(d, "DrivenState", 1))
            else:
                _try(lambda: slip._put(d, "SystemValue", dm["value"]))
            applied += 1
    finally:
        if toggle is not None:
            _try(lambda: slip._inv(app, "SetUserPreferenceToggle", 10, toggle))
    report["dimensions_applied"] = applied
    report["dimensions_source"] = len(src["dims"])
    report["relations_source"] = len([r for r in src["rels"] if r["type"] not in _DIM_RELS])
    removed = sw_s._remove_dangling_relations(rm)
    if removed:
        report["removed_dangling"] = removed
    name = sw_b._last_sketch_name(tdoc)
    slip._inv(skm, "InsertSketch", True)
    status = 0
    try:
        status = int(slip._inv(slip._inv(sw_w._feature(tdoc, name), "GetSpecificFeature2"),
                               "GetConstrainedStatus"))
    except Exception:  # noqa: BLE001
        pass
    report["constrained"] = sw_s._STATUS.get(status, str(status))
    return name, report


def _slot_internals(src, tseg, tpt, rm):
    """Slots made with the slot tool carry implicit relations; re-create them on the primitives:
    tangent line/arc at shared ends, equal end arcs, slot centre point at the centre line middle."""
    rows, P = src["rows"], src["P"]
    made = 0
    existing = {(min(e[0][1], e[1][1]), max(e[0][1], e[1][1]), r["type"])
                for r in src["rels"] for e in [r["ents"]] if len(e) == 2
                and e[0][0] == "seg" and e[1][0] == "seg"}
    arcs = [r for r in rows if r["type"] == 1 and not r.get("circle")]
    lines = [r for r in rows if r["type"] == 0]

    def add(a, b, t):
        nonlocal made
        key = (min(a, b), max(a, b), t)
        if key in existing or a not in tseg or b not in tseg:
            return
        if slip._inv(rm, "AddRelation", sw_w._variant_dispatch_array([tseg[a], tseg[b]]), t) is not None:
            made += 1
            existing.add(key)
    for ar in arcs:
        ends = {ar["a"], ar["b"]}
        for ln in lines:
            if ln["constr"]:
                continue
            if ln["a"] in ends or ln["b"] in ends:
                add(ar["i"], ln["i"], 6)
    # pair arcs of one slot: arcs joined by the same two lines
    for i, a1 in enumerate(arcs):
        for a2 in arcs[i + 1:]:
            l1 = {ln["i"] for ln in lines if not ln["constr"] and ({ln["a"], ln["b"]} & {a1["a"], a1["b"]})}
            l2 = {ln["i"] for ln in lines if not ln["constr"] and ({ln["a"], ln["b"]} & {a2["a"], a2["b"]})}
            if l1 and l1 == l2:
                add(a1["i"], a2["i"], 14)
                # centre point of the slot: middle of the construction line between the arc centres
                for cl in lines:
                    if cl["constr"] and {cl["a"], cl["b"]} == {a1["c"], a2["c"]}:
                        for s in src["slots"]:
                            c = s.get("center")
                            if c is not None and c in tpt and math.dist(
                                    P[c], [(P[a1["c"]][k] + P[a2["c"]][k]) / 2 for k in range(3)]) < 1e-7:
                                if slip._inv(rm, "AddRelation", sw_w._variant_dispatch_array(
                                        [tpt[c], tseg[cl["i"]]]), 12) is not None:
                                    made += 1
    return made


# --------------------------------------------------------------------------- features

_EXTRUDE_TYPES = {"Extrusion", "Cut", "ICE", "BaseBody", "Boss"}


def _clone_extrude(app, sfeat, tdoc, tsketch):
    d = slip._inv(sfeat, "GetDefinition")
    ft = _ftype(sfeat)
    e1 = int(slip._inv(d, "GetEndCondition", True))
    e2 = int(slip._inv(d, "GetEndCondition", False))
    both = bool(slip._inv(d, "BothDirections"))
    dep1 = float(slip._inv(d, "GetDepth", True))
    dep2 = float(slip._inv(d, "GetDepth", False))
    rev = bool(slip._inv(d, "ReverseDirection"))
    flip = bool(_try(lambda: slip._inv(d, "FlipSideToCut"), False))
    normal = bool(_try(lambda: slip._inv(d, "NormalCut"), False))
    merge = bool(_try(lambda: slip._inv(d, "Merge"), True))
    if e1 not in (0, 1, 2, 6, 9) or (both and e2 not in (0, 1, 2)):
        raise SWError(f"end condition {e1}/{e2} not supported yet")
    sw_b._close_sketch(tdoc)
    slip._inv(tdoc, "ClearSelection2", True)
    f = sw_w._feature(tdoc, tsketch)
    if not bool(slip._inv(f, "Select2", False, 0)):
        raise SWError(f"could not select {tsketch!r}")
    fm = slip._inv(tdoc, "FeatureManager")
    if ft in ("Cut", "ICE"):
        res = slip._inv(fm, "FeatureCut4", not both, flip, rev, e1, e2 if both else 0, dep1, dep2 if both else 0.0,
                        False, False, False, False, 0.0, 0.0, False, False, False, False, normal, False, True,
                        False, False, False, 0, 0.0, False, False)
    else:
        res = slip._inv(fm, "FeatureExtrusion3", not both, False, rev, e1, e2 if both else 0, dep1,
                        dep2 if both else 0.0, False, False, False, False, 0.0, 0.0, False, False, False,
                        False, merge, True, True, 0, 0.0, False)
    if res is None:
        raise SWError(f"{ft} creation returned None")
    return str(slip._inv(res, "Name"))


def _clone_fillet(app, sfeat, sdoc, tdoc, idx):
    d = slip._inv(sfeat, "GetDefinition")
    from win32com.client import VARIANT
    import pythoncom
    nocomp = VARIANT(pythoncom.VT_DISPATCH, None)
    slip._inv(d, "AccessSelections", sdoc, nocomp)
    try:
        edges = list(slip._inv(d, "Edges") or ())
        r = float(slip._inv(d, "DefaultRadius"))
    finally:
        _try(lambda: slip._inv(d, "ReleaseSelectionAccess"))
    pts = [[v * _MM for v in _point_on_edge(e)] for e in edges]
    out = sw_b._fillet_edges(r * _MM, pts)
    return out.get("feature")


def _clone_lpattern(app, sfeat, sdoc, tdoc, idx, names):
    d = slip._inv(sfeat, "GetDefinition")
    from win32com.client import VARIANT
    import pythoncom
    nocomp = VARIANT(pythoncom.VT_DISPATCH, None)
    slip._inv(d, "AccessSelections", sdoc, nocomp)
    try:
        n1 = int(slip._inv(d, "D1TotalInstances"))
        s1 = float(slip._inv(d, "D1Spacing"))
        rev1 = bool(slip._inv(d, "D1ReverseDirection"))
        n2 = int(_try(lambda: slip._inv(d, "D2TotalInstances"), 1) or 1)
        s2 = float(_try(lambda: slip._inv(d, "D2Spacing"), 0.0) or 0.0)
        rev2 = bool(_try(lambda: slip._inv(d, "D2ReverseDirection"), False))
        dir1 = slip._inv(d, "D1Axis")
        dir2 = _try(lambda: slip._inv(d, "D2Axis"))
        seeds = [str(slip._inv(f, "Name")) for f in list(slip._inv(d, "PatternFeatureArray") or ())]
    finally:
        _try(lambda: slip._inv(d, "ReleaseSelectionAccess"))
    tdir1 = _map_dir(dir1, tdoc, idx)
    if tdir1 is None:
        raise SWError("pattern direction 1 not mapped")
    slip._inv(tdoc, "ClearSelection2", True)
    sw_s._select(tdoc, app, None, ("obj", tdir1, ""), False, 1)
    if n2 > 1 and dir2 is not None:
        tdir2 = _map_dir(dir2, tdoc, idx)
        sw_s._select(tdoc, app, None, ("obj", tdir2, ""), True, 2)
    for s in seeds:
        f = sw_w._feature(tdoc, names.get(s, s))
        slip._inv(f, "Select2", True, 4)
    fm = slip._inv(tdoc, "FeatureManager")
    res = slip._inv(fm, "FeatureLinearPattern5", n1, s1, n2, s2, rev1, rev2, "", "", False, False,
                    False, False, False, False, True, True, False, False, 0.0, 0.0, False, False)
    if res is None:
        raise SWError("FeatureLinearPattern5 returned None")
    return str(slip._inv(res, "Name"))


def _map_dir(ent, tdoc, idx):
    if ent is None:
        return None
    if _try(lambda: slip._inv(ent, "GetCurveParams2")) is not None:
        return idx.edge(_point_on_edge(ent))
    if _try(lambda: slip._inv(ent, "GetSurface")) is not None:
        return idx.face(_point_on_face(ent))
    n = _try(lambda: str(slip._inv(ent, "Name")))
    if n:
        return _try(lambda: sw_w._feature(tdoc, n))
    return None


def _clone_base_flange(app, sfeat, tdoc, tsketch, is_tab):
    d = slip._inv(sfeat, "GetDefinition")
    th = float(slip._inv(d, "Thickness"))
    br = float(_try(lambda: slip._inv(d, "BendRadius"), 0.0) or 0.0)
    rev_th = bool(_try(lambda: slip._inv(d, "ReverseThickness"), False))
    e1 = int(_try(lambda: slip._inv(d, "D1EndCondition"), 0) or 0)
    dep = float(_try(lambda: slip._inv(d, "D1OffsetDistance"), 0.0) or 0.0)
    rev1 = bool(_try(lambda: slip._inv(d, "D1ReverseDirection"), False))
    k = 0.5
    ba = _try(lambda: slip._inv(d, "GetCustomBendAllowance"))
    if ba is not None:
        k = float(_try(lambda: slip._inv(ba, "KFactor"), 0.5) or 0.5)
    sw_b._close_sketch(tdoc)
    slip._inv(tdoc, "ClearSelection2", True)
    f = sw_w._feature(tdoc, tsketch)
    slip._inv(f, "Select2", False, 0)
    fm = slip._inv(tdoc, "FeatureManager")
    cba = slip._inv(fm, "CreateCustomBendAllowance")
    slip._put(cba, "Type", 2)
    slip._put(cba, "KFactor", k)
    res = slip._inv(fm, "InsertSheetMetalBaseFlange2", th, rev_th, br, dep, 0.0, rev1, e1, 0, 0, cba,
                    True, 1, th / 2, th / 2, 0.5, True, bool(is_tab), not is_tab, True)
    if res is None:
        raise SWError("InsertSheetMetalBaseFlange2 returned None")
    return str(slip._inv(res, "Name"))


def _clone_member(app, sfeat, sdoc, tdoc, tsketch):
    d = slip._inv(sfeat, "GetDefinition")
    from win32com.client import VARIANT
    import pythoncom
    nocomp = VARIANT(pythoncom.VT_DISPATCH, None)
    _try(lambda: slip._inv(d, "AccessSelections", sdoc, nocomp))
    try:
        info = {}
        for k in ("WeldmentProfilePath", "ProfilePath", "ConfigurationName", "ConnectedSegmentsOption"):
            v = _try(lambda k=k: slip._inv(d, k))
            if v is not None:
                info[k] = v
        groups = list(_try(lambda: slip._inv(d, "Groups")) or ())
        g = groups[0] if groups else None
        angle = float(_try(lambda: slip._inv(g, "Angle"), 0.0) or 0.0) if g is not None else 0.0
        mirror = bool(_try(lambda: slip._inv(g, "MirrorProfile"), False)) if g is not None else False
        axis = int(_try(lambda: slip._inv(g, "MirrorProfileAxis"), 1) or 1) if g is not None else 1
        merge = bool(_try(lambda: slip._inv(g, "MergeArcSegmentBodies"), False)) if g is not None else False
    finally:
        _try(lambda: slip._inv(d, "ReleaseSelectionAccess"))
    prof = str(info.get("WeldmentProfilePath") or info.get("ProfilePath") or "")
    if not prof:
        raise SWError(f"no profile path in the member definition ({list(info)})")
    out = sw_w._insert_structural_member(tsketch, None, prof, math.degrees(angle), mirror, axis, merge,
                                         int(info.get("ConnectedSegmentsOption", 1) or 1))
    tname = out.get("feature")
    cfg = info.get("ConfigurationName")
    if cfg:
        _try(lambda: sw_w._set_feature_properties(tname, {"ConfigurationName": cfg}))
    return tname


# --------------------------------------------------------------------------- entry points

def clone_sketch(source: str, sketch: str, rename: bool = True) -> dict:
    app, tdoc = sw_w._active_part()
    sdoc = _doc(source)
    src = _read_sketch(app, sdoc, sketch)
    idx = _ModelIndex(tdoc)
    name, rep = _clone_sketch_into(app, tdoc, src, idx, {})
    if rename and name != sketch and _try(lambda: sw_w._feature(tdoc, sketch)) is None:
        _try(lambda: slip._put(sw_w._feature(tdoc, name), "Name", sketch))
        name = sketch
    return {"status": "done", "sketch": name, "report": rep}


def read_source_sketch(source: str, sketch: str) -> dict:
    """Debug: what clone_sketch reads from the source (no COM objects)."""
    app = _app()
    src = _read_sketch(app, _doc(source), sketch)
    rows = [{k: v for k, v in r.items() if k not in ("fmt",)} for r in src["rows"]]
    return {"plane": src["plane"], "points": [[round(v * _MM, 4) for v in p] for p in src["P"]],
            "segments": rows, "lone_points": src["lone"], "relations": src["rels"],
            "dims": src["dims"], "slots": src["slots"],
            "x_axis": src["x_axis"], "y_axis": src["y_axis"]}


def clone_part(source: str, start_after: str = "", stop_at: str = "", skip: list | None = None) -> dict:
    """Walk the SOURCE tree and clone sketches + the features that consume them into the ACTIVE
    part (already holding everything up to start_after). Stops at the first unsupported feature."""
    app, tdoc = sw_w._active_part()
    sdoc = _doc(source)
    skip = set(skip or ())
    feats = _features(sdoc)
    names: dict = {}             # source feature name -> target feature name
    log = []
    started = not start_after
    pending_sketch = None
    cache = {"names": names}
    idx = _ModelIndex(tdoc)
    passive = {"CommentsFolder", "FavoriteFolder", "HistoryFolder", "SelectionSetFolder", "SensorFolder",
               "DocsFolder", "DetailCabinet", "EnvFolder", "InkMarkupFolder", "EqnFolder", "RefPlane",
               "OriginProfileFeature", "MaterialFolder", "SurfaceBodyFolder", "SolidBodyFolder",
               "CutListFolder", "SubWeldFolder", "FtrFolder", "ConfigTableFolder", "NotesAreaFtrFolder",
               "FlatPattern", "SheetMetal", "WeldmentFeature", "AmbientLight", "DirectionLight",
               "SMEnvTableFeat", "FoldUpDownSketchFolder", "LiveSectionFolder"}
    for f in feats:
        fname, ft = _fname(f), _ftype(f)
        if not started:
            if fname == start_after:
                started = True
            continue
        if stop_at and fname == stop_at:
            break
        if ft in passive or fname in skip:
            continue
        try:
            if ft == "ProfileFeature":
                src = _read_sketch(app, sdoc, fname)
                idx.edges = idx.faces = None          # geometry changed since the last feature
                tname, rep = _clone_sketch_into(app, tdoc, src, idx, cache)
                names[fname] = tname
                pending_sketch = tname
                log.append({"source": fname, "target": tname, "report": rep})
            elif ft in _EXTRUDE_TYPES:
                tname = _clone_extrude(app, f, tdoc, pending_sketch or _feature_sketch(f, names))
                names[fname] = tname
                log.append({"source": fname, "target": tname, **sw_w._body_report(tdoc)})
            elif ft == "SMBaseFlange":
                is_tab = "Tab" in fname
                tname = _clone_base_flange(app, f, tdoc, pending_sketch or _feature_sketch(f, names), is_tab)
                names[fname] = tname
                log.append({"source": fname, "target": tname, **sw_w._body_report(tdoc)})
            elif ft == "WeldMemberFeat":
                tname = _clone_member(app, f, sdoc, tdoc, pending_sketch or _feature_sketch(f, names))
                names[fname] = tname
                log.append({"source": fname, "target": tname, **sw_w._body_report(tdoc)})
            elif ft == "Fillet":
                idx.edges = idx.faces = None
                tname = _clone_fillet(app, f, sdoc, tdoc, idx)
                names[fname] = tname
                log.append({"source": fname, "target": tname, **sw_w._body_report(tdoc)})
            elif ft == "LPattern":
                idx.edges = idx.faces = None
                tname = _clone_lpattern(app, f, sdoc, tdoc, idx, names)
                names[fname] = tname
                log.append({"source": fname, "target": tname, **sw_w._body_report(tdoc)})
            else:
                log.append({"source": fname, "type": ft, "stopped": "unsupported feature type"})
                break
        except Exception as ex:  # noqa: BLE001
            log.append({"source": fname, "type": ft, "error": str(ex)[:300]})
            break
    return {"status": "done", "names": names, "log": log, **sw_w._body_report(tdoc)}


def _feature_sketch(feat, names):
    sub = _try(lambda: slip._inv(feat, "GetFirstSubFeature"))
    while sub is not None:
        if _ftype(sub) == "ProfileFeature":
            n = _fname(sub)
            return names.get(n, n)
        sub = _try(lambda sub=sub: slip._inv(sub, "GetNextSubFeature"))
    raise SWError(f"no sketch found for {_fname(feat)}")


def rename_like_source(source: str, mapping: dict) -> dict:
    """Rename target features to the source names (two passes through temporary names)."""
    app, tdoc = sw_w._active_part()
    done = []
    tmp = {}
    for s, t in mapping.items():
        if s == t:
            continue
        f = _try(lambda t=t: sw_w._feature(tdoc, t))
        if f is None:
            continue
        tn = f"zz_tmp_{len(tmp)}"
        slip._put(f, "Name", tn)
        tmp[tn] = s
    for tn, s in tmp.items():
        f = sw_w._feature(tdoc, tn)
        slip._put(f, "Name", s)
        done.append(s)
    return {"status": "done", "renamed": done}
