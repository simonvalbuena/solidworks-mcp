# insert_bom_table Implementation Plan

Goal: 實作 MCP tool `insert_bom_table`，在組立件工程圖視圖上插入 Top-Level Only BOM 表。

Architecture: 在 `src/tools/annotation.py` 新增 `_insert_bom_table` COM 函式 + MCP tool 註冊；主路徑用 `IDrawingDoc::InsertBomTable4`，pywin32 late-binding 失敗時退化到 `InsertBomTable3`。

Tech Stack: Python 3.12 / FastMCP / pywin32 / pytest + unittest.mock

Spec: `docs/specs/bom-table.md`

Design: `docs/plans/2026-05-22-insert-bom-table-design.md`

---

## 通用慣例（給 implementer）

- 寫測試前先 `cd D:\UserData\Documents\Code\solidworks-mcp`
- 跑測試：`.venv/Scripts/pytest tests/test_bom_table.py -v`
- 每個 task 結束都跑 `.venv/Scripts/pytest tests/test_bom_table.py -v` 確認該 task 加的 test 過、舊 test 不破
- Commit 訊息格式：`feat(insert_bom_table): <task 主旨>` 或 `test(insert_bom_table): ...`
- 不需要在每個 commit 加 footer

---

## Task 1: 建立測試骨架 + happy path

Implements: `bom-table.md` #R1, #R2, #R3, #R6

Files:
- Create: `tests/test_bom_table.py`
- Modify: `src/tools/annotation.py`（新增 `_insert_bom_table` 函式 stub）

Step 1: 建立 `tests/test_bom_table.py`，內容：

```python
"""測試 insert_bom_table（不需 SolidWorks）。"""

import pytest
from unittest.mock import MagicMock, patch

try:
    from tools.annotation import _insert_bom_table
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(
    not HAS_DEPS, reason="需要 mcp/pywin32 依賴（僅在 SW 主機上可用）"
)


def _make_mock_com(**kwargs):
    """建立模擬 COM 物件的 mock（有 _oleobj_ 屬性）。"""
    m = MagicMock(**kwargs)
    m._oleobj_ = True
    return m


def _make_mock_drawing_with_assembly_view(view_name="工程視圖1"):
    """建立 mock drawing，含一個參考組立件的視圖（FeatureTree 結構）。"""
    drawing = _make_mock_com()
    type(drawing).GetType = property(lambda self: 3)  # swDocDRAWING

    # 視圖 sub-feature
    sub = _make_mock_com()
    sub.GetTypeName2 = "AbsoluteView"
    sub.Name = view_name
    view_obj = _make_mock_com()
    # 視圖參考的文件（組立件 type=2）
    ref_doc = _make_mock_com()
    type(ref_doc).GetType = property(lambda self: 2)
    view_obj.ReferencedDocument = ref_doc
    sub.GetSpecificFeature2.return_value = view_obj
    type(sub).GetNextSubFeature = property(lambda self: None)

    # DrSheet
    sheet = _make_mock_com()
    sheet.GetTypeName2 = "DrSheet"
    sheet.Name = "圖頁1"
    type(sheet).GetFirstSubFeature = property(lambda self, s=sub: s)
    type(sheet).GetNextFeature = property(lambda self: None)
    type(drawing).FirstFeature = property(lambda self, sh=sheet: sh)

    return drawing


def test_insert_bom_table_success():
    """happy path：插入 BOM 表成功，mm→m 換算正確。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        result = _insert_bom_table(
            view_name="工程視圖1", x=250.0, y=180.0, drawing_name=None,
        )

    assert result["status"] == "done"
    assert result["table_name"] == "Bill of Materials1"

    # 驗證 mm→m 換算（250 → 0.25, 180 → 0.18）
    call_args = drawing.InsertBomTable4.call_args[0]
    assert call_args[1] == pytest.approx(0.25)
    assert call_args[2] == pytest.approx(0.18)
    # BomType = swBomTable_TopLevelOnly = 1
    assert call_args[4] == 1
```

Step 2: 跑測試確認失敗（_insert_bom_table 還沒實作）
Run: `.venv/Scripts/pytest tests/test_bom_table.py::test_insert_bom_table_success -v`
Expected: FAIL（ImportError 被 skip，或 AttributeError）

Step 3: 在 `src/tools/annotation.py` 檔尾（`_insert_balloon` 之後）加入：

```python
# === insert_bom_table ===

# swBomType_e
SW_BOM_TOP_LEVEL_ONLY = 1   # swBomTable_TopLevelOnly
SW_BOM_PARTS_ONLY = 2        # swBomTable_PartsOnly
SW_BOM_INDENTED = 3          # swBomTable_Indented


def _insert_bom_table(
    view_name: str,
    x: float,
    y: float,
    drawing_name: str | None = None,
) -> dict:
    """COM 操作：在組立件工程圖視圖上插入 Top-Level Only BOM 表。

    pywin32 late-binding 踩坑：
    - InsertBomTable4 帶 7 參數可能因 VARIANT byref 失敗 → 退化到 InsertBomTable3
    """
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()

    if drawing_name:
        try:
            app.ActivateDoc2(drawing_name, True, 0)
        except Exception as e:
            raise SWError(f"切換到 drawing '{drawing_name}' 失敗: {e}")

    drawing = app.ActiveDoc
    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    doc_type = _safe_get(drawing, "GetType")
    if doc_type is not None and doc_type != 3:
        raise SWError("Active document is not a drawing")

    # 找視圖
    views = _get_drawing_views(drawing, view_name)
    if not views:
        raise SWError(f"View '{view_name}' not found")

    vname, view_obj = views[0]

    # 驗證視圖參考的是組立件
    try:
        ref_doc = view_obj.ReferencedDocument
        if ref_doc is None:
            raise SWError(f"View '{view_name}' has no referenced document")
        ref_type = _safe_get(ref_doc, "GetType")
        if ref_type != 2:
            raise SWError(
                f"View '{view_name}' does not reference an assembly "
                f"(type={ref_type})"
            )
    except SWError:
        raise
    except Exception as e:
        raise SWError(f"檢查視圖參考文件失敗: {e}")

    # 啟動並選取視圖
    drawing.ActivateView(vname)
    try:
        drawing.SelectByID2(
            vname, "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0,
        )
    except Exception as e:
        logger.warning("SelectByID2 失敗: %s", e)

    # mm → meters
    x_m = x / _M_TO_MM
    y_m = y / _M_TO_MM

    # 主路徑 InsertBomTable4
    bom_table = None
    errors = []
    try:
        bom_table = drawing.InsertBomTable4(
            False, x_m, y_m, 0, SW_BOM_TOP_LEVEL_ONLY, "", "",
        )
    except Exception as e:
        errors.append(f"InsertBomTable4: {e}")

    # 退化 InsertBomTable3
    if bom_table is None:
        try:
            bom_table = drawing.InsertBomTable3(
                False, x_m, y_m, 0, SW_BOM_TOP_LEVEL_ONLY, "", "",
            )
        except Exception as e:
            errors.append(f"InsertBomTable3: {e}")

    if bom_table is None:
        raise SWError(
            "Failed to insert BOM table (both API versions returned None): "
            + "; ".join(errors)
        )

    table_name = _safe_get(bom_table, "Name") or ""

    return {
        "status": "done",
        "table_name": table_name,
    }
```

Step 4: 跑測試確認通過
Run: `.venv/Scripts/pytest tests/test_bom_table.py::test_insert_bom_table_success -v`
Expected: PASS

Step 5: Commit
```bash
git add tests/test_bom_table.py src/tools/annotation.py
git commit -m "feat(insert_bom_table): _insert_bom_table 主路徑 + happy path test"
```

---

## Task 2: 視圖驗證錯誤處理

Implements: `bom-table.md` #R4, #R6

Files:
- Modify: `tests/test_bom_table.py`（新增 3 條 test）

Step 1: 在 `tests/test_bom_table.py` 檔尾加入：

```python
def test_not_drawing_raises():
    """active doc 非 drawing → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    type(drawing).GetType = property(lambda self: 1)  # swDocPART

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="not a drawing"):
            _insert_bom_table("工程視圖1", 0, 0, None)


def test_view_not_found_raises():
    """指定的 view_name 不存在 → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="View '不存在的視圖' not found"):
            _insert_bom_table("不存在的視圖", 0, 0, None)


def test_view_not_assembly_raises():
    """view 參考的是零件而非組立件 → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    # 把 ReferencedDocument 改成零件（type=1）
    ref_doc = _make_mock_com()
    type(ref_doc).GetType = property(lambda self: 1)
    # 從 FeatureTree 拿到 view_obj 改其 ReferencedDocument
    sheet = drawing.FirstFeature
    sub = sheet.GetFirstSubFeature
    view_obj = sub.GetSpecificFeature2.return_value
    view_obj.ReferencedDocument = ref_doc

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="does not reference an assembly"):
            _insert_bom_table("工程視圖1", 0, 0, None)
```

Step 2: 跑測試確認 3 條通過
Run: `.venv/Scripts/pytest tests/test_bom_table.py -v`
Expected: 4 PASS（含 task 1 的 happy path）

Step 3: Commit
```bash
git add tests/test_bom_table.py
git commit -m "test(insert_bom_table): 視圖驗證錯誤處理"
```

---

## Task 3: COM API 退化路徑

Implements: `bom-table.md` #R5

Files:
- Modify: `tests/test_bom_table.py`（新增 2 條 test）

Step 1: 在 `tests/test_bom_table.py` 檔尾加入：

```python
def test_fallback_to_v3():
    """InsertBomTable4 失敗時退化到 InsertBomTable3。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    drawing.InsertBomTable4.side_effect = Exception("VARIANT byref failed")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable3.return_value = bom_table

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        result = _insert_bom_table("工程視圖1", 250, 180, None)

    assert result["status"] == "done"
    assert result["table_name"] == "Bill of Materials1"
    drawing.InsertBomTable3.assert_called_once()


def test_both_versions_fail_raises():
    """v4 與 v3 都失敗 → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    drawing.InsertBomTable4.return_value = None
    drawing.InsertBomTable3.return_value = None

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="both API versions returned None"):
            _insert_bom_table("工程視圖1", 0, 0, None)
```

Step 2: 跑測試確認 2 條通過
Run: `.venv/Scripts/pytest tests/test_bom_table.py -v`
Expected: 6 PASS

Step 3: Commit
```bash
git add tests/test_bom_table.py
git commit -m "test(insert_bom_table): COM API 退化路徑 v4→v3"
```

---

## Task 4: drawing_name 切換 + 單位換算邊界

Files:
- Modify: `tests/test_bom_table.py`（新增 3 條 test）

Step 1: 在 `tests/test_bom_table.py` 檔尾加入：

```python
def test_drawing_name_provided():
    """傳入 drawing_name 時呼叫 ActivateDoc2。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table
    app = _make_mock_com(ActiveDoc=drawing)

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        _insert_bom_table("工程視圖1", 250, 180, "assembly_abc.SLDDRW")

    app.ActivateDoc2.assert_called_once_with("assembly_abc.SLDDRW", True, 0)


def test_drawing_name_omitted_skips_activate():
    """drawing_name=None 時不呼叫 ActivateDoc2。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table
    app = _make_mock_com(ActiveDoc=drawing)

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        _insert_bom_table("工程視圖1", 250, 180, None)

    app.ActivateDoc2.assert_not_called()


def test_mm_to_meters_conversion_boundary():
    """邊界值（0,0）與一般值（300.5）換算正確。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        _insert_bom_table("工程視圖1", 0.0, 0.0, None)
        _insert_bom_table("工程視圖1", 300.5, 420.25, None)

    calls = drawing.InsertBomTable4.call_args_list
    # 第一次：0,0
    assert calls[0][0][1] == pytest.approx(0.0)
    assert calls[0][0][2] == pytest.approx(0.0)
    # 第二次：0.3005, 0.42025
    assert calls[1][0][1] == pytest.approx(0.3005)
    assert calls[1][0][2] == pytest.approx(0.42025)
```

Step 2: 跑測試確認 3 條通過
Run: `.venv/Scripts/pytest tests/test_bom_table.py -v`
Expected: 9 PASS

Step 3: Commit
```bash
git add tests/test_bom_table.py
git commit -m "test(insert_bom_table): drawing_name 切換 + 單位換算邊界"
```

---

## Task 5: 註冊 MCP tool

Implements: `bom-table.md` #R1, #R2, #R3, #R6

Files:
- Modify: `src/tools/annotation.py:60-186`（在 `register_tools` 內加新 tool）

Step 1: 在 `src/tools/annotation.py` 的 `register_tools` 函式內、`insert_balloon` 之後加入：

找到這段（約 line 185）：
```python
        except SWError as e:
            raise ToolError(f"insert_balloon 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_balloon 未預期錯誤: {e}")
```

在這段之後加入：
```python
    @mcp.tool()
    async def insert_bom_table(
        view_name: str,
        x: float,
        y: float,
        drawing_name: str | None = None,
    ) -> str:
        """在組立件工程圖視圖上插入 BOM 表（Top-Level Only）。
        view_name: 目標視圖名稱（須參考組立件）。
        x: 放置 X 座標（單位 mm）。
        y: 放置 Y 座標（單位 mm）。
        drawing_name: 目標 drawing，省略則用 active doc。"""
        try:
            result = await sw.execute(
                _insert_bom_table,
                view_name, x, y, drawing_name,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_bom_table 失敗: {e}")
        except Exception as e:
            raise ToolError(f"insert_bom_table 未預期錯誤: {e}")
```

Step 2: 確認 tool 可被 import（不破壞 server 啟動）
Run: `.venv/Scripts/python -c "from tools.annotation import register_tools; print('OK')"`
Expected: `OK`

Step 3: 跑全部測試確認不破壞既有 tool
Run: `.venv/Scripts/pytest tests/ -v`
Expected: 既有 121 tests + 新增 9 tests = 130 PASS（數量視既有實際 test 數）

Step 4: Commit
```bash
git add src/tools/annotation.py
git commit -m "feat(insert_bom_table): 註冊 MCP tool"
```

---

## Task 6: 部署準備 + 整合驗證 checklist

Files:
- Modify: `deploy.sh`（確認 annotation.py 在同步清單）
- Create: 無

Step 1: 開 `deploy.sh` 確認 `src/tools/annotation.py` 已在 rsync/scp 同步清單中

Run: `grep annotation.py deploy.sh`
Expected: 至少一行命中（既有 insert_balloon 已部署過）

Step 2: 若沒有命中，加進去；若有則略過 Edit

Step 3: 列出整合驗證 checklist（不寫進 repo，給使用者參考）

實機驗證步驟（SW 主機 RDP session）：
1. 確認 COM server 已在 RDP session 啟動（不是 SSH）
2. 開一個含組立件視圖的工程圖（至少 3 個零件）
3. 透過 Claude Code 呼叫：
   ```
   insert_bom_table(view_name="工程視圖1", x=250, y=180)
   ```
4. 預期 SW 介面右上區域出現 BOM 表，欄位：Item No / Part Number / Description / Quantity
5. 用 `capture_drawing` 截圖回傳 AI 確認視覺正確
6. 反例驗證：
   - 對零件 view 呼叫 → 應收到 "does not reference an assembly"
   - view_name 亂打 → 應收到 "View '...' not found"

Step 4: 若 deploy.sh 有改，commit
```bash
git add deploy.sh
git commit -m "chore(deploy): 確認 annotation.py 在同步清單"
```

若 deploy.sh 無需改，本 task 不 commit。

---

## 驗收標準（給 reviewer）

- [ ] `pytest tests/test_bom_table.py -v` 9 PASS 0 FAIL
- [ ] `pytest tests/ -v` 整體 PASS（不破壞既有 121 tests）
- [ ] `src/tools/annotation.py` 新增 `_insert_bom_table` + tool 註冊
- [ ] `tests/test_bom_table.py` 9 條 test 覆蓋：happy path / 3 條視圖驗證 / 2 條 COM 退化 / 2 條 drawing_name / 1 條單位換算
- [ ] 實機驗證 checklist 至少跑過項目 3、4、6
- [ ] spec `docs/specs/bom-table.md` status: draft（implement 完成後另開 PR sync 改 active）
