"""測試 annotation helper（不需 SolidWorks）。"""

import pytest
from unittest.mock import MagicMock

try:
    from tools.annotation import _get_drawing_views, _classify_edges, _find_bounding_edges
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(
    not HAS_DEPS, reason="需要 mcp/pywin32 依賴（僅在 SW 主機上可用）"
)


def _make_mock_com(attrs=None, **kwargs):
    """建立模擬 COM 物件的 mock（有 _oleobj_ 屬性）。"""
    m = MagicMock(**kwargs)
    m._oleobj_ = True  # 模擬 pywin32 CDispatch 的 _oleobj_
    if attrs:
        for k, v in attrs.items():
            setattr(m, k, v)
    return m


def _make_mock_drawing(view_names):
    """建立 mock drawing，模擬 FeatureTree 結構。

    模擬 pywin32 COM 的存取規則：
    - FirstFeature, GetNextFeature, GetTypeName2, Name → 屬性存取
    - GetFirstSubFeature, GetNextSubFeature, GetSpecificFeature2 → 先屬性再方法
    - COM 物件有 _oleobj_ 屬性
    """
    drawing = _make_mock_com()

    # 建立 sub features（視圖）
    subs = []
    for i, name in enumerate(view_names):
        sub = _make_mock_com()
        sub.GetTypeName2 = "AbsoluteView" if i == 0 else "UnfoldedView"
        sub.Name = name
        view_obj = _make_mock_com()
        sub.GetSpecificFeature2.return_value = view_obj
        subs.append((sub, view_obj))

    # 串連 sub features — 用 property 模擬屬性存取
    # _get_drawing_views 先試 property（檢查 _oleobj_），再試 method
    for i, (sub, _) in enumerate(subs):
        next_sub = subs[i + 1][0] if i + 1 < len(subs) else None
        # 讓 property 存取回傳下一個 sub（模擬 COM 屬性行為）
        sub_type = type(sub)
        # 用閉包避免 late binding 問題
        sub_type.GetNextSubFeature = property(lambda self, ns=next_sub: ns)

    # 建立 sheet feature
    sheet = _make_mock_com()
    sheet.GetTypeName2 = "DrSheet"
    sheet.Name = "圖頁1"
    first_sub = subs[0][0] if subs else None
    type(sheet).GetFirstSubFeature = property(lambda self: first_sub)

    # 建立其他 features
    folder = _make_mock_com()
    folder.GetTypeName2 = "CommentsFolder"
    folder.Name = "備註"
    type(folder).GetNextFeature = property(lambda self: sheet)

    type(drawing).FirstFeature = property(lambda self: folder)
    type(sheet).GetNextFeature = property(lambda self: None)

    return drawing, {name: vo for (_, vo), name in zip(subs, view_names)}


def test_get_drawing_views_finds_all():
    from tools.annotation import _get_drawing_views
    drawing, expected_views = _make_mock_drawing(["工程視圖1", "工程視圖2", "工程視圖3"])
    result = _get_drawing_views(drawing)
    assert len(result) == 3
    assert [name for name, _ in result] == ["工程視圖1", "工程視圖2", "工程視圖3"]


def test_get_drawing_views_empty():
    from tools.annotation import _get_drawing_views
    drawing = MagicMock()
    type(drawing).FirstFeature = property(lambda self: None)
    result = _get_drawing_views(drawing)
    assert result == []


def test_get_drawing_views_filter_by_name():
    from tools.annotation import _get_drawing_views
    drawing, _ = _make_mock_drawing(["工程視圖1", "工程視圖2"])
    result = _get_drawing_views(drawing, view_name="工程視圖2")
    assert len(result) == 1
    assert result[0][0] == "工程視圖2"


def _make_mock_line_edge(start, end):
    """建立模擬直線邊線。"""
    edge = MagicMock()
    curve = MagicMock()
    type(edge).GetCurve = property(lambda self: curve)
    type(curve).IsLine = property(lambda self: True)
    type(curve).IsCircle = property(lambda self: False)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dz = end[2] - start[2]
    type(curve).LineParams = property(lambda self: (*start, dx, dy, dz))
    sv = MagicMock()
    type(sv).GetPoint = property(lambda self: start)
    ev = MagicMock()
    type(ev).GetPoint = property(lambda self: end)
    type(edge).GetStartVertex = property(lambda self: sv)
    type(edge).GetEndVertex = property(lambda self: ev)
    return edge


def _make_mock_circle_edge(center, full=True):
    """建立模擬圓弧/完整圓邊線。"""
    edge = MagicMock()
    curve = MagicMock()
    type(edge).GetCurve = property(lambda self: curve)
    type(curve).IsLine = property(lambda self: False)
    type(curve).IsCircle = property(lambda self: True)
    type(curve).CircleParams = property(lambda self: (*center, 0, 0, 1, 0.005))
    if full:
        type(edge).GetStartVertex = property(lambda self: None)
        type(edge).GetEndVertex = property(lambda self: None)
    else:
        sv = MagicMock()
        type(sv).GetPoint = property(lambda self: (0.01, 0, 0))
        ev = MagicMock()
        type(ev).GetPoint = property(lambda self: (-0.01, 0, 0))
        type(edge).GetStartVertex = property(lambda self: sv)
        type(edge).GetEndVertex = property(lambda self: ev)
    return edge


def test_classify_horizontal_line():
    from tools.annotation import _classify_edges
    edge = _make_mock_line_edge((0, 0, 0), (0.1, 0, 0))
    result = _classify_edges([edge])
    assert len(result["horizontal"]) == 1
    assert result["vertical"] == []


def test_classify_vertical_line():
    from tools.annotation import _classify_edges
    edge = _make_mock_line_edge((0, 0, 0), (0, 0.1, 0))
    result = _classify_edges([edge])
    assert len(result["vertical"]) == 1
    assert result["horizontal"] == []


def test_classify_full_circle():
    from tools.annotation import _classify_edges
    edge = _make_mock_circle_edge((0, 0, 0), full=True)
    result = _classify_edges([edge])
    assert len(result["circles"]) == 1


def test_classify_arc_skipped():
    from tools.annotation import _classify_edges
    edge = _make_mock_circle_edge((0, 0, 0), full=False)
    result = _classify_edges([edge])
    assert result["circles"] == []
    assert result["other"] == 1


def test_classify_mixed():
    from tools.annotation import _classify_edges
    edges = [
        _make_mock_line_edge((0, 0, 0), (0.1, 0, 0)),      # horizontal
        _make_mock_line_edge((0, 0, 0), (0, 0.1, 0)),      # vertical
        _make_mock_line_edge((0, 0, 0), (0.1, 0.1, 0)),    # diagonal → other
        _make_mock_circle_edge((0, 0, 0), full=True),       # circle
    ]
    result = _classify_edges(edges)
    assert len(result["horizontal"]) == 1
    assert len(result["vertical"]) == 1
    assert len(result["circles"]) == 1
    assert result["other"] == 1


def test_find_bounding_edges_horizontal():
    from tools.annotation import _find_bounding_edges
    edges = {
        "horizontal": [
            ("e1", (0, 0.01, 0), (0.1, 0.01, 0)),
            ("e2", (0, 0.05, 0), (0.1, 0.05, 0)),
            ("e3", (0, -0.02, 0), (0.1, -0.02, 0)),
        ],
        "vertical": [],
    }
    result = _find_bounding_edges(edges)
    assert result["h_top"][0] == "e2"
    assert result["h_bottom"][0] == "e3"


def test_find_bounding_edges_vertical():
    from tools.annotation import _find_bounding_edges
    edges = {
        "horizontal": [],
        "vertical": [
            ("e1", (0.01, 0, 0), (0.01, 0.1, 0)),
            ("e2", (-0.03, 0, 0), (-0.03, 0.1, 0)),
            ("e3", (0.05, 0, 0), (0.05, 0.1, 0)),
        ],
    }
    result = _find_bounding_edges(edges)
    assert result["v_right"][0] == "e3"
    assert result["v_left"][0] == "e2"


def test_find_bounding_edges_insufficient():
    from tools.annotation import _find_bounding_edges
    edges = {
        "horizontal": [("e1", (0, 0, 0), (0.1, 0, 0))],
        "vertical": [],
    }
    result = _find_bounding_edges(edges)
    assert result["h_top"] is None or result["h_top"] == result["h_bottom"]
