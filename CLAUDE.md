# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

SolidWorks MCP Server — 透過 pywin32 COM 橋接 SolidWorks 2021，讓 Claude Code 從區網呼叫 MCP tools 操作 SolidWorks 出工程圖。

## Commands

```bash
# 測試（全部）
.venv/Scripts/pytest tests/ -v

# 測試（單一檔案）
.venv/Scripts/pytest tests/test_sw_connection.py -v

# 測試（單一 test）
.venv/Scripts/pytest tests/test_sw_connection.py::test_execute_returns_result -v

# 啟動 server（需在有 SolidWorks 的主機上）
.venv/Scripts/python src/server.py
```

## Architecture

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

核心設計約束：SolidWorks COM 必須在同一個 STA 執行緒操作。所有 tool handler 是 async，透過 `SWConnection.execute()` 將 callable 派發到專用的 COM Worker Thread，用 `concurrent.futures.Future` 橋接 async/sync。

### Tool 註冊模式

每個 `src/tools/*.py` 匯出 `register_tools(mcp, sw)`，在 `server.py` 啟動時統一註冊。Tool handler 內部透過 `sw.execute(_com_function, args)` 派發 COM 操作，catch `SWError` 轉成 `ToolError`。

### 截圖回傳機制

`capture_drawing` 預設 auto 模式：截圖 <= 1MB 回傳 base64 ImageContent，超過存 SMB 共享資料夾回傳路徑。可用 `output_mode` 強制指定。

## Configuration

設定從 `.env` 載入（python-dotenv），範本見 `.env.example`。`conftest.py` 將 `src/` 加入 sys.path。

## Development Phases

- Phase 1 (current): 9 tools — 檔案操作 + 基本出圖 + 標註 + 輸出
- Phase 2: 組立件分析 — 配合關係讀取 + 規則引擎自動視圖判斷
- Phase 3: 進階視圖 — 剖面圖、局部放大、氣球標註、BOM 表

詳見 `docs/phase*.md` 和 `docs/plans/`。

## Key Constraints

- COM 操作逾時 30 秒（`sw_connection.COM_TIMEOUT`）
- SWConnection 是 Singleton，測試時需 reset `_instance` 並 mock `pythoncom` + `win32com.client`
- SW 主機僅區網，所有依賴需離線安裝（`pip download -d ./wheels`）
- 出圖規格：第一角法、mm、公司圖框模板
