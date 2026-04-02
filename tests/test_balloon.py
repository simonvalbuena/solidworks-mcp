"""測試 insert_balloon（不需 SolidWorks）。"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock

try:
    from tools.annotation import (
        _insert_balloon,
        _extract_notes,
        _read_balloon_info,
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


# === _extract_notes helper tests ===


def test_extract_notes_from_list():
    """從 list 提取 Note 物件。"""
    notes = _make_mock_notes([("a", "1"), ("b", "2")])
    result = _extract_notes(notes)
    assert len(result) == 2


def test_extract_notes_none():
    """None 回傳空 list。"""
    assert _extract_notes(None) == []


# === _read_balloon_info helper tests ===


def test_read_balloon_info():
    """從 Note 物件讀取零件名稱和 item number。"""
    notes = _make_mock_notes([("bracket-1", "1"), ("shaft-1", "2")])
    result = _read_balloon_info(notes)
    assert len(result) == 2
    assert result[0]["component"] == "bracket-1"
    assert result[0]["item_number"] == "1"
    assert result[1]["component"] == "shaft-1"
    assert result[1]["item_number"] == "2"


# === insert_balloon 完整流程 ===


def test_insert_balloon_full_flow():
    """完整 COM 流程：啟動視圖 → 選取 → AutoBalloon → 讀取結果。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([
        ("bracket-1", "1"),
        ("shaft-1", "2"),
        ("plate-1", "3"),
    ])
    # AutoBalloon() 回傳 notes（無參數版本）
    drawing.AutoBalloon.return_value = notes

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
    drawing.SelectByID.assert_called_once_with(
        "工程視圖1", "DRAWINGVIEW", 0, 0, 0,
    )
    drawing.AutoBalloon.assert_called_once()


def test_insert_balloon_fallback_to_autoballoon2():
    """AutoBalloon() 失敗時 fallback 到 AutoBalloon2。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([("part-1", "1")])
    drawing.AutoBalloon.side_effect = Exception("not optional")
    drawing.AutoBalloon2.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["balloon_count"] == 1
    drawing.AutoBalloon2.assert_called_once()
    # AutoBalloon2 第一個參數是 layout
    ab_args = drawing.AutoBalloon2.call_args[0]
    assert ab_args[0] == SW_BALLOON_LAYOUT_RIGHT


def test_insert_balloon_autoballoon_returns_none():
    """AutoBalloon 回傳 None（late-binding VARIANT 問題）→ 空結果但不報錯。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    drawing.AutoBalloon.return_value = None

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["status"] == "done"
    assert result["balloon_count"] == 0
    assert result["balloons"] == []


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
    drawing.AutoBalloon.return_value = notes

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
    drawing.AutoBalloon.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(
            view_name="工程視圖1", component="not-exist",
        )

    assert result["balloon_count"] == 0
    assert result["balloons"] == []


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
    """AutoBalloon 回傳 None（視圖無元件）→ 空結果。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    drawing.AutoBalloon.return_value = None

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["balloon_count"] == 0
    assert result["balloons"] == []
