"""Ad-hoc read-only probes (dev hatch)."""
from sw_connection import SWConnection
from tools.annotation import _com_prop_or_method


def drawing_tree(max_depth: int = 4) -> list:
    d = SWConnection.get_instance().get_app().ActiveDoc
    out = []

    def walk(f, depth):
        while f is not None:
            try:
                out.append(("  " * depth) + f"{f.GetTypeName2} | {f.Name}")
            except Exception as ex:  # noqa: BLE001
                out.append(f"err {ex}")
                break
            if depth < max_depth:
                try:
                    walk(_com_prop_or_method(f, "GetFirstSubFeature"), depth + 1)
                except Exception:  # noqa: BLE001
                    pass
            try:
                f = _com_prop_or_method(f, "GetNextSubFeature") if depth else f.GetNextFeature
            except Exception:  # noqa: BLE001
                break

    walk(d.FirstFeature, 0)
    return [x for x in out if "View" in x or "DrSheet" in x or "Section" in x]


def section_info(view_name: str = "Drawing View5", parent: str = "Drawing View3") -> dict:
    from tools import slip
    from tools.annotation import _get_drawing_views
    d = SWConnection.get_instance().get_app().ActiveDoc
    out = {}
    v = _get_drawing_views(d, view_name)[0][1]
    p = _get_drawing_views(d, parent)[0][1]
    for nm in ("GetFaceHatchCount", "GetSectionLineCount2", "Type", "Name", "GetDisplayMode2", "IsEmphasizeOutline"):
        try:
            out[f"v.{nm}"] = slip._inv(v, nm) if nm != "GetSectionLineCount2" else None
        except Exception as ex:  # noqa: BLE001
            out[f"v.{nm}"] = f"err {ex}"
    for nm in ("GetSectionLineCount2",):
        try:
            out[f"p.{nm}"] = slip._inv(p, nm, 0)
        except Exception as ex:  # noqa: BLE001
            out[f"p.{nm}"] = f"err {ex}"
    try:
        sec = slip._wrap(slip._inv(v, "GetSection"))
        out["sec"] = str(type(sec))
        for nm in ("GetLabel", "GetLayer", "GetHidden", "Layer", "GetSectionView", "GetLineInfo", "GetDisplay", "GetTextFormat", "IsAligned", "GetPartialSection", "GetScaleWithModelChanges", "GetReversedCutDirection"):
            try:
                r = slip._inv(sec, nm)
                out["sec." + nm] = str(r)[:200]
            except Exception as ex:  # noqa: BLE001
                out["sec." + nm] = f"err {str(ex)[:80]}"
    except Exception as ex:  # noqa: BLE001
        out["sec_err"] = str(ex)
    return out


def delete_by_name(name: str, sel_type: str) -> dict:
    from tools import slip
    d = slip._active_drawing()
    ok = slip._select(d, name, sel_type)
    if ok:
        slip._edit_delete(d)
    return {"selected": ok, "tree": drawing_tree()}


def view_sketch_segments(view_feature: str = "Drawing View3", delete: bool = False) -> dict:
    from tools import slip
    from tools.annotation import _get_drawing_views
    d = slip._active_drawing()
    v = _get_drawing_views(d, view_feature)[0][1]
    sk = slip._wrap(slip._inv(v, "GetSketch"))
    segs = slip._inv(sk, "GetSketchSegments") or ()
    out = {"count": len(segs)}
    if delete and segs:
        d.ActivateView(str(slip._inv(v, "Name")))
        d.ClearSelection2(True)
        n = 0
        for s in segs:
            try:
                n += bool(slip._inv(slip._wrap(s), "Select4", True, None))
            except Exception:  # noqa: BLE001
                try:
                    n += bool(slip._wrap(s).Select(True))
                except Exception:  # noqa: BLE001
                    pass
        out["selected"] = n
        slip._edit_delete(d)
        sk = slip._wrap(slip._inv(v, "GetSketch"))
        out["after"] = len(slip._inv(sk, "GetSketchSegments") or ())
    return out


def section_props(view_feature: str = None) -> dict:
    from tools import slip
    from tools.annotation import _get_drawing_views
    d = slip._active_drawing()
    out = {"tree": drawing_tree()}
    secs = [n for n, _ in _get_drawing_views(d, None) if n not in ("Drawing View3", "Drawing View4")]
    out["sec_views"] = secs
    name = view_feature or (secs[0] if secs else None)
    if not name:
        return out
    v = _get_drawing_views(d, name)[0][1]
    out["view"] = str(slip._inv(v, "Name"))
    out["hatches"] = slip._inv(v, "GetFaceHatchCount")
    sec = slip._wrap(slip._inv(v, "GetSection"))
    for nm in ("SectionDepth", "GetDisplayOnlySurfaceCut", "GetAutoHatch", "CutSurfaceBodies", "DisplaySurfaceBodies",
               "GetArrowInfo", "GetTextInfo", "IGetLineSegmentCount", "ExcludeFasteners", "GetDisplayOnlySpeedPakBodies",
               "ExcludeSliceSectionBodies", "CuttingLineShoulders"):
        try:
            out[nm] = str(slip._inv(sec, nm))[:200]
        except Exception as ex:  # noqa: BLE001
            out[nm] = f"err {str(ex)[:60]}"
    return out
