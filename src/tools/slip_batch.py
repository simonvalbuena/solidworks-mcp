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


def _model_to_sheet_fn(app, view_obj):
    """Return f(x_m, y_m, z_m) -> (X_mm, Y_mm) in sheet space, or None if unavailable."""
    try:
        xform = view_obj.ModelToViewTransform
        mu = app.GetMathUtility
        if xform is None or mu is None:
            return None
    except Exception as e:  # noqa: BLE001
        logger.debug("ModelToViewTransform unavailable: %s", e)
        return None

    def f(x, y, z):
        pt = mu.CreatePoint((float(x), float(y), float(z)))
        pt2 = pt.MultiplyTransform(xform)
        d = pt2.ArrayData
        return float(d[0]) * _M_TO_MM, float(d[1]) * _M_TO_MM

    return f


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


def _sheet_edges(app, view_obj, edges, edges_info) -> list[dict]:
    """Edge list with sheet-space coordinates; self-calibrated against the view outline."""
    to_sheet = _model_to_sheet_fn(app, view_obj)
    out = []
    for info, edge in zip(edges_info, edges):
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
        # keep the model midpoint so the entry can also be passed to add_dimension (probe format)
        mp = info.get("midpoint")
        if mp:
            rec["model_mid"] = [mp["x"], mp["y"]]
        out.append(rec)

    # self-calibration: the transformed bbox must coincide with GetOutline (which includes
    # a small margin) — if the centres differ, shift everything.
    outline = _outline_mm(view_obj)
    pts = [p for r in out for p in (r.get("s"), r.get("e"), r.get("c")) if p]
    if outline and pts:
        bx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
        by = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
        ox = (outline[0] + outline[2]) / 2
        oy = (outline[1] + outline[3]) / 2
        dx, dy = ox - bx, oy - by
        if abs(dx) > 1.0 or abs(dy) > 1.0:
            for r in out:
                for k in ("s", "e", "c", "m"):
                    if k in r:
                        r[k] = [round(r[k][0] + dx, 2), round(r[k][1] + dy, 2)]
    return out


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
        edges_info = _build_edges_info(edges)
        sheet_edges = _sheet_edges(app, view_obj, edges, edges_info)
        scale = _view_scale(view_obj)
        summ = _summarize(sheet_edges)
        if scale:
            if "width_sheet_mm" in summ:
                summ["width_in"] = round(summ["width_sheet_mm"] / scale / _IN, 4)
            if "height_sheet_mm" in summ:
                summ["height_in"] = round(summ["height_sheet_mm"] / scale / _IN, 4)
            for g in summ.get("circles", []):
                g["dia_in"] = round(2 * g["r_sheet_mm"] / scale / _IN, 4)
        entry = {"view": vname, "outline_mm": _outline_mm(view_obj), "scale": scale,
                 "edge_count": len(sheet_edges),
                 "sheet_coords": "ok" if any("s" in r or "c" in r for r in sheet_edges) else "UNAVAILABLE (ModelToViewTransform failed — fall back to probe_drawing_edges)",
                 "summary": summ}
        if include_edges:
            entry["edges"] = sheet_edges
        result.append(entry)
    return {"status": "done", "views": result}


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
    edges_info = _build_edges_info(edges)

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
                if edges_info[e1]["type"] != need:
                    results.append({"n": n, "error": f"{kind} needs a {need} edge; e1 {e1} is {edges_info[e1]['type']}"})
                    continue
            if not text or len(text) != 2:
                results.append({"n": n, "error": "text [x_mm, y_mm] is required"})
                continue
            tx, ty = float(text[0]) / _M_TO_MM, float(text[1]) / _M_TO_MM

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
        fin = 'FINISH: $PRPSHEET:"FINISH", $PRPSHEET:"FINISH_COLOR"'
        if fin in src:
            src = src.replace(fin, f"FINISH: {finish_text}" if finish_text else 'FINISH: $PRPSHEET:"FINISH"')
            edits.append("finish_color" if not finish_text else f"finish_literal:{finish_text}")
        elif finish_text and 'FINISH: $PRPSHEET:"FINISH"' in src:
            src = src.replace('FINISH: $PRPSHEET:"FINISH"', f"FINISH: {finish_text}")
            edits.append(f"finish_literal:{finish_text}")
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
