# insert_section_view Implementation Plan

Goal: 新增 `insert_section_view` MCP tool，在父視圖上畫剖面線建立剖面圖

Architecture: 在 `src/tools/drawing.py` 新增 `_insert_section_view` COM 函式和 `insert_section_view` async handler。使用既有的 `_get_drawing_views`（從 annotation.py import）找父視圖，SketchManager.CreateLine 畫剖面線，CreateSectionViewAt5 建立剖面圖。

Tech Stack: pywin32 COM, FastMCP, pytest

---

### Task 1: insert_section_view 實作 + 3 tests

Files:
- Modify: `src/tools/drawing.py` — 新增 `_insert_section_view` + handler 註冊
- Create: `tests/test_drawing.py` — 3 個測試
- Modify: `src/server.py` — 確認 drawing.py 的 register_tools 已註冊（應已存在）

Step 1: 寫 3 個失敗的測試

建立 `tests/test_drawing.py`：

```python
"""測試 drawing tools（不需 SolidWorks）。"""

import pytest
from unittest.mock import MagicMock, patch

try:
    from tools.drawing import _insert_section_view
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


def _make_mock_drawing_with_views(view_names_and_outlines):
    """建立 mock drawing + FeatureTree，包含指定視圖和 outline。

    view_names_and_outlines: [("工程視圖1", [xmin, ymin, xmax, ymax]), ...]
    outline 單位為 meters。
    """
    drawing = _make_mock_com()

    subs = []
    for i, (name, outline) in enumerate(view_names_and_outlines):
        sub = _make_mock_com()
        sub.GetTypeName2 = "AbsoluteView" if i == 0 else "UnfoldedView"
        sub.Name = name
        view_obj = _make_mock_com()
        view_obj.GetOutline = outline
        # Position = center of outline
        cx = (outline[0] + outline[2]) / 2
        cy = (outline[1] + outline[3]) / 2
        view_obj.Position = (cx, cy)
        view_obj.ScaleRatio = (1.0, 5.0)
        sub.GetSpecificFeature2.return_value = view_obj
        subs.append((sub, view_obj))

    for i, (sub, _) in enumerate(subs):
        next_sub = subs[i + 1][0] if i + 1 < len(subs) else None
        type(sub).GetNextSubFeature = property(lambda self, ns=next_sub: ns)

    sheet = _make_mock_com()
    sheet.GetTypeName2 = "DrSheet"
    sheet.Name = "圖頁1"
    first_sub = subs[0][0] if subs else None
    type(sheet).GetFirstSubFeature = property(lambda self: first_sub)

    type(drawing).FirstFeature = property(lambda self: sheet)
    type(sheet).GetNextFeature = property(lambda self: None)

    return drawing, {name: vo for (_, vo), (name, _) in zip(subs, view_names_and_outlines)}


# --- Test 1: 完整 COM 流程 ---

def test_insert_section_view_com_flow():
    """mock 完整 COM 流程：ActivateView → CreateLine → CreateSectionViewAt5。"""
    from tools.drawing import _insert_section_view

    outline_m = [0.4364, 0.4332, 0.5148, 0.4919]  # meters
    drawing, views = _make_mock_drawing_with_views([
        ("工程視圖1", outline_m),
    ])

    # Mock section view result
    section_view = _make_mock_com()
    section_view.Name = "剖面視圖 A-A"
    section_view.Position = (0.5648, 0.4626)
    section_view.ScaleRatio = (1.0, 5.0)
    drawing.CreateSectionViewAt5.return_value = section_view

    # Mock SketchManager
    sketch_mgr = _make_mock_com()
    seg = _make_mock_com()
    sketch_mgr.CreateLine.return_value = seg
    type(drawing).SketchManager = property(lambda self: sketch_mgr)

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_section_view(
            parent_view="工程視圖1",
            section_line={
                "start": {"x": 475.6, "y": 497.8},
                "end": {"x": 475.6, "y": 427.3},
            },
            label="A",
            position={"x": 564.8, "y": 462.6},
            scale=None,
        )

    assert result["status"] == "done"
    assert result["view_name"] == "剖面視圖 A-A"
    assert result["label"] == "A"
    assert result["parent_view"] == "工程視圖1"
    assert "position" in result

    # 驗證 COM 呼叫
    drawing.ActivateView.assert_called_once_with("工程視圖1")
    drawing.ClearSelection2.assert_called_once_with(True)
    sketch_mgr.CreateLine.assert_called_once()
    drawing.CreateSectionViewAt5.assert_called_once()

    # 驗證 CreateLine 座標（mm → meters）
    cl_args = sketch_mgr.CreateLine.call_args[0]
    assert abs(cl_args[0] - 0.4756) < 0.001  # start_x
    assert abs(cl_args[1] - 0.4978) < 0.001  # start_y
    assert cl_args[2] == 0                     # z
    assert abs(cl_args[3] - 0.4756) < 0.001  # end_x
    assert abs(cl_args[4] - 0.4273) < 0.001  # end_y


# --- Test 2: auto position ---

def test_insert_section_view_auto_position():
    """不提供 position，驗證自動計算（父視圖右側 +50mm）。"""
    from tools.drawing import _insert_section_view

    outline_m = [0.4364, 0.4332, 0.5148, 0.4919]
    drawing, views = _make_mock_drawing_with_views([
        ("工程視圖1", outline_m),
    ])

    section_view = _make_mock_com()
    section_view.Name = "剖面視圖 A-A"
    section_view.Position = (0.5648, 0.4626)
    section_view.ScaleRatio = (1.0, 5.0)
    drawing.CreateSectionViewAt5.return_value = section_view

    sketch_mgr = _make_mock_com()
    sketch_mgr.CreateLine.return_value = _make_mock_com()
    type(drawing).SketchManager = property(lambda self: sketch_mgr)

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_section_view(
            parent_view="工程視圖1",
            section_line={
                "start": {"x": 475.6, "y": 497.8},
                "end": {"x": 475.6, "y": 427.3},
            },
            label="A",
            position=None,  # auto
            scale=None,
        )

    # 驗證 CreateSectionViewAt5 的 position 參數
    csv_args = drawing.CreateSectionViewAt5.call_args[0]
    expected_x = outline_m[2] + 0.05  # 右邊 + 50mm
    expected_y = (outline_m[1] + outline_m[3]) / 2  # 垂直居中
    assert abs(csv_args[0] - expected_x) < 0.001
    assert abs(csv_args[1] - expected_y) < 0.001


# --- Test 3: parent view not found ---

def test_insert_section_view_parent_not_found():
    """父視圖名稱不存在時 raise SWError。"""
    from tools.drawing import _insert_section_view
    from errors import SWError

    drawing, _ = _make_mock_drawing_with_views([
        ("工程視圖1", [0.4, 0.4, 0.5, 0.5]),
    ])

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        with pytest.raises(SWError, match="找不到"):
            _insert_section_view(
                parent_view="不存在的視圖",
                section_line={
                    "start": {"x": 100, "y": 200},
                    "end": {"x": 100, "y": 100},
                },
            )
```

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_drawing.py -v`
Expected: FAIL（`_insert_section_view` 尚未定義）

Step 3: 在 `src/tools/drawing.py` 實作

在檔案頂部新增 import：

```python
from tools.annotation import _get_drawing_views
```

在 `register_tools` 函式內新增 handler：

```python
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
```

在檔案底部新增 `_insert_section_view` 同步函式：

```python
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

    # 剖面線座標 mm → meters
    start_x = section_line["start"]["x"] * _MM_TO_M
    start_y = section_line["start"]["y"] * _MM_TO_M
    end_x = section_line["end"]["x"] * _MM_TO_M
    end_y = section_line["end"]["y"] * _MM_TO_M

    # COM 流程
    drawing.ActivateView(parent_view)
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
        "scale": f"{int(sv_scale[0])}:{int(sv_scale[1])}",
        "parent_view": parent_view,
    }
```

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_drawing.py -v`
Expected: PASS（3 tests）

Step 5: 跑全部測試確認沒有 regression

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS

Step 6: Commit

```bash
git add src/tools/drawing.py tests/test_drawing.py
git commit -m "feat: insert_section_view tool — 剖面圖建立 + 3 tests"
```
