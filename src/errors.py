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


# MCP ToolError 統一 import
try:
    from mcp.shared.exceptions import McpError as ToolError
except ImportError:
    class ToolError(Exception):
        pass
