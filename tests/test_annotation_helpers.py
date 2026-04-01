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


# --- circle dedup tests ---

def test_dedupe_circles_same_radius_picks_farthest():
    """同半徑 3 個圓，選離重心最遠的。"""
    from tools.annotation import _dedupe_circles
    # 3 個半徑 0.005m 的圓，圓心分別在 (0,0), (0.02,0), (0.05,0)
    # 重心 x = (0 + 0.02 + 0.05) / 3 ≈ 0.0233
    # 離重心最遠的是 (0.05, 0)
    circles = [
        ("edge_a", (0.0, 0.0, 0.0), 0.005),
        ("edge_b", (0.02, 0.0, 0.0), 0.005),
        ("edge_c", (0.05, 0.0, 0.0), 0.005),
    ]
    result = _dedupe_circles(circles)
    assert len(result) == 1
    assert result[0][0] == "edge_c"


def test_dedupe_circles_different_radii_keeps_all():
    """不同半徑各保留一個。"""
    from tools.annotation import _dedupe_circles
    circles = [
        ("edge_a", (0.0, 0.0, 0.0), 0.005),
        ("edge_b", (0.01, 0.0, 0.0), 0.010),
        ("edge_c", (0.02, 0.0, 0.0), 0.003),
    ]
    result = _dedupe_circles(circles)
    assert len(result) == 3


def test_dedupe_circles_tolerance():
    """半徑差 < 0.1mm (1e-4m) 視為同組。"""
    from tools.annotation import _dedupe_circles
    # 3 個圓：重心 x = (0 + 0.01 + 0.05) / 3 ≈ 0.02
    # edge_c 離重心最遠 (|0.05 - 0.02| = 0.03)
    circles = [
        ("edge_a", (0.0, 0.0, 0.0), 0.00500),
        ("edge_b", (0.01, 0.0, 0.0), 0.00509),  # 差 0.09mm < 0.1mm
        ("edge_c", (0.05, 0.0, 0.0), 0.00504),  # 差 0.04mm < 0.1mm
    ]
    result = _dedupe_circles(circles)
    assert len(result) == 1
    assert result[0][0] == "edge_c"  # 離重心最遠


def test_dedupe_circles_empty():
    """空列表回傳空。"""
    from tools.annotation import _dedupe_circles
    assert _dedupe_circles([]) == []


def test_dedupe_circles_single():
    """只有一個圓，直接回傳。"""
    from tools.annotation import _dedupe_circles
    circles = [("edge_a", (0.0, 0.0, 0.0), 0.005)]
    result = _dedupe_circles(circles)
    assert len(result) == 1


# --- circle classify → dedupe integration test ---

def _make_mock_circle_edge_r(center, radius, full=True):
    """建立模擬完整圓邊線（可自訂半徑）。"""
    edge = MagicMock()
    curve = MagicMock()
    type(edge).GetCurve = property(lambda self: curve)
    type(curve).IsLine = property(lambda self: False)
    type(curve).IsCircle = property(lambda self: True)
    type(curve).CircleParams = property(
        lambda self, c=center, r=radius: (*c, 0, 0, 1, r),
    )
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


def test_dedupe_circles_mixed_with_classify():
    """classify → dedupe 端到端：3 個同半徑圓 + 1 個不同半徑 → 2 個代表。"""
    from tools.annotation import _classify_edges, _dedupe_circles
    edges = [
        _make_mock_circle_edge_r((0.0, 0.0, 0.0), 0.005),     # r=5mm, group A
        _make_mock_circle_edge_r((0.02, 0.0, 0.0), 0.005),    # r=5mm, group A
        _make_mock_circle_edge_r((0.04, 0.0, 0.0), 0.005),    # r=5mm, group A
        _make_mock_circle_edge_r((0.01, 0.01, 0.0), 0.010),   # r=10mm, group B
    ]
    classified = _classify_edges(edges)
    assert len(classified["circles"]) == 4

    deduped = _dedupe_circles(classified["circles"])
    assert len(deduped) == 2

    radii = sorted([r for _, _, r in deduped])
    assert abs(radii[0] - 0.005) < 1e-6
    assert abs(radii[1] - 0.010) < 1e-6


# --- edge info builder tests ---

def test_build_edge_info_line():
    """直線邊線：回傳 type=line + start/end/midpoint/length。"""
    from tools.annotation import _build_edge_info
    # start=(0,0,0) end=(0.1,0,0) → 100mm 水平線
    edge = _make_mock_line_edge((0, 0, 0), (0.1, 0, 0))
    info = _build_edge_info(edge, 0)
    assert info["index"] == 0
    assert info["type"] == "line"
    assert abs(info["start"]["x"] - 0.0) < 0.01
    assert abs(info["end"]["x"] - 100.0) < 0.01
    assert abs(info["midpoint"]["x"] - 50.0) < 0.01
    assert abs(info["midpoint"]["y"] - 0.0) < 0.01
    assert abs(info["length"] - 100.0) < 0.01


def test_build_edge_info_circle():
    """完整圓邊線：回傳 type=circle + midpoint(=圓心) + radius_mm。"""
    from tools.annotation import _build_edge_info
    # center=(0.01, 0.02, 0), radius=0.005 → 5mm
    edge = _make_mock_circle_edge_r((0.01, 0.02, 0), 0.005, full=True)
    info = _build_edge_info(edge, 3)
    assert info["index"] == 3
    assert info["type"] == "circle"
    assert abs(info["midpoint"]["x"] - 10.0) < 0.01
    assert abs(info["midpoint"]["y"] - 20.0) < 0.01
    assert abs(info["radius_mm"] - 5.0) < 0.01


# --- edge matching tests ---

def _make_edges_info():
    """建立測試用 edges_info 清單（3 條邊線）。"""
    return [
        {"index": 0, "type": "line", "midpoint": {"x": 10.0, "y": 20.0}},
        {"index": 1, "type": "line", "midpoint": {"x": 50.0, "y": 20.0}},
        {"index": 2, "type": "line", "midpoint": {"x": 30.0, "y": 60.0}},
    ]


def test_match_by_index_hit():
    """索引 + 座標吻合 → 回傳 (index, 'index')。"""
    from tools.annotation import _match_edge_by_index
    edges_info = _make_edges_info()
    idx, method = _match_edge_by_index(edges_info, 0, 10.0, 20.0)
    assert idx == 0
    assert method == "index"


def test_match_by_index_coords_drift():
    """索引存在但座標偏移超過容差 → fallback。"""
    from tools.annotation import _match_edge_by_index
    edges_info = _make_edges_info()
    idx, method = _match_edge_by_index(edges_info, 0, 15.0, 20.0, tolerance=0.5)
    assert idx is None
    assert method == "fallback"


def test_match_by_index_out_of_range():
    """索引超出範圍 → fallback。"""
    from tools.annotation import _match_edge_by_index
    edges_info = _make_edges_info()
    idx, method = _match_edge_by_index(edges_info, 99, 10.0, 20.0)
    assert idx is None
    assert method == "fallback"


def test_match_by_proximity_normal():
    """近鄰匹配成功。"""
    from tools.annotation import _match_edge_by_proximity
    edges_info = _make_edges_info()
    idx, dist = _match_edge_by_proximity(edges_info, 10.5, 20.0)
    assert idx == 0
    assert dist < 1.0


def test_match_by_proximity_picks_nearest():
    """多條邊線取最近的。"""
    from tools.annotation import _match_edge_by_proximity
    edges_info = _make_edges_info()
    idx, dist = _match_edge_by_proximity(edges_info, 48.0, 20.0)
    assert idx == 1  # (50, 20) 最近


def test_match_by_proximity_too_far():
    """全部超過 max_distance → 拋 SWError。"""
    from tools.annotation import _match_edge_by_proximity
    from errors import SWError
    edges_info = _make_edges_info()
    with pytest.raises(SWError, match="找不到"):
        _match_edge_by_proximity(edges_info, 999.0, 999.0, max_distance=2.0)


def test_resolve_edge_index_success():
    """index 吻合 → 直接回傳。"""
    from tools.annotation import _resolve_edge
    edges_info = _make_edges_info()
    idx, method = _resolve_edge(edges_info, {"index": 1, "x": 50.0, "y": 20.0})
    assert idx == 1
    assert method == "index"


def test_resolve_edge_fallback_proximity():
    """index 不吻合 → fallback proximity 成功。"""
    from tools.annotation import _resolve_edge
    edges_info = _make_edges_info()
    # index=0 的 midpoint 是 (10,20)，但傳入 (50,20) → index 不吻合
    # fallback 會找到 index=1 (50,20)
    idx, method = _resolve_edge(edges_info, {"index": 0, "x": 50.0, "y": 20.0})
    assert idx == 1
    assert method == "proximity"


def test_resolve_edge_both_fail():
    """index + proximity 都失敗 → 拋 SWError。"""
    from tools.annotation import _resolve_edge
    from errors import SWError
    edges_info = _make_edges_info()
    with pytest.raises(SWError):
        _resolve_edge(edges_info, {"index": 99, "x": 999.0, "y": 999.0})


# --- probe structure test ---

def test_probe_output_structure():
    """驗證 _build_edges_info 產出的結構符合 probe 規格。"""
    from tools.annotation import _build_edges_info
    edges = [
        _make_mock_line_edge((0, 0, 0), (0.1, 0, 0)),          # horizontal
        _make_mock_line_edge((0, 0, 0), (0, 0.05, 0)),         # vertical
        _make_mock_circle_edge_r((0.02, 0.03, 0), 0.005),      # circle
    ]
    infos = _build_edges_info(edges)
    assert len(infos) == 3

    # 檢查必要欄位
    for info in infos:
        assert "index" in info
        assert "type" in info
        assert info["type"] in ("line", "circle", "arc", "other")

    # line 有 start/end/midpoint/length
    line_info = infos[0]
    assert line_info["type"] == "line"
    assert "start" in line_info
    assert "end" in line_info
    assert "midpoint" in line_info
    assert "length" in line_info
    assert "x" in line_info["midpoint"]
    assert "y" in line_info["midpoint"]

    # circle 有 midpoint/radius_mm
    circle_info = infos[2]
    assert circle_info["type"] == "circle"
    assert "midpoint" in circle_info
    assert "radius_mm" in circle_info


# --- add_dimension integration tests ---

def _make_mock_edges_for_dim():
    """建立兩條水平邊線的 mock（上下各一條）。"""
    # 下方邊線: y=0, x: 0→100mm
    edge_bottom = _make_mock_line_edge((0, 0, 0), (0.1, 0, 0))
    # 上方邊線: y=50mm, x: 0→100mm
    edge_top = _make_mock_line_edge((0, 0.05, 0), (0.1, 0.05, 0))
    return [edge_bottom, edge_top]


def test_add_dim_resolve_edges():
    """probe edges_info → resolve 兩條邊線成功。"""
    from tools.annotation import _build_edges_info, _resolve_edge
    edges = _make_mock_edges_for_dim()
    edges_info = _build_edges_info(edges)

    # edge1: index=0, midpoint=(50, 0)
    idx1, m1 = _resolve_edge(edges_info, {"index": 0, "x": 50.0, "y": 0.0})
    assert idx1 == 0
    assert m1 == "index"

    # edge2: index=1, midpoint=(50, 50)
    idx2, m2 = _resolve_edge(edges_info, {"index": 1, "x": 50.0, "y": 50.0})
    assert idx2 == 1
    assert m2 == "index"

    assert idx1 != idx2


def test_add_dim_auto_text_position():
    """未指定 text_position 時自動計算。"""
    from tools.annotation import _build_edges_info, _calc_text_position
    edges = _make_mock_edges_for_dim()
    edges_info = _build_edges_info(edges)

    pos = _calc_text_position(edges_info, 0, 1)
    # 兩邊中點: ((50+50)/2, (0+50)/2) = (50, 25)，再偏移
    assert "x" in pos
    assert "y" in pos


def test_add_dim_same_edge_error():
    """兩條邊線相同 → 拋錯。"""
    from tools.annotation import _build_edges_info, _resolve_edge
    from errors import SWError
    edges = _make_mock_edges_for_dim()
    edges_info = _build_edges_info(edges)

    idx1, _ = _resolve_edge(edges_info, {"index": 0, "x": 50.0, "y": 0.0})
    idx2, _ = _resolve_edge(edges_info, {"index": 0, "x": 50.0, "y": 0.0})
    assert idx1 == idx2  # 驗證會選到同一條


# --- diameter tests ---

def test_add_dim_diameter_resolve_edge():
    """diameter 正確 resolve circle edge。"""
    from tools.annotation import _build_edges_info, _resolve_edge
    edges = [
        _make_mock_line_edge((0, 0, 0), (0.1, 0, 0)),
        _make_mock_circle_edge_r((0.05, 0.03, 0), 0.005, full=True),
    ]
    edges_info = _build_edges_info(edges)
    idx, method = _resolve_edge(edges_info, {"index": 1, "x": 50.0, "y": 30.0})
    assert idx == 1
    assert edges_info[idx]["type"] == "circle"


def test_add_dim_diameter_rejects_non_circle():
    """edge type 不是 circle 時報 SWError。"""
    from tools.annotation import _check_edge_type
    from errors import SWError
    edges_info = [{"index": 0, "type": "line", "midpoint": {"x": 50.0, "y": 0.0}}]
    with pytest.raises(SWError, match="circle"):
        _check_edge_type(edges_info, 0, "circle", "diameter")


def test_add_dim_diameter_auto_text_position():
    """不傳 text_position 時自動計算：x 在視圖右側偏移，y 在圓心。"""
    from tools.annotation import _calc_radial_text_pos
    edge_info = {"index": 1, "type": "circle", "midpoint": {"x": 50.0, "y": 30.0}, "radius_mm": 5.0}
    outline_m = [0.0, 0.0, 0.1, 0.06]  # xMax=0.1m=100mm
    pos = _calc_radial_text_pos(edge_info, outline_m)
    assert "x" in pos
    assert "y" in pos
    assert pos["x"] > 100.0  # 右側偏移
    assert abs(pos["y"] - 30.0) < 0.01  # 圓心 y


def test_add_dim_diameter_com_flow():
    """diameter COM 流程：SelectEntity 一次、AddDimension 正確、回傳 value_mm。"""
    from tools.annotation import _add_radial_dimension
    from unittest.mock import patch

    # 建立 circle edge mock
    circle_edge = _make_mock_circle_edge_r((0.05, 0.03, 0), 0.005, full=True)

    # Mock view
    mock_view = _make_mock_com()
    type(mock_view).GetVisibleComponents = property(lambda self: ("comp1",))
    mock_view.GetVisibleEntities2.return_value = [circle_edge]
    mock_view.SelectEntity.return_value = True
    type(mock_view).GetOutline = property(lambda self: [0.0, 0.0, 0.12, 0.08])

    # Mock disp_dim + dimension value
    mock_dim_value = MagicMock()
    mock_dim_value.Value = 10.0  # 10mm diameter (SetUnits2 後回傳 mm)
    mock_disp_dim = MagicMock()
    mock_disp_dim.GetDimension2.return_value = mock_dim_value

    mock_ext = MagicMock()
    mock_ext.AddDimension.return_value = mock_disp_dim

    mock_drawing = _make_mock_com()
    mock_drawing.Extension = mock_ext
    type(mock_drawing).GetType = property(lambda self: 3)

    mock_app = MagicMock()
    mock_app.ActiveDoc = mock_drawing

    mock_sw = MagicMock()
    mock_sw.get_app.return_value = mock_app

    with patch("tools.annotation.SWConnection.get_instance", return_value=mock_sw), \
         patch("tools.annotation._get_drawing_views", return_value=[("工程視圖1", mock_view)]), \
         patch("tools.annotation._get_view_edges", return_value=[circle_edge]):

        result = _add_radial_dimension(
            "工程視圖1",
            {"index": 0, "x": 50.0, "y": 30.0},
            None,
            "diameter",
        )

    assert result["status"] == "done"
    assert result["dimension_type"] == "diameter"
    assert result["value_mm"] == 10.0
    assert result["match_method_edge1"] in ("index", "proximity")
    # SelectEntity 只呼叫一次，不帶 append
    mock_view.SelectEntity.assert_called_once_with(circle_edge, False)


# --- radius tests ---

def test_add_dim_radius_resolve_arc_edge():
    """radius 正確 resolve arc edge。"""
    from tools.annotation import _build_edges_info, _resolve_edge
    edges = [
        _make_mock_line_edge((0, 0, 0), (0.1, 0, 0)),
        _make_mock_circle_edge_r((0.05, 0.03, 0), 0.005, full=False),
    ]
    edges_info = _build_edges_info(edges)
    idx, method = _resolve_edge(edges_info, {"index": 1, "x": 50.0, "y": 30.0})
    assert idx == 1
    assert edges_info[idx]["type"] == "arc"


def test_add_dim_radius_rejects_non_arc():
    """edge type 不是 arc 時報 SWError。"""
    from tools.annotation import _check_edge_type
    from errors import SWError
    edges_info = [{"index": 0, "type": "line", "midpoint": {"x": 50.0, "y": 0.0}}]
    with pytest.raises(SWError, match="arc"):
        _check_edge_type(edges_info, 0, "arc", "radius")


def test_add_dim_radius_com_flow():
    """radius COM 流程：SelectEntity 一次、AddDimension 正確、回傳 value_mm。"""
    from tools.annotation import _add_radial_dimension
    from unittest.mock import patch

    # 建立 arc edge mock（full=False → arc）
    arc_edge = _make_mock_circle_edge_r((0.05, 0.03, 0), 0.005, full=False)

    # Mock view
    mock_view = _make_mock_com()
    type(mock_view).GetVisibleComponents = property(lambda self: ("comp1",))
    mock_view.GetVisibleEntities2.return_value = [arc_edge]
    mock_view.SelectEntity.return_value = True
    type(mock_view).GetOutline = property(lambda self: [0.0, 0.0, 0.12, 0.08])

    # Mock disp_dim + dimension value
    mock_dim_value = MagicMock()
    mock_dim_value.Value = 5.0  # 5mm radius (SetUnits2 後回傳 mm)
    mock_disp_dim = MagicMock()
    mock_disp_dim.GetDimension2.return_value = mock_dim_value

    mock_ext = MagicMock()
    mock_ext.AddDimension.return_value = mock_disp_dim

    mock_drawing = _make_mock_com()
    mock_drawing.Extension = mock_ext
    type(mock_drawing).GetType = property(lambda self: 3)

    mock_app = MagicMock()
    mock_app.ActiveDoc = mock_drawing

    mock_sw = MagicMock()
    mock_sw.get_app.return_value = mock_app

    with patch("tools.annotation.SWConnection.get_instance", return_value=mock_sw), \
         patch("tools.annotation._get_drawing_views", return_value=[("工程視圖1", mock_view)]), \
         patch("tools.annotation._get_view_edges", return_value=[arc_edge]):

        result = _add_radial_dimension(
            "工程視圖1",
            {"index": 0, "x": 50.0, "y": 30.0},
            None,
            "radius",
        )

    assert result["status"] == "done"
    assert result["dimension_type"] == "radius"
    assert result["value_mm"] == 5.0
    assert result["match_method_edge1"] in ("index", "proximity")
    mock_view.SelectEntity.assert_called_once_with(arc_edge, False)


# --- linear return structure tests ---

def test_add_dim_linear_missing_edge2():
    """dimension_type=linear 但 edge2=None → ToolError。"""
    from errors import ToolError
    # handler 層驗證：edge2 is None → raise ToolError
    with pytest.raises(ToolError, match="edge2"):
        raise ToolError("linear 尺寸需要 edge2")


def test_add_dim_linear_returns_value_mm():
    """linear 回傳結構包含 dimension_type + value_mm。"""
    from tools.annotation import _add_linear_dimension
    from unittest.mock import patch

    edge_bottom = _make_mock_line_edge((0, 0, 0), (0.1, 0, 0))
    edge_top = _make_mock_line_edge((0, 0.05, 0), (0.1, 0.05, 0))

    mock_view = _make_mock_com()
    type(mock_view).GetVisibleComponents = property(lambda self: ("comp1",))
    mock_view.GetVisibleEntities2.return_value = [edge_bottom, edge_top]
    mock_view.SelectEntity.return_value = True

    mock_dim_value = MagicMock()
    mock_dim_value.Value = 50.0  # 50mm (SetUnits2 後回傳 mm)
    mock_disp_dim = MagicMock()
    mock_disp_dim.GetDimension2.return_value = mock_dim_value

    mock_ext = MagicMock()
    mock_ext.AddDimension.return_value = mock_disp_dim

    mock_drawing = _make_mock_com()
    mock_drawing.Extension = mock_ext
    type(mock_drawing).GetType = property(lambda self: 3)

    mock_app = MagicMock()
    mock_app.ActiveDoc = mock_drawing

    mock_sw = MagicMock()
    mock_sw.get_app.return_value = mock_app

    with patch("tools.annotation.SWConnection.get_instance", return_value=mock_sw), \
         patch("tools.annotation._get_drawing_views", return_value=[("工程視圖1", mock_view)]), \
         patch("tools.annotation._get_view_edges", return_value=[edge_bottom, edge_top]):

        result = _add_linear_dimension(
            "工程視圖1",
            {"index": 0, "x": 50.0, "y": 0.0},
            {"index": 1, "x": 50.0, "y": 50.0},
            None,
        )

    assert result["status"] == "done"
    assert result["dimension_type"] == "linear"
    assert result["value_mm"] == 50.0


# --- angle dimension tests ---


def test_add_dim_angle_resolve_line_edges():
    """angle 正確 resolve 兩條 line edge。"""
    from tools.annotation import _build_edges_info, _resolve_edge, _check_edge_type
    edges = [
        _make_mock_line_edge((0, 0, 0), (0.1, 0, 0)),        # horizontal
        _make_mock_line_edge((0, 0, 0), (0, 0.05, 0)),       # vertical
        _make_mock_circle_edge_r((0.05, 0.03, 0), 0.005),    # circle
    ]
    edges_info = _build_edges_info(edges)
    idx1, _ = _resolve_edge(edges_info, {"index": 0, "x": 50.0, "y": 0.0})
    idx2, _ = _resolve_edge(edges_info, {"index": 1, "x": 0.0, "y": 25.0})
    assert edges_info[idx1]["type"] == "line"
    assert edges_info[idx2]["type"] == "line"
    # type check 應該通過（不拋例外）
    _check_edge_type(edges_info, idx1, "line", "angle")
    _check_edge_type(edges_info, idx2, "line", "angle")


def test_add_dim_angle_rejects_non_line():
    """edge type 不是 line 時報 SWError。"""
    from tools.annotation import _check_edge_type
    from errors import SWError
    edges_info = [{"index": 0, "type": "circle", "midpoint": {"x": 50.0, "y": 30.0}}]
    with pytest.raises(SWError, match="line"):
        _check_edge_type(edges_info, 0, "line", "angle")


def test_add_dim_angle_com_flow():
    """angle COM 流程：SelectEntity 兩次、回傳 dimension_type='angle' + value_deg。"""
    from tools.annotation import _add_angle_dimension
    from unittest.mock import patch, call

    edge_h = _make_mock_line_edge((0, 0, 0), (0.1, 0, 0))       # horizontal
    edge_v = _make_mock_line_edge((0, 0, 0), (0, 0.05, 0))      # vertical

    mock_view = _make_mock_com()
    type(mock_view).GetVisibleComponents = property(lambda self: ("comp1",))
    mock_view.GetVisibleEntities2.return_value = [edge_h, edge_v]
    mock_view.SelectEntity.return_value = True

    mock_dim_value = MagicMock()
    mock_dim_value.Value = 90.0  # 90 degrees
    mock_disp_dim = MagicMock()
    mock_disp_dim.GetDimension2.return_value = mock_dim_value

    mock_ext = MagicMock()
    mock_ext.AddDimension.return_value = mock_disp_dim

    mock_drawing = _make_mock_com()
    mock_drawing.Extension = mock_ext
    type(mock_drawing).GetType = property(lambda self: 3)

    mock_app = MagicMock()
    mock_app.ActiveDoc = mock_drawing

    mock_sw = MagicMock()
    mock_sw.get_app.return_value = mock_app

    with patch("tools.annotation.SWConnection.get_instance", return_value=mock_sw), \
         patch("tools.annotation._get_drawing_views", return_value=[("工程視圖1", mock_view)]), \
         patch("tools.annotation._get_view_edges", return_value=[edge_h, edge_v]):

        result = _add_angle_dimension(
            "工程視圖1",
            {"index": 0, "x": 50.0, "y": 0.0},
            {"index": 1, "x": 0.0, "y": 25.0},
            None,
        )

    assert result["status"] == "done"
    assert result["dimension_type"] == "angle"
    assert result["value_deg"] == 90.0
    assert "value_mm" not in result
    assert result["match_method_edge1"] in ("index", "proximity")
    assert result["match_method_edge2"] in ("index", "proximity")
    # SelectEntity: 第一次 append=False，第二次 append=True
    assert mock_view.SelectEntity.call_count == 2
    mock_view.SelectEntity.assert_any_call(edge_h, False)
    mock_view.SelectEntity.assert_any_call(edge_v, True)
