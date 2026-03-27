"""標註 tools — 匯入模型尺寸。"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

SW_INSERT_DIMENSION = 0
SW_INSERT_MODEL_ITEMS_ALL = 32767


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


def _insert_model_dimensions(
    view_name: str | None,
    dimension_type: str,
) -> dict:
    sw_conn = SWConnection.get_instance()
    drawing = sw_conn.get_active_doc()

    sheet = drawing.GetCurrentSheet()
    views = sheet.GetViews()

    if views is None or len(views) == 0:
        raise SWError("目前的 Drawing Sheet 沒有任何視圖")

    source_map = {
        "all": 0,
        "marked": 1,
        "reference": 2,
    }
    source = source_map.get(dimension_type.lower(), 0)

    results = []
    total_count = 0

    for view in views:
        current_name = view.GetName2()

        if view_name and current_name != view_name:
            continue

        drawing.ActivateView(current_name)

        drawing.InsertModelAnnotations3(
            source,
            SW_INSERT_MODEL_ITEMS_ALL,
            True,
            True,
            False,
            False,
        )

        dim_count = 0
        annotations = view.GetAnnotations
        if annotations:
            dim_count = len(annotations)

        results.append({
            "view": current_name,
            "dimensions_inserted": dim_count,
        })
        total_count += dim_count

    return {
        "total_dimensions": total_count,
        "views": results,
        "status": "inserted",
        "note": "尺寸位置可能重疊，建議用 capture_drawing 確認後手動調整",
    }
