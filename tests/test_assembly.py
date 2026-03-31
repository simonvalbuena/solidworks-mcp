"""測試 assembly tool 的純邏輯函式（不需要 SolidWorks）。"""

import pytest

try:
    from tools.assembly import (
        get_mate_type_name,
        is_system_feature,
        parse_entity_params,
        MATE_TYPE_MAP,
        ENTITY_TYPE_MAP,
        SYSTEM_FEATURE_TYPES,
        _M_TO_MM,
    )
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(
    not HAS_DEPS, reason="需要 pywin32（僅在 SW 主機上可用）"
)


# --- get_mate_type_name ---

class TestGetMateTypeName:

    def test_coincident(self):
        assert get_mate_type_name(0) == "coincident"

    def test_concentric(self):
        assert get_mate_type_name(1) == "concentric"

    def test_distance(self):
        assert get_mate_type_name(5) == "distance"

    def test_angle(self):
        assert get_mate_type_name(6) == "angle"

    def test_symmetric(self):
        assert get_mate_type_name(8) == "symmetric"

    def test_lock(self):
        assert get_mate_type_name(16) == "lock"

    def test_hinge(self):
        assert get_mate_type_name(22) == "hinge"

    def test_unknown_type_returns_fallback(self):
        assert get_mate_type_name(99) == "unknown(99)"

    def test_all_known_types_covered(self):
        """確認所有 MATE_TYPE_MAP 的值都能正確對應。"""
        for type_int, name in MATE_TYPE_MAP.items():
            assert get_mate_type_name(type_int) == name


# --- parse_entity_params ---

class TestParseEntityParams:

    def test_point_entity_conversion(self):
        """swMatePoint (ref_type=0): point 從公尺轉 mm。"""
        params = [0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0]
        result = parse_entity_params(params, 0)
        assert result["entity_type"] == "point"
        assert result["point"] == pytest.approx([100.0, 200.0, 300.0])
        assert result["vector"] == [0.0, 0.0, 0.0]
        assert result["radius1"] == 0.0
        assert result["radius2"] == 0.0

    def test_plane_entity(self):
        """swMatePlane (ref_type=2): point + 法向量。"""
        params = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        result = parse_entity_params(params, 2)
        assert result["entity_type"] == "plane"
        assert result["point"] == [0.0, 0.0, 0.0]
        assert result["vector"] == [0.0, 0.0, 1.0]

    def test_line_entity(self):
        """swMateLine (ref_type=1): point + 方向向量。"""
        params = [0.01, 0.02, 0.03, 1.0, 0.0, 0.0, 0.0, 0.0]
        result = parse_entity_params(params, 1)
        assert result["entity_type"] == "line"
        assert result["point"] == pytest.approx([10.0, 20.0, 30.0])
        assert result["vector"] == [1.0, 0.0, 0.0]

    def test_cylinder_entity_with_radius(self):
        """swMateCylinder (ref_type=3): point + 軸向量 + radius1。"""
        params = [0.05, 0.0, 0.0, 0.0, 0.0, 1.0, 0.005, 0.0]
        result = parse_entity_params(params, 3)
        assert result["entity_type"] == "cylinder"
        assert result["point"] == pytest.approx([50.0, 0.0, 0.0])
        assert result["vector"] == [0.0, 0.0, 1.0]
        assert result["radius1"] == pytest.approx(5.0)
        assert result["radius2"] == 0.0

    def test_cone_entity_with_two_radii(self):
        """swMateCone (ref_type=4): point + 軸向量 + radius1 + radius2。"""
        params = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.01, 0.005]
        result = parse_entity_params(params, 4)
        assert result["entity_type"] == "cone"
        assert result["radius1"] == pytest.approx(10.0)
        assert result["radius2"] == pytest.approx(5.0)

    def test_unknown_ref_type(self):
        """未知的 ref_type 應回傳 unknown(N) 字串。"""
        params = [0.0] * 8
        result = parse_entity_params(params, 99)
        assert result["entity_type"] == "unknown(99)"

    def test_none_params_handled(self):
        """params 為 None 時不應報錯。"""
        result = parse_entity_params(None, 0)
        assert result["point"] == [0.0, 0.0, 0.0]
        assert result["vector"] == [0.0, 0.0, 0.0]
        assert result["radius1"] == 0.0
        assert result["radius2"] == 0.0

    def test_short_params_padded(self):
        """params 不足 8 個 double 時自動補零。"""
        params = [0.1, 0.2]
        result = parse_entity_params(params, 0)
        assert result["point"] == pytest.approx([100.0, 200.0, 0.0])
        assert result["vector"] == [0.0, 0.0, 0.0]
        assert result["radius1"] == 0.0
        assert result["radius2"] == 0.0

    def test_tuple_params_accepted(self):
        """pywin32 COM 可能回傳 tuple，應正確處理。"""
        params = (0.001, 0.002, 0.003, 0.0, 0.0, 1.0, 0.0, 0.0)
        result = parse_entity_params(params, 2)
        assert result["point"] == pytest.approx([1.0, 2.0, 3.0])

    def test_vector_not_converted(self):
        """向量是無因次方向，不應乘以 1000。"""
        params = [0.0, 0.0, 0.0, 0.577, 0.577, 0.577, 0.0, 0.0]
        result = parse_entity_params(params, 1)
        assert result["vector"] == pytest.approx([0.577, 0.577, 0.577])

    def test_all_entity_types_covered(self):
        """確認所有 ENTITY_TYPE_MAP 的值都能正確對應。"""
        for ref_type, name in ENTITY_TYPE_MAP.items():
            result = parse_entity_params([0.0] * 8, ref_type)
            assert result["entity_type"] == name


# --- is_system_feature ---

class TestIsSystemFeature:

    def test_comments_folder_is_system(self):
        assert is_system_feature("CommentsFolder") is True

    def test_extrude_type_is_not_system(self):
        """非系統的 type name 應回傳 False。"""
        assert is_system_feature("Extrude1") is False

    def test_ref_plane_is_system(self):
        assert is_system_feature("RefPlane") is True

    def test_all_system_types_return_true(self):
        """確認 SYSTEM_FEATURE_TYPES 中每個值都被判定為系統特徵。"""
        for type_name in SYSTEM_FEATURE_TYPES:
            assert is_system_feature(type_name) is True, f"{type_name} should be system"

    def test_modeling_feature_types_return_false(self):
        """常見建模特徵類型應回傳 False。"""
        modeling_types = ["ICE", "Cut", "Fillet", "Chamfer", "MirrorPattern",
                          "LPattern", "CirPattern", "Rib", "Shell", "Helix"]
        for type_name in modeling_types:
            assert is_system_feature(type_name) is False, f"{type_name} should not be system"
