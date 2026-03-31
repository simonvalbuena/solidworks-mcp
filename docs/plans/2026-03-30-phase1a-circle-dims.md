# Phase 1a Circle Dims Implementation Plan

Goal: 在 `auto_add_reference_dimensions` 的 circles 路徑中，偵測完整圓邊線，同半徑去重後自動加上直徑參考尺寸。

Architecture: 複用現有 `_classify_edges` 的 circles 輸出，新增 `_dedupe_circles` 去重 + `_add_circle_dims` 放置尺寸。尺寸文字放視圖右側堆疊（接在 bbox 垂直尺寸後面）。所有邏輯在 `src/tools/annotation.py` 內完成。

Tech Stack: pywin32 COM (IView.SelectEntity, Extension.AddDimension), pytest + MagicMock

---

### Task 1: _dedupe_circles 去重邏輯 + 測試

Files:
- Modify: `src/tools/annotation.py` (在 `_find_bounding_edges` 後面新增)
- Modify: `tests/test_annotation_helpers.py` (新增測試)

Step 1: 在 `tests/test_annotation_helpers.py` 末尾新增測試

```python
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
    circles = [
        ("edge_a", (0.0, 0.0, 0.0), 0.00500),
        ("edge_b", (0.03, 0.0, 0.0), 0.00509),  # 差 0.09mm < 0.1mm
    ]
    result = _dedupe_circles(circles)
    assert len(result) == 1
    assert result[0][0] == "edge_b"  # 離重心更遠


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
```

Step 2: 跑測試確認失敗（_dedupe_circles 尚未存在）
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py::test_dedupe_circles_same_radius_picks_farthest -v`
Expected: FAIL (ImportError)

Step 3: 在 `src/tools/annotation.py` 的 `_find_bounding_edges` 函式後面（約 line 347）新增

```python
_RADIUS_TOL = 1e-4  # 0.1mm — 同半徑去重容差


def _dedupe_circles(
    circles: list[tuple],
) -> list[tuple]:
    """同半徑只保留離幾何重心最遠的圓。

    circles: [(edge, (cx,cy,cz), radius), ...]
    回傳: [(edge, (cx,cy,cz), radius), ...]  去重後
    """
    if not circles:
        return []

    # 按 radius 分組（容差 _RADIUS_TOL）
    groups: dict[float, list] = {}
    for item in circles:
        _, _center, r = item
        matched = False
        for key_r in groups:
            if abs(r - key_r) < _RADIUS_TOL:
                groups[key_r].append(item)
                matched = True
                break
        if not matched:
            groups[r] = [item]

    # 幾何重心
    all_cx = sum(c[0] for _, c, _ in circles) / len(circles)
    all_cy = sum(c[1] for _, c, _ in circles) / len(circles)

    # 每組取離重心最遠的
    result = []
    for _r, items in groups.items():
        farthest = max(
            items,
            key=lambda it: (it[1][0] - all_cx) ** 2 + (it[1][1] - all_cy) ** 2,
        )
        result.append(farthest)

    return result
```

Step 4: 跑測試確認全部通過
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v -k dedupe`
Expected: 5 passed

Step 5: Commit
```
git add src/tools/annotation.py tests/test_annotation_helpers.py
git commit -m "feat: _dedupe_circles — 同半徑圓形去重邏輯"
```

---

### Task 2: _add_circle_dims 放置直徑尺寸

Files:
- Modify: `src/tools/annotation.py` (在 `_add_bbox_dims` 後面新增)

Step 1: 在 `src/tools/annotation.py` 的 `_add_bbox_dims` 函式後面新增

```python
DIM_DIAMETER_OFFSET = 0.008  # 8mm — 直徑尺寸文字離視圖邊緣


def _add_circle_dims(
    drawing,
    view_obj,
    circles: list[tuple],
    outline: list,
    offset: float,
    start_index: int = 0,
) -> list:
    """對去重後的圓 SelectEntity → AddDimension 建直徑尺寸。

    circles: [(edge, (cx,cy,cz), radius), ...]（已去重）
    outline: [xMin, yMin, xMax, yMax]（圖紙公尺）
    offset: 基礎偏移量（公尺）
    start_index: 堆疊起始索引（接在 bbox 尺寸後面）
    回傳: [{"type": "diameter", "value_mm": float, "ok": bool}, ...]
    """
    dims = []

    try:
        ext = drawing.Extension
        if ext is None:
            return [{"error": "drawing.Extension 回傳 None"}]
    except Exception as e:
        return [{"error": f"drawing.Extension 失敗: {e}"}]

    # 關閉尺寸值輸入對話框
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    orig_pref = None
    try:
        orig_pref = app.GetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE)
        app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, False)
    except Exception as e:
        dims.append({"warning": f"swInputDimValOnCreate: {e}"})

    x_max = outline[2]
    y_mid = (outline[1] + outline[3]) / 2

    for i, (edge, (_cx, _cy, _cz), radius) in enumerate(circles):
        diag = {
            "type": "diameter",
            "value_mm": round(radius * 2 * 1000, 3),
        }

        try:
            drawing.ClearSelection2(True)
        except Exception:
            pass

        # SelectEntity 選取圓形邊線
        ok = False
        try:
            ok = view_obj.SelectEntity(edge, False)
            diag["select_entity"] = ok
        except Exception as e:
            diag["select_entity"] = f"FAIL: {e}"

        if not ok:
            diag["ok"] = False
            diag["error"] = "SelectEntity 失敗"
            dims.append(diag)
            continue

        # 文字位置：視圖右側堆疊
        stack_idx = start_index + i
        dim_x = x_max + DIM_DIAMETER_OFFSET + stack_idx * 0.010
        dim_y = y_mid

        # 嘗試 AddDimension（圓形選取應自動建直徑尺寸）
        disp_dim = None
        add_dim_errors = []

        for d in range(4):
            try:
                disp_dim = ext.AddDimension(dim_x, dim_y, 0, d)
                if disp_dim is not None:
                    diag["method"] = f"ext.AddDimension(dir={d})"
                    break
            except Exception as e:
                add_dim_errors.append(f"dir={d}: {e}")

        if disp_dim is None:
            try:
                disp_dim = drawing.AddDimension2(dim_x, dim_y, 0)
                if disp_dim is not None:
                    diag["method"] = "AddDimension2"
            except Exception as e:
                add_dim_errors.append(f"AddDimension2: {e}")

        if add_dim_errors:
            diag["add_dim_errors"] = add_dim_errors

        if disp_dim is not None:
            try:
                disp_dim.SetUnits2(
                    False, SW_UNIT_MM, SW_FRACTION_DECIMAL, 0, False, 0,
                )
                disp_dim.SetPrecision3(
                    2, SW_PRECISION_UNCHANGED, 2, SW_PRECISION_UNCHANGED,
                )
            except Exception as e:
                diag["unit_warning"] = f"設定單位/精度失敗: {e}"
            diag["ok"] = True
            logger.info(
                "circle 直徑尺寸已加: r=%.4f x=%.4f y=%.4f",
                radius, dim_x, dim_y,
            )
        else:
            diag["ok"] = False
            diag["error"] = "AddDimension 回傳 None"

        dims.append(diag)

    try:
        drawing.ClearSelection2(True)
    except Exception:
        pass

    # 恢復偏好
    if orig_pref is not None:
        try:
            app.SetUserPreferenceToggle(SW_INPUT_DIM_VAL_ON_CREATE, orig_pref)
        except Exception:
            pass

    return dims
```

Step 2: 確認語法正確
Run: `.venv/Scripts/python -c "from tools.annotation import _add_circle_dims; print('OK')"`
Expected: OK（如果缺 pywin32 依賴會跳 ImportError，在 CI 環境預期）

Step 3: Commit
```
git add src/tools/annotation.py
git commit -m "feat: _add_circle_dims — 直徑尺寸放置邏輯"
```

---

### Task 3: 接入 _auto_add_ref_dims_inner 主流程

Files:
- Modify: `src/tools/annotation.py:890-941` (_auto_add_ref_dims_inner 的 view loop)

Step 1: 修改 `_auto_add_ref_dims_inner` 的 view loop，在 bbox 區塊後加入 circles 區塊

在現有的 `if do_bbox:` 區塊（結束於 `total_dims += ...`）後面，加入：

```python
            do_circles = phase in ("circles", "all")
            if do_circles:
                step = f"dedupe_circles_{vi}"
                deduped = _dedupe_circles(classified["circles"])
                view_detail["circles_before_dedup"] = len(classified["circles"])
                view_detail["circles_after_dedup"] = len(deduped)

                if deduped:
                    step = f"get_outline_circ_{vi}"
                    try:
                        raw_outline = view_obj.GetOutline
                        if raw_outline is not None:
                            outline_c = [raw_outline[0], raw_outline[1],
                                         raw_outline[2], raw_outline[3]]
                        else:
                            outline_c = [0, 0, 0.2, 0.2]
                    except Exception:
                        outline_c = [0, 0, 0.2, 0.2]

                    # bbox 佔用的右側堆疊數量
                    bbox_right_count = sum(
                        1 for d in view_detail["dims_added"]
                        if d.get("ok") and d.get("type") == "vertical_extent"
                    )

                    step = f"add_circle_dims_{vi}"
                    circle_dims = _add_circle_dims(
                        drawing, view_obj, deduped, outline_c, offset,
                        start_index=bbox_right_count,
                    )
                    view_detail["dims_added"].extend(circle_dims)
                    total_dims += sum(1 for d in circle_dims if d.get("ok"))
```

同時把 `do_bbox` 的宣告提到 view loop 外面（和 `do_circles` 一起）：

```python
        do_bbox = phase in ("bbox", "all")
        do_circles = phase in ("circles", "all")
```

並移除 view loop 內部的 `do_circles` 宣告（已在外面）。

注意：`outline` 變數在 bbox 區塊已取過，circles 區塊需要獨立取（因為 bbox 可能沒跑），所以用 `outline_c` 避免混淆。如果 `do_bbox` 和 `do_circles` 都跑，可以複用 outline，但為了簡潔先各自取。

Step 2: 跑全部測試確認沒有 regression
Run: `.venv/Scripts/pytest tests/ -v`
Expected: all passed

Step 3: Commit
```
git add src/tools/annotation.py
git commit -m "feat: circles phase 接入 auto_add_reference_dimensions 主流程"
```

---

### Task 4: 整合測試 — 圓形標註端到端 mock 測試

Files:
- Modify: `tests/test_annotation_helpers.py`

Step 1: 新增 `_make_mock_circle_edge` 帶自訂半徑的版本，並加整合測試

```python
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
```

Step 2: 跑測試
Run: `.venv/Scripts/pytest tests/test_annotation_helpers.py -v`
Expected: all passed

Step 3: Commit
```
git add tests/test_annotation_helpers.py
git commit -m "test: 圓形去重整合測試 — classify → dedupe 端到端"
```
