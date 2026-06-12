# Issue #16 清理 Implementation Plan

Goal: 結清 Issue #16 的 4 條有效 incidental findings（補 test、修 error message、刪 dead code、issue 收尾留言），第 5 條 spec 註記已隨設計 commit 完成。

Architecture: 全部是既有檔案的小修。`tests/test_bom_table.py` 補一條 fallback 覆蓋 test；`src/tools/annotation.py` 修 `_insert_bom_table` 錯誤訊息尾端空 colon、刪 `_auto_add_ref_dims_inner` 末尾不可達 dead code。不改任何行為契約。

Tech Stack: Python + pytest + unittest.mock（測試不需 SolidWorks）

Spec: `docs/specs/bom-table.md`（active；D1 delta 已在 Pending Changes，本 plan 不再動 spec）

設計文件: `docs/plans/2026-06-12-issue-16-cleanup-design.md`

執行環境注意：
- 測試指令：`.venv/Scripts/pytest tests/ -v`（Windows venv）
- 本 repo 全套測試目前 143 條 PASS（部分測試在無 pywin32 環境會 skip，skip 不算失敗）

---

### Task 1: 補 test — v4 回 None（不拋例外）退化到 v3 成功

Implements: `bom-table.md` #R5（D5「失敗或回 None 則試 InsertBomTable3」的缺漏組合）

Files:
- Modify: `tests/test_bom_table.py`（在 `test_fallback_to_v3` 之後、`test_both_versions_fail_raises` 之前插入）

注意：這是**補覆蓋型測試**，現有實作（`annotation.py` `_insert_bom_table` 的 `if bom_table is None:` 退化邏輯）已支援此路徑，測試寫完應**直接 PASS**，不是紅燈。這是本 task 的預期，不要因為沒有紅燈階段而懷疑測試無效——鑑別力由「v4 確實回 None 且 v3 被呼叫恰一次」的斷言保證。

Step 1: 在 `tests/test_bom_table.py` 的 `test_fallback_to_v3`（結束於第 142 行 `drawing.InsertBomTable3.assert_called_once()`）之後插入：

```python
def test_v4_returns_none_fallback_to_v3():
    """InsertBomTable4 回 None（不拋例外）時退化到 InsertBomTable3。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    drawing.InsertBomTable4.return_value = None
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable3.return_value = bom_table

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        result = _insert_bom_table("工程視圖1", 250, 180, None)

    assert result["status"] == "done"
    assert result["table_name"] == "Bill of Materials1"
    drawing.InsertBomTable4.assert_called_once()
    drawing.InsertBomTable3.assert_called_once()
```

Step 2: 跑新測試
Run: `.venv/Scripts/pytest tests/test_bom_table.py::test_v4_returns_none_fallback_to_v3 -v`
Expected: PASS（補覆蓋型，見上方注意）

Step 3: 跑整檔確認無回歸
Run: `.venv/Scripts/pytest tests/test_bom_table.py -v`
Expected: 全 PASS（原 9 條 + 新 1 條 = 10 條）

Step 4: Commit
```
git add tests/test_bom_table.py
git commit -m "test: 補 InsertBomTable4 回 None 退化 v3 的覆蓋 (Issue #16)"
```

---

### Task 2: 修 error message 尾端空 colon

Implements: `bom-table.md` #R6（錯誤訊息品質，行為不變）

Files:
- Modify: `src/tools/annotation.py`（`_insert_bom_table` 內 `if bom_table is None: raise SWError(...)` 區塊，約 2030-2034 行）
- Test: `tests/test_bom_table.py`（改既有 `test_both_versions_fail_raises` 的斷言）

Step 1: 先改測試讓它要求新訊息。`tests/test_bom_table.py` 的 `test_both_versions_fail_raises`（第 145-157 行）中：

```python
        with pytest.raises(SWError, match="both API versions returned None"):
            _insert_bom_table("工程視圖1", 0, 0, None)
```

改為：

```python
        with pytest.raises(SWError, match="no exceptions raised"):
            _insert_bom_table("工程視圖1", 0, 0, None)
```

（此 test 的 mock 是兩版都 `return_value = None`、不拋例外，正是 errors 為空的路徑）

Step 2: 跑測試確認失敗
Run: `.venv/Scripts/pytest tests/test_bom_table.py::test_both_versions_fail_raises -v`
Expected: FAIL（現行訊息尾端是空的 `": "`，不含 "no exceptions raised"）

Step 3: 改實作。`src/tools/annotation.py` 中找到（`_insert_bom_table` 內）：

```python
    if bom_table is None:
        raise SWError(
            "Failed to insert BOM table (both API versions returned None): "
            + "; ".join(errors)
        )
```

改為：

```python
    if bom_table is None:
        msg = "Failed to insert BOM table (both API versions returned None"
        if errors:
            msg += "): " + "; ".join(errors)
        else:
            msg += ", no exceptions raised)"
        raise SWError(msg)
```

Step 4: 跑整檔測試
Run: `.venv/Scripts/pytest tests/test_bom_table.py -v`
Expected: 全 PASS。特別確認 `test_fallback_to_v3`（errors 非空路徑不受影響）與 `test_v4_returns_none_fallback_to_v3` 仍綠

Step 5: Commit
```
git add src/tools/annotation.py tests/test_bom_table.py
git commit -m "fix: BOM 插入兩版 API 都回 None 時錯誤訊息去除尾端空 colon (Issue #16)"
```

---

### Task 3: 刪 _auto_add_ref_dims_inner 末尾 dead code

Implements: 無 spec 條款（純清理，無行為變化）

Files:
- Modify: `src/tools/annotation.py:1366-1371`

Step 1: `src/tools/annotation.py` 的 `_auto_add_ref_dims_inner` 末尾現為：

```python
        step = "return"
        return {
            "status": "done",
            "views_processed": [v[0] for v in views],
            "dimensions_added": total_dims,
            "details": details,
        }
    except Exception as e:
        return {"status": "error", "step": step, "error": str(e),
                "error_type": type(e).__name__}

    return {
        "status": "done",
        "views_processed": [v[0] for v in views],
        "dimensions_added": total_dims,
        "details": details,
    }
```

try 路徑與 except 路徑都已 return，最後的 `return {...}` 區塊（`except` 區塊之後、與 `try` 同縮排的 6 行）不可達。刪除它，改為：

```python
        step = "return"
        return {
            "status": "done",
            "views_processed": [v[0] for v in views],
            "dimensions_added": total_dims,
            "details": details,
        }
    except Exception as e:
        return {"status": "error", "step": step, "error": str(e),
                "error_type": type(e).__name__}
```

（即只刪最後 6 行的不可達 return 區塊，其餘不動）

Step 2: 跑全套測試確認無回歸
Run: `.venv/Scripts/pytest tests/ -v`
Expected: 全 PASS（144 條，無新 FAIL；無 pywin32 環境的 skip 不算失敗）

Step 3: Commit
```
git add src/tools/annotation.py
git commit -m "refactor: 移除 _auto_add_ref_dims_inner 末尾不可達 dead code (Issue #16)"
```

---

### Task 4: Issue #16 第 1 條過時 finding 收尾留言

Implements: 無 spec 條款（issue 管理）

Files: 無（GitHub 操作）

Step 1: 在 Issue #16 留言說明第 1 條已過時：

```
gh issue comment 16 --repo haunchen/solidworks-mcp --body "查證結果（2026-06-12）：第 1 條 \`_insert_balloon\` AutoBalloon 退化邏輯瑕疵已過時——PR #19 重寫整段（現 \`annotation.py:1862-1903\`）：主路徑 \`AutoBalloon5(opts)\` 拋例外才退化到 \`AutoBalloon()\`/\`AutoBalloon2()\`；回 None 不退化、改走 annotation diff 兜底（balloon.md spec 刻意設計）。原 issue 描述的 \`if notes is None and errors:\` 程式碼已不存在，此條不需修，直接勾掉。其餘 4 條由 branch \`chore/issue-16-cleanup\` 處理。"
```

Step 2: 編輯 issue body 把第 1 條勾掉。先 `gh issue view 16 --repo haunchen/solidworks-mcp --json body -q .body` 取得現行 body，將第 1 條的 `- [ ] **\`src/tools/annotation.py:1741\`**` 改為 `- [x] **\`src/tools/annotation.py:1741\`**`（只改這一個 checkbox，其餘內容逐字保留），再以 `gh issue edit 16 --repo haunchen/solidworks-mcp --body-file <檔案>` 寫回。body 含反引號與多行內容，務必走 `--body-file` 不要 inline 字串。

Step 3: 驗證
Run: `gh issue view 16 --repo haunchen/solidworks-mcp --json body -q .body`
Expected: 第 1 條為 `- [x]`，其餘 4 條仍 `- [ ]`，其他內容無變動
