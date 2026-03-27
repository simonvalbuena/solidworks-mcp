"""測試 config 從環境變數載入。"""

import os
from unittest.mock import patch


def test_config_defaults():
    """未設定環境變數時應使用預設值。"""
    with patch.dict(os.environ, {}, clear=True):
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
