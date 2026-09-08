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

# swAngleUnit_e
SW_ANGLE_DEGREES = 0

# swFractionDisplay_e
SW_FRACTION_DECIMAL = 1

# swDimensionPrecisionSettings_e
SW_PRECISION_UNCHANGED = -1

# Slip Robotics patch: keep the drawing document's units (inches) and use 3-place precision.
import os as _os
DIM_PRECISION = int(_os.getenv("SW_MCP_DIM_PRECISION", "3"))

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
        """查詢 Drawing 視圖的可見邊線資訊。
        回傳每條邊線的 index、type、座標（mm）、幾何參數。
        用於 add_dimension 前確認邊線位置。
        view_name: 指定視圖名稱，預設全部視圖。"""
        try:
            result = await sw.execute(_probe_drawing_edges, view_name)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"probe_drawing_edges 失敗: {e}")
        except Exception as e:
            raise ToolError(f"probe_drawing_edges 未預期錯誤: {e}")

    @mcp.tool()
    async def add_dimension(
        view_name: str,
        edge1: dict,
        edge2: dict | None = None,
        dimension_type: str = "linear",
        text_position: dict | None = None,
    ) -> str:
        """在視圖上新增尺寸標註。
        先用 probe_drawing_edges 查詢邊線，再傳入邊線的 index + 座標。
        view_name: 目標視圖名稱。
        edge1: {"index": int, "x": float, "y": float} — 第一條邊線。
        edge2: {"index": int, "x": float, "y": float} — 第二條邊線（linear/angle 必填，diameter/radius 不需要）。
        dimension_type: "linear"（兩條邊線距離）、"diameter"（圓形直徑）、"radius"（圓弧半徑）或 "angle"（兩條直線夾角）。
        text_position: {"x": float, "y": float}（mm）— 尺寸文字位置，選填。"""
        try:
            if dimension_type == "linear":
                if edge2 is None:
                    raise ToolError("linear 尺寸需要 edge2")
                result = await sw.execute(
                    _add_linear_dimension,
                    view_name, edge1, edge2, text_position,
                )
            elif dimension_type in ("diameter", "radius"):
                result = await sw.execute(
                    _add_radial_dimension,
                    view_name, edge1, text_position, dimension_type,
                )
            elif dimension_type == "angle":
                if edge2 is None:
                    raise ToolError("angle 尺寸需要 edge2")
                result = await sw.execute(
                    _add_angle_dimension,
                    view_name, edge1, edge2, text_position,
                )
            else:
                raise ToolError(f"不支援的 dimension_type: {dimension_type}")
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"add_dimension 失敗: {e}")
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"add_dimension 未預期錯誤: {e}")

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

    @mcp.tool()
    async def insert_balloon(
        view_name: str,
        component: str | None = None,
        style: str = "circular",
        auto_layout: bool = True,
    ) -> str:
        """在組立件工程圖視圖上插入氣球標註。
        view_name: 目標視圖名稱。
        component: 指定零件名稱（包含比對、不分大小寫），不指定則標全部零件；
                   不符的氣球建立後會被刪除。
        style: 氣球樣式（circular/triangle/hexagon），預設 circular。
        auto_layout: 自動排列氣球位置，預設 true。
        回傳 balloons 含每顆氣球的零件名稱與 item number；
        COM 退化路徑或讀取失敗時以 warnings 欄位告知。"""
        try:
            result = await sw.execute(
                _insert_balloon,
                view_name, component, style, auto_layout,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_balloon 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_balloon 未預期錯誤: {e}")

    @mcp.tool()
    async def insert_bom_table(
        view_name: str,
        x: float,
        y: float,
        drawing_name: str | None = None,
    ) -> str:
        """在組立件工程圖視圖上插入 BOM 表（Top-Level Only）。
        view_name: 目標視圖名稱（須參考組立件）。
        x: 放置 X 座標（單位 mm）。
        y: 放置 Y 座標（單位 mm）。
        drawing_name: 目標 drawing，省略則用 active doc。"""
        try:
            result = await sw.execute(
                _insert_bom_table,
                view_name, x, y, drawing_name,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_bom_table 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_bom_table 未預期錯誤: {e}")


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


_M_TO_MM = 1000.0


def _build_edge_info(edge, index: int) -> dict:
    """從 COM edge 物件建立結構化邊線資訊（座標單位 mm）。"""
    info = {"index": index, "type": "other"}

    try:
        curve = edge.GetCurve  # 屬性(dispatch)
    except Exception:
        return info

    # 頂點座標（m）
    start_pt = end_pt = None
    try:
        sv = edge.GetStartVertex
        if sv is not None:
            pt = sv.GetPoint
            start_pt = (pt[0], pt[1], pt[2])
    except Exception:
        pass
    try:
        ev = edge.GetEndVertex
        if ev is not None:
            pt = ev.GetPoint
            end_pt = (pt[0], pt[1], pt[2])
    except Exception:
        pass

    try:
        is_line = curve.IsLine
    except Exception:
        is_line = False
    try:
        is_circle = curve.IsCircle
    except Exception:
        is_circle = False

    if is_line:
        info["type"] = "line"
        if start_pt:
            info["start"] = {
                "x": round(start_pt[0] * _M_TO_MM, 4),
                "y": round(start_pt[1] * _M_TO_MM, 4),
            }
        if end_pt:
            info["end"] = {
                "x": round(end_pt[0] * _M_TO_MM, 4),
                "y": round(end_pt[1] * _M_TO_MM, 4),
            }
        if start_pt and end_pt:
            info["midpoint"] = {
                "x": round((start_pt[0] + end_pt[0]) / 2 * _M_TO_MM, 4),
                "y": round((start_pt[1] + end_pt[1]) / 2 * _M_TO_MM, 4),
            }
            dx = (end_pt[0] - start_pt[0]) * _M_TO_MM
            dy = (end_pt[1] - start_pt[1]) * _M_TO_MM
            dz = (end_pt[2] - start_pt[2]) * _M_TO_MM
            info["length"] = round(math.sqrt(dx * dx + dy * dy + dz * dz), 4)
    elif is_circle:
        info["type"] = "circle" if start_pt is None else "arc"
        try:
            cp = curve.CircleParams  # (cx,cy,cz, ax,ay,az, radius)
            info["midpoint"] = {
                "x": round(cp[0] * _M_TO_MM, 4),
                "y": round(cp[1] * _M_TO_MM, 4),
            }
            info["radius_mm"] = round(cp[6] * _M_TO_MM, 4)
        except Exception:
            pass
        if start_pt:
            info["start"] = {
                "x": round(start_pt[0] * _M_TO_MM, 4),
                "y": round(start_pt[1] * _M_TO_MM, 4),
            }
        if end_pt:
            info["end"] = {
                "x": round(end_pt[0] * _M_TO_MM, 4),
                "y": round(end_pt[1] * _M_TO_MM, 4),
            }

    return info


def _build_edges_info(edges) -> list[dict]:
    """批次建立邊線資訊清單。"""
    return [_build_edge_info(edge, i) for i, edge in enumerate(edges)]


def _match_edge_by_index(
    edges_info: list[dict], index: int, x: float, y: float, tolerance: float = 0.5,
) -> tuple:
    """索引優先匹配。回傳 (matched_index, "index") 或 (None, "fallback")。"""
    if index < 0 or index >= len(edges_info):
        return None, "fallback"

    midpoint = edges_info[index].get("midpoint")
    if midpoint is None:
        return None, "fallback"

    dist = math.sqrt((midpoint["x"] - x) ** 2 + (midpoint["y"] - y) ** 2)
    if dist <= tolerance:
        return index, "index"
    return None, "fallback"


def _match_edge_by_proximity(
    edges_info: list[dict], x: float, y: float, max_distance: float = 2.0,
) -> tuple:
    """近鄰 fallback。回傳 (matched_index, distance) 或拋 SWError。"""
    best_idx = None
    best_dist = float("inf")

    for info in edges_info:
        midpoint = info.get("midpoint")
        if midpoint is None:
            continue
        dist = math.sqrt((midpoint["x"] - x) ** 2 + (midpoint["y"] - y) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_idx = info["index"]

    if best_idx is None or best_dist > max_distance:
        raise SWError(
            f"找不到距離 ({x}, {y}) 在 {max_distance}mm 內的邊線"
            f"（最近距離: {best_dist:.2f}mm）"
        )
    return best_idx, best_dist


def _resolve_edge(edges_info: list[dict], edge_spec: dict) -> tuple:
    """統一入口：先 index 匹配，失敗走 proximity fallback。

    edge_spec: {"index": int, "x": float, "y": float}
    回傳: (matched_index, match_method)
    """
    index = edge_spec.get("index", -1)
    x = edge_spec["x"]
    y = edge_spec["y"]

    matched_idx, method = _match_edge_by_index(edges_info, index, x, y)
    if method == "index":
        return matched_idx, "index"

    matched_idx, _dist = _match_edge_by_proximity(edges_info, x, y)
    return matched_idx, "proximity"


_DIM_TEXT_OFFSET_MM = 15.0


def _calc_text_position(
    edges_info: list[dict], idx1: int, idx2: int,
) -> dict:
    """計算尺寸文字預設位置（mm）。

    取兩條邊線中點的平均位置，往 Y 方向偏移。
    """
    mp1 = edges_info[idx1].get("midpoint", {"x": 0, "y": 0})
    mp2 = edges_info[idx2].get("midpoint", {"x": 0, "y": 0})
    return {
        "x": round((mp1["x"] + mp2["x"]) / 2, 4),
        "y": round((mp1["y"] + mp2["y"]) / 2 + _DIM_TEXT_OFFSET_MM, 4),
    }


def _check_edge_type(
    edges_info: list[dict], idx: int, required_type: str, dim_type_label: str,
) -> None:
    """驗證邊線類型。不符合時拋 SWError。"""
    actual = edges_info[idx]["type"]
    if actual != required_type:
        raise SWError(
            f"{dim_type_label} 尺寸需要 {required_type} 邊線，"
            f"但 edge {idx} 是 {actual}"
        )


def _calc_radial_text_pos(
    edge_info: dict, outline_m: list,
) -> dict:
    """計算徑向尺寸文字位置（mm）。

    x: 視圖右邊界 + 偏移
    y: 圓心 y
    edge_info: from _build_edge_info（mm）
    outline_m: [xMin, yMin, xMax, yMax]（meters，from view.GetOutline）
    """
    x_right = outline_m[2] * _M_TO_MM + _DIM_TEXT_OFFSET_MM
    y_center = edge_info["midpoint"]["y"]
    return {"x": round(x_right, 4), "y": round(y_center, 4)}


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
    """查詢 Drawing 視圖的可見邊線，回傳結構化邊線資訊。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    # 取得視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        # fallback: 用 _discover_view_names 暴力搜索
        discovered = _discover_view_names(drawing)
        if not discovered:
            raise SWError("找不到任何工程圖視圖")
        target = view_name or discovered[0]
        views = _get_drawing_views(drawing, target)
        if not views:
            raise SWError(f"找不到視圖: {target}")

    results = []
    for vname, view_obj in views:
        drawing.ActivateView(vname)
        edges = _get_view_edges(view_obj)
        edges_info = _build_edges_info(edges)
        results.append({
            "view": vname,
            "edge_count": len(edges_info),
            "edges": edges_info,
        })

    return {
        "status": "done",
        "views": results,
    }


def _add_linear_dimension(
    view_name: str,
    edge1: dict,
    edge2: dict,
    text_position: dict | None,
) -> dict:
    """COM 操作：在兩條邊線間加線性尺寸。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    # 取得視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    vname, view_obj = views[0]
    drawing.ActivateView(vname)

    # 取邊線
    edges = _get_view_edges(view_obj)
    if not edges:
        raise SWError(f"視圖 {vname} 沒有可見邊線")

    # 匹配
    edges_info = _build_edges_info(edges)
    idx1, method1 = _resolve_edge(edges_info, edge1)
    idx2, method2 = _resolve_edge(edges_info, edge2)

    if idx1 == idx2:
        raise SWError("兩條邊線不能相同（index 皆為 %d）" % idx1)

    # 文字位置
    if text_position:
        text_x = text_position["x"] / _M_TO_MM
        text_y = text_position["y"] / _M_TO_MM
    else:
        auto_pos = _calc_text_position(edges_info, idx1, idx2)
        text_x = auto_pos["x"] / _M_TO_MM
        text_y = auto_pos["y"] / _M_TO_MM

    # 關閉尺寸值輸入對話框
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:
        pass

    try:
        # 選取邊線
        drawing.ClearSelection2(True)
        ok1 = view_obj.SelectEntity(edges[idx1], False)
        ok2 = view_obj.SelectEntity(edges[idx2], True)  # append

        if not ok1 or not ok2:
            raise SWError(f"SelectEntity 失敗: edge1={ok1}, edge2={ok2}")

        # AddDimension
        ext = drawing.Extension
        disp_dim = None

        for d in range(4):
            try:
                disp_dim = ext.AddDimension(text_x, text_y, 0, d)
                if disp_dim is not None:
                    break
            except Exception:
                pass

        if disp_dim is None:
            try:
                disp_dim = drawing.AddDimension2(text_x, text_y, 0)
            except Exception:
                pass

        if disp_dim is None:
            raise SWError("AddDimension 回傳 None — 無法建立尺寸")

        # 設定單位 mm / 2 位小數
        try:
            disp_dim.SetUnits2(
                True, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
            )
            disp_dim.SetPrecision3(
                DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED,
            )
        except Exception:
            pass

        value_mm = None
        try:
            dim = disp_dim.GetDimension2(0)
            value_mm = round(dim.Value, 4)
        except Exception:
            pass

        drawing.ClearSelection2(True)

        return {
            "status": "done",
            "dimension_type": "linear",
            "value_mm": value_mm,
            "text_position": {
                "x": round(text_x * _M_TO_MM, 4),
                "y": round(text_y * _M_TO_MM, 4),
            },
            "match_method_edge1": method1,
            "match_method_edge2": method2,
        }

    finally:
        if orig_pref is not None:
            try:
                app.SetUserPreferenceToggle(
                    SW_INPUT_DIM_VAL_ON_CREATE, orig_pref,
                )
            except Exception:
                pass


def _add_radial_dimension(
    view_name: str,
    edge1: dict,
    text_position: dict | None,
    dim_kind: str,
) -> dict:
    """COM 操作：在圓形/圓弧邊線加直徑或半徑尺寸。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    vname, view_obj = views[0]
    drawing.ActivateView(vname)

    edges = _get_view_edges(view_obj)
    if not edges:
        raise SWError(f"視圖 {vname} 沒有可見邊線")

    edges_info = _build_edges_info(edges)
    idx, method = _resolve_edge(edges_info, edge1)

    required = "circle" if dim_kind == "diameter" else "arc"
    _check_edge_type(edges_info, idx, required, dim_kind)

    if text_position:
        text_x = text_position["x"] / _M_TO_MM
        text_y = text_position["y"] / _M_TO_MM
    else:
        try:
            raw_outline = view_obj.GetOutline
            outline_m = [raw_outline[0], raw_outline[1],
                         raw_outline[2], raw_outline[3]]
        except Exception:
            outline_m = [0, 0, 0.2, 0.2]
        auto_pos = _calc_radial_text_pos(edges_info[idx], outline_m)
        text_x = auto_pos["x"] / _M_TO_MM
        text_y = auto_pos["y"] / _M_TO_MM

    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:
        pass

    try:
        drawing.ClearSelection2(True)
        ok = view_obj.SelectEntity(edges[idx], False)

        if not ok:
            raise SWError(f"SelectEntity 失敗: edge {idx}")

        ext = drawing.Extension
        disp_dim = None

        for d in range(4):
            try:
                disp_dim = ext.AddDimension(text_x, text_y, 0, d)
                if disp_dim is not None:
                    break
            except Exception:
                pass

        if disp_dim is None:
            try:
                disp_dim = drawing.AddDimension2(text_x, text_y, 0)
            except Exception:
                pass

        if disp_dim is None:
            label = "直徑" if dim_kind == "diameter" else "半徑"
            raise SWError(f"AddDimension 回傳 None — 無法建立{label}尺寸")

        try:
            disp_dim.SetUnits2(
                True, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
            )
            disp_dim.SetPrecision3(
                DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED,
            )
        except Exception:
            pass

        value_mm = None
        try:
            dim = disp_dim.GetDimension2(0)
            value_mm = round(dim.Value, 4)
        except Exception:
            pass

        drawing.ClearSelection2(True)

        return {
            "status": "done",
            "dimension_type": dim_kind,
            "value_mm": value_mm,
            "text_position": {
                "x": round(text_x * _M_TO_MM, 4),
                "y": round(text_y * _M_TO_MM, 4),
            },
            "match_method_edge1": method,
        }

    finally:
        if orig_pref is not None:
            try:
                app.SetUserPreferenceToggle(
                    SW_INPUT_DIM_VAL_ON_CREATE, orig_pref,
                )
            except Exception:
                pass


def _add_angle_dimension(
    view_name: str,
    edge1: dict,
    edge2: dict,
    text_position: dict | None,
) -> dict:
    """COM 操作：在兩條直線邊線間加角度尺寸。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    vname, view_obj = views[0]
    drawing.ActivateView(vname)

    edges = _get_view_edges(view_obj)
    if not edges:
        raise SWError(f"視圖 {vname} 沒有可見邊線")

    edges_info = _build_edges_info(edges)
    idx1, method1 = _resolve_edge(edges_info, edge1)
    idx2, method2 = _resolve_edge(edges_info, edge2)

    if idx1 == idx2:
        raise SWError("兩條邊線不能相同（index 皆為 %d）" % idx1)

    _check_edge_type(edges_info, idx1, "line", "angle")
    _check_edge_type(edges_info, idx2, "line", "angle")

    # 文字位置
    if text_position:
        text_x = text_position["x"] / _M_TO_MM
        text_y = text_position["y"] / _M_TO_MM
    else:
        auto_pos = _calc_text_position(edges_info, idx1, idx2)
        text_x = auto_pos["x"] / _M_TO_MM
        text_y = auto_pos["y"] / _M_TO_MM

    # 關閉尺寸值輸入對話框
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:
        pass

    try:
        # 選取邊線
        drawing.ClearSelection2(True)
        ok1 = view_obj.SelectEntity(edges[idx1], False)
        ok2 = view_obj.SelectEntity(edges[idx2], True)  # append

        if not ok1 or not ok2:
            raise SWError(f"SelectEntity 失敗: edge1={ok1}, edge2={ok2}")

        # AddDimension
        ext = drawing.Extension
        disp_dim = None

        for d in range(4):
            try:
                disp_dim = ext.AddDimension(text_x, text_y, 0, d)
                if disp_dim is not None:
                    break
            except Exception:
                pass

        if disp_dim is None:
            try:
                disp_dim = drawing.AddDimension2(text_x, text_y, 0)
            except Exception:
                pass

        if disp_dim is None:
            raise SWError("AddDimension 回傳 None — 無法建立角度尺寸")

        # 設定單位（swDEGREES=0）/ 2 位小數
        try:
            disp_dim.SetUnits2(
                False, SW_ANGLE_DEGREES, SW_FRACTION_DECIMAL, 0, False, 0,
            )
            disp_dim.SetPrecision3(
                DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED,
            )
        except Exception:
            pass

        value_deg = None
        try:
            dim = disp_dim.GetDimension2(0)
            value_deg = round(dim.Value, 4)
        except Exception:
            pass

        drawing.ClearSelection2(True)

        return {
            "status": "done",
            "dimension_type": "angle",
            "value_deg": value_deg,
            "text_position": {
                "x": round(text_x * _M_TO_MM, 4),
                "y": round(text_y * _M_TO_MM, 4),
            },
            "match_method_edge1": method1,
            "match_method_edge2": method2,
        }

    finally:
        if orig_pref is not None:
            try:
                app.SetUserPreferenceToggle(
                    SW_INPUT_DIM_VAL_ON_CREATE, orig_pref,
                )
            except Exception:
                pass


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
                    True, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
                )
                # SetPrecision3(Primary, Dual, PrimaryTol, DualTol)
                disp_dim.SetPrecision3(
                    DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED,
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
                    True, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
                )
                disp_dim.SetPrecision3(
                    DIM_PRECISION, SW_PRECISION_UNCHANGED, DIM_PRECISION, SW_PRECISION_UNCHANGED,
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


# === insert_balloon ===

# swBalloonStyle_e
SW_BALLOON_STYLE = {
    "circular": 1,   # swBS_Circular
    "triangle": 2,   # swBS_Triangle
    "hexagon": 4,    # swBS_Hexagon
}

# swBalloonLayoutStyle_e
SW_BALLOON_LAYOUT_RIGHT = 4   # swDetailingBalloonLayout_Right
SW_BALLOON_LAYOUT_NONE = 0    # swDetailingBalloonLayout_None

# swBalloonTextContent_e
SW_BALLOON_TEXT_ITEM_NUM = 1  # swBalloonTextItemNumber

# swBalloonFit_e
SW_BALLOON_FIT_TIGHTEST = 0   # swBF_Tightest


def _extract_notes(notes) -> list:
    """從 AutoBalloon 回傳的 VARIANT 中提取 Note 物件列表。

    pywin32 late-binding 下 VARIANT 陣列可能為 None、tuple、或
    需要 indexing 的 SAFEARRAY。
    """
    if notes is None:
        return []
    result = []
    # 嘗試 1: 直接迭代（tuple/list）
    try:
        for note in notes:
            result.append(note)
        return result
    except Exception:
        pass
    # 嘗試 2: indexing（SAFEARRAY）
    try:
        i = 0
        while True:
            try:
                result.append(notes[i])
                i += 1
            except (IndexError, Exception):
                break
    except Exception:
        pass
    return result


def _read_balloon_info(balloon_notes: list) -> list[dict]:
    """從 Note 物件列表讀取氣球資訊（零件名稱、item number）。"""
    balloons = []
    for note in balloon_notes:
        try:
            balloon_texts = note.GetBomBalloonTexts(True)
            item_number = ""
            comp_name = ""
            if balloon_texts is not None and len(balloon_texts) > 0:
                bt = balloon_texts[0]
                try:
                    item_number = str(bt.GetText())
                except Exception:
                    pass
                try:
                    comp = bt.GetComponent()
                    if comp is not None:
                        comp_name = comp.Name2
                except Exception:
                    pass
            balloons.append({
                "component": comp_name,
                "item_number": item_number,
            })
        except Exception as e:
            logger.warning("讀取氣球資訊失敗: %s", e)
            balloons.append({"component": "", "item_number": ""})
    return balloons


def _com_get(obj, name):
    """讀取 COM 成員，處理 late-binding 屬性/方法歧義（含非 COM 物件回傳值）。

    與 _com_prop_or_method 不同：適用回傳字串/bool/陣列的成員。
    先嘗試方法呼叫，TypeError（值非 callable，屬性形式）時回傳值本身。
    COM 呼叫本身的錯誤向上拋，由呼叫端決定兜底。
    """
    attr = getattr(obj, name)
    try:
        return attr()
    except TypeError:
        return attr


def _get_view_note_names(view) -> set[str]:
    """取得視圖上所有 note 名稱集合（建立前快照，diff 兜底用）。"""
    names: set[str] = set()
    for note in _extract_notes(_com_get(view, "GetNotes")):
        try:
            names.add(str(_com_get(note, "GetName")))
        except Exception as e:
            logger.debug("GetName 失敗: %s", e)
    return names


def _collect_new_balloon_notes(view, before_names: set[str]) -> list:
    """diff 出建立後新增、且為 BOM balloon 的 Note 物件。"""
    result = []
    for note in _extract_notes(_com_get(view, "GetNotes")):
        try:
            name = str(_com_get(note, "GetName"))
        except Exception:
            continue
        if name in before_names:
            continue
        try:
            if not bool(_com_get(note, "IsBomBalloon")):
                continue
        except Exception:
            # 無法判斷時，新增的 note 視為本次氣球
            logger.debug("IsBomBalloon 判斷失敗，視為氣球: %s", name)
        result.append(note)
    return result


def _delete_balloon_note(drawing, note, view_name: str) -> bool:
    """刪除單顆氣球 Note。

    用 SelectByID（5 參數版，late-binding 安全）+ EditDelete；
    名稱先試 view 限定格式再試裸名。
    """
    try:
        name = str(_com_get(note, "GetName"))
    except Exception as e:
        logger.warning("取得氣球名稱失敗: %s", e)
        return False
    drawing.ClearSelection2(True)
    for sel_name in (f"{name}@{view_name}", name):
        try:
            ok = drawing.SelectByID(sel_name, "NOTE", 0, 0, 0)
        except Exception as e:
            logger.debug("SelectByID(%s) 失敗: %s", sel_name, e)
            continue
        if ok:
            try:
                drawing.EditDelete()
                return True
            except Exception as e:
                logger.warning("EditDelete 失敗: %s", e)
                return False
    return False


def _apply_balloon_options(opts, style_val: int, layout: int) -> None:
    """以 property 賦值設定 AutoBalloonOptions。

    避開舊式 positional VARIANT_BOOL 的 DISP_E_TYPEMISMATCH
    （MathTransform ArrayData property 賦值有成功前例）。
    屬性名稱皆經 SW 2021 本機 API 文件核對（IAutoBalloonOptions，
    InsertMagneticLine 為單數）。
    只設必要屬性，減少 late-binding 下屬性名不符直接炸掉主路徑的面積；
    任何一個賦值失敗即整個主路徑失敗，由呼叫端退化。
    """
    opts.Layout = layout
    opts.Style = style_val
    opts.Size = SW_BALLOON_FIT_TIGHTEST
    opts.UpperTextContent = SW_BALLOON_TEXT_ITEM_NUM
    opts.InsertMagneticLine = False


def _insert_balloon(
    view_name: str,
    component: str | None = None,
    style: str = "circular",
    auto_layout: bool = True,
) -> dict:
    """COM 操作：在視圖上插入氣球標註。

    pywin32 late-binding 限制與對策：
    - 主路徑用 IDrawingDoc::AutoBalloon5(AutoBalloonOptions) +
      IDrawingDoc::CreateAutoBalloonOptions（property 賦值避開
      positional VARIANT_BOOL 類型不符），style/auto_layout 在主路徑下生效
    - 主路徑失敗退化到 AutoBalloon()/AutoBalloon2()，
      style/auto_layout 不套用並以 warnings 告知
    - 視圖選取維持 SelectByID（5 參數版），SelectByID2 有類型問題
    """
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    style_val = SW_BALLOON_STYLE.get(style)
    if style_val is None:
        raise SWError(
            f"不支援的 style: {style}，可用: {', '.join(SW_BALLOON_STYLE)}"
        )

    # 找視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    view_obj = views[0][1]

    # 建立前快照：視圖既有 note 名稱（回傳值不可用時 diff 兜底）
    before_names: set[str] | None = None
    try:
        before_names = _get_view_note_names(view_obj)
    except Exception as e:
        logger.warning("建立前 note 快照失敗: %s", e)

    warnings: list[str] = []

    # 啟動並選取視圖（AutoBalloon 需要 view 被選中）
    drawing.ActivateView(view_name)
    try:
        drawing.SelectByID(view_name, "DRAWINGVIEW", 0, 0, 0)
    except Exception as e:
        logger.warning("SelectByID 失敗: %s", e)

    layout = SW_BALLOON_LAYOUT_RIGHT if auto_layout else SW_BALLOON_LAYOUT_NONE

    # 建立：主路徑 AutoBalloon5(Options)，退化 AutoBalloon()/AutoBalloon2()
    # CreateAutoBalloonOptions / AutoBalloon5 皆掛在 IDrawingDoc（SW 2021 API）
    notes = None
    fallback_used = False
    errors = []
    try:
        opts = drawing.CreateAutoBalloonOptions()
        _apply_balloon_options(opts, style_val, layout)
        notes = drawing.AutoBalloon5(opts)
    except Exception as e:
        errors.append(f"AutoBalloon5(opts): {e}")
        try:
            notes = drawing.AutoBalloon()
            fallback_used = True
        except Exception as e2:
            errors.append(f"AutoBalloon(): {e2}")
            try:
                notes = drawing.AutoBalloon2(int(layout), 0)
                fallback_used = True
            except Exception as e3:
                errors.append(f"AutoBalloon2(): {e3}")
                raise SWError(f"氣球建立失敗: {'; '.join(errors)}")

    if fallback_used:
        warnings.append("style/auto_layout 未套用（退化路徑）")

    # 讀取：回傳值優先，None/空時 annotation diff 兜底
    balloon_notes = _extract_notes(notes)
    read_failed = False
    if not balloon_notes:
        if before_names is None:
            read_failed = True
        else:
            try:
                balloon_notes = _collect_new_balloon_notes(
                    view_obj, before_names,
                )
            except Exception as e:
                logger.warning("annotation diff 讀取失敗: %s", e)
                read_failed = True
    if read_failed:
        warnings.append("氣球可能已建立但無法讀取資訊")

    # 遍歷 Note 取零件資訊
    balloons = _read_balloon_info(balloon_notes)

    # component 過濾：標全部、刪不符（包含比對，Name2 帶 instance 後綴全名）
    if component is not None:
        if read_failed:
            warnings.append("component 過濾未執行（無法讀取氣球資訊）")
        else:
            kept = []
            for note, info in zip(balloon_notes, balloons):
                if not info["component"]:
                    # 讀不到零件名的氣球不刪，避免誤殺
                    kept.append(info)
                    warnings.append("無法判斷氣球零件名稱，保留該氣球")
                    continue
                if component.lower() in info["component"].lower():
                    kept.append(info)
                    continue
                if not _delete_balloon_note(drawing, note, view_name):
                    warnings.append(f"刪除氣球失敗: {info['component']}")
            balloons = kept

    drawing.ClearSelection2(True)

    result = {
        "status": "done",
        "balloon_count": len(balloons),
        "balloons": balloons,
        "view_name": view_name,
    }
    if warnings:
        result["warnings"] = warnings
    return result


# === insert_bom_table ===

# swBomType_e
SW_BOM_TOP_LEVEL_ONLY = 1   # swBomTable_TopLevelOnly
SW_BOM_PARTS_ONLY = 2        # swBomTable_PartsOnly
SW_BOM_INDENTED = 3          # swBomTable_Indented


def _insert_bom_table(
    view_name: str,
    x: float,
    y: float,
    drawing_name: str | None = None,
) -> dict:
    """COM 操作：在組立件工程圖視圖上插入 Top-Level Only BOM 表。

    pywin32 late-binding 踩坑：
    - InsertBomTable4 帶 7 參數可能因 VARIANT byref 失敗 → 退化到 InsertBomTable3
    """
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()

    if drawing_name:
        try:
            app.ActivateDoc2(drawing_name, True, 0)
        except Exception as e:
            raise SWError(f"切換到 drawing '{drawing_name}' 失敗: {e}")

    drawing = app.ActiveDoc
    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError("Active document is not a drawing")

    # 找視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"View '{view_name}' not found")

    vname, view_obj = views[0]

    # 驗證視圖參考的是組立件
    try:
        ref_doc = view_obj.ReferencedDocument
        if ref_doc is None:
            raise SWError(f"View '{view_name}' has no referenced document")
        ref_type = _safe_get(ref_doc, "GetType")
        if ref_type != 2:
            raise SWError(
                f"View '{view_name}' does not reference an assembly "
                f"(type={ref_type})"
            )
    except SWError:
        raise
    except Exception as e:
        raise SWError(f"檢查視圖參考文件失敗: {e}")

    # 啟動並選取視圖
    if not drawing.ActivateView(vname):
        logger.warning("ActivateView 失敗: %s", vname)
    try:
        drawing.SelectByID(vname, "DRAWINGVIEW", 0, 0, 0)
    except Exception as e:
        logger.warning("SelectByID 失敗: %s", e)

    # mm → meters
    x_m = x / _M_TO_MM
    y_m = y / _M_TO_MM

    # 主路徑 InsertBomTable4
    bom_table = None
    errors = []
    try:
        bom_table = drawing.InsertBomTable4(
            False, x_m, y_m, 0, SW_BOM_TOP_LEVEL_ONLY, "", "",
        )
    except Exception as e:
        errors.append(f"InsertBomTable4: {e}")

    # 退化 InsertBomTable3
    if bom_table is None:
        try:
            bom_table = drawing.InsertBomTable3(
                False, x_m, y_m, 0, SW_BOM_TOP_LEVEL_ONLY, "", "",
            )
        except Exception as e:
            errors.append(f"InsertBomTable3: {e}")

    if bom_table is None:
        msg = "Failed to insert BOM table (both API versions returned None"
        if errors:
            msg += "): " + "; ".join(errors)
        else:
            msg += ", no exceptions raised)"
        raise SWError(msg)

    table_name = _safe_get(bom_table, "Name") or ""

    return {
        "status": "done",
        "table_name": table_name,
    }
