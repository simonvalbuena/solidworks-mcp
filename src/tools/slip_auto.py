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
GAP_V, GAP_H = 34.0, 62.0                  # view-to-view gaps (room for the inner dims)
ISO_POS = (770.0, 180.0)
STD_SCALES = (0.125, 0.1667, 0.2, 0.25, 0.3333, 0.5, 0.6667, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0)
# ---- dimension placement rules (§7b) ----
INNER_OFF = 24.0       # hole-location dims off the view bbox
OUTER_STEP = 17.0      # envelope dims a little further out
DIA_OFF = 26.0         # Ø text clear of the view / dim lines
THK_TEXT_ALONG = 18.0  # tiny (thk) dims: text offset ALONG the dimension direction

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
    ) -> str:
        """Lay out a Slip part drawing on the ACTIVE drawing (create it first from the Slip
        template, e.g. solidpilot.create_drawing): ONE base model view (base_view: top|front|
        right|... or 'auto' = look along the thin axis), PROJECTED views (edge_views: 'auto' =
        'bottom' for a flat plate, 'bottom,right' for a bent part; or a comma list), a small iso at
        lower-right, standard scale chosen to fit ~1/3 of the sheet (or `scale`), HLR + tangent
        edges removed (iso visible), saved to save_path (default C:\\Drawings\\<PN>.SLDDRW).
        Returns view names, roles, outlines and the scale — feed to dimension_slip_views."""
        return await _run(sw, "layout_slip_part", _layout_slip_part, model_path, base_view,
                          edge_views, scale, save_path, rotate_base_deg)

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
    ) -> str:
        """Flat-pattern sheet: activates sheet_name, inserts the flat pattern view (SM-FLAT-PATTERN
        configuration, bend notes), flips/rotates it so its hole pattern matches base_view on
        sheet 1, HLR/tangent removed, then dimensions flat overall W/H + every edge-to-bend-line
        distance (2026 form, no parentheses). Returns the view name, orientation chosen and dims."""
        return await _run(sw, "slip_flat_sheet", _slip_flat_sheet, model_path, base_view, scale,
                          sheet_name, x_mm, y_mm)

    @mcp.tool()
    async def export_slip_pdf(pdf_path: str | None = None, save: bool = True) -> str:
        """Save the active drawing and export it to PDF (default <drawing dir>\\<PN>-<REV>.pdf,
        REV from the model's REVISION property or R00). Uses SaveAs3 with ByRef VARIANTs."""
        return await _run(sw, "export_slip_pdf", _export_slip_pdf, pdf_path, save)


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

def _find_slip_template() -> str | None:
    pats = [r"C:\ProgramData\SolidWorks\SOLIDWORKS *\templates\*.drwdot",
            r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS *\templates\*.drwdot"]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    pref = [h for h in hits if any(k in os.path.basename(h).upper() for k in ("SLIP", " SR ", "SR_", "SR-", "FORMAT SR"))]
    return (pref or [None])[0]


def _ensure_drawing(app):
    doc = _try(lambda: app.ActiveDoc)
    if doc is not None and _try(lambda: int(slip._inv(doc, "GetType"))) == 3:
        return doc, None
    # any open drawing (most recently created first is not knowable; take the first found)
    d = _try(lambda: app.GetFirstDocument)
    while d is not None:
        if _try(lambda: int(slip._inv(d, "GetType"))) == 3:
            _try(lambda: sv._activate_doc(app, str(_val(d, "GetTitle"))))
            return d, None
        d = _try(lambda: _val(d, "GetNext"))
    tpl = _find_slip_template()
    if not tpl:
        raise SWError("no active drawing and no Slip .drwdot found in the SolidWorks templates folder — "
                      "create the drawing first (solidpilot.create_drawing)")
    d = app.NewDocument(tpl, 4, 0.0, 0.0)  # swDwgPaperDsize=4 (ignored when the template carries its sheet format)
    if d is None:
        raise SWError(f"NewDocument failed for template {tpl}")
    return d, tpl


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


def _layout_slip_part(model_path, base_view, edge_views, scale, save_path, rotate_base_deg) -> dict:
    app = SWConnection.get_instance().get_app()
    # the drawing FIRST (opening/activating the part changes ActiveDoc)
    drawing, tpl = _ensure_drawing(app)
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


def _hole_dims(summary, edges, hole, occupied: dict) -> list[dict]:
    """X + Y + Ø for one hole. Returns dims and records which sides got an inner dim."""
    bb = summary["bbox_mm"]
    env = summary["envelope_edges"]
    cx, cy = hole["c"]
    dims = []
    # nearest vertical edge for X, nearest horizontal edge for Y
    use_left = (cx - bb[0]) <= (bb[2] - cx)
    use_bottom = (cy - bb[1]) <= (bb[3] - cy)
    ex = env["left"] if use_left else env["right"]
    ey = env["bottom"] if use_bottom else env["top"]
    # X dim (horizontal): place on the side (top/bottom) nearer the hole so extension lines stay short
    x_side = "bottom" if use_bottom else "top"
    y_band = (bb[1] - INNER_OFF) if x_side == "bottom" else (bb[3] + INNER_OFF)
    if ex:
        tx = (bb[0] - 20.0) if use_left else (bb[2] + 20.0)   # text outside the (often tiny) span
        dims.append({"type": "linear", "e1": hole["i"], "e2": ex["i"], "text": [round(tx, 1), round(y_band, 1)], "label": "hole_x"})
        occupied[x_side] = occupied.get(x_side, 0) + 1
    # Y dim (vertical): on the side (left/right) nearer the hole
    y_side = "left" if use_left else "right"
    x_band = (bb[0] - INNER_OFF) if y_side == "left" else (bb[2] + INNER_OFF)
    if ey:
        dims.append({"type": "linear", "e1": hole["i"], "e2": ey["i"], "text": [round(x_band, 1), round((cy + (bb[1] if use_bottom else bb[3])) / 2, 1)], "label": "hole_y"})
        occupied[y_side] = occupied.get(y_side, 0) + 1
    # Ø leader text: same band as the X dim, ~55 mm toward the view centre from the hole
    dirx = 1.0 if cx < (bb[0] + bb[2]) / 2 else -1.0
    dims.append({"type": "diameter", "e1": hole["i"], "text": [round(cx + dirx * 55.0, 1), round(y_band + (3 if x_side == "bottom" else -3), 1)], "label": "hole_dia"})
    return dims


def _envelope_dims(summary, occupied: dict, want_w=True, want_h=True) -> list[dict]:
    bb = summary["bbox_mm"]
    env = summary["envelope_edges"]
    dims = []
    if want_w and env["left"] and env["right"]:
        # envelope width above the view; a step further out when the hole X dim is already there
        off = INNER_OFF + (OUTER_STEP if occupied.get("top", 0) else 0)
        y = bb[3] + off
        dims.append({"type": "linear", "e1": env["left"]["i"], "e2": env["right"]["i"], "text": [round((bb[0] + bb[2]) / 2, 1), round(y, 1)], "label": "envelope_w"})
    if want_h and env["top"] and env["bottom"]:
        side = "left"
        off = INNER_OFF + (OUTER_STEP if occupied.get(side, 0) else 0)
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


def _bend_arc(edges, thk_model_mm, bend_radius_in):
    arcs = [r for r in edges if r.get("t") == "arc" and r.get("r")]
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


def _dimension_slip_views(base_view, edge_view, side_view, thickness_in, bend_radius_in, base_hole) -> dict:
    drawing = slip._active_drawing()
    report = {"status": "done", "views": {}}

    # ---- base view
    g = _geom(base_view)
    summ, edges, scale = g["summary"], g["edges"], g["scale"] or 1.0
    occupied: dict = {}
    dims = []
    hole = _pick_hole(summ, edges, base_hole)
    if hole:
        dims += _hole_dims(summ, edges, hole, occupied)
    dims += _envelope_dims(summ, occupied)
    r = sb._add_dimensions(base_view, [{k: v for k, v in d.items() if k != "label"} for d in dims])
    report["views"][base_view] = _compact(r, dims)

    thk_model_mm = thickness_in * _IN if thickness_in else None

    # ---- edge view (bottom projection)
    if edge_view:
        g = _geom(edge_view)
        summ, edges = g["summary"], g["edges"]
        bb = summ["bbox_mm"]
        dims = []
        h_in = summ.get("height_in") or 0
        circles = [e for e in edges if e.get("t") == "circle"]
        thin = (thickness_in and abs(h_in - thickness_in) < 0.02) or (not circles and h_in < 0.35)
        if thin and summ["envelope_edges"]["top"] and summ["envelope_edges"]["bottom"]:
            # (thk): vertical dim at the left end, text offset ALONG the dim (above the view)
            dims.append({"type": "linear", "e1": summ["envelope_edges"]["top"]["i"], "e2": summ["envelope_edges"]["bottom"]["i"],
                         "text": [round(bb[0] - INNER_OFF, 1), round(bb[3] + THK_TEXT_ALONG, 1)], "reference": True, "label": "thk"})
        elif circles:
            occ: dict = {}
            hole = _pick_hole(summ, edges, "top_left")
            dims += _hole_dims(summ, edges, hole, occ)
        if dims:
            r = sb._add_dimensions(edge_view, [{k: v for k, v in d.items() if k != "label"} for d in dims])
            report["views"][edge_view] = _compact(r, dims)
        else:
            report["views"][edge_view] = {"note": "no dims (no holes, not a thin edge)"}

    # ---- side view (right projection): flange height + (thk) + (R)
    if side_view:
        g = _geom(side_view)
        summ, edges = g["summary"], g["edges"]
        bb = summ["bbox_mm"]
        env = summ["envelope_edges"]
        dims = []
        thk_sheet = thk_model_mm * scale if thk_model_mm else None
        # flange height = horizontal extent (left/right envelope), BELOW the view (outer)
        if env["left"] and env["right"]:
            dims.append({"type": "linear", "e1": env["left"]["i"], "e2": env["right"]["i"],
                         "text": [round((bb[0] + bb[2]) / 2, 1), round(bb[1] - INNER_OFF - OUTER_STEP, 1)], "label": "flange_h"})
        pair = None
        if thk_sheet:
            pair = _thickness_pair(edges, thk_sheet, horizontal_lines=False)  # vertical lines thk apart (plate edge-on)
            if pair:
                dims.append({"type": "linear", "e1": pair[0], "e2": pair[1],
                             "text": [round(bb[0] - INNER_OFF - 4, 1), round(bb[1] - INNER_OFF, 1)], "reference": True, "label": "thk"})
            else:
                pair = _thickness_pair(edges, thk_sheet, horizontal_lines=True)
                if pair:
                    dims.append({"type": "linear", "e1": pair[0], "e2": pair[1],
                                 "text": [round(bb[2] + INNER_OFF, 1), round(bb[3] + THK_TEXT_ALONG, 1)], "reference": True, "label": "thk"})
        arc = _bend_arc(edges, thk_model_mm, bend_radius_in)
        if arc:
            dims.append({"type": "radius", "e1": arc["i"], "text": [round(bb[2] + 20.0, 1), round(bb[3] + 22.0, 1)], "reference": True, "label": "bend_r"})
        if dims:
            r = sb._add_dimensions(side_view, [{k: v for k, v in d.items() if k != "label"} for d in dims])
            report["views"][side_view] = _compact(r, dims)
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
    """Circles normalised to the bbox: list of (dia_group_rank, u, v)."""
    bb = summary["bbox_mm"]
    w = max(1e-6, bb[2] - bb[0]); h = max(1e-6, bb[3] - bb[1])
    circles = [r for r in edges if r.get("t") == "circle" and "c" in r]
    radii = sorted({round(c["r"], 2) for c in circles}, reverse=True)
    return [(radii.index(round(c["r"], 2)), (c["c"][0] - bb[0]) / w, (c["c"][1] - bb[1]) / h) for c in circles]


def _pattern_error(a, b):
    """Sum of nearest-neighbour distances between two normalised patterns (same radius rank)."""
    if not a or not b:
        return 0.0
    err = 0.0
    for ra, ua, va in a:
        cands = [(ub, vb) for rb, ub, vb in b if rb == ra] or [(ub, vb) for _, ub, vb in b]
        err += min(math.hypot(ua - ub, va - vb) for ub, vb in cands)
    return err


def _transform_pattern(p, angle_deg, flip):
    out = []
    for rk, u, v in p:
        x, y = u - 0.5, v - 0.5
        if flip:
            x = -x
        a = math.radians(angle_deg)
        xr = x * math.cos(a) - y * math.sin(a)
        yr = x * math.sin(a) + y * math.cos(a)
        out.append((rk, xr + 0.5, yr + 0.5))
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


def _slip_flat_sheet(model_path, base_view, scale, sheet_name, x_mm, y_mm) -> dict:
    drawing = slip._active_drawing()
    # base pattern (sheet 1) BEFORE switching sheets
    gb = _geom(base_view)
    base_pat = _norm_pattern(gb["summary"], gb["edges"])
    base_scale = gb["scale"] or 1.0
    s = float(scale) if scale else base_scale
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
        return g, _norm_pattern(g["summary"], g["edges"])

    # brute force: flip False first; only re-insert flipped when no rotation matches the base view
    tried = []
    fname = fview = None
    best = None
    unflipped_sig = None
    # (flip, variant): unflipped first; then the three ways SolidWorks may accept a flip — keep the first
    # one that actually changes the geometry (the API is ambiguous about the Flip argument position)
    for flip, variant in ((False, 0), (True, 0), (True, 1), (True, 2)):
        if fname:
            _try(lambda: slip._delete_view(fname))
        fname, fview = _insert_flat_view(drawing, model_path, cfg, x_mm, y_mm, flip, variant)
        _set_scale(fview, s)
        set_angle(fview, 0)
        g0, p0 = measure(fname)
        sig = tuple(sorted((rk, round(u, 3), round(v, 3)) for rk, u, v in p0))
        if not flip:
            unflipped_sig = sig
        elif sig == unflipped_sig:
            tried.append({"flip": True, "variant": variant, "note": "no effect"})
            continue  # this flip variant did nothing — try the next
        bb0 = g0["summary"]["bbox_mm"]
        flat_aspect0 = (bb0[2] - bb0[0]) >= (bb0[3] - bb0[1])
        rots = [0, 180] if flat_aspect0 == base_aspect else [-90, 90]
        for ang in rots:
            set_angle(fview, ang)
            _, p = measure(fname)
            err = _pattern_error(base_pat, p) + _pattern_error(p, base_pat)
            tried.append({"flip": flip, "variant": variant, "angle": ang, "err": round(err, 3)})
            if best is None or err < best[0]:
                best = (err, flip, ang, variant)
        if best and best[1] == flip and best[0] < 0.15:
            break
        if flip:
            break  # a working flip variant was found and measured; stop trying others
    err_best, flip, ang, variant = best
    cur_flip = tried[-1].get("flip") if tried else False
    if fview is not None and (flip != cur_flip):
        _try(lambda: slip._delete_view(fname))
        fname, fview = _insert_flat_view(drawing, model_path, cfg, x_mm, y_mm, flip, variant)
        _set_scale(fview, s)
    set_angle(fview, ang)
    _try(lambda: slip._set_view_display(fname, "hidden_lines_removed", "removed"))
    _set_pos(fview, x_mm, y_mm)
    slip._rebuild(drawing)
    g = _geom(fname)
    summ, edges = g["summary"], g["edges"]
    bb = summ["bbox_mm"]
    env = summ["envelope_edges"]

    # bend lines -> edge-to-bend-line dims (nearest parallel envelope edge), then overall
    bl = sv._flat_bend_lines(fname).get("bend_lines", [])
    bend_dims, occupied = [], {}
    for b in bl:
        m = b["m"]
        if b["orientation"] == "horizontal":
            near_top = (bb[3] - m[1]) <= (m[1] - bb[1])
            ref = env["top"] if near_top else env["bottom"]
            if ref:
                y_mid = (m[1] + (bb[3] if near_top else bb[1])) / 2
                bend_dims.append({"e_ref": ref["i"], "bend": b["i"], "text": [round(bb[0] - INNER_OFF, 1), round(y_mid, 1)]})
                occupied["left"] = occupied.get("left", 0) + 1
        else:
            near_left = (m[0] - bb[0]) <= (bb[2] - m[0])
            ref = env["left"] if near_left else env["right"]
            if ref:
                x_mid = (m[0] + (bb[0] if near_left else bb[2])) / 2
                bend_dims.append({"e_ref": ref["i"], "bend": b["i"], "text": [round(x_mid, 1), round(bb[3] + INNER_OFF, 1)]})
                occupied["top"] = occupied.get("top", 0) + 1
    rb = sv._add_bend_dimensions(fname, bend_dims) if bend_dims else {"dimensions": []}
    # edge indices can change after annotations are added: re-read before the envelope dims
    summ = _geom(fname)["summary"]
    env_dims = _envelope_dims(summ, occupied)
    re_ = sb._add_dimensions(fname, [{k: v for k, v in d.items() if k != "label"} for d in env_dims])
    return {"status": "done", "sheet": sheet_name, "view": fname, "config": cfg, "scale": s,
            "orientation": {"angle_deg": ang, "flip": flip, "flip_variant": variant, "pattern_error": round(err_best, 3), "tried": tried},
            "flat_size_in": [summ.get("width_in"), summ.get("height_in")],
            "bend_dims": [{"value": d.get("value"), "name": d.get("name"), "error": d.get("error")} for d in rb.get("dimensions", [])],
            "envelope": _compact(re_, env_dims)}


# --------------------------------------------------------------------------- export

def _export_slip_pdf(pdf_path, save) -> dict:
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
    return out
