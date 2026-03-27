# Phase 1 MVP 設計文件

## 設計決策摘要

| 項目 | 決策 | 理由 |
|------|------|------|
| MCP Server 寫法 | FastMCP + 純 type hints | 參數簡單，Phase 3 再局部加 Pydantic |
| COM 執行緒策略 | 專用 COM 執行緒 + queue | SW COM 物件生命週期跨 tool 呼叫，必須同一執行緒 |
| 截圖回傳 | 預設 base64，超過 1MB 自動降級 SMB | AI 不需操心 output_mode，內部自動處理 |
| Config 管理 | .env + python-dotenv | 設定與程式碼分離，避免意外 commit |
| 錯誤處理 | COM 層拋 exception → tool 層 catch 轉 MCP ToolError | 內部保留 stack trace 除錯，對外給 AI 可讀錯誤 |

## 架構

```
Claude Code（有網路電腦）         SW 主機（僅區網）
┌──────────────┐                ┌─────────────────────────────┐
│ Claude Code  │◄── HTTP ──────►│ server.py                   │
│              │   區網         │   FastMCP (Streamable HTTP) │
└──────────────┘                │         │                   │
                                │         ▼                   │
                                │   tool handlers             │
                                │   (async, event loop)       │
                                │         │                   │
                                │         ▼ asyncio queue     │
                                │   COM Worker Thread         │
                                │   (STA, CoInitialize)       │
                                │         │                   │
                                │         ▼ pywin32 COM       │
                                │   SolidWorks 2021           │
                                └─────────────────────────────┘
                                          │
                                          ▼
                                   SMB 共享資料夾
                                 （PDF / 大圖輸出）
```

## 專案結構

```
solidwork-mcp/
├── .env.example           # 設定範本（commit 進 repo）
├── .env                   # 實際設定（gitignore）
├── .gitignore
├── server.py              # MCP server 入口
├── sw_connection.py       # COM 連線管理 + COM Worker Thread
├── tools/
│   ├── __init__.py
│   ├── file_ops.py        # open_document, close_document, list_open_documents
│   ├── drawing.py         # create_drawing, insert_standard_views
│   ├── annotation.py      # insert_model_dimensions
│   └── export.py          # capture_drawing, save_as_pdf, save_drawing
├── config.py              # 從 .env 載入設定
├── docs/
│   ├── phase1-mvp.md
│   ├── phase2-assembly-analysis.md
│   ├── phase3-advanced.md
│   └── plans/
│       └── 2026-03-27-phase1-mvp-design.md
├── requirements.txt
└── wheels/                # 離線 wheel 檔（gitignore）
```

## 元件設計

### 1. config.py — 設定載入

從 .env 檔讀取所有設定，提供預設值。

```python
from dotenv import load_dotenv
import os

load_dotenv()

SW_HOST = os.getenv("SW_MCP_HOST", "0.0.0.0")
SW_PORT = int(os.getenv("SW_MCP_PORT", "8080"))
SMB_SHARE_PATH = os.getenv("SW_MCP_SMB_PATH", r"C:\mcp-share")
TEMPLATE_PATH = os.getenv("SW_MCP_TEMPLATE", "")
DEFAULT_PAPER_SIZE = os.getenv("SW_MCP_PAPER_SIZE", "A3")
MAX_BASE64_SIZE = int(os.getenv("SW_MCP_MAX_BASE64", str(1 * 1024 * 1024)))  # 1MB
```

`.env.example`：
```
SW_MCP_HOST=192.168.1.100
SW_MCP_PORT=8080
SW_MCP_SMB_PATH=C:\mcp-share
SW_MCP_TEMPLATE=C:\templates\company.drwdot
SW_MCP_PAPER_SIZE=A3
SW_MCP_MAX_BASE64=1048576
```

### 2. sw_connection.py — COM Worker Thread

核心元件。啟動一個專用執行緒持有 SolidWorks COM 連線，所有 COM 操作透過 queue 派發。

```
啟動流程：
1. 建立 threading.Thread (daemon=True)
2. Thread 內部：pythoncom.CoInitialize() → STA
3. Thread 內部：win32com.client.Dispatch("SldWorks.Application")
4. Thread 進入迴圈，從 queue 取 callable 執行並回傳結果

呼叫流程（從 async tool handler）：
1. tool handler 將 callable 放入 queue
2. await asyncio.get_event_loop().run_in_executor(None, future.result)
3. COM Worker Thread 執行 callable，結果透過 concurrent.futures.Future 回傳
```

介面設計：
```python
class SWConnection:
    """SolidWorks COM 連線管理（Singleton）"""

    _instance = None

    def start(self) -> None:
        """啟動 COM Worker Thread"""

    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """將 COM 操作派發到 Worker Thread 執行"""
        # 包裝成 Future，放入 queue，await 結果

    def get_app(self) -> Any:
        """取得 ISldWorks COM 物件（僅在 Worker Thread 內呼叫）"""

    def get_active_doc(self) -> Any:
        """取得目前活動文件（僅在 Worker Thread 內呼叫）"""

    def shutdown(self) -> None:
        """關閉 COM 連線與 Worker Thread"""
```

錯誤處理：
- COM 物件失效 → 嘗試重新連線一次，失敗拋 `SWConnectionError`
- SW 未啟動 → 拋 `SWNotRunningError`
- Thread 卡死（COM 呼叫逾時 30 秒） → 拋 `SWTimeoutError`

### 3. server.py — MCP Server 入口

```python
from mcp.server.fastmcp import FastMCP
from sw_connection import SWConnection

mcp = FastMCP("solidworks")
sw = SWConnection()

# 註冊 tools（從各 tools/ 模組 import）
from tools.file_ops import register_tools as register_file_ops
from tools.drawing import register_tools as register_drawing
from tools.annotation import register_tools as register_annotation
from tools.export import register_tools as register_export

register_file_ops(mcp, sw)
register_drawing(mcp, sw)
register_annotation(mcp, sw)
register_export(mcp, sw)

# 啟動
if __name__ == "__main__":
    sw.start()
    mcp.run(transport="streamable-http", host=config.SW_HOST, port=config.SW_PORT)
```

### 4. Tool 註冊模式

每個 tools/ 模組匯出 `register_tools(mcp, sw)` 函式，將 tool handler 註冊到 FastMCP。tool handler 內部透過 `sw.execute()` 派發 COM 操作。

```python
# tools/file_ops.py 範例

def register_tools(mcp: FastMCP, sw: SWConnection):

    @mcp.tool()
    async def open_document(file_path: str) -> str:
        """開啟 SolidWorks 文件（.sldprt 或 .sldasm）。
        回傳文件名稱與類型。"""
        try:
            result = await sw.execute(_open_document, file_path)
            return result
        except SWNotRunningError:
            raise ToolError("SolidWorks 未啟動，請先啟動 SolidWorks")
        except FileNotFoundError:
            raise ToolError(f"檔案不存在: {file_path}")
        except SWError as e:
            raise ToolError(f"open_document 失敗: {e}")

def _open_document(file_path: str) -> str:
    """在 COM Worker Thread 中執行的實際邏輯"""
    import os
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)

    sw_app = SWConnection.get_instance().get_app()
    # ... COM 呼叫 ...
```

### 5. 截圖回傳邏輯（export.py）

```
capture_drawing 流程：
1. 呼叫 COM 截圖 → 存到暫存 PNG
2. 檢查檔案大小
3. if 大小 <= MAX_BASE64_SIZE:
     讀取 PNG → base64 encode → 回傳 MCP ImageContent
   else:
     複製到 SMB_SHARE_PATH → 回傳 SMB 路徑字串
4. 清理暫存檔

output_mode 參數：
- "auto"（預設）→ 上述自動判斷
- "base64" → 強制 base64（超過 1MB 則先壓縮降解析度）
- "smb" → 強制存 SMB
```

## 資料流

### 完整出圖流程

```
Claude Code                          MCP Server                    SolidWorks
    │                                    │                             │
    ├─ open_document(bracket.sldprt) ──►│                             │
    │                                    ├─ sw.execute(_open) ───────►│
    │                                    │◄─── doc info ──────────────┤
    │◄── "bracket.sldprt (part)" ───────┤                             │
    │                                    │                             │
    ├─ create_drawing() ───────────────►│                             │
    │                                    ├─ sw.execute(_create) ─────►│
    │                                    │◄─── drawing doc ───────────┤
    │◄── "Drawing1 created (A3)" ──────┤                             │
    │                                    │                             │
    ├─ insert_standard_views(...) ─────►│                             │
    │                                    ├─ sw.execute(_insert) ─────►│
    │                                    │◄─── view info ─────────────┤
    │◄── "4 views inserted" ───────────┤                             │
    │                                    │                             │
    ├─ insert_model_dimensions() ──────►│                             │
    │                                    ├─ sw.execute(_dims) ───────►│
    │                                    │◄─── dim count ─────────────┤
    │◄── "23 dimensions imported" ─────┤                             │
    │                                    │                             │
    ├─ capture_drawing() ─────────────►│                             │
    │                                    ├─ sw.execute(_capture) ────►│
    │                                    │◄─── PNG bytes ─────────────┤
    │                                    ├─ size check ── < 1MB ─►base64
    │◄── [ImageContent: base64 PNG] ───┤                             │
    │                                    │                             │
    │  (AI 看圖確認 OK)                  │                             │
    │                                    │                             │
    ├─ save_as_pdf() ─────────────────►│                             │
    │                                    ├─ sw.execute(_pdf) ────────►│
    │                                    │◄─── saved ─────────────────┤
    │◄── "PDF saved: \\SW-PC\share\b.pdf"┤                           │
    │                                    │                             │
    ├─ save_drawing() ────────────────►│                             │
    │◄── "Saved: bracket.slddrw" ──────┤                             │
```

## 離線依賴

```bash
# 有網路的電腦上執行
pip download -d ./wheels mcp[server] pywin32 Pillow python-dotenv

# SW 主機上執行
pip install --no-index --find-links=./wheels mcp[server] pywin32 Pillow python-dotenv
```

完整 requirements.txt：
```
mcp[server]
pywin32
Pillow
python-dotenv
```

## 測試策略

Phase 1 的測試分兩層：

1. **無 SW 的單元測試**（開發機可跑）
   - config 載入測試
   - COM Worker Thread 的 queue 機制（mock COM 物件）
   - 截圖大小判斷邏輯
   - 錯誤轉換邏輯（exception → ToolError）

2. **有 SW 的整合測試**（SW 主機上跑）
   - open_document → 開啟測試零件
   - create_drawing → insert_standard_views → capture_drawing → 確認截圖
   - 完整流程 e2e
   - 需準備測試用 .sldprt
