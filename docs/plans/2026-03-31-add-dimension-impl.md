# add_dimension Implementation Plan

Goal: 實作 add_dimension tool（僅 linear），搭配強化版 probe_drawing_edges，透過混合匹配（索引+座標近鄰）選取邊線。

Architecture: 在 annotation.py 新增邊線資訊建構 + 匹配函式（純邏輯，可單元測試），重構 probe_drawing_edges 回傳結構化資料，新增 add_dimension tool handler + COM 函式。匹配函式以 edges_info（dict list）操作，COM 函式用匹配到的 index 回查原始 edges 陣列取得 COM 物件。

Tech Stack: pywin32 COM / FastMCP / pytest + unittest.mock

---

### Task 1: 邊線資訊建構函式

Files:
- Modify: `src/tools/annotation.py` (在 `_get_view_edges` 之後新增)
- Test: `tests/test_annotation_helpers.py`

Step 1: 寫測試

在 `tests/test_annotation_helpers.py` 末尾新增：

```python
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
```

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py::test_build_edge_info_line tests/test_annotation_helpers.py::test_build_edge_info_circle -v`
Expected: FAIL (ImportError: cannot import name '_build_edge_info')

Step 3: 在 `src/tools/annotation.py` 的 `_get_view_edges` 函式之後（約 line 429）新增：

```python
_M_TO_MM = 1000.0


def _build_edge_info(edge, index: int) -> dict:
    """從 COM edge 物件建立結構化邊線資訊（座標單位 mm）。"""
    info = {"index": index, "type": "other"}

    try:
        curve = edge.GetCurve  # 屬性(dispatch)
    except Exception:
        return info

    # 頂點座標（m）
    start_pt = end_pt = None
    try:
        sv = edge.GetStartVertex
        if sv is not None:
            pt = sv.GetPoint
            start_pt = (pt[0], pt[1], pt[2])
    except Exception:
        pass
    try:
        ev = edge.GetEndVertex
        if ev is not None:
            pt = ev.GetPoint
            end_pt = (pt[0], pt[1], pt[2])
    except Exception:
        pass

    try:
        is_line = curve.IsLine
    except Exception:
        is_line = False
    try:
        is_circle = curve.IsCircle
    except Exception:
        is_circle = False

    if is_line:
        info["type"] = "line"
        if start_pt:
            info["start"] = {
                "x": round(start_pt[0] * _M_TO_MM, 4),
                "y": round(start_pt[1] * _M_TO_MM, 4),
            }
        if end_pt:
            info["end"] = {
                "x": round(end_pt[0] * _M_TO_MM, 4),
                "y": round(end_pt[1] * _M_TO_MM, 4),
            }
        if start_pt and end_pt:
            info["midpoint"] = {
                "x": round((start_pt[0] + end_pt[0]) / 2 * _M_TO_MM, 4),
                "y": round((start_pt[1] + end_pt[1]) / 2 * _M_TO_MM, 4),
            }
            dx = (end_pt[0] - start_pt[0]) * _M_TO_MM
            dy = (end_pt[1] - start_pt[1]) * _M_TO_MM
            dz = (end_pt[2] - start_pt[2]) * _M_TO_MM
            info["length"] = round(math.sqrt(dx * dx + dy * dy + dz * dz), 4)
    elif is_circle:
        info["type"] = "circle" if start_pt is None else "arc"
        try:
            cp = curve.CircleParams  # (cx,cy,cz, ax,ay,az, radius)
            info["midpoint"] = {
                "x": round(cp[0] * _M_TO_MM, 4),
                "y": round(cp[1] * _M_TO_MM, 4),
            }
            info["radius_mm"] = round(cp[6] * _M_TO_MM, 4)
        except Exception:
            pass
        if start_pt:
            info["start"] = {
                "x": round(start_pt[0] * _M_TO_MM, 4),
                "y": round(start_pt[1] * _M_TO_MM, 4),
            }
        if end_pt:
            info["end"] = {
                "x": round(end_pt[0] * _M_TO_MM, 4),
                "y": round(end_pt[1] * _M_TO_MM, 4),
            }

    return info


def _build_edges_info(edges) -> list[dict]:
    """批次建立邊線資訊清單。"""
    return [_build_edge_info(edge, i) for i, edge in enumerate(edges)]
```

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py::test_build_edge_info_line tests/test_annotation_helpers.py::test_build_edge_info_circle -v`
Expected: PASS

Step 5: Commit

```
git add src/tools/annotation.py tests/test_annotation_helpers.py
git commit -m "feat: _build_edge_info 邊線資訊建構函式 + 2 tests"
```

---

### Task 2: 邊線匹配函式

Files:
- Modify: `src/tools/annotation.py` (在 `_build_edges_info` 之後新增)
- Test: `tests/test_annotation_helpers.py`

Step 1: 寫測試

在 `tests/test_annotation_helpers.py` 末尾新增：

```python
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
```

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -k "match_by_index or match_by_proximity or resolve_edge" -v`
Expected: FAIL (ImportError)

Step 3: 在 `src/tools/annotation.py` 的 `_build_edges_info` 之後新增：

```python
def _match_edge_by_index(
    edges_info: list[dict], index: int, x: float, y: float, tolerance: float = 0.5,
) -> tuple:
    """索引優先匹配。回傳 (matched_index, "index") 或 (None, "fallback")。"""
    if index < 0 or index >= len(edges_info):
        return None, "fallback"

    midpoint = edges_info[index].get("midpoint")
    if midpoint is None:
        return None, "fallback"

    dist = math.sqrt((midpoint["x"] - x) ** 2 + (midpoint["y"] - y) ** 2)
    if dist <= tolerance:
        return index, "index"
    return None, "fallback"


def _match_edge_by_proximity(
    edges_info: list[dict], x: float, y: float, max_distance: float = 2.0,
) -> tuple:
    """近鄰 fallback。回傳 (matched_index, distance) 或拋 SWError。"""
    best_idx = None
    best_dist = float("inf")

    for info in edges_info:
        midpoint = info.get("midpoint")
        if midpoint is None:
            continue
        dist = math.sqrt((midpoint["x"] - x) ** 2 + (midpoint["y"] - y) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_idx = info["index"]

    if best_idx is None or best_dist > max_distance:
        raise SWError(
            f"找不到距離 ({x}, {y}) 在 {max_distance}mm 內的邊線"
            f"（最近距離: {best_dist:.2f}mm）"
        )
    return best_idx, best_dist


def _resolve_edge(edges_info: list[dict], edge_spec: dict) -> tuple:
    """統一入口：先 index 匹配，失敗走 proximity fallback。

    edge_spec: {"index": int, "x": float, "y": float}
    回傳: (matched_index, match_method)
    """
    index = edge_spec.get("index", -1)
    x = edge_spec["x"]
    y = edge_spec["y"]

    matched_idx, method = _match_edge_by_index(edges_info, index, x, y)
    if method == "index":
        return matched_idx, "index"

    matched_idx, _dist = _match_edge_by_proximity(edges_info, x, y)
    return matched_idx, "proximity"
```

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -k "match_by_index or match_by_proximity or resolve_edge" -v`
Expected: PASS (9 tests)

Step 5: Commit

```
git add src/tools/annotation.py tests/test_annotation_helpers.py
git commit -m "feat: 邊線匹配函式 — index 優先 + proximity fallback + 9 tests"
```

---

### Task 3: 重構 probe_drawing_edges

Files:
- Modify: `src/tools/annotation.py` — 替換 `_probe_drawing_edges` 內部實作
- Test: `tests/test_annotation_helpers.py`

Step 1: 寫測試

在 `tests/test_annotation_helpers.py` 末尾新增：

```python
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
```

Step 2: 跑測試確認通過（_build_edges_info 已在 Task 1 實作）

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py::test_probe_output_structure -v`
Expected: PASS

Step 3: 替換 `_probe_drawing_edges` 函式

在 `src/tools/annotation.py` 中，將整個 `_probe_drawing_edges` 函式（約 line 523-882）替換為：

```python
def _probe_drawing_edges(view_name: str | None) -> dict:
    """查詢 Drawing 視圖的可見邊線，回傳結構化邊線資訊。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    # 取得視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        # fallback: 用 _discover_view_names 暴力搜索
        discovered = _discover_view_names(drawing)
        if not discovered:
            raise SWError("找不到任何工程圖視圖")
        target = view_name or discovered[0]
        views = _get_drawing_views(drawing, target)
        if not views:
            raise SWError(f"找不到視圖: {target}")

    results = []
    for vname, view_obj in views:
        drawing.ActivateView(vname)
        edges = _get_view_edges(view_obj)
        edges_info = _build_edges_info(edges)
        results.append({
            "view": vname,
            "edge_count": len(edges_info),
            "edges": edges_info,
        })

    return {
        "status": "done",
        "views": results,
    }
```

同時更新 tool handler 的 docstring（在 `register_tools` 內）：

```python
    @mcp.tool()
    async def probe_drawing_edges(
        view_name: str | None = None,
    ) -> str:
        """查詢 Drawing 視圖的可見邊線資訊。
        回傳每條邊線的 index、type、座標（mm）、幾何參數。
        用於 add_dimension 前確認邊線位置。
        view_name: 指定視圖名稱，預設全部視圖。"""
        try:
            result = await sw.execute(_probe_drawing_edges, view_name)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"probe_drawing_edges 失敗: {e}")
        except Exception as e:
            raise ToolError(f"probe_drawing_edges 未預期錯誤: {e}")
```

Step 4: 跑全部測試確認通過

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS

Step 5: Commit

```
git add src/tools/annotation.py tests/test_annotation_helpers.py
git commit -m "refactor: probe_drawing_edges 回傳結構化邊線資訊 + 1 test"
```

---

### Task 4: add_dimension tool（linear）

Files:
- Modify: `src/tools/annotation.py` — 新增 `_add_linear_dimension` + tool handler
- Test: `tests/test_annotation_helpers.py`

Step 1: 寫測試

在 `tests/test_annotation_helpers.py` 末尾新增：

```python
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
```

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -k "add_dim" -v`
Expected: FAIL (_calc_text_position 不存在)

Step 3: 在 `src/tools/annotation.py` 新增實作

3a. 在 `_resolve_edge` 之後新增 `_calc_text_position`：

```python
_DIM_TEXT_OFFSET_MM = 15.0


def _calc_text_position(
    edges_info: list[dict], idx1: int, idx2: int,
) -> dict:
    """計算尺寸文字預設位置（mm）。

    取兩條邊線中點的平均位置，往 Y 方向偏移。
    """
    mp1 = edges_info[idx1].get("midpoint", {"x": 0, "y": 0})
    mp2 = edges_info[idx2].get("midpoint", {"x": 0, "y": 0})
    return {
        "x": round((mp1["x"] + mp2["x"]) / 2, 4),
        "y": round((mp1["y"] + mp2["y"]) / 2 + _DIM_TEXT_OFFSET_MM, 4),
    }
```

3b. 在 `_auto_add_ref_dims` 之前新增 COM 函式：

```python
def _add_linear_dimension(
    view_name: str,
    edge1: dict,
    edge2: dict,
    text_position: dict | None,
) -> dict:
    """COM 操作：在兩條邊線間加線性尺寸。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    # 取得視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    vname, view_obj = views[0]
    drawing.ActivateView(vname)

    # 取邊線
    edges = _get_view_edges(view_obj)
    if not edges:
        raise SWError(f"視圖 {vname} 沒有可見邊線")

    # 匹配
    edges_info = _build_edges_info(edges)
    idx1, method1 = _resolve_edge(edges_info, edge1)
    idx2, method2 = _resolve_edge(edges_info, edge2)

    if idx1 == idx2:
        raise SWError("兩條邊線不能相同（index 皆為 %d）" % idx1)

    # 文字位置
    if text_position:
        text_x = text_position["x"] / _M_TO_MM
        text_y = text_position["y"] / _M_TO_MM
    else:
        auto_pos = _calc_text_position(edges_info, idx1, idx2)
        text_x = auto_pos["x"] / _M_TO_MM
        text_y = auto_pos["y"] / _M_TO_MM

    # 關閉尺寸值輸入對話框
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception:
        pass

    try:
        # 選取邊線
        drawing.ClearSelection2(True)
        ok1 = view_obj.SelectEntity(edges[idx1], False)
        ok2 = view_obj.SelectEntity(edges[idx2], True)  # append

        if not ok1 or not ok2:
            raise SWError(f"SelectEntity 失敗: edge1={ok1}, edge2={ok2}")

        # AddDimension
        ext = drawing.Extension
        disp_dim = None

        for d in range(4):
            try:
                disp_dim = ext.AddDimension(text_x, text_y, 0, d)
                if disp_dim is not None:
                    break
            except Exception:
                pass

        if disp_dim is None:
            try:
                disp_dim = drawing.AddDimension2(text_x, text_y, 0)
            except Exception:
                pass

        if disp_dim is None:
            raise SWError("AddDimension 回傳 None — 無法建立尺寸")

        # 設定單位 mm / 2 位小數
        try:
            disp_dim.SetUnits2(
                False, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
            )
            disp_dim.SetPrecision3(
                2, SW_PRECISION_UNCHANGED, 2, SW_PRECISION_UNCHANGED,
            )
        except Exception:
            pass

        drawing.ClearSelection2(True)

        return {
            "status": "done",
            "text_position": {
                "x": round(text_x * _M_TO_MM, 4),
                "y": round(text_y * _M_TO_MM, 4),
            },
            "match_method_edge1": method1,
            "match_method_edge2": method2,
        }

    finally:
        if orig_pref is not None:
            try:
                app.SetUserPreferenceToggle(
                    SW_INPUT_DIM_VAL_ON_CREATE, orig_pref,
                )
            except Exception:
                pass
```

3c. 在 `register_tools` 內（`auto_add_reference_dimensions` 之前）新增 tool handler：

```python
    @mcp.tool()
    async def add_dimension(
        view_name: str,
        edge1: dict,
        edge2: dict,
        text_position: dict | None = None,
    ) -> str:
        """在兩條邊線之間加線性尺寸。
        先用 probe_drawing_edges 查詢邊線，再傳入邊線的 index + 座標。
        view_name: 目標視圖名稱。
        edge1: {"index": int, "x": float, "y": float} — 第一條邊線。
        edge2: {"index": int, "x": float, "y": float} — 第二條邊線。
        text_position: {"x": float, "y": float}（mm）— 尺寸文字位置，選填。"""
        try:
            result = await sw.execute(
                _add_linear_dimension,
                view_name, edge1, edge2, text_position,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"add_dimension 失敗: {e}")
        except Exception as e:
            raise ToolError(f"add_dimension 未預期錯誤: {e}")
```

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS

Step 5: Commit

```
git add src/tools/annotation.py tests/test_annotation_helpers.py
git commit -m "feat: add_dimension tool — 線性尺寸 + 混合匹配 + 3 tests"
```
