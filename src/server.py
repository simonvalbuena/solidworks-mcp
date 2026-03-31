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

mcp = FastMCP(
    "solidworks",
    host=config.SW_HOST,
    port=config.SW_PORT,
)
sw = SWConnection()


def register_all_tools() -> None:
    """註冊所有 MCP tools。"""
    from tools.file_ops import register_tools as register_file_ops
    from tools.drawing import register_tools as register_drawing
    from tools.annotation import register_tools as register_annotation
    from tools.export import register_tools as register_export
    from tools.assembly import register_tools as register_assembly

    register_file_ops(mcp, sw)
    register_drawing(mcp, sw)
    register_annotation(mcp, sw)
    register_export(mcp, sw)
    register_assembly(mcp, sw)


register_all_tools()

if __name__ == "__main__":
    logger.info("啟動 SolidWorks MCP Server on %s:%s", config.SW_HOST, config.SW_PORT)
    sw.start()
    mcp.run(transport="streamable-http")
