"""Slip Robotics additions — view display, sheet management, sheet notes.

Small, deliberately conservative tools that fill the gaps found while making
production drawings to the Slip standard:

* set_view_display   – Hidden Lines Removed + tangent edges removed on drawing views
* list_sheets / delete_sheet – drop unused template sheets
* list_notes / set_note_text / replace_in_note / delete_note – edit the notes block

All COM work runs on the SWConnection worker thread like the upstream tools.
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection
from tools.annotation import _com_get, _get_drawing_views, _extract_notes

logger = logging.getLogger(__name__)

# swDisplayMode_e
DISPLAY_MODES = {
    "wireframe": 1,
    "hidden_lines_visible": 2,
    "shaded": 3,
    "shaded_with_edges": 4,
    "hidden_lines_removed": 6,
}

# swTangentEdgeDisplay_e
TANGENT_EDGES = {
    "visible": 0,
    "fonted": 1,
    "removed": 2,
}


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
        tangent_edges: visible | fonted | removed."""
        try:
            result = await sw.execute(_set_view_display, view_name, display_mode, tangent_edges)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"set_view_display failed: {e}")

    @mcp.tool()
    async def list_sheets() -> str:
        """List the sheets of the active drawing and which one is current."""
        try:
            return json.dumps(await sw.execute(_list_sheets), ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"list_sheets failed: {e}")

    @mcp.tool()
    async def delete_sheet(sheet_name: str) -> str:
        """Delete a sheet from the active drawing (refuses to delete the last sheet).
        Another sheet is activated first so the deleted one is never current."""
        try:
            return json.dumps(await sw.execute(_delete_sheet, sheet_name), ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"delete_sheet failed: {e}")

    @mcp.tool()
    async def list_notes(sheet_name: str | None = None) -> str:
        """List sheet-level notes (title block/notes block text) of the active drawing.
        Returns name, evaluated text and property-linked text for each note.
        sheet_name: defaults to the current sheet."""
        try:
            return json.dumps(await sw.execute(_list_notes, sheet_name), ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"list_notes failed: {e}")

    @mcp.tool()
    async def set_note_text(note_name: str, text: str, linked: bool = True) -> str:
        """Replace the whole text of a sheet note.
        linked=True writes PropertyLinkedText (keeps $PRP: property links you include);
        linked=False writes plain evaluated text."""
        try:
            return json.dumps(await sw.execute(_set_note_text, note_name, text, linked), ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"set_note_text failed: {e}")

    @mcp.tool()
    async def replace_in_note(note_name: str, find: str, replace: str = "") -> str:
        """Find/replace inside a sheet note's property-linked text (keeps property links intact).
        Use replace="" to delete a fragment or a whole line."""
        try:
            return json.dumps(await sw.execute(_replace_in_note, note_name, find, replace), ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"replace_in_note failed: {e}")

    @mcp.tool()
    async def delete_note(note_name: str) -> str:
        """Delete a sheet note by name (as returned by list_notes)."""
        try:
            return json.dumps(await sw.execute(_delete_note, note_name), ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"delete_note failed: {e}")


# --------------------------------------------------------------------------- helpers

def _active_drawing():
    app = SWConnection.get_instance().get_app()
    doc = app.ActiveDoc
    if doc is None:
        raise SWError("no active document")
    if _com_get(doc, "GetType") != 3:
        raise SWError("active document is not a drawing")
    return doc


def _call_first(obj, names: list[str], *args):
    """Call the first COM member that exists/works from a list of candidate names."""
    last = None
    for n in names:
        try:
            attr = getattr(obj, n)
        except AttributeError as e:
            last = e
            continue
        try:
            return attr(*args) if callable(attr) else attr
        except Exception as e:  # noqa: BLE001
            last = e
    raise SWError(f"none of {names} worked: {last}")


def _sheet_names(drawing) -> list[str]:
    names = _com_get(drawing, "GetSheetNames")
    return [str(n) for n in (names or [])]


def _current_sheet_name(drawing) -> str:
    sheet = _com_get(drawing, "GetCurrentSheet")
    return str(_com_get(sheet, "GetName"))


def _sheet_view(drawing):
    """The sheet's own IView (parent of all drawing views); it owns sheet-level notes."""
    for name in ("GetFirstView", "IGetFirstView"):
        try:
            v = getattr(drawing, name)
            v = v() if callable(v) else v
            if v is not None:
                return v
        except Exception as e:  # noqa: BLE001
            logger.debug("%s failed: %s", name, e)
    raise SWError("cannot access the sheet view (GetFirstView)")


def _notes_on_sheet(drawing) -> list:
    return _extract_notes(_com_get(_sheet_view(drawing), "GetNotes"))


def _note_info(note) -> dict:
    info = {"name": None, "text": None, "linked_text": None}
    try:
        info["name"] = str(_com_get(note, "GetName"))
    except Exception:  # noqa: BLE001
        pass
    try:
        info["text"] = str(_com_get(note, "GetText"))
    except Exception:  # noqa: BLE001
        pass
    try:
        info["linked_text"] = str(note.PropertyLinkedText)
    except Exception:  # noqa: BLE001
        pass
    return info


def _find_note(drawing, note_name: str):
    for note in _notes_on_sheet(drawing):
        try:
            if str(_com_get(note, "GetName")) == note_name:
                return note
        except Exception:  # noqa: BLE001
            continue
    raise SWError(f"note not found on current sheet: {note_name}")


def _rebuild(drawing) -> None:
    try:
        drawing.EditRebuild3()
    except Exception:  # noqa: BLE001
        try:
            drawing.ForceRebuild3(False)
        except Exception as e:  # noqa: BLE001
            logger.debug("rebuild failed: %s", e)


# --------------------------------------------------------------------------- views

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
            _call_first(view, ["SetDisplayTangentEdges2", "SetDisplayTangentEdges"], TANGENT_EDGES[tangent_edges])
        except Exception as e:  # noqa: BLE001
            errors[name] = (errors.get(name, "") + f" SetDisplayTangentEdges2: {e}").strip()
        done.append(name)
    _rebuild(drawing)
    drawing.GraphicsRedraw2()
    return {"status": "done", "views": done, "display_mode": display_mode,
            "tangent_edges": tangent_edges, "errors": errors}


# --------------------------------------------------------------------------- sheets

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
    drawing.ClearSelection2(True)
    ok = False
    for selector in (
        lambda: drawing.Extension.SelectByID2(sheet_name, "SHEET", 0, 0, 0, False, 0, None, 0),
        lambda: drawing.SelectByID(sheet_name, "SHEET", 0, 0, 0),
    ):
        try:
            ok = bool(selector())
        except Exception as e:  # noqa: BLE001
            logger.debug("sheet select failed: %s", e)
        if ok:
            break
    if not ok:
        raise SWError(f"could not select sheet {sheet_name!r}")
    drawing.EditDelete()
    remaining = _sheet_names(drawing)
    if sheet_name in remaining:
        raise SWError(f"EditDelete did not remove {sheet_name!r}; sheets now {remaining}")
    return {"status": "done", "deleted": sheet_name, "sheets": remaining,
            "current": _current_sheet_name(drawing)}


# --------------------------------------------------------------------------- notes

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
        note.PropertyLinkedText = text
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
    new = src.replace(find, replace)
    note.PropertyLinkedText = new
    _rebuild(drawing)
    return {"status": "done", "note": note_name, "occurrences": src.count(find),
            "before": before, "after": _note_info(note)}


def _delete_note(note_name: str) -> dict:
    drawing = _active_drawing()
    _find_note(drawing, note_name)  # existence check
    drawing.ClearSelection2(True)
    ok = False
    for selector in (
        lambda: drawing.Extension.SelectByID2(note_name, "NOTE", 0, 0, 0, False, 0, None, 0),
        lambda: drawing.SelectByID(note_name, "NOTE", 0, 0, 0),
    ):
        try:
            ok = bool(selector())
        except Exception as e:  # noqa: BLE001
            logger.debug("note select failed: %s", e)
        if ok:
            break
    if not ok:
        raise SWError(f"could not select note {note_name!r}")
    drawing.EditDelete()
    remaining = [n["name"] for n in (_note_info(x) for x in _notes_on_sheet(drawing))]
    return {"status": "done", "deleted": note_name, "remaining_notes": remaining}
