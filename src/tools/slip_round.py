"""Machined round parts (§5d): dimension a SECTION view by picking its edges at sheet points.

Section-face edges (the cut outline, a cross hole cut square to its axis) are not returned by
IView.GetVisibleEntities, so view_geometry/add_dimensions cannot reach them. Here every entity
is picked at a sheet point (mm) with SelectByRay / SelectByID2 and dimensioned with
AddDimension2. First used on 107045 (McMaster 93392A240 clevis pin, 2026-10-21).
"""
from __future__ import annotations

import logging

from errors import SWError
from tools import slip

logger = logging.getLogger(__name__)
_MM = 1000.0
_IN = 0.0254
SW_SEL_EDGES = 1
SW_SEL_SILHOUETTES = 27


def _selected_type(drawing, idx):
    try:
        sm = slip._inv(drawing, "SelectionManager")
        return int(slip._inv(sm, "GetSelectedObjectType3", idx, -1))
    except Exception:  # noqa: BLE001
        return -1


def _sel_count(drawing):
    try:
        sm = slip._inv(drawing, "SelectionManager")
        return int(slip._inv(sm, "GetSelectedObjectCount2", -1))
    except Exception:  # noqa: BLE001
        return -1


def _pick(drawing, pt_mm, append):
    import pythoncom
    from win32com.client import VARIANT
    null_disp = VARIANT(pythoncom.VT_DISPATCH, None)
    ext = drawing.Extension
    before = _sel_count(drawing) if append else 0
    x, y = pt_mm[0] / _MM, pt_mm[1] / _MM
    tries = []
    for typ in ("EDGE", "SILHOUETTE"):
        tries.append(("id2:" + typ, lambda typ=typ: ext.SelectByID2("", typ, x, y, 0.0, append, 0, null_disp, 0)))
    for st in (SW_SEL_EDGES, SW_SEL_SILHOUETTES):
        for r in (0.0005, 0.0015):
            tries.append((f"ray:{st}:{r}", lambda st=st, r=r: ext.SelectByRay(x, y, 0.0, 0.0, 0.0, -1.0, r, st, append, 0, 0)))
    for label, fn in tries:
        try:
            ok = bool(fn())
        except Exception as ex:  # noqa: BLE001
            logger.info("pick %s at %s raised %s", label, pt_mm, ex)
            ok = False
        if ok and _sel_count(drawing) > before:
            return label, _selected_type(drawing, _sel_count(drawing))
    return None, None


def _apply_tol(dim, tol):
    """tol = [upper, lower] in inches, e.g. [0, -0.015] or [-0.002, -0.004] (bilateral)."""
    t = slip._inv(dim, "Tolerance")
    slip._put(t, "Type", 2)  # swTolBILAT
    ok = slip._inv(t, "SetValues", float(tol[1]) * _IN, float(tol[0]) * _IN)  # min, max (metres)
    return bool(ok)


def _set_arc(disp, cond_name):
    """IDimension.SetArcEndCondition (NOT IDisplayDimension): 1 centre, 2 min, 3 max. Returns 0 on success."""
    cond = {"center": 1, "min": 2, "max": 3}[cond_name]
    dim = slip._wrap(disp.GetDimension2(0))
    return [slip._inv(dim, "SetArcEndCondition", idx, cond) for idx in (1, 2)]


def fix_dims(view_feature: str, updates: list) -> dict:
    """updates: [{"name": "RD2@Drawing View5@...", "arc": "min", "tol": [up, low], "tol_precision": 3}]"""
    from tools.annotation import _get_drawing_views
    drawing = slip._active_drawing()
    views = _get_drawing_views(drawing, view_feature)
    if not views:
        raise SWError(f"view feature {view_feature!r} not found")
    by = {}
    for dd in slip._iter_display_dims(views[0][1]):
        by[slip._dim_info(dd).get("name")] = dd
    out = []
    for u in updates:
        dd = by.get(u["name"])
        if dd is None:
            out.append({"name": u["name"], "error": "not found", "have": sorted(k for k in by if k)})
            continue
        e = {"name": u["name"]}
        try:
            if u.get("arc"):
                e["arc"] = _set_arc(dd, u["arc"])
            if u.get("tol"):
                dim = slip._wrap(dd.GetDimension2(0))
                e["tol"] = _apply_tol(dim, u["tol"])
                tp = int(u.get("tol_precision", 3))
                slip._inv(dd, "SetPrecision3", 3, tp, 3, tp)
        except Exception as ex:  # noqa: BLE001
            e["error"] = str(ex)
        out.append(e)
    slip._rebuild(drawing)
    for u in out:
        dd = by.get(u["name"])
        if dd is not None:
            u.update({k: v for k, v in slip._dim_info(dd).items() if k == "value"})
    return {"status": "done", "updates": out}


def dims_by_points(view_name: str, dims: list, precision: int = 3) -> dict:
    """dims: [{"p": [[x,y],[x,y]]  (one point = single-entity dim, e.g. a circle's Ø),
               "text": [x,y], "arc": "min"|"max"|None, "prefix": "<MOD-DIAM>",
               "tol": [upper, lower] (in), "reference": bool, "tol_precision": 3}]"""
    drawing = slip._active_drawing()
    if not drawing.ActivateView(view_name):
        raise SWError(f"ActivateView({view_name!r}) failed — use the IView name, e.g. 'Section View A-A'")
    out = []
    for n, d in enumerate(dims):
        drawing.ClearSelection2(True)
        picks = []
        for i, p in enumerate(d["p"]):
            lab, typ = _pick(drawing, p, append=i > 0)
            picks.append({"at": p, "how": lab, "type": typ})
        if any(pk["how"] is None for pk in picks):
            out.append({"n": n, "error": "pick failed", "picks": picks})
            continue
        tx, ty = d["text"][0] / _MM, d["text"][1] / _MM
        try:
            disp = drawing.AddDimension2(tx, ty, 0.0)
        except Exception as ex:  # noqa: BLE001
            disp = None
            logger.info("AddDimension2 raised %s", ex)
        if disp is None:
            out.append({"n": n, "error": "AddDimension2 returned None", "picks": picks})
            continue
        disp = slip._wrap(disp) if hasattr(slip, "_wrap") else disp
        entry = {"n": n, "picks": picks}
        try:
            slip._inv(disp, "SetPrecision3", precision, -1, precision, -1)
        except Exception as ex:  # noqa: BLE001
            entry["precision_error"] = str(ex)
        if d.get("arc"):
            try:
                entry["arc"] = _set_arc(disp, d["arc"])
            except Exception as ex:  # noqa: BLE001
                entry["arc"] = str(ex)
        if d.get("prefix") is not None:
            try:
                slip._inv(disp, "SetText", 1, d["prefix"])
            except Exception as ex:  # noqa: BLE001
                entry["prefix_error"] = str(ex)
        if d.get("tol"):
            try:
                dim = slip._wrap(disp.GetDimension2(0)) if hasattr(slip, "_wrap") else disp.GetDimension2(0)
                entry["tol"] = _apply_tol(dim, d["tol"])
                tp = int(d.get("tol_precision", precision))
                slip._inv(disp, "SetPrecision3", precision, tp, precision, tp)
            except Exception as ex:  # noqa: BLE001
                entry["tol_error"] = str(ex)
        if d.get("reference"):
            try:
                slip._put(disp, "ShowParenthesis", True)
            except Exception as ex:  # noqa: BLE001
                entry["reference_error"] = str(ex)
        info = slip._dim_info(disp)
        entry.update({"name": info.get("name"), "value": info.get("value"), "text_mm": info.get("text_mm")})
        if entry.get("value") is not None:
            entry["value_in"] = round(entry["value"] / 25.4, 4) if entry["value"] > 3 else entry["value"]
        out.append(entry)
    drawing.ClearSelection2(True)
    slip._rebuild(drawing)
    return {"status": "done", "view": view_name, "dims": out}


def section_view(parent_view: str, line_mm: list, pos_mm: list, label: str = "A", api: str = "at2", flip: bool = False) -> dict:
    """Section view of a PART through a sketched line (sheet mm) in parent_view (IView name).
    api="at2": CreateSectionViewAt2 (part-style section; At5 produced a 'SectionAssemView' with no hatch
    and no visible cutting line on 107045)."""
    drawing = slip._active_drawing()
    if not drawing.ActivateView(parent_view):
        raise SWError(f"ActivateView({parent_view!r}) failed")
    drawing.ClearSelection2(True)
    (x1, y1), (x2, y2) = line_mm
    seg = drawing.SketchManager.CreateLine(x1 / _MM, y1 / _MM, 0.0, x2 / _MM, y2 / _MM, 0.0)
    if seg is None:
        raise SWError("could not sketch the section line")
    px, py = pos_mm[0] / _MM, pos_mm[1] / _MM
    if api == "at2":
        v = drawing.CreateSectionViewAt2(px, py, 0.0, False, False, label, bool(flip), False, False, False)
    elif api == "at4":
        import pythoncom
        from win32com.client import VARIANT
        v = drawing.CreateSectionViewAt4(px, py, 0.0, label, 4 if flip else 0, VARIANT(pythoncom.VT_DISPATCH, None))
    else:
        drawing.CreateSectionView()
        v = None
    slip._rebuild(drawing)
    out = {"status": "done", "api": api}
    if v is not None:
        try:
            out["name"] = str(slip._inv(v, "Name"))
            out["hatches"] = int(slip._inv(v, "GetFaceHatchCount"))
        except Exception as ex:  # noqa: BLE001
            out["read_error"] = str(ex)
    return out


def release_rev(rev: str = "R00", description: str = "INITIAL RELEASE", date: str = "", drawn_by: str = "",
                set_drawing_prop: bool = True) -> dict:
    """Fill the General-Table revision block's first data row and (optionally) the DRAWING's own
    REVISION custom property (for parts whose model must not be written, e.g. vault COTS parts)."""
    from pythoncom import DISPATCH_PROPERTYPUT
    drawing = slip._active_drawing()
    out = {}
    if set_drawing_prop:
        cpm = drawing.Extension.CustomPropertyManager("")
        try:
            out["prop_add"] = int(cpm.Add3("REVISION", 30, rev, 2))  # swCustomInfoText, swCustomPropertyReplaceValue
        except Exception as ex:  # noqa: BLE001
            out["prop_error"] = str(ex)
    sheet_view = slip._wrap(slip._inv(drawing, "GetFirstView"))
    tables = slip._inv(sheet_view, "GetTableAnnotations") or ()
    done = False
    for t in tables:
        t = slip._wrap(t)
        rows, cols = int(slip._inv(t, "RowCount")), int(slip._inv(t, "ColumnCount"))
        hdr = [str(slip._inv(t, "Text", 0, c)).strip().upper() for c in range(cols)]
        if "DESCRIPTION" not in hdr or "REV" not in hdr:
            continue
        vals = {"DESCRIPTION": description, "DATE": date, "DRAWN BY": drawn_by, "APPROVED BY": drawn_by, "REV": rev}
        ole = slip._ole(t)
        dispid = ole.GetIDsOfNames(0, "Text")
        for c, h in enumerate(hdr):
            if h in vals and rows > 1:
                ole.Invoke(dispid, 0, DISPATCH_PROPERTYPUT, False, 1, c, vals[h])
        out["table"] = {"header": hdr, "row1": [str(slip._inv(t, "Text", 1, c)) for c in range(cols)], "rows": rows}
        done = True
        break
    out["table_found"] = done
    slip._rebuild(drawing)
    return out
