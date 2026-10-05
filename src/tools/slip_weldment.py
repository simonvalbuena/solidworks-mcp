"""Weldment tools + API introspection.

sw_api(interface, member_filter)      signatures straight from sldworks.tlb (no guessing)
dump_feature(feature_name)            every zero-arg property of a feature's definition object
                                      (e.g. IStructuralMemberFeatureData: profile path, groups, angle)
find_weldment_profiles(filter)        *.sldlfp files under the weldment-profile file locations
insert_structural_member(...)         weldment feature (if missing) + structural member on the
                                      segments of a named sketch

The structural-member insert method is resolved at run time from the type library: the newest
IFeatureManager.InsertStructuralWeldment<N> is called with its arguments filled BY PARAMETER
NAME, so a SolidWorks version change does not silently shift positional arguments.
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
from tools import slip_tube

logger = logging.getLogger(__name__)

_VT = {0: "empty", 2: "short", 3: "long", 4: "float", 5: "double", 8: "string", 9: "IDispatch",
       11: "bool", 12: "VARIANT", 13: "IUnknown", 16: "char", 17: "byte", 18: "ushort", 19: "ulong",
       20: "int64", 22: "int", 23: "uint", 24: "void", 25: "HRESULT", 26: "ptr", 27: "safearray",
       28: "carray", 29: "userdefined"}
_INVKIND = {1: "method", 2: "get", 4: "put", 8: "putref"}
_SLDWORKS_TLB: dict = {}


# --------------------------------------------------------------------------- type library

def _vt_name(td) -> str:
    """pywin32 TYPEDESC: an int VT, or (VT_PTR|VT_SAFEARRAY|VT_CARRAY|VT_USERDEFINED, inner)."""
    if isinstance(td, int):
        base = td & 0x0FFF
        s = _VT.get(base, f"vt{base}")
        if td & 0x2000:
            s += "[]"
        if td & 0x4000:
            s += "&"
        return s
    if isinstance(td, tuple) and td:
        vt = td[0]
        inner = td[1] if len(td) > 1 else None
        if isinstance(vt, tuple):
            return _vt_name(vt)
        if vt == 26:
            return _vt_name(inner) + "*"
        if vt == 27:
            return _vt_name(inner) + "[]"
        if vt == 29:
            return "userdefined"
        return _vt_name(vt)
    return str(td)


def _param_str(a) -> tuple[str, bool]:
    """ELEMDESC (typedesc, paramflags, default) -> (type, is_out)."""
    td = a[0] if isinstance(a, tuple) and len(a) >= 2 else a
    flags = a[1] if isinstance(a, tuple) and len(a) >= 2 and isinstance(a[1], int) else 0
    return _vt_name(td), bool(flags & 2)


def _tlb_paths(app) -> list[str]:
    out = []
    for p in slip_tube._swconst_paths(app):
        q = os.path.join(os.path.dirname(p), "sldworks.tlb")
        if os.path.isfile(q):
            out.append(q)
    return out


def _sldworks_tlb(app):
    if "tlb" in _SLDWORKS_TLB:
        return _SLDWORKS_TLB["tlb"]
    import pythoncom
    for path in _tlb_paths(app):
        try:
            _SLDWORKS_TLB["tlb"] = pythoncom.LoadTypeLib(path)
            _SLDWORKS_TLB["path"] = path
            return _SLDWORKS_TLB["tlb"]
        except Exception as ex:  # noqa: BLE001
            logger.info("LoadTypeLib %s: %s", path, ex)
    raise SWError("sldworks.tlb not found next to swconst.tlb")


def _typeinfo_by_name(app, name):
    tlb = _sldworks_tlb(app)
    want = {name, "I" + name} if not name.startswith("I") else {name, name[1:]}
    for i in range(tlb.GetTypeInfoCount()):
        n = tlb.GetDocumentation(i)[0]
        if n in want:
            return tlb.GetTypeInfo(i), n
    raise SWError(f"interface {name!r} not in sldworks.tlb")


def _members(ti) -> list[dict]:
    import pythoncom
    attr = ti.GetTypeAttr()
    # dual interface: prefer the dispatch side
    if attr.typekind == pythoncom.TKIND_INTERFACE:
        try:
            href = ti.GetRefTypeOfImplType(-1)
            ti = ti.GetRefTypeInfo(href)
            attr = ti.GetTypeAttr()
        except Exception:  # noqa: BLE001
            pass
    out = []
    for j in range(attr.cFuncs):
        fd = ti.GetFuncDesc(j)
        names = ti.GetNames(fd.memid)
        params = []
        for k, a in enumerate(fd.args):
            pname = names[k + 1] if k + 1 < len(names) else f"p{k}"
            t, is_out = _param_str(a)
            params.append({"name": pname, "type": t + (" out" if is_out else "")})
        out.append({"name": names[0], "kind": _INVKIND.get(fd.invkind, str(fd.invkind)),
                    "params": params, "returns": _param_str(fd.rettype)[0], "memid": fd.memid})
    return out


def _object_members(obj, hint: str = "") -> list[dict]:
    """Members from the object's own typeinfo; else from sldworks.tlb by interface name (hint)."""
    try:
        return _members(slip._ole(obj).GetTypeInfo())
    except Exception as ex:  # noqa: BLE001
        if not hint:
            raise SWError(f"no typeinfo on object and no interface hint ({ex})")
    app = SWConnection.get_instance().get_app()
    ti, _real = _typeinfo_by_name(app, hint)
    return _members(ti)


def _object_interface(obj) -> str:
    try:
        return slip._ole(obj).GetTypeInfo().GetDocumentation(-1)[0]
    except Exception:  # noqa: BLE001
        return type(obj).__name__


def _sw_api(interface, member_filter):
    app = SWConnection.get_instance().get_app()
    ti, real = _typeinfo_by_name(app, interface)
    f = (member_filter or "").lower()
    rows = []
    seen = set()
    plumbing = {"QueryInterface", "AddRef", "Release", "GetTypeInfoCount", "GetTypeInfo",
                "GetIDsOfNames", "Invoke"}
    for m in _members(ti):
        if m["name"] in plumbing:
            continue
        if f and f not in m["name"].lower():
            continue
        key = (m["name"], m["kind"])
        if key in seen:
            continue
        seen.add(key)
        sig = ", ".join(f"{p['name']}: {p['type']}" for p in m["params"])
        rows.append(f"{m['kind']:6} {m['name']}({sig}) -> {m['returns']}")
    return {"status": "done", "interface": real, "tlb": _SLDWORKS_TLB.get("path"), "count": len(rows),
            "members": rows}


# --------------------------------------------------------------------------- dumping objects

def _jsonable(v, depth):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (tuple, list)):
        if len(v) > 64:
            return {"array_len": len(v), "head": [_jsonable(x, depth) for x in v[:8]]}
        return [_jsonable(x, depth) for x in v]
    tn = type(v).__name__
    if tn in ("CDispatch", "PyIDispatch") or hasattr(v, "_oleobj_"):
        iface = _object_interface(v)
        if depth > 0:
            return {"interface": iface, "props": _dump_props(v, depth - 1)}
        return {"interface": iface}
    return str(v)


# nested objects without typeinfo: interface by the property that returned them
_NESTED_HINT = {"Groups": "IStructuralMemberGroup", "GetGroups": "IStructuralMemberGroup"}
_DEF_HINT = {"WeldMemberFeat": "IStructuralMemberFeatureData", "Cut": "IExtrudeFeatureData2",
             "Extrusion": "IExtrudeFeatureData2", "Boss": "IExtrudeFeatureData2",
             "ICE": "IExtrudeFeatureData2", "SMBaseFlange": "IBaseFlangeFeatureData",
             "Chamfer": "IChamferFeatureData2", "Fillet": "ISimpleFilletFeatureData2",
             "WeldmentFeature": "IWeldmentFeatureData", "SheetMetal": "ISheetMetalFeatureData"}


def _dump_props(obj, depth=1, hint=""):
    out = {}
    try:
        members = _object_members(obj, hint)
    except Exception as ex:  # noqa: BLE001
        return {"error": f"no typeinfo: {ex}"}
    for m in members:
        if m["kind"] != "get" or m["params"]:
            continue
        name = m["name"]
        if name in out:
            continue
        try:
            v = slip._inv(obj, name)
            if depth > 0 and name in _NESTED_HINT and isinstance(v, (tuple, list)):
                out[name] = [{"interface": _NESTED_HINT[name],
                              "props": _dump_props(x, depth - 1, _NESTED_HINT[name])} for x in v]
            else:
                out[name] = _jsonable(v, depth)
        except Exception as ex:  # noqa: BLE001
            out[name] = f"<err {str(ex)[:80]}>"
    return out


def _active_part():
    app = SWConnection.get_instance().get_app()
    doc = slip._inv(app, "ActiveDoc")
    if doc is None:
        raise SWError("no active document")
    if int(slip._inv(doc, "GetType")) != 1:
        raise SWError("active document is not a part")
    return app, doc


def _feature(doc, name):
    feat = slip._inv(doc, "FeatureByName", name)
    if feat is None:
        raise SWError(f"feature {name!r} not found")
    return feat


def _dump_feature(feature_name, depth, interface=""):
    app = SWConnection.get_instance().get_app()
    doc = slip._inv(app, "ActiveDoc")
    feat = _feature(doc, feature_name)
    res = {"status": "done", "feature": feature_name,
           "type": str(slip._inv(feat, "GetTypeName2"))}
    try:
        d = slip._inv(feat, "GetDefinition")
        hint = interface or _DEF_HINT.get(res["type"], "")
        res["definition_interface"] = hint or _object_interface(d)
        res["definition"] = _dump_props(d, depth, hint)
    except Exception as ex:  # noqa: BLE001
        res["definition_error"] = str(ex)
    try:
        s = slip._inv(feat, "GetSpecificFeature2")
        res["specific_interface"] = _object_interface(s)
    except Exception:  # noqa: BLE001
        pass
    return res


def _set_feature_properties(feature_name, props, interface=""):
    """GetDefinition -> put properties -> ModifyDefinition (in place, keeps face ids)."""
    app = SWConnection.get_instance().get_app()
    doc = slip._inv(app, "ActiveDoc")
    feat = _feature(doc, feature_name)
    ftype = str(slip._inv(feat, "GetTypeName2"))
    d = slip._inv(feat, "GetDefinition")
    log = []
    accessed = False
    import pythoncom
    from win32com.client import VARIANT
    no_comp = VARIANT(pythoncom.VT_DISPATCH, None)   # a plain None is rejected ("Type mismatch")
    try:
        accessed = bool(slip._inv(d, "AccessSelections", doc, no_comp))
    except Exception as ex:  # noqa: BLE001
        log.append(f"AccessSelections: {str(ex)[:60]}")
    before, after = {}, {}
    try:
        for k, v in props.items():
            try:
                before[k] = slip._inv(d, k)
            except Exception:  # noqa: BLE001
                before[k] = None
            slip._put(d, k, v)
        # K-factor lives in the custom bend allowance object (a plain KFactor put does not stick)
        if "KFactor" in props:
            try:
                cba = slip._inv(d, "GetCustomBendAllowance")
                if cba is not None:
                    slip._put(cba, "Type", 2)   # swBendAllowanceKFactor
                    slip._put(cba, "KFactor", float(props["KFactor"]))
                    try:
                        slip._inv(d, "SetCustomBendAllowance", cba)
                    except Exception as ex:  # noqa: BLE001
                        log.append(f"SetCustomBendAllowance: {str(ex)[:60]}")
            except Exception as ex:  # noqa: BLE001
                log.append(f"custom bend allowance: {str(ex)[:60]}")
        ok = bool(slip._inv(feat, "ModifyDefinition", d, doc, no_comp))
    except Exception as ex:  # noqa: BLE001
        if accessed:
            try:
                slip._inv(d, "ReleaseSelectionAccess")
            except Exception:  # noqa: BLE001
                pass
        raise SWError(f"set_feature_properties: {ex}")
    if not ok and accessed:
        try:
            slip._inv(d, "ReleaseSelectionAccess")
        except Exception:  # noqa: BLE001
            pass
    try:
        slip._inv(doc, "EditRebuild3")
    except Exception:  # noqa: BLE001
        pass
    d2 = slip._inv(_feature(doc, feature_name), "GetDefinition")
    for k in props:
        try:
            after[k] = slip._inv(d2, k)
        except Exception:  # noqa: BLE001
            after[k] = None
    return {"status": "done" if ok else "failed", "feature": feature_name, "type": ftype,
            "before": before, "after": after, "log": log, **_body_report(doc)}


# --------------------------------------------------------------------------- profiles

def _profile_dirs(app):
    dirs = []
    slip_tube._load_enums(app)
    idx = slip_tube._ENUM_CACHE.get("swUserPreferenceStringValue_e", {}).get(
        "swFileLocationsWeldmentProfiles")
    if idx is not None:
        try:
            v = str(slip._inv(app, "GetUserPreferenceStringValue", idx) or "")
            dirs += [d for d in v.split(";") if d]
        except Exception as ex:  # noqa: BLE001
            logger.info("weldment profile location: %s", ex)
    try:
        exe = str(slip._inv(app, "GetExecutablePath"))
        base = os.path.join(os.path.dirname(exe), "lang", "english", "weldment profiles")
        if os.path.isdir(base):
            dirs.append(base)
    except Exception:  # noqa: BLE001
        pass
    seen, out = set(), []
    for d in dirs:
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen and os.path.isdir(d):
            seen.add(k)
            out.append(d)
    return out


def _find_profiles(name_filter, limit=60):
    app = SWConnection.get_instance().get_app()
    dirs = _profile_dirs(app)
    toks = [t for t in (name_filter or "").lower().replace("\\", "/").split() if t]
    hits = []
    for d in dirs:
        for root, _sub, files in os.walk(d):
            for f in files:
                if not f.lower().endswith(".sldlfp"):
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, d).lower().replace("\\", "/")
                if all(t in rel for t in toks):
                    hits.append(p)
                    if len(hits) >= limit:
                        return {"status": "done", "dirs": dirs, "profiles": hits, "truncated": True}
    return {"status": "done", "dirs": dirs, "profiles": hits}


# --------------------------------------------------------------------------- structural member

def _variant_dispatch_array(objs):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(objs))


def _feature_types(doc):
    out = []
    feat = slip._inv(doc, "FirstFeature")
    while feat is not None:
        try:
            out.append((str(slip._inv(feat, "Name")), str(slip._inv(feat, "GetTypeName2"))))
        except Exception:  # noqa: BLE001
            pass
        feat = slip._inv(feat, "GetNextFeature")
    return out


def _body_report(doc):
    rep = {}
    try:
        bodies = slip._inv(doc, "GetBodies2", 0, True)
        rep["bodies"] = len(bodies) if bodies else 0
        boxes = []
        for b in bodies or ():
            bx = slip._inv(b, "GetBodyBox")
            boxes.append([round(float(v) * 1000.0, 3) for v in bx])
        rep["body_boxes_mm"] = boxes
    except Exception as ex:  # noqa: BLE001
        rep["bodies_error"] = str(ex)
    try:
        ext = slip._inv(doc, "Extension")
        mp = slip._inv(ext, "CreateMassProperty")
        rep["volume_m3"] = float(slip._inv(mp, "Volume"))
        com = slip._inv(mp, "CenterOfMass")
        rep["com_mm"] = [round(float(v) * 1000.0, 4) for v in com]
    except Exception as ex:  # noqa: BLE001
        rep["mass_error"] = str(ex)
    return rep


def _insert_method(fm):
    best = None
    for m in _object_members(fm, "IFeatureManager"):
        n = m["name"]
        if n.startswith("InsertStructuralWeldment") and m["kind"] == "method":
            suf = n[len("InsertStructuralWeldment"):]
            num = int(suf) if suf.isdigit() else 1
            if best is None or num > best[0]:
                best = (num, m)
    if best is None:
        raise SWError("IFeatureManager has no InsertStructuralWeldment* method")
    return best[1]


def _insert_structural_member(sketch_name, segment_indices, profile_path, angle_deg,
                              mirror_profile, mirror_axis, merge_arc_segments, connected_option):
    app, doc = _active_part()
    if not os.path.isfile(profile_path):
        raise SWError(f"profile not found: {profile_path} (use find_weldment_profiles)")
    log = []
    fm = slip._inv(doc, "FeatureManager")
    # 0) leave an open sketch (the path sketch is usually still in edit mode)
    try:
        skm = slip._inv(doc, "SketchManager")
        if slip._inv(skm, "ActiveSketch") is not None:
            slip._inv(skm, "InsertSketch", True)
            log.append("closed active sketch")
    except Exception as ex:  # noqa: BLE001
        log.append(f"close sketch: {str(ex)[:60]}")
    # 1) weldment feature
    if not any(t == "WeldmentFeature" for _n, t in _feature_types(doc)):
        wf = slip._inv(fm, "InsertWeldmentFeature")
        log.append("InsertWeldmentFeature " + ("ok" if wf is not None else "None"))
    # 2) segments of the sketch
    sk_feat = _feature(doc, sketch_name)
    sk = slip._inv(sk_feat, "GetSpecificFeature2")
    segs = list(slip._inv(sk, "GetSketchSegments") or ())
    segs = [s for s in segs if not bool(slip._inv(s, "ConstructionGeometry"))]
    if segment_indices:
        segs = [segs[i] for i in segment_indices]
    if not segs:
        raise SWError(f"sketch {sketch_name!r} has no usable segments")
    # 3) group
    group = slip._inv(fm, "CreateStructuralMemberGroup")
    if group is None:
        raise SWError("CreateStructuralMemberGroup returned None")
    slip._put(group, "Segments", _variant_dispatch_array(segs))
    # defaults as read from a SolidWorks-made member (dump_feature on TS5X5X0.25, SW 2025)
    for prop, val in (("Angle", math.radians(angle_deg)), ("MirrorProfile", bool(mirror_profile)),
                      ("MirrorProfileAxis", int(mirror_axis) or 1), ("AlignAxis", 1),
                      ("ApplyCornerTreatment", True), ("CornerTreatmentType", 1),
                      ("MergeArcSegmentBodies", bool(merge_arc_segments))):
        try:
            slip._put(group, prop, val)
        except Exception as ex:  # noqa: BLE001
            log.append(f"group.{prop}: {str(ex)[:60]}")
    # 4) insert, arguments by parameter name
    m = _insert_method(fm)
    values = {
        "path": profile_path,
        "profile": profile_path,
        "allowprotrusion": True,
        "configurationname": "",
        "endcond": int(connected_option),
        "merge": bool(merge_arc_segments),
        "connectedsegmentsoption": int(connected_option),
        "angle": math.radians(angle_deg),
        "mirrorprofile": bool(mirror_profile),
        "mirrorprofileaxis": int(mirror_axis),
        "mergearcsegmentbodies": bool(merge_arc_segments),
        "groups": _variant_dispatch_array([group]),
    }
    args, unknown = [], []
    for p in m["params"]:
        key = p["name"].lower()
        if key in values:
            args.append(values[key])
            continue
        unknown.append(f"{p['name']}:{p['type']}")
        t = p["type"]
        args.append(False if t.startswith("bool") else 0.0 if t.startswith(("double", "float"))
                    else "" if t.startswith("string") else None if t.startswith(("IDispatch", "VARIANT"))
                    else 0)
    log.append(f"{m['name']}({', '.join(p['name'] for p in m['params'])})")
    if unknown:
        log.append("defaulted: " + ", ".join(unknown))
    feat = slip._inv(fm, m["name"], *args)
    if feat is None:
        raise SWError(f"{m['name']} returned None; {'; '.join(log)}")
    try:
        slip._inv(doc, "EditRebuild3")
    except Exception:  # noqa: BLE001
        pass
    name = str(slip._inv(feat, "Name"))
    return {"status": "done", "feature": name, "segments": len(segs), "log": log, **_body_report(doc)}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def sw_api(interface: str, member_filter: str = "") -> str:
        """List methods/properties of a SolidWorks API interface from the installed sldworks.tlb
        (exact parameter names and types for this SolidWorks version), e.g.
        sw_api("IFeatureManager", "StructuralWeldment"). member_filter is a case-insensitive substring."""
        return await slip_tube._run(sw, "sw_api", _sw_api, interface, member_filter)

    @mcp.tool()
    async def dump_feature(feature_name: str, depth: int = 1, interface: str = "") -> str:
        """Read every zero-argument property of a feature's definition object in the ACTIVE
        document (IFeature.GetDefinition), e.g. a structural member's profile path, groups, angle.
        depth: how far nested objects (groups, segments) are expanded. interface: the definition
        interface when it cannot be guessed from the feature type (e.g. "IExtrudeFeatureData2"). Read-only."""
        return await slip_tube._run(sw, "dump_feature", _dump_feature, feature_name, depth, interface)

    @mcp.tool()
    async def set_feature_properties(feature_name: str, properties: str) -> str:
        """Edit a feature IN PLACE (keeps face ids, so assembly mates survive): reads its definition
        (IFeature.GetDefinition), sets the given properties, ModifyDefinition, rebuild.
        properties: JSON object, names as dump_feature shows them, SI units, angles in radians —
        e.g. {"BendRadius": 0.0013208, "KFactor": 0.45} on a Sheet-Metal feature.
        Returns before/after values, body count, volume."""
        return await slip_tube._run(sw, "set_feature_properties", _set_feature_properties,
                                    feature_name, json.loads(properties))

    @mcp.tool()
    async def find_weldment_profiles(name_filter: str = "") -> str:
        """Find weldment profile files (*.sldlfp) under the SolidWorks weldment-profile file
        locations. name_filter: space-separated substrings matched against the relative path,
        e.g. "square tube 5 x 5"."""
        return await slip_tube._run(sw, "find_weldment_profiles", _find_profiles, name_filter)

    @mcp.tool()
    async def insert_structural_member(
        sketch_name: str,
        profile_path: str,
        segment_indices: str = "",
        angle_deg: float = 0.0,
        mirror_profile: bool = False,
        mirror_axis: int = 0,
        merge_arc_segments: bool = False,
        connected_option: int = 1,
    ) -> str:
        """Weldment structural member in the ACTIVE part along the (non-construction) segments of a
        sketch. Adds the Weldment feature first if the part has none. profile_path: a .sldlfp file
        (find_weldment_profiles). segment_indices: optional JSON list to use only some segments.
        Returns the feature, body count/boxes, volume and centre of mass for verification."""
        idx = json.loads(segment_indices) if segment_indices else []
        return await slip_tube._run(sw, "insert_structural_member", _insert_structural_member,
                                    sketch_name, idx, profile_path, angle_deg, mirror_profile,
                                    mirror_axis, merge_arc_segments, connected_option)
