# insert_balloon 修復設計：AutoBalloon 回傳值讀取（Issue #14）

## 背景

`insert_balloon` 用 `AutoBalloon()` 建氣球成功，但 pywin32 late-binding 下回傳 VARIANT 為 None，導致：

- `balloons` 清單永遠空，caller 不知道哪些零件被標了什麼編號
- `component` 過濾無法運作（沒有 Note 物件可刪除）
- `style` / `auto_layout` 無法傳遞（AutoBalloon5/3 帶參數版有 VARIANT_BOOL DISP_E_TYPEMISMATCH，VARIANT(VT_BOOL) 明確包裝也無效）

修復範圍：三層全部救回。

## 技術路線

主路徑改用 `IModelDocExtension::InsertAutoBalloon` + `CreateAutoBalloonOptions`，
options 用 property 賦值避開 positional VARIANT_BOOL 坑（專案已有 MathTransform
ArrayData property 賦值成功經驗）。不採 EnsureDispatch / typelib early-binding
（改變全局 dispatch 模式會波及其他 tools，離線主機 gencache 行為不確定）。

## 建立路徑（三層退化鏈）

1. **主路徑**：`ext = drawing.Extension` → `opts = ext.CreateAutoBalloonOptions()`
   → property 賦值（Style / Layout / UpperTextContent=ItemNumber 等）
   → `notes = ext.InsertAutoBalloon(opts)`。style / auto_layout 全功能。
2. **退化**：CreateAutoBalloonOptions 或 InsertAutoBalloon 在 late-binding 踩坑
   → 退回現行 `AutoBalloon()` 無參數版。此時 style / auto_layout 無法套用，
   回傳加 `warnings` 明說（退化丟功能，不做 bom-table D5 那種無感退化）。
3. **兩層全敗** → SWError 帶兩層錯誤上下文。

視圖選取維持現行 `ActivateView` + `SelectByID`（5 參數舊版），已驗證可行不動。

## 讀取（雙層）

1. **回傳值**：InsertAutoBalloon 回傳 VARIANT 用現有 `_extract_notes()` 解析，
   有 Note 走現有 `_read_balloon_info()`（`GetBomBalloonTexts` 讀 item number、
   `GetComponent().Name2` 讀零件名）。
2. **annotation 前後 diff**：回傳值 None 時啟用。呼叫前遍歷 `view.GetNotes()`
   記錄既有 balloon note 名稱集合（`note.GetName()`）；建立後再遍歷一次，
   找出「新增 + `IsBomBalloon()` 為 True」的 Note → 同樣走 `_read_balloon_info()`。
   既有氣球不會被誤認為本次建立。

## component 過濾

語意：「標全部、再刪不相關的」（原設計語意，這次落實）。
比對採包含比對（`component in comp_name`）——Name2 回傳 `bracket-1@assembly`
帶 instance 後綴全名，要求精確全名 caller 幾乎打不出來；
代價是 `bracket` 會同時命中 `bracket-mount`。
對不符的 Note 取 Annotation 物件選取後刪除（late-binding 可行的刪法 plan 階段定）；
個別刪除失敗不中斷，記 warnings。

## 讀取全敗兜底

兩層都讀不到時（氣球其實已建立）：回空清單 + warnings
「氣球已建立但無法讀取資訊」；有指定 component 時過濾不執行，warnings 一併說明。

## 回傳格式

```json
{
  "status": "done",
  "balloon_count": 5,
  "balloons": [
    {"component": "bracket-1@assy", "item_number": "1"}
  ],
  "view_name": "工程視圖1",
  "warnings": ["style 未套用（退化路徑）"]
}
```

`warnings` 僅在有內容時出現，來源三種：退化路徑（style/auto_layout 未套用）、
讀取全敗、個別氣球刪除失敗。

## 錯誤處理（拋 SWError 的四類）

1. 沒有開啟的 Drawing
2. 找不到 view_name
3. 無效 style
4. 建立兩層全敗（錯訊帶兩層上下文）

## 測試策略

沿用現有 mock COM 模式（`tests/test_balloon.py` 擴充）：

- Options property 賦值與 style/layout 對映
- InsertAutoBalloon 回 Note → 第一層讀取
- 回 None → diff fallback，且既有氣球被排除
- component 包含比對 + 刪除呼叫驗證
- 退化路徑 → warnings 含「style 未套用」
- 讀取全敗 → 空清單 + warnings
- 四類 SWError

實機驗證（SW 主機 deploy）列為 plan 收尾 task——COM 路徑可行性唯有實機能證明
（AutoBalloon 踩坑前例）。

## 程式碼位置

- 實作：`src/tools/annotation.py`（`_insert_balloon` 與輔助函式）
- 測試：`tests/test_balloon.py`
- Spec：`docs/specs/balloon.md`（greenfield，domain `balloon`）

## 相關

- Issue #14
- 踩坑紀錄：memory `project_autoballoon_com.md`
- 原始設計：`docs/plans/2026-04-02-insert-balloon-design.md`
