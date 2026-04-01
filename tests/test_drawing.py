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
    """mock 完整 COM 流程：ActivateView -> CreateLine -> CreateSectionViewAt5。"""
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

    # 驗證 CreateLine 座標（mm -> meters）
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
