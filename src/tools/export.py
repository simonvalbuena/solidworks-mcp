"""輸出 tools — 截圖、PDF、儲存 Drawing。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime

from mcp.server.fastmcp import FastMCP

import config
from errors import SWError, ToolError
from sw_connection import SWConnection

logger = logging.getLogger(__name__)

SW_SAVE_AS_CURRENT_VERSION = 0
SW_SAVE_WITH_REFERENCES_NO = 0
SW_SAVE_AS_OPTIONS_SILENT = 1


def register_tools(mcp: FastMCP, sw: SWConnection) -> None:

    @mcp.tool()
    async def capture_drawing(
        resolution: str = "low",
    ) -> str:
        """截取目前 Drawing 畫面，存為 JPEG 回傳檔案路徑。
        resolution: low（800px）/ high（2000px）。
        回傳路徑可用 Read tool 查看截圖。"""
        try:
            result = await sw.execute(
                _capture_drawing,
                resolution,
            )
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


def should_use_base64(output_mode: str, file_size: int, max_size: int) -> bool:
    """判斷截圖應使用 base64 或 SMB。供外部測試呼叫。"""
    if output_mode == "base64":
        return True
    if output_mode == "smb":
        return False
    return file_size <= max_size


JPEG_QUALITY = 85


def _bmp_to_jpeg(bmp_path: str, jpeg_path: str) -> None:
    """將 BMP 轉為 JPEG。"""
    from PIL import Image

    img = Image.open(bmp_path)
    img = img.convert("RGB")
    img.save(jpeg_path, "JPEG", quality=JPEG_QUALITY, optimize=True)


def _server_to_client_path(server_path: str) -> str:
    """將 server 端路徑轉為 client 端可存取的路徑。"""
    if not config.SMB_CLIENT_PATH:
        return server_path
    return server_path.replace(config.SMB_SHARE_PATH, config.SMB_CLIENT_PATH, 1)


def _capture_drawing(resolution: str) -> dict:
    sw_conn = SWConnection.get_instance()
    doc = sw_conn.get_active_doc()

    width = 800 if resolution == "low" else 2000

    tmp_dir = tempfile.mkdtemp(prefix="sw_mcp_")
    bmp_path = os.path.join(tmp_dir, "capture.bmp")
    jpeg_path = os.path.join(tmp_dir, "capture.jpg")

    try:
        doc.SaveBMP(bmp_path, width, 0)

        if not os.path.exists(bmp_path):
            raise SWError("截圖失敗：SaveBMP 未產生檔案")

        _bmp_to_jpeg(bmp_path, jpeg_path)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        title = doc.GetTitle.replace(" ", "_")
        smb_filename = f"{title}_{timestamp}.jpg"
        smb_path = os.path.join(config.SMB_SHARE_PATH, smb_filename)

        os.makedirs(config.SMB_SHARE_PATH, exist_ok=True)
        shutil.copy2(jpeg_path, smb_path)

        client_path = _server_to_client_path(smb_path)

        return {
            "path": client_path,
            "size_bytes": os.path.getsize(smb_path),
            "status": "saved",
        }
    finally:
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

    extension = doc.Extension
    extension.SaveAs3(
        output_path,
        0,
        0,
    )

    if not os.path.exists(output_path):
        raise SWError(f"PDF 輸出失敗: {output_path}")

    return {
        "path": _server_to_client_path(output_path),
        "size_bytes": os.path.getsize(output_path),
        "status": "saved",
    }


def _save_drawing(file_path: str | None) -> dict:
    sw_conn = SWConnection.get_instance()
    doc = sw_conn.get_active_doc()

    if file_path:
        extension = doc.Extension
        extension.SaveAs3(
            file_path,
            SW_SAVE_AS_CURRENT_VERSION,
            SW_SAVE_WITH_REFERENCES_NO,
        )
        saved_path = file_path
    else:
        doc.Save3(
            SW_SAVE_AS_OPTIONS_SILENT,
            0,
            0,
        )
        saved_path = doc.GetPathName

    return {
        "path": saved_path,
        "status": "saved",
    }
