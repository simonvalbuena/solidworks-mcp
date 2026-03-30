"""測試 export 的截圖模式判斷邏輯（不需要 SolidWorks）。"""

import pytest

try:
    from tools.export import should_use_base64
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(
    not HAS_DEPS, reason="需要 pywin32（僅在 SW 主機上可用）"
)

MAX_SIZE = 1 * 1024 * 1024  # 1MB


def test_auto_mode_small_file_uses_base64():
    """auto 模式下，小檔案應用 base64。"""
    assert should_use_base64("auto", 500 * 1024, MAX_SIZE) is True


def test_auto_mode_large_file_uses_smb():
    """auto 模式下，大檔案應用 SMB。"""
    assert should_use_base64("auto", 2 * 1024 * 1024, MAX_SIZE) is False


def test_auto_mode_exact_boundary():
    """auto 模式下，剛好等於上限應用 base64。"""
    assert should_use_base64("auto", MAX_SIZE, MAX_SIZE) is True


def test_forced_base64_overrides_auto():
    """強制 base64 模式應忽略檔案大小。"""
    assert should_use_base64("base64", 2 * 1024 * 1024, MAX_SIZE) is True


def test_forced_smb_overrides_auto():
    """強制 smb 模式應忽略檔案大小。"""
    assert should_use_base64("smb", 100, MAX_SIZE) is False
