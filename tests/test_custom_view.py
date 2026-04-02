"""測試 insert_custom_view（不需 SolidWorks）。"""

import math
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

try:
    from tools.drawing import (
        _euler_to_transform_array,
        SW_DISPLAY_MODES,
    )
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

try:
    from tools.drawing import _insert_custom_view  # noqa: F401 — Task 2 才新增
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not HAS_DEPS, reason="需要 mcp/pywin32 依賴（僅在 SW 主機上可用）"
)


def _make_mock_com(attrs=None, **kwargs):
    """建立模擬 COM 物件的 mock（有 _oleobj_ 屬性）。"""
    m = MagicMock(**kwargs)
    m._oleobj_ = True
    if attrs:
        for k, v in attrs.items():
            setattr(m, k, v)
    return m


# === Euler rotation matrix tests ===


def test_euler_identity():
    """(0, 0, 0) → 單位矩陣。"""
    arr = _euler_to_transform_array(0, 0, 0)
    assert len(arr) == 16
    # 3x3 identity
    assert abs(arr[0] - 1.0) < 1e-10   # R00
    assert abs(arr[5] - 1.0) < 1e-10   # R11
    assert abs(arr[10] - 1.0) < 1e-10  # R22
    # off-diagonals = 0
    for i in [1, 2, 4, 6, 8, 9]:
        assert abs(arr[i]) < 1e-10
    # padding zeros + scale
    assert arr[3] == 0.0
    assert arr[7] == 0.0
    assert arr[11] == 0.0
    assert arr[12] == 0.0
    assert arr[13] == 0.0
    assert arr[14] == 0.0
    assert arr[15] == 1.0


def test_euler_rotate_x_90():
    """繞 X 軸轉 90°: Y→Z, Z→-Y。"""
    arr = _euler_to_transform_array(90, 0, 0)
    # R = [[1,0,0],[0,0,-1],[0,1,0]]
    assert abs(arr[0] - 1.0) < 1e-10
    assert abs(arr[5] - 0.0) < 1e-10
    assert abs(arr[6] - (-1.0)) < 1e-10
    assert abs(arr[9] - 1.0) < 1e-10
    assert abs(arr[10] - 0.0) < 1e-10


def test_euler_rotate_y_90():
    """繞 Y 軸轉 90°: X→-Z, Z→X。"""
    arr = _euler_to_transform_array(0, 90, 0)
    # R = [[0,0,1],[0,1,0],[-1,0,0]]
    assert abs(arr[0] - 0.0) < 1e-10
    assert abs(arr[2] - 1.0) < 1e-10
    assert abs(arr[5] - 1.0) < 1e-10
    assert abs(arr[8] - (-1.0)) < 1e-10
    assert abs(arr[10] - 0.0) < 1e-10


def test_euler_rotate_z_90():
    """繞 Z 軸轉 90°: X→Y, Y→-X。"""
    arr = _euler_to_transform_array(0, 0, 90)
    # R = [[0,-1,0],[1,0,0],[0,0,1]]
    assert abs(arr[0] - 0.0) < 1e-10
    assert abs(arr[1] - (-1.0)) < 1e-10
    assert abs(arr[4] - 1.0) < 1e-10
    assert abs(arr[5] - 0.0) < 1e-10
    assert abs(arr[10] - 1.0) < 1e-10


# === Parameter validation tests ===


def test_custom_view_both_params_error():
    """同時指定 view_name 和 orientation 時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="不能同時"):
            _insert_custom_view(
                source_doc="C:\\test.sldprt",
                view_name="front",
                orientation={"x": 0, "y": 0, "z": 0},
            )


def test_custom_view_no_params_error():
    """view_name 和 orientation 都不指定時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="必須指定"):
            _insert_custom_view(source_doc="C:\\test.sldprt")


def test_custom_view_invalid_view_name():
    """無效的 view_name 時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="不支援的 view_name"):
            _insert_custom_view(
                source_doc="C:\\test.sldprt",
                view_name="invalid_view",
            )


def test_custom_view_invalid_display_mode():
    """無效的 display_mode 時 raise SWError。"""
    from errors import SWError

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = _make_mock_com()
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="不支援的 display_mode"):
            _insert_custom_view(
                source_doc="C:\\test.sldprt",
                view_name="front",
                display_mode="invalid_mode",
            )


# === Named view path tests ===


def test_custom_view_named_view_com_flow():
    """具名視角路徑：驗證 CreateDrawViewFromModelView3 呼叫 + 後處理。"""
    drawing = _make_mock_com()
    view = _make_mock_com()
    view.Name = "Drawing View1"
    view.Position = (0.210, 0.1485)
    view.ScaleRatio = (1.0, 2.0)
    drawing.CreateDrawViewFromModelView3.return_value = view

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        app = _make_mock_com()
        app.ActiveDoc = drawing
        inst.get_app.return_value = app

        result = _insert_custom_view(
            source_doc="C:\\models\\test.sldprt",
            view_name="isometric",
            position={"x": 210.0, "y": 148.5},
            scale=2.0,
            display_mode="shaded",
        )

    assert result["status"] == "done"
    assert result["view_name"] == "Drawing View1"
    assert result["display_mode"] == "shaded"
    assert result["position"] == {"x": 210.0, "y": 148.5}

    # 驗證 CreateDrawViewFromModelView3 參數
    cdv_args = drawing.CreateDrawViewFromModelView3.call_args[0]
    assert cdv_args[0] == "C:\\models\\test.sldprt"
    assert cdv_args[1] == "*等角視"  # isometric
    assert abs(cdv_args[2] - 0.210) < 0.001   # x (meters)
    assert abs(cdv_args[3] - 0.1485) < 0.001  # y (meters)

    # 驗證 SetDisplayMode3
    view.SetDisplayMode3.assert_called_once_with(False, 3, False, False)  # 3 = shaded

    # 驗證 ScaleRatio
    assert view.ScaleRatio == (1.0, 2.0)


# === Custom orientation path tests ===


def _make_mock_app_for_custom_view():
    """建立 custom orientation 路徑需要的完整 mock 體系。

    回傳 (app, drawing, model, model_view, transform, created_view)。
    """
    drawing = _make_mock_com()
    type(drawing).GetTitle = property(lambda self: "Drawing1")

    model = _make_mock_com()
    model_view = _make_mock_com()
    transform = _make_mock_com()
    model_view.Orientation3 = transform
    model.ActiveView = model_view

    created_view = _make_mock_com()
    created_view.Name = "Drawing View2"
    created_view.Position = (0.210, 0.1485)
    created_view.ScaleRatio = (1.0, 1.0)
    drawing.CreateDrawViewFromModelView3.return_value = created_view

    app = _make_mock_com()
    app.ActiveDoc = drawing
    # ActivateDoc: 檔名 → model, Drawing1 → drawing
    def activate_doc(name):
        if name == "Drawing1":
            app.ActiveDoc = drawing
            return drawing
        else:
            app.ActiveDoc = model
            return model
    app.ActivateDoc.side_effect = activate_doc

    return app, drawing, model, model_view, transform, created_view


def test_custom_view_orientation_com_flow():
    """自訂角度路徑：驗證完整 COM 呼叫順序。"""
    app, drawing, model, model_view, transform, view = (
        _make_mock_app_for_custom_view()
    )

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        result = _insert_custom_view(
            source_doc="C:\\models\\test.sldprt",
            orientation={"x": 30, "y": 45, "z": 0},
            position={"x": 210.0, "y": 148.5},
        )

    assert result["status"] == "done"
    assert result["view_name"] == "Drawing View2"

    # 驗證 COM 呼叫順序
    # 1. ActivateDoc 切到來源模型
    first_activate = app.ActivateDoc.call_args_list[0]
    assert first_activate[0][0] == "test.sldprt"

    # 2. ShowNamedView2 reset 到前視圖
    model.ShowNamedView2.assert_called_once_with("", 1)

    # 3. Orientation3 被設定（transform.ArrayData 被寫入）
    assert transform.ArrayData is not None

    # 4. NameView 命名暫存視圖
    model.NameView.assert_called_once_with("_mcp_custom_temp")

    # 5. ActivateDoc 切回 Drawing
    # 6. CreateDrawViewFromModelView3 建立視圖
    cdv_args = drawing.CreateDrawViewFromModelView3.call_args[0]
    assert cdv_args[0] == "C:\\models\\test.sldprt"
    assert cdv_args[1] == "_mcp_custom_temp"

    # 7. 清理：DeleteNamedView
    model.DeleteNamedView.assert_called_once_with("_mcp_custom_temp")


def test_custom_view_orientation_cleanup_on_failure():
    """CreateDrawViewFromModelView3 失敗時，暫存視圖仍被清理。"""
    from errors import SWError

    app, drawing, model, model_view, transform, _ = (
        _make_mock_app_for_custom_view()
    )
    # 讓 CreateDrawViewFromModelView3 回傳 None（失敗）
    drawing.CreateDrawViewFromModelView3.return_value = None

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        with pytest.raises(SWError, match="自訂角度視圖失敗"):
            _insert_custom_view(
                source_doc="C:\\models\\test.sldprt",
                orientation={"x": 0, "y": 90, "z": 0},
                position={"x": 210.0, "y": 148.5},
            )

    # 即使失敗，DeleteNamedView 仍被呼叫
    model.DeleteNamedView.assert_called_once_with("_mcp_custom_temp")


def test_custom_view_auto_position_paper_center():
    """不提供 position 時預設紙張中央（A3: 210, 148.5mm）。"""
    app, drawing, model, model_view, transform, view = (
        _make_mock_app_for_custom_view()
    )

    with patch("tools.drawing.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        result = _insert_custom_view(
            source_doc="C:\\models\\test.sldprt",
            orientation={"x": 0, "y": 0, "z": 0},
            paper_size="A3",
        )

    cdv_args = drawing.CreateDrawViewFromModelView3.call_args[0]
    assert abs(cdv_args[2] - 0.210) < 0.001    # A3 寬 420/2 = 210mm
    assert abs(cdv_args[3] - 0.1485) < 0.001   # A3 高 297/2 = 148.5mm
