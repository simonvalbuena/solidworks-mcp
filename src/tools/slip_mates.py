"""Assembly mates by face points: add_mate_faces.

Faces are named by component instance + a point ON the face in ASSEMBLY coordinates (mm).
The face is found geometrically on that component's body (IFace2.GetClosestPointOn in the
component frame), so two coincident faces of different components at the same location are
never confused (SelectByID2 ray picking is only the fallback).

Mate types: coincident, concentric, parallel, perpendicular, distance, width.
Width (swMateWIDTH = 11): faces[0:2] = width selections (the outer pair, e.g. the plate sides),
faces[2:4] = tab selections (the pair centred between them, e.g. the tube sides);
constraint = centered (default), free, dimension, percent.

Creation path: select every face with a mark (assembly-context entities come back from the
SelectionManager), then IAssemblyDoc.CreateMateData(type) + CreateMate(data). Fallback:
IAssemblyDoc.AddMate5 on the current selection.
"""

from __future__ import annotations

import json
import logging
import math

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection
from tools import slip

logger = logging.getLogger(__name__)

SW_DOC_ASSEMBLY = 2
_MATE_TYPES = {"coincident": 0, "concentric": 1, "perpendicular": 2, "parallel": 3,
               "distance": 5, "width": 11}
_ALIGN = {"aligned": 0, "anti_aligned": 1, "closest": 2}      # swMateAlign_e
_WIDTH = {"centered": 0, "free": 1, "dimension": 2, "percent": 3}  # swMateWidthOptions_e
_FACE_TOL_M = 0.0005  # a face point must lie within 0.5 mm of the face


def _val(obj, name):
    try:
        v = getattr(obj, name)
        if callable(v) and type(v).__name__ == "method":
            v = v()
        return v
    except Exception:  # noqa: BLE001
        return slip._inv(obj, name)


def _variant_doubles(vals):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in vals])


def _variant_dispatch_array(objs):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(objs))


def _assembly():
    app = SWConnection.get_instance().get_app()
    doc = slip._inv(app, "ActiveDoc")
    if doc is None:
        raise SWError("no active document")
    if int(_val(doc, "GetType")) != SW_DOC_ASSEMBLY:
        raise SWError("active document is not an assembly")
    return app, doc


def _components(doc):
    comps = slip._inv(doc, "GetComponents", False)
    return list(comps) if comps else []


def _find_component(doc, name):
    comps = _components(doc)
    names = []
    for c in comps:
        n = str(_val(c, "Name2"))
        names.append(n)
        if n == name or n.split("/")[-1] == name:
            return c
    raise SWError(f"component {name!r} not found; available: {names}")


def _to_component_frame(app, comp, p_m):
    """Assembly point (m) -> component (part) frame."""
    try:
        mu = slip._inv(app, "GetMathUtility")
        pt = slip._inv(mu, "CreatePoint", _variant_doubles(p_m))
        xf = slip._inv(comp, "Transform2")
        if xf is None:
            return list(p_m)
        inv = slip._inv(xf, "Inverse")
        q = slip._inv(pt, "MultiplyTransform", inv)
        arr = slip._inv(q, "ArrayData")
        return [float(arr[0]), float(arr[1]), float(arr[2])]
    except Exception as ex:  # noqa: BLE001
        logger.info("_to_component_frame: %s", ex)
        return list(p_m)


def _component_faces(comp):
    bodies = []
    try:
        b = slip._inv(comp, "GetBodies2", 0)  # swSolidBody
        if b:
            bodies = list(b) if isinstance(b, (tuple, list)) else [b]
    except Exception as ex:  # noqa: BLE001
        logger.info("GetBodies2: %s", ex)
    if not bodies:
        b = slip._inv(comp, "GetBody")
        if b is None:
            raise SWError(f"component {_val(comp, 'Name2')!r} has no body (lightweight/suppressed?)")
        bodies = [b]
    faces = []
    for body in bodies:
        arr = slip._inv(body, "GetFaces")
        if arr:
            faces.extend(list(arr))
    return faces


def _closest_face(faces, p):
    best, best_d = None, float("inf")
    for f in faces:
        try:
            r = slip._inv(f, "GetClosestPointOn", p[0], p[1], p[2])
            d = math.dist([float(r[0]), float(r[1]), float(r[2])], p)
        except Exception as ex:  # noqa: BLE001
            logger.debug("GetClosestPointOn: %s", ex)
            continue
        if d < best_d:
            best, best_d = f, d
    return best, best_d


def _sel_count(selmgr):
    return int(slip._inv(selmgr, "GetSelectedObjectCount2", -1))


def _selected_component_name(selmgr, idx):
    try:
        c = slip._inv(selmgr, "GetSelectedObjectsComponent4", idx, -1)
        return str(_val(c, "Name2")) if c is not None else None
    except Exception:  # noqa: BLE001
        return None


def _select_face(app, doc, selmgr, comp_name, point_mm, mark):
    """Append-select the face of comp_name containing point_mm; returns (entity, how, dist_mm)."""
    comp = _find_component(doc, comp_name)
    p_asm = [c / 1000.0 for c in point_mm]
    faces = _component_faces(comp)
    tried = []
    for label, p in (("component-frame", _to_component_frame(app, comp, p_asm)), ("assembly-frame", p_asm)):
        face, d = _closest_face(faces, p)
        tried.append(f"{label} {d * 1000:.3f} mm")
        if face is None or d > _FACE_TOL_M:
            continue
        before = _sel_count(selmgr)
        try:
            sd = slip._inv(selmgr, "CreateSelectData")
            slip._put(sd, "Mark", int(mark))
            ok = bool(slip._inv(face, "Select4", True, sd))
        except Exception as ex:  # noqa: BLE001
            logger.info("face.Select4: %s", ex)
            ok = False
        n = _sel_count(selmgr)
        if ok and n == before + 1:
            got = _selected_component_name(selmgr, n)
            if got is None or got.split("/")[-1] == comp_name or got == comp_name:
                ent = slip._inv(selmgr, "GetSelectedObject6", n, -1)
                return ent, f"Select4 ({label})", d * 1000
            logger.info("Select4 picked component %r, wanted %r", got, comp_name)
    # fallback: ray pick at the assembly point, then verify the component
    before = _sel_count(selmgr)
    ext = slip._inv(doc, "Extension")
    ok = bool(slip._inv(ext, "SelectByID2", "", "FACE", p_asm[0], p_asm[1], p_asm[2],
                        True, int(mark), None, 0))
    n = _sel_count(selmgr)
    if ok and n == before + 1:
        got = _selected_component_name(selmgr, n)
        if got is not None and (got == comp_name or got.split("/")[-1] == comp_name):
            return slip._inv(selmgr, "GetSelectedObject6", n, -1), "SelectByID2", None
        raise SWError(f"SelectByID2 at {point_mm} picked a face of {got!r}, not {comp_name!r} "
                      f"(geometric search: {', '.join(tried)})")
    raise SWError(f"no face of {comp_name!r} at {point_mm} mm (geometric search: {', '.join(tried)})")


def _last_mate_name(doc):
    """Name of the last feature inside the MateGroup folder."""
    last = None
    feat = _val(doc, "FirstFeature")
    while feat is not None:
        try:
            if str(_val(feat, "GetTypeName2")) == "MateGroup":
                sub = _val(feat, "GetFirstSubFeature")
                while sub is not None:
                    last = str(_val(sub, "Name"))
                    sub = _val(sub, "GetNextSubFeature")
        except Exception as ex:  # noqa: BLE001
            logger.debug("mate group walk: %s", ex)
        feat = _val(feat, "GetNextFeature")
    return last


def _add_mate_faces(mate_type, faces, alignment, distance_mm, flip, width_constraint, name):
    if mate_type not in _MATE_TYPES:
        raise SWError(f"mate_type must be one of {sorted(_MATE_TYPES)}")
    need = 4 if mate_type == "width" else 2
    if len(faces) != need:
        raise SWError(f"{mate_type} mate needs {need} faces, got {len(faces)}")
    app, doc = _assembly()
    selmgr = slip._inv(doc, "SelectionManager")
    slip._inv(doc, "ClearSelection2", True)
    before_name = _last_mate_name(doc)
    ents, picks = [], []
    for i, f in enumerate(faces):
        mark = (1 if i < 2 else 2) if mate_type == "width" else 1
        ent, how, d = _select_face(app, doc, selmgr, f["component"], f["point"], mark)
        ents.append(ent)
        picks.append({"component": f["component"], "point": f["point"], "how": how,
                      "dist_mm": None if d is None else round(d, 4)})
    log = []
    mate = None
    swtype = _MATE_TYPES[mate_type]
    # path 1: CreateMateData / CreateMate
    try:
        md = slip._inv(doc, "CreateMateData", swtype)
        if md is None:
            raise SWError("CreateMateData returned None")
        if mate_type == "width":
            slip._put(md, "WidthSelection", _variant_dispatch_array(ents[:2]))
            slip._put(md, "TabSelection", _variant_dispatch_array(ents[2:]))
            slip._put(md, "ConstraintType", _WIDTH[width_constraint])
        else:
            slip._put(md, "EntitiesToMate", _variant_dispatch_array(ents))
            try:
                slip._put(md, "MateAlignment", _ALIGN[alignment])
            except Exception as ex:  # noqa: BLE001
                log.append(f"MateAlignment: {ex}")
            if mate_type == "distance":
                slip._put(md, "Distance", float(distance_mm) / 1000.0)
                if flip:
                    slip._put(md, "FlipDimension", True)
        mate = slip._inv(doc, "CreateMate", md)
        log.append("CreateMate " + ("ok" if mate is not None else "returned None"))
    except Exception as ex:  # noqa: BLE001
        log.append(f"CreateMate path: {ex}")
        mate = None
    # path 2: AddMate5 on the live selection
    if mate is None:
        try:
            res = slip._inv(doc, "AddMate5", swtype, _ALIGN[alignment], bool(flip),
                            float(distance_mm) / 1000.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0,
                            False, False, _WIDTH[width_constraint], 0)
            mate = res[0] if isinstance(res, tuple) else res
            log.append(f"AddMate5 -> {res!r}"[:200])
        except Exception as ex:  # noqa: BLE001
            log.append(f"AddMate5: {ex}")
    slip._inv(doc, "ClearSelection2", True)
    if mate is None:
        raise SWError(f"{mate_type} mate not created: {'; '.join(log)}")
    try:
        slip._inv(doc, "EditRebuild3")
    except Exception:  # noqa: BLE001
        pass
    after_name = _last_mate_name(doc)
    mate_name = after_name if after_name != before_name else None
    if name and mate_name:
        try:
            feat = slip._inv(doc, "FeatureByName", mate_name)
            slip._put(feat, "Name", name)
            mate_name = name
        except Exception as ex:  # noqa: BLE001
            log.append(f"rename: {ex}")
    return {"status": "done", "mate_type": mate_type, "mate": mate_name, "faces": picks, "log": log}


def _component_positions():
    app, doc = _assembly()
    out = []
    for c in _components(doc):
        row = {"name": str(_val(c, "Name2"))}
        try:
            xf = slip._inv(c, "Transform2")
            arr = list(slip._inv(xf, "ArrayData"))
            row["rotation"] = [round(float(v), 6) for v in arr[:9]]
            row["origin_mm"] = [round(float(v) * 1000.0, 4) for v in arr[9:12]]
        except Exception as ex:  # noqa: BLE001
            row["error"] = str(ex)
        try:
            row["fixed"] = bool(slip._inv(c, "IsFixed"))
        except Exception:  # noqa: BLE001
            pass
        out.append(row)
    return {"status": "done", "components": out}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def add_mate_faces(
        mate_type: str,
        faces: str,
        alignment: str = "closest",
        distance_mm: float = 0.0,
        flip: bool = False,
        width_constraint: str = "centered",
        name: str = "",
    ) -> str:
        """Add a mate in the ACTIVE assembly between faces named by component + a point on the face.
        faces: JSON list of {"component": "TUBE-1", "point": [x, y, z]} — point in ASSEMBLY mm,
        lying on the face (interior, not on an edge).
        mate_type: coincident | concentric | parallel | perpendicular | distance | width.
        width: 4 faces — first two = width pair (outer bounds), last two = tab pair (centred);
        width_constraint centered|free|dimension|percent.
        alignment: aligned | anti_aligned | closest. distance_mm for distance mates.
        name: optional new mate feature name."""
        try:
            fl = json.loads(faces) if isinstance(faces, str) else faces
            result = await sw.execute(_add_mate_faces, mate_type, fl, alignment, distance_mm,
                                      flip, width_constraint, name)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"add_mate_faces failed: {e}")
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"add_mate_faces unexpected error: {e}")

    @mcp.tool()
    async def component_positions() -> str:
        """List every component of the ACTIVE assembly with its transform
        (rotation 3x3 row data + origin in mm) and fixed flag — for comparing two assemblies."""
        try:
            return json.dumps(await sw.execute(_component_positions), ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"component_positions failed: {e}")
