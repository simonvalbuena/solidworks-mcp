"""Read-only drawing inspector for learning Slip Robotics drawing conventions.

inspect_drawing(file_path=None, out_path=None)
    Opens (or uses the active) drawing and reports, per sheet: paper/scale/template, every
    view (feature type, IView.Type, parent/base view, referenced model + configuration,
    orientation, display mode, tangent-edge setting, scale, rotation, flat-pattern / break /
    section flags, bend-line count), every display dimension (value, type, parenthesis,
    text position, attached-entity types), notes (with leaders / balloons), tables, and a
    sample of the referenced model's feature types (SheetMetal, Weldment, ...).

Nothing is modified and nothing is saved. All zero-arg COM getters go through _val()
(attribute read, call if a bound method came back, else DISPID invoke) — see slip.py header.
"""

from __future__ import annotations

import json
import logging
import math
import os

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection
from tools import slip

logger = logging.getLogger(__name__)

_M_TO_MM = 1000.0

# swDrawingViewTypes_e
_VIEW_TYPES = {1: "sheet", 2: "section", 3: "detail", 4: "projected", 5: "auxiliary",
               6: "standard(model)", 7: "named(model)", 8: "relative", 9: "detached",
               10: "alternate_position", 11: "broken_out_section", 12: "empty", 13: "crop"}
# swDisplayMode_e (view display styles)
_DISPLAY_MODES = {0: "?0", 1: "wireframe", 2: "hidden_lines_visible", 3: "hidden_lines_removed",
                  4: "shaded", 5: "shaded_with_edges", 6: "?6"}
# swDisplayTangentEdges_e
_TANGENT = {0: "visible", 1: "visible_font", 2: "removed"}
# swSelectType_e (attached entities)
_SEL = {0: "nothing", 1: "edge", 2: "face", 3: "vertex", 4: "datum_plane", 5: "datum_axis",
        9: "sketch_segment", 10: "sketch_point", 11: "dimension", 12: "?12", 14: "note",
        15: "sketch_segment?15", 20: "silhouette", 22: "view_arrow", 46: "drawing_view",
        58: "centerline", 59: "centermark", 66: "sketch_hatch", 85: "external_sketch_segment",
        86: "external_sketch_point", 160: "hole_callout?"}
# swDimensionType_e (IDisplayDimension.Type2)
_DIM_TYPES = {0: "unknown", 1: "ordinate", 2: "linear", 3: "angular", 4: "arc_length",
              5: "radius", 6: "diameter", 7: "hor_ordinate", 8: "vert_ordinate", 9: "z_ordinate",
              10: "chamfer", 11: "angular_ordinate", 12: "scalar"}
# swTableAnnotationType_e
_TABLE_TYPES = {0: "general", 1: "hole", 2: "bom", 3: "revision", 4: "weld", 5: "weldment_cutlist",
                6: "title_block", 7: "punch", 8: "bend", 9: "design", 10: "general_tolerance"}

_MODEL_FEATURE_TYPES_OF_INTEREST = (
    "SheetMetal", "FlatPattern", "Flatten", "SMBaseFlange", "EdgeFlange", "SketchBend",
    "Hem", "Jog", "WeldMemberFeat", "WeldmentFeature", "WeldCornerFeat", "StructuralMember",
    "CutListFolder", "Weldment", "MirrorStock", "MirrorPattern", "HoleWzd", "LPattern",
    "CirPattern", "Chamfer", "Fillet", "Extrusion", "Cut", "Boss", "Thread", "Split",
    "Stock", "DerivedLPattern", "SolidBodyFolder",
)


def _val(obj, name):
    try:
        v = getattr(obj, name)
        if callable(v):
            v = v()
        return v
    except Exception:  # noqa: BLE001
        return slip._inv(obj, name)


def _try(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


def _seq(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]


def _mm(v, nd=2):
    try:
        return round(float(v) * _M_TO_MM, nd)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- view details

def _display_mode(view) -> dict:
    """IView.GetDisplayMode3(ByRef useParent, ByRef mode, ByRef useParentQuality, ByRef quality)."""
    out = {}
    try:
        import pythoncom
        from win32com.client import VARIANT
        a = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
        b = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        c = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_BOOL, False)
        d = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        for name in ("GetDisplayMode3", "GetDisplayMode2"):
            try:
                m = getattr(view, name)
                if isinstance(m, (int, float)) and not isinstance(m, bool):
                    # late binding evaluated the getter without args and returned the mode itself
                    out["display_mode"] = _DISPLAY_MODES.get(int(m), str(m))
                    break
                m(a, b, c, d) if name == "GetDisplayMode3" else m(a, b)
                out["display_mode"] = _DISPLAY_MODES.get(int(b.value), str(b.value))
                out["display_uses_parent"] = bool(a.value)
                break
            except Exception as ex:  # noqa: BLE001
                out["display_mode_error"] = str(ex)
    except Exception as ex:  # noqa: BLE001
        out["display_mode_error"] = str(ex)
    te = _try(lambda: _val(view, "GetDisplayTangentEdges2"))
    if te is None:
        te = _try(lambda: _val(view, "GetDisplayTangentEdges"))
    if te is not None:
        try:
            out["tangent_edges"] = _TANGENT.get(int(te), str(te))
        except Exception:  # noqa: BLE001
            out["tangent_edges"] = str(te)
    return out


def _dim_details(dd) -> dict:
    info = {}
    try:
        dim = slip._wrap(dd.GetDimension2(0))
        info["name"] = str(slip._inv(dim, "FullName"))
        info["value"] = round(float(slip._inv(dim, "Value")), 4)
        ds = _try(lambda: _val(dim, "DrivenState"))
        if ds is not None:
            info["driven"] = {0: "unknown", 1: "driven", 2: "driving"}.get(int(ds), str(ds))
    except Exception as ex:  # noqa: BLE001
        info["error"] = str(ex)
    t2 = _try(lambda: _val(dd, "Type2"))
    if t2 is not None:
        info["display_type"] = _DIM_TYPES.get(int(t2), str(t2))
    for key, member in (("parenthesis", "GetParenthesis"), ("parenthesis", "ShowParenthesis"),
                        ("parenthesis", "Parenthesis"), ("reference", "IsReference"),
                        ("reference", "ReferenceDimension"), ("dual", "DualDimension"),
                        ("marked_for_drawing", "MarkedForDrawing"), ("centre_text", "CenterText"),
                        ("hole_callout", "IsHoleCallout"), ("inspection", "Inspection"),
                        ("broken_leader", "BrokenLeader"), ("witness_visible", "WitnessVisible")):
        if key in info:
            continue
        v = _try(lambda m=member: _val(dd, m))
        if v is not None and not hasattr(v, "_oleobj_"):
            try:
                info[key] = bool(v) if isinstance(v, (bool, int)) else v
            except Exception:  # noqa: BLE001
                pass
    for idx, key in ((0, "prefix"), (1, "suffix"), (2, "callout_above"), (3, "callout_below")):
        v = _try(lambda i=idx: dd.GetText(i))
        if v:
            info[key] = str(v)[:80]
    # tolerance
    try:
        tol = _val(slip._wrap(dd.GetDimension2(0)), "Tolerance")
        tt = _try(lambda: _val(tol, "Type"))
        if tt not in (None, 0):
            info["tolerance_type"] = int(tt)
    except Exception:  # noqa: BLE001
        pass
    try:
        ann = slip._inv(dd, "GetAnnotation")
        pos = slip._inv(ann, "GetPosition")
        info["text_mm"] = [_mm(pos[0]), _mm(pos[1])]
        n = _try(lambda: int(_val(ann, "GetAttachedEntityCount")))
        if n is not None:
            info["attached_count"] = n
        types = _try(lambda: _seq(_val(ann, "GetAttachedEntityTypes3")))
        if not types:
            types = _try(lambda: _seq(_val(ann, "GetAttachedEntityTypes2")))
        if types:
            info["attached_types"] = [_SEL.get(int(t), str(int(t))) for t in types]
        info["leader"] = _try(lambda: bool(_val(ann, "GetLeader")))
        vis = _try(lambda: int(_val(ann, "Visible")))
        if vis is not None:
            info["visible"] = vis
    except Exception:  # noqa: BLE001
        pass
    return info


def _note_details(note) -> dict:
    info = {}
    for key, member in (("name", "GetName"), ("text", "GetText"), ("linked_text", "PropertyLinkedText")):
        v = _try(lambda m=member: str(slip._inv(note, m)))
        if v is not None:
            info[key] = v if key == "name" else (v[:3000] if key == "text" else v[:600])
    if info.get("text") == info.get("linked_text"):
        info.pop("linked_text", None)
    info["balloon"] = _try(lambda: bool(_val(note, "IsBomBalloon")), False)
    bs = _try(lambda: int(_val(note, "GetBalloonStyle")))
    if bs is not None and bs != 0:
        info["balloon_style"] = bs
    try:
        ann = slip._inv(note, "GetAnnotation")
        info["leader"] = _try(lambda: bool(_val(ann, "GetLeader")))
        n = _try(lambda: int(_val(ann, "GetAttachedEntityCount")))
        if n:
            info["attached_count"] = n
            types = _try(lambda: _seq(_val(ann, "GetAttachedEntityTypes3")))
            if types:
                info["attached_types"] = [_SEL.get(int(t), str(int(t))) for t in types]
        pos = _try(lambda: slip._inv(ann, "GetPosition"))
        if pos is not None:
            info["pos_mm"] = [_mm(pos[0]), _mm(pos[1])]
        h = _try(lambda: _val(note, "GetHeight"))
        if h is not None:
            info["font_height_mm"] = _mm(h)
    except Exception:  # noqa: BLE001
        pass
    return info


def _table_details(tbl) -> dict:
    info = {}
    t = _try(lambda: int(_val(tbl, "Type")))
    if t is not None:
        info["type"] = _TABLE_TYPES.get(t, str(t))
    rows = _try(lambda: int(_val(tbl, "RowCount")))
    cols = _try(lambda: int(_val(tbl, "ColumnCount")))
    info["rows"] = rows
    info["cols"] = cols
    info["title"] = _try(lambda: str(_val(tbl, "Title")))
    if rows and cols and rows <= 40 and cols <= 12:
        cells = []
        for r in range(rows):
            row = []
            for c in range(cols):
                row.append(_try(lambda r=r, c=c: str(tbl.Text(r, c)), "")[:60])
            cells.append(row)
        info["cells"] = cells
    return info


def _model_feature_sample(model, limit=400) -> dict:
    """Bounded walk of the referenced model's feature tree; counts of interesting types."""
    counts: dict[str, int] = {}
    names = []
    try:
        feat = _val(model, "FirstFeature")
        n = 0
        while feat is not None and n < limit:
            n += 1
            tn = _try(lambda: str(_val(feat, "GetTypeName2")), "")
            if tn in _MODEL_FEATURE_TYPES_OF_INTEREST:
                counts[tn] = counts.get(tn, 0) + 1
                if len(names) < 12:
                    names.append(f"{tn}:{_try(lambda: str(_val(feat, 'Name')), '?')}")
            feat = _try(lambda: _val(feat, "GetNextFeature"))
    except Exception as ex:  # noqa: BLE001
        counts["_error"] = str(ex)[:80]
    return {"feature_type_counts": counts, "sample": names}


def _view_details(view, feature_type_by_name: dict) -> dict:
    name = _try(lambda: str(_val(view, "Name")), "?")
    info: dict = {"name": name, "feature_type": feature_type_by_name.get(name)}
    vt = _try(lambda: int(_val(view, "Type")))
    if vt is not None:
        info["type"] = _VIEW_TYPES.get(vt, str(vt))
    info["referenced_model"] = _try(lambda: os.path.basename(str(_val(view, "GetReferencedModelName"))))
    info["referenced_config"] = _try(lambda: str(_val(view, "ReferencedConfiguration")))
    for member in ("GetOrientationName", "Orientation"):
        v = _try(lambda m=member: _val(view, m))
        if v is not None and not hasattr(v, "_oleobj_"):
            info["orientation"] = str(v)
            break
    base = _try(lambda: _val(view, "GetBaseView"))
    if base is not None and hasattr(base, "_oleobj_"):
        info["base_view"] = _try(lambda: str(_val(base, "Name")))
    info["scale"] = _try(lambda: round(float(_val(view, "ScaleDecimal")), 4))
    ratio = _try(lambda: _seq(_val(view, "ScaleRatio")))
    if ratio:
        info["scale_ratio"] = [float(x) for x in ratio[:2]]
    ang = _try(lambda: float(_val(view, "Angle")))
    if ang is not None:
        info["angle_deg"] = round(math.degrees(ang), 2)
    pos = _try(lambda: _val(view, "Position"))
    if pos is not None:
        info["position_mm"] = [_mm(pos[0]), _mm(pos[1])]
    o = _try(lambda: _val(view, "GetOutline"))
    if o is not None:
        info["outline_mm"] = [_mm(o[0]), _mm(o[1]), _mm(o[2]), _mm(o[3])]
    info["flat_pattern"] = _try(lambda: bool(_val(view, "IsFlatPatternView")))
    bl = _try(lambda: int(_val(view, "GetBendLineCount")))
    if bl:
        info["bend_lines"] = bl
    br = _try(lambda: int(_val(view, "GetBreakLineCount")))
    if br:
        info["break_lines"] = br
    info["is_section"] = _try(lambda: bool(_val(view, "IsSectionView")))
    info["is_detail"] = _try(lambda: bool(_val(view, "IsDetailView")))
    info["is_flat_pattern_bend_notes"] = None
    info.update(_display_mode(view))
    # dimensions
    dims = []
    for dd in slip._iter_display_dims(view):
        dims.append(_dim_details(dd))
    info["dimensions"] = dims
    # notes attached to / owned by this view
    notes = [_note_details(n) for n in _seq(_try(lambda: _val(view, "GetNotes")))]
    info["notes"] = [n for n in notes if n.get("text") not in (None, "")]
    # weld symbols, centerlines, centermarks
    ws = _seq(_try(lambda: _val(view, "GetWeldSymbols")))
    if ws:
        out_ws = []
        for w in ws:
            rec = {}
            for m in ("GetText", "GetSymbolText", "Text", "GetWeldSymbolText"):
                v = _try(lambda m=m: _val(w, m))
                if isinstance(v, str) and v:
                    rec["text"] = v[:80]
                    break
            for m in ("GetFieldWeld", "GetAllAround", "GetStaggered", "GetIdentificationLine"):
                v = _try(lambda m=m: _val(w, m))
                if isinstance(v, (bool, int)) and v:
                    rec[m[3:].lower()] = True
            try:
                ann = slip._inv(w, "GetAnnotation")
                n = _try(lambda: int(_val(ann, "GetAttachedEntityCount")))
                if n:
                    types = _try(lambda: _seq(_val(ann, "GetAttachedEntityTypes3")))
                    if types:
                        rec["attached"] = [_SEL.get(int(t), str(int(t))) for t in types]
            except Exception:  # noqa: BLE001
                pass
            out_ws.append(rec or "?")
        info["weld_symbols"] = out_ws
    cm = _try(lambda: int(_val(view, "GetCenterMarkCount")))
    if cm:
        info["center_marks"] = cm
    cl = _try(lambda: int(_val(view, "GetCenterLineCount")))
    if cl:
        info["center_lines"] = cl
    tables = [_table_details(t) for t in _seq(_try(lambda: _val(view, "GetTableAnnotations")))]
    if tables:
        info["tables"] = tables
    return {k: v for k, v in info.items() if v is not None and v != [] and v != {}}


def _sheet_details(drawing, sheet_view) -> dict:
    info = {}
    try:
        sheet = _val(drawing, "GetCurrentSheet")
        info["name"] = _try(lambda: str(_val(sheet, "GetName")))
        props = _try(lambda: _seq(_val(sheet, "GetProperties2")))
        if not props:
            props = _try(lambda: _seq(_val(sheet, "GetProperties")))
        if props:
            # [paperSize, templateIn, scale1, scale2, firstAngle, width, height, custom?]
            info["paper_size_code"] = _try(lambda: int(props[0]))
            info["sheet_scale"] = [_try(lambda: float(props[2])), _try(lambda: float(props[3]))]
            info["first_angle"] = _try(lambda: bool(props[4]))
            if len(props) > 6:
                info["size_mm"] = [_mm(props[5], 1), _mm(props[6], 1)]
        info["template"] = _try(lambda: os.path.basename(str(_val(sheet, "GetTemplateName"))))
    except Exception as ex:  # noqa: BLE001
        info["error"] = str(ex)
    return info


def _feature_types_by_view_name(drawing) -> dict:
    """Feature-tree type names (AbsoluteView / UnfoldedView / DetailView / SectionView ...) keyed
    by view name — tells a model view from a projected view."""
    out = {}
    try:
        from tools.annotation import _com_prop_or_method
        feat = drawing.FirstFeature
        while feat is not None:
            tn = _try(lambda: str(feat.GetTypeName2), "")
            if tn == "DrSheet":
                sub = _com_prop_or_method(feat, "GetFirstSubFeature")
                while sub is not None:
                    st = _try(lambda: str(sub.GetTypeName2), "")
                    sn = _try(lambda: str(sub.Name), "")
                    if st and sn and st not in ("DrSheet",):
                        out[sn] = st
                    sub = _com_prop_or_method(sub, "GetNextSubFeature")
            feat = _try(lambda: feat.GetNextFeature)
    except Exception as ex:  # noqa: BLE001
        logger.info("feature walk failed: %s", ex)
    return out


def _inspect_drawing(file_path, include_model_features: bool) -> dict:
    app = SWConnection.get_instance().get_app()
    if file_path:
        import pythoncom
        from win32com.client import VARIANT
        errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warns = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = app.OpenDoc6(file_path, 3, 1, "", errs, warns)  # swDocDRAWING=3, silent
        if doc is None:
            raise SWError(f"could not open {file_path} (errors={errs.value}, warnings={warns.value})")
        _try(lambda: app.ActivateDoc3(os.path.basename(file_path), False, 0,
                                      VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)))
    drawing = slip._active_drawing()
    title = _try(lambda: str(_val(drawing, "GetTitle")))
    path = _try(lambda: str(_val(drawing, "GetPathName")))
    result: dict = {"title": title, "path": path, "sheets": []}
    ftypes = _feature_types_by_view_name(drawing)
    result["view_feature_types"] = ftypes
    # custom properties of the drawing (revision etc.)
    try:
        cpm = drawing.Extension.CustomPropertyManager("")
        names = _seq(_try(lambda: _val(cpm, "GetNames")))
        props = {}
        for n in names[:40]:
            v = _try(lambda n=n: cpm.Get(n))
            if v is not None:
                props[str(n)] = str(v)[:80]
        if props:
            result["drawing_custom_props"] = props
    except Exception:  # noqa: BLE001
        pass
    original_sheet = slip._current_sheet_name(drawing)
    all_views = _seq(slip._inv(drawing, "GetViews"))
    models_seen: dict[str, dict] = {}
    for per_sheet in all_views:
        items = list(per_sheet) if isinstance(per_sheet, tuple) else [per_sheet]
        if not items:
            continue
        sheet_view = items[0]
        sheet_name = _try(lambda: str(_val(sheet_view, "Name")), "?")
        if slip._current_sheet_name(drawing) != sheet_name:
            _try(lambda: drawing.ActivateSheet(sheet_name))
        sheet_info = _sheet_details(drawing, sheet_view)
        sheet_info["name"] = sheet_name
        # sheet-level notes (skip zone letters / numbers)
        snotes = []
        for n in _seq(_try(lambda: _val(sheet_view, "GetNotes"))):
            d = _note_details(n)
            t = (d.get("text") or "").strip()
            if len(t) <= 2 and not d.get("leader"):
                continue
            snotes.append(d)
        sheet_info["notes"] = snotes
        tables = [_table_details(t) for t in _seq(_try(lambda: _val(sheet_view, "GetTableAnnotations")))]
        if tables:
            sheet_info["tables"] = tables
        views = []
        for v in items[1:]:
            vd = _view_details(v, ftypes)
            views.append(vd)
            if include_model_features:
                mp = _try(lambda: str(_val(v, "GetReferencedModelName")))
                if mp and mp not in models_seen:
                    model = _try(lambda: _val(v, "ReferencedDocument"))
                    if model is not None:
                        ms = _model_feature_sample(model)
                        ms["type"] = _try(lambda: int(_val(model, "GetType")))
                        # sheet-metal thickness / bend radius / material props if present
                        try:
                            cpm = model.Extension.CustomPropertyManager("")
                            want = ("MATERIAL", "THICKNESS", "FINISH", "TITLE", "DESCRIPTION", "REVISION",
                                    "BEND_RADIUS", "ENG OF RECORD", "APPROVED BY", "WEIGHT", "LEGACY PN",
                                    "MODULE", "COMPONENT", "VARIANT")
                            props = {}
                            for n in _seq(_try(lambda: _val(cpm, "GetNames")))[:60]:
                                if str(n).upper() in want:
                                    val = _try(lambda n=n: cpm.Get(n))
                                    if val:
                                        props[str(n)] = str(val)[:60]
                            if props:
                                ms["custom_props"] = props
                        except Exception:  # noqa: BLE001
                            pass
                        models_seen[mp] = ms
        sheet_info["views"] = views
        result["sheets"].append(sheet_info)
    if slip._current_sheet_name(drawing) != original_sheet:
        _try(lambda: drawing.ActivateSheet(original_sheet))
    if models_seen:
        result["models"] = {os.path.basename(k): v for k, v in models_seen.items()}
    return result


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def inspect_drawing(file_path: str | None = None, out_path: str | None = None,
                              include_model_features: bool = True, quiet: bool = False) -> str:
        """READ-ONLY inspector for learning drawing conventions. Opens file_path (or uses the
        active drawing) and reports per sheet: paper size / scale / template, every view
        (feature type AbsoluteView=model view vs UnfoldedView=projected, IView.Type, base view,
        referenced model + configuration, orientation, display mode, tangent edges, scale,
        rotation, flat-pattern / break / section flags, bend lines), every display dimension
        (value, type, parenthesis flags, text position, attached entity types), notes (leader,
        balloon), tables (revision / BOM cells), plus the referenced model's feature-type counts
        and title-block custom properties. Nothing is modified or saved. out_path: also write
        the JSON there. quiet: with out_path, return only a one-line summary (saves tokens)."""
        try:
            data = await sw.execute(_inspect_drawing, file_path, include_model_features)
        except SWError as e:
            raise ToolError(str(e))
        written = None
        if out_path:
            try:
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
                written = out_path
            except Exception as ex:  # noqa: BLE001
                data["out_path_error"] = str(ex)
        if quiet and written:
            cp = data.get("drawing_custom_props", {})
            return json.dumps({"written": written, "title": data.get("title"),
                               "sheets": len(data.get("sheets", [])),
                               "views": sum(len(s.get("views", [])) for s in data.get("sheets", [])),
                               "eor": cp.get("ENG OF RECORD"), "approved": cp.get("APPROVED BY"),
                               "rev": cp.get("REVISION"), "type": cp.get("TYPE")})
        return json.dumps(data, ensure_ascii=False, default=str)
