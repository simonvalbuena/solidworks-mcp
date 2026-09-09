"""Slip Robotics tube-drawing tools: broken (break) views, view scale/position.

Reference: CC-M240711-11-004 (BEAM, LONG CENTER) — one long side view with a zig-zag
break near one end, the overall length dimension carrying the break symbol, an end view
with the section size in parentheses, one hole located from the end and from the bottom.

Break API (SolidWorks 2012+):
    IBreakLine = IView.InsertBreak(orientation, pos1_m, pos2_m, style)   # metres FROM THE VIEW CENTRE
    IDrawingDoc.BreakView()                                              # breaks the SELECTED view
    gap: document property swUserPreferenceDoubleValue_e.swDetailingBreakLineGap (0.125" = 0.003175 m)
    NOTE: after a break, annotation positions and ModelToViewTransform stay in UNBROKEN space —
    see slip._record_break_shift / _to_unbroken; view_geometry + add_dimensions handle it.
    IView.GetBreakLines / IView.UnBreakView

Enum values are read from swconst.tlb next to SLDWORKS.exe when possible so no numeric
value is hard-coded blindly; the response reports which source was used.
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
from tools.annotation import _get_drawing_views

logger = logging.getLogger(__name__)

_M_TO_MM = 1000.0
_IN = 25.4

# Fallbacks if swconst.tlb cannot be read (values as documented in the SOLIDWORKS API help).
_FALLBACK_ENUMS = {
    "swBreakLineOrientation_e": {"swBreakLineHorizontal": 1, "swBreakLineVertical": 2},
    "swBreakLineStyle_e": {"swBreakLine_Straight": 1, "swBreakLine_ZigZag": 2,
                           "swBreakLine_Curve": 3, "swBreakLine_SmallZigZag": 4,
                           "swBreakLine_Jagged": 5},   # verified from swconst.tlb, SW 2025
}
_ORIENT = {"vertical": "swBreakLineVertical", "horizontal": "swBreakLineHorizontal"}
_STYLE = {"straight": "swBreakLine_Straight", "curve": "swBreakLine_Curve",
          "zigzag": "swBreakLine_ZigZag", "small_zigzag": "swBreakLine_SmallZigZag",
          "jagged": "swBreakLine_Jagged"}

_ENUM_CACHE: dict[str, dict[str, int]] = {}
_ENUM_SOURCE = {"source": "unresolved"}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def break_view(
        view_name: str,
        pos1_mm: float,
        pos2_mm: float,
        orientation: str = "vertical",
        gap_in: float = 0.125,
        style: str = "zigzag",
    ) -> str:
        """Insert a break in a drawing view and break it (Slip long-tube convention:
        vertical zig-zag break lines, 0.125" gap). pos1_mm/pos2_mm are sheet-mm offsets
        FROM THE VIEW CENTRE (IView.Position) of the two break lines — X positions for orientation="vertical" (removes
        length), Y positions for "horizontal". The removed span is what lies between them.
        Choose them from view_geometry so no dimensioned feature is removed; an overall
        length dimension across the break automatically shows the break symbol.
        Returns break-line positions, gap, style and the view outline after breaking."""
        return await _run(sw, "break_view", _break_view, view_name, pos1_mm, pos2_mm,
                          orientation, gap_in, style)

    @mcp.tool()
    async def set_break_gap(gap_in: float = 0.125) -> str:
        """Set the drawing's break-line gap (document detailing property, applies to all broken
        views). Slip standard: 0.125 in."""
        return await _run(sw, "set_break_gap", _set_break_gap_tool, gap_in)

    @mcp.tool()
    async def unbreak_view(view_name: str) -> str:
        """Remove the break(s) from a drawing view (IView.UnBreakView) and delete its break lines."""
        return await _run(sw, "unbreak_view", _unbreak_view, view_name)

    @mcp.tool()
    async def set_view_scale(view_name: str, scale: float) -> str:
        """Set a drawing view's scale as a decimal (0.25 = 1:4, 0.125 = 1:8). Returns the new
        outline in sheet mm so the layout can be re-centred with set_view_position."""
        return await _run(sw, "set_view_scale", _set_view_scale, view_name, scale)

    @mcp.tool()
    async def set_view_position(view_name: str, x_mm: float, y_mm: float) -> str:
        """Move a drawing view so its centre (IView.Position) is at sheet (x_mm, y_mm)."""
        return await _run(sw, "set_view_position", _set_view_position, view_name, x_mm, y_mm)

    @mcp.tool()
    async def sw_enum(enum_name: str) -> str:
        """Read a swconst enum (e.g. 'swBreakLineStyle_e') from the installed swconst.tlb;
        reports the values and whether they came from the type library or the fallback table."""
        return await _run(sw, "sw_enum", _enum_report, enum_name)


async def _run(sw, tool_name, fn, *args):
    try:
        return json.dumps(await sw.execute(fn, *args), ensure_ascii=False)
    except SWError as e:
        raise ToolError(f"{tool_name} failed: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("%s crashed", tool_name)
        raise ToolError(f"{tool_name} failed: {type(e).__name__}: {e}") from e


# --------------------------------------------------------------------------- enums from typelib

def _swconst_paths(app) -> list[str]:
    cands = []
    try:
        exe = str(slip._inv(app, "GetExecutablePath"))
        if exe:
            cands.append(os.path.join(os.path.dirname(exe), "swconst.tlb"))
    except Exception:  # noqa: BLE001
        pass
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
        base = os.path.join(root, "SOLIDWORKS Corp")
        if os.path.isdir(base):
            for d in os.listdir(base):
                cands.append(os.path.join(base, d, "swconst.tlb"))
    return [p for p in cands if os.path.isfile(p)]


def _load_enums(app) -> None:
    if _ENUM_CACHE:
        return
    try:
        import pythoncom
        for path in _swconst_paths(app):
            tlb = pythoncom.LoadTypeLib(path)
            for i in range(tlb.GetTypeInfoCount()):
                ti = tlb.GetTypeInfo(i)
                attr = ti.GetTypeAttr()
                if attr.typekind != pythoncom.TKIND_ENUM:
                    continue
                ename = tlb.GetDocumentation(i)[0]
                members = {}
                for j in range(attr.cVars):
                    vd = ti.GetVarDesc(j)
                    members[ti.GetNames(vd.memid)[0]] = int(vd.value)
                _ENUM_CACHE[ename] = members
            if _ENUM_CACHE:
                _ENUM_SOURCE["source"] = f"typelib:{path}"
                return
    except Exception as e:  # noqa: BLE001
        logger.warning("swconst.tlb enum load failed: %s", e)
    _ENUM_CACHE.update(_FALLBACK_ENUMS)
    _ENUM_SOURCE["source"] = "fallback table"


def _enum(app, enum_name: str, member: str) -> int:
    _load_enums(app)
    table = _ENUM_CACHE.get(enum_name) or _FALLBACK_ENUMS.get(enum_name, {})
    if member not in table:
        raise SWError(f"{enum_name}.{member} unknown (source {_ENUM_SOURCE['source']})")
    return table[member]


def _enum_report(enum_name: str) -> dict:
    app = SWConnection.get_instance().get_app()
    _load_enums(app)
    return {"status": "done", "enum": enum_name, "source": _ENUM_SOURCE["source"],
            "values": _ENUM_CACHE.get(enum_name) or _FALLBACK_ENUMS.get(enum_name)}


# --------------------------------------------------------------------------- helpers

def _set_break_gap(app, drawing, gap_in: float):
    """Break-line gap is a DOCUMENT detailing property (Document Properties > Detailing >
    Break lines > Gap): swUserPreferenceDoubleValue_e.swDetailingBreakLineGap. Applies to
    every broken view of the drawing. Returns the value read back (inches) or an error string."""
    _load_enums(app)
    table = _ENUM_CACHE.get("swUserPreferenceDoubleValue_e", {})
    key = None
    for name in table:
        if "breakline" in name.lower() and "gap" in name.lower():
            key = name
            break
    if key is None:
        return "swDetailingBreakLineGap not found in swconst.tlb"
    val = table[key]
    gap_m = gap_in * _IN / _M_TO_MM
    ext = drawing.Extension
    try:
        ok = ext.SetUserPreferenceDouble(val, 0, gap_m)
    except Exception as e:  # noqa: BLE001
        return f"SetUserPreferenceDouble failed: {e}"
    try:
        back = float(ext.GetUserPreferenceDouble(val, 0))
        return {"enum": key, "value": val, "set_ok": bool(ok), "gap_in": round(back * _M_TO_MM / _IN, 4)}
    except Exception as e:  # noqa: BLE001
        return {"enum": key, "value": val, "set_ok": bool(ok), "readback_error": str(e)}


def _view(drawing, view_name):
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"view {view_name!r} not found")
    return views[0]


def _outline_mm(view_obj):
    try:
        o = view_obj.GetOutline
        return [round(float(v) * _M_TO_MM, 3) for v in o]
    except Exception:  # noqa: BLE001
        try:
            return [round(float(v) * _M_TO_MM, 3) for v in slip._inv(view_obj, "GetOutline")]
        except Exception:  # noqa: BLE001
            return None


def _select_view(drawing, name: str) -> bool:
    try:
        drawing.ActivateView(name)
    except Exception:  # noqa: BLE001
        pass
    return slip._select(drawing, name, "DRAWINGVIEW")


def _break_lines(view_obj) -> list:
    for getter in (lambda: view_obj.GetBreakLines, lambda: slip._inv(view_obj, "GetBreakLines")):
        try:
            bl = getter()
            if bl is None:
                return []
            return [slip._wrap(b) for b in bl]
        except Exception:  # noqa: BLE001
            continue
    return []


def _describe_break_line(bl) -> dict:
    d: dict = {}
    for key in ("Style", "Orientation", "Gap"):
        try:
            d[key.lower()] = getattr(bl, key)
        except Exception:  # noqa: BLE001
            try:
                d[key.lower()] = slip._inv(bl, key)
            except Exception:  # noqa: BLE001
                pass
    if "gap" in d and d["gap"] is not None:
        d["gap_in"] = round(float(d["gap"]) * _M_TO_MM / _IN, 4)
    try:
        pos = bl.GetPosition
        if callable(pos):
            pos = pos()
        d["pos_mm"] = [round(float(v) * _M_TO_MM, 2) for v in pos]
    except Exception:  # noqa: BLE001
        try:
            d["pos_mm"] = [round(float(v) * _M_TO_MM, 2) for v in slip._inv(bl, "GetPosition")]
        except Exception:  # noqa: BLE001
            pass
    return d


# --------------------------------------------------------------------------- tools

def _break_view(view_name, pos1_mm, pos2_mm, orientation, gap_in, style) -> dict:
    app = SWConnection.get_instance().get_app()
    drawing = slip._active_drawing()
    name, view_obj = _view(drawing, view_name)
    if orientation not in _ORIENT:
        raise SWError(f"orientation must be one of {sorted(_ORIENT)}")
    if style not in _STYLE:
        raise SWError(f"style must be one of {sorted(_STYLE)}")
    o_val = _enum(app, "swBreakLineOrientation_e", _ORIENT[orientation])
    s_val = _enum(app, "swBreakLineStyle_e", _STYLE[style])
    p1, p2 = sorted((float(pos1_mm), float(pos2_mm)))
    before = _outline_mm(view_obj)
    # Set the document gap BEFORE the break: a view broken under the old gap kept it on 108538
    # even after the property changed (Simon had to fix two views by hand).
    gap_pre = _set_break_gap(app, drawing, float(gap_in))

    _select_view(drawing, name)
    bl = None
    err = None
    for call in (lambda: view_obj.InsertBreak(o_val, p1 / _M_TO_MM, p2 / _M_TO_MM, s_val),
                 lambda: slip._inv(view_obj, "InsertBreak", o_val, p1 / _M_TO_MM, p2 / _M_TO_MM, s_val)):
        try:
            bl = slip._wrap(call())
            if bl is not None:
                break
        except Exception as e:  # noqa: BLE001
            err = e
    if bl is None:
        raise SWError(f"InsertBreak failed ({err})")

    # break the (selected) view
    _select_view(drawing, name)
    broke = None
    for call in (lambda: drawing.BreakView(), lambda: slip._inv(drawing, "BreakView")):
        try:
            broke = call()
            break
        except Exception as e:  # noqa: BLE001
            err = e
    gap_set = {"before_break": gap_pre, "after_break": _set_break_gap(app, drawing, float(gap_in))}
    slip._rebuild(drawing)
    lines = [_describe_break_line(b) for b in _break_lines(view_obj)]
    return {"status": "done", "view": name, "orientation": orientation, "style": style,
            "enum_source": _ENUM_SOURCE["source"], "enum_values": {"orientation": o_val, "style": s_val},
            "break_view_result": broke, "gap_in_requested": gap_in, "gap_set": gap_set,
            "break_lines": lines, "outline_before_mm": before, "outline_after_mm": _outline_mm(view_obj)}


def _set_break_gap_tool(gap_in: float) -> dict:
    app = SWConnection.get_instance().get_app()
    drawing = slip._active_drawing()
    res = _set_break_gap(app, drawing, float(gap_in))
    slip._rebuild(drawing)
    return {"status": "done", "gap": res}


def _unbreak_view(view_name) -> dict:
    drawing = slip._active_drawing()
    name, view_obj = _view(drawing, view_name)
    _select_view(drawing, name)
    res = None
    for call in (lambda: view_obj.UnBreakView(), lambda: slip._inv(view_obj, "UnBreakView"),
                 lambda: drawing.UnBreakView(), lambda: slip._inv(drawing, "UnBreakView")):
        try:
            res = call()
            break
        except Exception as e:  # noqa: BLE001
            res = f"error: {e}"
    # delete remaining break lines (select each and delete)
    deleted = 0
    for bl in _break_lines(view_obj):
        try:
            if bool(bl.Select(False)):
                drawing.EditDelete()
                deleted += 1
        except Exception:  # noqa: BLE001
            pass
    slip._rebuild(drawing)
    return {"status": "done", "view": name, "unbreak_result": res, "break_lines_deleted": deleted,
            "outline_mm": _outline_mm(view_obj)}


def _set_view_scale(view_name, scale) -> dict:
    drawing = slip._active_drawing()
    name, view_obj = _view(drawing, view_name)
    try:
        view_obj.ScaleDecimal = float(scale)
    except Exception:  # noqa: BLE001
        slip._put(view_obj, "ScaleDecimal", float(scale))
    slip._rebuild(drawing)
    try:
        got = float(view_obj.ScaleDecimal)
    except Exception:  # noqa: BLE001
        got = float(slip._inv(view_obj, "ScaleDecimal"))
    return {"status": "done", "view": name, "scale": got, "outline_mm": _outline_mm(view_obj)}


def _set_view_position(view_name, x_mm, y_mm) -> dict:
    drawing = slip._active_drawing()
    name, view_obj = _view(drawing, view_name)
    import pythoncom
    import win32com.client
    pos = [float(x_mm) / _M_TO_MM, float(y_mm) / _M_TO_MM]
    variant = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, pos)
    # a plain Python list marshals as an array of VARIANTs, which IView.Position mis-reads
    # (observed: [0.38, 0.445] -> view landed at (0, 0.38)); force a double SAFEARRAY.
    ole = slip._ole(view_obj)
    dispid = ole.GetIDsOfNames(0, "Position")
    ole.Invoke(dispid, 0, slip.DISPATCH_PROPERTYPUT, False, variant)
    slip._rebuild(drawing)
    try:
        got = [round(float(v) * _M_TO_MM, 2) for v in slip._inv(view_obj, "Position")]
    except Exception:  # noqa: BLE001
        got = None
    if got and (abs(got[0] - float(x_mm)) > 0.5 or abs(got[1] - float(y_mm)) > 0.5):
        raise SWError(f"Position put landed at {got}, wanted [{x_mm}, {y_mm}]")
    return {"status": "done", "view": name, "position_mm": got, "outline_mm": _outline_mm(view_obj)}
