"""Slip one-call drawing pipeline (Track B): the layout / dimensioning / flat-pattern rules of
Simón Valbuena's drawing standard, applied server-side so a bracket drawing takes ~6 MCP calls
instead of ~40. Composes the verified internals of slip_batch / slip_views / slip / slip_tube.

    part_brief(model_path)               -> opens the part, bbox, props, sheet-metal flags, base view suggestion
    layout_slip_part(model_path, ...)    -> base model view + projected edge/face views + iso, scale, positions,
                                            HLR/tangent, save to C:\\Drawings\\<PN>.SLDDRW
    dimension_slip_views(...)            -> envelope + ONE located hole per face, (thk), (R), flange height,
                                            text placed per §7b rules
    slip_flat_sheet(model_path, ...)     -> flat pattern on Sheet2, oriented like the base view,
                                            overall + edge-to-bend-line dims
    finish_slip_r00 (slip_batch)         -> display / notes (+ this module's export_slip_pdf)

Sheet constants are for the Slip D-size SR 2025 template (863.6 x 558.8 mm): notes block top-left
(x < ~330, y > ~455), title block bottom-right (x > 580, y < 120), rev table top-right (y > 480).
"""
from __future__ import annotations

import glob
import json
import logging
import math
import os

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection
from tools import slip
from tools import slip_batch as sb
from tools import slip_views as sv
from tools.annotation import _get_drawing_views

logger = logging.getLogger(__name__)

_M_TO_MM = 1000.0
_IN = 25.4

# ---- layout rules (sheet mm) -------------------------------------------------------------
SHEET_W, SHEET_H = 863.6, 558.8
STACK_CX, STACK_CY = 400.0, 262.0          # centre of the base view + bottom projection stack
BASE_MAX_W, BASE_MAX_H = 330.0, 200.0      # base view footprint target (~1/3 of the sheet)
STACK_MAX_H = 330.0                        # base + gap + bottom projection
GAP_V, GAP_H = 72.0, 62.0                  # view-to-view gaps (Simón 108716: ~75 mm between plan and edge view)
ISO_POS = (770.0, 180.0)
STD_SCALES = (0.125, 0.1667, 0.2, 0.25, 0.3333, 0.5, 0.6667, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
# ---- dimension placement rules (§7b) ----
INNER_OFF = 24.0       # hole-location dims off the view bbox
OUTER_STEP = 17.0      # envelope dims a little further out
DIA_OFF = 26.0         # Ø text clear of the view / dim lines
THK_TEXT_ALONG = 18.0  # tiny (thk) dims: text offset ALONG the dimension direction
PROJ_BAND = 14.0       # projected (edge/side) views: hole dims sit in one tight row ~14 mm off the view (Simón 108716)
ROW_OFF = 14.0         # (thk) / (R) texts share that row
SHORT_H, SHORT_V = 22.0, 8.0   # a horizontal dim needs ~22 mm for its text; a vertical one ~8 mm (text stacks beside)

ORIENT = {"top": "*Top", "bottom": "*Bottom", "front": "*Front", "back": "*Back",
          "right": "*Right", "left": "*Left", "iso": "*Isometric"}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def part_brief(model_path: str) -> str:
        """Open (or activate) a part and return what the Slip layout needs in one call: bounding
        box (in), the thin axis, sheet-metal / bend detection from the feature tree, flat-pattern
        configuration, title-block properties (MATERIAL, THICKNESS, BEND_RADIUS, FINISH, TITLE,
        REVISION) and a suggested base view + edge views. Replaces open_document + get_feature_tree
        + inspect_model + analyze_model."""
        return await _run(sw, "part_brief", _part_brief, model_path)

    @mcp.tool()
    async def layout_slip_part(
        model_path: str,
        base_view: str = "auto",
        edge_views: str = "auto",
        scale: float | None = None,
        save_path: str | None = None,
        rotate_base_deg: float = 0.0,
        create_new: bool = False,
    ) -> str:
        """Lay out a Slip part drawing on the ACTIVE drawing (or, create_new=True / no drawing
        open, on a new one from the Slip D-size template SR FAB D DWG 2026): ONE base model view (base_view: top|front|
        right|... or 'auto' = look along the thin axis), PROJECTED views (edge_views: 'auto' =
        'bottom' for a flat plate, 'bottom,right' for a bent part; or a comma list), a small iso at
        lower-right, standard scale chosen to fit ~1/3 of the sheet (or `scale`), HLR + tangent
        edges removed (iso visible), saved to save_path (default C:\\Drawings\\<PN>.SLDDRW).
        Returns view names, roles, outlines and the scale — feed to dimension_slip_views."""
        return await _run(sw, "layout_slip_part", _layout_slip_part, model_path, base_view,
                          edge_views, scale, save_path, rotate_base_deg, create_new)

    @mcp.tool()
    async def dimension_slip_views(
        base_view: str,
        edge_view: str | None = None,
        side_view: str | None = None,
        thickness_in: float | None = None,
        bend_radius_in: float | None = None,
        base_hole: str = "top_left",
    ) -> str:
        """Apply the Slip dimension set: base view = envelope W/H + ONE located hole (X, Y, Ø;
        base_hole: top_left|largest|none); edge_view (bottom projection) = (thk) reference when it
        is a thin edge, else ONE located hole of that face; side_view (right projection) = flange
        height + (thk) + (R) reference. Text positions follow §7b (inner ~24 mm, envelope +17 mm,
        Ø text ~26 mm, top/left preferred, no crossings). Returns names and values only."""
        return await _run(sw, "dimension_slip_views", _dimension_slip_views, base_view, edge_view,
                          side_view, thickness_in, bend_radius_in, base_hole)

    @mcp.tool()
    async def slip_flat_sheet(
        model_path: str,
        base_view: str,
        scale: float | None = None,
        sheet_name: str = "Sheet2",
        x_mm: float = 431.8,
        y_mm: float = 279.4,
        match_base_view: bool = False,
    ) -> str:
        """Flat-pattern sheet: activates sheet_name, inserts the flat pattern view (configuration ""
        -> SM-FLAT-PATTERN, bend notes) centred at (x_mm, y_mm), rotates 90° only if it would not
        fit the sheet (Simón 2026-09-17: the flat need not match the formed views' orientation;
        match_base_view=True restores the hole-pattern matching), HLR/tangent removed, then
        dimensions flat overall W/H + every edge-to-bend-line distance (2026 form, no parentheses)."""
        return await _run(sw, "slip_flat_sheet", _slip_flat_sheet, model_path, base_view, scale,
                          sheet_name, x_mm, y_mm, match_base_view)

    @mcp.tool()
    async def export_slip_pdf(pdf_path: str | None = None, save: bool = True, png_per_sheet: bool = False) -> str:
        """Save the active drawing and export it to PDF (default <drawing dir>\\<PN>-<REV>.pdf,
        REV from the model's REVISION property or R00); png_per_sheet=True also writes
        <PN>-<REV>-Sheet1.png / -Sheet2.png for the visual check. SaveAs3 with ByRef VARIANTs."""
        return await _run(sw, "export_slip_pdf", _export_slip_pdf, pdf_path, save, png_per_sheet)

    @mcp.tool()
    async def make_slip_drawing(
        model_path: str,
        base_view: str = "auto",
        edge_views: str = "auto",
        scale: float | None = None,
        save_dir: str = r"C:\Drawings",
        bend_radius_text: str | None = None,
        finish_text: str | None = None,
        export: bool = True,
    ) -> str:
        """The whole Slip R00 part drawing in ONE call: part_brief -> new drawing from the Slip
        D-size template -> layout_slip_part -> dimension_slip_views -> slip_flat_sheet (bent parts)
        -> finish_slip_r00 (notes, display, bend-radius literal = bend_radius_text or the measured
        (R)) -> save + PDF + one PNG per sheet. Each stage runs as its own COM job; the report
        carries every stage's result and stops at the first stage that is not clean ("partial"),
        so the step tools can repair from there. Returns the combined report."""
        return await _make_slip_drawing(sw, model_path, base_view, edge_views, scale, save_dir,
                                        bend_radius_text, finish_text, export)


def _write_report(save_dir, pn, rep) -> None:
    """The bridge may time out before a long run returns: the report is always on disk too."""
    try:
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, f"{pn or 'last'}-report.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    except Exception as ex:  # noqa: BLE001
        logger.info("report write failed: %s", ex)


async def _make_slip_drawing(sw, model_path, base_view, edge_views, scale, save_dir, bend_radius_text, finish_text, export):
    import time
    from tools import slip_batch as _sb
    t0 = time.time()
    pn = os.path.splitext(os.path.basename(model_path))[0]
    rep: dict = {"status": "done", "stages": {}}

    async def stage(name, fn, *args):
        t = time.time()
        try:
            r = await sw.execute(fn, *args)
        except SWError as e:
            r = {"status": "error", "error": str(e)}
        except Exception as e:  # noqa: BLE001
            logger.exception("make_slip_drawing stage %s crashed", name)
            r = {"status": "error", "error": f"{type(e).__name__}: {e}"}
        r["seconds"] = round(time.time() - t, 1)
        rep["stages"][name] = r
        return r

    brief = await stage("brief", _part_brief, model_path)
    if brief.get("status") != "done":
        rep["status"] = "partial"; rep["seconds"] = round(time.time() - t0, 1)
        _write_report(save_dir, pn, rep)
        return json.dumps(rep, ensure_ascii=False)
    save_path = os.path.join(save_dir, f"{pn}.SLDDRW")
    lay = await stage("layout", _layout_slip_part, model_path, base_view, edge_views, scale, save_path, 0.0, True)
    if lay.get("status") != "done":
        rep["status"] = "partial"; rep["seconds"] = round(time.time() - t0, 1)
        _write_report(save_dir, pn, rep)
        return json.dumps(rep, ensure_ascii=False)
    roles = {v.get("role"): v.get("name") for v in lay.get("views", []) if v.get("name")}
    thk = brief.get("thickness_in")
    br = brief.get("bend_radius_in")
    dims = await stage("dimensions", _dimension_slip_views, roles.get("base"), roles.get("edge"), roles.get("side"), thk, br, "top_left")
    if dims.get("status") != "done":
        rep["status"] = "partial"; rep["seconds"] = round(time.time() - t0, 1)
        _write_report(save_dir, pn, rep)
        return json.dumps(rep, ensure_ascii=False)
    # measured bend radius (R) for the material note when the property is blank
    measured_r = None
    for vname, vr in dims.get("views", {}).items():
        for d in vr.get("dims", []) if isinstance(vr, dict) else []:
            if d.get("label") == "bend_r" and d.get("value"):
                measured_r = d["value"]
    bent = bool(brief.get("bent"))
    if bent:
        flat = await stage("flat_sheet", _slip_flat_sheet, model_path, roles.get("base"), None, "Sheet2", 431.8, 279.4, False)
        if flat.get("status") != "done":
            rep["status"] = "partial"; rep["seconds"] = round(time.time() - t0, 1)
            return json.dumps(rep, ensure_ascii=False)
    br_text = bend_radius_text
    keep_link = bool(bent and br is not None and br_text is None)   # property filled: keep the $PRPSHEET link ...
    if br_text is None and bent:
        # ... with a literal fallback (property value, else the measured (R)) for when the link resolves to "-"
        br_text = (f"{br:.4g}" if br is not None else (f"{measured_r:.3f}" if measured_r else None))
    fin_text = finish_text
    if fin_text is None and not (brief.get("props") or {}).get("FINISH"):
        fin_text = "NONE"
    # bent part with a filled BEND_RADIUS property: keep the $PRPSHEET link in the material note (108689)
    fin = await stage("finish", _sb._finish_slip_r00, roles.get("iso"), fin_text, not bent, br_text, "Sheet1", False, keep_link)
    if fin.get("status") != "done":
        rep["status"] = "partial"
    if export:
        ex = await stage("export", _export_slip_pdf, None, True, True)
        if ex.get("status") != "done":
            rep["status"] = "partial"
    rep["views"] = roles
    rep["seconds"] = round(time.time() - t0, 1)
    _write_report(save_dir, pn, rep)
    return json.dumps(rep, ensure_ascii=False)


async def _run(sw, tool_name, fn, *args):
    try:
        return json.dumps(await sw.execute(fn, *args), ensure_ascii=False)
    except SWError as e:
        raise ToolError(f"{tool_name} failed: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("%s crashed", tool_name)
        raise ToolError(f"{tool_name} failed: {type(e).__name__}: {e}") from e


# --------------------------------------------------------------------------- helpers

def _val(obj, name):
    return sv._val(obj, name)


def _try(fn, default=None):
    try:
        return fn()
    except Exception as ex:  # noqa: BLE001
        logger.debug("try %s: %s", getattr(fn, "__name__", "fn"), ex)
        return default


def _byref_i4():
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)


def _open_part(app, model_path: str):
    """Open the part (silent) or reuse the already-open document; activate it; return the doc."""
    errs, warns = _byref_i4(), _byref_i4()
    doc = None
    try:
        doc = app.OpenDoc6(model_path, 1, 1, "", errs, warns)  # swDocPART=1, silent
    except Exception as ex:  # noqa: BLE001
        logger.info("OpenDoc6 raised %s", ex)
    if doc is None:
        # already open under another path spelling? find by file name
        base = os.path.basename(model_path).lower()
        d = _try(lambda: app.GetFirstDocument)
        while d is not None:
            if _try(lambda: str(_val(d, "GetPathName")).lower().endswith(base)):
                doc = d
                break
            d = _try(lambda: _val(d, "GetNext"))
    if doc is None:
        raise SWError(f"could not open {model_path} (errors={errs.value}, warnings={warns.value})")
    title = _try(lambda: str(_val(doc, "GetTitle"))) or os.path.basename(model_path)
    _try(lambda: sv._activate_doc(app, title))
    return doc


def _custom_props(doc, names=("MATERIAL", "THICKNESS", "BEND_RADIUS", "FINISH", "FINISH_COLOR",
                              "TITLE", "REVISION", "TYPE", "MODULE", "COMPONENT", "WEIGHT")) -> dict:
    out = {}
    for cfg in ("", ):
        try:
            cpm = doc.Extension.CustomPropertyManager(cfg)
        except Exception:  # noqa: BLE001
            continue
        import pythoncom
        from win32com.client import VARIANT
        for n in names:
            v = None
            try:  # resolved value (Get5 ByRef strings) — Get() returns the unevaluated "Thickness@..." link
                val = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
                res = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BSTR, "")
                wr = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
                cpm.Get5(n, False, val, res, wr)
                v = res.value or val.value
            except Exception as ex:  # noqa: BLE001
                logger.debug("Get5 %s: %s", n, ex)
            if v in (None, ""):
                v = _try(lambda n=n: cpm.Get(n))
            if v not in (None, ""):
                out[n] = str(v)
    return out


def _feature_type_names(doc) -> list[str]:
    names = []
    feat = _try(lambda: doc.FirstFeature)
    guard = 0
    while feat is not None and guard < 500:
        guard += 1
        t = _try(lambda: str(_val(feat, "GetTypeName2")))
        if t:
            names.append(t)
        # sub-features (BaseBend under a Base-Flange, bends under a flat pattern, ...)
        sub = _try(lambda: _val(feat, "GetFirstSubFeature"))
        g2 = 0
        while sub is not None and g2 < 200:
            g2 += 1
            st_ = _try(lambda: str(_val(sub, "GetTypeName2")))
            if st_:
                names.append(st_)
            sub = _try(lambda: _val(sub, "GetNextSubFeature"))
        feat = _try(lambda: _val(feat, "GetNextFeature"))
    return names


def _part_box_in(doc):
    box = slip._inv(doc, "GetPartBox", True)
    b = [float(v) for v in box]
    return [b[0] / _IN * _M_TO_MM, b[1] / _IN * _M_TO_MM, b[2] / _IN * _M_TO_MM,
            b[3] / _IN * _M_TO_MM, b[4] / _IN * _M_TO_MM, b[5] / _IN * _M_TO_MM]


def _std_scale(s: float) -> float:
    cands = [c for c in STD_SCALES if c <= s + 1e-9]
    return cands[-1] if cands else STD_SCALES[0]


def _parse_num(s):
    try:
        return float(str(s).strip().split()[0].replace('"', ""))
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- part_brief

def _brief_dict(app, doc, model_path) -> dict:
    box = _part_box_in(doc)
    size = [round(box[3] - box[0], 4), round(box[4] - box[1], 4), round(box[5] - box[2], 4)]
    axes = ["x", "y", "z"]
    thin = axes[size.index(min(size))]
    props = _custom_props(doc)
    ftypes = _feature_type_names(doc)
    sheet_metal = any(t in ("SheetMetal", "FlatPattern", "SMBaseFlange") for t in ftypes)
    bends = sum(1 for t in ftypes if t in ("SketchBend", "EdgeFlange", "OneBend", "Hem", "Jog", "MiterFlange",
                                            "SM3dBend", "SMMiteredFlange", "EdgeFlangeSMFeature", "UiBend",
                                            "ProcessBends", "FlattenBends"))
    # a bent part also shows up as a thin dimension that is much larger than the thickness
    thk = _parse_num(props.get("THICKNESS"))
    if thk is None and sheet_metal:
        thk = round(min(size), 4) if bends == 0 else None
    bent = sheet_metal and (bends > 0 or (thk is not None and min(size) > 1.8 * thk))
    # base view: look along the thin axis (plan face of a plate / bracket)
    base = {"x": "right", "y": "top", "z": "front"}[thin]
    edge_views = "bottom,right" if bent else "bottom"
    # flat-pattern configuration
    cfgs = []
    try:
        cfgs = [str(c) for c in (_val(doc, "GetConfigurationNames") or [])]
    except Exception:  # noqa: BLE001
        pass
    flat_cfgs = [c for c in cfgs if "FLAT" in c.upper()]
    br = _parse_num(props.get("BEND_RADIUS"))
    return {
        "status": "done", "model": model_path, "title": _try(lambda: str(_val(doc, "GetTitle"))),
        "bbox_in": {"x": size[0], "y": size[1], "z": size[2]}, "thin_axis": thin,
        "sheet_metal": sheet_metal, "bent": bent, "bend_features": bends,
        "thickness_in": thk, "bend_radius_in": br,
        "props": props, "configurations": cfgs, "flat_pattern_configs": flat_cfgs,
        "suggest": {"base_view": base, "edge_views": edge_views,
                    "flat_sheet": bent, "bend_radius_note": (None if br else "BEND_RADIUS blank — pass the measured (R) literal to finish"),
                    "thickness_note": ("THICKNESS property has fewer than 3 decimals — the material note shows it as-is" if props.get("THICKNESS") and len(props["THICKNESS"].split(".")[-1].strip()) < 3 else None)},
        "feature_types": sorted(set(ftypes)),
    }


def _part_brief(model_path: str) -> dict:
    app = SWConnection.get_instance().get_app()
    doc = _open_part(app, model_path)
    return _brief_dict(app, doc, model_path)


# --------------------------------------------------------------------------- layout

SLIP_TEMPLATE = r"C:\SLIP_ROBOTICS_VAULT\SOLIDWORKS SETUP\START FILE TEMPLATES\SR FAB D DWG 2026.DRWDOT"


def _find_slip_template() -> str | None:
    if os.path.isfile(SLIP_TEMPLATE):
        return SLIP_TEMPLATE
    pats = [os.path.join(os.path.dirname(SLIP_TEMPLATE), "*.drwdot"),
            r"C:\ProgramData\SolidWorks\SOLIDWORKS *\templates\*.drwdot",
            r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS *\templates\*.drwdot"]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    pref = [h for h in hits if any(k in os.path.basename(h).upper() for k in ("SR FAB", "SLIP", " SR ", "SR_", "SR-", "FORMAT SR"))]
    return (pref or hits or [None])[0]


def _new_slip_drawing(app):
    """Create a new drawing from the Slip D-size template (fork-side; no solidpilot hop)."""
    tpl = _find_slip_template()
    if not tpl:
        raise SWError(f"Slip drawing template not found ({SLIP_TEMPLATE})")
    d = app.NewDocument(tpl, 4, 0.0, 0.0)  # swDwgPaperDsize=4 (the template carries its own sheet format)
    if d is None:
        raise SWError(f"NewDocument failed for template {tpl}")
    return d, tpl


def _ensure_drawing(app, create_new: bool = False):
    if not create_new:
        doc = _try(lambda: app.ActiveDoc)
        if doc is not None and _try(lambda: int(slip._inv(doc, "GetType"))) == 3:
            return doc, None
        d = _try(lambda: app.GetFirstDocument)
        while d is not None:
            if _try(lambda: int(slip._inv(d, "GetType"))) == 3:
                _try(lambda: sv._activate_doc(app, str(_val(d, "GetTitle"))))
                return d, None
            d = _try(lambda: _val(d, "GetNext"))
    return _new_slip_drawing(app)


def _view_by_name(drawing, name):
    vs = _get_drawing_views(drawing, name)
    if not vs:
        raise SWError(f"view not found: {name}")
    return vs[0][1]


def _outline(view_obj):
    return sb._outline_mm(view_obj)


def _set_scale(view_obj, scale):
    try:
        view_obj.ScaleDecimal = float(scale)
    except Exception:  # noqa: BLE001
        slip._put(view_obj, "ScaleDecimal", float(scale))


def _set_pos(view_obj, x_mm, y_mm):
    import pythoncom
    import win32com.client
    variant = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8,
                                      [float(x_mm) / _M_TO_MM, float(y_mm) / _M_TO_MM])
    ole = slip._ole(view_obj)
    dispid = ole.GetIDsOfNames(0, "Position")
    ole.Invoke(dispid, 0, slip.DISPATCH_PROPERTYPUT, False, variant)


def _insert_model_view(drawing, model_path, orient_key, x_mm, y_mm):
    before = {n for n, _ in _get_drawing_views(drawing, None)}
    name_sw = ORIENT.get(orient_key, orient_key)
    v = drawing.CreateDrawViewFromModelView3(model_path, name_sw, x_mm / _M_TO_MM, y_mm / _M_TO_MM, 0.0)
    name = _try(lambda: v.Name) if v is not None else None
    if not name:
        name = sv._new_view_name(drawing, before)
    if not name:
        raise SWError(f"CreateDrawViewFromModelView3({name_sw}) created nothing — is the part open and saved?")
    return name, _view_by_name(drawing, name)


def _save_drawing_as(drawing, path: str) -> dict:
    """SaveAs3 with ByRef VARIANT errors/warnings (the plain SaveAs raises Type mismatch late-bound)."""
    errs, warns = _byref_i4(), _byref_i4()
    import pythoncom
    from win32com.client import VARIANT
    null = VARIANT(pythoncom.VT_DISPATCH, None)
    for call in (lambda: drawing.Extension.SaveAs3(path, 0, 1, null, null, errs, warns),
                 lambda: drawing.Extension.SaveAs(path, 0, 1, null, errs, warns),
                 lambda: drawing.SaveAs3(path, 0, 1)):
        try:
            ok = call()
            if ok:
                return {"saved": path, "errors": _try(lambda: errs.value, 0), "warnings": _try(lambda: warns.value, 0)}
            logger.info("save attempt returned %r (errors=%s warnings=%s)", ok, _try(lambda: errs.value), _try(lambda: warns.value))
        except Exception as ex:  # noqa: BLE001
            logger.info("save attempt raised %s", ex)
    return {"saved": None, "error": f"all SaveAs variants failed (errors={_try(lambda: errs.value)})"}


def _layout_slip_part(model_path, base_view, edge_views, scale, save_path, rotate_base_deg, create_new=False) -> dict:
    app = SWConnection.get_instance().get_app()
    # the drawing FIRST (opening/activating the part changes ActiveDoc)
    drawing, tpl = _ensure_drawing(app, create_new)
    dtitle = _try(lambda: str(_val(drawing, "GetTitle")))
    part = _open_part(app, model_path)
    brief = _brief_dict(app, part, model_path)
    if dtitle:
        _try(lambda: sv._activate_doc(app, dtitle))
    drawing = slip._active_drawing()

    base_key = brief["suggest"]["base_view"] if base_view in (None, "", "auto") else base_view.lower()
    ev = brief["suggest"]["edge_views"] if edge_views in (None, "", "auto") else edge_views
    ev_list = [e.strip().lower() for e in ev.split(",") if e.strip()]

    # 1. base view at 1:1 to measure it
    bname, bview = _insert_model_view(drawing, model_path, base_key, STACK_CX, STACK_CY)
    _set_scale(bview, 1.0)
    if rotate_base_deg:
        try:
            bview.Angle = math.radians(rotate_base_deg)
        except Exception:  # noqa: BLE001
            slip._put(bview, "Angle", math.radians(rotate_base_deg))
    slip._rebuild(drawing)
    o = _outline(bview)
    margin = 11.5  # GetOutline margin per side
    w1 = max(1.0, (o[2] - o[0]) - 2 * margin)
    h1 = max(1.0, (o[3] - o[1]) - 2 * margin)
    # depth of the part along the viewing axis (drives the bottom/right projections' size)
    sz = brief["bbox_in"]
    depth_in = {"top": sz["y"], "bottom": sz["y"], "front": sz["z"], "back": sz["z"],
                "right": sz["x"], "left": sz["x"]}.get(base_key, min(sz.values()))
    d1 = depth_in * _IN
    if scale is None:
        s = min(BASE_MAX_W / w1, BASE_MAX_H / h1)
        if "bottom" in ev_list or "top" in ev_list:
            s = min(s, (STACK_MAX_H - GAP_V) / (h1 + d1))
        if "right" in ev_list or "left" in ev_list:
            # side projection must end by x≈700 to leave the iso its corner
            s = min(s, (700.0 - STACK_CX - GAP_H) / (w1 * 0.5 + d1))
        s = _std_scale(s)
    else:
        s = float(scale)
    _set_scale(bview, s)
    slip._rebuild(drawing)
    ws, hs, ds = w1 * s, h1 * s, d1 * s

    # 2. positions: stack (base over bottom projection) centred at STACK_CY
    stack_h = hs + (GAP_V + ds if "bottom" in ev_list else 0.0)
    base_cy = STACK_CY + stack_h / 2 - hs / 2
    base_cx = STACK_CX
    _set_pos(bview, base_cx, base_cy)
    slip._rebuild(drawing)
    views = [{"name": bname, "role": "base", "orientation": ORIENT.get(base_key, base_key)}]

    # 3. projected views
    for e in ev_list:
        if e == "bottom":
            cx, cy = base_cx, base_cy - hs / 2 - GAP_V - ds / 2
        elif e == "top":
            cx, cy = base_cx, base_cy + hs / 2 + GAP_V + ds / 2
        elif e == "right":
            cx, cy = base_cx + ws / 2 + GAP_H + ds / 2, base_cy
        elif e == "left":
            cx, cy = base_cx - ws / 2 - GAP_H - ds / 2, base_cy
        else:
            continue
        try:
            r = sv._project_view(bname, e, cx, cy)
            views.append({"name": r.get("name"), "role": {"bottom": "edge", "top": "edge", "right": "side", "left": "side"}[e],
                          "direction": e})
        except Exception as ex:  # noqa: BLE001
            views.append({"role": e, "error": str(ex)})

    # 4. iso (small, lower-right; keep clear of the side view and the title block)
    iso_scale = _std_scale(max(0.125, s / 3.0))
    right_edge = max([(_outline(_view_by_name(drawing, v["name"])) or [0, 0, 0, 0])[2] for v in views if v.get("name")] + [0])
    iso_x = max(ISO_POS[0], min(800.0, right_edge + 75.0))
    iname, iview = _insert_model_view(drawing, model_path, "iso", iso_x, ISO_POS[1])
    _set_scale(iview, iso_scale)
    slip._rebuild(drawing)
    io = _outline(iview)
    if io and io[1] < 128.0:   # title block starts at y≈120 for x>580
        _set_pos(iview, iso_x, ISO_POS[1] + (128.0 - io[1]))
    views.append({"name": iname, "role": "iso", "scale": iso_scale})

    # 5. display
    _try(lambda: slip._set_view_display(None, "hidden_lines_removed", "removed"))
    _try(lambda: slip._set_view_display(iname, "hidden_lines_removed", "visible"))
    slip._rebuild(drawing)

    # 6. save
    pn = os.path.splitext(os.path.basename(model_path))[0]
    path = save_path or os.path.join(r"C:\Drawings", f"{pn}.SLDDRW")
    saved = _save_drawing_as(drawing, path)

    for v in views:
        if v.get("name"):
            v["outline_mm"] = _outline(_view_by_name(drawing, v["name"]))
    return {"status": "done", "template": tpl, "scale": s, "part_size_in": sz, "base_view": base_key,
            "edge_views": ev_list, "views": views, "save": saved, "brief": {k: brief[k] for k in ("bent", "thickness_in", "bend_radius_in", "props")}}


# --------------------------------------------------------------------------- dimensioning

def _geom(view_name) -> dict:
    r = sb._view_geometry(view_name, True)["views"][0]
    return r


def _lines(edges, horizontal: bool, tol=0.05):
    out = []
    for r in edges:
        if r.get("t") != "line" or "s" not in r:
            continue
        if horizontal and abs(r["s"][1] - r["e"][1]) < tol:
            out.append(r)
        if not horizontal and abs(r["s"][0] - r["e"][0]) < tol:
            out.append(r)
    return out


def _pick_hole(summary, edges, mode: str):
    circles = [r for r in edges if r.get("t") == "circle" and "c" in r]
    if not circles or mode == "none":
        return None
    bb = summary["bbox_mm"]
    if mode == "largest":
        rmax = max(c["r"] for c in circles)
        circles = [c for c in circles if abs(c["r"] - rmax) < 1e-6]
    # nearest to the top-left corner (Simón: short extension lines, dims above/left)
    return min(circles, key=lambda c: (c["c"][0] - bb[0]) + (bb[3] - c["c"][1]))


def _face_regions(summary, edges):
    """Separator coordinates that split a view into FACE regions: a line running (almost) the full
    width/height of the view strictly inside its outline is a wall seen edge-on — a bend between two
    faces (108689 side view: the two side walls split the spine face from the two ear faces)."""
    bb = summary["bbox_mm"]
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    ys = {round(r["s"][1], 1) for r in _lines(edges, horizontal=True)
          if r.get("len", 0) >= 0.8 * w and bb[1] + 1.0 < r["s"][1] < bb[3] - 1.0}
    xs = {round(r["s"][0], 1) for r in _lines(edges, horizontal=False)
          if r.get("len", 0) >= 0.8 * h and bb[0] + 1.0 < r["s"][0] < bb[2] - 1.0}
    return sorted(xs), sorted(ys)


def _pick_holes(summary, edges, mode: str) -> list[dict]:
    """ONE located hole per FACE seen in the view (Simón: every face that carries a feature gets its
    feature — the spine's centre hole as well as the ear's), the view's top-left hole first. Identical
    faces (mirrored ears) are filtered later by their dimension values."""
    circles = [r for r in edges if r.get("t") == "circle" and "c" in r]
    if not circles or mode == "none":
        return []
    bb = summary["bbox_mm"]
    xs, ys = _face_regions(summary, edges)
    tl = lambda c: (c["c"][0] - bb[0]) + (bb[3] - c["c"][1])
    groups: dict = {}
    for c in circles:
        key = (sum(1 for x in xs if x < c["c"][0]), sum(1 for y in ys if y < c["c"][1]))
        groups.setdefault(key, []).append(c)
    picks = [min(cs, key=tl) for cs in groups.values()]
    picks.sort(key=tl)
    return picks


def _dim_value_in(edges, d, scale) -> float | None:
    """Value a linear/diameter dim will read, from the geometry (for duplicate filtering)."""
    idx = {r["i"]: r for r in edges}
    try:
        if d["type"] == "diameter":
            return round(idx[d["e1"]]["r"] * 2 / _IN, 4)
        a, b = idx[d["e1"]], idx[d["e2"]]
        pa = a["c"] if a["t"] == "circle" else a["m"]
        line = b if b["t"] == "line" else a
        other = pa if b["t"] == "line" else (b["c"] if b["t"] == "circle" else b["m"])
        if abs(line["s"][0] - line["e"][0]) < 0.05:      # vertical line -> horizontal distance
            return round(abs(line["s"][0] - other[0]) / scale / _IN, 4)
        return round(abs(line["s"][1] - other[1]) / scale / _IN, 4)
    except Exception:  # noqa: BLE001
        return None


def _face_hole_dims(summary, edges, occupied: dict, thk_sheet, band, scale, base_hole="top_left") -> list[dict]:
    """Hole dims for every face in a view: first hole as usual; further holes drop an X or Y that
    repeats a value already placed (the centred spine hole shares the ear's .688) and put their Ø
    level with the hole beside the view; a face whose (Ø, X, Y) all repeat is skipped (mirrored ear)."""
    out = []
    placed_x: list = []
    placed_y: list = []
    seen_faces: list = []
    same = lambda a, b: a is not None and b is not None and abs(a - b) < 0.005   # 3-place display equality
    first = True
    for hole in _pick_holes(summary, edges, base_hole):
        dims = _hole_dims(summary, edges, hole, dict(occupied), thk_sheet, band=band)
        vals = {d["label"]: _dim_value_in(edges, d, scale) for d in dims}
        sig = (vals.get("hole_dia"), vals.get("hole_x"), vals.get("hole_y"))
        if any(all(same(a, b) for a, b in zip(sig, f)) for f in seen_faces):
            continue          # a mirrored twin of a face already dimensioned (the second ear)
        seen_faces.append(sig)
        if first:
            first = False
            _hole_dims(summary, edges, hole, occupied, thk_sheet, band=band)   # record the bands actually used
            out += dims
            placed_x.append(vals.get("hole_x")); placed_y.append(vals.get("hole_y"))
            continue
        bb = summary["bbox_mm"]
        cx, cy = hole["c"]
        for d in dims:
            v = vals.get(d["label"])
            if d["label"] == "hole_x" and any(same(p, v) for p in placed_x):
                continue
            if d["label"] == "hole_y" and any(same(p, v) for p in placed_y):
                continue
            if d["label"] == "hole_dia":
                # level with the hole, beside the view, on the side AWAY from the Y dims' band
                right = not (occupied.get("right", 0) > 0) if occupied.get("left", 0) or occupied.get("right", 0) else cx >= (bb[0] + bb[2]) / 2
                d = dict(d, text=[round((bb[2] + 30.0) if right else (bb[0] - 30.0), 1), round(cy, 1)])
            if d["label"] == "hole_x":
                placed_x.append(v)
            if d["label"] == "hole_y":
                placed_y.append(v)
            out.append(d)
    return out


def _hole_dims(summary, edges, hole, occupied: dict, thk_sheet: float | None = None, band: float = INNER_OFF) -> list[dict]:
    """X + Y + Ø for one hole. Returns dims and records which sides got a dim in the `band`."""
    bb = summary["bbox_mm"]
    env = summary["envelope_edges"]
    cx, cy = hole["c"]
    dims = []
    # nearest ADJACENT edge: a line whose span covers the hole row/column (Simón 108716: the notch
    # edge beside the hole, .375, not the far envelope edge, .875). The INNER face of a wall seen
    # edge-on (a line thk away from a farther candidate) is skipped -> dimension to the outer face,
    # so a plan view still reads 2.875 from the part's end, not 2.750 from the inside of the tab.
    pair_tol = (2.5 * thk_sheet + 0.5) if thk_sheet else 5.0

    def adjacent(vertical: bool):
        cands = []
        for r in _lines(edges, horizontal=not vertical):
            if vertical:
                lo, hi = sorted([r["s"][1], r["e"][1]])
                if lo - 1.0 <= cy <= hi + 1.0:
                    cands.append((abs(r["s"][0] - cx), r["s"][0] - cx, r))
            else:
                lo, hi = sorted([r["s"][0], r["e"][0]])
                if lo - 1.0 <= cx <= hi + 1.0:
                    cands.append((abs(r["s"][1] - cy), r["s"][1] - cy, r))
        cands.sort(key=lambda t: t[0])
        for d, signed, r in cands:
            same_side = [c for c in cands if (c[1] > 0) == (signed > 0) and c[0] > d + 0.3]
            if any(c[0] - d <= pair_tol for c in same_side):
                continue          # inner face of a wall -> take the outer one
            return r
        return None
    vx = adjacent(True)
    hy = adjacent(False)
    use_left = (vx["s"][0] < cx) if vx else ((cx - bb[0]) <= (bb[2] - cx))
    use_bottom = (hy["s"][1] < cy) if hy else ((cy - bb[1]) <= (bb[3] - cy))
    ex = vx if vx else (env["left"] if use_left else env["right"])
    ey = hy if hy else (env["bottom"] if use_bottom else env["top"])
    # text bands: measured from the edge actually used
    edge_x = ex["s"][0] if ex and "s" in ex else (bb[0] if use_left else bb[2])
    edge_y = ey["s"][1] if ey and "s" in ey else (bb[1] if use_bottom else bb[3])
    # X dim (horizontal): place on the side (top/bottom) nearer the hole so extension lines stay short
    x_side = "bottom" if use_bottom else "top"
    y_band = (bb[1] - band) if x_side == "bottom" else (bb[3] + band)
    proj = band < INNER_OFF          # projected view: everything in ONE row `band` off the view (Simón 108716)
    x_short = False
    if ex:
        span_x = abs(cx - edge_x)
        x_short = span_x < SHORT_H
        tx = (cx + edge_x) / 2 if not x_short else ((edge_x - 14.0) if use_left else (edge_x + 14.0))  # text outside a short span
        dims.append({"type": "linear", "e1": hole["i"], "e2": ex["i"], "text": [round(tx, 1), round(y_band, 1)], "label": "hole_x"})
        occupied[x_side] = occupied.get(x_side, 0) + 1
    # Y dim (vertical): on the side (left/right) nearer the hole
    y_side = "left" if use_left else "right"
    x_band = (bb[0] - band) if y_side == "left" else (bb[2] + band)
    if ey:
        span = abs(cy - edge_y)
        if span >= SHORT_V:
            dims.append({"type": "linear", "e1": hole["i"], "e2": ey["i"], "text": [round(x_band, 1), round((cy + edge_y) / 2, 1)], "label": "hole_y"})
            occupied[y_side] = occupied.get(y_side, 0) + 1
        else:
            # tiny span (Simón 108716, .375/.388): the dim line goes on the band BESIDE the view — never
            # inside its outline — with the text stacked outside the edge, in the same row as the X dim.
            # Band side = the hole's side, unless the X text already sits outside there (short X span).
            side = y_side
            if ex and x_short:
                side = "right" if side == "left" else "left"
            xd = (bb[0] - band) if side == "left" else (bb[2] + band)
            ty = edge_y - band if use_bottom else edge_y + band
            dims.append({"type": "linear", "e1": hole["i"], "e2": ey["i"], "text": [round(xd, 1), round(ty, 1)], "label": "hole_y"})
            occupied[side] = occupied.get(side, 0) + 1
    # Ø leader text: toward the view centre from the hole — in the row for projected views (~40 mm,
    # short leader), a little further and just off the band in the base view
    dirx = 1.0 if cx < (bb[0] + bb[2]) / 2 else -1.0
    if proj:
        if ex and x_short and ((tx < cx) == (dirx < 0)):
            # the X text already sits outside on this side: a Ø text in the same row would put the leader
            # shoulder through it — so the Ø goes level with the hole, beside the view, straight leader
            dtext = [round((bb[0] - 30.0) if dirx < 0 else (bb[2] + 30.0), 1), round(cy, 1)]
        else:
            dtext = [round(cx + dirx * 40.0, 1), round(y_band, 1)]
    else:
        dtext = [round(cx + dirx * 55.0, 1), round(y_band + (3 if x_side == "bottom" else -3), 1)]
    dims.append({"type": "diameter", "e1": hole["i"], "text": dtext, "label": "hole_dia"})
    return dims


def _envelope_dims(summary, occupied: dict, want_w=True, want_h=True) -> list[dict]:
    bb = summary["bbox_mm"]
    env = summary["envelope_edges"]
    dims = []
    if want_w and env["left"] and env["right"]:
        # envelope width above the view; one step further out for EVERY dimension row already there
        off = INNER_OFF + OUTER_STEP * occupied.get("top", 0)
        y = bb[3] + off
        dims.append({"type": "linear", "e1": env["left"]["i"], "e2": env["right"]["i"], "text": [round((bb[0] + bb[2]) / 2, 1), round(y, 1)], "label": "envelope_w"})
    if want_h and env["top"] and env["bottom"]:
        side = "left"
        off = INNER_OFF + OUTER_STEP * occupied.get(side, 0)
        x = bb[0] - off
        dims.append({"type": "linear", "e1": env["top"]["i"], "e2": env["bottom"]["i"], "text": [round(x, 1), round((bb[1] + bb[3]) / 2, 1)], "label": "envelope_h"})
    return dims


def _thickness_pair(edges, thk_sheet_mm, horizontal_lines: bool):
    """Two parallel lines thk apart: returns (i1, i2) — the outermost such pair."""
    lines = _lines(edges, horizontal=horizontal_lines)
    key = (lambda r: r["s"][1]) if horizontal_lines else (lambda r: r["s"][0])
    best = None
    for a in lines:
        for b in lines:
            if a["i"] >= b["i"]:
                continue
            d = abs(key(a) - key(b))
            if abs(d - thk_sheet_mm) < max(0.35, 0.06 * thk_sheet_mm):
                cand = (min(key(a), key(b)), a["i"], b["i"])
                if best is None or cand[0] < best[0]:
                    best = cand
    return None if best is None else (best[1], best[2])


def _real_arc(a) -> bool:
    """True for an arc actually SEEN as an arc in the view. A bend arc whose axis lies in the view
    plane projects edge-on to a straight segment (108689 edge/side views): its start and end differ
    along one axis only, and a radius dimension on it renders as a bogus linear-looking (.039)."""
    if a.get("t") != "arc" or "s" not in a or "e" not in a:
        return False
    return abs(a["s"][0] - a["e"][0]) > 0.25 and abs(a["s"][1] - a["e"][1]) > 0.25


def _bend_arc(edges, thk_model_mm, bend_radius_in):
    arcs = [r for r in edges if r.get("t") == "arc" and r.get("r") and _real_arc(r)]
    if not arcs:
        return None
    if bend_radius_in:
        target = bend_radius_in * _IN
        near = [a for a in arcs if abs(a["r"] - target) < 0.08 * target + 0.05]
        if near:
            return near[0]
    # inner/outer pair: outer r = inner r + thickness
    if thk_model_mm:
        for a in arcs:
            if any(abs(b["r"] - (a["r"] + thk_model_mm)) < 0.15 for b in arcs):
                return a
    return min(arcs, key=lambda a: a["r"])


def _thk_pairs(edges, thk_sheet_mm, horizontal_lines: bool):
    """All pairs of parallel lines thk apart -> list of (pos, i_a, i_b, span_lo, span_hi)."""
    lines = _lines(edges, horizontal=horizontal_lines)
    key = (lambda r: r["s"][1]) if horizontal_lines else (lambda r: r["s"][0])
    along = (lambda r: sorted([r["s"][0], r["e"][0]])) if horizontal_lines else (lambda r: sorted([r["s"][1], r["e"][1]]))
    out = []
    for a in lines:
        for b in lines:
            if a["i"] >= b["i"]:
                continue
            if abs(abs(key(a) - key(b)) - thk_sheet_mm) < max(0.35, 0.06 * thk_sheet_mm):
                sa, sb = along(a), along(b)
                lo, hi = max(sa[0], sb[0]), min(sa[1], sb[1])
                if hi - lo > 0.5:  # they overlap along their length
                    out.append(((key(a) + key(b)) / 2, a["i"], b["i"], lo, hi))
    return out


def _flange_dims(edges, summary, thk_sheet_mm, scale, plate_vertical: bool, already: list, text_side: str, occupied: dict | None = None) -> list[dict]:
    """Distinct flange lengths seen edge-on in a projected view: plate = the long thk-pair (vertical
    when plate_vertical), flanges = thk-pairs perpendicular to it; each flange length (plate outer
    face -> flange tip) dimensioned ONCE per distinct value; the longest is skipped when it equals
    the envelope already dimensioned. `already` (inches) collects values dimensioned so far.
    Simón 2026-09-17: all flanges dimensioned; equal lengths only once."""
    if not thk_sheet_mm:
        return []
    bb = summary["bbox_mm"]
    plate_pairs = _thk_pairs(edges, thk_sheet_mm, horizontal_lines=not plate_vertical)
    flange_pairs = _thk_pairs(edges, thk_sheet_mm, horizontal_lines=plate_vertical)
    if not plate_pairs or not flange_pairs:
        return []
    plate = max(plate_pairs, key=lambda p: p[4] - p[3])
    idx = {r["i"]: r for r in edges}
    # plate outer face line: the plate-pair line nearer the bbox edge on the side away from the flanges
    ax = 0 if plate_vertical else 1              # coordinate across the plate thickness
    fl_mid = sum(p[0] for p in flange_pairs) / len(flange_pairs)   # not used for side; flanges extend along `ax`
    la, lb = idx[plate[1]], idx[plate[2]]
    pos_a, pos_b = la["s"][ax], lb["s"][ax]
    # flange tips lie along ax; the plate outer face is on the opposite side from the tips
    tips = []
    for pos, ia, ib, lo, hi in flange_pairs:
        # tip = the end of the flange pair farther from the plate position
        pp = (pos_a + pos_b) / 2
        tip = hi if abs(hi - pp) > abs(lo - pp) else lo
        tips.append((tip, pos, ia, ib))
    tip_dir = 1 if sum(t[0] for t in tips) / len(tips) > (pos_a + pos_b) / 2 else -1
    outer_line = la if (pos_a < pos_b) == (tip_dir > 0) else lb   # outer = farther from the tips
    outer_pos = outer_line["s"][ax]
    # tip lines: short lines perpendicular to the flange (length ~ thk) at the tip position
    perp_lines = _lines(edges, horizontal=not plate_vertical)
    across = 1 if plate_vertical else 0        # coordinate across the flange thickness
    groups: dict[float, tuple] = {}
    for tip, pos, ia, ib in tips:
        # the straight part of the flange pair ends where the bend/corner arcs begin; the real tip
        # line (length ~ thk, perpendicular) lies a little beyond it in the tip direction
        reach = 2.5 * thk_sheet_mm + 1.0
        cand = [r for r in perp_lines
                if abs(r.get("len", 0) - thk_sheet_mm) < max(0.5, 0.3 * thk_sheet_mm)
                and abs(r["m"][across] - pos) < thk_sheet_mm          # on this flange
                and 0 <= (r["s"][ax] - tip) * tip_dir <= reach]
        if not cand:
            continue
        tip_line = max(cand, key=lambda r: (r["s"][ax] - tip) * tip_dir)
        real_tip = tip_line["s"][ax]
        length_in = abs(real_tip - outer_pos) / scale / _IN
        k = round(length_in, 2)
        if k in groups:
            continue
        groups[k] = (tip_line["i"], real_tip, pos, length_in)
    dims = []
    occupied = occupied if occupied is not None else {}
    for k, (i_tip, tip, pos, length_in) in sorted(groups.items(), key=lambda kv: -kv[0]):
        if any(abs(length_in - a) < 0.02 for a in already):
            continue
        already.append(length_in)
        span = abs(tip - outer_pos)
        # never let a dimension collide with itself: a horizontal dim needs ~22 mm for its text, a
        # vertical one only ~8 mm (text stacks beside the line) -> otherwise text ALONG the dim, outside
        short = span < (SHORT_H if plate_vertical else SHORT_V)
        mid = (tip + outer_pos) / 2 if not short else tip + (14 if tip > outer_pos else -14)
        # put the dimension on the side of the view where THIS flange is (short extension lines),
        # one step further out for every dimension already sitting on that side
        if plate_vertical:   # flange lengths run horizontally -> dim line horizontal, below or above
            side = "bottom" if pos < (bb[1] + bb[3]) / 2 else "top"
            off = INNER_OFF + OUTER_STEP * occupied.get(side, 0)
            text = [round(mid, 1), round(bb[1] - off, 1)] if side == "bottom" else [round(mid, 1), round(bb[3] + off, 1)]
        else:                # flange lengths run vertically -> dim line vertical, left or right
            side = "left" if pos < (bb[0] + bb[2]) / 2 else "right"
            off = INNER_OFF + OUTER_STEP * occupied.get(side, 0)
            text = [round(bb[0] - off, 1), round(mid, 1)] if side == "left" else [round(bb[2] + off, 1), round(mid, 1)]
        occupied[side] = occupied.get(side, 0) + 1
        dims.append({"type": "linear", "e1": outer_line["i"], "e2": i_tip, "text": text, "label": f"flange_{k}"})
    return dims


def _dimension_slip_views(base_view, edge_view, side_view, thickness_in, bend_radius_in, base_hole) -> dict:
    drawing = slip._active_drawing()
    report = {"status": "done", "views": {}}

    # ---- geometry of every view first: the (thk)/(R) view is decided before any dimension is placed
    g = _geom(base_view)
    summ, edges, scale = g["summary"], g["edges"], g["scale"] or 1.0
    thk_model_mm = thickness_in * _IN if thickness_in else None
    thk_sheet = thk_model_mm * scale if thk_model_mm else None
    ge = _geom(edge_view) if edge_view else None
    gs = _geom(side_view) if side_view else None
    flange_vals: list = []

    # Where do (thk) and (R) go? Simón (108716): on the view where a flange is seen EDGE-ON next to a
    # bend arc that is SEEN as an arc — the bottom (edge) view first, then the side view. When the bends
    # are edge-on in both projections (108689 C-bracket: its arcs are only face-on in the plan view) the
    # base view takes them, in a row BELOW the view (the envelope sits above).
    def has_thk_and_arc(gg):
        return bool(gg and thk_sheet and (_thickness_pair(gg["edges"], thk_sheet, False) or _thickness_pair(gg["edges"], thk_sheet, True))
                    and _bend_arc(gg["edges"], thk_model_mm, bend_radius_in))
    thk_r_on = None
    if has_thk_and_arc(ge):
        thk_r_on = edge_view
    elif has_thk_and_arc(gs):
        thk_r_on = side_view
    elif has_thk_and_arc(g):
        thk_r_on = base_view
    elif gs:
        thk_r_on = side_view

    def thk_and_r(g, view_is_side: bool, prefer_right: bool = True, below: bool = False) -> list[dict]:
        """(thk) across a flange seen edge-on + (R) leader on its bend arc, on the flange AWAY from
        the hole dims (Simón 108716: beside the 1.25 flange on the right). Both texts sit in the
        same row as the hole dims, ROW_OFF above the view: (thk) a full band (25 mm) outside the flange so
        the text clears the arrows, (R) ~42 mm inboard of its arc, clear of the (thk) extension lines."""
        out = []
        summ_, edges_ = g["summary"], g["edges"]
        bb_ = summ_["bbox_mm"]
        mid_x = (bb_[0] + bb_[2]) / 2
        pairs = _thk_pairs(edges_, thk_sheet, horizontal_lines=False)   # vertical pairs -> horizontal dim
        row = (bb_[1] - ROW_OFF) if below else (bb_[3] + ROW_OFF)
        # preferred side first, then the pair whose span ends nearest the text row (short extension lines)
        pairs = sorted(pairs, key=lambda p: ((p[0] < mid_x) if prefer_right else (p[0] > mid_x),
                                             (p[3] - bb_[1]) if below else (bb_[3] - p[4])))
        pair = (pairs[0][1], pairs[0][2], pairs[0][0]) if pairs else None
        if pair:
            xa = pair[2]
            right = xa > mid_x
            out.append({"type": "linear", "e1": pair[0], "e2": pair[1],
                        "text": [round(xa + (25 if right else -25), 1), round(row, 1)], "reference": True, "label": "thk"})
            # the bend arc of THIS flange: nearest matching arc
            arcs = [r for r in edges_ if r.get("t") == "arc" and r.get("r") and _real_arc(r)]
            target = (bend_radius_in * _IN) if bend_radius_in else None
            good = [a for a in arcs if (target and abs(a["r"] - target) < 0.08 * target + 0.05)
                    or any(abs(b["r"] - (a["r"] + thk_model_mm)) < 0.15 for b in arcs)]
            near = [a for a in good if abs(a.get("m", a.get("c"))[0] - xa) < 4 * thk_sheet + 2]
            # the inner arc of this flange's bend, on the corner nearest the text row
            if near:
                rmin = min(a["r"] for a in near)
                near = [a for a in near if a["r"] < rmin + 0.15]
            arc = min(near, key=lambda a: abs(a.get("m", a.get("c"))[1] - row)) if near else None
            if arc:
                am = arc.get("m", arc.get("c"))
                out.append({"type": "radius", "e1": arc["i"], "text": [round(am[0] + (-42 if right else 42), 1), round(row, 1)], "reference": True, "label": "bend_r"})
            return out
        else:
            pair = _thickness_pair(edges_, thk_sheet, horizontal_lines=True)   # horizontal pair -> vertical dim
            if pair:
                ya = next(r for r in edges_ if r["i"] == pair[0])["s"][1]
                up = ya > (bb_[1] + bb_[3]) / 2
                out.append({"type": "linear", "e1": pair[0], "e2": pair[1],
                            "text": [round(bb_[2] + 4.0, 1), round(ya + (14 if up else -14), 1)], "reference": True, "label": "thk"})
        arc = _bend_arc(edges_, thk_model_mm, bend_radius_in)
        if arc:
            ax_, ay_ = arc.get("m", arc.get("c"))[0], arc.get("m", arc.get("c"))[1]
            toward = -15.0 if ax_ > (bb_[0] + bb_[2]) / 2 else 15.0   # text a little toward the view centre
            out.append({"type": "radius", "e1": arc["i"], "text": [round(ax_ + toward, 1), round(bb_[3] + 6.0, 1)], "reference": True, "label": "bend_r"})
        return out

    # ---- base view
    occupied: dict = {}
    dims = []
    hole = _pick_hole(summ, edges, base_hole)
    if hole:
        dims += _face_hole_dims(summ, edges, occupied, thk_sheet, INNER_OFF, scale, base_hole)
    dims += _envelope_dims(summ, occupied)
    if thk_r_on == base_view:
        hole_left = hole is not None and hole["c"][0] < (summ["bbox_mm"][0] + summ["bbox_mm"][2]) / 2
        dims += thk_and_r(g, False, prefer_right=(hole_left or hole is None), below=True)
    r = sb._add_dimensions(base_view, [{k: v for k, v in d.items() if k != "label"} for d in dims])
    report["views"][base_view] = _compact(r, dims)

    # ---- edge view (bottom projection)
    if edge_view and ge:
        summ, edges = ge["summary"], ge["edges"]
        bb = summ["bbox_mm"]
        dims = []
        h_in = summ.get("height_in") or 0
        circles = [e for e in edges if e.get("t") == "circle"]
        thin = (thickness_in and abs(h_in - thickness_in) < 0.02) or (not circles and h_in < 0.35)
        if thin and summ["envelope_edges"]["top"] and summ["envelope_edges"]["bottom"]:
            dims.append({"type": "linear", "e1": summ["envelope_edges"]["top"]["i"], "e2": summ["envelope_edges"]["bottom"]["i"],
                         "text": [round(bb[0] - INNER_OFF, 1), round(bb[3] + THK_TEXT_ALONG, 1)], "reference": True, "label": "thk"})
        else:
            occ: dict = {}
            if circles:
                hole = _pick_hole(summ, edges, "top_left")
                dims += _face_hole_dims(summ, edges, occ, thk_sheet, PROJ_BAND, scale)
            if thk_sheet:
                # flanges seen edge-on here (plate horizontal): each distinct length once
                dims += _flange_dims(edges, summ, thk_sheet, scale, False, flange_vals, "left", occ)
            if thk_r_on == edge_view:
                hole_left = bool(circles) and hole is not None and hole["c"][0] < (bb[0] + bb[2]) / 2
                dims += thk_and_r(ge, False, prefer_right=hole_left or not circles)
        if dims:
            r = sb._add_dimensions(edge_view, [{k: v for k, v in d.items() if k != "label"} for d in dims])
            report["views"][edge_view] = _compact(r, dims)
        else:
            report["views"][edge_view] = {"note": "no dims (no holes, not a thin edge)"}

    # ---- side view (right projection): flange face hole + flange length(s) (+ thk/R when not on the edge view)
    if side_view and gs:
        summ, edges = gs["summary"], gs["edges"]
        bb = summ["bbox_mm"]
        env = summ["envelope_edges"]
        dims = []
        circles = [e for e in edges if e.get("t") == "circle"]
        occ = {}
        if circles:   # every FACE visible here gets its one located hole (Simón, 108716 / 108689)
            hole = _pick_hole(summ, edges, "top_left")
            dims += _face_hole_dims(summ, edges, occ, thk_sheet, PROJ_BAND, scale)
        # flange height = horizontal extent (left/right envelope), BELOW the view (outer)
        if env["left"] and env["right"]:
            w_in = summ.get("width_in") or 0.0
            if not any(abs(w_in - a) < 0.02 for a in flange_vals):
                off = INNER_OFF + OUTER_STEP * occ.get("bottom", 0)
                dims.append({"type": "linear", "e1": env["left"]["i"], "e2": env["right"]["i"],
                             "text": [round((bb[0] + bb[2]) / 2, 1), round(bb[1] - off, 1)], "label": "flange_h"})
                occ["bottom"] = occ.get("bottom", 0) + 1
                flange_vals.append(w_in)
        if thk_sheet:
            dims += _flange_dims(edges, summ, thk_sheet, scale, True, flange_vals, "bottom", occ)
        if thk_r_on == side_view and thk_sheet:
            dims += thk_and_r(gs, True, prefer_right=True)
        if dims:
            r = sb._add_dimensions(side_view, [{k: v for k, v in d.items() if k != "label"} for d in dims])
            report["views"][side_view] = _compact(r, dims)
    report["thk_r_on"] = thk_r_on
    report["flange_lengths_in"] = [round(v, 4) for v in flange_vals]
    return report


def _compact(r: dict, dims: list[dict]) -> dict:
    out = []
    for d, res in zip(dims, r.get("dimensions", [])):
        e = {"label": d.get("label"), "value": res.get("value"), "name": res.get("name")}
        if res.get("error"):
            e["error"] = res["error"]
        if d.get("reference"):
            e["reference"] = True
        out.append(e)
    return {"added": r.get("added"), "dims": out}


# --------------------------------------------------------------------------- flat sheet

def _norm_pattern(summary, edges):
    """Circles as (radius_model_mm, x_sheet_mm, y_sheet_mm). Both views share the scale, so sheet mm
    are directly comparable — no bbox normalisation (a flat's bbox includes the flanges and distorted
    the match on 108716: mirrored candidate won 10.7 vs 12.5; this metric gives 10.7 vs 96)."""
    return [(round(r["r"], 2), r["c"][0], r["c"][1]) for r in edges if r.get("t") == "circle" and "c" in r and "r" in r]


def _pattern_error(base, cand):
    """Mean nearest-neighbour distance (sheet mm) from each base hole to a candidate hole of the SAME
    radius, after aligning the centroids (candidate centroid over the radii present in the base)."""
    if not base or not cand:
        return 0.0
    radii = {r for r, _, _ in base}
    sub = [(r, x, y) for r, x, y in cand if r in radii]
    if not sub:
        return 1e6
    bc = (sum(x for _, x, _ in base) / len(base), sum(y for _, _, y in base) / len(base))
    cc = (sum(x for _, x, _ in sub) / len(sub), sum(y for _, _, y in sub) / len(sub))
    err = 0.0
    for r, x, y in base:
        same = [(cx - cc[0], cy - cc[1]) for cr, cx, cy in sub if cr == r] or [(cx - cc[0], cy - cc[1]) for _, cx, cy in sub]
        err += min(math.hypot((x - bc[0]) - dx, (y - bc[1]) - dy) for dx, dy in same)
    return err / len(base)


def _transform_pattern(p, angle_deg, flip):
    """Rotate (CCW, degrees) / mirror-x the pattern about its own centroid (sheet mm)."""
    if not p:
        return p
    cx = sum(x for _, x, _ in p) / len(p); cy = sum(y for _, _, y in p) / len(p)
    a = math.radians(angle_deg)
    out = []
    for r, x, y in p:
        dx, dy = x - cx, y - cy
        if flip:
            dx = -dx
        out.append((r, cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)))
    return out


def _insert_flat_view(drawing, model_path, cfg, x_mm, y_mm, flip=False, flip_variant=0):
    """flip_variant: 0 = Flip as 6th arg, 1 = Flip as 7th arg (API doc ambiguity), 2 = IView.FlipView property."""
    before = {n for n, _ in _get_drawing_views(drawing, None)}
    v = None
    a6, a7 = (bool(flip), False) if flip_variant == 0 else ((False, bool(flip)) if flip_variant == 1 else (False, False))
    for call in (lambda: drawing.CreateFlatPatternViewFromModelView3(model_path, cfg, x_mm / _M_TO_MM, y_mm / _M_TO_MM, 0.0, a6, a7),
                 lambda: drawing.CreateFlatPatternViewFromModelView2(model_path, cfg, x_mm / _M_TO_MM, y_mm / _M_TO_MM, 0.0)):
        try:
            v = call()
            if v is not None:
                break
        except Exception as ex:  # noqa: BLE001
            logger.info("flat view insert raised %s", ex)
    slip._rebuild(drawing)
    name = _try(lambda: v.Name) if v is not None else None
    if not name:
        name = sv._new_view_name(drawing, before)
    if not name:
        raise SWError("flat-pattern view was not created")
    view = _view_by_name(drawing, name)
    if flip and flip_variant == 2:
        try:
            view.FlipView = True
        except Exception as ex:  # noqa: BLE001
            logger.info("FlipView property: %s", ex)
            try:
                slip._put(view, "FlipView", True)
            except Exception as ex2:  # noqa: BLE001
                logger.info("FlipView put: %s", ex2)
        slip._rebuild(drawing)
        view = _view_by_name(drawing, name)
    return name, view


def _slip_flat_sheet(model_path, base_view, scale, sheet_name, x_mm, y_mm, match_base_view=False) -> dict:
    drawing = slip._active_drawing()
    # base pattern (sheet 1) BEFORE switching sheets
    gb = _geom(base_view)
    base_pat = _norm_pattern(gb["summary"], gb["edges"])
    base_scale = gb["scale"] or 1.0
    s = float(scale) if scale else base_scale
    if abs(s - base_scale) > 1e-6:
        logger.info("flat scale %s differs from base %s: pattern match uses scaled coordinates", s, base_scale)
    bbb = gb["summary"]["bbox_mm"]
    base_aspect = (bbb[2] - bbb[0]) >= (bbb[3] - bbb[1])

    sheets = slip._sheet_names(drawing)
    if sheet_name not in sheets:
        raise SWError(f"sheet {sheet_name!r} not found; sheets: {sheets}")
    if not drawing.ActivateSheet(sheet_name):
        raise SWError(f"could not activate {sheet_name}")

    # configuration: ALWAYS "" — SolidWorks then creates/uses <cfg>SM-FLAT-PATTERN and flattens it.
    # Passing the existing "DEFAULTSM-FLAT-PATTERN" name explicitly yields a FORMED view (108789:
    # 8.0 x 1.375 instead of 8.0 x 5.785, no bend lines).
    cfg = ""

    def set_angle(view, angle):
        try:
            view.Angle = math.radians(angle)
        except Exception:  # noqa: BLE001
            slip._put(view, "Angle", math.radians(angle))
        slip._rebuild(drawing)

    def measure(name):
        g = _geom(name)
        pat = _norm_pattern(g["summary"], g["edges"])
        if abs(s - base_scale) > 1e-6:   # bring the flat's sheet mm to the base view's scale
            k = base_scale / s
            pat = [(r, x * k, y * k) for r, x, y in pat]
        return g, pat

    tried = []
    fname, fview = _insert_flat_view(drawing, model_path, cfg, x_mm, y_mm, False, 0)
    _set_scale(fview, s)
    slip._rebuild(drawing)
    g0, p0 = measure(fname)
    bb0 = g0["summary"]["bbox_mm"]
    w0, h0 = bb0[2] - bb0[0], bb0[3] - bb0[1]
    flip, variant, ang, err_best = False, 0, 0, None
    if match_base_view:
        # optional: flip/rotate so the hole pattern matches the base view (centroid-aligned metric)
        best = None
        for fl, var in ((False, 0), (True, 1), (True, 2)):
            if fl:
                _try(lambda: slip._delete_view(fname))
                fname, fview = _insert_flat_view(drawing, model_path, cfg, x_mm, y_mm, fl, var)
                _set_scale(fview, s)
                slip._rebuild(drawing)
                g0, p0 = measure(fname)
            flat_aspect0 = (g0["summary"]["bbox_mm"][2] - g0["summary"]["bbox_mm"][0]) >= (g0["summary"]["bbox_mm"][3] - g0["summary"]["bbox_mm"][1])
            rots = [0, 180] if flat_aspect0 == base_aspect else [-90, 90]
            for a in rots:
                e = _pattern_error(base_pat, _transform_pattern(p0, a, False))
                tried.append({"flip": fl, "variant": var, "angle": a, "err": round(e, 2)})
                if best is None or e < best[0]:
                    best = (e, fl, a, var)
            if best and best[1] == fl and best[0] < 6.0:
                break
            if fl:
                break
        err_best, flip, ang, variant = best
        if flip != (tried[-1]["flip"] if tried else False):
            _try(lambda: slip._delete_view(fname))
            fname, fview = _insert_flat_view(drawing, model_path, cfg, x_mm, y_mm, flip, variant)
            _set_scale(fview, s)
        set_angle(fview, ang)
    else:
        # Simón 2026-09-17: the flat does NOT need to match the formed views' orientation — keep it
        # as SolidWorks inserts it; rotate 90° only when it would not fit the sheet otherwise.
        max_h = 340.0   # notes block above, title block below (D sheet)
        max_w = 640.0
        if (h0 > max_h or w0 > max_w) and (w0 <= max_h and h0 <= max_w):
            ang = 90
            set_angle(fview, ang)
            tried.append({"rotated_to_fit": ang})
    logger.info("flat orientation: flip=%s angle=%s match_base_view=%s tried=%s", flip, ang, match_base_view, tried)
    err_after = err_best
    _try(lambda: slip._set_view_display(fname, "hidden_lines_removed", "removed"))
    _set_pos(fview, x_mm, y_mm)
    slip._rebuild(drawing)
    g = _geom(fname)
    summ, edges = g["summary"], g["edges"]
    bb = summ["bbox_mm"]
    env = summ["envelope_edges"]

    # bend lines -> edge-to-bend-line dims (nearest parallel envelope edge), then overall.
    # Several bend lines measured from the SAME edge (108689: 1.199 and 5.534 from the left end) are
    # stacked outward, shortest span innermost, one OUTER_STEP apart — never on one shared row.
    bl = sv._flat_bend_lines(fname).get("bend_lines", [])
    bend_dims, occupied = [], {}
    seen: set = set()   # (orientation, side, distance_in rounded): equal bend distances dimensioned ONCE
    groups: dict = {}   # (orientation, side) -> [(span, ref_i, bend_i, m)]
    for b in bl:
        m = b["m"]
        if b["orientation"] == "horizontal":
            near_top = (bb[3] - m[1]) <= (m[1] - bb[1])
            ref = env["top"] if near_top else env["bottom"]
            if ref:
                edge_y = bb[3] if near_top else bb[1]
                span = abs(m[1] - edge_y)
                key = ("h", near_top, round(span / s / _IN, 2))
                if key in seen:
                    continue
                seen.add(key)
                groups.setdefault(("h", near_top), []).append((span, ref["i"], b["i"], m, edge_y))
        else:
            near_left = (m[0] - bb[0]) <= (bb[2] - m[0])
            ref = env["left"] if near_left else env["right"]
            if ref:
                edge_x = bb[0] if near_left else bb[2]
                span = abs(m[0] - edge_x)
                key = ("v", near_left, round(span / s / _IN, 2))
                if key in seen:
                    continue
                seen.add(key)
                groups.setdefault(("v", near_left), []).append((span, ref["i"], b["i"], m, edge_x))
    for (orient, side), items in groups.items():
        items.sort(key=lambda t: t[0])
        for n, (span, ref_i, bend_i, m, edge) in enumerate(items):
            off = INNER_OFF + OUTER_STEP * n
            if orient == "h":
                # vertical dimension on the LEFT of the view; short span: text ALONG the dim, outside the span
                ty = (m[1] + edge) / 2 if span >= SHORT_V else (edge + 12 if side else edge - 12)
                bend_dims.append({"e_ref": ref_i, "bend": bend_i, "text": [round(bb[0] - off, 1), round(ty, 1)]})
                occupied["left"] = max(occupied.get("left", 0), n + 1)
            else:
                # horizontal dimension ABOVE the view
                tx = (m[0] + edge) / 2 if span >= SHORT_H else (edge - 14 if side else edge + 14)
                bend_dims.append({"e_ref": ref_i, "bend": bend_i, "text": [round(tx, 1), round(bb[3] + off, 1)]})
                occupied["top"] = max(occupied.get("top", 0), n + 1)
    rb = sv._add_bend_dimensions(fname, bend_dims) if bend_dims else {"dimensions": []}
    # edge indices can change after annotations are added: re-read before the envelope dims
    summ = _geom(fname)["summary"]
    env_dims = _envelope_dims(summ, occupied)
    re_ = sb._add_dimensions(fname, [{k: v for k, v in d.items() if k != "label"} for d in env_dims])
    return {"status": "done", "sheet": sheet_name, "view": fname, "config": cfg, "scale": s,
            "orientation": {"angle_deg": ang, "flip": flip, "flip_variant": variant, "match_base_view": match_base_view, "pattern_error_mm": (round(err_after, 2) if err_after is not None else None), "tried": tried},
            "flat_size_in": [summ.get("width_in"), summ.get("height_in")],
            "bend_dims": [{"value": d.get("value"), "name": d.get("name"), "error": d.get("error")} for d in rb.get("dimensions", [])],
            "envelope": _compact(re_, env_dims)}


# --------------------------------------------------------------------------- export

def _export_slip_pdf(pdf_path, save, png_per_sheet=False) -> dict:
    drawing = slip._active_drawing()
    out = {"status": "done"}
    path = _try(lambda: str(_val(drawing, "GetPathName"))) or ""
    if save and path:
        out["save"] = _save_drawing_as(drawing, path)
    if not pdf_path:
        pn = os.path.splitext(os.path.basename(path))[0] or "drawing"
        rev = "R00"
        try:
            views = _get_drawing_views(drawing, None)
            for _, v in views:
                m = _try(lambda: _val(v, "ReferencedDocument"))
                if m is not None:
                    r = _custom_props(m, ("REVISION",)).get("REVISION")
                    if r:
                        rev = r
                    break
        except Exception:  # noqa: BLE001
            pass
        pdf_path = os.path.join(os.path.dirname(path) or r"C:\Drawings", f"{pn}-{rev}.pdf")
    errs, warns = _byref_i4(), _byref_i4()
    import pythoncom
    from win32com.client import VARIANT
    null = VARIANT(pythoncom.VT_DISPATCH, None)
    ok = False
    try:
        ok = bool(drawing.Extension.SaveAs3(pdf_path, 0, 1, null, null, errs, warns))
    except Exception as ex:  # noqa: BLE001
        out["pdf_error"] = str(ex)
    out["pdf"] = pdf_path if ok else None
    out["pdf_errors"] = _try(lambda: errs.value)
    if not ok:
        out["status"] = "partial"
    if png_per_sheet:
        # one PNG per sheet for the visual check (SaveAs .png renders the ACTIVE sheet)
        pngs = []
        cur = _try(lambda: slip._current_sheet_name(drawing))
        for sh in _try(lambda: slip._sheet_names(drawing), []) or []:
            try:
                drawing.ActivateSheet(sh)
                _try(lambda: slip._inv(drawing, "ViewZoomtofit2"))  # PNG SaveAs renders the current zoom
                png = os.path.splitext(pdf_path)[0] + f"-{sh}.png"
                e2, w2 = _byref_i4(), _byref_i4()
                if bool(drawing.Extension.SaveAs3(png, 0, 1, null, null, e2, w2)):
                    pngs.append(png)
            except Exception as ex:  # noqa: BLE001
                logger.info("png export %s: %s", sh, ex)
        if cur:
            _try(lambda: drawing.ActivateSheet(cur))
        out["png"] = pngs
    return out
