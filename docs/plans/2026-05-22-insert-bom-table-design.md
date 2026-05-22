# insert_bom_table 設計

## 概要

在組立件工程圖上插入 BOM 表（Bill of Materials）。包裝 `IDrawingDoc::InsertBomTable4`，失敗退化到 `InsertBomTable3`。

MVP 範圍（YAGNI）：
- 只支援 Top-Level Only（不做 Parts Only / Indented）
- 只用 SW 內建範本 `bom-standard.sldbomtbl`
- 只接受 X/Y 座標，不做 AnchorType / 自動配置

## 參數

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `view_name` | str | 是 | — | 目標視圖名稱（中文，如「工程視圖1」），須為組立件 view |
| `x` | float | 是 | — | 放置 X 座標（單位 mm） |
| `y` | float | 是 | — | 放置 Y 座標（單位 mm） |
| `drawing_name` | str \| None | 否 | None | 目標 drawing；None 則用 active doc |

固定值：
- `BomType` = `swBomTable_TopLevelOnly`（1）
- `TableTemplate` = `""`（用 SW 內建 bom-standard.sldbomtbl）
- `ConfigName` = `""`（用 view 現有 config）
- `AnchorType` = `0`（無 anchor，用座標）

## COM 流程

1. 若傳入 `drawing_name`，`ActivateDoc2(drawing_name)`；否則用 active doc
2. 驗證 active doc 是 drawing（`GetType() == 3`）
3. `FeatureByName(view_name)` 找視圖
4. 驗證 view 的 `ReferencedDocument.GetType() == 2`（組立件）
5. `SelectByID2(view_name, "DRAWINGVIEW", 0, 0, 0, False, 0, None, 0)` 選取 view
6. 主路徑：`drawing.InsertBomTable4(False, x*0.001, y*0.001, 0, 1, "", "")`
7. 退化路徑：若主路徑拋 `pywintypes.com_error` 或回 `None`，改試 `InsertBomTable3(False, x*0.001, y*0.001, 0, 1, "", "")`
8. 讀回傳 BOM table 的 `.Name` 屬性

座標單位換算：tool 介面用 mm，COM 用 meters，乘 0.001。

## 回傳

```json
{
  "status": "done",
  "table_name": "Bill of Materials1"
}
```

## 錯誤處理

| 條件 | Exception |
|------|-----------|
| Active doc 非 drawing | `SWError("Active document is not a drawing")` |
| `view_name` 找不到 | `SWError(f"View '{view_name}' not found")` |
| View 非組立件 | `SWError(f"View '{view_name}' does not reference an assembly")` |
| 兩版 API 都回 None | `SWError("Failed to insert BOM table (both API versions returned None)")` |

## 程式碼位置

- 工具註冊：`src/tools/annotation.py`
- 測試：`tests/test_bom_table.py`

## 測試案例

1. `test_insert_bom_table_success` — happy path + mm→m 換算
2. `test_drawing_name_provided` — 呼叫 `ActivateDoc2`
3. `test_drawing_name_omitted` — 用 active doc
4. `test_not_drawing_raises` — 非 drawing → SWError
5. `test_view_not_found_raises` — view 不存在 → SWError
6. `test_view_not_assembly_raises` — view 非組立件 → SWError
7. `test_fallback_to_v3` — v4 拋 com_error，v3 成功
8. `test_both_versions_fail_raises` — 兩版都 None → SWError
9. `test_mm_to_meters_conversion` — 邊界值與一般值

整合驗證（不在 pytest）：SW 主機 RDP session 開組立件工程圖，跑 tool 後用 `capture_drawing` 截圖確認 BOM 出現在指定座標且欄位齊全。

## 已知踩坑（前置防呆）

- `InsertBomTable4` 在 pywin32 late-binding 可能因 VARIANT byref 失敗 → 內建退化路徑
- 視圖名稱本地化（中文 SW 2021）→ 寫死中文視圖名，使用者要傳對
- `deploy.sh` 易漏帶新檔 → MVP 只動 annotation.py 與 test 檔，不新增模組
