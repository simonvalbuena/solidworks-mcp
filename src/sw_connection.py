"""SolidWorks COM 連線管理 — 專用 COM Worker Thread。

所有 SolidWorks COM 操作都必須在同一個 STA 執行緒中執行。
此模組提供 SWConnection，透過 queue 將 COM 操作派發到專用執行緒。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from concurrent.futures import Future
from typing import Any, Callable

import pythoncom
import win32com.client

from errors import SWConnectionError, SWNotRunningError, SWTimeoutError

logger = logging.getLogger(__name__)

COM_TIMEOUT = int(os.environ.get("SW_COM_TIMEOUT", "90"))  # dense tube views need > 30 s

_SHUTDOWN = object()


class SWConnection:
    """SolidWorks COM 連線管理（Singleton）。"""

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
        if cls._instance is None:
            raise SWConnectionError("SWConnection 尚未建立")
        return cls._instance

    def start(self) -> None:
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
            self._ready.set()
        finally:
            self._sw_app = None
            pythoncom.CoUninitialize()
            logger.info("COM Worker Thread 已結束")

    def _init_com(self) -> None:
        try:
            # 優先嘗試 early-binding（EnsureDispatch），可提供更好的介面解析。
            # 若 type library 不可用則 fallback 到 late-binding（Dispatch）。
            # 注意：即使 early-binding，ActiveDoc 仍回傳 IModelDoc2，
            # IDrawingDoc 專屬方法（如 GetCurrentSheet）需用替代方案。
            try:
                self._sw_app = win32com.client.gencache.EnsureDispatch(
                    "SldWorks.Application"
                )
                logger.info("SolidWorks COM 連線成功（early-binding）")
            except Exception:
                self._sw_app = win32com.client.Dispatch("SldWorks.Application")
                logger.info("SolidWorks COM 連線成功（late-binding）")
            self._sw_app.Visible = True
        except Exception as e:
            raise SWNotRunningError(
                f"無法連線到 SolidWorks，請確認 SolidWorks 已啟動: {e}"
            ) from e

    def _process_queue(self) -> None:
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
        if not self._running:
            raise SWConnectionError("COM Worker Thread 未啟動")
        future: Future = Future()
        with self._queue_lock:
            self._queue.append((func, args, kwargs, future))
        self._queue_event.set()
        loop = asyncio.get_running_loop()
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
        if self._sw_app is None:
            raise SWNotRunningError("SolidWorks COM 連線不可用")
        return self._sw_app

    def get_active_doc(self) -> Any:
        app = self.get_app()
        doc = app.ActiveDoc
        if doc is None:
            raise SWConnectionError("目前沒有開啟的文件")
        return doc

    def shutdown(self) -> None:
        if not self._running:
            return
        with self._queue_lock:
            self._queue.append(_SHUTDOWN)
        self._queue_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._running = False
        logger.info("SWConnection 已關閉")
