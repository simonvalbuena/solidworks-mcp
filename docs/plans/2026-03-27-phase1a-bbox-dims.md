# Phase 1a: Bbox 整體外形尺寸 Implementation Plan

Goal: 自動偵測工程圖視圖中的最外邊線，加上水平/垂直方向的整體外形參考尺寸。

Architecture: 透過 FeatureTree 取得 IView → GetVisibleEntities2 取得邊線 → 分類直線方向找最外對 → SelectEntity 選取 → AddDimension 放置尺寸。所有 COM 存取遵循 probe 驗證的屬性/方法規則。

Tech Stack: pywin32 COM (SolidWorks 2021), FastMCP, asyncio

---

### Task 1: 提取共用 helper — `_get_drawing_views`

將 probe tool 中驗證過的 FeatureTree 遍歷邏輯提取為可重用的 helper。

Files:
- Modify: `src/tools/annotation.py`
- Test: `tests/test_annotation_helpers.py`

Step 1: 寫測試

```python
# tests/test_annotation_helpers.py
"""測試 annotation helper（不需 SolidWorks）。"""

import pytest
from unittest.mock import MagicMock


def _make_mock_drawing(view_names):
    """建立 mock drawing，模擬 FeatureTree 結構。"""
    drawing = MagicMock()

    # 建立 sub features（視圖）
    subs = []
    for i, name in enumerate(view_names):
        sub = MagicMock()
        sub.GetTypeName2 = "AbsoluteView" if i == 0 else "UnfoldedView"
        sub.Name = name
        view_obj = MagicMock()
        sub.GetSpecificFeature2.return_value = view_obj
        subs.append((sub, view_obj))

    # 串連 sub features
    for i, (sub, _) in enumerate(subs):
        if i + 1 < len(subs):
            sub.GetNextSubFeature.return_value = subs[i + 1][0]
        else:
            sub.GetNextSubFeature.return_value = None

    # 建立 sheet feature
    sheet = MagicMock()
    sheet.GetTypeName2 = "DrSheet"
    sheet.Name = "圖頁1"
    sheet.GetFirstSubFeature.return_value = subs[0][0] if subs else None

    # 建立其他 features
    folder = MagicMock()
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
```

Step 2: 跑測試確認失敗
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: FAIL（`_get_drawing_views` 尚未存在）

Step 3: 在 `annotation.py` 加入 `_get_drawing_views`

在 `_discover_view_names` 後面加入：

```python
_VIEW_FEATURE_TYPES = ("AbsoluteView", "UnfoldedView", "DrDrawingView")


def _get_drawing_views(
    drawing, view_name: str | None = None,
) -> list[tuple[str, object]]:
    """透過 FeatureTree 取得 Drawing 中的 IView 物件列表。

    回傳 [(view_name, view_obj), ...]。
    GetFirstView 在 pywin32 EnsureDispatch 下不可用，
    改用 FirstFeature → DrSheet → GetFirstSubFeature 遍歷。
    """
    results = []
    feat = drawing.FirstFeature  # 屬性
    while feat is not None:
        try:
            type_name = feat.GetTypeName2  # 屬性
        except Exception:
            break
        if type_name == "DrSheet":
            try:
                sub = feat.GetFirstSubFeature()  # 方法
            except Exception:
                sub = None
            while sub is not None:
                try:
                    st = sub.GetTypeName2  # 屬性
                    sn = sub.Name  # 屬性
                except Exception:
                    break
                if st in _VIEW_FEATURE_TYPES:
                    if view_name is None or sn == view_name:
                        try:
                            view_obj = sub.GetSpecificFeature2()  # 方法
                            results.append((sn, view_obj))
                        except Exception:
                            pass
                try:
                    sub = sub.GetNextSubFeature()  # 方法
                except Exception:
                    sub = None
        try:
            feat = feat.GetNextFeature  # 屬性
        except Exception:
            break
    return results
```

Step 4: 跑測試確認通過
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: PASS

Step 5: Commit
`git add src/tools/annotation.py tests/test_annotation_helpers.py && git commit -m "feat: 新增 _get_drawing_views helper — FeatureTree 遍歷取得 IView"`

---

### Task 2: 邊線分類 helper — `_classify_edges`

將邊線分為水平線、垂直線、圓、其他。

Files:
- Modify: `src/tools/annotation.py`
- Modify: `tests/test_annotation_helpers.py`

Step 1: 寫測試

在 `tests/test_annotation_helpers.py` 加入：

```python
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
```

Step 2: 跑測試確認失敗
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py::test_classify_horizontal_line -v`
Expected: FAIL

Step 3: 在 `annotation.py` 加入 `_classify_edges`

```python
import math

_DIR_THRESHOLD = 0.1  # 方向向量分量門檻


def _classify_edges(edges) -> dict:
    """分類邊線為水平線、垂直線、完整圓、其他。

    回傳 {
        "horizontal": [(edge, start, end), ...],
        "vertical": [(edge, start, end), ...],
        "circles": [(edge, center, radius), ...],
        "other": int,
    }
    """
    horizontal = []
    vertical = []
    circles = []
    other = 0

    for edge in edges:
        try:
            curve = edge.GetCurve  # 屬性(dispatch)
        except Exception:
            other += 1
            continue

        try:
            is_line = curve.IsLine  # 屬性
        except Exception:
            is_line = False
        try:
            is_circle = curve.IsCircle  # 屬性
        except Exception:
            is_circle = False

        if is_line:
            try:
                params = curve.LineParams  # 屬性: (px, py, pz, dx, dy, dz)
                dx, dy, dz = params[3], params[4], params[5]
            except Exception:
                other += 1
                continue

            # 方向向量正規化後判斷
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length < 1e-12:
                other += 1
                continue
            ndx, ndy, ndz = abs(dx / length), abs(dy / length), abs(dz / length)

            start = end = None
            try:
                sv = edge.GetStartVertex  # 屬性
                if sv is not None:
                    start = tuple(sv.GetPoint)[:3]  # 屬性
                ev = edge.GetEndVertex  # 屬性
                if ev is not None:
                    end = tuple(ev.GetPoint)[:3]  # 屬性
            except Exception:
                pass

            if ndy < _DIR_THRESHOLD and ndz < _DIR_THRESHOLD:
                # X 方向為主 → 水平線
                horizontal.append((edge, start, end))
            elif ndx < _DIR_THRESHOLD and ndz < _DIR_THRESHOLD:
                # Y 方向為主 → 垂直線
                vertical.append((edge, start, end))
            else:
                other += 1

        elif is_circle:
            # 判斷完整圓 vs 圓弧
            try:
                sv = edge.GetStartVertex  # 屬性
            except Exception:
                sv = None
            if sv is None:
                # 完整圓
                try:
                    cp = curve.CircleParams  # 屬性: (cx,cy,cz, ax,ay,az, radius)
                    center = (cp[0], cp[1], cp[2])
                    radius = cp[6]
                    circles.append((edge, center, radius))
                except Exception:
                    other += 1
            else:
                other += 1  # 圓弧，Phase 1 跳過
        else:
            other += 1

    return {
        "horizontal": horizontal,
        "vertical": vertical,
        "circles": circles,
        "other": other,
    }
```

Step 4: 跑測試確認通過
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: PASS

Step 5: Commit
`git add src/tools/annotation.py tests/test_annotation_helpers.py && git commit -m "feat: 新增 _classify_edges helper — 邊線分類（水平/垂直/圓）"`

---

### Task 3: 外圍邊線配對 helper — `_find_bounding_edges`

從分類後的水平/垂直線中找最外對（用於 bbox 尺寸）。

Files:
- Modify: `src/tools/annotation.py`
- Modify: `tests/test_annotation_helpers.py`

Step 1: 寫測試

```python
def test_find_bounding_edges_horizontal():
    from tools.annotation import _find_bounding_edges
    # 三條水平線在不同 Y 位置
    edges = {
        "horizontal": [
            ("e1", (0, 0.01, 0), (0.1, 0.01, 0)),   # y=0.01
            ("e2", (0, 0.05, 0), (0.1, 0.05, 0)),   # y=0.05
            ("e3", (0, -0.02, 0), (0.1, -0.02, 0)),  # y=-0.02
        ],
        "vertical": [],
    }
    result = _find_bounding_edges(edges)
    # 垂直範圍尺寸用最上和最下的水平線
    assert result["h_top"][0] == "e2"      # y 最大
    assert result["h_bottom"][0] == "e3"   # y 最小


def test_find_bounding_edges_vertical():
    from tools.annotation import _find_bounding_edges
    edges = {
        "horizontal": [],
        "vertical": [
            ("e1", (0.01, 0, 0), (0.01, 0.1, 0)),   # x=0.01
            ("e2", (-0.03, 0, 0), (-0.03, 0.1, 0)),  # x=-0.03
            ("e3", (0.05, 0, 0), (0.05, 0.1, 0)),    # x=0.05
        ],
    }
    result = _find_bounding_edges(edges)
    assert result["v_right"][0] == "e3"    # x 最大
    assert result["v_left"][0] == "e2"     # x 最小


def test_find_bounding_edges_insufficient():
    from tools.annotation import _find_bounding_edges
    # 只有一條水平線，無法配對
    edges = {
        "horizontal": [("e1", (0, 0, 0), (0.1, 0, 0))],
        "vertical": [],
    }
    result = _find_bounding_edges(edges)
    assert result["h_top"] is None or result["h_top"] == result["h_bottom"]
```

Step 2: 跑測試確認失敗
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py::test_find_bounding_edges_horizontal -v`
Expected: FAIL

Step 3: 在 `annotation.py` 加入 `_find_bounding_edges`

```python
def _find_bounding_edges(classified: dict) -> dict:
    """從分類後的邊線找最外對。

    回傳 {
        "h_top": (edge, start, end) or None,      — Y 最大的水平線
        "h_bottom": (edge, start, end) or None,    — Y 最小的水平線
        "v_left": (edge, start, end) or None,      — X 最小的垂直線
        "v_right": (edge, start, end) or None,     — X 最大的垂直線
    }
    """
    result = {"h_top": None, "h_bottom": None, "v_left": None, "v_right": None}

    # 水平線：用 Y 座標排序（取 start 的 Y）
    h_lines = classified.get("horizontal", [])
    if len(h_lines) >= 2:
        def h_y(item):
            _, start, end = item
            if start is not None:
                return start[1]
            return 0.0
        sorted_h = sorted(h_lines, key=h_y)
        result["h_bottom"] = sorted_h[0]
        result["h_top"] = sorted_h[-1]
    elif len(h_lines) == 1:
        result["h_top"] = h_lines[0]
        result["h_bottom"] = h_lines[0]

    # 垂直線：用 X 座標排序（取 start 的 X）
    v_lines = classified.get("vertical", [])
    if len(v_lines) >= 2:
        def v_x(item):
            _, start, end = item
            if start is not None:
                return start[0]
            return 0.0
        sorted_v = sorted(v_lines, key=v_x)
        result["v_left"] = sorted_v[0]
        result["v_right"] = sorted_v[-1]
    elif len(v_lines) == 1:
        result["v_left"] = v_lines[0]
        result["v_right"] = v_lines[0]

    return result
```

Step 4: 跑測試確認通過
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: PASS

Step 5: Commit
`git add src/tools/annotation.py tests/test_annotation_helpers.py && git commit -m "feat: 新增 _find_bounding_edges helper — 找最外邊線對"`

---

### Task 4: 取得邊線 helper — `_get_view_edges`

封裝 GetVisibleComponents + GetVisibleEntities2 的 COM 呼叫。

Files:
- Modify: `src/tools/annotation.py`

Step 1: 在 `annotation.py` 加入 `_get_view_edges`

```python
def _get_view_edges(view_obj) -> list:
    """從 IView 取得可見邊線列表。

    必須傳入 component（從 GetVisibleComponents 取得），
    傳 None 會 DISP_E_TYPEMISMATCH。
    """
    comps = view_obj.GetVisibleComponents  # 屬性，回傳 tuple
    if not comps or len(comps) == 0:
        return []
    edges = view_obj.GetVisibleEntities2(comps[0], 1)  # swViewEntityType_Edge=1
    if edges is None:
        return []
    return list(edges)
```

Step 2: Commit
`git add src/tools/annotation.py && git commit -m "feat: 新增 _get_view_edges helper — 取得視圖可見邊線"`

---

### Task 5: 主 tool — `auto_add_reference_dimensions`

整合所有 helper，加上 SelectEntity + AddDimension 的 COM 呼叫。

Files:
- Modify: `src/tools/annotation.py`

Step 1: 在 `register_tools` 中新增 tool

```python
    @mcp.tool()
    async def auto_add_reference_dimensions(
        view_name: str | None = None,
        phase: str = "all",
        offset_mm: float = 15.0,
    ) -> str:
        """自動添加參考尺寸到 Drawing 視圖。
        適用於無參數化尺寸的零件（模具、匯入幾何）。
        view_name: 指定視圖名稱，預設全部視圖。
        phase: "bbox"（外形尺寸）/ "circles"（圓形）/ "all"（全部）。
        offset_mm: 尺寸文字偏移量（mm）。"""
        try:
            result = await sw.execute(
                _auto_add_ref_dims, view_name, phase, offset_mm,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"auto_add_reference_dimensions 失敗: {e}")
        except Exception as e:
            raise ToolError(f"auto_add_reference_dimensions 未預期錯誤: {e}")
```

Step 2: 加入同步 COM 函式 `_auto_add_ref_dims`

```python
DIM_OFFSET_BASE = 0.015   # 15mm
DIM_OFFSET_STACK = 0.010  # 10mm

# swSmartDimensionDirection_e
SW_DIM_DOWN = 0
SW_DIM_UP = 1
SW_DIM_RIGHT = 2
SW_DIM_LEFT = 3


def _auto_add_ref_dims(
    view_name: str | None,
    phase: str,
    offset_mm: float,
) -> dict:
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = drawing.GetType
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    offset = offset_mm / 1000.0  # mm → m

    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError("找不到任何工程圖視圖")

    do_bbox = phase in ("bbox", "all")
    total_dims = 0
    details = []

    for vname, view_obj in views:
        drawing.ActivateView(vname)

        edges = _get_view_edges(view_obj)
        if not edges:
            details.append({"view": vname, "error": "無可見邊線"})
            continue

        classified = _classify_edges(edges)
        view_detail = {
            "view": vname,
            "edges_found": {
                "lines_h": len(classified["horizontal"]),
                "lines_v": len(classified["vertical"]),
                "circles": len(classified["circles"]),
                "other": classified["other"],
            },
            "dims_added": [],
        }

        if do_bbox:
            outline = view_obj.GetOutline  # 屬性: [xMin, yMin, xMax, yMax]
            if outline is not None:
                outline = list(outline)
            else:
                outline = [0, 0, 0.2, 0.2]

            bounding = _find_bounding_edges(classified)
            bbox_dims = _add_bbox_dims(
                drawing, view_obj, bounding, outline, offset,
            )
            view_detail["dims_added"].extend(bbox_dims)
            total_dims += len(bbox_dims)

        details.append(view_detail)

    return {
        "status": "done",
        "views_processed": [v[0] for v in views],
        "dimensions_added": total_dims,
        "details": details,
    }


def _add_bbox_dims(
    drawing, view_obj, bounding: dict, outline: list, offset: float,
) -> list:
    """選取外圍邊線對，放置 bbox 尺寸。回傳已加尺寸的描述列表。"""
    dims = []
    ext = drawing.Extension

    # 垂直範圍尺寸（用最上+最下水平線）
    h_top = bounding.get("h_top")
    h_bottom = bounding.get("h_bottom")
    if (h_top and h_bottom and h_top is not h_bottom):
        try:
            drawing.ClearSelection2(True)
        except Exception:
            pass
        try:
            view_obj.SelectEntity(h_top[0], False)
            view_obj.SelectEntity(h_bottom[0], True)
            dim_x = outline[0] - offset  # 視圖左邊外
            dim_y = (outline[1] + outline[3]) / 2  # 垂直居中
            disp_dim = ext.AddDimension(dim_x, dim_y, 0, SW_DIM_LEFT)
            if disp_dim is not None:
                dims.append({"type": "vertical_extent", "position": [dim_x, dim_y]})
                logger.info("bbox 垂直尺寸已加: x=%.4f y=%.4f", dim_x, dim_y)
            else:
                logger.warning("AddDimension 回傳 None（垂直範圍）")
        except Exception as e:
            logger.warning("垂直範圍尺寸失敗: %s", e)

    # 水平範圍尺寸（用最左+最右垂直線）
    v_left = bounding.get("v_left")
    v_right = bounding.get("v_right")
    if (v_left and v_right and v_left is not v_right):
        try:
            drawing.ClearSelection2(True)
        except Exception:
            pass
        try:
            view_obj.SelectEntity(v_left[0], False)
            view_obj.SelectEntity(v_right[0], True)
            dim_x = (outline[0] + outline[2]) / 2  # 水平居中
            dim_y = outline[1] - offset  # 視圖下方
            disp_dim = ext.AddDimension(dim_x, dim_y, 0, SW_DIM_DOWN)
            if disp_dim is not None:
                dims.append({"type": "horizontal_extent", "position": [dim_x, dim_y]})
                logger.info("bbox 水平尺寸已加: x=%.4f y=%.4f", dim_x, dim_y)
            else:
                logger.warning("AddDimension 回傳 None（水平範圍）")
        except Exception as e:
            logger.warning("水平範圍尺寸失敗: %s", e)

    try:
        drawing.ClearSelection2(True)
    except Exception:
        pass

    return dims
```

Step 3: Commit
`git add src/tools/annotation.py && git commit -m "feat: 新增 auto_add_reference_dimensions tool — bbox 外形尺寸"`

---

### Task 6: 實機測試

在 SolidWorks 主機重啟 server 後，用 MCP tool 執行完整流程。

Step 1: 重啟 server
Run: `.venv/Scripts/python src/server.py`

Step 2: 執行完整流程
```
create_drawing(paper_size="A3")
insert_standard_views_aligned(source_doc="Papilla_#3+_上模具250207.SLDPRT")
auto_add_reference_dimensions(phase="bbox")
capture_drawing(resolution="high")
```

Step 3: 確認截圖中有外形尺寸出現

Step 4: 若 `AddDimension` 有問題，根據錯誤訊息調整參數
已知待驗證：`Extension` 屬性存取方式、`AddDimension` 參數格式

Step 5: Commit 最終修正
