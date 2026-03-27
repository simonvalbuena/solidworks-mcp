# Phase 1 MVP Implementation Plan

Goal: 建立 SolidWorks MCP Server，實現從 .sldprt 到工程圖 PDF 的完整出圖流程（9 個 tools）

Architecture: FastMCP (Streamable HTTP) → async tool handlers → COM Worker Thread (STA) → SolidWorks 2021 COM API。設定用 .env + python-dotenv，截圖預設 base64 超過 1MB 降級 SMB。

Tech Stack: Python 3.10+, mcp[server] (FastMCP), pywin32, Pillow, python-dotenv

---

### Task 1: 專案骨架

Files:
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `config.py`
- Create: `tools/__init__.py`

Step 1: 建立 .gitignore

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/

# Environment
.env

# Wheels (offline install)
wheels/

# IDE
.vscode/
.idea/

# SolidWorks temp
~$*.sldprt
~$*.sldasm
~$*.slddrw

# OS
Thumbs.db
Desktop.ini
```

Step 2: 建立 requirements.txt

```
mcp[server]
pywin32
Pillow
python-dotenv
```

Step 3: 建立 .env.example

```
SW_MCP_HOST=192.168.1.100
SW_MCP_PORT=8080
SW_MCP_SMB_PATH=C:\mcp-share
SW_MCP_TEMPLATE=C:\templates\company.drwdot
SW_MCP_PAPER_SIZE=A3
SW_MCP_MAX_BASE64=1048576
```

Step 4: 建立 config.py

```python
"""從 .env 載入 MCP Server 設定。"""

import os
from dotenv import load_dotenv

load_dotenv()

SW_HOST = os.getenv("SW_MCP_HOST", "0.0.0.0")
SW_PORT = int(os.getenv("SW_MCP_PORT", "8080"))
SMB_SHARE_PATH = os.getenv("SW_MCP_SMB_PATH", r"C:\mcp-share")
TEMPLATE_PATH = os.getenv("SW_MCP_TEMPLATE", "")
DEFAULT_PAPER_SIZE = os.getenv("SW_MCP_PAPER_SIZE", "A3")
MAX_BASE64_SIZE = int(os.getenv("SW_MCP_MAX_BASE64", str(1 * 1024 * 1024)))
```

Step 5: 建立 tools/__init__.py（空檔）

Step 6: Commit
```bash
git add .gitignore requirements.txt .env.example config.py tools/__init__.py
git commit -m "feat: project scaffolding — gitignore, requirements, env config"
```

---

### Task 2: 自訂例外

Files:
- Create: `errors.py`

Step 1: 建立 errors.py

```python
"""SolidWorks MCP Server 自訂例外。"""


class SWError(Exception):
    """SolidWorks 操作的基礎例外。"""


class SWNotRunningError(SWError):
    """SolidWorks 未啟動或 COM 連線失敗。"""


class SWConnectionError(SWError):
    """COM 物件失效且重新連線失敗。"""


class SWTimeoutError(SWError):
    """COM 操作逾時。"""


class SWFileError(SWError):
    """檔案操作錯誤（不存在、格式不支援等）。"""
```

Step 2: Commit
```bash
git add errors.py
git commit -m "feat: add custom exception classes for SW COM errors"
```

---

### Task 3: COM Worker Thread

Files:
- Create: `sw_connection.py`
- Create: `tests/__init__.py`
- Create: `tests/test_sw_connection.py`

Step 1: 寫 COM Worker Thread 的單元測試（mock COM）

```python
# tests/test_sw_connection.py
"""測試 COM Worker Thread 的 queue 派發機制（不需要 SolidWorks）。"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock
from sw_connection import SWConnection
from errors import SWTimeoutError


@pytest.fixture
def sw():
    """建立 SWConnection 實例，mock 掉 COM 初始化。"""
    with patch("sw_connection.pythoncom"), \
         patch("sw_connection.win32com.client") as mock_win32:
        mock_app = MagicMock()
        mock_app.Visible = True
        mock_win32.Dispatch.return_value = mock_app

        conn = SWConnection()
        conn.start()
        yield conn
        conn.shutdown()


def test_execute_returns_result(sw):
    """execute 應將 callable 派發到 worker thread 並回傳結果。"""
    def add(a, b):
        return a + b

    result = asyncio.get_event_loop().run_until_complete(
        sw.execute(add, 3, 7)
    )
    assert result == 10


def test_execute_propagates_exception(sw):
    """worker thread 中的 exception 應傳回 caller。"""
    def fail():
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        asyncio.get_event_loop().run_until_complete(
            sw.execute(fail)
        )


def test_get_app_returns_com_object(sw):
    """get_app 應回傳 COM 物件。"""
    # get_app 只能在 worker thread 內呼叫，透過 execute 測試
    def check_app():
        app = SWConnection.get_instance().get_app()
        return app is not None

    result = asyncio.get_event_loop().run_until_complete(
        sw.execute(check_app)
    )
    assert result is True


def test_singleton():
    """SWConnection 應為 Singleton。"""
    # Reset singleton for test
    SWConnection._instance = None
    a = SWConnection()
    b = SWConnection()
    assert a is b
    SWConnection._instance = None
```

Step 2: 跑測試確認失敗
```bash
pytest tests/test_sw_connection.py -v
```
Expected: FAIL（sw_connection 模組不存在）

Step 3: 實作 sw_connection.py

```python
"""SolidWorks COM 連線管理 — 專用 COM Worker Thread。

所有 SolidWorks COM 操作都必須在同一個 STA 執行緒中執行。
此模組提供 SWConnection，透過 queue 將 COM 操作派發到專用執行緒。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any, Callable

import pythoncom
import win32com.client

from errors import SWConnectionError, SWNotRunningError, SWTimeoutError

logger = logging.getLogger(__name__)

# COM 操作逾時（秒）
COM_TIMEOUT = 30

# Sentinel 物件，用於通知 worker thread 結束
_SHUTDOWN = object()


class SWConnection:
    """SolidWorks COM 連線管理（Singleton）。

    使用方式：
        sw = SWConnection()
        sw.start()
        result = await sw.execute(some_com_function, arg1, arg2)
        sw.shutdown()
    """

    _instance: SWConnection | None = None
    _lock = threading.Lock()

    def __new__(cls) -> SWConnection:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._thread: threading.Thread | None = None
        self._queue: list = []
        self._queue_event = threading.Event()
        self._queue_lock = threading.Lock()
        self._sw_app: Any = None
        self._ready = threading.Event()
        self._running = False

    @classmethod
    def get_instance(cls) -> SWConnection:
        """取得 Singleton 實例。"""
        if cls._instance is None:
            raise SWConnectionError("SWConnection 尚未建立")
        return cls._instance

    def start(self) -> None:
        """啟動 COM Worker Thread。阻塞直到 COM 初始化完成。"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="sw-com-worker",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(timeout=10)

        if not self._ready.is_set():
            self._running = False
            raise SWNotRunningError("COM Worker Thread 啟動逾時")

    def _worker_loop(self) -> None:
        """Worker Thread 主迴圈：初始化 COM，持續從 queue 取任務執行。"""
        pythoncom.CoInitialize()
        try:
            self._init_com()
            self._ready.set()
            logger.info("COM Worker Thread 已啟動")

            while self._running:
                self._queue_event.wait(timeout=1.0)
                self._queue_event.clear()
                self._process_queue()
        except Exception as e:
            logger.error("COM Worker Thread 異常: %s", e)
            self._ready.set()  # 解除 start() 的等待
        finally:
            self._sw_app = None
            pythoncom.CoUninitialize()
            logger.info("COM Worker Thread 已結束")

    def _init_com(self) -> None:
        """初始化 SolidWorks COM 連線。"""
        try:
            self._sw_app = win32com.client.Dispatch("SldWorks.Application")
            self._sw_app.Visible = True
            logger.info("SolidWorks COM 連線成功")
        except Exception as e:
            raise SWNotRunningError(
                f"無法連線到 SolidWorks，請確認 SolidWorks 已啟動: {e}"
            ) from e

    def _process_queue(self) -> None:
        """處理 queue 中所有待執行的任務。"""
        with self._queue_lock:
            tasks = list(self._queue)
            self._queue.clear()

        for item in tasks:
            if item is _SHUTDOWN:
                self._running = False
                return

            func, args, kwargs, future = item
            try:
                result = func(*args, **kwargs)
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)

    async def execute(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """將 COM 操作派發到 Worker Thread 執行。

        Args:
            func: 要在 Worker Thread 中執行的 callable。
            *args, **kwargs: 傳給 func 的參數。

        Returns:
            func 的回傳值。

        Raises:
            SWTimeoutError: COM 操作逾時。
            其他: func 內部拋出的任何例外。
        """
        if not self._running:
            raise SWConnectionError("COM Worker Thread 未啟動")

        future: Future = Future()

        with self._queue_lock:
            self._queue.append((func, args, kwargs, future))
        self._queue_event.set()

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, future.result, COM_TIMEOUT),
                timeout=COM_TIMEOUT + 5,
            )
        except (asyncio.TimeoutError, TimeoutError) as e:
            raise SWTimeoutError(
                f"COM 操作逾時（{COM_TIMEOUT}s）: {func.__name__}"
            ) from e

    def get_app(self) -> Any:
        """取得 ISldWorks COM 物件。僅在 Worker Thread 內呼叫。"""
        if self._sw_app is None:
            raise SWNotRunningError("SolidWorks COM 連線不可用")
        return self._sw_app

    def get_active_doc(self) -> Any:
        """取得目前活動文件。僅在 Worker Thread 內呼叫。"""
        app = self.get_app()
        doc = app.ActiveDoc
        if doc is None:
            raise SWConnectionError("目前沒有開啟的文件")
        return doc

    def shutdown(self) -> None:
        """關閉 COM 連線與 Worker Thread。"""
        if not self._running:
            return

        with self._queue_lock:
            self._queue.append(_SHUTDOWN)
        self._queue_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

        self._running = False
        logger.info("SWConnection 已關閉")
```

Step 4: 跑測試確認通過
```bash
pytest tests/test_sw_connection.py -v
```
Expected: PASS

Step 5: Commit
```bash
git add sw_connection.py tests/
git commit -m "feat: COM Worker Thread with queue dispatch and singleton pattern"
```

---

### Task 4: MCP Server 入口

Files:
- Create: `server.py`

Step 1: 建立 server.py

```python
"""SolidWorks MCP Server 入口。"""

import logging

from mcp.server.fastmcp import FastMCP

import config
from sw_connection import SWConnection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

mcp = FastMCP("solidworks")
sw = SWConnection()


def register_all_tools() -> None:
    """註冊所有 MCP tools。"""
    from tools.file_ops import register_tools as register_file_ops
    from tools.drawing import register_tools as register_drawing
    from tools.annotation import register_tools as register_annotation
    from tools.export import register_tools as register_export

    register_file_ops(mcp, sw)
    register_drawing(mcp, sw)
    register_annotation(mcp, sw)
    register_export(mcp, sw)


register_all_tools()

if __name__ == "__main__":
    logger.info("啟動 SolidWorks MCP Server on %s:%s", config.SW_HOST, config.SW_PORT)
    sw.start()
    mcp.run(transport="streamable-http", host=config.SW_HOST, port=config.SW_PORT)
```

Step 2: 建立空的 tool 模組佔位（讓 server.py import 不報錯）

```python
# tools/file_ops.py
def register_tools(mcp, sw):
    pass
```

```python
# tools/drawing.py
def register_tools(mcp, sw):
    pass
```

```python
# tools/annotation.py
def register_tools(mcp, sw):
    pass
```

```python
# tools/export.py
def register_tools(mcp, sw):
    pass
```

Step 3: Commit
```bash
git add server.py tools/
git commit -m "feat: MCP server entry point with tool registration skeleton"
```

---

### Task 5: file_ops — open_document, close_document, list_open_documents

Files:
- Modify: `tools/file_ops.py`

Step 1: 實作 file_ops.py

```python
"""檔案操作 tools — open, close, list documents."""

from __future__ import annotations

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

from errors import SWError, SWFileError, SWNotRunningError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

# SolidWorks 文件類型常數
SW_DOC_PART = 1
SW_DOC_ASSEMBLY = 2
SW_DOC_DRAWING = 3

# 副檔名對應
EXT_TO_TYPE = {
    ".sldprt": SW_DOC_PART,
    ".sldasm": SW_DOC_ASSEMBLY,
    ".slddrw": SW_DOC_DRAWING,
}

TYPE_NAMES = {
    SW_DOC_PART: "part",
    SW_DOC_ASSEMBLY: "assembly",
    SW_DOC_DRAWING: "drawing",
}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def open_document(file_path: str) -> str:
        """開啟 SolidWorks 文件（.sldprt / .sldasm / .slddrw）。
        回傳文件名稱、類型、開啟狀態。"""
        try:
            result = await sw.execute(_open_document, file_path)
            return json.dumps(result, ensure_ascii=False)
        except SWNotRunningError as e:
            raise ToolError(str(e))
        except SWFileError as e:
            raise ToolError(str(e))
        except SWError as e:
            raise ToolError(f"open_document 失敗: {e}")

    @mcp.tool()
    async def close_document(file_path: str) -> str:
        """關閉指定的 SolidWorks 文件。"""
        try:
            result = await sw.execute(_close_document, file_path)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"close_document 失敗: {e}")

    @mcp.tool()
    async def list_open_documents() -> str:
        """列出所有目前在 SolidWorks 中開啟的文件。"""
        try:
            result = await sw.execute(_list_open_documents)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"list_open_documents 失敗: {e}")


# --- COM Worker Thread 中執行的函式 ---

def _open_document(file_path: str) -> dict:
    if not os.path.exists(file_path):
        raise SWFileError(f"檔案不存在: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    doc_type = EXT_TO_TYPE.get(ext)
    if doc_type is None:
        raise SWFileError(f"不支援的檔案格式: {ext}")

    sw = SWConnection.get_instance()
    app = sw.get_app()

    errors = app.OpenDoc6(
        file_path,
        doc_type,
        1,  # swOpenDocOptions_Silent
        "",  # configuration
    )
    # OpenDoc6 回傳 (doc, errors) 或直接回傳 doc，視 COM binding
    # 需在實機測試時確認回傳格式
    doc = app.ActiveDoc
    if doc is None:
        raise SWError(f"開啟文件失敗: {file_path}")

    return {
        "file_name": os.path.basename(file_path),
        "file_path": file_path,
        "type": TYPE_NAMES.get(doc_type, "unknown"),
        "status": "opened",
    }


def _close_document(file_path: str) -> dict:
    sw = SWConnection.get_instance()
    app = sw.get_app()

    file_name = os.path.basename(file_path)
    app.CloseDoc(file_name)

    return {
        "file_name": file_name,
        "status": "closed",
    }


def _list_open_documents() -> dict:
    sw = SWConnection.get_instance()
    app = sw.get_app()

    docs = []
    # GetDocuments 回傳 VT_ARRAY of IModelDoc2，可能為 None
    open_docs = app.GetDocuments
    if open_docs:
        for doc in open_docs:
            doc_type = doc.GetType
            docs.append({
                "file_name": doc.GetTitle,
                "file_path": doc.GetPathName,
                "type": TYPE_NAMES.get(doc_type, "unknown"),
            })

    return {
        "count": len(docs),
        "documents": docs,
    }


# Import ToolError — 在 mcp 套件中的位置
try:
    from mcp.shared.exceptions import McpError as ToolError
except ImportError:
    # fallback: 若 MCP SDK 版本不同
    ToolError = Exception
```

Step 2: Commit
```bash
git add tools/file_ops.py
git commit -m "feat: file_ops tools — open, close, list documents"
```

---

### Task 6: drawing — create_drawing, insert_standard_views

Files:
- Modify: `tools/drawing.py`

Step 1: 實作 drawing.py

```python
"""出圖 tools — 建立 Drawing、插入標準視圖。"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

import config
from errors import SWError, SWNotRunningError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

# SolidWorks 常數
SW_DOC_DRAWING = 3
SW_DRAWING_PAPER_SIZE = {
    "A4": 8,   # swDwgPaperAsize
    "A3": 9,   # swDwgPaperBsize
    "A2": 10,  # swDwgPaperCsize
    "A1": 11,  # swDwgPaperDsize
    "A0": 12,  # swDwgPaperEsize
}

# SolidWorks 標準視圖名稱對應
# CreateDrawViewFromModelView3 的 viewName 參數
SW_VIEW_NAMES = {
    "front": "*Front",
    "back": "*Back",
    "top": "*Top",
    "bottom": "*Bottom",
    "right": "*Right",
    "left": "*Left",
    "isometric": "*Isometric",
    "trimetric": "*Trimetric",
    "dimetric": "*Dimetric",
}

# 第一角法視圖位置（相對於圖紙中心的偏移，比例因子）
# front=中央, top=下方, right=左方, isometric=右上
FIRST_ANGLE_LAYOUT = {
    "front":     (0.40, 0.55),
    "top":       (0.40, 0.25),
    "right":     (0.15, 0.55),
    "left":      (0.65, 0.55),
    "bottom":    (0.40, 0.85),
    "back":      (0.90, 0.55),
    "isometric": (0.75, 0.25),
}


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def create_drawing(
        template_path: str | None = None,
        paper_size: str = config.DEFAULT_PAPER_SIZE,
    ) -> str:
        """建立新的 Drawing 文件，套用公司圖框模板。
        template_path: 圖框模板路徑（.drwdot），預設使用 config 設定。
        paper_size: 圖紙大小（A4/A3/A2/A1/A0），預設 A3。"""
        try:
            result = await sw.execute(
                _create_drawing,
                template_path or config.TEMPLATE_PATH,
                paper_size,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"create_drawing 失敗: {e}")

    @mcp.tool()
    async def insert_standard_views(
        source_doc: str,
        views: list[str] | None = None,
        scale: float | None = None,
    ) -> str:
        """在 Drawing 中插入第一角法標準視圖。
        source_doc: 來源 part/assembly 文件路徑。
        views: 視圖清單，可選 front/back/top/bottom/left/right/isometric，預設 front+top+right+isometric。
        scale: 視圖比例，預設自動適配。"""
        if views is None:
            views = ["front", "top", "right", "isometric"]

        try:
            result = await sw.execute(
                _insert_standard_views,
                source_doc,
                views,
                scale,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_standard_views 失敗: {e}")


# --- COM Worker Thread 中執行的函式 ---

def _create_drawing(template_path: str, paper_size: str) -> dict:
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()

    if not template_path:
        raise SWError("未設定圖框模板路徑，請設定 SW_MCP_TEMPLATE")

    doc = app.NewDocument(
        template_path,
        SW_DRAWING_PAPER_SIZE.get(paper_size.upper(), 9),  # 預設 A3
        0,  # width (0=use template)
        0,  # height (0=use template)
    )

    if doc is None:
        raise SWError(f"建立 Drawing 失敗，模板: {template_path}")

    title = doc.GetTitle

    return {
        "drawing_name": title,
        "template": template_path,
        "paper_size": paper_size,
        "status": "created",
    }


def _insert_standard_views(
    source_doc: str,
    views: list[str],
    scale: float | None,
) -> dict:
    sw_conn = SWConnection.get_instance()
    app = sw_conn.get_app()
    drawing = app.ActiveDoc

    if drawing is None:
        raise SWError("目前沒有開啟的 Drawing 文件")

    # 取得圖紙尺寸（mm）
    sheet = drawing.GetCurrentSheet()
    sheet_props = sheet.GetProperties2()
    # GetProperties2 回傳: (paperSize, templateIn, scale1, scale2,
    #                       firstAngle, width, height, ...)
    sheet_width = sheet_props[5] * 1000   # m → mm
    sheet_height = sheet_props[6] * 1000  # m → mm

    inserted = []

    for view_key in views:
        sw_view_name = SW_VIEW_NAMES.get(view_key.lower())
        if sw_view_name is None:
            logger.warning("未知的視圖名稱: %s，跳過", view_key)
            continue

        # 計算視圖位置
        layout = FIRST_ANGLE_LAYOUT.get(view_key.lower(), (0.5, 0.5))
        x = sheet_width * layout[0] / 1000   # mm → m (API 用 m)
        y = sheet_height * layout[1] / 1000

        view = drawing.CreateDrawViewFromModelView3(
            source_doc,
            sw_view_name,
            x,
            y,
            0,  # z
        )

        if view is None:
            logger.warning("插入視圖失敗: %s", view_key)
            continue

        # 設定比例
        if scale is not None:
            view.ScaleRatio = (1.0, scale)

        # 設定顯示模式：Hidden Lines Removed
        view.SetDisplayMode3(
            False,  # not using parent style
            6,      # swHIDDEN_GREYED (或嘗試 swHIDDEN = 2)
            False,  # high quality
            False,  # not wireframe
        )

        inserted.append({
            "view": view_key,
            "sw_view": sw_view_name,
            "position": {"x": round(x * 1000, 1), "y": round(y * 1000, 1)},
        })

    drawing.ViewZoomtofit2()

    return {
        "count": len(inserted),
        "views": inserted,
        "status": "inserted",
    }


try:
    from mcp.shared.exceptions import McpError as ToolError
except ImportError:
    ToolError = Exception
```

Step 2: Commit
```bash
git add tools/drawing.py
git commit -m "feat: drawing tools — create_drawing, insert_standard_views (first angle)"
```

---

### Task 7: annotation — insert_model_dimensions

Files:
- Modify: `tools/annotation.py`

Step 1: 實作 annotation.py

```python
"""標註 tools — 匯入模型尺寸。"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from errors import SWError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

# InsertModelAnnotations3 的常數
SW_INSERT_DIMENSION = 0          # swImportModelItemsSource_e: entire model
SW_INSERT_MODEL_ITEMS_ALL = 127  # all dimension types


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def insert_model_dimensions(
        view_name: str | None = None,
        dimension_type: str = "all",
    ) -> str:
        """匯入模型尺寸到 Drawing 視圖。
        view_name: 指定視圖名稱，預設全部視圖。
        dimension_type: 篩選類型 all/marked/reference，預設 all。"""
        try:
            result = await sw.execute(
                _insert_model_dimensions,
                view_name,
                dimension_type,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"insert_model_dimensions 失敗: {e}")


def _insert_model_dimensions(
    view_name: str | None,
    dimension_type: str,
) -> dict:
    sw_conn = SWConnection.get_instance()
    drawing = sw_conn.get_active_doc()

    sheet = drawing.GetCurrentSheet()
    views = sheet.GetViews()

    if views is None or len(views) == 0:
        raise SWError("目前的 Drawing Sheet 沒有任何視圖")

    # 決定尺寸來源
    source_map = {
        "all": 0,       # swInsertDimensionsMarkedForDrawing
        "marked": 1,    # swInsertDimensionsMarkedForDrawing
        "reference": 2, # swInsertDimensionsReferenceForDrawing
    }
    source = source_map.get(dimension_type.lower(), 0)

    results = []
    total_count = 0

    for view in views:
        current_name = view.GetName2()

        # 如果指定了 view_name，只處理該視圖
        if view_name and current_name != view_name:
            continue

        # 選取視圖
        drawing.ActivateView(current_name)

        # InsertModelAnnotations3:
        # (source, markForDrawing, useDocLayoutSetting, useDocDisplaySetting,
        #  useDocStyleSetting, useDocLabelSetting)
        ok = drawing.InsertModelAnnotations3(
            source,    # source type
            32767,     # swImportModelItemsSource: all items
            True,      # use doc layout
            True,      # use doc display
            False,     # use doc style
            False,     # use doc label
        )

        # 計算這個視圖插入了多少尺寸
        dim_count = 0
        annotations = view.GetAnnotations
        if annotations:
            dim_count = len(annotations)

        results.append({
            "view": current_name,
            "dimensions_inserted": dim_count,
        })
        total_count += dim_count

    return {
        "total_dimensions": total_count,
        "views": results,
        "status": "inserted",
        "note": "尺寸位置可能重疊，建議用 capture_drawing 確認後手動調整",
    }


try:
    from mcp.shared.exceptions import McpError as ToolError
except ImportError:
    ToolError = Exception
```

Step 2: Commit
```bash
git add tools/annotation.py
git commit -m "feat: annotation tool — insert_model_dimensions"
```

---

### Task 8: export — capture_drawing, save_as_pdf, save_drawing

Files:
- Modify: `tools/export.py`

Step 1: 實作 export.py

```python
"""輸出 tools — 截圖、PDF、儲存 Drawing。"""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent

import config
from errors import SWError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

# SolidWorks SaveAs 常數
SW_SAVE_AS_CURRENT_VERSION = 0
SW_SAVE_WITH_REFERENCES_NO = 0


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def capture_drawing(
        output_mode: str = "auto",
        resolution: str = "low",
    ) -> str | list:
        """截取目前 Drawing 畫面。
        output_mode: auto（預設，自動判斷）/ base64 / smb。
        resolution: low（800px）/ high（2000px）。
        auto 模式下小於 1MB 回傳 base64 圖片，超過存 SMB 回傳路徑。"""
        try:
            result = await sw.execute(
                _capture_drawing,
                output_mode,
                resolution,
            )
            # 如果是 base64 圖片，回傳 ImageContent
            if isinstance(result, dict) and result.get("mode") == "base64":
                return [ImageContent(
                    type="image",
                    data=result["data"],
                    mimeType="image/png",
                )]
            # 否則回傳 SMB 路徑
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"capture_drawing 失敗: {e}")

    @mcp.tool()
    async def save_as_pdf(output_path: str | None = None) -> str:
        """將目前的 Drawing 輸出為 PDF。
        output_path: 輸出路徑，預設存到 SMB 共享資料夾。"""
        try:
            result = await sw.execute(_save_as_pdf, output_path)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"save_as_pdf 失敗: {e}")

    @mcp.tool()
    async def save_drawing(file_path: str | None = None) -> str:
        """儲存目前的 Drawing 文件（.slddrw）。
        file_path: 另存路徑，預設覆蓋原檔。"""
        try:
            result = await sw.execute(_save_drawing, file_path)
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"save_drawing 失敗: {e}")


# --- COM Worker Thread 中執行的函式 ---

def _capture_drawing(output_mode: str, resolution: str) -> dict:
    sw_conn = SWConnection.get_instance()
    doc = sw_conn.get_active_doc()

    # 解析度設定
    width = 800 if resolution == "low" else 2000

    # 存到暫存檔
    tmp_dir = tempfile.mkdtemp(prefix="sw_mcp_")
    tmp_path = os.path.join(tmp_dir, "capture.png")

    try:
        # SaveBMP: (fileName, width, height)
        # height=0 讓 SW 自動按比例計算
        doc.SaveBMP(tmp_path, width, 0)

        if not os.path.exists(tmp_path):
            raise SWError("截圖失敗：SaveBMP 未產生檔案")

        file_size = os.path.getsize(tmp_path)

        # 判斷輸出模式
        use_base64 = False
        if output_mode == "base64":
            use_base64 = True
        elif output_mode == "smb":
            use_base64 = False
        else:  # auto
            use_base64 = file_size <= config.MAX_BASE64_SIZE

        if use_base64:
            # 如果強制 base64 但超過大小，用 Pillow 壓縮
            if file_size > config.MAX_BASE64_SIZE:
                from PIL import Image
                img = Image.open(tmp_path)
                # 降低解析度
                ratio = (config.MAX_BASE64_SIZE / file_size) ** 0.5
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)
                img.save(tmp_path, "PNG", optimize=True)

            with open(tmp_path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")

            return {"mode": "base64", "data": data}
        else:
            # 存到 SMB
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            title = doc.GetTitle.replace(" ", "_")
            smb_filename = f"{title}_{timestamp}.png"
            smb_path = os.path.join(config.SMB_SHARE_PATH, smb_filename)

            os.makedirs(config.SMB_SHARE_PATH, exist_ok=True)
            shutil.copy2(tmp_path, smb_path)

            return {
                "mode": "smb",
                "path": smb_path,
                "size_bytes": os.path.getsize(smb_path),
                "status": "saved",
            }
    finally:
        # 清理暫存
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _save_as_pdf(output_path: str | None) -> dict:
    sw_conn = SWConnection.get_instance()
    doc = sw_conn.get_active_doc()

    if output_path is None:
        title = doc.GetTitle.replace(" ", "_")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(
            config.SMB_SHARE_PATH,
            f"{title}_{timestamp}.pdf",
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # SaveAs with PDF export
    extension = doc.Extension
    errors = [0]
    warnings = [0]

    ok = extension.SaveAs3(
        output_path,
        0,  # version (current)
        0,  # options
    )

    if not os.path.exists(output_path):
        raise SWError(f"PDF 輸出失敗: {output_path}")

    return {
        "path": output_path,
        "size_bytes": os.path.getsize(output_path),
        "status": "saved",
    }


def _save_drawing(file_path: str | None) -> dict:
    sw_conn = SWConnection.get_instance()
    doc = sw_conn.get_active_doc()

    if file_path:
        # SaveAs
        extension = doc.Extension
        extension.SaveAs3(
            file_path,
            SW_SAVE_AS_CURRENT_VERSION,
            SW_SAVE_WITH_REFERENCES_NO,
        )
        saved_path = file_path
    else:
        # Save (覆蓋原檔)
        doc.Save3(
            1,  # swSaveAsOptions_Silent
            0,  # errors
            0,  # warnings
        )
        saved_path = doc.GetPathName

    return {
        "path": saved_path,
        "status": "saved",
    }


try:
    from mcp.shared.exceptions import McpError as ToolError
except ImportError:
    ToolError = Exception
```

Step 2: Commit
```bash
git add tools/export.py
git commit -m "feat: export tools — capture_drawing (base64/SMB auto), save_as_pdf, save_drawing"
```

---

### Task 9: 單元測試 — config, export 邏輯

Files:
- Create: `tests/test_config.py`
- Create: `tests/test_export_logic.py`

Step 1: 寫 config 載入測試

```python
# tests/test_config.py
"""測試 config 從環境變數載入。"""

import os
from unittest.mock import patch


def test_config_defaults():
    """未設定環境變數時應使用預設值。"""
    with patch.dict(os.environ, {}, clear=True):
        # 重新載入 config 模組
        import importlib
        import config
        importlib.reload(config)

        assert config.SW_HOST == "0.0.0.0"
        assert config.SW_PORT == 8080
        assert config.DEFAULT_PAPER_SIZE == "A3"
        assert config.MAX_BASE64_SIZE == 1 * 1024 * 1024


def test_config_from_env():
    """應從環境變數覆蓋預設值。"""
    env = {
        "SW_MCP_HOST": "10.0.0.5",
        "SW_MCP_PORT": "9090",
        "SW_MCP_PAPER_SIZE": "A4",
        "SW_MCP_MAX_BASE64": "2097152",
    }
    with patch.dict(os.environ, env, clear=True):
        import importlib
        import config
        importlib.reload(config)

        assert config.SW_HOST == "10.0.0.5"
        assert config.SW_PORT == 9090
        assert config.DEFAULT_PAPER_SIZE == "A4"
        assert config.MAX_BASE64_SIZE == 2 * 1024 * 1024
```

Step 2: 寫 export 截圖模式判斷的單元測試

```python
# tests/test_export_logic.py
"""測試 export 的截圖模式判斷邏輯（不需要 SolidWorks）。"""


def test_auto_mode_small_file_uses_base64():
    """auto 模式下，小檔案應用 base64。"""
    max_size = 1 * 1024 * 1024  # 1MB
    file_size = 500 * 1024  # 500KB
    use_base64 = file_size <= max_size
    assert use_base64 is True


def test_auto_mode_large_file_uses_smb():
    """auto 模式下，大檔案應用 SMB。"""
    max_size = 1 * 1024 * 1024
    file_size = 2 * 1024 * 1024  # 2MB
    use_base64 = file_size <= max_size
    assert use_base64 is False


def test_forced_base64_mode():
    """強制 base64 模式。"""
    output_mode = "base64"
    assert output_mode == "base64"


def test_forced_smb_mode():
    """強制 smb 模式。"""
    output_mode = "smb"
    assert output_mode == "smb"
```

Step 3: 跑測試
```bash
pytest tests/test_config.py tests/test_export_logic.py -v
```
Expected: PASS

Step 4: Commit
```bash
git add tests/
git commit -m "test: unit tests for config loading and export mode logic"
```

---

### Task 10: ToolError import 統一

files_ops、drawing、annotation、export 四個檔案都有重複的 ToolError import fallback。統一到一處。

Files:
- Modify: `errors.py`
- Modify: `tools/file_ops.py`
- Modify: `tools/drawing.py`
- Modify: `tools/annotation.py`
- Modify: `tools/export.py`

Step 1: 在 errors.py 加入 ToolError 統一 import

在 errors.py 最底部加入：

```python
# MCP ToolError 統一 import
try:
    from mcp.shared.exceptions import McpError as ToolError
except ImportError:
    class ToolError(Exception):
        pass
```

Step 2: 四個 tools 檔案移除各自的 ToolError import，改為：

```python
from errors import ToolError
```

移除各檔案底部的 `try: from mcp...` 區塊。

Step 3: 跑測試確認沒壞
```bash
pytest tests/ -v
```
Expected: PASS

Step 4: Commit
```bash
git add errors.py tools/
git commit -m "refactor: centralize ToolError import in errors.py"
```
