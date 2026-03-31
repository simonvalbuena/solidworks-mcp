"""標註 tools — 匯入模型尺寸。"""

from __future__ import annotations

import json
import logging
import math

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

# swUserPreferenceToggle_e
SW_INPUT_DIM_VAL_ON_CREATE = 220

# swLengthUnit_e
SW_UNIT_MM = 0

# swFractionDisplay_e
SW_FRACTION_DECIMAL = 1

# swDimensionPrecisionSettings_e
SW_PRECISION_UNCHANGED = -1

# swInsertAnnotation_e 列舉值（bitmask）
SW_INSERT_DIMENSIONS = 8                          # 0x8
SW_INSERT_DIMS_MARKED_FOR_DRAWING = 32768          # 0x8000
SW_INSERT_DIMS_NOT_MARKED_FOR_DRAWING = 524288     # 0x80000
SW_INSERT_HOLE_WIZARD_PROFILE = 65536              # 0x10000
SW_INSERT_HOLE_WIZARD_LOCATION = 131072            # 0x20000
SW_INSERT_HOLE_CALLOUT = 1048576                   # 0x100000

# 匯入所有尺寸相關標註的 bitmask 組合
SW_INSERT_ALL_DIMS = (
    SW_INSERT_DIMENSIONS
    | SW_INSERT_DIMS_MARKED_FOR_DRAWING
    | SW_INSERT_DIMS_NOT_MARKED_FOR_DRAWING
    | SW_INSERT_HOLE_WIZARD_PROFILE
    | SW_INSERT_HOLE_WIZARD_LOCATION
    | SW_INSERT_HOLE_CALLOUT
)

# Create1stAngleViews2 在不同語系 SW 產生的視圖名稱模式
_VIEW_NAME_CANDIDATES = [
    # 中文版 SW（常見自動命名）
    "工程視圖1", "工程視圖2", "工程視圖3", "工程視圖4",
    "工程圖檢視1", "工程圖檢視2", "工程圖檢視3", "工程圖檢視4",
    "繪圖視圖1", "繪圖視圖2", "繪圖視圖3", "繪圖視圖4",
    # 英文版 SW
    "Drawing View1", "Drawing View2", "Drawing View3", "Drawing View4",
]


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def insert_model_dimensions(
        view_name: str | None = None,
        dimension_type: str = "all",
    ) -> str:
        """匯入模型尺寸到 Drawing 視圖。
        view_name: 指定視圖名稱，預設全部視圖。
        dimension_type: 篩選類型 all/marked/reference，預設 all。"""
        try:
            result = await sw.execute(
                _insert_model_dimensions,
                view_name,
                dimension_type,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_model_dimensions 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_model_dimensions 未預期錯誤: {e}")

    @mcp.tool()
    async def probe_drawing_edges(
        view_name: str | None = None,
    ) -> str:
        """診斷工具：測試 GetVisibleEntities2 和 IEdge 方法是否可用。
        view_name: 指定視圖名稱，預設第一個視圖。"""
        try:
            result = await sw.execute(_probe_drawing_edges, view_name)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"probe_drawing_edges 失敗: {e}")
        except Exception as e:
            raise ToolError(f"probe_drawing_edges 未預期錯誤: {e}")

    @mcp.tool()
    async def auto_add_reference_dimensions(
        view_name: str | None = None,
        phase: str = "all",
        offset_mm: float = 15.0,
    ) -> str:
        """自動添加參考尺寸到 Drawing 視圖。
        適用於無參數化尺寸的零件（模具、匯入幾何）。
        view_name: 指定視圖名稱，預設全部視圖。
        phase: "bbox"（外形尺寸）/ "circles"（圓形）/ "all"（全部）。
        offset_mm: 尺寸文字偏移量（mm）。"""
        try:
            result = await sw.execute(
                _auto_add_ref_dims, view_name, phase, offset_mm,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"auto_add_reference_dimensions 失敗: {e}")
        except Exception as e:
            raise ToolError(f"auto_add_reference_dimensions 未預期錯誤: {e}")


def _safe_get(obj, name):
    """取得 COM 屬性/方法回傳值，處理 pywin32 方法/屬性歧義。"""
    try:
        attr = getattr(obj, name)
    except Exception:
        return None
    if callable(attr):
        try:
            return attr()
        except Exception:
            return None
    return attr


def _discover_view_names(drawing) -> list[str]:
    """用 ActivateView 暴力搜索找出存在的視圖名稱。"""
    found = []
    for name in _VIEW_NAME_CANDIDATES:
        try:
            ok = drawing.ActivateView(name)
            if ok:
                found.append(name)
                logger.info("發現視圖: %s", name)
        except Exception:
            pass
    return found


_VIEW_FEATURE_TYPES = ("AbsoluteView", "UnfoldedView", "DrDrawingView")


def _com_prop_or_method(obj, name):
    """嘗試屬性存取，驗證有 _oleobj_；失敗則用方法呼叫。"""
    try:
        val = getattr(obj, name)
        if val is not None and hasattr(val, '_oleobj_'):
            return val
    except Exception:
        pass
    try:
        return getattr(obj, name)()
    except Exception:
        return None


def _get_drawing_views(
    drawing, view_name: str | None = None,
) -> list[tuple[str, object]]:
    """透過 FeatureTree 取得 Drawing 中的 IView 物件列表。

    回傳 [(view_name, view_obj), ...]。
    GetFirstView 在 pywin32 EnsureDispatch 下不可用，
    改用 FirstFeature → DrSheet → GetFirstSubFeature 遍歷。
    """
    results = []
    feat = drawing.FirstFeature  # 屬性
    while feat is not None:
        try:
            type_name = feat.GetTypeName2  # 屬性
        except Exception as exc:
            logger.debug("GetTypeName2 失敗: %s", exc)
            break
        if type_name == "DrSheet":
            sub = _com_prop_or_method(feat, "GetFirstSubFeature")
            while sub is not None:
                try:
                    st = sub.GetTypeName2  # 屬性
                    sn = sub.Name  # 屬性
                except Exception as exc:
                    logger.debug("sub feature 屬性存取失敗: %s", exc)
                    break
                if st in _VIEW_FEATURE_TYPES:
                    if view_name is None or sn == view_name:
                        try:
                            view_obj = sub.GetSpecificFeature2()
                        except Exception:
                            try:
                                view_obj = sub.GetSpecificFeature2
                            except Exception as exc:
                                logger.debug("GetSpecificFeature2 失敗: %s", exc)
                                view_obj = None
                        if view_obj is not None:
                            results.append((sn, view_obj))
                sub = _com_prop_or_method(sub, "GetNextSubFeature")
        try:
            feat = feat.GetNextFeature  # 屬性
        except Exception as exc:
            logger.debug("GetNextFeature 失敗: %s", exc)
            break
    return results


_DIR_THRESHOLD = 0.1  # 方向向量分量門檻


def _classify_edges(edges) -> dict:
    """分類邊線為水平線、垂直線、完整圓、其他。

    回傳 {
        "horizontal": [(edge, start, end), ...],
        "vertical": [(edge, start, end), ...],
        "circles": [(edge, center, radius), ...],
        "other": int,
    }
    """
    horizontal = []
    vertical = []
    circles = []
    other = 0

    for edge in edges:
        try:
            curve = edge.GetCurve  # 屬性(dispatch)
        except Exception:
            other += 1
            continue

        try:
            is_line = curve.IsLine  # 屬性
        except Exception:
            is_line = False
        try:
            is_circle = curve.IsCircle  # 屬性
        except Exception:
            is_circle = False

        if is_line:
            try:
                params = curve.LineParams  # 屬性: (px, py, pz, dx, dy, dz)
                dx, dy, dz = params[3], params[4], params[5]
            except Exception:
                other += 1
                continue

            # 方向向量正規化後判斷
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length < 1e-12:
                other += 1
                continue
            ndx, ndy, ndz = abs(dx / length), abs(dy / length), abs(dz / length)

            start = end = None
            try:
                sv = edge.GetStartVertex  # 屬性
                if sv is not None:
                    pt = sv.GetPoint  # 屬性，COM SAFEARRAY
                    start = (pt[0], pt[1], pt[2])
                ev = edge.GetEndVertex  # 屬性
                if ev is not None:
                    pt = ev.GetPoint  # 屬性，COM SAFEARRAY
                    end = (pt[0], pt[1], pt[2])
            except Exception:
                pass

            if ndy < _DIR_THRESHOLD and ndz < _DIR_THRESHOLD:
                # X 方向為主 → 水平線
                horizontal.append((edge, start, end))
            elif ndx < _DIR_THRESHOLD and ndz < _DIR_THRESHOLD:
                # Y 方向為主 → 垂直線
                vertical.append((edge, start, end))
            else:
                other += 1

        elif is_circle:
            # 判斷完整圓 vs 圓弧
            try:
                sv = edge.GetStartVertex  # 屬性
            except Exception:
                sv = None
            if sv is None:
                # 完整圓
                try:
                    cp = curve.CircleParams  # 屬性: (cx,cy,cz, ax,ay,az, radius)
                    center = (cp[0], cp[1], cp[2])
                    radius = cp[6]
                    circles.append((edge, center, radius))
                except Exception:
                    other += 1
            else:
                other += 1  # 圓弧，Phase 1 跳過
        else:
            other += 1

    return {
        "horizontal": horizontal,
        "vertical": vertical,
        "circles": circles,
        "other": other,
    }


def _find_bounding_edges(classified: dict) -> dict:
    """從分類後的邊線找最外對。

    回傳 {
        "h_top": (edge, start, end) or None,      — Y 最大的水平線
        "h_bottom": (edge, start, end) or None,    — Y 最小的水平線
        "v_left": (edge, start, end) or None,      — X 最小的垂直線
        "v_right": (edge, start, end) or None,     — X 最大的垂直線
    }
    """
    result = {"h_top": None, "h_bottom": None, "v_left": None, "v_right": None}

    h_lines = classified.get("horizontal", [])
    if len(h_lines) >= 2:
        def h_y(item):
            _, start, end = item
            if start is not None:
                return start[1]
            return 0.0
        sorted_h = sorted(h_lines, key=h_y)
        result["h_bottom"] = sorted_h[0]
        result["h_top"] = sorted_h[-1]
    elif len(h_lines) == 1:
        result["h_top"] = h_lines[0]
        result["h_bottom"] = h_lines[0]

    v_lines = classified.get("vertical", [])
    if len(v_lines) >= 2:
        def v_x(item):
            _, start, end = item
            if start is not None:
                return start[0]
            return 0.0
        sorted_v = sorted(v_lines, key=v_x)
        result["v_left"] = sorted_v[0]
        result["v_right"] = sorted_v[-1]
    elif len(v_lines) == 1:
        result["v_left"] = v_lines[0]
        result["v_right"] = v_lines[0]

    return result


_RADIUS_TOL = 1e-4  # 0.1mm — 同半徑去重容差


def _dedupe_circles(
    circles: list[tuple],
) -> list[tuple]:
    """同半徑只保留離幾何重心最遠的圓。

    circles: [(edge, (cx,cy,cz), radius), ...]
    回傳: [(edge, (cx,cy,cz), radius), ...]  去重後
    """
    if not circles:
        return []

    # 按 radius 分組（容差 _RADIUS_TOL）
    groups: dict[float, list] = {}
    for item in circles:
        _, _center, r = item
        matched = False
        for key_r in groups:
            if abs(r - key_r) < _RADIUS_TOL:
                groups[key_r].append(item)
                matched = True
                break
        if not matched:
            groups[r] = [item]

    # 幾何重心
    all_cx = sum(c[0] for _, c, _ in circles) / len(circles)
    all_cy = sum(c[1] for _, c, _ in circles) / len(circles)

    # 每組取離重心最遠的
    result = []
    for _r, items in groups.items():
        farthest = max(
            items,
            key=lambda it: (it[1][0] - all_cx) ** 2 + (it[1][1] - all_cy) ** 2,
        )
        result.append(farthest)

    return result


def _get_view_edges(view_obj) -> list:
    """從 IView 取得可見邊線列表。

    遍歷所有 visible component 收集邊線。
    必須傳入 component（從 GetVisibleComponents 取得），
    傳 None 會 DISP_E_TYPEMISMATCH。
    """
    try:
        comps = view_obj.GetVisibleComponents  # 屬性
    except Exception:
        logger.warning("GetVisibleComponents 失敗")
        return []
    if comps is None:
        return []
    if not isinstance(comps, (tuple, list)):
        comps = (comps,)
    if len(comps) == 0:
        return []
    all_edges = []
    for comp in comps:
        try:
            edges = view_obj.GetVisibleEntities2(comp, 1)  # swViewEntityType_Edge=1
        except Exception:
            continue
        if edges is None:
            continue
        if isinstance(edges, (tuple, list)):
            all_edges.extend(edges)
        else:
            i = 0
            while True:
                try:
                    all_edges.append(edges[i])
                    i += 1
                except Exception:
                    break
    return all_edges


def _insert_model_dimensions(
    view_name: str | None,
    dimension_type: str,
) -> dict:
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    # 決定要匯入的標註類型 bitmask
    type_map = {
        "all": SW_INSERT_ALL_DIMS,
        "marked": SW_INSERT_DIMS_MARKED_FOR_DRAWING,
        "reference": SW_INSERT_DIMENSIONS,
    }
    types_bitmask = type_map.get(dimension_type.lower(), SW_INSERT_ALL_DIMS)

    # 取得視圖列表
    view_names = _discover_view_names(drawing)
    if not view_names:
        raise SWError("找不到任何工程圖視圖")

    results = []
    for vname in view_names:
        if view_name and vname != view_name:
            continue

        # ActivateView
        try:
            drawing.ActivateView(vname)
        except Exception as e:
            logger.warning("ActivateView(%s) 失敗: %s", vname, e)

        # InsertModelAnnotations3（AllViews=False，只對當前視圖）
        inserted = False
        ann_count = 0
        method_used = None
        errors = []

        try:
            annotations = drawing.InsertModelAnnotations3(
                0,               # swImportModelItemsFromEntireModel
                types_bitmask,   # 尺寸相關 bitmask
                False,           # AllViews=False
                False,           # DuplicateDims=False（允許重複）
                True,            # HiddenFeatureDims=True
                True,            # UsePlacementInSketch=True
            )
            if annotations is not None:
                try:
                    ann_count = len(annotations)
                except Exception:
                    ann_count = 1 if annotations else 0
            inserted = True
            method_used = "InsertModelAnnotations3"
            logger.info("InsertModelAnnotations3 view=%s, bitmask=%d, annotations=%d",
                        vname, types_bitmask, ann_count)
        except Exception as e:
            errors.append(f"InsertModelAnnotations3(bitmask={types_bitmask}): {e}")
            logger.warning("InsertModelAnnotations3 失敗: %s", e)

        # 備援: InsertModelDimensions
        if not inserted:
            try:
                drawing.InsertModelDimensions(0)
                inserted = True
                method_used = "InsertModelDimensions"
            except Exception as e:
                errors.append(f"InsertModelDimensions: {e}")

        results.append({
            "view": vname,
            "inserted": inserted,
            "annotations_count": ann_count,
            "method": method_used,
            "errors": errors if errors else None,
        })

    return {
        "views_discovered": view_names,
        "types_bitmask": types_bitmask,
        "results": results,
        "status": "done",
    }


def _probe_drawing_edges(view_name: str | None) -> dict:
    """診斷：測試 GetVisibleEntities2 + IEdge 方法鏈。"""
    import pythoncom

    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    # 找視圖
    view_names = _discover_view_names(drawing)
    if not view_names:
        raise SWError("找不到任何工程圖視圖")

    target = view_name or view_names[0]
    drawing.ActivateView(target)

    # 測試 1: 取得 IView 物件
    probes = {"target_view": target, "tests": {}}

    view_obj = None
    all_view_names_in_chain = []

    # 方法 A: _safe_get (可能被 early-binding 擋住)
    try:
        v = _safe_get(drawing, "GetFirstView")
        while v is not None:
            vname = _safe_get(v, "GetName2")
            all_view_names_in_chain.append(vname)
            if vname == target:
                view_obj = v
                break
            v = _safe_get(v, "GetNextView")
        probes["tests"]["method_A_safe_get"] = (
            f"OK" if view_obj
            else f"鏈結名稱={all_view_names_in_chain}"
        )
    except Exception as e:
        probes["tests"]["method_A_safe_get"] = f"FAIL: {e}"

    # 方法 B: 強制 late-binding dispatch
    if view_obj is None:
        import win32com.client
        try:
            drawing_late = win32com.client.Dispatch(drawing._oleobj_)
            v = drawing_late.GetFirstView()
            late_names = []
            while v is not None:
                vname = v.GetName2()
                late_names.append(vname)
                if vname == target:
                    view_obj = v
                    break
                v = v.GetNextView()
            probes["tests"]["method_B_late_binding"] = (
                f"OK" if view_obj
                else f"鏈結名稱={late_names}"
            )
        except Exception as e:
            probes["tests"]["method_B_late_binding"] = f"FAIL: {e}"

    # 方法 C: 透過 FeatureTree（繞過 _safe_get，直接測試）
    if view_obj is None:
        # C1: 當屬性存取
        try:
            feat = drawing.FirstFeature
            probes["tests"]["C1_FirstFeature_prop"] = (
                f"type={type(feat).__name__}" if feat else "None"
            )
        except Exception as e:
            probes["tests"]["C1_FirstFeature_prop"] = f"FAIL: {e}"
            feat = None

        # C2: 當方法呼叫
        if feat is None:
            try:
                feat = drawing.FirstFeature()
                probes["tests"]["C2_FirstFeature_call"] = (
                    f"type={type(feat).__name__}" if feat else "None"
                )
            except Exception as e:
                probes["tests"]["C2_FirstFeature_call"] = f"FAIL: {e}"
                feat = None

        # 遍歷 feature tree（含子特徵）— 全部用屬性存取
        if feat is not None:
            feat_names = []
            try:
                while feat is not None:
                    try:
                        type_name = feat.GetTypeName2
                    except Exception:
                        type_name = "?"
                    try:
                        feat_name = feat.Name
                    except Exception:
                        feat_name = "?"
                    feat_names.append(f"{feat_name}({type_name})")

                    # DrSheet 下有子特徵（視圖）
                    if type_name == "DrSheet":
                        try:
                            sub = feat.GetFirstSubFeature
                        except Exception:
                            try:
                                sub = feat.GetFirstSubFeature()
                            except Exception:
                                sub = None
                        while sub is not None:
                            try:
                                st = sub.GetTypeName2
                            except Exception:
                                st = "?"
                            try:
                                sn = sub.Name
                            except Exception:
                                sn = "?"
                            feat_names.append(f"  └ {sn}({st})")
                            if st in ("AbsoluteView", "UnfoldedView",
                                      "DrDrawingView") and sn == target:
                                try:
                                    view_obj = sub.GetSpecificFeature2()
                                except Exception:
                                    try:
                                        view_obj = sub.GetSpecificFeature2
                                    except Exception:
                                        pass
                                break
                            try:
                                sub = sub.GetNextSubFeature
                            except Exception:
                                try:
                                    sub = sub.GetNextSubFeature()
                                except Exception:
                                    sub = None
                        if view_obj is not None:
                            break

                    try:
                        feat = feat.GetNextFeature
                    except Exception:
                        try:
                            feat = feat.GetNextFeature()
                        except Exception:
                            feat = None

                probes["tests"]["C_feature_tree"] = (
                    f"OK, got IView"
                    if view_obj
                    else f"features={feat_names[:25]}"
                )
            except Exception as e:
                probes["tests"]["C_feature_tree"] = (
                    f"FAIL: {e}, partial={feat_names[:15]}"
                )

    if view_obj is None:
        probes["tests"]["conclusion"] = "無法取得 IView 物件，後續測試跳過"
        return probes

    # 測試 2: GetVisibleEntities / GetVisibleEntities2
    edges = None

    # 2a: 取得 view 的 component（零件圖可能需要傳入）
    comp = None
    try:
        # 可能是屬性或方法
        comps = view_obj.GetVisibleComponents
        if callable(comps):
            comps = comps()
        if comps and len(comps) > 0:
            comp = comps[0]
            probes["tests"]["GetVisibleComponents"] = (
                f"OK, count={len(comps)}, type={type(comp).__name__}"
            )
        else:
            probes["tests"]["GetVisibleComponents"] = "空或 None"
    except Exception as e:
        probes["tests"]["GetVisibleComponents"] = f"FAIL: {e}"

    # 2b: 嘗試各種 GetVisibleEntities2 參數組合
    attempts = [
        ("comp", comp),
        ("None", None),
        ("Empty", pythoncom.Empty),
    ]
    for arg_name, first_arg in attempts:
        try:
            edges = view_obj.GetVisibleEntities2(first_arg, 1)
            if edges is not None:
                try:
                    edge_count = len(edges)
                except Exception:
                    edge_count = "不可迭代"
                probes["tests"][f"GetVisibleEntities2({arg_name})"] = (
                    f"OK, count={edge_count}"
                )
                break
            else:
                probes["tests"][f"GetVisibleEntities2({arg_name})"] = "回傳 None"
        except Exception as e:
            probes["tests"][f"GetVisibleEntities2({arg_name})"] = f"FAIL: {e}"

    # 2c: 嘗試 GetVisibleEntities（帶 entityType 參數）
    if edges is None:
        try:
            edges = view_obj.GetVisibleEntities(1)  # swViewEntityType_Edge
            if edges is not None:
                try:
                    edge_count = len(edges)
                except Exception:
                    edge_count = "不可迭代"
                probes["tests"]["GetVisibleEntities(1)"] = f"OK, count={edge_count}"
            else:
                probes["tests"]["GetVisibleEntities(1)"] = "回傳 None"
        except Exception as e:
            probes["tests"]["GetVisibleEntities(1)"] = f"FAIL: {e}"

    # 2d: 嘗試 GetPolylines 系列
    if edges is None:
        for method_name in ("GetPolylines7", "GetPolylines6",
                            "GetPolylines5", "GetPolylines4"):
            try:
                method = getattr(view_obj, method_name)
                if callable(method):
                    polylines = method()
                else:
                    polylines = method
                if polylines is not None:
                    probes["tests"][method_name] = f"OK, len={len(polylines)}"
                    break
                else:
                    probes["tests"][method_name] = "回傳 None"
            except Exception as e:
                probes["tests"][method_name] = f"FAIL: {e}"

    if edges is None or (isinstance(edges, (list, tuple)) and len(edges) == 0):
        probes["tests"]["conclusion"] = "無法取得邊線"
        return probes

    # 測試 3: IEdge 方法（取前 3 條邊）
    def _com_get(obj, name):
        """嘗試屬性存取和方法呼叫兩種方式。"""
        # 先當屬性
        try:
            val = getattr(obj, name)
            # 如果拿到的不是 callable，直接回傳
            if not callable(val):
                return val, "prop"
            # 拿到 callable，可能是方法或 COM dispatch 物件
            # 先檢查是否像 COM 物件（有 _oleobj_）
            if hasattr(val, '_oleobj_'):
                return val, "prop(dispatch)"
            # 否則當方法呼叫
            result = val()
            return result, "method()"
        except Exception as e1:
            pass
        # 再試直接呼叫
        try:
            val = getattr(obj, name)()
            return val, "call()"
        except Exception:
            pass
        return None, "NONE"

    edge_samples = []
    sample_edges = edges[:5] if len(edges) > 5 else edges

    for i, edge in enumerate(sample_edges):
        sample = {"index": i, "edge_type": type(edge).__name__}

        # 列出 edge 物件上的屬性/方法
        try:
            edge_attrs = [a for a in dir(edge) if not a.startswith('_')][:20]
            sample["attrs"] = edge_attrs
        except Exception:
            sample["attrs"] = "無法列舉"

        # GetCurve
        curve = None
        curve_val, curve_how = _com_get(edge, "GetCurve")
        if curve_val is not None:
            curve = curve_val
            sample["GetCurve"] = f"OK via {curve_how}, type={type(curve).__name__}"
        else:
            sample["GetCurve"] = "全部失敗"

        # IsLine / IsCircle
        if curve is not None:
            for prop in ("IsLine", "IsCircle"):
                val, how = _com_get(curve, prop)
                sample[prop] = f"{val} via {how}"

            # LineParams / CircleParams
            for prop in ("LineParams", "CircleParams"):
                val, how = _com_get(curve, prop)
                if val is not None:
                    try:
                        sample[prop] = f"{list(val)[:6]} via {how}"
                    except Exception:
                        sample[prop] = f"{val} via {how}"

        # GetStartVertex / GetEndVertex
        for vmethod in ("GetStartVertex", "GetEndVertex"):
            vertex, how = _com_get(edge, vmethod)
            if vertex is not None:
                pt, pt_how = _com_get(vertex, "GetPoint")
                if pt is not None:
                    try:
                        sample[vmethod] = list(pt)[:3]
                    except Exception:
                        sample[vmethod] = f"{pt} via {pt_how}"
                else:
                    sample[vmethod] = f"vertex OK but GetPoint failed"
            else:
                sample[vmethod] = f"None via {how}"

        edge_samples.append(sample)

    probes["tests"]["edge_samples"] = edge_samples

    # 測試 4: IView.GetOutline
    try:
        outline = _safe_get(view_obj, "GetOutline")
        if outline is not None:
            probes["tests"]["GetOutline"] = list(outline)
        else:
            probes["tests"]["GetOutline"] = "回傳 None"
    except Exception as e:
        probes["tests"]["GetOutline"] = f"FAIL: {e}"

    # 測試 5: ModelToViewTransform
    try:
        xform = _safe_get(view_obj, "ModelToViewTransform")
        probes["tests"]["ModelToViewTransform"] = (
            "OK" if xform is not None else "回傳 None"
        )
    except Exception as e:
        probes["tests"]["ModelToViewTransform"] = f"FAIL: {e}"

    # 測試 6: IView.SelectEntity（用第一條邊）
    try:
        drawing.ClearSelection2(True)
    except Exception:
        pass
    try:
        ok = view_obj.SelectEntity(edges[0], False)
        probes["tests"]["SelectEntity"] = f"OK, returned={ok}"
    except Exception as e:
        probes["tests"]["SelectEntity"] = f"FAIL: {e}"

    probes["tests"]["conclusion"] = "所有測試完成"
    return probes


DIM_OFFSET_BASE = 0.015   # 15mm
DIM_OFFSET_STACK = 0.010  # 10mm

# swSmartDimensionDirection_e
SW_DIM_DOWN = 0
SW_DIM_UP = 1
SW_DIM_RIGHT = 2
SW_DIM_LEFT = 3


def _auto_add_ref_dims(
    view_name: str | None,
    phase: str,
    offset_mm: float,
) -> dict:
    try:
        return _auto_add_ref_dims_inner(view_name, phase, offset_mm)
    except Exception as e:
        return {"status": "error", "error": str(e), "error_type": type(e).__name__}


def _auto_add_ref_dims_inner(
    view_name: str | None,
    phase: str,
    offset_mm: float,
) -> dict:
    step = "init"
    try:
        step = "get_app"
        sw_conn = SWConnection.get_instance()
        app = sw_conn.get_app()
        drawing = app.ActiveDoc

        if drawing is None:
            return {"status": "error", "step": step, "error": "no drawing"}

        step = "check_type"
        doc_type = drawing.GetType
        if doc_type is not None and doc_type != 3:
            return {"status": "error", "step": step, "error": f"type={doc_type}"}

        offset = offset_mm / 1000.0

        step = "get_views"
        views = _get_drawing_views(drawing, view_name)
        if not views:
            return {"status": "error", "step": step, "error": "no views"}

        do_bbox = phase in ("bbox", "all")
        do_circles = phase in ("circles", "all")
        total_dims = 0
        details = []

        for vi, (vname, view_obj) in enumerate(views):
            step = f"activate_view_{vi}_{vname}"
            drawing.ActivateView(vname)

            step = f"get_edges_{vi}"
            edges = _get_view_edges(view_obj)
            if not edges:
                details.append({"view": vname, "error": "無可見邊線"})
                continue

            step = f"classify_{vi}"
            classified = _classify_edges(edges)

            step = f"build_detail_{vi}"
            view_detail = {
                "view": vname,
                "edges_found": {
                    "lines_h": len(classified["horizontal"]),
                    "lines_v": len(classified["vertical"]),
                    "circles": len(classified["circles"]),
                    "other": classified["other"],
                },
                "dims_added": [],
            }

            # GetOutline 共用（bbox 和 circles 都需要）
            if do_bbox or do_circles:
                step = f"get_outline_{vi}"
                try:
                    raw_outline = view_obj.GetOutline
                    if raw_outline is not None:
                        outline = [raw_outline[0], raw_outline[1],
                                   raw_outline[2], raw_outline[3]]
                    else:
                        outline = [0, 0, 0.2, 0.2]
                except Exception:
                    outline = [0, 0, 0.2, 0.2]

            if do_bbox:
                step = f"find_bounding_{vi}"
                bounding = _find_bounding_edges(classified)

                step = f"add_dims_{vi}"
                bbox_dims = _add_bbox_dims(
                    drawing, view_obj, bounding, outline, offset,
                )
                view_detail["dims_added"].extend(bbox_dims)
                total_dims += sum(1 for d in bbox_dims if d.get("ok"))

            if do_circles:
                step = f"dedupe_circles_{vi}"
                deduped = _dedupe_circles(classified["circles"])
                view_detail["circles_before_dedup"] = len(classified["circles"])
                view_detail["circles_after_dedup"] = len(deduped)

                if deduped:
                    step = f"add_circle_dims_{vi}"
                    circle_dims = _add_circle_dims(
                        drawing, view_obj, deduped, outline, offset,
                    )
                    view_detail["dims_added"].extend(circle_dims)
                    total_dims += sum(1 for d in circle_dims if d.get("ok"))

            details.append(view_detail)

        step = "return"
        return {
            "status": "done",
            "views_processed": [v[0] for v in views],
            "dimensions_added": total_dims,
            "details": details,
        }
    except Exception as e:
        return {"status": "error", "step": step, "error": str(e),
                "error_type": type(e).__name__}

    return {
        "status": "done",
        "views_processed": [v[0] for v in views],
        "dimensions_added": total_dims,
        "details": details,
    }


def _add_bbox_dims(
    drawing, view_obj, bounding: dict, outline: list, offset: float,
) -> list:
    """選取外圍邊線對，放置 bbox 尺寸。回傳已加尺寸的描述列表。"""
    dims = []
    sub_step = "get_ext"

    try:
        ext = drawing.Extension
        if ext is None:
            return [{"error": "drawing.Extension 回傳 None"}]
    except Exception as e:
        return [{"error": f"drawing.Extension 失敗: {e}"}]

    sub_step = "set_pref"
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception as e:
        dims.append({"warning": f"swInputDimValOnCreate: {e}"})

    def _try_add_dim(label, edge_a, edge_b, dim_x, dim_y, direction):
        """選取兩邊線並加尺寸，回傳結果 dict。"""
        diag = {"type": label, "pos": [dim_x, dim_y]}

        try:
            drawing.ClearSelection2(True)
        except Exception:
            pass

        # SelectEntity 選取兩條邊線
        try:
            ok1 = view_obj.SelectEntity(edge_a[0], False)
            ok2 = view_obj.SelectEntity(edge_b[0], True)
            diag["select_entity"] = [ok1, ok2]
        except Exception as e:
            diag["select_entity"] = f"FAIL: {e}"
            ok1 = ok2 = False

        # 檢查 SelectionManager 有沒有真的選中
        try:
            sel_mgr = drawing.SelectionManager
            sel_count = sel_mgr.GetSelectedObjectCount2(-1)
            sel_types = []
            for si in range(1, sel_count + 1):
                sel_types.append(sel_mgr.GetSelectedObjectType3(si, -1))
            diag["sel_mgr"] = {"count": sel_count, "types": sel_types}
        except Exception as e:
            diag["sel_mgr"] = f"FAIL: {e}"

        # 嘗試 AddDimension
        disp_dim = None
        add_dim_errors = []

        if ok1 and ok2:
            # 嘗試所有 direction 值（0-3）
            for d in range(4):
                try:
                    disp_dim = ext.AddDimension(dim_x, dim_y, 0, d)
                    if disp_dim is not None:
                        diag["method"] = f"ext.AddDimension(dir={d})"
                        break
                except Exception as e:
                    add_dim_errors.append(f"dir={d}: {e}")

            # 嘗試 AddDimension2（無 direction）
            if disp_dim is None:
                try:
                    disp_dim = drawing.AddDimension2(dim_x, dim_y, 0)
                    if disp_dim is not None:
                        diag["method"] = "AddDimension2"
                except Exception as e:
                    add_dim_errors.append(f"AddDimension2: {e}")

        if add_dim_errors:
            diag["add_dim_errors"] = add_dim_errors

        if disp_dim is not None:
            # 設定尺寸顯示為 mm、2 位小數
            try:
                # SetUnits2(UseDoc, UType, FractBase, FractDenom, RoundToFrac, DecRound)
                disp_dim.SetUnits2(
                    False, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
                )
                # SetPrecision3(Primary, Dual, PrimaryTol, DualTol)
                disp_dim.SetPrecision3(
                    2, SW_PRECISION_UNCHANGED, 2, SW_PRECISION_UNCHANGED,
                )
            except Exception as e:
                diag["unit_warning"] = f"設定單位/精度失敗: {e}"
            diag["ok"] = True
            logger.info("bbox %s 尺寸已加: x=%.4f y=%.4f", label, dim_x, dim_y)
        else:
            diag["error"] = "AddDimension 回傳 None"

        return diag

    # 垂直範圍尺寸（用最上+最下水平線）
    h_top = bounding.get("h_top")
    h_bottom = bounding.get("h_bottom")
    if h_top and h_bottom and h_top is not h_bottom:
        dim_x = outline[0] - offset
        dim_y = (outline[1] + outline[3]) / 2
        result = _try_add_dim("vertical_extent",
                              h_top, h_bottom, dim_x, dim_y, SW_DIM_LEFT)
        dims.append(result)

    # 水平範圍尺寸（用最左+最右垂直線）
    v_left = bounding.get("v_left")
    v_right = bounding.get("v_right")
    if v_left and v_right and v_left is not v_right:
        dim_x = (outline[0] + outline[2]) / 2
        dim_y = outline[1] - offset
        result = _try_add_dim("horizontal_extent",
                              v_left, v_right, dim_x, dim_y, SW_DIM_DOWN)
        dims.append(result)

    try:
        drawing.ClearSelection2(True)
    except Exception:
        pass

    # 恢復尺寸值輸入對話框偏好
    if orig_pref is not None:
        try:
            app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, orig_pref)
        except Exception:
            pass

    return dims


DIM_DIAMETER_OFFSET = 0.008  # 8mm — 直徑尺寸文字離視圖邊緣


def _add_circle_dims(
    drawing,
    view_obj,
    circles: list[tuple],
    outline: list,
    offset: float,
) -> list:
    """對去重後的圓 SelectEntity → AddDimension 建直徑尺寸。

    circles: [(edge, (cx,cy,cz), radius), ...]（已去重）
    outline: [xMin, yMin, xMax, yMax]（圖紙公尺）
    offset: 基礎偏移量（公尺）
    回傳: [{"type": "diameter", "value_mm": float, "ok": bool}, ...]
    """
    dims = []

    try:
        ext = drawing.Extension
        if ext is None:
            return [{"error": "drawing.Extension 回傳 None"}]
    except Exception as e:
        return [{"error": f"drawing.Extension 失敗: {e}"}]

    # 關閉尺寸值輸入對話框
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception as e:
        dims.append({"warning": f"swInputDimValOnCreate: {e}"})

    x_max = outline[2]
    y_mid = (outline[1] + outline[3]) / 2

    for i, (edge, (_cx, _cy, _cz), radius) in enumerate(circles):
        diag = {
            "type": "diameter",
            "value_mm": round(radius * 2 * 1000, 3),
        }

        try:
            drawing.ClearSelection2(True)
        except Exception:
            pass

        # SelectEntity 選取圓形邊線
        ok = False
        try:
            ok = view_obj.SelectEntity(edge, False)
            diag["select_entity"] = ok
        except Exception as e:
            diag["select_entity"] = f"FAIL: {e}"

        if not ok:
            diag["ok"] = False
            diag["error"] = "SelectEntity 失敗"
            dims.append(diag)
            continue

        # 文字位置：視圖右側堆疊
        stack_idx = i
        dim_x = x_max + offset + stack_idx * DIM_OFFSET_STACK
        dim_y = y_mid

        # 嘗試 AddDimension（圓形選取應自動建直徑尺寸）
        disp_dim = None
        add_dim_errors = []

        for d in range(4):
            try:
                disp_dim = ext.AddDimension(dim_x, dim_y, 0, d)
                if disp_dim is not None:
                    diag["method"] = f"ext.AddDimension(dir={d})"
                    break
            except Exception as e:
                add_dim_errors.append(f"dir={d}: {e}")

        if disp_dim is None:
            try:
                disp_dim = drawing.AddDimension2(dim_x, dim_y, 0)
                if disp_dim is not None:
                    diag["method"] = "AddDimension2"
            except Exception as e:
                add_dim_errors.append(f"AddDimension2: {e}")

        if add_dim_errors:
            diag["add_dim_errors"] = add_dim_errors

        if disp_dim is not None:
            try:
                disp_dim.SetUnits2(
                    False, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
                )
                disp_dim.SetPrecision3(
                    2, SW_PRECISION_UNCHANGED, 2, SW_PRECISION_UNCHANGED,
                )
            except Exception as e:
                diag["unit_warning"] = f"設定單位/精度失敗: {e}"
            diag["ok"] = True
            logger.info(
                "circle 直徑尺寸已加: r=%.4f x=%.4f y=%.4f",
                radius, dim_x, dim_y,
            )
        else:
            diag["ok"] = False
            diag["error"] = "AddDimension 回傳 None"

        dims.append(diag)

    try:
        drawing.ClearSelection2(True)
    except Exception:
        pass

    # 恢復偏好
    if orig_pref is not None:
        try:
            app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, orig_pref)
        except Exception:
            pass

    return dims
