"""檔案操作 tools — open, close, list documents."""

from __future__ import annotations

import json
import logging
import os

import pythoncom
import win32com.client
from mcp.server.fastmcp import FastMCP

from errors import SWError, SWFileError, SWNotRunningError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

SW_DOC_PART = 1
SW_DOC_ASSEMBLY = 2
SW_DOC_DRAWING = 3

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
        except (SWNotRunningError, SWFileError) as e:
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


def _open_document(file_path: str) -> dict:
    if not os.path.exists(file_path):
        raise SWFileError(f"檔案不存在: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()
    doc_type = EXT_TO_TYPE.get(ext)
    if doc_type is None:
        raise SWFileError(f"不支援的檔案格式: {ext}")

    sw = SWConnection.get_instance()
    app = sw.get_app()

    errors = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warnings = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = app.OpenDoc6(
        file_path,
        doc_type,
        1,  # swOpenDocOptions_Silent
        "",  # configuration
        errors,
        warnings,
    )
    if doc is None:
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
