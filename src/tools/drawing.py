"""出圖 tools — 建立 Drawing、插入標準視圖。"""

from __future__ import annotations

import json
import logging
import math
import os

from mcp.server.fastmcp import FastMCP

import config
from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

SW_DOC_DRAWING = 3

SW_DISPLAY_MODES = {
    "wireframe": 1,              # swWIREFRAME
    "hidden_lines_removed": 6,   # swHIDDEN_LINES_REMOVED
    "shaded": 3,                 # swSHADED
}

_TEMP_VIEW_NAME = "_mcp_custom_temp"

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

    @mcp.tool()
    async def insert_section_view(
        parent_view: str,
        section_line: dict,
        label: str = "A",
        position: dict | None = None,
        scale: float | None = None,
    ) -> str:
        """在工程圖中建立剖面圖。
        parent_view: 父視圖名稱（在哪個視圖上切剖面）。
        section_line: 剖面線定義 {"start": {"x": mm, "y": mm}, "end": {"x": mm, "y": mm}}，sheet 絕對座標。
        label: 剖面標記（A, B, C...），預設 "A"。
        position: 剖面圖在 sheet 上的位置 {"x": mm, "y": mm}，預設父視圖右側 +50mm。
        scale: 比例分母（如 5 表示 1:5），預設繼承父視圖。"""
        try:
            result = await sw.execute(
                _insert_section_view,
                parent_view, section_line, label, position, scale,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_section_view 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_section_view 未預期錯誤: {e}")

    @mcp.tool()
    async def insert_detail_view(
        parent_view: str,
        center: dict,
        radius: float,
        label: str = "A",
        scale: float = 2.0,
        position: dict | None = None,
    ) -> str:
        """在工程圖中建立局部放大圖。
        parent_view: 父視圖名稱（在哪個視圖上圈放大區域）。
        center: 放大區域中心點 {"x": mm, "y": mm}，sheet 絕對座標。
        radius: 放大區域半徑（mm）。
        label: 局部圖標記（A, B, C...），預設 "A"。
        scale: DECIMAL view scale (1.0 = 1:1, 2.0 = 2:1, 0.5 = 1:2), default 2.
        center/radius are ABSOLUTE SHEET mm (converted to the parent view's sketch space
        internally, honouring view scale and rotation).
        position: 局部放大圖在 sheet 上的位置 {"x": mm, "y": mm}，預設父視圖右上方。"""
        try:
            result = await sw.execute(
                _insert_detail_view,
                parent_view, center, radius, label, scale, position,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_detail_view 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_detail_view 未預期錯誤: {e}")

    @mcp.tool()
    async def insert_custom_view(
        source_doc: str,
        view_name: str | None = None,
        orientation: dict | None = None,
        position: dict | None = None,
        scale: float | None = None,
        display_mode: str = "hidden_lines_removed",
        paper_size: str = "A3",
    ) -> str:
        """插入自訂角度視圖。支援具名視角和任意 XYZ 旋轉角度。
        source_doc: 來源 part/assembly 文件路徑。
        view_name: 具名視角（front/back/top/bottom/left/right/isometric/trimetric/dimetric），與 orientation 二擇一。
        orientation: 自訂旋轉角度 {"x": 度, "y": 度, "z": 度}，XYZ extrinsic rotation，與 view_name 二擇一。
        position: 視圖位置 {"x": mm, "y": mm}，預設紙張中央。
        scale: 比例分母（如 2 表示 1:2），預設自動。
        display_mode: 顯示模式（wireframe/hidden_lines_removed/shaded），預設 hidden_lines_removed。
        paper_size: 圖紙大小（A4/A3/A2/A1/A0），用於預設位置計算。"""
        try:
            result = await sw.execute(
                _insert_custom_view,
                source_doc, view_name, orientation,
                position, scale, display_mode, paper_size,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_custom_view 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_custom_view 未預期錯誤: {e}")


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
                False, SW_DISPLAY_MODES["hidden_lines_removed"], False, False,
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


_MM_TO_M = 0.001


def _insert_section_view(
    parent_view: str,
    section_line: dict,
    label: str = "A",
    position: dict | None = None,
    scale: float | None = None,
) -> dict:
    """在父視圖上畫剖面線，建立剖面圖。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    if scale is not None and scale <= 0:
        raise SWError("比例分母必須大於 0")

    # 找父視圖
    from tools.annotation import _get_drawing_views

    views = _get_drawing_views(drawing, parent_view)
    if not views:
        raise SWError(f"找不到視圖: {parent_view}")

    _, view_obj = views[0]

    # 取得父視圖 outline（meters）
    outline = view_obj.GetOutline  # [xmin, ymin, xmax, ymax]

    # 計算剖面圖位置
    if position is not None:
        pos_x = position["x"] * _MM_TO_M
        pos_y = position["y"] * _MM_TO_M
    else:
        pos_x = outline[2] + 0.05  # 右邊 + 50mm
        pos_y = (outline[1] + outline[3]) / 2  # 垂直居中

    # 剖面線座標 mm -> meters
    start_x = section_line["start"]["x"] * _MM_TO_M
    start_y = section_line["start"]["y"] * _MM_TO_M
    end_x = section_line["end"]["x"] * _MM_TO_M
    end_y = section_line["end"]["y"] * _MM_TO_M

    # COM 流程
    if not drawing.ActivateView(parent_view):
        raise SWError(f"無法啟動視圖: {parent_view}")
    drawing.ClearSelection2(True)

    sketch_mgr = drawing.SketchManager
    seg = sketch_mgr.CreateLine(start_x, start_y, 0, end_x, end_y, 0)
    if seg is None:
        raise SWError("無法繪製剖面線")

    section_view = drawing.CreateSectionViewAt5(
        pos_x, pos_y, 0,
        label,
        0,      # options
        None,   # excludedComponents
        0,      # sectionDepth
    )
    if section_view is None:
        raise SWError("無法建立剖面圖")

    # 設定比例
    if scale is not None:
        try:
            section_view.ScaleRatio = (1.0, scale)
        except Exception:
            pass

    # Rebuild
    try:
        drawing.EditRebuild3()
    except Exception:
        pass

    # 讀取結果
    sv_name = section_view.Name
    sv_pos = section_view.Position
    sv_scale = section_view.ScaleRatio

    return {
        "status": "done",
        "view_name": sv_name,
        "label": label,
        "position": {
            "x": round(sv_pos[0] * 1000, 1),
            "y": round(sv_pos[1] * 1000, 1),
        },
        "scale": f"{sv_scale[0]:g}:{sv_scale[1]:g}",
        "parent_view": parent_view,
    }


# Detail view constants
_SW_DET_VIEW_STANDARD = 0
_SW_DET_CIRCLE_CIRCLE = 1


def _insert_detail_view(
    parent_view: str,
    center: dict,
    radius: float,
    label: str = "A",
    scale: float = 2.0,
    position: dict | None = None,
) -> dict:
    """在父視圖上畫放大圓，建立局部放大圖。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    if scale <= 0:
        raise SWError("放大比例必須大於 0")

    if radius <= 0:
        raise SWError("放大區域半徑必須大於 0")

    # 找父視圖
    from tools.annotation import _get_drawing_views

    views = _get_drawing_views(drawing, parent_view)
    if not views:
        raise SWError(f"找不到視圖: {parent_view}")

    _, view_obj = views[0]

    # 取得父視圖 outline（meters）
    outline = view_obj.GetOutline  # [xmin, ymin, xmax, ymax]

    # 計算局部放大圖位置
    if position is not None:
        pos_x = position["x"] * _MM_TO_M
        pos_y = position["y"] * _MM_TO_M
    else:
        pos_x = outline[2] + 0.05  # 右邊 + 50mm
        pos_y = outline[3]          # 上緣齊平

    # 放大圓座標: center/radius are SHEET mm. The circle is drawn in the parent
    # view's sketch, whose space is model-oriented (unscaled, un-rotated):
    #   sheet = C + s * R(angle) * p   =>   p = R(-angle) * (sheet - C) / s
    import math
    try:
        vpos = view_obj.Position
        vs = float(view_obj.ScaleDecimal)
        try:
            ang = float(view_obj.Angle)
        except Exception:
            ang = 0.0
        dx = center["x"] * _MM_TO_M - vpos[0]
        dy = center["y"] * _MM_TO_M - vpos[1]
        ca, sa = math.cos(-ang), math.sin(-ang)
        cx = (dx * ca - dy * sa) / vs
        cy = (dx * sa + dy * ca) / vs
        r = radius * _MM_TO_M / vs
    except Exception:
        cx = center["x"] * _MM_TO_M
        cy = center["y"] * _MM_TO_M
        r = radius * _MM_TO_M

    # COM 流程
    if not drawing.ActivateView(parent_view):
        raise SWError(f"無法啟動視圖: {parent_view}")
    drawing.ClearSelection2(True)

    sketch_mgr = drawing.SketchManager
    circle = sketch_mgr.CreateCircle(cx, cy, 0, cx + r, cy, 0)
    if circle is None:
        raise SWError("無法繪製放大區域圓")

    detail_view = drawing.CreateDetailViewAt4(
        pos_x, pos_y, 0,
        _SW_DET_VIEW_STANDARD,     # style
        scale, 1.0,                 # scale1, scale2
        label,
        _SW_DET_CIRCLE_CIRCLE,     # showtype
        True,                       # fullOutline
        False,                      # jaggedOutline
        False,                      # noOutline
        5,                          # shapeIntensity
    )
    if detail_view is None:
        raise SWError("無法建立局部放大圖")

    # scale is a DECIMAL view scale (1.0 = 1:1, 0.5 = 1:2, 2.0 = 2:1)
    try:
        detail_view.ScaleDecimal = float(scale)
    except Exception:
        try:
            detail_view.ScaleRatio = (float(scale), 1.0)
        except Exception:
            pass

    # Rebuild
    try:
        drawing.EditRebuild3()
    except Exception:
        pass

    # 讀取結果
    dv_name = detail_view.Name
    dv_pos = detail_view.Position
    dv_scale = detail_view.ScaleRatio

    return {
        "status": "done",
        "view_name": dv_name,
        "label": label,
        "position": {
            "x": round(dv_pos[0] * 1000, 1),
            "y": round(dv_pos[1] * 1000, 1),
        },
        "scale": f"{dv_scale[0]:g}:{dv_scale[1]:g}",
        "parent_view": parent_view,
    }


def _insert_custom_view(
    source_doc: str,
    view_name: str | None = None,
    orientation: dict | None = None,
    position: dict | None = None,
    scale: float | None = None,
    display_mode: str = "hidden_lines_removed",
    paper_size: str = "A3",
) -> dict:
    """插入自訂角度視圖（路徑 1: 具名視角 / 路徑 2: 自訂角度）。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    # 參數互斥檢查
    if view_name is not None and orientation is not None:
        raise SWError("view_name 和 orientation 不能同時指定")
    if view_name is None and orientation is None:
        raise SWError("必須指定 view_name 或 orientation 其中之一")

    # display_mode 驗證
    dm_value = SW_DISPLAY_MODES.get(display_mode)
    if dm_value is None:
        raise SWError(
            f"不支援的 display_mode: {display_mode}，"
            f"可用: {', '.join(SW_DISPLAY_MODES)}"
        )

    if scale is not None and scale <= 0:
        raise SWError("比例分母必須大於 0")

    # 計算位置 (mm → meters)
    if position is not None:
        pos_x = position["x"] * _MM_TO_M
        pos_y = position["y"] * _MM_TO_M
    else:
        sheet_w, sheet_h = PAPER_SIZE_MM.get(
            paper_size.upper(), PAPER_SIZE_MM["A3"],
        )
        pos_x = sheet_w / 2 * _MM_TO_M
        pos_y = sheet_h / 2 * _MM_TO_M

    if view_name is not None:
        # 路徑 1: 具名視角
        sw_name = SW_VIEW_NAMES.get(view_name.lower())
        if sw_name is None:
            valid = ", ".join(SW_VIEW_NAMES.keys())
            raise SWError(f"不支援的 view_name: {view_name}，可用: {valid}")

        view = drawing.CreateDrawViewFromModelView3(
            source_doc, sw_name, pos_x, pos_y, 0,
        )
        if view is None:
            raise SWError(f"CreateDrawViewFromModelView3 失敗: {sw_name}")
    else:
        # 路徑 2: 自訂角度（Task 3 實作）
        view = _create_custom_orientation_view(
            app, drawing, source_doc, orientation, pos_x, pos_y,
        )

    # 共用後處理: display mode
    try:
        view.SetDisplayMode3(False, dm_value, False, False)
    except Exception as e:
        logger.warning("SetDisplayMode3 失敗（非致命）: %s", e)

    # 共用後處理: scale
    if scale is not None:
        try:
            view.ScaleRatio = (1.0, scale)
        except Exception as e:
            logger.warning("ScaleRatio 設定失敗（非致命）: %s", e)

    # Rebuild
    try:
        drawing.EditRebuild3()
    except Exception:
        pass

    # 讀取結果
    v_name = view.Name
    v_pos = view.Position
    v_scale = view.ScaleRatio

    return {
        "status": "done",
        "view_name": v_name,
        "position": {
            "x": round(v_pos[0] * 1000, 1),
            "y": round(v_pos[1] * 1000, 1),
        },
        "scale": f"{v_scale[0]:g}:{v_scale[1]:g}",
        "display_mode": display_mode,
    }


def _create_custom_orientation_view(app, drawing, source_doc, orientation, pos_x, pos_y):
    """路徑 2: 切到來源模型設定旋轉方向，建立暫存視圖，再切回 Drawing 使用。"""
    drawing_title = drawing.GetTitle
    source_name = os.path.basename(source_doc)

    # 切到來源模型
    model = app.ActivateDoc(source_name)
    if model is None:
        model = app.ActivateDoc(source_doc)
    if model is None:
        raise SWError(f"無法切換到來源模型: {source_doc}")

    try:
        # reset 到前視圖作為基準
        model.ShowNamedView2("", 1)  # 1 = swFrontView

        # 建構旋轉 MathTransform 並設定到模型視圖
        model_view = model.ActiveView
        transform = model_view.Orientation3

        arr = _euler_to_transform_array(
            orientation["x"], orientation["y"], orientation["z"],
        )
        # 注意：pywin32 late-binding 下 ArrayData 賦值若失敗，
        # 需改用 win32com.client.VARIANT(pythoncom.VT_ARRAY|VT_R8, arr)
        transform.ArrayData = arr
        model_view.Orientation3 = transform

        # 命名暫存視圖
        model.NameView(_TEMP_VIEW_NAME)

        # 切回 Drawing
        app.ActivateDoc(drawing_title)

        # 建立視圖
        view = drawing.CreateDrawViewFromModelView3(
            source_doc, _TEMP_VIEW_NAME, pos_x, pos_y, 0,
        )
        if view is None:
            raise SWError("CreateDrawViewFromModelView3 自訂角度視圖失敗")

        return view
    finally:
        # 清理暫存視圖（即使建立失敗也要清理）
        try:
            cleanup_model = app.ActivateDoc(source_name)
            if cleanup_model is None:
                cleanup_model = app.ActivateDoc(source_doc)
            if cleanup_model is not None:
                cleanup_model.DeleteNamedView(_TEMP_VIEW_NAME)
            app.ActivateDoc(drawing_title)
        except Exception as e:
            logger.warning("清理暫存視圖失敗（非致命）: %s", e)


def _euler_to_transform_array(x_deg: float, y_deg: float, z_deg: float) -> list[float]:
    """Euler XYZ extrinsic rotation → SolidWorks MathTransform 16-element array.

    Rotation order: X → Y → Z (extrinsic) = Rz * Ry * Rx.
    Array layout: [R00,R01,R02,0, R10,R11,R12,0, R20,R21,R22,0, Tx,Ty,Tz,Scale]
    """
    x = math.radians(x_deg)
    y = math.radians(y_deg)
    z = math.radians(z_deg)

    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)

    return [
        cz * cy,                      cz * sy * sx - sz * cx,     cz * sy * cx + sz * sx,   0.0,
        sz * cy,                      sz * sy * sx + cz * cx,     sz * sy * cx - cz * sx,   0.0,
        -sy,                          cy * sx,                     cy * cx,                   0.0,
        0.0,                          0.0,                         0.0,                       1.0,
    ]
