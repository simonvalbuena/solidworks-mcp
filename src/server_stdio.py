"""stdio entry point for solidworks-mcp so Claude Desktop (Cowork) can launch it
as a local MCP server instead of connecting over HTTP.

All logging goes to stderr — stdout is reserved for the MCP protocol.
"""

import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    force=True,
)

from server import mcp, sw  # noqa: E402  (registers all tools on import)

if __name__ == "__main__":
    logging.getLogger(__name__).info("starting solidworks-mcp over stdio")
    sw.start()
    mcp.run(transport="stdio")
