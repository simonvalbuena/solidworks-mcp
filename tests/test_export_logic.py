"""測試 export 的截圖模式判斷邏輯（不需要 SolidWorks）。"""

import pytest

try:
    from tools.export import (
        should_use_base64,
        STANDARD_VIEWS,
        DEFAULT_VIEWS,
        validate_views,
    )
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


class TestStandardViews:

    def test_all_nine_views_present(self):
        expected = {"front", "back", "top", "bottom", "left", "right",
                    "isometric", "dimetric", "trimetric"}
        assert set(STANDARD_VIEWS.keys()) == expected

    def test_enum_values_are_unique(self):
        values = list(STANDARD_VIEWS.values())
        assert len(values) == len(set(values))

    def test_enum_values_range(self):
        for name, val in STANDARD_VIEWS.items():
            assert 1 <= val <= 9, f"{name} has invalid enum value {val}"


class TestDefaultViews:

    def test_default_views_are_valid(self):
        for v in DEFAULT_VIEWS:
            assert v in STANDARD_VIEWS

    def test_default_views_count(self):
        assert len(DEFAULT_VIEWS) == 4


class TestValidateViews:

    def test_all_valid(self):
        valid, invalid = validate_views(["front", "top", "isometric"])
        assert valid == ["front", "top", "isometric"]
        assert invalid == []

    def test_mixed_valid_invalid(self):
        valid, invalid = validate_views(["front", "diagonal", "top"])
        assert valid == ["front", "top"]
        assert invalid == ["diagonal"]

    def test_all_invalid(self):
        valid, invalid = validate_views(["xxx", "yyy"])
        assert valid == []
        assert invalid == ["xxx", "yyy"]

    def test_empty_list(self):
        valid, invalid = validate_views([])
        assert valid == []
        assert invalid == []

    def test_case_insensitive(self):
        valid, invalid = validate_views(["Front", "TOP", "Isometric"])
        assert valid == ["front", "top", "isometric"]
        assert invalid == []

    def test_deduplication(self):
        valid, invalid = validate_views(["front", "front", "top"])
        assert valid == ["front", "top"]
        assert invalid == []

    def test_whitespace_trimmed(self):
        valid, invalid = validate_views(["  front  ", "top"])
        assert valid == ["front", "top"]
        assert invalid == []
