"""Slip Robotics batch tools — the fast path for a standard R00 fabrication drawing.

Built after timing the first three drawings made with the one-call-per-action tools
(~28 round trips, ~6.7 min per drawing). These three tools replace ~15 of those calls:

* view_geometry     – every visible edge of a view with SHEET-space coordinates (mm), the
                      view outline, and a summary that already names the envelope edges
                      (leftmost/rightmost vertical, top/bottom horizontal — picking the
                      segment on the dimension's side when an edge is split by a tab) and
                      the circles grouped by diameter with the top-left instance flagged.
                      No more hand-computed sheet positions, no more guessing which of two
                      identical circles is the top one.
* add_dimensions    – place a whole list of dimensions in one call (edges by index from
                      view_geometry, text in sheet mm, optional reference=parentheses),
                      compact result.
* finish_slip_r00   – HLR everywhere, tangent edges removed on ortho views / visible on the
                      iso, delete Sheet2, standard notes clean-up (bend-radius fragment,
                      FINISH_COLOR, MASK paragraph, rev-flag paragraph), compact result.

Same COM binding rules as slip.py (late binding: zero-arg members are read as attributes,
never called; use _inv for the ones that misbehave).
"""

from __future__ import annotations

import json
import logging
import math

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection
from tools import slip
from tools.annotation import (
    DIM_PRECISION,
    SW_FRACTION_DECIMAL,
    SW_INPUT_DIM_VAL_ON_CREATE,
    SW_PRECISION_UNCHANGED,
    SW_UNIT_MM,
    _build_edges_info,
    _get_drawing_views,
    _get_view_edges,
)

logger = logging.getLogger(__name__)

_M_TO_MM = 1000.0
_IN = 25.4


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:
    @mcp.tool()
    async def view_geometry(view_name: str | None = None, include_edges: bool = True) -> str:
        """Geometry of drawing view(s) in SHEET millimetres (origin bottom-left of the sheet).
        Returns per view: outline [xmin,ymin,xmax,ymax], scale, edges (index, type, sheet
        start/end/center, length, radius) and a summary: envelope size in inches, the edge
        indices to dimension the envelope (left/right vertical, top/bottom horizontal — the
        segment on the outer side is chosen when an edge is split by a tab/notch), and circles
        grouped by diameter with the top-left instance flagged. Feed the indices straight into
        add_dimensions. include_edges=False returns only outline + summary (small)."""
        return await slip._run(sw, "view_geometry", _view_geometry, view_name, include_edges)

    @mcp.tool()
    async def add_dimensions(view_name: str, dims: list[dict]) -> str:
        """Add several dimensions to one view in a single call. Each dim:
        {"type": "linear"|"diameter"|"radius", "e1": <edge index>, "e2": <edge index, linear only>,
         "text": [x_mm, y_mm] (sheet coords), "reference": true|false (parentheses, default false)}.
        Edge indices are those returned by view_geometry / probe_drawing_edges for the same view
        (do not rebuild in between). Document units, 3-place precision. Returns one compact
        entry per dimension: name (for move/delete/reference tools), value, text position."""
        return await slip._run(sw, "add_dimensions", _add_dimensions, view_name, dims)

    @mcp.tool()
    async def add_dimension_to_intersection(
        view_name: str,
        e_line1: int,
        e_line2: int,
        e_ref: int,
        text: list[float],
        reference: bool = False,
    ) -> str:
        """Slip rule for edges that end in a radius (chamfers/end cuts meeting a relief fillet,
        non-90 deg flanges): dimension to the VIRTUAL SHARP, never to the arc. This is SolidWorks'
        Smart Dimension -> "Find Intersection": it puts a sketch point at the theoretical
        intersection of two straight edges in the view's sketch and dimensions from e_ref to it.
        e_line1/e_line2: indices (view_geometry) of the two straight edges whose extensions meet;
        e_ref: the edge the dimension is measured from (e.g. the bottom edge); text: [x_mm, y_mm]
        ACTUAL sheet position. Returns the sharp's view coordinates, the dimension name and value."""
        return await slip._run(sw, "add_dimension_to_intersection", _add_dimension_to_intersection,
                               view_name, e_line1, e_line2, e_ref, text, reference)

    @mcp.tool()
    async def delete_view_sketch_points(view_name: str) -> str:
        """Delete stray sketch points left in a drawing view's sketch (e.g. by a failed
        add_dimension_to_intersection). Returns how many points were found/deleted."""
        return await slip._run(sw, "delete_view_sketch_points", _delete_view_sketch_points, view_name)

    @mcp.tool()
    async def finish_slip_r00(
        iso_view: str | None = "Drawing View3",
        finish_text: str | None = None,
        delete_sheet2: bool = True,
    ) -> str:
        """One-call R00 finish for a Slip flat/sheet-metal part drawing: every view Hidden Lines
        Removed with tangent edges REMOVED, the iso view (iso_view) tangent edges VISIBLE, delete
        Sheet2, and clean the notes block: drop the bend-radius fragment, drop FINISH_COLOR (or
        replace the FINISH line with a literal, e.g. finish_text="NONE" when the part property is
        blank), remove the MASK paragraph and the rev-flag paragraph. Every step is tolerant of
        already-clean input. Returns a compact summary with the final note lines."""
        return await slip._run(sw, "finish_slip_r00", _finish_slip_r00, iso_view, finish_text, delete_sheet2)


# --------------------------------------------------------------------------- geometry

def _outline_mm(view_obj) -> list[float] | None:
    try:
        o = view_obj.GetOutline  # zero-arg -> value under late binding
        return [round(float(o[0]) * _M_TO_MM, 3), round(float(o[1]) * _M_TO_MM, 3),
                round(float(o[2]) * _M_TO_MM, 3), round(float(o[3]) * _M_TO_MM, 3)]
    except Exception as e:  # noqa: BLE001
        logger.debug("GetOutline failed: %s", e)
        return None


def _view_scale(view_obj) -> float | None:
    for name in ("ScaleDecimal", "ScaleRatio"):
        try:
            v = getattr(view_obj, name)
            if isinstance(v, (tuple, list)) and len(v) == 2 and v[1]:
                return float(v[0]) / float(v[1])
            if v is not None:
                return float(v)
        except Exception:  # noqa: BLE001
            continue
    return None


_TRANSFORM_DEBUG: dict = {}


def _get_xform_array(view_obj):
    """16 doubles of IView.ModelToViewTransform (IMathTransform.ArrayData), or raise."""
    errors = []
    xform = None
    for getter in (lambda: view_obj.ModelToViewTransform,
                   lambda: slip._inv(view_obj, "ModelToViewTransform")):
        try:
            xform = getter()
            if xform is not None:
                break
        except Exception as e:  # noqa: BLE001
            errors.append(f"xform: {e}")
    if xform is None:
        raise SWError("; ".join(errors) or "ModelToViewTransform returned None")
    for getter in (lambda: xform.ArrayData, lambda: slip._inv(xform, "ArrayData")):
        try:
            arr = getter()
            if arr is not None and len(arr) >= 13:
                return [float(v) for v in arr]
        except Exception as e:  # noqa: BLE001
            errors.append(f"ArrayData: {e}")
    raise SWError("; ".join(errors) or "ArrayData unavailable")


def _make_transform(arr: list[float], row_major: bool):
    """IMathTransform.ArrayData: [0..8] 3x3 rotation, [9..11] translation, [12] scale.
    SolidWorks documents the point transform as  p' = scale * (p · R) + t  (row vector);
    the other convention is tried too and the outline self-check picks the right one."""
    r = arr[0:9]; t = arr[9:12]; sc = arr[12] if arr[12] else 1.0

    def f(x, y, z):
        if row_major:   # p · R  (row vector times matrix)
            X = x * r[0] + y * r[3] + z * r[6]
            Y = x * r[1] + y * r[4] + z * r[7]
        else:           # R · p
            X = x * r[0] + y * r[1] + z * r[2]
            Y = x * r[3] + y * r[4] + z * r[5]
        return (sc * X + t[0]) * _M_TO_MM, (sc * Y + t[1]) * _M_TO_MM

    return f


def _model_to_sheet_fn(app, view_obj):
    """Return f(x_m, y_m, z_m) -> (X_mm, Y_mm) in sheet space (both matrix conventions are
    returned as candidates; _sheet_edges picks the one matching GetOutline), or None."""
    _TRANSFORM_DEBUG.clear()
    try:
        arr = _get_xform_array(view_obj)
    except Exception as e:  # noqa: BLE001
        _TRANSFORM_DEBUG["error"] = str(e)
        logger.debug("ModelToViewTransform unavailable: %s", e)
        return None
    _TRANSFORM_DEBUG["array"] = [round(v, 6) for v in arr[:13]]
    return (_make_transform(arr, True), _make_transform(arr, False))


def _edge_points_m(edge) -> tuple:
    """(start, end, circle_center, radius_m) in model metres; missing parts are None."""
    start = end = center = None
    radius = None
    try:
        sv = edge.GetStartVertex
        if sv is not None:
            p = sv.GetPoint
            start = (float(p[0]), float(p[1]), float(p[2]))
    except Exception:  # noqa: BLE001
        pass
    try:
        ev = edge.GetEndVertex
        if ev is not None:
            p = ev.GetPoint
            end = (float(p[0]), float(p[1]), float(p[2]))
    except Exception:  # noqa: BLE001
        pass
    try:
        curve = edge.GetCurve
        if curve.IsCircle:
            cp = curve.CircleParams
            center = (float(cp[0]), float(cp[1]), float(cp[2]))
            radius = float(cp[6])
    except Exception:  # noqa: BLE001
        pass
    return start, end, center, radius


def _read_edge_once(edge, index: int) -> dict:
    """ONE COM pass per edge (type + vertices + circle params). Dense tube views have
    200-300 visible edges, so every extra COM round-trip per edge costs seconds."""
    rec: dict = {"index": index, "type": "other", "_cached": True}
    curve = None
    try:
        curve = edge.GetCurve
    except Exception:  # noqa: BLE001
        pass
    for attr, key in (("GetStartVertex", "start"), ("GetEndVertex", "end")):
        try:
            v = getattr(edge, attr)
            if v is not None:
                p = v.GetPoint
                rec[key] = (float(p[0]), float(p[1]), float(p[2]))
        except Exception:  # noqa: BLE001
            pass
    if curve is not None:
        try:
            if curve.IsLine:
                rec["type"] = "line"
            elif curve.IsCircle:
                rec["type"] = "circle" if "start" not in rec else "arc"
                cp = curve.CircleParams
                rec["center"] = (float(cp[0]), float(cp[1]), float(cp[2]))
                rec["radius"] = float(cp[6])
        except Exception:  # noqa: BLE001
            pass
    s, e, c = rec.get("start"), rec.get("end"), rec.get("center")
    if s and e:
        rec["midpoint"] = {"x": round((s[0] + e[0]) / 2 * _M_TO_MM, 4),
                           "y": round((s[1] + e[1]) / 2 * _M_TO_MM, 4)}
    elif c:
        rec["midpoint"] = {"x": round(c[0] * _M_TO_MM, 4), "y": round(c[1] * _M_TO_MM, 4)}
    return rec


def _read_edges_once(edges) -> list[dict]:
    return [_read_edge_once(edge, i) for i, edge in enumerate(edges)]


def _apply_transform(to_sheet, edges, edges_info) -> list[dict]:
    """edges_info entries may carry cached model points (from _read_edges_once); only when
    they don't is the edge queried over COM."""
    out = []
    for info, edge in zip(edges_info, edges):
        if info.get("_cached"):
            start, end, center, radius = (info.get("start"), info.get("end"),
                                          info.get("center"), info.get("radius"))
        else:
            start, end, center, radius = _edge_points_m(edge)
        rec = {"i": info["index"], "t": info["type"]}
        if to_sheet is not None:
            try:
                if start is not None:
                    rec["s"] = [round(v, 2) for v in to_sheet(*start)]
                if end is not None:
                    rec["e"] = [round(v, 2) for v in to_sheet(*end)]
                if center is not None:
                    rec["c"] = [round(v, 2) for v in to_sheet(*center)]
            except Exception as e:  # noqa: BLE001
                logger.debug("transform failed on edge %s: %s", info["index"], e)
        if "s" in rec and "e" in rec:
            rec["m"] = [round((rec["s"][0] + rec["e"][0]) / 2, 2), round((rec["s"][1] + rec["e"][1]) / 2, 2)]
            rec["len"] = round(math.dist(rec["s"], rec["e"]), 3)
        if radius is not None:
            rec["r"] = round(radius * _M_TO_MM, 3)
        mp = info.get("midpoint")
        if mp:
            rec["model_mid"] = [mp["x"], mp["y"]]
        out.append(rec)
    return out


def _bbox(recs):
    pts = [p for r in recs for p in (r.get("s"), r.get("e"), r.get("c")) if p]
    if not pts:
        return None
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def _sheet_edges(app, view_obj, edges, edges_info) -> list[dict]:
    """Edge list with sheet-space coordinates; the matrix convention and any residual offset
    are calibrated against GetOutline (sheet mm, includes a small margin)."""
    candidates = _model_to_sheet_fn(app, view_obj)
    outline = _outline_mm(view_obj)
    if candidates is None:
        return _apply_transform(None, edges, edges_info)
    best, best_err = None, float("inf")
    for cand in candidates:
        recs = _apply_transform(cand, edges, edges_info)
        bb = _bbox(recs)
        if bb is None:
            continue
        if outline:
            # size error (catches a wrong rotation) + centre error (catches the transposed
            # convention whenever the model is not centred on its origin)
            err = (abs((bb[2] - bb[0]) - (outline[2] - outline[0]))
                   + abs((bb[3] - bb[1]) - (outline[3] - outline[1]))
                   + abs((bb[0] + bb[2]) / 2 - (outline[0] + outline[2]) / 2)
                   + abs((bb[1] + bb[3]) / 2 - (outline[1] + outline[3]) / 2))
        else:
            err = 0.0
        if err < best_err:
            best, best_err = recs, err
            _TRANSFORM_DEBUG["convention"] = "row_vector(p*R)" if cand is candidates[0] else "column(R*p)"
    if best is None:
        return _apply_transform(None, edges, edges_info)
    _TRANSFORM_DEBUG["fit_error_mm"] = round(best_err, 2)
    bb = _bbox(best)
    if outline and bb:
        dx = (outline[0] + outline[2]) / 2 - (bb[0] + bb[2]) / 2
        dy = (outline[1] + outline[3]) / 2 - (bb[1] + bb[3]) / 2
        _TRANSFORM_DEBUG["offset_applied_mm"] = [round(dx, 2), round(dy, 2)]
        if abs(dx) > 0.5 or abs(dy) > 0.5:
            for r in best:
                for k in ("s", "e", "c", "m"):
                    if k in r:
                        r[k] = [round(r[k][0] + dx, 2), round(r[k][1] + dy, 2)]
    return best


def _summarize(edges: list[dict]) -> dict:
    """Envelope edges + circles by diameter, all in sheet space."""
    tol = 0.05
    lines = [r for r in edges if r["t"] == "line" and "s" in r and "e" in r]
    vert = [r for r in lines if abs(r["s"][0] - r["e"][0]) < tol]
    horz = [r for r in lines if abs(r["s"][1] - r["e"][1]) < tol]
    pts = [p for r in edges for p in (r.get("s"), r.get("e"), r.get("c")) if p]
    summary: dict = {}
    if pts:
        xmin = min(p[0] for p in pts); xmax = max(p[0] for p in pts)
        ymin = min(p[1] for p in pts); ymax = max(p[1] for p in pts)
        summary["bbox_mm"] = [round(xmin, 2), round(ymin, 2), round(xmax, 2), round(ymax, 2)]

    def pick(cands, key_side, key_pref):
        if not cands:
            return None
        extreme = key_side(cands[0])
        for c in cands:
            extreme = key_side(c) if key_side(c) > extreme else extreme
        best = [c for c in cands if abs(key_side(c) - extreme) < tol]
        best.sort(key=key_pref)
        return best[0]

    # leftmost vertical: min x -> maximise -x; among ties prefer the TOP segment (max y)
    left = pick(vert, lambda r: -r["s"][0], lambda r: -max(r["s"][1], r["e"][1]))
    right = pick(vert, lambda r: r["s"][0], lambda r: -max(r["s"][1], r["e"][1]))
    # topmost horizontal: max y; among ties prefer the LEFT segment (min x)
    top = pick(horz, lambda r: r["s"][1], lambda r: min(r["s"][0], r["e"][0]))
    bottom = pick(horz, lambda r: -r["s"][1], lambda r: min(r["s"][0], r["e"][0]))

    def brief(r):
        return None if r is None else {"i": r["i"], "m": r.get("m"), "len": r.get("len")}

    summary["envelope_edges"] = {"left": brief(left), "right": brief(right),
                                 "top": brief(top), "bottom": brief(bottom)}
    if left and right:
        w = abs(right["s"][0] - left["s"][0])
        summary["width_sheet_mm"] = round(w, 2)
    if top and bottom:
        h = abs(top["s"][1] - bottom["s"][1])
        summary["height_sheet_mm"] = round(h, 2)

    circles = [r for r in edges if r["t"] == "circle" and "c" in r and "r" in r]
    groups: dict[float, list] = {}
    for c in circles:
        groups.setdefault(round(c["r"], 2), []).append(c)
    circ_summary = []
    for r_mm, items in sorted(groups.items(), key=lambda kv: -kv[0]):
        # top-left = smallest (x - y): leftmost wins, then highest
        tl = min(items, key=lambda c: c["c"][0] - c["c"][1])
        circ_summary.append({"r_sheet_mm": r_mm, "count": len(items),
                             "top_left": {"i": tl["i"], "c": tl["c"]},
                             "all": [{"i": c["i"], "c": c["c"]} for c in items]})
    summary["circles"] = circ_summary
    return summary


def _view_geometry(view_name, include_edges: bool) -> dict:
    app = SWConnection.get_instance().get_app()
    drawing = slip._active_drawing()
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"no drawing view found ({view_name!r})")
    result = []
    for vname, view_obj in views:
        try:
            drawing.ActivateView(vname)
        except Exception:  # noqa: BLE001
            pass
        edges = _get_view_edges(view_obj)
        edges_info = _read_edges_once(edges)   # single COM pass; transforms reuse the cache
        sheet_edges = _sheet_edges(app, view_obj, edges, edges_info)
        scale = _view_scale(view_obj)
        summ = _summarize(sheet_edges)
        if scale:
            if "width_sheet_mm" in summ:
                summ["width_in"] = round(summ["width_sheet_mm"] / scale / _IN, 4)
            if "height_sheet_mm" in summ:
                summ["height_in"] = round(summ["height_sheet_mm"] / scale / _IN, 4)
            for g in summ.get("circles", []):
                # radii come from CircleParams = MODEL mm (scale-independent)
                g["r_model_mm"] = g.pop("r_sheet_mm")
                g["dia_in"] = round(2 * g["r_model_mm"] / _IN, 4)
        # Broken views: SolidWorks reports geometry in UNBROKEN space. Record the shift so
        # add_dimensions / move_dimension_text can take ACTUAL sheet coordinates, and expose
        # actual coordinates on the summary/edges ("*_actual").
        bbox = summ.get("bbox_mm")
        binfo = slip._record_break_shift(vname, view_obj, bbox)
        if binfo.get("broken"):
            def _act(pt):
                return [round(v, 2) for v in slip._to_actual(vname, pt[0], pt[1])]
            for r in sheet_edges:
                for k in ("s", "e", "c", "m"):
                    if k in r:
                        r[k + "_actual"] = _act(r[k])
            for side in summ.get("envelope_edges", {}).values():
                if side and side.get("m"):
                    side["m_actual"] = _act(side["m"])
            for g in summ.get("circles", []):
                g["top_left"]["c_actual"] = _act(g["top_left"]["c"])
                for c in g.get("all", []):
                    c["c_actual"] = _act(c["c"])
        entry = {"view": vname, "outline_mm": _outline_mm(view_obj), "scale": scale,
                 "break": binfo,
                 "coords_note": ("BROKEN VIEW: s/e/c/m are unbroken-space; *_actual are what is on the sheet "
                                 "(+/-2 mm). Pass ACTUAL sheet mm to add_dimensions (converted to the unbroken "
                                 "point AddDimension wants) and to move_dimension_text (no conversion needed)."
                                 if binfo.get("broken") else "sheet mm"),
                 "edge_count": len(sheet_edges),
                 "sheet_coords": "ok" if any("s" in r or "c" in r for r in sheet_edges) else "UNAVAILABLE (ModelToViewTransform failed — fall back to probe_drawing_edges)",
                 "transform_debug": dict(_TRANSFORM_DEBUG),
                 "summary": summ}
        if include_edges:
            entry["edges"] = sheet_edges
        result.append(entry)
    return {"status": "done", "views": result}


# --------------------------------------------------------------------------- virtual sharp

def _view_space_xy(arr, p):
    """Model point (m) -> drawing-view SKETCH space (m): rotation only (row-vector p.R), no
    scale, no translation. Verified against a recorded macro on 108538: the virtual-sharp
    sketch point at the +Z end of the Right view was selected at (2.600325, -0.0202406) =
    (-z, y) in metres."""
    r = arr[0:9]
    return (p[0] * r[0] + p[1] * r[3] + p[2] * r[6],
            p[0] * r[1] + p[1] * r[4] + p[2] * r[7])


def _line_intersection(a1, a2, b1, b2):
    """2D intersection of infinite lines a and b (each given by two points); None if parallel."""
    x1, y1 = a1; x2, y2 = a2; x3, y3 = b1; x4, y4 = b2
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-15:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _sketch_point_names(pt) -> list[str]:
    names = []
    try:
        ids = slip._inv(pt, "GetID")
        if ids is not None:
            for v in list(ids)[::-1]:
                names.append(f"Point{int(v)}")
    except Exception:  # noqa: BLE001
        pass
    return names


def _select_sketch_point(drawing, pt, ip) -> tuple:
    """Select a drawing-view sketch point (append to selection). Tries, in order:
    SelectByID2 with the point's name (Point<ID>, as the recorded macro), SelectByID2 with an
    empty name at the point's coordinates, ISketchPoint.Select2, ISketchPoint.Select4 with a real
    SelectData. Returns (ok, method)."""
    ext = drawing.Extension
    for nm in _sketch_point_names(pt) + [""]:
        try:
            if bool(ext.SelectByID2(nm, "SKETCHPOINT", ip[0], ip[1], 0.0, True, 0, None, 0)):
                return True, f"SelectByID2({nm!r})"
        except Exception as e:  # noqa: BLE001
            logger.debug("SelectByID2 %r failed: %s", nm, e)
    try:
        if bool(pt.Select2(True, 0)):
            return True, "Select2"
    except Exception as e:  # noqa: BLE001
        logger.debug("Select2 failed: %s", e)
    try:
        sel_mgr = drawing.SelectionManager
        sd = sel_mgr.CreateSelectData
        if bool(pt.Select4(True, sd)):
            return True, "Select4(SelectData)"
    except Exception as e:  # noqa: BLE001
        logger.debug("Select4 failed: %s", e)
    return False, "none"


def _delete_view_sketch_points(view_name) -> dict:
    """Delete every sketch point in the view's own sketch (stray Find-Intersection points)."""
    drawing = slip._active_drawing()
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"view not found: {view_name}")
    vname, view_obj = views[0]
    drawing.ActivateView(vname)
    sk = None
    try:
        sk = slip._inv(view_obj, "GetSketch")
    except Exception as e:  # noqa: BLE001
        raise SWError(f"IView.GetSketch failed: {e}")
    if sk is None:
        return {"status": "done", "view": vname, "found": 0, "deleted": 0, "note": "view has no sketch"}
    try:
        pts = slip._inv(sk, "GetSketchPoints2") or []
    except Exception as e:  # noqa: BLE001
        raise SWError(f"ISketch.GetSketchPoints2 failed: {e}")
    pts = list(pts)
    found = len(pts)
    deleted, methods = 0, []
    for pt in pts:
        try:
            ip = (float(pt.X), float(pt.Y))
        except Exception:  # noqa: BLE001
            ip = (0.0, 0.0)
        drawing.ClearSelection2(True)
        ok, how = _select_sketch_point(drawing, pt, ip)
        if not ok:
            methods.append(f"{ip}: not selectable")
            continue
        try:
            drawing.EditDelete()
            deleted += 1
            methods.append(f"{ip}: deleted via {how}")
        except Exception as e:  # noqa: BLE001
            methods.append(f"{ip}: EditDelete failed {e}")
    drawing.ClearSelection2(True)
    try:
        drawing.EditRebuild3()
    except Exception:  # noqa: BLE001
        pass
    return {"status": "done", "view": vname, "found": found, "deleted": deleted, "detail": methods}


def _add_dimension_to_intersection(view_name, e_line1, e_line2, e_ref, text, reference) -> dict:
    app = SWConnection.get_instance().get_app()
    drawing = slip._active_drawing()
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"view not found: {view_name}")
    vname, view_obj = views[0]
    drawing.ActivateView(vname)
    edges = _get_view_edges(view_obj)
    for i in (e_line1, e_line2, e_ref):
        if int(i) < 0 or int(i) >= len(edges):
            raise SWError(f"edge index {i} out of range 0..{len(edges)-1}")
    l1 = _read_edge_once(edges[int(e_line1)], int(e_line1))
    l2 = _read_edge_once(edges[int(e_line2)], int(e_line2))
    for lab, rec in (("e_line1", l1), ("e_line2", l2)):
        if rec["type"] != "line" or "start" not in rec or "end" not in rec:
            raise SWError(f"{lab} (index {rec['index']}) is not a straight edge with two vertices ({rec['type']})")
    arr = _get_xform_array(view_obj)
    a1, a2 = _view_space_xy(arr, l1["start"]), _view_space_xy(arr, l1["end"])
    b1, b2 = _view_space_xy(arr, l2["start"]), _view_space_xy(arr, l2["end"])
    ip_rot = _line_intersection(a1, a2, b1, b2)
    if ip_rot is None:
        raise SWError("the two edges are parallel in this view - no intersection")
    # The drawing view's SKETCH space is centred on IView.Position (unscaled sheet offsets):
    #   sketch = (sheet_unbroken - Position) / scale,  sheet_unbroken = scale*(p.R) + t
    # (2026-09-09: using the rotated model point alone put the point 4 in off on 108544 - the
    # transform translation t is not the view centre.)
    t = arr[9:12]
    sc = arr[12] if arr[12] else 1.0
    try:
        pos = view_obj.Position
        cx, cy = float(pos[0]), float(pos[1])
    except Exception as ex:  # noqa: BLE001
        raise SWError(f"IView.Position unavailable: {ex}")
    try:
        vs = float(view_obj.ScaleDecimal) or sc
    except Exception:  # noqa: BLE001
        vs = sc
    sheet_x = sc * ip_rot[0] + t[0]
    sheet_y = sc * ip_rot[1] + t[1]
    ip = ((sheet_x - cx) / vs, (sheet_y - cy) / vs)
    logger.info("sharp: rotated=%s sheet_unbroken_m=(%s,%s) position=(%s,%s) scale=%s -> sketch=%s",
                ip_rot, sheet_x, sheet_y, cx, cy, vs, ip)

    # sketch point at the virtual sharp, in the view's sketch (what Find Intersection creates).
    # 2026-09-09: the first version toggled SketchManager.AddToDB and selected the point with
    # ISketchPoint.Select4(True, None) -> SolidWorks crashed (RPC failed). Now: plain CreatePoint
    # and selection exactly like the recorded macro: SelectByID2("", "SKETCHPOINT", x, y, 0, True, ...).
    # 2026-09-09 (v3 crashed SolidWorks too, after the point was created): v4 mirrors the recorded
    # macro step by step and logs every step to logs/server.log BEFORE executing it, so a crash
    # can be attributed. Selection fallbacks that are not in the macro (ISketchPoint.Select2 /
    # Select4) are no longer attempted here.
    log = logger.info
    log("sharp: view=%s ip=%s e_ref=%s", vname, ip, e_ref)
    ref_mid_sheet = None  # (SelectByID2 "EDGE" needs a sheet point on the edge; SelectEntity is used instead)

    drawing.ClearSelection2(True)
    skm = drawing.SketchManager
    log("sharp: CreatePoint")
    try:
        pt = skm.CreatePoint(ip[0], ip[1], 0.0)
    except Exception as e:  # noqa: BLE001
        raise SWError(f"SketchManager.CreatePoint failed: {e}")
    if pt is None:
        raise SWError("SketchManager.CreatePoint returned None (is the view activated?)")
    names = _sketch_point_names(pt)
    log("sharp: point created, GetID names=%s", names)
    # leave any implicit sketch edit the point creation may have started
    try:
        active = skm.ActiveSketch
        log("sharp: ActiveSketch is %s", "set" if active is not None else "None")
        if active is not None:
            log("sharp: InsertSketch(True) to exit the sketch")
            skm.InsertSketch(True)
    except Exception as ex:  # noqa: BLE001
        log("sharp: ActiveSketch/InsertSketch raised %s", ex)
    log("sharp: ActivateView(%s)", vname)
    drawing.ActivateView(vname)

    ax, ay = float(text[0]), float(text[1])
    ux, uy = slip._to_unbroken(vname, view_obj, ax, ay)
    tx, ty = ux / _M_TO_MM, uy / _M_TO_MM
    ext = drawing.Extension
    drawing.ClearSelection2(True)

    # 1) the sketch point FIRST — SelectByID2 with Callout=None raises "Type mismatch" (arg 8) under
    #    late binding, so try a VT_DISPATCH-null VARIANT, then the old IModelDoc2.SelectByID
    #    (5 args, no append flag — hence the point goes first and the edge is appended after).
    import pythoncom
    from win32com.client import VARIANT
    null_disp = VARIANT(pythoncom.VT_DISPATCH, None)
    ok2, how = False, "none"
    for nm in names + [""]:
        log("sharp: SelectByID2(VARIANT null callout) SKETCHPOINT name=%r at %s", nm, ip)
        try:
            if bool(ext.SelectByID2(nm, "SKETCHPOINT", ip[0], ip[1], 0.0, False, 0, null_disp, 0)):
                ok2, how = True, f"SelectByID2({nm!r})"
                break
        except Exception as ex:  # noqa: BLE001
            log("sharp: SelectByID2 %r raised %s", nm, ex)
        log("sharp: SelectByID SKETCHPOINT name=%r at %s", nm, ip)
        try:
            if bool(drawing.SelectByID(nm, "SKETCHPOINT", ip[0], ip[1], 0.0)):
                ok2, how = True, f"SelectByID({nm!r})"
                break
        except Exception as ex:  # noqa: BLE001
            log("sharp: SelectByID %r raised %s", nm, ex)
    log("sharp: point selected=%s via %s", ok2, how)

    # 2) reference edge, APPENDED to the selection
    ok1, how1 = False, "none"
    if ok2:
        log("sharp: IView.SelectEntity(edge, True)")
        ok1 = bool(view_obj.SelectEntity(edges[int(e_ref)], True))
        how1 = "SelectEntity(append)"
        log("sharp: edge selected=%s via %s", ok1, how1)
        try:
            n_sel = int(slip._inv(drawing.SelectionManager, "GetSelectedObjectCount2", -1))
        except Exception:  # noqa: BLE001
            try:
                n_sel = int(drawing.SelectionManager.GetSelectedObjectCount2(-1))
            except Exception:  # noqa: BLE001
                n_sel = -1
        log("sharp: selection count=%s (need 2)", n_sel)
        if n_sel != -1 and n_sel < 2:
            ok1 = False
    if not ok1 or not ok2:
        removed = False
        try:
            drawing.ClearSelection2(True)
            log("sharp: removing the point (Select2 + EditDelete, as delete_view_sketch_points)")
            if bool(pt.Select2(False, 0)):
                drawing.EditDelete()
                removed = True
        except Exception as ex:  # noqa: BLE001
            log("sharp: point removal raised %s", ex)
            removed = False
        raise SWError(f"selection failed (edge {ok1} via {how1}, sketch point {ok2} via {how}) - "
                      f"point at {ip} {'removed by undo' if removed else 'LEFT IN VIEW (run delete_view_sketch_points)'}")

    # 3) AddDimension2 at the text point — as the macro
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:  # noqa: BLE001
        pass
    disp = None
    try:
        log("sharp: AddDimension2(%s, %s)", tx, ty)
        try:
            disp = drawing.AddDimension2(tx, ty, 0)
        except Exception as ex:  # noqa: BLE001
            log("sharp: AddDimension2 raised %s", ex)
            disp = None
        if disp is None:
            log("sharp: Extension.AddDimension(%s, %s, 0, 0)", tx, ty)
            try:
                disp = ext.AddDimension(tx, ty, 0, 0)
            except Exception as ex:  # noqa: BLE001
                log("sharp: Extension.AddDimension raised %s", ex)
                disp = None
    finally:
        if orig_pref is not None:
            try:
                app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, orig_pref)
            except Exception:  # noqa: BLE001
                pass
    log("sharp: dimension object %s", "ok" if disp is not None else "None")
    if disp is None:
        raise SWError("AddDimension returned None after selecting edge + virtual sharp")
    try:
        disp.SetUnits2(True, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0)
        disp.SetPrecision3(DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED)
    except Exception:  # noqa: BLE001
        pass
    entry = {"status": "done", "view": vname, "point_selected_via": how,
             "sharp_view_m": [round(ip[0], 6), round(ip[1], 6)],
             "sharp_model_note": "view-sketch space = model point rotated into the view (m), no scale"}
    info = slip._dim_info(disp)
    entry["name"] = info.get("name"); entry["value"] = info.get("value")
    entry["text_mm"] = info.get("text_mm") or [ax, ay]
    if reference:
        try:
            slip._put(disp, "ShowParenthesis", True)
            entry["reference"] = bool(slip._inv(disp, "ShowParenthesis"))
        except Exception as e:  # noqa: BLE001
            entry["reference_error"] = str(e)
    drawing.ClearSelection2(True)
    slip._rebuild(drawing)
    return entry


# --------------------------------------------------------------------------- batch dimensions

def _add_dimensions(view_name: str, dims: list[dict]) -> dict:
    app = SWConnection.get_instance().get_app()
    drawing = slip._active_drawing()
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"view not found: {view_name}")
    vname, view_obj = views[0]
    drawing.ActivateView(vname)
    edges = _get_view_edges(view_obj)
    if not edges:
        raise SWError(f"view {vname} has no visible edges")
    # Type-check only the edges we are about to use (dense views: 200+ edges, full probe = 20 s+)
    _type_cache: dict[int, str] = {}

    def _edge_type(i: int) -> str:
        if i not in _type_cache:
            _type_cache[i] = _read_edge_once(edges[i], i)["type"]
        return _type_cache[i]

    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:  # noqa: BLE001
        pass

    results = []
    try:
        for n, d in enumerate(dims):
            kind = (d.get("type") or "linear").lower()
            e1 = int(d["e1"])
            e2 = d.get("e2")
            text = d.get("text")
            if e1 < 0 or e1 >= len(edges):
                results.append({"n": n, "error": f"e1 {e1} out of range 0..{len(edges)-1}"})
                continue
            if kind == "linear" and (e2 is None or int(e2) < 0 or int(e2) >= len(edges) or int(e2) == e1):
                results.append({"n": n, "error": f"linear needs a distinct e2 in range (got {e2})"})
                continue
            if kind in ("diameter", "radius"):
                need = "circle" if kind == "diameter" else "arc"
                if _edge_type(e1) != need:
                    results.append({"n": n, "error": f"{kind} needs a {need} edge; e1 {e1} is {_edge_type(e1)}"})
                    continue
            if not text or len(text) != 2:
                results.append({"n": n, "error": "text [x_mm, y_mm] is required"})
                continue
            ax, ay = float(text[0]), float(text[1])
            ux, uy = slip._to_unbroken(vname, view_obj, ax, ay)
            tx, ty = ux / _M_TO_MM, uy / _M_TO_MM

            drawing.ClearSelection2(True)
            ok1 = view_obj.SelectEntity(edges[e1], False)
            ok2 = True
            if kind == "linear":
                ok2 = view_obj.SelectEntity(edges[int(e2)], True)
            if not ok1 or not ok2:
                results.append({"n": n, "error": f"SelectEntity failed ({ok1}, {ok2})"})
                continue

            ext = drawing.Extension
            disp = None
            for dd in range(4):
                try:
                    disp = ext.AddDimension(tx, ty, 0, dd)
                    if disp is not None:
                        break
                except Exception:  # noqa: BLE001
                    pass
            if disp is None:
                try:
                    disp = drawing.AddDimension2(tx, ty, 0)
                except Exception:  # noqa: BLE001
                    disp = None
            if disp is None:
                results.append({"n": n, "error": "AddDimension returned None"})
                continue
            try:
                disp.SetUnits2(True, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0)
                disp.SetPrecision3(DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED)
            except Exception:  # noqa: BLE001
                pass
            entry = {"n": n, "type": kind}
            info = slip._dim_info(disp)
            entry["name"] = info.get("name")
            entry["value"] = info.get("value")
            entry["text_mm"] = info.get("text_mm") or [round(tx * _M_TO_MM, 2), round(ty * _M_TO_MM, 2)]
            if d.get("reference"):
                try:
                    slip._put(disp, "ShowParenthesis", True)
                    entry["reference"] = bool(slip._inv(disp, "ShowParenthesis"))
                except Exception as e:  # noqa: BLE001
                    entry["reference_error"] = str(e)
            results.append(entry)
        drawing.ClearSelection2(True)
        slip._rebuild(drawing)
    finally:
        if orig_pref is not None:
            try:
                app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, orig_pref)
            except Exception:  # noqa: BLE001
                pass
    ok = sum(1 for r in results if "error" not in r)
    return {"status": "done" if ok == len(dims) else "partial", "added": ok, "requested": len(dims),
            "view": vname, "dimensions": results}


# --------------------------------------------------------------------------- R00 finish

_NOTES_BLOCK_CANDIDATES = ("DetailItem1814",)


def _find_notes_block(drawing):
    """The Slip template's numbered notes block: the sheet note containing 'INTERPRET ALL'."""
    for note in slip._notes_on_sheet(drawing):
        try:
            info = slip._note_info(note)
        except Exception:  # noqa: BLE001
            continue
        txt = (info.get("linked_text") or info.get("text") or "")
        if "INTERPRET ALL DIMENSIONS" in txt.upper():
            return note, info
    raise SWError("notes block not found on current sheet (no note containing 'INTERPRET ALL DIMENSIONS')")


def _finish_slip_r00(iso_view, finish_text, delete_sheet2: bool) -> dict:
    drawing = slip._active_drawing()
    report: dict = {"status": "done", "steps": []}

    # 1. display style
    try:
        r = slip._set_view_display(None, "hidden_lines_removed", "removed")
        report["steps"].append({"display_all": r.get("views"), "errors": r.get("errors") or None})
    except Exception as e:  # noqa: BLE001
        report["steps"].append({"display_all_error": str(e)})
    if iso_view:
        try:
            r = slip._set_view_display(iso_view, "hidden_lines_removed", "visible")
            report["steps"].append({"iso_visible": r.get("views"), "errors": r.get("errors") or None})
        except Exception as e:  # noqa: BLE001
            report["steps"].append({"iso_visible_error": str(e)})

    # 2. Sheet2
    if delete_sheet2:
        try:
            sheets = slip._sheet_names(drawing)
            if "Sheet2" in sheets:
                r = slip._delete_sheet("Sheet2")
                report["steps"].append({"deleted_sheet": "Sheet2", "sheets": r.get("sheets")})
            else:
                report["steps"].append({"deleted_sheet": None, "sheets": sheets})
        except Exception as e:  # noqa: BLE001
            report["steps"].append({"delete_sheet_error": str(e)})

    # 3. notes
    try:
        note, info = _find_notes_block(drawing)
        name = info["name"]
        src = info.get("linked_text") or ""
        edits = []
        frag = ', ($PRPSHEET:"BEND_RADIUS" IN BEND RADIUS).'
        if frag in src:
            src = src.replace(frag, "."); edits.append("bend_radius_fragment")
        # Every note ends with a period (Simon, 2026-09-09): the template's FINISH line has none.
        fin_lit = (f"FINISH: {finish_text.rstrip('.')}." if finish_text else 'FINISH: $PRPSHEET:"FINISH".')
        for fin in ('FINISH: $PRPSHEET:"FINISH", $PRPSHEET:"FINISH_COLOR".',
                    'FINISH: $PRPSHEET:"FINISH", $PRPSHEET:"FINISH_COLOR"',
                    'FINISH: $PRPSHEET:"FINISH".', 'FINISH: $PRPSHEET:"FINISH"'):
            if fin in src:
                src = src.replace(fin, fin_lit)
                edits.append(f"finish_literal:{finish_text}." if finish_text else "finish_color_dropped+period")
                break
        # (FINISH literal lines that were previously written without a period get one)
        import re as _re
        src, n_fix = _re.subn(r'(FINISH: [A-Z0-9 ,\-]+?)(?<!\.)(\r\n|<PARA)', r'\1.\2', src)
        if n_fix:
            edits.append("finish_period_added")
        lines = src.split("\r\n")
        for key, label in (("MASK AREAS SHOWN FROM FINISH", "mask_paragraph"), ("WHERE SHOWN", "revflag_paragraph")):
            hits = [i for i, ln in enumerate(lines) if key in ln]
            if len(hits) == 1:
                i = hits[0]
                del lines[i]
                if i < len(lines) and slip._is_blank_para(lines[i]):
                    del lines[i]
                elif i > 0 and slip._is_blank_para(lines[i - 1]):
                    del lines[i - 1]
                edits.append(label)
        new_src = "\r\n".join(lines)
        if new_src != (info.get("linked_text") or ""):
            slip._put(note, "PropertyLinkedText", new_src)
            slip._rebuild(drawing)
        after = slip._note_info(note)
        text_lines = [ln for ln in (after.get("text") or "").replace("\r\n", "\n").split("\n") if ln.strip()]
        report["notes"] = {"note": name, "edits": edits, "lines": text_lines}
    except Exception as e:  # noqa: BLE001
        report["notes_error"] = str(e)
        report["status"] = "partial"

    return report
