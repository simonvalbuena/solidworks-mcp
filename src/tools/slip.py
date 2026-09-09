"""Slip Robotics additions — view display, dimensions, sheets, sheet notes.

Tools that fill the gaps found while making production drawings to the Slip
standard:

* set_view_display        – HLR + tangent-edge display per view / all views
* list_dimensions / delete_dimension / move_dimension_text
* delete_view
* list_sheets / delete_sheet
* list_notes / set_note_text / replace_in_note / delete_note

COM binding notes (verified live on SolidWorks 2025 SP5, pywin32 late binding,
2026-09-09 — see diag_drawingdoc.py):

* EnsureDispatch fails for SldWorks.Application on this install, so everything is
  late-bound (win32com.client.CDispatch).
* Under late binding, ZERO-ARGUMENT SolidWorks members come back as their VALUE
  when the attribute is read (doc.GetSheetNames -> tuple, doc.GetFirstView -> view).
  Calling that value raises "'tuple' object is not callable" or, for COM objects,
  a misleading "Member not found". Never call zero-arg getters; use _inv().
* Methods WITH arguments (ActivateSheet(name), SetDisplayMode3(...), SelectByID(...))
  work as ordinary attribute calls.
* _inv(obj, name, *args) resolves the DISPID and invokes with METHOD|PROPGET, which
  SolidWorks accepts for both kinds of member; interface results are re-wrapped.

All COM work runs on the SWConnection worker thread like the upstream tools.
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection
from tools.annotation import _get_drawing_views

logger = logging.getLogger(__name__)

# swDisplayMode_e
DISPLAY_MODES = {
    "wireframe": 1,
    "hidden_lines_visible": 2,
    "shaded": 3,
    "shaded_with_edges": 4,
    "hidden_lines_removed": 6,
}

# IView.SetDisplayTangentEdges2 values — verified empirically on SolidWorks 2025 by
# exporting PDFs after each setting (108664 plate, 2026-09-08):
#   0 -> tangent edges REMOVED, 1 -> visible WITH FONT (phantom), 2 -> VISIBLE (solid).
TANGENT_EDGES = {
    "removed": 0,
    "fonted": 1,
    "visible": 2,
}

DISPATCH_METHOD = 0x1
DISPATCH_PROPERTYGET = 0x2
DISPATCH_PROPERTYPUT = 0x4


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def set_view_display(
        view_name: str | None = None,
        display_mode: str = "hidden_lines_removed",
        tangent_edges: str = "removed",
    ) -> str:
        """Set display style on drawing views of the active drawing.
        view_name: one view (e.g. "Drawing View3"); omit for ALL views on the active sheet.
        display_mode: wireframe | hidden_lines_visible | hidden_lines_removed | shaded | shaded_with_edges.
        tangent_edges: removed | fonted | visible  (visible = solid lines)."""
        return await _run(sw, "set_view_display", _set_view_display, view_name, display_mode, tangent_edges)

    @mcp.tool()
    async def list_dimensions(view_name: str | None = None) -> str:
        """List display dimensions on drawing views (name like 'RD1@Drawing View1', value in
        document units, text position in sheet mm). view_name: one view, or omit for all views."""
        return await _run(sw, "list_dimensions", _list_dimensions, view_name)

    @mcp.tool()
    async def delete_dimension(dimension_name: str) -> str:
        """Delete one display dimension by its full name from list_dimensions (e.g. 'RD3@Drawing View1')."""
        return await _run(sw, "delete_dimension", _delete_dimension, dimension_name)

    @mcp.tool()
    async def move_dimension_text(dimension_name: str, x_mm: float, y_mm: float) -> str:
        """Move a display dimension's text to sheet coordinates (mm from the sheet's bottom-left)."""
        return await _run(sw, "move_dimension_text", _move_dimension_text, dimension_name, x_mm, y_mm)

    @mcp.tool()
    async def delete_view(view_name: str) -> str:
        """Delete a drawing view (and its dimensions) by name, e.g. 'Drawing View2'."""
        return await _run(sw, "delete_view", _delete_view, view_name)

    @mcp.tool()
    async def list_sheets() -> str:
        """List the sheets of the active drawing and which one is current."""
        return await _run(sw, "list_sheets", _list_sheets)

    @mcp.tool()
    async def delete_sheet(sheet_name: str) -> str:
        """Delete a sheet from the active drawing (refuses to delete the last sheet).
        Another sheet is activated first so the deleted one is never current."""
        return await _run(sw, "delete_sheet", _delete_sheet, sheet_name)

    @mcp.tool()
    async def list_notes(sheet_name: str | None = None) -> str:
        """List sheet-level notes (notes block, title-block text) of the active drawing.
        Returns name, evaluated text and property-linked text for each note.
        sheet_name: defaults to the current sheet."""
        return await _run(sw, "list_notes", _list_notes, sheet_name)

    @mcp.tool()
    async def set_note_text(note_name: str, text: str, linked: bool = True) -> str:
        """Replace the whole text of a sheet note.
        linked=True writes PropertyLinkedText (keeps $PRP: property links you include);
        linked=False writes plain evaluated text."""
        return await _run(sw, "set_note_text", _set_note_text, note_name, text, linked)

    @mcp.tool()
    async def replace_in_note(note_name: str, find: str, replace: str = "") -> str:
        """Find/replace inside a sheet note's property-linked text (keeps property links intact).
        Use replace="" to delete a fragment or a whole line."""
        return await _run(sw, "replace_in_note", _replace_in_note, note_name, find, replace)

    @mcp.tool()
    async def delete_note(note_name: str) -> str:
        """Delete a sheet note by name (as returned by list_notes)."""
        return await _run(sw, "delete_note", _delete_note, note_name)


async def _run(sw: SWConnection, label: str, fn, *args) -> str:
    try:
        return json.dumps(await sw.execute(fn, *args), ensure_ascii=False, default=str)
    except SWError as e:
        raise ToolError(f"{label} failed: {e}")
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"{label} unexpected error: {type(e).__name__}: {e}")


# --------------------------------------------------------------------------- COM helpers

def _wrap(value):
    """Re-wrap raw PyIDispatch results (and tuples of them) as CDispatch objects."""
    try:
        import win32com.client  # noqa: WPS433
    except Exception:  # noqa: BLE001
        return value
    if isinstance(value, tuple):
        return tuple(_wrap(v) for v in value)
    if type(value).__name__ in ("PyIDispatch", "PyIUnknown"):
        return win32com.client.Dispatch(value)
    return value


def _ole(obj):
    return getattr(obj, "_oleobj_", obj)


def _inv(obj, name: str, *args):
    """Invoke a COM member by name with METHOD|PROPGET; works for zero-arg getters
    that pywin32 late binding otherwise mis-handles."""
    ole = _ole(obj)
    try:
        dispid = ole.GetIDsOfNames(0, name)
    except Exception as e:  # noqa: BLE001
        raise SWError(f"{name}: not found on object ({e})")
    try:
        return _wrap(ole.Invoke(dispid, 0, DISPATCH_METHOD | DISPATCH_PROPERTYGET, True, *args))
    except Exception as e:  # noqa: BLE001
        raise SWError(f"{name}{args!r}: {e}")


def _put(obj, name: str, value) -> None:
    """Property put by name (PropertyLinkedText = ...)."""
    try:
        setattr(obj, name, value)
        return
    except Exception as e:  # noqa: BLE001
        logger.debug("setattr %s failed (%s); trying raw PROPERTYPUT", name, e)
    ole = _ole(obj)
    dispid = ole.GetIDsOfNames(0, name)
    ole.Invoke(dispid, 0, DISPATCH_PROPERTYPUT, False, value)


def _active_drawing():
    app = SWConnection.get_instance().get_app()
    doc = app.ActiveDoc
    if doc is None:
        raise SWError("no active document")
    if int(_inv(doc, "GetType")) != 3:
        raise SWError("active document is not a drawing")
    return doc


def _rebuild(drawing) -> None:
    for name in ("EditRebuild3", "GraphicsRedraw2"):
        try:
            _inv(drawing, name)
        except Exception as e:  # noqa: BLE001
            logger.debug("%s failed: %s", name, e)


def _select(drawing, name: str, sel_type: str) -> bool:
    try:
        drawing.ClearSelection2(True)
    except Exception:  # noqa: BLE001
        pass
    attempts = (
        lambda: drawing.Extension.SelectByID2(name, sel_type, 0, 0, 0, False, 0, None, 0),
        lambda: drawing.SelectByID(name, sel_type, 0, 0, 0),
    )
    for sel in attempts:
        try:
            if bool(sel()):
                return True
        except Exception as e:  # noqa: BLE001
            logger.debug("select %s %r failed: %s", sel_type, name, e)
    return False


def _edit_delete(drawing) -> None:
    try:
        drawing.EditDelete()
    except TypeError:
        _inv(drawing, "EditDelete")


# --------------------------------------------------------------------------- views / display

def _set_view_display(view_name, display_mode: str, tangent_edges: str) -> dict:
    if display_mode not in DISPLAY_MODES:
        raise SWError(f"unknown display_mode {display_mode!r}; use one of {sorted(DISPLAY_MODES)}")
    if tangent_edges not in TANGENT_EDGES:
        raise SWError(f"unknown tangent_edges {tangent_edges!r}; use one of {sorted(TANGENT_EDGES)}")
    drawing = _active_drawing()
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"no drawing views found{' named ' + view_name if view_name else ''}")
    done, errors = [], {}
    for name, view in views:
        try:
            view.SetDisplayMode3(False, DISPLAY_MODES[display_mode], False, False)
        except Exception as e:  # noqa: BLE001
            errors[name] = f"SetDisplayMode3: {e}"
        try:
            view.SetDisplayTangentEdges2(TANGENT_EDGES[tangent_edges])
        except Exception as e:  # noqa: BLE001
            errors[name] = (errors.get(name, "") + f" SetDisplayTangentEdges2: {e}").strip()
        done.append(name)
    _rebuild(drawing)
    return {"status": "done", "views": done, "display_mode": display_mode,
            "tangent_edges": tangent_edges, "errors": errors}


def _delete_view(view_name: str) -> dict:
    drawing = _active_drawing()
    names = [n for n, _ in _get_drawing_views(drawing, None)]
    if view_name not in names:
        raise SWError(f"view {view_name!r} not found; views: {names}")
    if not _select(drawing, view_name, "DRAWINGVIEW"):
        raise SWError(f"could not select view {view_name!r}")
    _edit_delete(drawing)
    remaining = [n for n, _ in _get_drawing_views(drawing, None)]
    if view_name in remaining:
        raise SWError(f"EditDelete did not remove {view_name!r}")
    return {"status": "done", "deleted": view_name, "views": remaining}


# --------------------------------------------------------------------------- dimensions

def _iter_display_dims(view):
    dd = None
    for n in ("GetFirstDisplayDimension5", "GetFirstDisplayDimension4", "GetFirstDisplayDimension3"):
        try:
            dd = _inv(view, n)
            break
        except SWError:
            continue
    guard = 0
    while dd is not None and guard < 500:
        yield dd
        guard += 1
        nxt = None
        for n in ("GetNext5", "GetNext4", "GetNext3"):
            try:
                nxt = _inv(dd, n)
                break
            except SWError:
                continue
        dd = nxt


def _dim_info(dd) -> dict:
    info = {"name": None, "value": None, "text_mm": None}
    try:
        dim = _wrap(dd.GetDimension2(0))
        info["name"] = str(_inv(dim, "FullName"))
        info["value"] = round(float(_inv(dim, "Value")), 4)
    except Exception as e:  # noqa: BLE001
        info["error"] = str(e)
    try:
        ann = _inv(dd, "GetAnnotation")
        pos = _inv(ann, "GetPosition")
        info["text_mm"] = [round(float(pos[0]) * 1000, 2), round(float(pos[1]) * 1000, 2)]
    except Exception:  # noqa: BLE001
        pass
    return info


def _list_dimensions(view_name) -> dict:
    drawing = _active_drawing()
    out = []
    for name, view in _get_drawing_views(drawing, view_name):
        for dd in _iter_display_dims(view):
            d = _dim_info(dd)
            d["view"] = name
            out.append(d)
    return {"count": len(out), "dimensions": out}


def _delete_dimension(dimension_name: str) -> dict:
    drawing = _active_drawing()
    before = {d["name"] for d in _list_dimensions(None)["dimensions"]}
    if dimension_name not in before:
        raise SWError(f"dimension {dimension_name!r} not found; have {sorted(x for x in before if x)}")
    if not _select(drawing, dimension_name, "DIMENSION"):
        raise SWError(f"could not select dimension {dimension_name!r}")
    _edit_delete(drawing)
    after = {d["name"] for d in _list_dimensions(None)["dimensions"]}
    if dimension_name in after:
        raise SWError(f"EditDelete did not remove {dimension_name!r}")
    return {"status": "done", "deleted": dimension_name, "remaining": sorted(x for x in after if x)}


def _move_dimension_text(dimension_name: str, x_mm: float, y_mm: float) -> dict:
    drawing = _active_drawing()
    for _name, view in _get_drawing_views(drawing, None):
        for dd in _iter_display_dims(view):
            info = _dim_info(dd)
            if info.get("name") == dimension_name:
                ann = _inv(dd, "GetAnnotation")
                ok = ann.SetPosition(x_mm / 1000.0, y_mm / 1000.0, 0)
                _rebuild(drawing)
                return {"status": "done", "dimension": dimension_name, "set_position_ok": bool(ok),
                        "after": _dim_info(dd)}
    raise SWError(f"dimension {dimension_name!r} not found")


# --------------------------------------------------------------------------- sheets

def _sheet_names(drawing) -> list[str]:
    return [str(n) for n in (_inv(drawing, "GetSheetNames") or ())]


def _current_sheet_name(drawing) -> str:
    return str(_inv(_inv(drawing, "GetCurrentSheet"), "GetName"))


def _list_sheets() -> dict:
    drawing = _active_drawing()
    return {"sheets": _sheet_names(drawing), "current": _current_sheet_name(drawing)}


def _delete_sheet(sheet_name: str) -> dict:
    drawing = _active_drawing()
    names = _sheet_names(drawing)
    if sheet_name not in names:
        raise SWError(f"sheet {sheet_name!r} not found; sheets: {names}")
    if len(names) < 2:
        raise SWError("refusing to delete the only sheet")
    keep = next(n for n in names if n != sheet_name)
    if _current_sheet_name(drawing) == sheet_name:
        if not drawing.ActivateSheet(keep):
            raise SWError(f"could not activate sheet {keep!r}")
    tried = []
    for how, action in (
        ("Extension.DeleteSelection2(0)", lambda: drawing.Extension.DeleteSelection2(0)),
        ("Extension.DeleteSelection2(2)", lambda: drawing.Extension.DeleteSelection2(2)),  # swDelete_Children
        ("EditDelete", lambda: _edit_delete(drawing)),
    ):
        if not _select(drawing, sheet_name, "SHEET"):
            raise SWError(f"could not select sheet {sheet_name!r}")
        try:
            rc = action()
        except Exception as e:  # noqa: BLE001
            rc = f"error: {e}"
        tried.append(f"{how} -> {rc!r}")
        if sheet_name not in _sheet_names(drawing):
            return {"status": "done", "deleted": sheet_name, "method": how,
                    "sheets": _sheet_names(drawing), "current": _current_sheet_name(drawing)}
    raise SWError(f"sheet {sheet_name!r} still present after: " + "; ".join(tried)
                  + " (if SolidWorks showed a 'confirm delete' dialog, that is the blocker)")


# --------------------------------------------------------------------------- notes

def _sheet_view(drawing):
    """The current sheet's own IView (parent of the drawing views); it owns sheet notes.
    GetViews returns one tuple per sheet whose first element is that sheet's view."""
    current = _current_sheet_name(drawing)
    all_views = _inv(drawing, "GetViews") or ()
    for per_sheet in all_views:
        items = tuple(per_sheet) if isinstance(per_sheet, tuple) else (per_sheet,)
        if not items:
            continue
        try:
            if str(_inv(items[0], "Name")) == current:
                return items[0]
        except Exception:  # noqa: BLE001
            continue
    first = _inv(drawing, "GetFirstView")
    if first is not None:
        return first
    raise SWError("cannot access the sheet view")


def _notes_on_sheet(drawing) -> list:
    notes = _inv(_sheet_view(drawing), "GetNotes")
    if notes is None:
        return []
    return list(notes) if isinstance(notes, tuple) else [notes]


def _note_info(note) -> dict:
    info = {"name": None, "text": None, "linked_text": None}
    for key, member in (("name", "GetName"), ("text", "GetText"), ("linked_text", "PropertyLinkedText")):
        try:
            info[key] = str(_inv(note, member))
        except Exception:  # noqa: BLE001
            pass
    return info


def _find_note(drawing, note_name: str):
    for note in _notes_on_sheet(drawing):
        try:
            if str(_inv(note, "GetName")) == note_name:
                return note
        except Exception:  # noqa: BLE001
            continue
    raise SWError(f"note not found on current sheet: {note_name}")


def _list_notes(sheet_name) -> dict:
    drawing = _active_drawing()
    if sheet_name and _current_sheet_name(drawing) != sheet_name:
        if not drawing.ActivateSheet(sheet_name):
            raise SWError(f"could not activate sheet {sheet_name!r}")
    notes = [_note_info(n) for n in _notes_on_sheet(drawing)]
    return {"sheet": _current_sheet_name(drawing), "count": len(notes), "notes": notes}


def _set_note_text(note_name: str, text: str, linked: bool) -> dict:
    drawing = _active_drawing()
    note = _find_note(drawing, note_name)
    before = _note_info(note)
    if linked:
        _put(note, "PropertyLinkedText", text)
    else:
        if not note.SetText(text):
            raise SWError("SetText returned False")
    _rebuild(drawing)
    return {"status": "done", "note": note_name, "before": before, "after": _note_info(note)}


def _replace_in_note(note_name: str, find: str, replace: str) -> dict:
    drawing = _active_drawing()
    note = _find_note(drawing, note_name)
    before = _note_info(note)
    src = before.get("linked_text") or before.get("text") or ""
    if find not in src:
        raise SWError(f"fragment not found in note {note_name!r}: {find!r}")
    _put(note, "PropertyLinkedText", src.replace(find, replace))
    _rebuild(drawing)
    return {"status": "done", "note": note_name, "occurrences": src.count(find),
            "before": before, "after": _note_info(note)}


def _delete_note(note_name: str) -> dict:
    drawing = _active_drawing()
    _find_note(drawing, note_name)
    if not _select(drawing, note_name, "NOTE"):
        raise SWError(f"could not select note {note_name!r}")
    _edit_delete(drawing)
    remaining = [_note_info(n)["name"] for n in _notes_on_sheet(drawing)]
    if note_name in remaining:
        raise SWError(f"EditDelete did not remove {note_name!r}")
    return {"status": "done", "deleted": note_name, "remaining_notes": remaining}
