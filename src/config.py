"""從 .env 載入 MCP Server 設定。"""

import os
from dotenv import load_dotenv

load_dotenv()

SW_HOST = os.getenv("SW_MCP_HOST", "0.0.0.0")
SW_PORT = int(os.getenv("SW_MCP_PORT", "8080"))
SMB_SHARE_PATH = os.getenv("SW_MCP_SMB_PATH", r"C:\mcp-share")
SMB_CLIENT_PATH = os.getenv("SW_MCP_SMB_CLIENT_PATH", "")
TEMPLATE_PATH = os.getenv("SW_MCP_TEMPLATE", "")
DEFAULT_PAPER_SIZE = os.getenv("SW_MCP_PAPER_SIZE", "A3")
MAX_BASE64_SIZE = int(os.getenv("SW_MCP_MAX_BASE64", "1048576"))
