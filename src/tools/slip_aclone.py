"""Clone an assembly from an open SOURCE assembly into the ACTIVE (target) assembly, mapping each
source part / sub-assembly file to its rebuilt counterpart (part_map).

Steps (run separately or together, through slip_dev.fork_call — background for big assemblies):
  components  insert every directly-placed source component (tree type "Reference") with the
              mapped file, the same transform, configuration, fixed state and instance number
  mates       every mate of the source MateGroup, in order: same type / alignment / flip / value,
              entities mapped through the component pairs (faces, edges, vertices by identical
              part-frame geometry; component / assembly planes by name); renamed like the source
  patterns    mirror-component and local linear / circular pattern features with mapped seeds
  verify      every component transform compared with its source counterpart

Pattern / mirror instances are created by the pattern features, never inserted.
"""

from __future__ import annotations

import logging
import math
import os

from errors import SWError
from sw_connection import SWConnection
from tools import slip
from tools import slip_clone as sc
from tools import slip_mates as sm
from tools import slip_weldment as sw_w

logger = logging.getLogger(__name__)
_MM = 1000.0


# --------------------------------------------------------------------------- helpers

def _app():
    return SWConnection.get_instance().get_app()


def _try(f, default=None):
    try:
        return f()
    except Exception:  # noqa: BLE001
        return default


def _target():
    app = _app()
    doc = slip._inv(app, "ActiveDoc")
    if doc is None or int(slip._inv(doc, "GetType")) != 2:
        raise SWError("the ACTIVE document must be the target assembly")
    return app, doc


def _xf(comp):
    return [float(v) for v in list(slip._inv(slip._inv(comp, "Transform2"), "ArrayData"))]


def _xf_close(a, b, tol=1e-6):
    return all(abs(a[i] - b[i]) < (tol if i < 9 else tol * 10) for i in range(12))


def _path(comp):
    return os.path.normcase(str(slip._inv(comp, "GetPathName")))


def _name(comp):
    return str(slip._inv(comp, "Name2"))


def _all_components(doc, top_only=False):
    return list(slip._inv(doc, "GetComponents", bool(top_only)) or ())


def _norm_map(part_map):
    return {os.path.normcase(k): v for k, v in part_map.items()}


def _base(path):
    return os.path.splitext(os.path.basename(path))[0]


def _mate_group(doc):
    for f in sc._features(doc):
        if sc._ftype(f) == "MateGroup":
            return f
    return None


def _subfeatures(feat):
    out = []
    sub = _try(lambda: slip._inv(feat, "GetFirstSubFeature"))
    while sub is not None:
        out.append(sub)
        sub = _try(lambda sub=sub: slip._inv(sub, "GetNextSubFeature"))
    return out


def _enum(name):
    from tools import slip_tube
    app = _app()
    slip_tube._load_enums(app)
    return slip_tube._ENUM_CACHE.get(name, {})


# --------------------------------------------------------------------------- survey

def survey(source: str) -> dict:
    """Plan: directly placed components, files, mate types, pattern features of the source."""
    app = _app()
    sdoc = sc._doc(source)
    feats = sc._features(sdoc)
    placed, patterned, others = [], [], []
    for f in feats:
        t = sc._ftype(f)
        if t == "Reference":
            placed.append(sc._fname(f))
        elif t == "ReferencePattern":
            patterned.append(sc._fname(f))
        elif t in ("MirrorCompFeat", "LocalLPattern", "LocalCirPattern", "DerivedLPattern",
                   "LocalChainPattern", "LocalCurvePattern", "LocalSketchPattern", "DerivedCirPattern"):
            others.append({"name": sc._fname(f), "type": t})
    files = {}
    for c in _all_components(sdoc, True):
        files.setdefault(_path(c), 0)
        files[_path(c)] += 1
    mates = {}
    mg = _mate_group(sdoc)
    names = {int(v): k for k, v in _enum("swMateType_e").items()}
    for m in _subfeatures(mg) if mg is not None else []:
        mm = _try(lambda m=m: slip._inv(m, "GetSpecificFeature2"))
        t = _try(lambda: int(slip._inv(mm, "Type")))
        key = names.get(t, str(t)) if t is not None else sc._ftype(m)
        mates[key] = mates.get(key, 0) + 1
    return {"placed": placed, "patterned_count": len(patterned), "pattern_features": others,
            "files": files, "mate_types": mates,
            "mate_count": sum(mates.values())}


# --------------------------------------------------------------------------- components

def _instance_suffix(name):
    leaf = name.split("/")[-1]
    return leaf.rsplit("-", 1)[1] if "-" in leaf else "1"


def clone_components(source: str, part_map: dict, only: list | None = None) -> dict:
    app, tdoc = _target()
    sdoc = sc._doc(source)
    pm = _norm_map(part_map)
    mu = slip._inv(app, "GetMathUtility")
    placed = [sc._fname(f) for f in sc._features(sdoc) if sc._ftype(f) == "Reference"]
    if only:
        placed = [p for p in placed if p in only]
    by_name = {_name(c): c for c in _all_components(sdoc, True)}
    existing = {_name(c) for c in _all_components(tdoc, True)}
    done, failed = [], []
    for n in placed:
        sc_ = by_name.get(n)
        if sc_ is None:
            failed.append({"component": n, "error": "not found"})
            continue
        src_file = _path(sc_)
        tgt_file = pm.get(src_file)
        if not tgt_file:
            failed.append({"component": n, "error": f"no mapping for {src_file}"})
            continue
        want = f"{_base(tgt_file)}-{_instance_suffix(n)}"
        if want in existing:
            done.append({"component": n, "target": want, "skipped": "exists"})
            continue
        try:
            cfg = str(_try(lambda: slip._inv(sc_, "ReferencedConfiguration"), "") or "")
            x = _xf(sc_)
            comp = slip._inv(tdoc, "AddComponent5", tgt_file, 0, "", False, "", x[9], x[10], x[11])
            if comp is None:
                raise SWError("AddComponent5 returned None (is the file saved / path right?)")
            xf = slip._inv(mu, "CreateTransform", _doubles((x + [0.0] * 16)[:16]))
            slip._put(comp, "Transform2", xf)
            if cfg:
                _try(lambda: slip._put(comp, "ReferencedConfiguration", cfg))
            _try(lambda: slip._put(comp, "Name2", want))
            fixed_src = bool(_try(lambda: slip._inv(sc_, "IsFixed"), False))
            fixed_tgt = bool(_try(lambda: slip._inv(comp, "IsFixed"), False))
            if fixed_src != fixed_tgt:
                slip._inv(tdoc, "ClearSelection2", True)
                _select_comp(comp)
                slip._inv(tdoc, "FixComponent" if fixed_src else "UnfixComponent")
                slip._inv(tdoc, "ClearSelection2", True)
            done.append({"component": n, "target": _name(comp)})
        except Exception as ex:  # noqa: BLE001
            failed.append({"component": n, "error": str(ex)[:200]})
    _try(lambda: slip._inv(tdoc, "EditRebuild3"))
    return {"status": "done", "inserted": done, "failed": failed}


def _doubles(vals):
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in vals])


def _select_comp(comp, append=False, mark=0):
    import pythoncom
    from win32com.client import VARIANT
    try:
        return bool(slip._inv(comp, "Select4", append, VARIANT(pythoncom.VT_DISPATCH, None), False))
    except Exception:  # noqa: BLE001
        return bool(slip._inv(comp, "Select2", append, mark))


def _comp_map(sdoc, tdoc, pm):
    """source component (any depth) name -> target component, by mapped file + identical transform."""
    tcomps = _all_components(tdoc, False)
    tinfo = [(c, _path(c), _xf(c)) for c in tcomps]
    out, missing = {}, []
    tgt_base = {os.path.normcase(v): None for v in pm.values()}
    for c in _all_components(sdoc, False):
        n = _name(c)
        p = _path(c)
        want = pm.get(p)
        x = _xf(c)
        hit = None
        for tc, tp, tx in tinfo:
            if (want is None or tp == os.path.normcase(want)) and _xf_close(x, tx):
                if want is None and _base(tp).replace(" RB", "") != _base(p):
                    continue
                hit = tc
                break
        if hit is None:
            missing.append(n)
        else:
            out[n] = hit
    return out, missing, tgt_base


# --------------------------------------------------------------------------- mates

def _entity_kind(e):
    if _try(lambda: slip._inv(e, "GetSurface")) is not None:
        return "face"
    if _try(lambda: slip._inv(e, "GetCurveParams2")) is not None:
        return "edge"
    if _try(lambda: slip._inv(e, "GetPoint")) is not None:
        return "vertex"
    return "other"


def _same(app, a, b):
    try:
        return int(slip._inv(app, "IsSame", a, b)) == 1
    except Exception:  # noqa: BLE001
        return False


def _comp_entities(comp, kind):
    out = []
    for b in list(_try(lambda: slip._inv(comp, "GetBodies2", 0)) or ()):
        if kind == "face":
            out.extend(list(slip._inv(b, "GetFaces") or ()))
        elif kind == "edge":
            out.extend(list(slip._inv(b, "GetEdges") or ()))
        elif kind == "vertex":
            out.extend(list(slip._inv(b, "GetVertices") or ()))
    return out


def _geom_key(e, kind):
    if kind == "face":
        box = [round(float(v), 7) for v in slip._inv(e, "GetBox")]
        area = round(float(slip._inv(e, "GetArea")), 10)
        return tuple(box) + (area,)
    if kind == "edge":
        cp = [float(v) for v in slip._inv(e, "GetCurveParams2")][:6]
        mid = sc._point_on_edge(e)
        return tuple(round(v, 7) for v in cp + mid)
    p = [float(v) for v in slip._inv(e, "GetPoint")][:3]
    return tuple(round(v, 7) for v in p)


def _map_entity(app, tdoc, ment, cmap, report):
    """IMateEntity2 of the source -> entity of the target assembly (to select)."""
    comp = _try(lambda: slip._inv(ment, "ReferenceComponent"))
    ref = slip._inv(ment, "Reference")
    if comp is None:
        n = _try(lambda: str(slip._inv(ref, "Name")))
        f = slip._inv(tdoc, "FeatureByName", n) if n else None
        if f is None:
            raise SWError(f"assembly reference {n!r} not found in the target")
        return f, ("plane", n)
    cname = _name(comp)
    tcomp = cmap.get(cname)
    if tcomp is None:
        raise SWError(f"component {cname!r} has no target counterpart")
    kind = _entity_kind(ref)
    if kind == "other":
        n = _try(lambda: str(slip._inv(ref, "Name")))
        if not n:
            # IRefPlane / IRefAxis without a Name: walk the component's features and compare
            for f in _comp_features(comp):
                spec = _try(lambda f=f: slip._inv(f, "GetSpecificFeature2"))
                if spec is not None and (_same(app, spec, ref) or _same(app, f, ref)):
                    n = sc._fname(f)
                    break
        if not n:
            raise SWError(f"unrecognised reference of {cname!r}")
        f = slip._inv(tcomp, "FeatureByName", n)
        if f is None:
            raise SWError(f"{n!r} not found in {_name(tcomp)!r}")
        return f, ("plane", n)
    # find the same entity among the source component's body entities (component frame)
    src_list = _comp_entities(comp, kind)
    src_hit = None
    for e in src_list:
        if _same(app, e, ref):
            src_hit = e
            break
    if src_hit is None:
        raise SWError(f"{kind} of {cname!r} not found among its bodies")
    key = _geom_key(src_hit, kind)
    for e in _comp_entities(tcomp, kind):
        if _geom_key(e, kind) == key:
            return e, (kind, key)
    raise SWError(f"no matching {kind} in {_name(tcomp)!r}")


def _comp_features(comp):
    out = []
    f = _try(lambda: slip._inv(comp, "FirstFeature"))
    guard = 0
    while f is not None and guard < 2000:
        guard += 1
        out.append(f)
        f = _try(lambda f=f: slip._inv(f, "GetNextFeature"))
    return out


def _select_entity(tdoc, ent, mark):
    selmgr = slip._inv(tdoc, "SelectionManager")
    sd = slip._inv(selmgr, "CreateSelectData")
    slip._put(sd, "Mark", int(mark))
    try:
        return bool(slip._inv(ent, "Select4", True, sd))
    except Exception:  # noqa: BLE001
        return bool(slip._inv(ent, "Select2", True, int(mark)))


def clone_mates(source: str, part_map: dict, start: int = 0, count: int = 100000) -> dict:
    app, tdoc = _target()
    sdoc = sc._doc(source)
    pm = _norm_map(part_map)
    cmap, missing, _ = _comp_map(sdoc, tdoc, pm)
    mg = _mate_group(sdoc)
    mates = _subfeatures(mg) if mg is not None else []
    done, failed = [], []
    for k, mf in enumerate(mates[start:start + count], start):
        mname = sc._fname(mf)
        if _try(lambda: bool(slip._inv(mf, "IsSuppressed")), False):
            done.append({"mate": mname, "skipped": "suppressed"})
            continue
        try:
            if _try(lambda: slip._inv(tdoc, "FeatureByName", mname)) is not None:
                done.append({"mate": mname, "skipped": "exists"})
                continue
            m = slip._inv(mf, "GetSpecificFeature2")
            t = int(slip._inv(m, "Type"))
            align = int(slip._inv(m, "Alignment"))
            flipped = bool(_try(lambda: slip._inv(m, "Flipped"), False))
            n = int(slip._inv(m, "GetMateEntityCount"))
            ents = [_map_entity(app, tdoc, slip._inv(m, "MateEntity", i), cmap, None) for i in range(n)]
            value = None
            dd = _try(lambda: slip._inv(mf, "GetFirstDisplayDimension"))
            if dd is not None:
                value = float(slip._inv(slip._inv(dd, "GetDimension2", 0), "SystemValue"))
            slip._inv(tdoc, "ClearSelection2", True)
            md = slip._inv(tdoc, "CreateMateData", t)
            if md is None:
                raise SWError(f"CreateMateData({t}) returned None")
            objs = [e for e, _ in ents]
            if t == 11 and len(objs) == 4:                  # width
                slip._put(md, "WidthSelection", sm._variant_dispatch_array(objs[:2]))
                slip._put(md, "TabSelection", sm._variant_dispatch_array(objs[2:]))
            else:
                slip._put(md, "EntitiesToMate", sm._variant_dispatch_array(objs))
            _try(lambda: slip._put(md, "MateAlignment", align))
            if t == 5 and value is not None:
                slip._put(md, "Distance", value)
                _try(lambda: slip._put(md, "FlipDimension", flipped))
            if t == 6 and value is not None:
                slip._put(md, "Angle", value)
                _try(lambda: slip._put(md, "FlipDimension", flipped))
            before = sm._last_mate_name(tdoc)
            mate = slip._inv(tdoc, "CreateMate", md)
            if mate is None:
                raise SWError("CreateMate returned None")
            after = sm._last_mate_name(tdoc)
            if after and after != before:
                _try(lambda: slip._put(slip._inv(tdoc, "FeatureByName", after), "Name", mname))
            done.append({"mate": mname, "type": t, "align": align})
        except Exception as ex:  # noqa: BLE001
            failed.append({"index": k, "mate": mname, "error": str(ex)[:240]})
        finally:
            _try(lambda: slip._inv(tdoc, "ClearSelection2", True))
    _try(lambda: slip._inv(tdoc, "EditRebuild3"))
    return {"status": "done", "total": len(mates), "range": [start, min(len(mates), start + count)],
            "created": len([d for d in done if "type" in d]), "skipped": len([d for d in done if "skipped" in d]),
            "failed": failed, "unmapped_components": missing[:40]}


# --------------------------------------------------------------------------- pattern features

def dump_pattern_feature(source: str, feature: str) -> dict:
    """Debug: definition properties of a mirror / pattern feature of the source assembly."""
    sdoc = sc._doc(source)
    f = slip._inv(sdoc, "FeatureByName", feature)
    d = slip._inv(f, "GetDefinition")
    from win32com.client import VARIANT
    import pythoncom
    _try(lambda: slip._inv(d, "AccessSelections", sdoc, VARIANT(pythoncom.VT_DISPATCH, None)))
    out = {"type": sc._ftype(f), "interface": sw_w._object_interface(d)}
    try:
        out["props"] = sw_w._dump_props(d, 1, out["interface"])
    finally:
        _try(lambda: slip._inv(d, "ReleaseSelectionAccess"))
    return out


def verify(source: str, part_map: dict) -> dict:
    app, tdoc = _target()
    sdoc = sc._doc(source)
    cmap, missing, _ = _comp_map(sdoc, tdoc, _norm_map(part_map))
    s_top = _all_components(sdoc, True)
    t_top = _all_components(tdoc, True)
    return {"status": "done", "source_components": len(_all_components(sdoc, False)),
            "target_components": len(_all_components(tdoc, False)),
            "source_top": len(s_top), "target_top": len(t_top),
            "matched": len(cmap), "unmatched_source": missing[:60]}
