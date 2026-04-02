# insert_custom_view Implementation Plan

Goal: 新增 `insert_custom_view` MCP tool，支援具名視角與自訂 XYZ 旋轉角度兩種模式

Architecture: 在 `src/tools/drawing.py` 新增 `_euler_to_transform_array`（純函式）、`_insert_custom_view`（COM 主函式，雙路徑分支）、`_create_custom_orientation_view`（路徑 2 的 COM 操作）。具名視角直接用 `CreateDrawViewFromModelView3`；自訂角度切到來源模型設定旋轉方向、命名暫存視圖、切回 Drawing 建立視圖、清理暫存。

Tech Stack: pywin32 COM, FastMCP, pytest, math（標準庫）

---

### Task 1: _euler_to_transform_array 旋轉矩陣函式 + 4 tests

Files:
- Modify: `src/tools/drawing.py` — 新增 `_euler_to_transform_array` 純函式 + `SW_DISPLAY_MODES` 常數 + `_TEMP_VIEW_NAME` 常數
- Create: `tests/test_custom_view.py` — 4 個旋轉矩陣測試

Step 1: 建立 `tests/test_custom_view.py`，寫 4 個旋轉矩陣測試

```python
"""測試 insert_custom_view（不需 SolidWorks）。"""

import math
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

try:
    from tools.drawing import (
        _euler_to_transform_array,
        _insert_custom_view,
        SW_DISPLAY_MODES,
    )
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(
    not HAS_DEPS, reason="需要 mcp/pywin32 依賴（僅在 SW 主機上可用）"
)


def _make_mock_com(attrs=None, **kwargs):
    """建立模擬 COM 物件的 mock（有 _oleobj_ 屬性）。"""
    m = MagicMock(**kwargs)
    m._oleobj_ = True
    if attrs:
        for k, v in attrs.items():
            setattr(m, k, v)
    return m


# === Euler rotation matrix tests ===


def test_euler_identity():
    """(0, 0, 0) → 單位矩陣。"""
    arr = _euler_to_transform_array(0, 0, 0)
    assert len(arr) == 16
    # 3x3 identity
    assert abs(arr[0] - 1.0) < 1e-10   # R00
    assert abs(arr[5] - 1.0) < 1e-10   # R11
    assert abs(arr[10] - 1.0) < 1e-10  # R22
    # off-diagonals = 0
    for i in [1, 2, 4, 6, 8, 9]:
        assert abs(arr[i]) < 1e-10
    # padding zeros + scale
    assert arr[3] == 0.0
    assert arr[7] == 0.0
    assert arr[11] == 0.0
    assert arr[12] == 0.0
    assert arr[13] == 0.0
    assert arr[14] == 0.0
    assert arr[15] == 1.0


def test_euler_rotate_x_90():
    """繞 X 軸轉 90°: Y→Z, Z→-Y。"""
    arr = _euler_to_transform_array(90, 0, 0)
    # R = [[1,0,0],[0,0,-1],[0,1,0]]
    assert abs(arr[0] - 1.0) < 1e-10
    assert abs(arr[5] - 0.0) < 1e-10
    assert abs(arr[6] - (-1.0)) < 1e-10
    assert abs(arr[9] - 1.0) < 1e-10
    assert abs(arr[10] - 0.0) < 1e-10


def test_euler_rotate_y_90():
    """繞 Y 軸轉 90°: X→-Z, Z→X。"""
    arr = _euler_to_transform_array(0, 90, 0)
    # R = [[0,0,1],[0,1,0],[-1,0,0]]
    assert abs(arr[0] - 0.0) < 1e-10
    assert abs(arr[2] - 1.0) < 1e-10
    assert abs(arr[5] - 1.0) < 1e-10
    assert abs(arr[8] - (-1.0)) < 1e-10
    assert abs(arr[10] - 0.0) < 1e-10


def test_euler_rotate_z_90():
    """繞 Z 軸轉 90°: X→Y, Y→-X。"""
    arr = _euler_to_transform_array(0, 0, 90)
    # R = [[0,-1,0],[1,0,0],[0,0,1]]
    assert abs(arr[0] - 0.0) < 1e-10
    assert abs(arr[1] - (-1.0)) < 1e-10
    assert abs(arr[4] - 1.0) < 1e-10
    assert abs(arr[5] - 0.0) < 1e-10
    assert abs(arr[10] - 1.0) < 1e-10
```

Step 2: 在 `src/tools/drawing.py` 新增常數和旋轉函式

在 `SW_DISPLAY_MODE_HIDDEN_GREYED = 6` 之後（約 line 18），新增：

```python
SW_DISPLAY_MODES = {
    "wireframe": 1,              # swWIREFRAME
    "hidden_lines_removed": 6,   # swHIDDEN_GREYED
    "shaded": 3,                 # swSHADED
}

_TEMP_VIEW_NAME = "_mcp_custom_temp"
```

在 `_insert_detail_view` 函式之後（檔案底部），新增：

```python
def _euler_to_transform_array(x_deg: float, y_deg: float, z_deg: float) -> list[float]:
    """Euler XYZ extrinsic rotation → SolidWorks MathTransform 16-element array.

    Rotation order: X → Y → Z (extrinsic) = Rz * Ry * Rx.
    Array layout: [R00,R01,R02,0, R10,R11,R12,0, R20,R21,R22,0, Tx,Ty,Tz,Scale]
    """
    import math as _math

    x = _math.radians(x_deg)
    y = _math.radians(y_deg)
    z = _math.radians(z_deg)

    cx, sx = _math.cos(x), _math.sin(x)
    cy, sy = _math.cos(y), _math.sin(y)
    cz, sz = _math.cos(z), _math.sin(z)

    return [
        cz * cy,                      cz * sy * sx - sz * cx,     cz * sy * cx + sz * sx,   0.0,
        sz * cy,                      sz * sy * sx + cz * cx,     sz * sy * cx - cz * sx,   0.0,
        -sy,                          cy * sx,                     cy * cx,                   0.0,
        0.0,                          0.0,                         0.0,                       1.0,
    ]
```

Step 3: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_custom_view.py -v`
Expected: PASS（4 tests）

Step 4: Commit

```bash
git add src/tools/drawing.py tests/test_custom_view.py
git commit -m "feat: _euler_to_transform_array 旋轉矩陣函式 + 4 tests"
```

---

### Task 2: insert_custom_view 具名視角路徑 + handler + 5 tests

Files:
- Modify: `src/tools/drawing.py` — 新增 `_insert_custom_view` COM 函式（僅路徑 1）+ handler 註冊
- Modify: `tests/test_custom_view.py` — 新增 5 個測試

Step 1: 在 `tests/test_custom_view.py` 底部新增 5 個測試

```python
# === Parameter validation tests ===


def test_custom_view_both_params_error():
    """同時指定 view_name 和 orientation 時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="不能同時"):
            _insert_custom_view(
                source_doc="C:\\test.sldprt",
                view_name="front",
                orientation={"x": 0, "y": 0, "z": 0},
            )


def test_custom_view_no_params_error():
    """view_name 和 orientation 都不指定時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="必須指定"):
            _insert_custom_view(source_doc="C:\\test.sldprt")


def test_custom_view_invalid_view_name():
    """無效的 view_name 時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="不支援的 view_name"):
            _insert_custom_view(
                source_doc="C:\\test.sldprt",
                view_name="invalid_view",
            )


def test_custom_view_invalid_display_mode():
    """無效的 display_mode 時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="不支援的 display_mode"):
            _insert_custom_view(
                source_doc="C:\\test.sldprt",
                view_name="front",
                display_mode="invalid_mode",
            )


# === Named view path tests ===


def test_custom_view_named_view_com_flow():
    """具名視角路徑：驗證 CreateDrawViewFromModelView3 呼叫 + 後處理。"""
    drawing = _make_mock_com()
    view = _make_mock_com()
    view.Name = "Drawing View1"
    view.Position = (0.210, 0.1485)
    view.ScaleRatio = (1.0, 2.0)
    drawing.CreateDrawViewFromModelView3.return_value = view

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = drawing
        inst.get_app.return_value = app

        result = _insert_custom_view(
            source_doc="C:\\models\\test.sldprt",
            view_name="isometric",
            position={"x": 210.0, "y": 148.5},
            scale=2.0,
            display_mode="shaded",
        )

    assert result["status"] == "done"
    assert result["view_name"] == "Drawing View1"
    assert result["display_mode"] == "shaded"
    assert result["position"] == {"x": 210.0, "y": 148.5}

    # 驗證 CreateDrawViewFromModelView3 參數
    cdv_args = drawing.CreateDrawViewFromModelView3.call_args[0]
    assert cdv_args[0] == "C:\\models\\test.sldprt"
    assert cdv_args[1] == "*等角視"  # isometric
    assert abs(cdv_args[2] - 0.210) < 0.001   # x (meters)
    assert abs(cdv_args[3] - 0.1485) < 0.001  # y (meters)

    # 驗證 SetDisplayMode3
    view.SetDisplayMode3.assert_called_once_with(False, 3, False, False)  # 3 = shaded

    # 驗證 ScaleRatio
    assert view.ScaleRatio == (1.0, 2.0)
```

Step 2: 在 `src/tools/drawing.py` 的 `register_tools` 內，`insert_detail_view` handler 之後（約 line 172），新增 handler：

```python
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
```

在 `_euler_to_transform_array` 之後，新增 `_insert_custom_view` COM 函式（先只做路徑 1）：

```python
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
        if scale <= 0:
            raise SWError("比例必須大於 0")
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
    """路徑 2 stub — Task 3 實作。"""
    raise SWError("自訂角度視圖尚未實作")
```

Step 3: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_custom_view.py -v`
Expected: PASS（9 tests）

Step 4: 跑全部測試確認沒有 regression

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS

Step 5: Commit

```bash
git add src/tools/drawing.py tests/test_custom_view.py
git commit -m "feat: insert_custom_view 具名視角路徑 + handler + 5 tests"
```

---

### Task 3: 自訂角度路徑 + 3 tests

Files:
- Modify: `src/tools/drawing.py` — 實作 `_create_custom_orientation_view`
- Modify: `tests/test_custom_view.py` — 新增 3 個測試

Step 1: 在 `tests/test_custom_view.py` 底部新增 3 個測試

```python
# === Custom orientation path tests ===


def _make_mock_app_for_custom_view():
    """建立 custom orientation 路徑需要的完整 mock 體系。

    回傳 (app, drawing, model, model_view, transform, created_view)。
    """
    drawing = _make_mock_com()
    type(drawing).GetTitle = property(lambda self: "Drawing1")

    model = _make_mock_com()
    model_view = _make_mock_com()
    transform = _make_mock_com()
    model_view.Orientation3 = transform
    model.ActiveView = model_view

    created_view = _make_mock_com()
    created_view.Name = "Drawing View2"
    created_view.Position = (0.210, 0.1485)
    created_view.ScaleRatio = (1.0, 1.0)
    drawing.CreateDrawViewFromModelView3.return_value = created_view

    app = _make_mock_com()
    app.ActiveDoc = drawing
    # ActivateDoc: 檔名 → model, Drawing1 → drawing
    def activate_doc(name):
        if name == "Drawing1":
            app.ActiveDoc = drawing
            return drawing
        else:
            app.ActiveDoc = model
            return model
    app.ActivateDoc.side_effect = activate_doc

    return app, drawing, model, model_view, transform, created_view


def test_custom_view_orientation_com_flow():
    """自訂角度路徑：驗證完整 COM 呼叫順序。"""
    app, drawing, model, model_view, transform, view = (
        _make_mock_app_for_custom_view()
    )

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        result = _insert_custom_view(
            source_doc="C:\\models\\test.sldprt",
            orientation={"x": 30, "y": 45, "z": 0},
            position={"x": 210.0, "y": 148.5},
        )

    assert result["status"] == "done"
    assert result["view_name"] == "Drawing View2"

    # 驗證 COM 呼叫順序
    # 1. ActivateDoc 切到來源模型
    first_activate = app.ActivateDoc.call_args_list[0]
    assert first_activate[0][0] == "test.sldprt"

    # 2. ShowNamedView2 reset 到前視圖
    model.ShowNamedView2.assert_called_once_with("", 1)

    # 3. Orientation3 被設定（transform.ArrayData 被寫入）
    assert transform.ArrayData is not None

    # 4. NameView 命名暫存視圖
    model.NameView.assert_called_once_with("_mcp_custom_temp")

    # 5. ActivateDoc 切回 Drawing
    # 6. CreateDrawViewFromModelView3 建立視圖
    cdv_args = drawing.CreateDrawViewFromModelView3.call_args[0]
    assert cdv_args[0] == "C:\\models\\test.sldprt"
    assert cdv_args[1] == "_mcp_custom_temp"

    # 7. 清理：DeleteNamedView
    model.DeleteNamedView.assert_called_once_with("_mcp_custom_temp")


def test_custom_view_orientation_cleanup_on_failure():
    """CreateDrawViewFromModelView3 失敗時，暫存視圖仍被清理。"""
    from errors import SWError

    app, drawing, model, model_view, transform, _ = (
        _make_mock_app_for_custom_view()
    )
    # 讓 CreateDrawViewFromModelView3 回傳 None（失敗）
    drawing.CreateDrawViewFromModelView3.return_value = None

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="自訂角度視圖失敗"):
            _insert_custom_view(
                source_doc="C:\\models\\test.sldprt",
                orientation={"x": 0, "y": 90, "z": 0},
                position={"x": 210.0, "y": 148.5},
            )

    # 即使失敗，DeleteNamedView 仍被呼叫
    model.DeleteNamedView.assert_called_once_with("_mcp_custom_temp")


def test_custom_view_auto_position_paper_center():
    """不提供 position 時預設紙張中央（A3: 210, 148.5mm）。"""
    app, drawing, model, model_view, transform, view = (
        _make_mock_app_for_custom_view()
    )

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        result = _insert_custom_view(
            source_doc="C:\\models\\test.sldprt",
            orientation={"x": 0, "y": 0, "z": 0},
            paper_size="A3",
        )

    cdv_args = drawing.CreateDrawViewFromModelView3.call_args[0]
    assert abs(cdv_args[2] - 0.210) < 0.001    # A3 寬 420/2 = 210mm
    assert abs(cdv_args[3] - 0.1485) < 0.001   # A3 高 297/2 = 148.5mm
```

Step 2: 在 `src/tools/drawing.py` 實作 `_create_custom_orientation_view`

替換 stub：

```python
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
```

Step 3: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_custom_view.py -v`
Expected: PASS（12 tests）

Step 4: 跑全部測試確認沒有 regression

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS

Step 5: Commit

```bash
git add src/tools/drawing.py tests/test_custom_view.py
git commit -m "feat: insert_custom_view 自訂角度路徑 — COM 旋轉 + 暫存視圖 + 清理"
```
