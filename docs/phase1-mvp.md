# Phase 1 PRD — MVP（檔案操作 + 基本出圖）

## 目標

建立 MCP Server 基礎架構，實現從 .sldprt 手動指定視圖到產出工程圖 PDF 的完整流程。Phase 1 不含組立件分析，使用者需自行指定要出哪些視圖。

## 環境

- SolidWorks 2021，Windows 11（SW 主機，僅區網）
- Python 3.10+，pywin32 COM 橋接
- MCP Transport：Streamable HTTP，監聽區網 IP
- 截圖：小圖 base64 回傳，大檔（PDF）存 SMB 共享

## 出圖規格

- 投影法：第一角法
- 單位：mm
- 圖框：公司自訂模板（.slddrt / .drwdot）

## 架構

```
Claude Code（有網路電腦）    SW 主機（僅區網）
┌──────────────┐           ┌──────────────────┐
│ Claude Code  │◄── HTTP ──►│ MCP Server (Py)  │
│              │   區網     │    ↓ pywin32 COM  │
│              │           │ SolidWorks 2021   │
└──────────────┘           └──────────────────┘
                            │
                            ▼
                     SMB 共享資料夾
                   （PDF / 大圖輸出）
```

## 專案結構

```
solidwork-mcp/
├── server.py          # MCP server 入口（Streamable HTTP transport）
├── sw_connection.py   # SolidWorks COM 連線管理
├── tools/
│   ├── file_ops.py    # open_document, close_document, list_open_documents
│   ├── drawing.py     # create_drawing, insert_standard_views
│   ├── annotation.py  # insert_model_dimensions
│   └── export.py      # capture_drawing, save_as_pdf, save_drawing
├── config.py          # 區網 IP、SMB 路徑、模板路徑等設定
├── docs/
│   ├── phase1-mvp.md
│   ├── phase2-assembly-analysis.md
│   └── phase3-advanced.md
└── requirements.txt
```

## MCP Tools

### 1. open_document

開啟 SolidWorks 文件。

- 參數：
  - `file_path: str` — .sldprt 或 .sldasm 的完整路徑
- 回傳：文件名稱、文件類型（part/assembly）、開啟狀態
- COM API：`ISldWorks::OpenDoc6`
- 錯誤處理：檔案不存在、檔案格式不支援、SW 未啟動

### 2. close_document

關閉指定的 SolidWorks 文件。

- 參數：
  - `file_path: str` — 要關閉的文件路徑
- 回傳：關閉狀態
- COM API：`ISldWorks::CloseDoc`

### 3. list_open_documents

列出所有目前在 SolidWorks 中開啟的文件。

- 參數：無
- 回傳：文件列表（名稱、路徑、類型）
- COM API：`ISldWorks::GetDocuments`

### 4. create_drawing

建立新的 Drawing 文件，套用公司圖框模板。

- 參數：
  - `template_path: str`（選填）— 圖框模板路徑，預設使用 config 中的公司模板
  - `paper_size: str`（選填）— 圖紙大小，預設 A3
- 回傳：Drawing 文件名稱、建立狀態
- COM API：`ISldWorks::NewDocument` with drawing template
- 注意：需確認模板中的投影法設定為第一角法

### 5. insert_standard_views

在 Drawing 中插入第一角法標準視圖（前視圖、上視圖、右視圖 + 等角視圖）。

- 參數：
  - `source_doc: str` — 來源 part/assembly 文件路徑
  - `views: list[str]`（選填）— 要插入的視圖清單，預設 `["front", "top", "right", "isometric"]`
  - `scale: float`（選填）— 視圖比例，預設自動適配圖紙
- 回傳：插入的視圖名稱與位置
- COM API：
  - `IDrawingDoc::CreateDrawViewFromModelView3` — 插入各視圖
  - `IView::SetDisplayMode` — 設定顯示模式
- 視圖對應（第一角法）：
  - front → 主視圖（中央）
  - top → 俯視圖（主視圖下方）
  - right → 右視圖（主視圖左方）
  - isometric → 等角視圖（右上角）

### 6. insert_model_dimensions

匯入模型中的尺寸標註到 Drawing 視圖。

- 參數：
  - `view_name: str`（選填）— 指定視圖，預設全部視圖
  - `dimension_type: str`（選填）— 篩選尺寸類型（all/marked/reference），預設 all
- 回傳：匯入的尺寸數量、各視圖的尺寸清單
- COM API：`IDrawingDoc::InsertModelAnnotations3`
- 注意：匯入後尺寸位置可能重疊，需要手動或後續自動排列

### 7. capture_drawing

截取目前 Drawing 的畫面回傳給 AI 確認。

- 參數：
  - `output_mode: str`（選填）— `base64`（預設，直接回傳）或 `smb`（存到共享資料夾）
  - `resolution: str`（選填）— `low`（800px，base64 友善）/ `high`（2000px，存 SMB）
- 回傳：
  - base64 模式：MCP image content（PNG base64）
  - smb 模式：SMB 共享路徑
- COM API：`IModelDoc2::SaveBMP` 或 `IModelDocExtension::SaveAs`（PNG）
- 注意：base64 圖片控制在 1MB 以內，超過自動切換 SMB

### 8. save_as_pdf

將 Drawing 輸出為 PDF。

- 參數：
  - `output_path: str`（選填）— 輸出路徑，預設存到 SMB 共享資料夾，檔名同 Drawing
- 回傳：PDF 檔案路徑、檔案大小
- COM API：`IModelDocExtension::SaveAs` with PDF export options
- PDF 選項：含圖紙邊界、嵌入字型

### 9. save_drawing

儲存目前的 Drawing 文件為 .slddrw。

- 參數：
  - `file_path: str`（選填）— 另存路徑，預設覆蓋原檔
- 回傳：儲存路徑、儲存狀態
- COM API：`IModelDoc2::Save3` 或 `IModelDocExtension::SaveAs`

## 基礎建設

### sw_connection.py — COM 連線管理

```python
# 核心功能
- get_sw_app() → 取得或建立 SolidWorks COM 連線（Singleton）
- COM API：win32com.client.Dispatch("SldWorks.Application")
- 連線健康檢查：呼叫前確認 COM 物件仍有效
- 錯誤處理：SW 未啟動、COM 連線中斷、權限不足
```

### server.py — MCP Server 入口

```python
# 核心功能
- Streamable HTTP transport，監聽 config 中指定的區網 IP + port
- 註冊所有 Phase 1 tools
- 啟動時驗證 SW COM 連線
```

### config.py — 設定

```python
# 設定項目
- SW_HOST: str          # 監聽 IP（區網）
- SW_PORT: int          # 監聽 port（預設 8080）
- SMB_SHARE_PATH: str   # SMB 共享資料夾路徑（SW 主機端）
- TEMPLATE_PATH: str    # 公司圖框模板路徑
- DEFAULT_PAPER_SIZE: str  # 預設圖紙大小
- MAX_BASE64_SIZE: int  # base64 圖片大小上限（bytes）
```

## 離線依賴

所有套件需提前在有網路的電腦下載 wheel：

```bash
pip download -d ./wheels mcp[server] pywin32 Pillow
```

帶到 SW 主機安裝：

```bash
pip install --no-index --find-links=./wheels mcp[server] pywin32 Pillow
```

## Phase 1 使用情境

```
使用者：「幫我把 C:\parts\bracket.sldprt 出工程圖」

AI 執行流程：
1. open_document("C:\parts\bracket.sldprt")
2. create_drawing()  → 套用公司 A3 模板
3. insert_standard_views("C:\parts\bracket.sldprt")  → 前/上/右/等角
4. insert_model_dimensions()  → 匯入所有模型尺寸
5. capture_drawing(output_mode="base64")  → 截圖確認
6. [AI 看截圖，判斷是否需要調整]
7. save_as_pdf()  → 輸出 PDF 到 SMB
8. save_drawing()  → 儲存 .slddrw
9. close_document()
```

## 驗收標準

- [ ] MCP Server 可透過區網連線，Claude Code 能呼叫所有 9 個 tools
- [ ] 從 .sldprt 到 PDF 的完整流程可走通
- [ ] 截圖可正確回傳 base64 圖片，AI 可判讀
- [ ] 第一角法視圖配置正確（俯視在下、右視在左）
- [ ] 尺寸標註可匯入且數值正確
- [ ] 公司圖框模板正確套用
