![solidworks-mcp banner](assets/banner-solidworks-mcp.svg)

# solidworks-mcp

[![CI](https://github.com/haunchen/solidworks-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/haunchen/solidworks-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

[English](README.md) | 繁體中文

讓 Claude——或任何支援 Streamable HTTP 的 MCP client——操作 **SolidWorks 2021** 出工程圖的 MCP server，透過 pywin32 COM API，從區網即可呼叫。

開零件、建第一角法標準視圖、加尺寸標註與氣球、插 BOM 表、輸出 PDF — 全部在 Claude 對話中完成。

## 功能

**21 個 tools**，分 5 類：

| 類別 | Tools |
|------|-------|
| 檔案操作（3） | 開啟／關閉／列出文件 |
| 出圖視圖（6） | 從模板建工程圖、標準視圖、第一角法三視圖、剖面圖、局部放大圖、自訂角度視圖 |
| 標註（6） | 匯入模型尺寸、邊線查詢、加尺寸（線性／直徑／半徑／角度）、自動參考尺寸、氣球標註、BOM 表 |
| 輸出（4） | 工程圖截圖（JPEG）、模型多角度截圖、輸出 PDF、儲存工程圖 |
| 組立件分析（2） | 讀取配合關係、讀取特徵樹 |

完整清單見下方 [Tool 參考](#tool-參考)。

## 架構

```
Claude Code ◄── Streamable HTTP ──► src/server.py (FastMCP)
                                         │
                                    async tool handlers
                                         │
                                    sw.execute(func)  ← asyncio + Future
                                         │
                                    COM Worker Thread (STA, singleton)
                                         │
                                    pywin32 COM → SolidWorks 2021
```

SolidWorks COM 必須在同一個 STA 執行緒操作。所有 tool handler 是 async，將 COM callable 派發到專用的 worker thread，用 `concurrent.futures.Future` 橋接回 async。

## 系統需求

- Windows 10/11 主機，已安裝 **SolidWorks 2021**
- **Python 3.10+**
- 支援 Streamable HTTP 的 MCP client（如 Claude Code、Codex CLI、Gemini CLI、Cursor，見[相容的 MCP clients](#相容的-mcp-clients)），與主機在同一個**信任區網**
- 選用：工程圖模板（`.drwdot`）與 SMB 共享資料夾（放大型截圖／PDF）

> 僅在 SolidWorks 2021 實測過。COM API 大致穩定，其他版本可能可用但不保證。

## 快速開始

### 1. 安裝（在 SolidWorks 主機上）

```powershell
git clone https://github.com/haunchen/solidworks-mcp.git
cd solidworks-mcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

<details>
<summary>離線安裝（主機無網路時）</summary>

在有網路的機器上：

```powershell
pip download -d ./wheels -r requirements.txt
```

把 repo（含 `wheels/`）複製到 SolidWorks 主機後：

```powershell
.venv\Scripts\pip install --no-index --find-links wheels -r requirements.txt
```

</details>

### 2. 設定

```powershell
Copy-Item .env.example .env
# 編輯 .env — 變數說明見下方「設定」
```

### 3. 啟動

```powershell
.venv\Scripts\python src\server.py
```

### 4. 從 Claude Code 連線（client 端）

```bash
claude mcp add --transport http solidworks http://<sw-host>:8080/mcp
```

接著直接對 Claude 說：「開啟 bracket.sldprt，建第一角法三視圖加尺寸標註，輸出 PDF。」

## 相容的 MCP clients

任何支援 **Streamable HTTP**、且在你的機器（或區網內）執行的 MCP client 都能連線。已查證的例子：

| Client | 設定 |
|--------|------|
| Claude Code | `claude mcp add --transport http solidworks http://<sw-host>:8080/mcp` |
| Codex CLI | `config.toml`：`[mcp_servers.solidworks]`<br>`url = "http://<sw-host>:8080/mcp"` |
| Gemini CLI | `gemini mcp add --transport http solidworks http://<sw-host>:8080/mcp` |
| Cursor | `mcp.json`：`{ "mcpServers": { "solidworks": { "url": "http://<sw-host>:8080/mcp" } } }` |
| VS Code Copilot | `.vscode/mcp.json`：`{ "servers": { "solidworks": { "type": "http", "url": "http://<sw-host>:8080/mcp" } } }` |
| Cline | `cline_mcp_settings.json`：`{ "mcpServers": { "solidworks": { "type": "streamableHttp", "url": "http://<sw-host>:8080/mcp" } } }` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json`：`{ "mcpServers": { "solidworks": { "serverUrl": "http://<sw-host>:8080/mcp" } } }` |
| Continue / Zed / JetBrains AI Assistant / opencode / Goose | 見各自的 MCP 文件（`url` / `uri` 欄位） |

Agent 框架也可以：OpenAI Agents SDK（`MCPServerStreamableHttp`）、LangChain（`langchain-mcp-adapters`）。

> 雲端代理連線的 connector 連不到僅限區網的 server：Claude Desktop / claude.ai 的 custom connectors 與 Anthropic Messages API 的 MCP connector 都是從廠商雲端發起連線——而這正是信任區網安全模型要擋下的。

## 設定

所有設定從 `.env` 載入（範本見 `.env.example`）：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `SW_MCP_HOST` | `0.0.0.0` | Server 綁定位址 |
| `SW_MCP_PORT` | `8080` | Server 埠號 |
| `SW_MCP_SMB_PATH` | `C:\mcp-share` | Server 端資料夾，放超過 base64 上限的截圖／PDF |
| `SW_MCP_SMB_CLIENT_PATH` | （空） | 同一個資料夾在 client 端看到的路徑（如 `U:\mcp-share`），用於改寫回傳路徑 |
| `SW_MCP_TEMPLATE` | （空） | `create_drawing` 使用的工程圖模板（`.drwdot`） |
| `SW_MCP_PAPER_SIZE` | `A3` | 預設圖紙大小 |
| `SW_MCP_MAX_BASE64` | `1048576` | 截圖以 base64 內嵌回傳的大小上限（bytes），超過則存共享資料夾 |

## 安全性

**本 server 沒有任何認證機制。**能連到這個埠的人就能操作你的 SolidWorks 並透過它讀寫檔案。

- 只部署在**信任的區網** — 絕不把埠暴露到公網
- `SW_MCP_HOST` 建議綁定特定區網介面，不要用 `0.0.0.0`
- 可加作業系統防火牆規則，限制只有已知 client IP 能連

## Tool 參考

### 檔案操作

| Tool | 說明 |
|------|------|
| `open_document` | 開啟 SolidWorks 文件（`.sldprt` / `.sldasm` / `.slddrw`） |
| `close_document` | 關閉指定文件 |
| `list_open_documents` | 列出所有目前開啟的文件 |

### 出圖視圖

| Tool | 說明 |
|------|------|
| `create_drawing` | 從模板建立新工程圖文件 |
| `insert_standard_views` | 插入獨立標準視圖（無投影關聯、可自訂比例） |
| `insert_standard_views_aligned` | 第一角法自動建立前／上／右三視圖（投影關聯、自動縮放） |
| `insert_section_view` | 在父視圖上建立剖面圖 |
| `insert_detail_view` | 在父視圖上建立局部放大圖 |
| `insert_custom_view` | 插入具名視角或任意 XYZ 旋轉角度視圖 |

### 標註

| Tool | 說明 |
|------|------|
| `insert_model_dimensions` | 匯入模型尺寸到工程圖視圖 |
| `probe_drawing_edges` | 查詢視圖可見邊線（index／類型／mm 座標），供標尺寸前定位 |
| `add_dimension` | 對查詢到的邊線加線性／直徑／半徑／角度尺寸 |
| `auto_add_reference_dimensions` | 自動加參考尺寸（外形 + 圓形），適用無參數化尺寸的幾何 |
| `insert_balloon` | 在組立件工程圖視圖插入氣球標註（可指定零件過濾） |
| `insert_bom_table` | 插入 BOM 表（Top-Level Only） |

### 輸出

| Tool | 說明 |
|------|------|
| `capture_drawing` | 截取目前工程圖為 JPEG（base64 內嵌或共享資料夾路徑） |
| `capture_view` | 從 3D 模型截取多角度截圖 |
| `save_as_pdf` | 將目前工程圖輸出為 PDF |
| `save_drawing` | 儲存目前工程圖（`.slddrw`） |

### 組立件分析

| Tool | 說明 |
|------|------|
| `read_assembly_mates` | 讀取組立件中指定零件的所有配合關係 |
| `get_feature_tree` | 讀取文件的特徵樹 |

## 開發

```powershell
# 跑全部測試（不需要 SolidWorks — COM 層全部 mock）
.venv\Scripts\pytest tests/ -v
```

行為契約在 `docs/specs/` — `status: active` 的 spec 描述已出貨功能的預期行為。設計歷程在 `docs/plans/`。

開發環境設定與 PR 規範見 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 授權

[MIT](LICENSE)
