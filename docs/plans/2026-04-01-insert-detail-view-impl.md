# insert_detail_view Implementation Plan

Goal: 新增 `insert_detail_view` MCP tool，在父視圖上畫放大圓建立局部放大圖

Architecture: 在 `src/tools/drawing.py` 新增 `_insert_detail_view` COM 函式和 `insert_detail_view` async handler。使用既有的 `_get_drawing_views`（從 annotation.py import）找父視圖，SketchManager.CreateCircle 畫放大圓，CreateDetailViewAt4 建立局部放大圖。

Tech Stack: pywin32 COM, FastMCP, pytest

---

### Task 1: insert_detail_view 實作 + 3 tests

Files:
- Modify: `src/tools/drawing.py` — 新增 `_insert_detail_view` + handler 註冊
- Modify: `tests/test_drawing.py` — 新增 3 個測試

Step 1: 在 `tests/test_drawing.py` 新增 3 個測試

在檔案頂部 import 區塊，把 `_insert_detail_view` 加入 import：

```python
try:
    from tools.drawing import _insert_section_view, _insert_detail_view
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False
```

在檔案底部新增 3 個測試：

```python
# === insert_detail_view tests ===


# --- Test 4: detail view 完整 COM 流程 ---

def test_insert_detail_view_com_flow():
    """mock 完整 COM 流程：ActivateView → CreateCircle → CreateDetailViewAt4。"""
    from tools.drawing import _insert_detail_view

    outline_m = [0.4364, 0.4332, 0.5148, 0.4919]  # meters
    drawing, views = _make_mock_drawing_with_views([
        ("工程視圖1", outline_m),
    ])

    # Mock detail view result
    detail_view = _make_mock_com()
    detail_view.Name = "局部視圖 A (2 : 1)"
    detail_view.Position = (0.5648, 0.4919)
    detail_view.ScaleRatio = (2.0, 1.0)
    drawing.CreateDetailViewAt4.return_value = detail_view

    # Mock SketchManager
    sketch_mgr = _make_mock_com()
    circle = _make_mock_com()
    sketch_mgr.CreateCircle.return_value = circle
    type(drawing).SketchManager = property(lambda self: sketch_mgr)

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_detail_view(
            parent_view="工程視圖1",
            center={"x": 475.6, "y": 462.6},
            radius=15.0,
            label="A",
            scale=2.0,
            position={"x": 564.8, "y": 491.9},
        )

    assert result["status"] == "done"
    assert result["view_name"] == "局部視圖 A (2 : 1)"
    assert result["label"] == "A"
    assert result["parent_view"] == "工程視圖1"
    assert "position" in result
    assert "scale" in result

    # 驗證 COM 呼叫順序
    drawing.ActivateView.assert_called_once_with("工程視圖1")
    drawing.ClearSelection2.assert_called_once_with(True)
    sketch_mgr.CreateCircle.assert_called_once()
    drawing.CreateDetailViewAt4.assert_called_once()

    # 驗證 CreateCircle 座標（mm → meters）
    cc_args = sketch_mgr.CreateCircle.call_args[0]
    assert abs(cc_args[0] - 0.4756) < 0.001   # center_x
    assert abs(cc_args[1] - 0.4626) < 0.001   # center_y
    assert cc_args[2] == 0                      # center_z
    assert abs(cc_args[3] - 0.4906) < 0.001   # edge_x = center_x + radius
    assert abs(cc_args[4] - 0.4626) < 0.001   # edge_y = center_y
    assert cc_args[5] == 0                      # edge_z

    # 驗證 CreateDetailViewAt4 參數
    cdv_args = drawing.CreateDetailViewAt4.call_args[0]
    assert abs(cdv_args[0] - 0.5648) < 0.001  # pos_x
    assert abs(cdv_args[1] - 0.4919) < 0.001  # pos_y
    assert cdv_args[2] == 0                     # z
    assert cdv_args[3] == 0                     # style = swDetViewSTANDARD
    assert cdv_args[4] == 2.0                   # scale1
    assert cdv_args[5] == 1.0                   # scale2
    assert cdv_args[6] == "A"                   # label
    assert cdv_args[7] == 1                     # showtype = swDetCircleCIRCLE


# --- Test 5: detail view auto position ---

def test_insert_detail_view_auto_position():
    """不提供 position，驗證自動計算（父視圖右上方）。"""
    from tools.drawing import _insert_detail_view

    outline_m = [0.4364, 0.4332, 0.5148, 0.4919]
    drawing, views = _make_mock_drawing_with_views([
        ("工程視圖1", outline_m),
    ])

    detail_view = _make_mock_com()
    detail_view.Name = "局部視圖 A (2 : 1)"
    detail_view.Position = (0.5648, 0.4919)
    detail_view.ScaleRatio = (2.0, 1.0)
    drawing.CreateDetailViewAt4.return_value = detail_view

    sketch_mgr = _make_mock_com()
    sketch_mgr.CreateCircle.return_value = _make_mock_com()
    type(drawing).SketchManager = property(lambda self: sketch_mgr)

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_detail_view(
            parent_view="工程視圖1",
            center={"x": 475.6, "y": 462.6},
            radius=15.0,
            label="A",
            scale=2.0,
            position=None,  # auto
        )

    # 驗證 CreateDetailViewAt4 的 position 參數
    cdv_args = drawing.CreateDetailViewAt4.call_args[0]
    expected_x = outline_m[2] + 0.05   # 右邊 + 50mm
    expected_y = outline_m[3]          # 上緣齊平
    assert abs(cdv_args[0] - expected_x) < 0.001
    assert abs(cdv_args[1] - expected_y) < 0.001


# --- Test 6: detail view parent not found ---

def test_insert_detail_view_parent_not_found():
    """父視圖名稱不存在時 raise SWError。"""
    from tools.drawing import _insert_detail_view
    from errors import SWError

    drawing, _ = _make_mock_drawing_with_views([
        ("工程視圖1", [0.4, 0.4, 0.5, 0.5]),
    ])

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        with pytest.raises(SWError, match="找不到"):
            _insert_detail_view(
                parent_view="不存在的視圖",
                center={"x": 100, "y": 200},
                radius=10.0,
            )
```

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_drawing.py -v`
Expected: FAIL（`_insert_detail_view` 尚未定義）

Step 3: 在 `src/tools/drawing.py` 實作

在 `register_tools` 函式內，`insert_section_view` handler 之後（約 line 145），新增 handler：

```python
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
        scale: 放大比例分子（如 2 表示 2:1），預設 2。
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
```

在檔案底部（`_insert_section_view` 之後）新增同步函式：

```python
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

    # 放大圓座標 mm → meters
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
```

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_drawing.py -v`
Expected: PASS（6 tests）

Step 5: 跑全部測試確認沒有 regression

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS

Step 6: Commit

```bash
git add src/tools/drawing.py tests/test_drawing.py
git commit -m "feat: insert_detail_view tool — 局部放大圖建立 + 3 tests"
```
