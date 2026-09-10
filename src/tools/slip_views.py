"""Slip Robotics view tools (2026-09-09, after the first five-drawing batch):

* project_view          - a PROJECTED (parent-linked) view from an existing view, like
                          Insert > Drawing View > Projected. Children stay aligned to the
                          parent. Use it for the top/edge/profile views of plates and bent
                          parts; keep model views only for the iso and for tube face stacks
                          that are rotated/rescaled individually.
* normal_to_face_view   - Simón's "FACE A" convention for features on an inclined face:
                          in the PART, select the planar face whose outward normal matches
                          the given vector, ShowNamedView2("*Normal To"), then insert that
                          current model view into the drawing ("*Current" model view) so the
                          face is seen normally and one feature can be dimensioned true-size.
* insert_note           - a free note (optionally with a leader attached to a view edge),
                          e.g. "FACE A" pointing at the face and "FACE A - NORMAL VIEW" as
                          the view label.
* flat_bend_lines       - the bend lines of a flat-pattern view in sheet mm.
* add_bend_dimensions   - edge-to-bend-line dimensions on a flat pattern (Slip flat views
                          carry ONLY overall + edge-to-bend-line dims).
"""
from __future__ import annotations

import json
import logging
import math

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection
from tools import slip
from tools.annotation import _get_drawing_views, _get_view_edges

logger = logging.getLogger(__name__)

_M_TO_MM = 1000.0
_IN = 25.4

# swUnfoldedViewDirection_e (SOLIDWORKS API help)
_DIRS = {"left": 1, "right": 2, "top": 3, "bottom": 4}
from tools.annotation import (SW_INPUT_DIM_VAL_ON_CREATE, SW_UNIT_MM, SW_FRACTION_DECIMAL,
                              SW_PRECISION_UNCHANGED, DIM_PRECISION)


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def project_view(parent_view: str, direction: str, x_mm: float, y_mm: float) -> str:
        """Insert a PROJECTED view (parent-linked, stays aligned) from parent_view in
        direction left|right|top|bottom, centred at sheet (x_mm, y_mm). Third-angle: 'top'
        goes above the parent, 'right' to its right. Returns the new view name and outline.
        Slip default for plate/bent-part edge and plan views (not for the iso, not for tube
        face stacks that are rotated/rescaled individually)."""
        return await _run(sw, "project_view", _project_view, parent_view, direction, x_mm, y_mm)

    @mcp.tool()
    async def normal_to_face_view(
        model_path: str,
        normal: list[float],
        x_mm: float,
        y_mm: float,
        scale: float | None = None,
        label: str | None = "FACE A - NORMAL VIEW",
        label_offset_mm: float = 18.0,
    ) -> str:
        """Simón's FACE A convention: in the part (model_path, must be open) select the planar
        face whose outward normal is closest to `normal` (model coordinates, e.g. [0,0.707,0.707]
        for a 45° flange), look normal to it (*Normal To), and insert that current model view
        into the active drawing centred at (x_mm, y_mm) with `scale` (decimal; None = sheet
        scale). Adds the label note under the view (label=None to skip). Returns the view name,
        the face that was used and the outline. Then dimension ONE feature on that face with
        view_geometry/add_dimensions, and put a leader note "FACE A" on the face in an
        orthographic view with insert_note."""
        return await _run(sw, "normal_to_face_view", _normal_to_face_view, model_path, normal,
                          x_mm, y_mm, scale, label, label_offset_mm)

    @mcp.tool()
    async def insert_note(
        text: str,
        x_mm: float,
        y_mm: float,
        view_name: str | None = None,
        leader_edge: int | None = None,
        font_height_mm: float | None = None,
    ) -> str:
        """Insert a free note at sheet (x_mm, y_mm). With view_name + leader_edge (index from
        view_geometry) the note is attached to that edge with a leader — e.g. "FACE A"
        pointing at the inclined face. Returns the note name."""
        return await _run(sw, "insert_note", _insert_note, text, x_mm, y_mm, view_name,
                          leader_edge, font_height_mm)

    @mcp.tool()
    async def flat_bend_lines(view_name: str) -> str:
        """Bend lines of a flat-pattern view: index, sheet-mm start/end/mid, orientation
        (horizontal|vertical) and the bend note text when available. Feed the index to
        add_bend_dimensions."""
        return await _run(sw, "flat_bend_lines", _flat_bend_lines, view_name)

    @mcp.tool()
    async def add_bend_dimensions(view_name: str, dims: list[dict]) -> str:
        """Edge-to-bend-line dimensions on a flat-pattern view (Slip flat views carry only the
        overall flat size and these). Each dim: {"e_ref": <edge index from view_geometry>,
        "bend": <index from flat_bend_lines>, "text": [x_mm, y_mm]}. 3-place, document units."""
        return await _run(sw, "add_bend_dimensions", _add_bend_dimensions, view_name, dims)


async def _run(sw, tool_name, fn, *args):
    try:
        return json.dumps(await sw.execute(fn, *args), ensure_ascii=False)
    except SWError as e:
        raise ToolError(f"{tool_name} failed: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("%s crashed", tool_name)
        raise ToolError(f"{tool_name} failed: {type(e).__name__}: {e}") from e


# --------------------------------------------------------------------------- helpers

def _view(drawing, name):
    views = _get_drawing_views(drawing, name)
    if not views:
        raise SWError(f"view not found: {name}")
    return views[0]


def _view_info(view_obj) -> dict:
    out = {}
    try:
        out["name"] = view_obj.Name
    except Exception:  # noqa: BLE001
        pass
    try:
        o = view_obj.GetOutline
        out["outline_mm"] = [round(float(v) * _M_TO_MM, 3) for v in o]
    except Exception:  # noqa: BLE001
        pass
    try:
        p = view_obj.Position
        out["position_mm"] = [round(float(p[0]) * _M_TO_MM, 2), round(float(p[1]) * _M_TO_MM, 2)]
    except Exception:  # noqa: BLE001
        pass
    try:
        out["scale"] = float(view_obj.ScaleDecimal)
    except Exception:  # noqa: BLE001
        pass
    return out


def _new_view_name(drawing, before: set[str]) -> str | None:
    after = {n for n, _ in _get_drawing_views(drawing, None)}
    new = sorted(after - before)
    return new[-1] if new else None


# --------------------------------------------------------------------------- project_view

def _project_view(parent_view, direction, x_mm, y_mm) -> dict:
    drawing = slip._active_drawing()
    d = str(direction).lower().strip()
    if d not in _DIRS:
        raise SWError(f"direction must be one of {sorted(_DIRS)}")
    pname, pview = _view(drawing, parent_view)
    before = {n for n, _ in _get_drawing_views(drawing, None)}
    drawing.ClearSelection2(True)
    if not drawing.ActivateView(pname):
        raise SWError(f"could not activate parent view {pname}")
    # select the parent view (DRAWINGVIEW) so CreateUnfoldedViewAt3 projects from it
    sel = False
    import pythoncom
    from win32com.client import VARIANT
    null_disp = VARIANT(pythoncom.VT_DISPATCH, None)
    try:
        sel = bool(drawing.Extension.SelectByID2(pname, "DRAWINGVIEW", 0, 0, 0, False, 0, null_disp, 0))
    except Exception as ex:  # noqa: BLE001
        logger.info("project_view: SelectByID2 DRAWINGVIEW raised %s", ex)
    if not sel:
        try:
            sel = bool(drawing.SelectByID(pname, "DRAWINGVIEW", 0, 0, 0))
        except Exception as ex:  # noqa: BLE001
            logger.info("project_view: SelectByID DRAWINGVIEW raised %s", ex)
    if not sel:
        raise SWError(f"could not select parent view {pname}")
    logger.info("project_view: CreateUnfoldedViewAt3(%s, %s, dir=%s)", x_mm, y_mm, d)
    try:
        v = drawing.CreateUnfoldedViewAt3(x_mm / _M_TO_MM, y_mm / _M_TO_MM, 0.0, False)
    except Exception as ex:  # noqa: BLE001
        raise SWError(f"CreateUnfoldedViewAt3 raised: {ex}")
    # CreateUnfoldedViewAt3 projects in the direction implied by the point relative to the
    # parent; the direction argument is validated against the parent position for the log.
    try:
        pp = pview.Position
        dx = x_mm / _M_TO_MM - float(pp[0]); dy = y_mm / _M_TO_MM - float(pp[1])
        implied = ("right" if dx > 0 else "left") if abs(dx) >= abs(dy) else ("top" if dy > 0 else "bottom")
        if implied != d:
            logger.info("project_view: requested %s but point implies %s (SolidWorks uses the point)", d, implied)
    except Exception:  # noqa: BLE001
        implied = None
    drawing.ClearSelection2(True)
    name = None
    try:
        name = v.Name if v is not None else None
    except Exception:  # noqa: BLE001
        name = None
    if not name:
        name = _new_view_name(drawing, before)
    if not name:
        raise SWError("projected view was not created (CreateUnfoldedViewAt3 returned nothing)")
    _, nv = _view(drawing, name)
    info = _view_info(nv)
    info.update({"status": "done", "parent": pname, "direction_requested": d,
                 "direction_implied_by_point": implied, "linked": True})
    try:
        slip._rebuild(drawing)
    except Exception:  # noqa: BLE001
        pass
    return info


# --------------------------------------------------------------------------- normal_to_face_view

def _activate_doc(app, title) -> None:
    """ActivateDoc3(name, silent, option, ByRef errors) - the ByRef long needs a VT_BYREF VARIANT
    under late binding (a plain 0 raises 'Type mismatch' on argument 4)."""
    import pythoncom
    from win32com.client import VARIANT
    errs = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    for call in (lambda: app.ActivateDoc3(title, False, 0, errs),
                 lambda: app.ActivateDoc2(title, False, errs),
                 lambda: app.ActivateDoc(title)):
        try:
            r = call()
            try:
                cur = _val(app.ActiveDoc, "GetTitle")
            except Exception:  # noqa: BLE001
                cur = None
            logger.info("activate %r -> %s (active now %r)", title, "ok" if r is not None else "None", cur)
            if r is not None:
                return
        except Exception as ex:  # noqa: BLE001
            logger.info("activate %r: %s", title, ex)
    raise SWError(f"could not activate document {title!r}")


def _unit(v):
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-12:
        raise SWError("normal vector is zero")
    return [c / n for c in v]


def _val(obj, name):
    """Zero-arg COM member: attribute read (late binding usually returns the value), call it if
    a bound method came back, else dispatch-ID invoke."""
    try:
        v = getattr(obj, name)
        if callable(v):
            v = v()
        return v
    except Exception:  # noqa: BLE001
        return slip._inv(obj, name)


def _planar_faces(body):
    faces = []
    arr = None
    for getter in (lambda: _val(body, "GetFaces"), lambda: slip._inv(body, "GetFaces")):
        try:
            arr = getter()
            if arr:
                break
        except Exception as ex:  # noqa: BLE001
            logger.info("GetFaces: %s", ex)
    if not arr:
        logger.info("_planar_faces: GetFaces returned nothing")
        return faces
    for f in arr:
        try:
            surf = slip._inv(f, "GetSurface")
            is_plane = bool(slip._inv(surf, "IsPlane"))
        except Exception as ex:  # noqa: BLE001
            logger.debug("IsPlane: %s", ex)
            continue
        if not is_plane:
            continue
        try:
            nrm = slip._inv(f, "Normal")
            area = float(slip._inv(f, "GetArea"))
        except Exception as ex:  # noqa: BLE001
            logger.debug("Normal/GetArea: %s", ex)
            continue
        faces.append((f, [float(nrm[0]), float(nrm[1]), float(nrm[2])], area))
    return faces


def _normal_to_face_view(model_path, normal, x_mm, y_mm, scale, label, label_offset_mm) -> dict:
    app = SWConnection.get_instance().get_app()
    drawing = slip._active_drawing()
    n = _unit([float(c) for c in normal])
    # find the part document
    part = None
    try:
        part = app.GetOpenDocumentByName(model_path)
    except Exception:  # noqa: BLE001
        part = None
    if part is None:
        import os
        title = os.path.splitext(os.path.basename(model_path))[0]
        try:
            part = app.GetOpenDocumentByName(title)
        except Exception:  # noqa: BLE001
            part = None
    if part is None:
        raise SWError(f"part is not open: {model_path} (open it first)")
    # candidate faces over all solid bodies
    bodies = []
    for getter in (lambda: part.GetBodies2(0, True), lambda: slip._inv(part, "GetBodies2", 0, True)):
        try:
            b = getter()
            if b:
                bodies = list(b)
                break
        except Exception as ex:  # noqa: BLE001
            logger.info("normal_to_face_view: GetBodies2 raised %s", ex)
    logger.info("normal_to_face_view: %d bodies", len(bodies))
    best = None
    n_faces = 0
    for body in bodies:
        pf = _planar_faces(body)
        n_faces += len(pf)
        for f, fn, area in pf:
            dot = fn[0] * n[0] + fn[1] * n[1] + fn[2] * n[2]
            score = (dot, area)
            if best is None or (dot > best[1][0] + 1e-6) or (abs(dot - best[1][0]) <= 1e-6 and area > best[1][1]):
                best = (f, score, fn)
    logger.info("normal_to_face_view: %d planar faces, best=%s", n_faces,
                None if best is None else (best[2], best[1]))
    face = None
    fn = None
    area = 0.0
    dot = 0.0
    if best is not None and best[1][0] >= 0.99:
        face, (dot, area), fn = best
    else:
        # Fallback: shoot a ray at the part along -normal from far outside (SelectByRay hits the
        # first face); verify the hit face's normal.
        logger.info("normal_to_face_view: enumeration failed/closest=%s -> SelectByRay fallback",
                    None if best is None else best[2])
        c = [0.0, 0.0, 0.0]
        try:
            box = slip._inv(part, "GetPartBox", True)
            c = [(float(box[0]) + float(box[3])) / 2, (float(box[1]) + float(box[4])) / 2,
                 (float(box[2]) + float(box[5])) / 2]
        except Exception as ex:  # noqa: BLE001
            logger.info("normal_to_face_view: GetPartBox raised %s", ex)
        origin = [c[i] + n[i] * 5.0 for i in range(3)]
        title0 = None
        try:
            title0 = part.GetTitle
        except Exception:  # noqa: BLE001
            title0 = slip._inv(part, "GetTitle")
        _activate_doc(app, title0)
        part.ClearSelection2(True)
        hit = False
        try:
            hit = bool(part.Extension.SelectByRay(origin[0], origin[1], origin[2], -n[0], -n[1], -n[2],
                                                  0.0005, 2, False, 0, 0))
        except Exception as ex:  # noqa: BLE001
            logger.info("normal_to_face_view: SelectByRay raised %s", ex)
        logger.info("normal_to_face_view: SelectByRay from %s hit=%s", origin, hit)
        if hit:
            try:
                sm = part.SelectionManager
                face = sm.GetSelectedObject6(1, -1)
                nrm = face.Normal
                fn = [float(nrm[0]), float(nrm[1]), float(nrm[2])]
                dot = fn[0] * n[0] + fn[1] * n[1] + fn[2] * n[2]
                try:
                    area = float(face.GetArea)
                except Exception:  # noqa: BLE001
                    area = 0.0
            except Exception as ex:  # noqa: BLE001
                logger.info("normal_to_face_view: reading hit face raised %s", ex)
                face = None
        if face is None or dot < 0.99:
            raise SWError(f"no planar face with normal ~{[round(v,3) for v in n]} found "
                          f"({n_faces} planar faces enumerated over {len(bodies)} bodies; ray hit normal={fn})")
    logger.info("normal_to_face_view: face normal=%s area=%.6f dot=%.4f", fn, area, dot)

    # activate the part, select the face, look normal to it
    title = None
    try:
        title = part.GetTitle
    except Exception:  # noqa: BLE001
        title = slip._inv(part, "GetTitle")
    _activate_doc(app, title)
    part.ClearSelection2(True)
    ok = False
    try:
        import pythoncom
        from win32com.client import VARIANT
        ok = bool(face.Select4(False, VARIANT(pythoncom.VT_DISPATCH, None)))
    except Exception as ex:  # noqa: BLE001
        logger.info("normal_to_face_view: face.Select4 raised %s", ex)
    if not ok:
        try:
            ok = bool(face.Select2(False, 0))
        except Exception as ex:  # noqa: BLE001
            logger.info("normal_to_face_view: face.Select2 raised %s", ex)
    if not ok:
        raise SWError("could not select the face in the part")
    logger.info("normal_to_face_view: ShowNamedView2(*Normal To)")
    part.ShowNamedView2("*Normal To", -1)
    try:
        part.ViewZoomtofit2()
    except Exception:  # noqa: BLE001
        pass
    part.ClearSelection2(True)
    # a named view is far more reliable than "*Current" for CreateDrawViewFromModelView3
    named = "FACE_A_NORMAL"
    try:
        part.NameView(named)
        logger.info("normal_to_face_view: NameView(%s)", named)
    except Exception as ex:  # noqa: BLE001
        logger.info("normal_to_face_view: NameView raised %s", ex)
        named = None

    # back to the drawing and insert the current model view
    dtitle = None
    try:
        dtitle = drawing.GetTitle
    except Exception:  # noqa: BLE001
        dtitle = slip._inv(drawing, "GetTitle")
    _activate_doc(app, dtitle)
    before = {nm for nm, _ in _get_drawing_views(drawing, None)}
    logger.info("normal_to_face_view: CreateDrawViewFromModelView3(*Current)")
    v = None
    for vname in ([named] if named else []) + ["*Current", "*Current Model View"]:
        try:
            v = drawing.CreateDrawViewFromModelView3(model_path, vname, x_mm / _M_TO_MM, y_mm / _M_TO_MM, 0.0)
            logger.info("normal_to_face_view: CreateDrawViewFromModelView3(%s) -> %r", vname, v)
        except Exception as ex:  # noqa: BLE001
            logger.info("normal_to_face_view: %s raised %s", vname, ex)
            v = None
        if v is not None:
            break
    name = None
    try:
        name = v.Name if v is not None else None
    except Exception:  # noqa: BLE001
        name = None
    if not name:
        name = _new_view_name(drawing, before)
    if not name:
        raise SWError("normal view was not created")
    _, nv = _view(drawing, name)
    if scale:
        try:
            nv.ScaleDecimal = float(scale)
        except Exception as ex:  # noqa: BLE001
            logger.info("normal_to_face_view: ScaleDecimal raised %s", ex)
    try:
        slip._set_view_display(name, "hidden_lines_removed", "removed")
    except Exception as ex:  # noqa: BLE001
        logger.info("normal_to_face_view: display style raised %s", ex)
    slip._rebuild(drawing)
    info = _view_info(nv)
    note_name = None
    if label:
        try:
            o = nv.GetOutline
            lx = (float(o[0]) + float(o[2])) / 2.0 * _M_TO_MM
            ly = float(o[1]) * _M_TO_MM - float(label_offset_mm)
            note_name = _insert_note(label, lx, ly, None, None, None).get("note")
        except Exception as ex:  # noqa: BLE001
            logger.info("normal_to_face_view: label failed %s", ex)
    info.update({"status": "done", "face_normal": [round(c, 4) for c in fn], "face_area_m2": round(area, 6),
                 "label_note": note_name, "linked": False,
                 "next": "view_geometry(this view) -> add_dimensions ONE feature (X from end, Y from edge, Ø); "
                         "insert_note('FACE A', ..., view_name=<ortho view>, leader_edge=<edge of the face>)"})
    return info


# --------------------------------------------------------------------------- insert_note

def _insert_note(text, x_mm, y_mm, view_name, leader_edge, font_height_mm) -> dict:
    drawing = slip._active_drawing()
    drawing.ClearSelection2(True)
    attached = False
    if view_name is not None and leader_edge is not None:
        vname, vobj = _view(drawing, view_name)
        drawing.ActivateView(vname)
        edges = _get_view_edges(vobj)
        i = int(leader_edge)
        if i < 0 or i >= len(edges):
            raise SWError(f"leader_edge {i} out of range 0..{len(edges)-1}")
        attached = bool(vobj.SelectEntity(edges[i], False))
        logger.info("insert_note: edge %s selected=%s", i, attached)
    note = drawing.InsertNote(str(text))
    if note is None:
        raise SWError("InsertNote returned None")
    ann = None
    try:
        ann = note.GetAnnotation
    except Exception:  # noqa: BLE001
        ann = slip._inv(note, "GetAnnotation")
    if attached and ann is not None:
        try:
            # swLeaderStyle_e.swSTRAIGHT = 1, swLeaderSide_e.swLS_SMART = 0
            ann.SetLeader3(1, 0, True, False, False, False)
        except Exception as ex:  # noqa: BLE001
            logger.info("insert_note: SetLeader3 raised %s", ex)
    try:
        note.SetTextPoint2(x_mm / _M_TO_MM, y_mm / _M_TO_MM, 0.0)
    except Exception:  # noqa: BLE001
        if ann is not None:
            try:
                ann.SetPosition2(x_mm / _M_TO_MM, y_mm / _M_TO_MM, 0.0)
            except Exception as ex:  # noqa: BLE001
                logger.info("insert_note: position raised %s", ex)
    if font_height_mm:
        try:
            tf = ann.GetTextFormat(0)
            tf.CharHeight = float(font_height_mm) / _M_TO_MM
            ann.SetTextFormat(0, False, tf)
        except Exception as ex:  # noqa: BLE001
            logger.info("insert_note: text format raised %s", ex)
    drawing.ClearSelection2(True)
    name = None
    try:
        name = ann.GetName if ann is not None else None
    except Exception:  # noqa: BLE001
        try:
            name = slip._inv(ann, "GetName")
        except Exception:  # noqa: BLE001
            name = None
    return {"status": "done", "note": name, "text": text, "text_mm": [x_mm, y_mm], "leader_attached": attached}


# --------------------------------------------------------------------------- bend lines

def _sketch_to_sheet(view_obj, app=None, seg=None):
    """f(x_m, y_m, z_m=0) bend-line sketch space -> sheet mm.
    Proper path: sketch -> model (inverse of ISketch.ModelToSketchTransform) -> sheet
    (IView.ModelToViewTransform, same calibrated conventions as view_geometry). Falls back to
    the old 'Position + scale * p' guess when any COM piece is missing."""
    pos = view_obj.Position
    cx, cy = float(pos[0]), float(pos[1])
    sc = float(view_obj.ScaleDecimal)

    def naive(x, y, z=0.0):
        return (round((cx + sc * x) * _M_TO_MM, 2), round((cy + sc * y) * _M_TO_MM, 2))

    if app is None or seg is None:
        logger.info("sketch_to_sheet(bend): naive position=(%s,%s) scale=%s", cx, cy, sc)
        return naive
    step = "import"
    try:
        from tools import slip_batch
        step = "GetSketch"
        sk = _val(seg, "GetSketch")
        step = "ModelToSketchTransform"
        m2s = _val(sk, "ModelToSketchTransform")
        step = "ArrayData"
        ma = [float(v) for v in _val(m2s, "ArrayData")]
        r = ma[0:9]; t = ma[9:12]; ssc = ma[12] if ma[12] else 1.0
        logger.info("sketch_to_sheet(bend): ModelToSketchTransform=%s", [round(v, 4) for v in ma[:13]])
        step = "ModelToViewTransform"
        arr = slip_batch._get_xform_array(view_obj)
        conv_row = slip_batch._TRANSFORM_DEBUG.get("convention", "row_vector(p*R)").startswith("row")
        m2v = slip_batch._make_transform(arr, conv_row)
        step = "apply"

        def to_model(x, y, z):
            # sketch = ssc * (model . R) + t   =>   model = ((sketch - t) / ssc) . R^T
            qx, qy, qz = (x - t[0]) / ssc, (y - t[1]) / ssc, (z - t[2]) / ssc
            mx = qx * r[0] + qy * r[1] + qz * r[2]
            my = qx * r[3] + qy * r[4] + qz * r[5]
            mz = qx * r[6] + qy * r[7] + qz * r[8]
            return mx, my, mz

        def f(x, y, z=0.0):
            mx, my, mz = to_model(x, y, z)
            X, Y = m2v(mx, my, mz)
            return (round(X, 2), round(Y, 2))
        # smoke test
        f(0.0, 0.0, 0.0)
        logger.info("sketch_to_sheet(bend): sketch->model->view transform in use (row=%s)", conv_row)
        return f
    except Exception as ex:  # noqa: BLE001
        logger.info("sketch_to_sheet(bend): transform path failed at %s (%s) -> naive", step, ex)
        return naive


def _get_bend_lines(view_obj) -> list:
    for getter in (lambda: view_obj.GetBendLines, lambda: slip._inv(view_obj, "GetBendLines")):
        try:
            bl = getter()
            if bl:
                return list(bl)
        except Exception as ex:  # noqa: BLE001
            logger.debug("GetBendLines: %s", ex)
    return []


def _flat_bend_lines(view_name) -> dict:
    drawing = slip._active_drawing()
    vname, vobj = _view(drawing, view_name)
    segs = _get_bend_lines(vobj)
    app = SWConnection.get_instance().get_app()
    to_sheet = _sketch_to_sheet(vobj, app, segs[0]) if segs else _sketch_to_sheet(vobj)
    out = []
    for i, seg in enumerate(segs):
        rec = {"i": i}
        try:
            sp = seg.GetStartPoint2
            ep = seg.GetEndPoint2
            s = to_sheet(float(sp.X), float(sp.Y), float(sp.Z)); e = to_sheet(float(ep.X), float(ep.Y), float(ep.Z))
            rec.update({"s": list(s), "e": list(e), "m": [round((s[0] + e[0]) / 2, 2), round((s[1] + e[1]) / 2, 2)],
                        "orientation": "horizontal" if abs(e[1] - s[1]) < abs(e[0] - s[0]) else "vertical"})
        except Exception as ex:  # noqa: BLE001
            rec["error"] = f"geometry: {ex}"
        try:
            rec["name"] = seg.GetName
        except Exception:  # noqa: BLE001
            try:
                rec["name"] = slip._inv(seg, "GetName")
            except Exception:  # noqa: BLE001
                pass
        out.append(rec)
    return {"status": "done", "view": vname, "count": len(out), "bend_lines": out,
            "coords_note": "sheet mm (unbroken view assumed)"}


def _select_segment(drawing, seg, mid_sketch_m, append: bool, sheet_mid_mm=None) -> tuple:
    """Select a sketch segment. 1) SelectByRay at its sheet position (swSelSKETCHSEGS = 9),
    2) SelectByID2 with a null VT_DISPATCH callout, 3) Select4 with SelectData, 4) Select2."""
    import pythoncom
    from win32com.client import VARIANT
    null_disp = VARIANT(pythoncom.VT_DISPATCH, None)
    ext = drawing.Extension
    if sheet_mid_mm:
        for cand in sheet_mid_mm:
            for radius in (0.001, 0.003):
                for z0, sel_type in ((0.0, 9), (1.0, 9)):
                    try:
                        ok = bool(ext.SelectByRay(cand[0] / _M_TO_MM, cand[1] / _M_TO_MM, z0,
                                                  0.0, 0.0, -1.0, radius, sel_type, append, 0, 0))
                        logger.info("bend select SelectByRay at %s z0=%s type=%s r=%s -> %s", cand, z0, sel_type, radius, ok)
                        if ok:
                            return True, f"SelectByRay{tuple(cand)}"
                    except Exception as ex:  # noqa: BLE001
                        logger.info("bend select SelectByRay raised %s", ex)
    name = ""
    try:
        name = seg.GetName
    except Exception:  # noqa: BLE001
        try:
            name = slip._inv(seg, "GetName")
        except Exception:  # noqa: BLE001
            name = ""
    pts = [(mid_sketch_m[0], mid_sketch_m[1])]
    if sheet_mid_mm:
        pts += [(c[0] / _M_TO_MM, c[1] / _M_TO_MM) for c in sheet_mid_mm]
    for nm in ([name] if name else []) + [""]:
        for px, py in pts:
            try:
                ok = bool(ext.SelectByID2(nm, "SKETCHSEGMENT", px, py, 0.0, append, 0, null_disp, 0))
                logger.info("bend select SelectByID2 %r at (%.4f, %.4f) -> %s", nm, px, py, ok)
                if ok:
                    return True, f"SelectByID2({nm!r})"
            except Exception as ex:  # noqa: BLE001
                logger.info("bend select SelectByID2 %r raised %s", nm, ex)
    try:
        sd = drawing.SelectionManager.CreateSelectData
        if bool(seg.Select4(append, sd)):
            return True, "Select4(SelectData)"
    except Exception as ex:  # noqa: BLE001
        logger.info("bend select Select4 raised %s", ex)
    try:
        if bool(seg.Select2(append, 0)):
            return True, "Select2"
    except Exception as ex:  # noqa: BLE001
        logger.info("bend select Select2 raised %s", ex)
    return False, "none"


def _add_bend_dimensions(view_name, dims) -> dict:
    app = SWConnection.get_instance().get_app()
    drawing = slip._active_drawing()
    vname, vobj = _view(drawing, view_name)
    drawing.ActivateView(vname)
    edges = _get_view_edges(vobj)
    segs = _get_bend_lines(vobj)
    if not segs:
        raise SWError("view has no bend lines (not a flat-pattern view?)")
    results = []
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:  # noqa: BLE001
        pass
    try:
        for n, d in enumerate(dims):
            e = int(d["e_ref"]); b = int(d["bend"])
            if e < 0 or e >= len(edges):
                raise SWError(f"dim {n}: e_ref {e} out of range 0..{len(edges)-1}")
            if b < 0 or b >= len(segs):
                raise SWError(f"dim {n}: bend {b} out of range 0..{len(segs)-1}")
            tx, ty = float(d["text"][0]) / _M_TO_MM, float(d["text"][1]) / _M_TO_MM
            seg = segs[b]
            try:
                sp = seg.GetStartPoint2; ep = seg.GetEndPoint2
                mid = ((float(sp.X) + float(ep.X)) / 2.0, (float(sp.Y) + float(ep.Y)) / 2.0,
                       (float(sp.Z) + float(ep.Z)) / 2.0)
            except Exception:  # noqa: BLE001
                mid = (0.0, 0.0, 0.0)
            drawing.ClearSelection2(True)
            to_sheet = _sketch_to_sheet(vobj, app, seg)
            cands = [to_sheet(mid[0], mid[1], mid[2])]
            logger.info("bend dim %s: sheet candidate %s", n, cands[0])
            # bend line FIRST (fresh selection), then the edge APPENDED
            ok2, how = _select_segment(drawing, seg, mid, False, cands)
            ok1 = bool(vobj.SelectEntity(edges[e], True)) if ok2 else False
            try:
                n_sel = int(drawing.SelectionManager.GetSelectedObjectCount2(-1))
            except Exception:  # noqa: BLE001
                n_sel = -1
            logger.info("bend dim %s: seg=%s via %s, edge=%s, selected=%s", n, ok2, how, ok1, n_sel)
            if n_sel != -1 and n_sel < 2:
                ok1 = False
            if not ok1 or not ok2:
                results.append({"n": n, "error": f"selection failed (edge {ok1}, bend line {ok2} via {how})"})
                continue
            disp = None
            try:
                disp = drawing.AddDimension2(tx, ty, 0)
            except Exception as ex:  # noqa: BLE001
                logger.info("bend dim %s: AddDimension2 raised %s", n, ex)
            if disp is None:
                try:
                    disp = drawing.Extension.AddDimension(tx, ty, 0, 0)
                except Exception as ex:  # noqa: BLE001
                    logger.info("bend dim %s: AddDimension raised %s", n, ex)
            if disp is None:
                results.append({"n": n, "error": "AddDimension returned None"})
                continue
            try:
                disp.SetUnits2(True, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0)
                disp.SetPrecision3(DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED)
            except Exception:  # noqa: BLE001
                pass
            info = slip._dim_info(disp)
            info["n"] = n
            results.append(info)
    finally:
        if orig_pref is not None:
            try:
                app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, orig_pref)
            except Exception:  # noqa: BLE001
                pass
        drawing.ClearSelection2(True)
    return {"status": "done", "view": vname, "added": sum(1 for r in results if "error" not in r),
            "requested": len(dims), "dimensions": results}
