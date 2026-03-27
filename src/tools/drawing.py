"""出圖 tools — 建立 Drawing、插入標準視圖。"""

from __future__ import annotations

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

import config
from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

SW_DOC_DRAWING = 3
SW_DISPLAY_MODE_HIDDEN_GREYED = 6

SW_DRAWING_PAPER_SIZE = {
    "A4": 8,
    "A3": 9,
    "A2": 10,
    "A1": 11,
    "A0": 12,
}

# 標準紙張尺寸（單位：mm）
PAPER_SIZE_MM = {
    "A0": (1189, 841),
    "A1": (841, 594),
    "A2": (594, 420),
    "A3": (420, 297),
    "A4": (297, 210),
}

# 中文版 SolidWorks 2021 本地化視圖名稱
SW_VIEW_NAMES = {
    "front": "*前視",
    "back": "*後視",
    "top": "*上視",
    "bottom": "*下視",
    "right": "*右視",
    "left": "*左視",
    "isometric": "*等角視",
    "trimetric": "*不等角視圖",
    "dimetric": "*二等角視圖",
}

FIRST_ANGLE_LAYOUT = {
    "front":     (0.40, 0.55),
    "top":       (0.40, 0.25),
    "right":     (0.15, 0.55),
    "left":      (0.65, 0.55),
    "bottom":    (0.40, 0.85),
    "back":      (0.90, 0.55),
    "isometric": (0.75, 0.25),
}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def create_drawing(
        template_path: str | None = None,
        paper_size: str = config.DEFAULT_PAPER_SIZE,
    ) -> str:
        """建立新的 Drawing 文件，套用公司圖框模板。
        template_path: 圖框模板路徑（.drwdot），預設使用 config 設定。
        paper_size: 圖紙大小（A4/A3/A2/A1/A0），預設 A3。"""
        try:
            result = await sw.execute(
                _create_drawing,
                template_path or config.TEMPLATE_PATH,
                paper_size,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"create_drawing 失敗: {e}")

    @mcp.tool()
    async def insert_standard_views(
        source_doc: str,
        views: list[str] | None = None,
        scale: float | None = None,
        paper_size: str = "A3",
    ) -> str:
        """在 Drawing 中插入獨立的標準視圖（無投影關聯）。
        source_doc: 來源 part/assembly 文件路徑。
        views: 視圖清單，可選 front/back/top/bottom/left/right/isometric，預設 front+top+right+isometric。
        scale: 視圖比例，預設自動適配。
        paper_size: 圖紙大小（A4/A3/A2/A1/A0），用於計算視圖佈局位置。"""
        if views is None:
            views = ["front", "top", "right", "isometric"]
        try:
            result = await sw.execute(
                _insert_standard_views,
                source_doc,
                views,
                scale,
                paper_size,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_standard_views 失敗: {e}")

    @mcp.tool()
    async def insert_standard_views_aligned(
        source_doc: str,
    ) -> str:
        """用第一角法自動建立標準三視圖（前視、上視、右視），視圖間有投影關聯、自動縮放。
        source_doc: 來源 part/assembly 文件路徑。"""
        try:
            result = await sw.execute(
                _insert_1st_angle_views,
                source_doc,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_standard_views_aligned 失敗: {e}")


def _create_drawing(template_path: str, paper_size: str) -> dict:
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()

    if not template_path:
        raise SWError("未設定圖框模板路徑，請設定 SW_MCP_TEMPLATE")

    doc = app.NewDocument(
        template_path,
        SW_DRAWING_PAPER_SIZE.get(paper_size.upper(), SW_DRAWING_PAPER_SIZE.get(config.DEFAULT_PAPER_SIZE, 9)),
        0,
        0,
    )

    if doc is None:
        raise SWError(f"建立 Drawing 失敗，模板: {template_path}")

    title = doc.GetTitle

    return {
        "drawing_name": title,
        "template": template_path,
        "paper_size": paper_size,
        "status": "created",
    }


def _insert_standard_views(
    source_doc: str,
    views: list[str],
    scale: float | None,
    paper_size: str = "A3",
) -> dict:
    """逐個插入獨立視圖（無投影關聯），可自訂視圖組合與比例。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    # pywin32 late-binding 無法呼叫 IDrawingDoc.GetCurrentSheet，改用硬編碼紙張尺寸
    sheet_width, sheet_height = PAPER_SIZE_MM.get(
        paper_size.upper(), PAPER_SIZE_MM["A3"]
    )

    inserted = []

    for view_key in views:
        sw_view_name = SW_VIEW_NAMES.get(view_key.lower())
        if sw_view_name is None:
            logger.warning("未知的視圖名稱: %s，跳過", view_key)
            continue

        layout = FIRST_ANGLE_LAYOUT.get(view_key.lower(), (0.5, 0.5))
        x = sheet_width * layout[0] / 1000  # mm → m
        y = sheet_height * layout[1] / 1000

        try:
            view = drawing.CreateDrawViewFromModelView3(
                source_doc, sw_view_name, x, y, 0,
            )
        except Exception as e:
            logger.error("CreateDrawViewFromModelView3 %s 失敗: %s", view_key, e)
            continue

        if view is None:
            logger.warning("插入視圖失敗: %s", view_key)
            continue

        if scale is not None:
            try:
                view.ScaleRatio = (1.0, scale)
            except Exception as e:
                logger.error("ScaleRatio 設定失敗: %s", e)

        try:
            view.SetDisplayMode3(
                False, SW_DISPLAY_MODE_HIDDEN_GREYED, False, False,
            )
        except Exception as e:
            logger.warning("SetDisplayMode3 失敗（非致命）: %s", e)

        inserted.append({
            "view": view_key,
            "sw_view": sw_view_name,
            "position": {"x": round(x * 1000, 1), "y": round(y * 1000, 1)},
        })

    try:
        drawing.ViewZoomtofit2()
    except Exception:
        pass

    return {
        "count": len(inserted),
        "views": inserted,
        "status": "inserted",
    }


def _insert_1st_angle_views(source_doc: str) -> dict:
    """用 Create1stAngleViews2 建立有投影關聯的標準三視圖。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    model_name = os.path.basename(source_doc)

    try:
        result = drawing.Create1stAngleViews2(model_name)
        logger.info("Create1stAngleViews2(%s): %s", model_name, result)
    except Exception as e:
        logger.warning("Create1stAngleViews2 用檔名失敗: %s，嘗試完整路徑", e)
        try:
            result = drawing.Create1stAngleViews2(source_doc)
        except Exception as e2:
            raise SWError(f"Create1stAngleViews2 失敗: {e2}") from e2

    if not result:
        raise SWError(f"Create1stAngleViews2 回傳失敗，來源: {model_name}")

    try:
        drawing.ViewZoomtofit2()
    except Exception:
        pass

    return {
        "model": model_name,
        "status": "inserted",
    }
