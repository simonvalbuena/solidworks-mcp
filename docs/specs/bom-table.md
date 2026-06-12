---
domain: bom-table
status: active
created: 2026-05-22
last_modified: 2026-06-12
---

# BOM Table

組立件工程圖的 BOM 表（Bill of Materials）插入與管理。

## Requirements

### R1: 插入 BOM 表到指定視圖
- **Level**: MUST
- **Description**: 使用者可指定組立件工程圖上的視圖名稱與放置座標（mm），於該位置插入 BOM 表。BOM 從該視圖所參考的組立件抽取。

### R2: 預設使用 Top-Level Only BOM 類型
- **Level**: MUST
- **Description**: MVP 階段僅支援 Top-Level Only 類型；不展開子組立件、不展平到零件層。

### R3: 預設使用 SolidWorks 內建範本
- **Level**: MUST
- **Description**: MVP 階段不接受自訂範本路徑，一律使用 SolidWorks 內建 `bom-standard.sldbomtbl`，包含 Item No / Part Number / Description / Quantity 欄位。

### R4: 視圖驗證
- **Level**: MUST
- **Description**: 若指定的視圖名稱不存在，或視圖所參考的文件非組立件，操作須失敗並回報明確錯誤。

### R5: COM API 版本退化
- **Level**: SHOULD
- **Description**: 主路徑使用較新 API 版本；若因 pywin32 late-binding 參數傳遞問題失敗，自動退化到較舊 API 版本，提升可用性。

### R6: 操作結果回報
- **Level**: MUST
- **Description**: 成功時回傳狀態與插入的 BOM 表名稱；失敗時拋出帶上下文的錯誤。

## Scenarios

### S1: 成功插入 Top-Level BOM
- **Given**: 開啟一份組立件工程圖，內含名為「工程視圖1」的組立件視圖
- **When**: 使用者呼叫 `insert_bom_table(view_name="工程視圖1", x=250, y=180)`
- **Then**: BOM 表插入於座標 (250mm, 180mm)，回傳 `{status: "done", table_name: "Bill of Materials1"}`
- **Implements**: #R1, #R2, #R3, #R6

### S2: 視圖名稱不存在
- **Given**: 工程圖上沒有名為「不存在的視圖」的視圖
- **When**: 使用者呼叫 `insert_bom_table(view_name="不存在的視圖", x=0, y=0)`
- **Then**: 拋出錯誤，訊息含 "View '不存在的視圖' not found"
- **Implements**: #R4, #R6

### S3: 視圖參考非組立件
- **Given**: 工程圖上的視圖「工程視圖1」參考的是零件而非組立件
- **When**: 使用者呼叫 `insert_bom_table(view_name="工程視圖1", x=0, y=0)`
- **Then**: 拋出錯誤，訊息含 "does not reference an assembly"
- **Implements**: #R4, #R6

### S4: COM API 退化
- **Given**: SolidWorks 環境中較新版 BOM 插入 API 在 pywin32 late-binding 下失敗
- **When**: 使用者呼叫 `insert_bom_table` 觸發退化路徑
- **Then**: 自動改用較舊版 API 插入 BOM，使用者無需察覺差異
- **Implements**: #R5

### S5: 非 drawing 文件
- **Given**: Active 文件是零件或組立件而非工程圖
- **When**: 使用者呼叫 `insert_bom_table(...)`
- **Then**: 拋出錯誤，訊息含 "Active document is not a drawing"
- **Implements**: #R4, #R6

## Design Decisions

### D1: MVP 採極簡範圍
- **Decision**: 第一版只做 Top-Level Only + SW 內建範本 + X/Y 座標，不支援 Parts Only / Indented / 自訂範本 / AnchorType。實作對兩版 API 的 AnchorType 參數固定傳 0（無 anchor，以 X/Y 座標放置）。
- **Rationale**: 與 `insert_detail_view` 只做 circle 的 YAGNI 策略一致；BomType 與 TableTemplate 在 pywin32 late-binding 下踩坑風險高，先用最小可行集合驗證 API 通路。AnchorType=0 讓放置位置完全由座標決定，與「不支援 AnchorType」的範圍宣告一致。
- **Date**: 2026-05-22（2026-06-12 補 AnchorType=0 註記，源自 Issue #16 finding 5）

### D2: 座標單位採 mm
- **Decision**: tool 介面接受 mm，內部轉 meters 傳給 COM
- **Rationale**: 與 `add_dimension` / `insert_balloon` 既有 tool 一致，使用者不需切換思考單位
- **Date**: 2026-05-22

### D3: 視圖名稱必填，不自動偵測
- **Decision**: `view_name` 必填，不做「自動找第一個組立件視圖」
- **Rationale**: 一張工程圖常有多個組立件視圖（不同方向），自動選擇容易選錯；保持 caller 明確指定的責任歸屬
- **Date**: 2026-05-22

### D4: 回傳只含 status + table_name
- **Decision**: 不在 tool 回傳中夾帶 BOM 內容（rows）
- **Rationale**: BOM 內容讀取屬於另一個職責（未來 `read_bom_table`），YAGNI；同時避免 pywin32 SAFEARRAY 遍歷踩坑風險
- **Date**: 2026-05-22

### D5: 主+退化雙路徑 COM 策略
- **Decision**: 先試 `InsertBomTable4`，失敗或回 None 則試 `InsertBomTable3`
- **Rationale**: 沿用 `insert_balloon` 的踩坑教訓（AutoBalloon5 失敗 → AutoBalloon），提升 pywin32 late-binding 環境下的成功率
- **Date**: 2026-05-22

## Pending Changes

<!-- Brownfield delta 放這裡，finish spec sync 時清除 -->
