# add_dimension radius Implementation Plan

Goal: 擴充 add_dimension 支援 radius（圓弧半徑）尺寸，重構 diameter 共用 COM 流程

Architecture: 把 `_add_diameter_dimension` 改名為 `_add_radial_dimension` 加 `dim_kind` 參數，diameter/radius 共用同一個 COM 函式。Handler 加 `"radius"` 分支。`_calc_diameter_text_pos` 改名為 `_calc_radial_text_pos`。

Tech Stack: pywin32 COM, pytest, MagicMock

---

### Task 1: 重構 — 改名 + 加 dim_kind 參數

Files:
- Modify: `src/tools/annotation.py:649-661` — `_calc_diameter_text_pos` → `_calc_radial_text_pos`
- Modify: `src/tools/annotation.py:926-1044` — `_add_diameter_dimension` → `_add_radial_dimension` + `dim_kind` 參數
- Modify: `src/tools/annotation.py:95-131` — handler 分派加 `"radius"` + docstring 更新
- Modify: `tests/test_annotation_helpers.py:567-576` — 測試 import `_calc_radial_text_pos`
- Modify: `tests/test_annotation_helpers.py:579-628` — 測試 import `_add_radial_dimension`

Step 1: 修改 `src/tools/annotation.py`

1a. `_calc_diameter_text_pos` → `_calc_radial_text_pos`（行 649）：

```python
def _calc_radial_text_pos(
    edge_info: dict, outline_m: list,
) -> dict:
    """計算徑向尺寸文字位置（mm）。

    x: 視圖右邊界 + 偏移
    y: 圓心 y
    edge_info: from _build_edge_info（mm）
    outline_m: [xMin, yMin, xMax, yMax]（meters，from view.GetOutline）
    """
    x_right = outline_m[2] * _M_TO_MM + _DIM_TEXT_OFFSET_MM
    y_center = edge_info["midpoint"]["y"]
    return {"x": round(x_right, 4), "y": round(y_center, 4)}
```

1b. `_add_diameter_dimension` → `_add_radial_dimension`（行 926）：

```python
def _add_radial_dimension(
    view_name: str,
    edge1: dict,
    text_position: dict | None,
    dim_kind: str,
) -> dict:
    """COM 操作：在圓形/圓弧邊線加直徑或半徑尺寸。"""
```

內部 3 處改動：

- 行 957 `_check_edge_type`：
  ```python
  required = "circle" if dim_kind == "diameter" else "arc"
  _check_edge_type(edges_info, idx, required, dim_kind)
  ```

- 行 969 `_calc_diameter_text_pos` → `_calc_radial_text_pos`：
  ```python
  auto_pos = _calc_radial_text_pos(edges_info[idx], outline_m)
  ```

- 行 1005 錯誤訊息：
  ```python
  raise SWError(f"AddDimension 回傳 None — 無法建立{dim_kind}尺寸")
  ```

- 行 1028 回傳：
  ```python
  "dimension_type": dim_kind,
  ```

1c. Handler 分派（行 95-131）：

```python
    @mcp.tool()
    async def add_dimension(
        view_name: str,
        edge1: dict,
        edge2: dict | None = None,
        dimension_type: str = "linear",
        text_position: dict | None = None,
    ) -> str:
        """在視圖上新增尺寸標註。
        先用 probe_drawing_edges 查詢邊線，再傳入邊線的 index + 座標。
        view_name: 目標視圖名稱。
        edge1: {"index": int, "x": float, "y": float} — 第一條邊線。
        edge2: {"index": int, "x": float, "y": float} — 第二條邊線（linear 必填，diameter/radius 不需要）。
        dimension_type: "linear"（兩條邊線距離）、"diameter"（圓形直徑）或 "radius"（圓弧半徑）。
        text_position: {"x": float, "y": float}（mm）— 尺寸文字位置，選填。"""
        try:
            if dimension_type == "linear":
                if edge2 is None:
                    raise ToolError("linear 尺寸需要 edge2")
                result = await sw.execute(
                    _add_linear_dimension,
                    view_name, edge1, edge2, text_position,
                )
            elif dimension_type in ("diameter", "radius"):
                result = await sw.execute(
                    _add_radial_dimension,
                    view_name, edge1, text_position, dimension_type,
                )
            else:
                raise ToolError(f"不支援的 dimension_type: {dimension_type}")
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"add_dimension 失敗: {e}")
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"add_dimension 未預期錯誤: {e}")
```

Step 2: 更新既有 diameter 測試（`tests/test_annotation_helpers.py`）

2a. `test_add_dim_diameter_auto_text_position`（行 567-576）：

```python
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
```

2b. `test_add_dim_diameter_com_flow`（行 579-628）— import 和呼叫改名：

```python
def test_add_dim_diameter_com_flow():
    """diameter COM 流程：SelectEntity 一次、AddDimension 正確、回傳 value_mm。"""
    from tools.annotation import _add_radial_dimension
    from unittest.mock import patch

    # ... mock setup 不變 ...

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
    mock_view.SelectEntity.assert_called_once_with(circle_edge, False)
```

Step 3: 跑測試確認既有測試全過

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: PASS（86 tests）

Step 4: Commit

```
git add src/tools/annotation.py tests/test_annotation_helpers.py
git commit -m "refactor: _add_diameter_dimension → _add_radial_dimension 共用 diameter/radius"
```

---

### Task 2: 新增 radius 測試 + 確認通過

Files:
- Modify: `tests/test_annotation_helpers.py` — 新增 3 個 radius 測試

Step 1: 在 `test_add_dim_diameter_com_flow` 之後新增 3 個測試

```python
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
```

Step 2: 跑測試確認全過

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: PASS（89 tests）

Step 3: Commit

```
git add tests/test_annotation_helpers.py
git commit -m "feat: add_dimension 支援 radius 圓弧半徑尺寸 + 3 tests"
```
