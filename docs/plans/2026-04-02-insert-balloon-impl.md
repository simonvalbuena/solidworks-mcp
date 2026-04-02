# insert_balloon Implementation Plan

Goal: 在組立件工程圖視圖上插入氣球標註，包裝 AutoBalloon COM API。

Architecture: 在 `src/tools/annotation.py` 新增 `insert_balloon` tool handler + `_insert_balloon` COM 函式。用 `_get_drawing_views` 找視圖，`SelectByID` 選取視圖，`AutoBalloon()` 建立氣球（AutoBalloon5/3 帶參數版本在 pywin32 late-binding 下有 VARIANT_BOOL 類型不符問題，改用無參數版 + AutoBalloon2 fallback）。回傳值在 late-binding 下為 None，氣球已建立但無法從回傳值讀取零件資訊（#14）。

Tech Stack: pywin32 COM / FastMCP / pytest + mock

---

### Task 1: 常數與 COM 函式骨架

Files:
- Modify: `src/tools/annotation.py` (檔尾新增)
- Test: `tests/test_balloon.py` (新建)

Step 1: 在 `src/tools/annotation.py` 檔尾（`return dims` 之後）新增常數與 COM 函式：

```python
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


def _insert_balloon(
    view_name: str,
    component: str | None = None,
    style: str = "circular",
    auto_layout: bool = True,
) -> dict:
    """COM 操作：在視圖上插入氣球標註。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    # 驗證 style
    style_val = SW_BALLOON_STYLE.get(style)
    if style_val is None:
        raise SWError(
            f"不支援的 style: {style}，可用: {', '.join(SW_BALLOON_STYLE)}"
        )

    # 找視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    # 選取視圖
    drawing.ActivateView(view_name)
    ext = drawing.Extension
    ext.SelectByID2(
        view_name, "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0,
    )

    # AutoBalloon5
    layout = SW_BALLOON_LAYOUT_RIGHT if auto_layout else SW_BALLOON_LAYOUT_NONE
    notes = drawing.AutoBalloon5(
        layout,             # Layout
        False,              # ReverseDirection
        False,              # LeaderAttachmentToFaces
        style_val,          # Style
        SW_BALLOON_FIT_TIGHTEST,  # Size
        SW_BALLOON_TEXT_ITEM_NUM, # UpperTextContent
        "",                 # UpperTextCustomName
        0,                  # LowerTextContent (none)
        "",                 # LowerTextCustomName
        "",                 # LayerName
        True,               # IgnoreHiddenParts
        False,              # InsertMagneticLine
    )

    if notes is None:
        drawing.ClearSelection2(True)
        return {
            "status": "done",
            "balloon_count": 0,
            "balloons": [],
            "view_name": view_name,
        }

    # 遍歷 Note 取零件資訊
    balloons = []
    for note in notes:
        try:
            balloon_texts = note.GetBomBalloonTexts(True)  # upper
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

    # component 過濾
    if component is not None:
        keep = [b for b in balloons if b["component"] == component]
        remove_count = len(balloons) - len(keep)
        if remove_count > 0:
            logger.info(
                "component 過濾：保留 %d / %d 氣球", len(keep), len(balloons),
            )
            # 刪除不相關的氣球 Note
            for i, note in enumerate(notes):
                if i < len(balloons) and balloons[i]["component"] != component:
                    try:
                        note_text = note.GetText()
                        ext.SelectByID2(
                            note_text, "NOTE", 0, 0, 0, False, 0, None, 0,
                        )
                        drawing.Extension.DeleteSelection2(0)
                    except Exception as e:
                        logger.warning("刪除氣球失敗: %s", e)
        balloons = keep

    drawing.ClearSelection2(True)

    return {
        "status": "done",
        "balloon_count": len(balloons),
        "balloons": balloons,
        "view_name": view_name,
    }
```

Step 2: 在 `register_tools` 函式內（`auto_add_reference_dimensions` 之後）新增 tool handler：

```python
    @mcp.tool()
    async def insert_balloon(
        view_name: str,
        component: str | None = None,
        style: str = "circular",
        auto_layout: bool = True,
    ) -> str:
        """在組立件工程圖視圖上插入氣球標註。
        view_name: 目標視圖名稱。
        component: 指定零件名稱，不指定則標全部零件。
        style: 氣球樣式（circular/triangle/hexagon），預設 circular。
        auto_layout: 自動排列氣球位置，預設 true。"""
        try:
            result = await sw.execute(
                _insert_balloon,
                view_name, component, style, auto_layout,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_balloon 失敗: {e}")
```

Step 3: 建立 `tests/test_balloon.py`，先寫 import 和 helper：

```python
"""測試 insert_balloon（不需 SolidWorks）。"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

try:
    from tools.annotation import (
        _insert_balloon,
        SW_BALLOON_STYLE,
        SW_BALLOON_LAYOUT_RIGHT,
        SW_BALLOON_LAYOUT_NONE,
        SW_BALLOON_TEXT_ITEM_NUM,
        SW_BALLOON_FIT_TIGHTEST,
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


def _make_mock_drawing_with_view(view_name="工程視圖1"):
    """建立 mock drawing，含一個視圖（用 FeatureTree 結構）。"""
    drawing = _make_mock_com()

    # 建立視圖 sub-feature
    sub = _make_mock_com()
    sub.GetTypeName2 = "AbsoluteView"
    sub.Name = view_name
    view_obj = _make_mock_com()
    view_obj.GetOutline = [0.1, 0.1, 0.3, 0.3]
    sub.GetSpecificFeature2.return_value = view_obj
    type(sub).GetNextSubFeature = property(lambda self: None)

    # DrSheet feature
    sheet = _make_mock_com()
    sheet.GetTypeName2 = "DrSheet"
    sheet.Name = "圖頁1"
    type(sheet).GetFirstSubFeature = property(lambda self, s=sub: s)
    type(sheet).GetNextFeature = property(lambda self: None)
    type(drawing).FirstFeature = property(lambda self, sh=sheet: sh)

    # Extension mock
    ext = _make_mock_com()
    drawing.Extension = ext

    return drawing, ext


def _make_mock_notes(balloon_data):
    """建立 mock Note 陣列。

    balloon_data: [("comp_name", "item_no"), ...]
    """
    notes = []
    for comp_name, item_no in balloon_data:
        note = _make_mock_com()
        bt = _make_mock_com()
        bt.GetText.return_value = item_no
        comp = _make_mock_com()
        comp.Name2 = comp_name
        bt.GetComponent.return_value = comp
        note.GetBomBalloonTexts.return_value = [bt]
        note.GetText.return_value = f"balloon_{item_no}"
        notes.append(note)
    return notes
```

Step 4: 跑測試確認 import 正常（還沒有測試函式，只確認 import）
Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: no tests ran (0 collected), no import errors

Step 5: Commit
Message: "feat(balloon): 常數、COM 函式骨架、tool handler、測試 helper"

---

### Task 2: 測試 — AutoBalloon5 完整流程

Files:
- Modify: `tests/test_balloon.py`

Step 1: 新增測試：

```python
def test_insert_balloon_full_flow():
    """AutoBalloon5 完整 COM 流程：啟動視圖 → 選取 → AutoBalloon5 → 讀取結果。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([
        ("bracket-1", "1"),
        ("shaft-1", "2"),
        ("plate-1", "3"),
    ])
    drawing.AutoBalloon5.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["status"] == "done"
    assert result["balloon_count"] == 3
    assert result["view_name"] == "工程視圖1"
    assert len(result["balloons"]) == 3
    assert result["balloons"][0]["component"] == "bracket-1"
    assert result["balloons"][0]["item_number"] == "1"

    # 驗證 COM 呼叫
    drawing.ActivateView.assert_called_once_with("工程視圖1")
    ext.SelectByID2.assert_called_once_with(
        "工程視圖1", "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0,
    )
    drawing.AutoBalloon5.assert_called_once()

    # 驗證 AutoBalloon5 參數
    ab_args = drawing.AutoBalloon5.call_args[0]
    assert ab_args[0] == SW_BALLOON_LAYOUT_RIGHT  # layout
    assert ab_args[3] == SW_BALLOON_STYLE["circular"]  # style
    assert ab_args[5] == SW_BALLOON_TEXT_ITEM_NUM  # UpperTextContent
    assert ab_args[10] is True   # IgnoreHiddenParts
    assert ab_args[11] is False  # InsertMagneticLine
```

Step 2: 跑測試
Run: `.venv/Scripts/pytest tests/test_balloon.py::test_insert_balloon_full_flow -v`
Expected: PASS

Step 3: Commit
Message: "test(balloon): AutoBalloon5 完整 COM 流程測試"

---

### Task 3: 測試 — style 對映 + layout 對映

Files:
- Modify: `tests/test_balloon.py`

Step 1: 新增測試：

```python
def test_insert_balloon_triangle_style():
    """style='triangle' 正確對映到 swBS_Triangle (2)。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    drawing.AutoBalloon5.return_value = _make_mock_notes([("part-1", "1")])

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        _insert_balloon(view_name="工程視圖1", style="triangle")

    ab_args = drawing.AutoBalloon5.call_args[0]
    assert ab_args[3] == 2  # swBS_Triangle


def test_insert_balloon_no_auto_layout():
    """auto_layout=False 對映到 layout=0 (None)。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    drawing.AutoBalloon5.return_value = _make_mock_notes([("part-1", "1")])

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        _insert_balloon(view_name="工程視圖1", auto_layout=False)

    ab_args = drawing.AutoBalloon5.call_args[0]
    assert ab_args[0] == SW_BALLOON_LAYOUT_NONE  # 0
```

Step 2: 跑測試
Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: PASS (3 tests)

Step 3: Commit
Message: "test(balloon): style/layout 參數對映測試"

---

### Task 4: 測試 — component 過濾

Files:
- Modify: `tests/test_balloon.py`

Step 1: 新增測試：

```python
def test_insert_balloon_component_filter():
    """指定 component 時，只保留該零件的氣球。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([
        ("bracket-1", "1"),
        ("shaft-1", "2"),
        ("bracket-1", "3"),
        ("plate-1", "4"),
        ("bracket-1", "5"),
    ])
    drawing.AutoBalloon5.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(
            view_name="工程視圖1", component="bracket-1",
        )

    assert result["balloon_count"] == 3
    for b in result["balloons"]:
        assert b["component"] == "bracket-1"


def test_insert_balloon_component_filter_no_match():
    """component 過濾後全部不符合 → balloon_count: 0。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([
        ("bracket-1", "1"),
        ("shaft-1", "2"),
    ])
    drawing.AutoBalloon5.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(
            view_name="工程視圖1", component="not-exist",
        )

    assert result["balloon_count"] == 0
    assert result["balloons"] == []
```

Step 2: 跑測試
Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: PASS (5 tests)

Step 3: Commit
Message: "test(balloon): component 過濾測試"

---

### Task 5: 測試 — 錯誤處理

Files:
- Modify: `tests/test_balloon.py`

Step 1: 新增測試：

```python
def test_insert_balloon_view_not_found():
    """視圖不存在時 raise SWError。"""
    from errors import SWError
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        with pytest.raises(SWError, match="找不到視圖"):
            _insert_balloon(view_name="不存在的視圖")


def test_insert_balloon_invalid_style():
    """無效的 style 值 raise SWError。"""
    from errors import SWError
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        with pytest.raises(SWError, match="不支援的 style"):
            _insert_balloon(view_name="工程視圖1", style="star")


def test_insert_balloon_no_components():
    """AutoBalloon5 回傳 None（視圖無元件）→ 空結果。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    drawing.AutoBalloon5.return_value = None

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["balloon_count"] == 0
    assert result["balloons"] == []
```

Step 2: 跑測試
Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: PASS (8 tests)

Step 3: Commit
Message: "test(balloon): 錯誤處理測試（view not found / invalid style / no components）"

---

### Task 6: 全部測試 + 最終 commit

Files:
- None (驗證)

Step 1: 跑全部測試確認無回歸
Run: `.venv/Scripts/pytest tests/ -v`
Expected: ALL PASS

Step 2: Commit
Message: "feat: insert_balloon — 氣球標註建立"
