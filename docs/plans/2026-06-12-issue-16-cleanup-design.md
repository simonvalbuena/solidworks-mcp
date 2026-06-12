# Issue #16 清理設計

日期：2026-06-12
Branch：`chore/issue-16-cleanup`
來源：[Issue #16](https://github.com/haunchen/solidworks-mcp/issues/16) — insert_bom_table 實作的 incidental findings

## 查證結論

5 條 findings 逐條對照程式碼現況（不憑 issue 文字）：

| # | Finding | 現況 | 處置 |
|---|---------|------|------|
| 1 | `_insert_balloon` AutoBalloon 退化邏輯瑕疵（`if notes is None and errors:`） | **已過時**：PR #19 重寫整段（`annotation.py:1862-1903`），主路徑 `AutoBalloon5(opts)` 拋例外才退化；回 None 改走 annotation diff 兜底，為 balloon.md spec 刻意設計 | issue 勾掉留言，不修 |
| 2 | 缺「v4 回 None + v3 成功」test | 仍缺：`test_bom_table.py` 只有「v4 拋 Exception」與「兩版都 None」 | 補 test |
| 3 | 兩版都回 None 時 error message 尾端空 colon | 仍存在（`annotation.py:2030-2034`） | 修訊息 |
| 4 | `_auto_add_ref_dims_inner` 末尾不可達 dead code | 仍存在（`annotation.py:1366-1371`） | 刪除 |
| 5 | spec D1 未記載 AnchorType=0 選擇 | 仍缺（`docs/specs/bom-table.md` D1） | spec 補註 |

## 改動設計

### 1. test 補強（`tests/test_bom_table.py`）

新增 `test_v4_returns_none_fallback_to_v3`：
- mock `InsertBomTable4.return_value = None`（不拋例外）
- mock `InsertBomTable3` 回有效物件（`Name="Bill of Materials1"`）
- 斷言：`status == "done"`、`table_name` 正確、`InsertBomTable3` 被呼叫一次

覆蓋 spec D5「失敗**或回 None** 則試 InsertBomTable3」目前缺的組合。

### 2. error message 修飾（`annotation.py:2030-2034`）

errors 為空時訊息改為：
`"Failed to insert BOM table (both API versions returned None, no exceptions raised)"`
有 errors 才接 `": " + "; ".join(errors)`。行為不變，純訊息品質。

### 3. 刪 dead code（`annotation.py:1366-1371`）

`_auto_add_ref_dims_inner` 末尾 try/except 兩條路徑都已 return，後面重複的 return 區塊不可達，直接刪除。無 spec domain 對應，純清理。

### 4. spec 註記（`docs/specs/bom-table.md` D1）

補一句：實作對兩版 API 的 AnchorType 參數傳 0（無 anchor，以 X/Y 座標放置），與「不支援 AnchorType」的 MVP 範圍一致。

## Spec 處理

- `bom-table.md`（active，brownfield）：D1 補註寫入 Pending Changes 區塊，MODIFIED 標記
- 第 1/2/3 項不改變行為契約，不產生新 R 條款
- `balloon.md` 不動

## Issue 收尾

- 第 1 條：issue 勾掉並留言（已被 PR #19 重寫取代，附現行行號）
- 其餘 4 條：隨 PR merge 後勾掉、關閉 Issue #16

## 測試策略

跑全套 pytest（現 143 tests + 新 1 條），確認無回歸。
