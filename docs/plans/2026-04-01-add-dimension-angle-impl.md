# add_dimension angle Implementation Plan

Goal: 擴充 add_dimension tool 支援 angle 尺寸類型，選取兩條 line edge 自動產生角度標註。

Architecture: 新增 `_add_angle_dimension` 函式處理角度 COM 流程，handler 加 `"angle"` 分派。COM 流程與 linear 相同（選兩條 line → AddDimension），回傳 `value_deg` 而非 `value_mm`。

Tech Stack: pywin32 COM / SolidWorks 2021 API / pytest

---

### Task 1: 測試 + _add_angle_dimension 實作

Files:
- Modify: `tests/test_annotation_helpers.py`（檔尾新增 3 個測試）
- Modify: `src/tools/annotation.py`（handler 分派 + 新增 `_add_angle_dimension`）

Step 1: 在 `tests/test_annotation_helpers.py` 檔尾新增 3 個測試

```python
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
```

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py::test_add_dim_angle_resolve_line_edges tests/test_annotation_helpers.py::test_add_dim_angle_rejects_non_line tests/test_annotation_helpers.py::test_add_dim_angle_com_flow -v`
Expected: 1 PASS（resolve + rejects 用現有函式）+ 1 FAIL（com_flow 找不到 `_add_angle_dimension`）

Step 3: 在 `src/tools/annotation.py` 加實作

3a. Handler 分派：將 `annotation.py:118` 的 `else` 替換為 angle 分派：

```python
            elif dimension_type in ("diameter", "radius"):
                result = await sw.execute(
                    _add_radial_dimension,
                    view_name, edge1, text_position, dimension_type,
                )
            elif dimension_type == "angle":
                if edge2 is None:
                    raise ToolError("angle 尺寸需要 edge2")
                result = await sw.execute(
                    _add_angle_dimension,
                    view_name, edge1, edge2, text_position,
                )
            else:
                raise ToolError(f"不支援的 dimension_type: {dimension_type}")
```

3b. Docstring 更新（`annotation.py:107-108`）：

```python
        edge2: {"index": int, "x": float, "y": float} — 第二條邊線（linear/angle 必填，diameter/radius 不需要）。
        dimension_type: "linear"（兩條邊線距離）、"diameter"（圓形直徑）、"radius"（圓弧半徑）或 "angle"（兩條直線夾角）。
```

3c. 在 `_add_radial_dimension` 函式之後（約 line 1048）新增 `_add_angle_dimension`：

```python
def _add_angle_dimension(
    view_name: str,
    edge1: dict,
    edge2: dict,
    text_position: dict | None,
) -> dict:
    """COM 操作：在兩條直線邊線間加角度尺寸。"""
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError(f"目前的文件不是 Drawing（type={doc_type}）")

    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    vname, view_obj = views[0]
    drawing.ActivateView(vname)

    edges = _get_view_edges(view_obj)
    if not edges:
        raise SWError(f"視圖 {vname} 沒有可見邊線")

    edges_info = _build_edges_info(edges)
    idx1, method1 = _resolve_edge(edges_info, edge1)
    idx2, method2 = _resolve_edge(edges_info, edge2)

    if idx1 == idx2:
        raise SWError("兩條邊線不能相同（index 皆為 %d）" % idx1)

    _check_edge_type(edges_info, idx1, "line", "angle")
    _check_edge_type(edges_info, idx2, "line", "angle")

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
            raise SWError("AddDimension 回傳 None — 無法建立角度尺寸")

        # 設定單位（swDEGREES=0）/ 2 位小數
        try:
            disp_dim.SetUnits2(
                False, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
            )
            disp_dim.SetPrecision3(
                2, SW_PRECISION_UNCHANGED, 2, SW_PRECISION_UNCHANGED,
            )
        except Exception:
            pass

        value_deg = None
        try:
            dim = disp_dim.GetDimension2(0)
            value_deg = round(dim.Value, 4)
        except Exception:
            pass

        drawing.ClearSelection2(True)

        return {
            "status": "done",
            "dimension_type": "angle",
            "value_deg": value_deg,
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

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: 92 passed（89 existing + 3 new）

Step 5: Commit

```bash
git add src/tools/annotation.py tests/test_annotation_helpers.py
git commit -m "feat: add_dimension 支援 angle 角度尺寸 + 3 tests"
```
