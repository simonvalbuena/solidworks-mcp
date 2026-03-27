"""標註 tools — 匯入模型尺寸。"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

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
