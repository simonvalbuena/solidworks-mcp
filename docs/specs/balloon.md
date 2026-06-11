---
domain: balloon
status: active
created: 2026-06-11
last_modified: 2026-06-11
---

# Balloon

組立件工程圖視圖上的氣球標註建立、資訊回報與零件過濾。

## Requirements

### R1: 在指定視圖插入氣球標註
- **Level**: MUST
- **Description**: 使用者可指定組立件工程圖上的視圖名稱，對該視圖的所有可見零件建立氣球標註（item number 文字）。

### R2: 回報已建立的氣球資訊
- **Level**: MUST
- **Description**: 操作成功後回傳每顆氣球的零件名稱與 item number 清單，caller 能得知哪些零件被標了什麼編號。

### R3: 既有氣球不計入本次結果
- **Level**: MUST
- **Description**: 視圖上原本已存在的氣球不得被誤報為本次建立，也不得被 component 過濾誤刪。

### R4: component 過濾
- **Level**: MUST
- **Description**: 使用者指定 component 時，僅保留零件名稱包含該字串的氣球，其餘本次建立的氣球被刪除。比對為包含比對（子字串、不分大小寫）。

### R5: style 與 auto_layout 參數生效
- **Level**: MUST
- **Description**: 主路徑下使用者指定的氣球樣式（circular/triangle/hexagon）與自動排列設定實際套用到建立的氣球。

### R6: COM API 退化與誠實告知
- **Level**: MUST
- **Description**: 主路徑建立失敗時自動退化到舊版 API；退化導致 style/auto_layout 未套用時，回傳須以 warnings 明確告知，不得無感吞掉功能差異。

### R7: 讀取失敗時的部分成功回報
- **Level**: MUST
- **Description**: 氣球已建立但資訊讀取失敗時，不拋錯誤；回傳空清單並以 warnings 說明「氣球已建立但無法讀取資訊」，若有指定 component 則一併說明過濾未執行。

### R8: 操作失敗回報
- **Level**: MUST
- **Description**: 沒有開啟的 Drawing、視圖不存在、無效 style、所有建立路徑全敗時，操作須失敗並回報帶上下文的明確錯誤。

## Scenarios

### S1: 成功插入並回報氣球資訊
- **Given**: 開啟組立件工程圖，視圖「工程視圖1」含 5 個零件
- **When**: 呼叫 `insert_balloon(view_name="工程視圖1")`
- **Then**: 回傳 `balloon_count: 5`，`balloons` 含 5 筆 `{component, item_number}`
- **Implements**: #R1, #R2

### S2: 主路徑回傳值不可用時仍能回報
- **Given**: 建立氣球的 COM 呼叫成功但回傳值為 None
- **When**: 呼叫 `insert_balloon(view_name="工程視圖1")`
- **Then**: 透過視圖 annotation 前後比對取得本次建立的氣球，`balloons` 仍有完整資訊
- **Implements**: #R2

### S3: 既有氣球不被誤報
- **Given**: 視圖上已有 2 顆舊氣球
- **When**: 呼叫 `insert_balloon` 新建 5 顆
- **Then**: `balloons` 僅含本次 5 顆，舊氣球不在清單也未被動
- **Implements**: #R3

### S4: component 過濾
- **Given**: 視圖含 bracket-1、shaft-1、pin-1 三個零件
- **When**: 呼叫 `insert_balloon(view_name="工程視圖1", component="bracket")`
- **Then**: 僅 bracket-1 的氣球保留，shaft-1 與 pin-1 的氣球被刪除，`balloon_count: 1`
- **Implements**: #R4

### S5: style 套用
- **Given**: 主路徑可用
- **When**: 呼叫 `insert_balloon(view_name="工程視圖1", style="triangle")`
- **Then**: 建立的氣球為三角形樣式，無 warnings
- **Implements**: #R5

### S6: 退化路徑誠實告知
- **Given**: 主路徑 COM 呼叫在 late-binding 下失敗
- **When**: 呼叫 `insert_balloon(view_name="工程視圖1", style="triangle")`
- **Then**: 改用舊版 API 建立氣球成功，回傳 warnings 含「style 未套用」字樣
- **Implements**: #R6

### S7: 讀取全敗的部分成功
- **Given**: 氣球建立成功，但回傳值與 annotation 比對皆無法取得 Note
- **When**: 呼叫 `insert_balloon(view_name="工程視圖1", component="bracket")`
- **Then**: 回傳 `balloon_count: 0`、空 `balloons`，warnings 說明氣球已建立但無法讀取、過濾未執行
- **Implements**: #R7

### S8: 視圖不存在
- **Given**: 工程圖上沒有名為「不存在的視圖」的視圖
- **When**: 呼叫 `insert_balloon(view_name="不存在的視圖")`
- **Then**: 拋出錯誤，訊息含視圖名稱
- **Implements**: #R8

### S9: 所有建立路徑全敗
- **Given**: 主路徑與退化路徑的 COM 呼叫皆失敗
- **When**: 呼叫 `insert_balloon(view_name="工程視圖1")`
- **Then**: 拋出錯誤，訊息含兩層失敗的上下文
- **Implements**: #R8

## Design Decisions

### D1: 主路徑採 AutoBalloon5 + Options 物件
- **Decision**: 建立氣球主路徑用 `IDrawingDoc::AutoBalloon5(AutoBalloonOptions)` 搭配 `IDrawingDoc::CreateAutoBalloonOptions` property 賦值，不用舊式 positional 參數呼叫
- **Rationale**: positional VARIANT_BOOL 在 pywin32 late-binding 下 DISP_E_TYPEMISMATCH（VARIANT 明確包裝也無效，踩坑紀錄）；property 賦值有 MathTransform ArrayData 成功前例。SW 2021 本機 API 文件查證：AutoBalloon5 唯一簽名是單一 AutoBalloonOptions 參數（4 月踩坑的「12 參數版」是錯誤呼叫方式）、`InsertAutoBalloon` 不存在於 SW 2021、兩個 API 皆掛 IDrawingDoc 非 Extension（PR #19 review 連帶查證修正）
- **Date**: 2026-06-11

### D2: 不採 EnsureDispatch early-binding
- **Decision**: 不用 typelib / gencache 產生 early-binding wrapper
- **Rationale**: 改變全局 dispatch 模式會波及其他 tools，離線 SW 主機上 gencache 行為不確定，影響面與風險最大
- **Date**: 2026-06-11

### D3: 退化不無感，warnings 誠實告知
- **Decision**: 退化到 `AutoBalloon()` 時以 warnings 告知 style/auto_layout 未套用，與 bom-table D5 的無感退化不同
- **Rationale**: bom-table 退化前後功能等價，無感合理；balloon 退化會丟失功能，無感等於欺騙 caller
- **Date**: 2026-06-11

### D4: 讀取走「回傳值優先、annotation diff 兜底」雙層
- **Decision**: 先解析 COM 回傳值，None 時用建立前後的視圖 Note 集合差集找出本次氣球
- **Rationale**: 回傳值在 late-binding 下不可靠（Issue #14 根因）；diff 不依賴回傳值且天然排除既有氣球（R3）
- **Date**: 2026-06-11

### D5: component 採包含比對（不分大小寫）
- **Decision**: 零件名過濾用子字串包含比對而非精確全名，且雙方轉小寫後比對
- **Rationale**: `Name2` 回傳帶 instance 後綴的全名（如 `bracket-1@assembly`），要求精確全名 caller 幾乎無法事先得知；接受 `bracket` 同時命中 `bracket-mount` 的代價。SW 零件名常為大寫而使用者輸入常為小寫，大小寫敏感會造成誤刪（PR #19 review #2 採納）
- **Date**: 2026-06-11

### D6: 讀取全敗回部分成功而非報錯
- **Decision**: 兩層讀取皆失敗時回空清單 + warnings，不拋 SWError
- **Rationale**: 氣球實際已建立在圖上，報錯會誘導 caller 重試造成重複氣球；「建立成功但讀不到」當失敗語意不誠實
- **Date**: 2026-06-11

## Pending Changes

<!-- Brownfield delta 放這裡，finish spec sync 時清除 -->
