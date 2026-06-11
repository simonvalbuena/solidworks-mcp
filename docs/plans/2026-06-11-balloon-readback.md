# insert_balloon 回傳值修復 Implementation Plan

Goal: 修復 Issue #14 — AutoBalloon 回傳 None 導致 balloons 清單空、component 過濾失效、style/auto_layout 無法傳遞，三層全部救回。

Architecture: 建立主路徑改 `IModelDocExtension::InsertAutoBalloon` + `CreateAutoBalloonOptions`（property 賦值避開 positional VARIANT_BOOL 的 DISP_E_TYPEMISMATCH），失敗退化到現行 `AutoBalloon()` / `AutoBalloon2()` 並以 warnings 告知功能丟失。讀取雙層：回傳值優先，None 時用視圖 annotation 建立前後 diff 找回本次氣球。component 過濾落實「標全部、刪不符」，包含比對 + `SelectByID("NOTE")` + `EditDelete` 刪除。

Tech Stack: Python 3 + pywin32 COM（late-binding）、FastMCP、pytest + unittest.mock

Spec: `docs/specs/balloon.md`

設計文件: `docs/plans/2026-06-11-balloon-readback-design.md`

## 執行者必讀背景

- 所有改動集中兩個檔案：`src/tools/annotation.py`（`_insert_balloon` 區段，約 1635-1791 行）與 `tests/test_balloon.py`。
- 測試不需 SolidWorks，COM 全 mock。測試指令：`.venv/Scripts/pytest tests/test_balloon.py -v`（在 repo 根目錄執行）。
- pywin32 late-binding 踩坑前提（不可改回）：
  - `SelectByID2` / `AutoBalloon5/3` positional bool 參數會 DISP_E_TYPEMISMATCH，即使 VARIANT(VT_BOOL) 包裝也無效 → 只能用 `SelectByID`（5 參數）、property 賦值、無參數/純 int 參數方法。
  - COM 成員有「屬性 vs 方法」歧義，既有 helper `_com_prop_or_method`（annotation.py:242）只適用回傳 COM 物件的成員（檢查 `_oleobj_`），字串/bool 回傳值要用本 plan 新增的 `_com_get`。
- `_make_mock_drawing_with_view`（test_balloon.py:36）回傳 `(drawing, ext)`，view 物件藏在 FeatureTree mock 裡；本 plan Task 2 會把 view 物件掛到 `drawing.mock_view` 供測試取用。
- 每個 task 完成即 commit，訊息格式照專案慣例（`fix:` / `test:` 前綴 + 中文描述 + Issue 編號）。

---

### Task 1: 建立路徑重構 — InsertAutoBalloon 主路徑 + 退化鏈 + warnings

Implements: `balloon.md` #R1, #R5, #R6, #R8

Files:
- Modify: `src/tools/annotation.py`（`_insert_balloon` 函式與其上方新增 helper）
- Test: `tests/test_balloon.py`

Step 1: 改寫既有測試以符合新建立路徑，並新增 3 個主路徑/退化測試

`tests/test_balloon.py` 修改既有測試（逐一替換）：

1a. `test_insert_balloon_full_flow`：把 `drawing.AutoBalloon.return_value = notes` 改成 `ext.InsertAutoBalloon.return_value = notes`，並把結尾的 `drawing.AutoBalloon.assert_called_once()` 改為：

```python
    ext.InsertAutoBalloon.assert_called_once()
    drawing.AutoBalloon.assert_not_called()
```

1b. `test_insert_balloon_fallback_to_autoballoon2`：在 `drawing.AutoBalloon.side_effect = ...` 前加一行，讓主路徑先失敗：

```python
    ext.InsertAutoBalloon.side_effect = Exception("DISP_E_TYPEMISMATCH")
```

並在結尾 assert 後加：

```python
    assert "warnings" in result
    assert any("未套用" in w for w in result["warnings"])
```

1c. `test_insert_balloon_autoballoon_returns_none` 與 `test_insert_balloon_no_components`：把 `drawing.AutoBalloon.return_value = None` 改成 `ext.InsertAutoBalloon.return_value = None`。

1d. `test_insert_balloon_component_filter` 與 `test_insert_balloon_component_filter_no_match`：把 `drawing.AutoBalloon.return_value = notes` 改成 `ext.InsertAutoBalloon.return_value = notes`。

新增測試（加在 `# === insert_balloon 完整流程 ===` 區段末尾）：

```python
def test_insert_balloon_main_path_options():
    """主路徑：CreateAutoBalloonOptions property 賦值 + InsertAutoBalloon。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([("bracket-1", "1")])
    ext.InsertAutoBalloon.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(
            view_name="工程視圖1", style="triangle", auto_layout=False,
        )

    opts = ext.CreateAutoBalloonOptions.return_value
    assert opts.Style == SW_BALLOON_STYLE["triangle"]
    assert opts.Layout == SW_BALLOON_LAYOUT_NONE
    assert opts.UpperTextContent == SW_BALLOON_TEXT_ITEM_NUM
    assert opts.Size == SW_BALLOON_FIT_TIGHTEST
    ext.InsertAutoBalloon.assert_called_once_with(opts)
    drawing.AutoBalloon.assert_not_called()
    assert result["balloon_count"] == 1
    assert "warnings" not in result


def test_insert_balloon_fallback_warns():
    """主路徑失敗 → AutoBalloon() 退化，warnings 告知 style 未套用。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([("part-1", "1")])
    ext.InsertAutoBalloon.side_effect = Exception("DISP_E_TYPEMISMATCH")
    drawing.AutoBalloon.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    drawing.AutoBalloon.assert_called_once()
    assert result["balloon_count"] == 1
    assert any("未套用" in w for w in result["warnings"])


def test_insert_balloon_all_paths_fail():
    """三層建立路徑全敗 → SWError 帶各層上下文。"""
    from errors import SWError
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    ext.InsertAutoBalloon.side_effect = Exception("err-main")
    drawing.AutoBalloon.side_effect = Exception("err-ab")
    drawing.AutoBalloon2.side_effect = Exception("err-ab2")

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        with pytest.raises(SWError, match="氣球建立失敗") as ei:
            _insert_balloon(view_name="工程視圖1")

    msg = str(ei.value)
    assert "err-main" in msg
    assert "err-ab2" in msg
```

注意：1b 修改後 `test_insert_balloon_fallback_to_autoballoon2` 內 AutoBalloon2 仍回 notes，warnings 斷言成立（退化路徑）。

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: FAIL（新測試 3 個失敗 + 改過的既有測試失敗；`_insert_balloon` 尚未走 InsertAutoBalloon、無 warnings）

Step 3: 改寫 `_insert_balloon` 建立段

在 `src/tools/annotation.py` 的 `_read_balloon_info` 之後、`_insert_balloon` 之前新增 helper：

```python
def _apply_balloon_options(opts, style_val: int, layout: int) -> None:
    """以 property 賦值設定 AutoBalloonOptions。

    避開 AutoBalloon5/3 positional VARIANT_BOOL 的 DISP_E_TYPEMISMATCH
    （MathTransform ArrayData property 賦值有成功前例）。
    只設必要屬性，減少 late-binding 下屬性名不符直接炸掉主路徑的面積；
    任何一個賦值失敗即整個主路徑失敗，由呼叫端退化。
    """
    opts.Layout = layout
    opts.Style = style_val
    opts.Size = SW_BALLOON_FIT_TIGHTEST
    opts.UpperTextContent = SW_BALLOON_TEXT_ITEM_NUM
    opts.InsertMagneticLine = False
```

改寫 `_insert_balloon`（完整取代現有函式本體；docstring 一併更新）：

```python
def _insert_balloon(
    view_name: str,
    component: str | None = None,
    style: str = "circular",
    auto_layout: bool = True,
) -> dict:
    """COM 操作：在視圖上插入氣球標註。

    pywin32 late-binding 限制與對策：
    - 主路徑用 IModelDocExtension::InsertAutoBalloon +
      CreateAutoBalloonOptions（property 賦值避開 positional
      VARIANT_BOOL 類型不符），style/auto_layout 在主路徑下生效
    - 主路徑失敗退化到 AutoBalloon()/AutoBalloon2()，
      style/auto_layout 不套用並以 warnings 告知
    - 視圖選取維持 SelectByID（5 參數版），SelectByID2 有類型問題
    """
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    style_val = SW_BALLOON_STYLE.get(style)
    if style_val is None:
        raise SWError(
            f"不支援的 style: {style}，可用: {', '.join(SW_BALLOON_STYLE)}"
        )

    # 找視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"找不到視圖: {view_name}")

    warnings: list[str] = []

    # 啟動並選取視圖（AutoBalloon 需要 view 被選中）
    drawing.ActivateView(view_name)
    try:
        drawing.SelectByID(view_name, "DRAWINGVIEW", 0, 0, 0)
    except Exception as e:
        logger.warning("SelectByID 失敗: %s", e)

    layout = SW_BALLOON_LAYOUT_RIGHT if auto_layout else SW_BALLOON_LAYOUT_NONE

    # 建立：主路徑 InsertAutoBalloon，退化 AutoBalloon()/AutoBalloon2()
    notes = None
    fallback_used = False
    errors = []
    try:
        ext = drawing.Extension
        opts = ext.CreateAutoBalloonOptions()
        _apply_balloon_options(opts, style_val, layout)
        notes = ext.InsertAutoBalloon(opts)
    except Exception as e:
        errors.append(f"InsertAutoBalloon: {e}")
        try:
            notes = drawing.AutoBalloon()
            fallback_used = True
        except Exception as e2:
            errors.append(f"AutoBalloon(): {e2}")
            try:
                notes = drawing.AutoBalloon2(int(layout), 0)
                fallback_used = True
            except Exception as e3:
                errors.append(f"AutoBalloon2(): {e3}")
                raise SWError(f"氣球建立失敗: {'; '.join(errors)}")

    if fallback_used:
        warnings.append("style/auto_layout 未套用（退化路徑）")

    # 嘗試從回傳值讀取 Note 物件
    balloon_notes = _extract_notes(notes)

    # 遍歷 Note 取零件資訊
    balloons = _read_balloon_info(balloon_notes)

    # component 過濾（僅在有 Note 物件時可刪除）
    if component is not None and balloon_notes:
        keep = [b for b in balloons if b["component"] == component]
        balloons = keep

    drawing.ClearSelection2(True)

    result = {
        "status": "done",
        "balloon_count": len(balloons),
        "balloons": balloons,
        "view_name": view_name,
    }
    if warnings:
        result["warnings"] = warnings
    return result
```

（component 過濾此 task 保持原邏輯，Task 3 重寫；annotation diff Task 2 加入。）

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: PASS（全部）

Step 5: 跑全套測試確認無回歸

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS

Step 6: Commit

```
git add src/tools/annotation.py tests/test_balloon.py
git commit -m "fix: insert_balloon 主路徑改 InsertAutoBalloon + Options 物件 (Issue #14)"
```

---

### Task 2: annotation 前後 diff 讀取兜底

Implements: `balloon.md` #R2, #R3, #R7

Files:
- Modify: `src/tools/annotation.py`
- Test: `tests/test_balloon.py`

Step 1: 擴充 mock helpers + 寫失敗的測試

2a. `_make_mock_drawing_with_view` 末尾（`return drawing, ext` 前）加一行，讓測試拿得到 view 物件：

```python
    drawing.mock_view = view_obj
```

2b. `_make_mock_notes` 的 for 迴圈內（`notes.append(note)` 前）加：

```python
        note.GetName.return_value = f"balloon_note_{item_no}"
        note.IsBomBalloon.return_value = True
```

2c. 新增測試：

```python
# === annotation diff 讀取兜底 ===


def _make_existing_note(name="old_note_1", is_balloon=True):
    """建立視圖上既有的 note mock。"""
    note = _make_mock_com()
    note.GetName.return_value = name
    note.IsBomBalloon.return_value = is_balloon
    bt = _make_mock_com()
    bt.GetText.return_value = "99"
    comp = _make_mock_com()
    comp.Name2 = "old-part-1"
    bt.GetComponent.return_value = comp
    note.GetBomBalloonTexts.return_value = [bt]
    return note


def test_insert_balloon_diff_fallback():
    """回傳值 None → annotation 前後 diff 找回本次氣球，排除既有氣球。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    ext.InsertAutoBalloon.return_value = None
    old = _make_existing_note("old_note_1")
    new_notes = _make_mock_notes([("bracket-1", "1"), ("shaft-1", "2")])
    drawing.mock_view.GetNotes = MagicMock(
        side_effect=[[old], [old] + new_notes],
    )

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["balloon_count"] == 2
    comps = [b["component"] for b in result["balloons"]]
    assert "old-part-1" not in comps
    assert "bracket-1" in comps
    assert "warnings" not in result


def test_insert_balloon_diff_skips_non_balloon():
    """diff 出的新 note 若非 BOM balloon 不計入。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    ext.InsertAutoBalloon.return_value = None
    plain_note = _make_existing_note("plain_text_1", is_balloon=False)
    new_notes = _make_mock_notes([("bracket-1", "1")])
    drawing.mock_view.GetNotes = MagicMock(
        side_effect=[[], [plain_note] + new_notes],
    )

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["balloon_count"] == 1
    assert result["balloons"][0]["component"] == "bracket-1"


def test_insert_balloon_read_total_failure():
    """回傳值 None 且 GetNotes 全程失敗 → 空清單 + warnings，不報錯。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    ext.InsertAutoBalloon.return_value = None
    drawing.mock_view.GetNotes = MagicMock(side_effect=Exception("COM fail"))

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["status"] == "done"
    assert result["balloon_count"] == 0
    assert any("無法讀取" in w for w in result["warnings"])


def test_insert_balloon_genuine_zero():
    """視圖無零件（diff 前後皆空）→ 0 顆，無 warnings。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    ext.InsertAutoBalloon.return_value = None
    drawing.mock_view.GetNotes = MagicMock(side_effect=[[], []])

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1")

    assert result["balloon_count"] == 0
    assert "warnings" not in result
```

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_balloon.py -v -k "diff or read_total or genuine"`
Expected: FAIL（diff 邏輯不存在，count 為 0 或 warnings 缺失）

Step 3: 實作 diff 兜底

3a. 在 `_apply_balloon_options` 前新增三個 helper：

```python
def _com_get(obj, name):
    """讀取 COM 成員，處理 late-binding 屬性/方法歧義（含非 COM 物件回傳值）。

    與 _com_prop_or_method 不同：適用回傳字串/bool/陣列的成員。
    先嘗試方法呼叫，TypeError（值非 callable，屬性形式）時回傳值本身。
    COM 呼叫本身的錯誤向上拋，由呼叫端決定兜底。
    """
    attr = getattr(obj, name)
    try:
        return attr()
    except TypeError:
        return attr


def _get_view_note_names(view) -> set[str]:
    """取得視圖上所有 note 名稱集合（建立前快照，diff 兜底用）。"""
    names: set[str] = set()
    for note in _extract_notes(_com_get(view, "GetNotes")):
        try:
            names.add(str(_com_get(note, "GetName")))
        except Exception as e:
            logger.debug("GetName 失敗: %s", e)
    return names


def _collect_new_balloon_notes(view, before_names: set[str]) -> list:
    """diff 出建立後新增、且為 BOM balloon 的 Note 物件。"""
    result = []
    for note in _extract_notes(_com_get(view, "GetNotes")):
        try:
            name = str(_com_get(note, "GetName"))
        except Exception:
            continue
        if name in before_names:
            continue
        try:
            if not bool(_com_get(note, "IsBomBalloon")):
                continue
        except Exception:
            # 無法判斷時，新增的 note 視為本次氣球
            logger.debug("IsBomBalloon 判斷失敗，視為氣球: %s", name)
        result.append(note)
    return result
```

3b. `_insert_balloon` 內兩處修改：

「找視圖」段之後（`warnings: list[str] = []` 之後、`drawing.ActivateView` 之前）插入快照：

```python
    view_obj = views[0][1]

    # 建立前快照：視圖既有 note 名稱（回傳值不可用時 diff 兜底）
    before_names: set[str] | None = None
    try:
        before_names = _get_view_note_names(view_obj)
    except Exception as e:
        logger.warning("建立前 note 快照失敗: %s", e)
```

「嘗試從回傳值讀取 Note 物件」段改為：

```python
    # 讀取：回傳值優先，None/空時 annotation diff 兜底
    balloon_notes = _extract_notes(notes)
    read_failed = False
    if not balloon_notes:
        if before_names is None:
            read_failed = True
        else:
            try:
                balloon_notes = _collect_new_balloon_notes(
                    view_obj, before_names,
                )
            except Exception as e:
                logger.warning("annotation diff 讀取失敗: %s", e)
                read_failed = True
    if read_failed:
        warnings.append("氣球可能已建立但無法讀取資訊")
```

注意 mock 相容性：`_make_mock_drawing_with_view` 的 view 物件未設 `GetNotes` 的測試（Task 1 的測試），MagicMock 自動屬性回傳的 MagicMock 不可迭代不可索引，`_extract_notes` 會回空清單、diff 得 0 顆——不影響那些走「回傳值讀取成功」的測試。

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: PASS（全部，含 Task 1 的測試）

Step 5: Commit

```
git add src/tools/annotation.py tests/test_balloon.py
git commit -m "fix: insert_balloon 回傳值 None 時用 annotation diff 找回氣球 (Issue #14)"
```

---

### Task 3: component 包含比對 + 實際刪除不符氣球

Implements: `balloon.md` #R4, #R7

Files:
- Modify: `src/tools/annotation.py`
- Test: `tests/test_balloon.py`

Step 1: 寫失敗的測試

```python
# === component 過濾（包含比對 + 刪除）===


def test_insert_balloon_component_substring_match():
    """component 採包含比對：bracket 命中 bracket-1@assy。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([
        ("bracket-1@assy", "1"),
        ("shaft-1@assy", "2"),
    ])
    ext.InsertAutoBalloon.return_value = notes

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1", component="bracket")

    assert result["balloon_count"] == 1
    assert result["balloons"][0]["component"] == "bracket-1@assy"
    # 不符的 shaft 氣球被選取並刪除
    sel_names = [c[0][0] for c in drawing.SelectByID.call_args_list
                 if c[0][1] == "NOTE"]
    assert any("balloon_note_2" in n for n in sel_names)
    drawing.EditDelete.assert_called_once()


def test_insert_balloon_component_delete_failure_warns():
    """個別氣球刪除失敗 → 不中斷，記 warnings。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    notes = _make_mock_notes([
        ("bracket-1@assy", "1"),
        ("shaft-1@assy", "2"),
    ])
    ext.InsertAutoBalloon.return_value = notes
    drawing.EditDelete.side_effect = Exception("delete blocked")

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1", component="bracket")

    assert result["balloon_count"] == 1
    assert any("刪除氣球失敗" in w for w in result["warnings"])


def test_insert_balloon_component_filter_skipped_on_read_failure():
    """讀取全敗 + 指定 component → 過濾不執行，warnings 說明。"""
    drawing, ext = _make_mock_drawing_with_view("工程視圖1")
    ext.InsertAutoBalloon.return_value = None
    drawing.mock_view.GetNotes = MagicMock(side_effect=Exception("COM fail"))

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com()
        inst.get_app.return_value.ActiveDoc = drawing

        result = _insert_balloon(view_name="工程視圖1", component="bracket")

    assert result["balloon_count"] == 0
    assert any("無法讀取" in w for w in result["warnings"])
    assert any("過濾未執行" in w for w in result["warnings"])
    drawing.EditDelete.assert_not_called()
```

既有測試連動修改：

3a. `test_insert_balloon_component_filter`（component="bracket-1" 保 3 刪 2）：新增斷言 `assert drawing.EditDelete.call_count == 2`。

3b. `test_insert_balloon_component_filter_no_match`（component="not-exist" 全刪）：新增斷言 `assert drawing.EditDelete.call_count == 2`。

Step 2: 跑測試確認失敗

Run: `.venv/Scripts/pytest tests/test_balloon.py -v -k "component"`
Expected: FAIL（現行只過濾清單不刪除、無包含比對、無 warnings）

Step 3: 實作

3c. `_read_balloon_info` 的外層 except 改為 append 空白資訊，保持與輸入 notes 長度對齊（zip 過濾需要）：

```python
        except Exception as e:
            logger.warning("讀取氣球資訊失敗: %s", e)
            balloons.append({"component": "", "item_number": ""})
```

3d. 在 `_collect_new_balloon_notes` 後新增 helper：

```python
def _delete_balloon_note(drawing, note, view_name: str) -> bool:
    """刪除單顆氣球 Note。

    用 SelectByID（5 參數版，late-binding 安全）+ EditDelete；
    名稱先試 view 限定格式再試裸名。
    """
    try:
        name = str(_com_get(note, "GetName"))
    except Exception as e:
        logger.warning("取得氣球名稱失敗: %s", e)
        return False
    drawing.ClearSelection2(True)
    for sel_name in (f"{name}@{view_name}", name):
        try:
            ok = drawing.SelectByID(sel_name, "NOTE", 0, 0, 0)
        except Exception as e:
            logger.debug("SelectByID(%s) 失敗: %s", sel_name, e)
            continue
        if ok:
            try:
                drawing.EditDelete()
                return True
            except Exception as e:
                logger.warning("EditDelete 失敗: %s", e)
                return False
    return False
```

3e. `_insert_balloon` 的 component 過濾段整段改為：

```python
    # component 過濾：標全部、刪不符（包含比對，Name2 帶 instance 後綴全名）
    if component is not None:
        if read_failed:
            warnings.append("component 過濾未執行（無法讀取氣球資訊）")
        else:
            kept = []
            for note, info in zip(balloon_notes, balloons):
                if not info["component"]:
                    # 讀不到零件名的氣球不刪，避免誤殺
                    kept.append(info)
                    warnings.append("無法判斷氣球零件名稱，保留該氣球")
                    continue
                if component in info["component"]:
                    kept.append(info)
                    continue
                if not _delete_balloon_note(drawing, note, view_name):
                    warnings.append(f"刪除氣球失敗: {info['component']}")
            balloons = kept
```

注意：`test_insert_balloon_component_delete_failure_warns` 中刪除失敗的氣球不進 kept（圖上殘留但不回報），warnings 已告知——與設計文件「個別刪除失敗不中斷，記 warnings」一致。

Step 4: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_balloon.py -v`
Expected: PASS（全部）

Step 5: Commit

```
git add src/tools/annotation.py tests/test_balloon.py
git commit -m "fix: insert_balloon component 過濾落實包含比對與實際刪除 (Issue #14)"
```

---

### Task 4: tool docstring 更新 + 全套回歸

Implements: `balloon.md` #R2, #R6

Files:
- Modify: `src/tools/annotation.py`（`insert_balloon` tool handler docstring，約 165-175 行）
- Test: 全套

Step 1: 更新 tool handler docstring

`async def insert_balloon` 的 docstring 改為：

```python
        """在組立件工程圖視圖上插入氣球標註。
        view_name: 目標視圖名稱。
        component: 指定零件名稱（包含比對），不指定則標全部零件；
                   不符的氣球建立後會被刪除。
        style: 氣球樣式（circular/triangle/hexagon），預設 circular。
        auto_layout: 自動排列氣球位置，預設 true。
        回傳 balloons 含每顆氣球的零件名稱與 item number；
        COM 退化路徑或讀取失敗時以 warnings 欄位告知。"""
```

Step 2: 跑全套測試

Run: `.venv/Scripts/pytest tests/ -v`
Expected: PASS（全部，無回歸）

Step 3: Commit

```
git add src/tools/annotation.py
git commit -m "docs: insert_balloon tool docstring 補 warnings 與包含比對說明 (Issue #14)"
```

---

### Task 5: 實機驗證（手動，SW 主機）

Implements: `balloon.md` #R1-#R8（COM 路徑可行性唯有實機能證明）

此 task 不在本機執行，列入 PR test plan 由使用者在 SW 主機驗證（deploy 流程見 memory `reference_sw_host_deploy.md`）：

- [ ] InsertAutoBalloon + Options 主路徑建球成功（無 warnings）
- [ ] 回傳 balloons 含正確 component / item_number
- [ ] style=triangle 實際生效（氣球為三角形）
- [ ] auto_layout=false 不自動排列
- [ ] component 過濾實際刪除不符氣球，保留指定零件
- [ ] 視圖上既有氣球不被誤報、不被誤刪
- [ ] 若主路徑在實機踩坑：確認退化路徑氣球仍建立 + warnings 出現
