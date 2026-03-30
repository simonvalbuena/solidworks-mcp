"""測試 COM Worker Thread 的 queue 派發機制（不需要 SolidWorks）。"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

try:
    from sw_connection import SWConnection
    from errors import SWTimeoutError
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

pytestmark = pytest.mark.skipif(
    not HAS_PYWIN32, reason="需要 pywin32（僅在 SW 主機上可用）"
)


@pytest.fixture
def sw():
    """建立 SWConnection 實例，mock 掉 COM 初始化。"""
    SWConnection._instance = None
    with patch("sw_connection.pythoncom"), \
         patch("sw_connection.win32com.client") as mock_win32:
        mock_app = MagicMock()
        mock_app.Visible = True
        mock_win32.Dispatch.return_value = mock_app

        conn = SWConnection()
        conn.start()
        yield conn
        conn.shutdown()
    SWConnection._instance = None


def test_execute_returns_result(sw):
    """execute 應將 callable 派發到 worker thread 並回傳結果。"""
    def add(a, b):
        return a + b

    result = asyncio.run(sw.execute(add, 3, 7))
    assert result == 10


def test_execute_propagates_exception(sw):
    """worker thread 中的 exception 應傳回 caller。"""
    def fail():
        raise ValueError("test error")

    with pytest.raises(ValueError, match="test error"):
        asyncio.run(sw.execute(fail))


def test_get_app_returns_com_object(sw):
    """get_app 應回傳 COM 物件。"""
    def check_app():
        app = SWConnection.get_instance().get_app()
        return app is not None

    result = asyncio.run(sw.execute(check_app))
    assert result is True


def test_singleton():
    """SWConnection 應為 Singleton。"""
    SWConnection._instance = None
    a = SWConnection()
    b = SWConnection()
    assert a is b
    SWConnection._instance = None
