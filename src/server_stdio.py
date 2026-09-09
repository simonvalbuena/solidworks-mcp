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
# Also log to logs/server.log (DEBUG) so a SolidWorks crash can be diagnosed afterwards
try:
    _LOG_DIR = os.path.join(os.path.dirname(HERE), "logs")
    os.makedirs(_LOG_DIR, exist_ok=True)
    _fh = logging.FileHandler(os.path.join(_LOG_DIR, "server.log"), encoding="utf-8")
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logging.getLogger().addHandler(_fh)
    logging.getLogger().setLevel(logging.DEBUG)
except Exception:  # noqa: BLE001
    pass

from server import mcp, sw  # noqa: E402  (registers all tools on import)

if __name__ == "__main__":
    logging.getLogger(__name__).info("starting solidworks-mcp over stdio")
    sw.start()
    mcp.run(transport="stdio")
