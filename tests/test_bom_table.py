"""測試 insert_bom_table（不需 SolidWorks）。"""

import pytest
from unittest.mock import MagicMock, patch

try:
    from tools.annotation import _insert_bom_table
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(
    not HAS_DEPS, reason="需要 mcp/pywin32 依賴（僅在 SW 主機上可用）"
)


def _make_mock_com(**kwargs):
    """建立模擬 COM 物件的 mock（有 _oleobj_ 屬性）。"""
    m = MagicMock(**kwargs)
    m._oleobj_ = True
    return m


def _make_mock_drawing_with_assembly_view(view_name="工程視圖1"):
    """建立 mock drawing，含一個參考組立件的視圖（FeatureTree 結構）。"""
    drawing = _make_mock_com()
    type(drawing).GetType = property(lambda self: 3)  # swDocDRAWING

    # 視圖 sub-feature
    sub = _make_mock_com()
    sub.GetTypeName2 = "AbsoluteView"
    sub.Name = view_name
    view_obj = _make_mock_com()
    # 視圖參考的文件（組立件 type=2）
    ref_doc = _make_mock_com()
    type(ref_doc).GetType = property(lambda self: 2)
    view_obj.ReferencedDocument = ref_doc
    sub.GetSpecificFeature2.return_value = view_obj
    type(sub).GetNextSubFeature = property(lambda self: None)

    # DrSheet
    sheet = _make_mock_com()
    sheet.GetTypeName2 = "DrSheet"
    sheet.Name = "圖頁1"
    type(sheet).GetFirstSubFeature = property(lambda self, s=sub: s)
    type(sheet).GetNextFeature = property(lambda self: None)
    type(drawing).FirstFeature = property(lambda self, sh=sheet: sh)

    return drawing


def test_insert_bom_table_success():
    """happy path：插入 BOM 表成功，mm→m 換算正確。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        result = _insert_bom_table(
            view_name="工程視圖1", x=250.0, y=180.0, drawing_name=None,
        )

    assert result["status"] == "done"
    assert result["table_name"] == "Bill of Materials1"

    # 驗證 mm→m 換算（250 → 0.25, 180 → 0.18）
    call_args = drawing.InsertBomTable4.call_args[0]
    assert call_args[1] == pytest.approx(0.25)
    assert call_args[2] == pytest.approx(0.18)
    # BomType = swBomTable_TopLevelOnly = 1
    assert call_args[4] == 1
    assert call_args[5] == ""   # ConfigName: 用 view 現有 config
    assert call_args[6] == ""   # TableTemplate: 用 SW 內建範本（R3）


def test_not_drawing_raises():
    """active doc 非 drawing → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    type(drawing).GetType = property(lambda self: 1)  # swDocPART

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="not a drawing"):
            _insert_bom_table("工程視圖1", 0, 0, None)


def test_view_not_found_raises():
    """指定的 view_name 不存在 → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="View '不存在的視圖' not found"):
            _insert_bom_table("不存在的視圖", 0, 0, None)


def test_view_not_assembly_raises():
    """view 參考的是零件而非組立件 → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    # 把 ReferencedDocument 改成零件（type=1）
    ref_doc = _make_mock_com()
    type(ref_doc).GetType = property(lambda self: 1)
    # 從 FeatureTree 拿到 view_obj 改其 ReferencedDocument
    sheet = drawing.FirstFeature
    sub = sheet.GetFirstSubFeature
    view_obj = sub.GetSpecificFeature2.return_value
    view_obj.ReferencedDocument = ref_doc

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="does not reference an assembly"):
            _insert_bom_table("工程視圖1", 0, 0, None)


def test_fallback_to_v3():
    """InsertBomTable4 失敗時退化到 InsertBomTable3。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    drawing.InsertBomTable4.side_effect = Exception("VARIANT byref failed")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable3.return_value = bom_table

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        result = _insert_bom_table("工程視圖1", 250, 180, None)

    assert result["status"] == "done"
    assert result["table_name"] == "Bill of Materials1"
    drawing.InsertBomTable3.assert_called_once()


def test_both_versions_fail_raises():
    """v4 與 v3 都失敗 → SWError。"""
    from errors import SWError
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    drawing.InsertBomTable4.return_value = None
    drawing.InsertBomTable3.return_value = None

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        with pytest.raises(SWError, match="both API versions returned None"):
            _insert_bom_table("工程視圖1", 0, 0, None)


def test_drawing_name_provided():
    """傳入 drawing_name 時呼叫 ActivateDoc2。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table
    app = _make_mock_com(ActiveDoc=drawing)

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        _insert_bom_table("工程視圖1", 250, 180, "assembly_abc.SLDDRW")

    app.ActivateDoc2.assert_called_once_with("assembly_abc.SLDDRW", True, 0)


def test_drawing_name_omitted_skips_activate():
    """drawing_name=None 時不呼叫 ActivateDoc2。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table
    app = _make_mock_com(ActiveDoc=drawing)

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = app

        _insert_bom_table("工程視圖1", 250, 180, None)

    app.ActivateDoc2.assert_not_called()


def test_mm_to_meters_conversion_boundary():
    """邊界值（0,0）與一般值（300.5）換算正確。"""
    drawing = _make_mock_drawing_with_assembly_view("工程視圖1")
    bom_table = _make_mock_com(Name="Bill of Materials1")
    drawing.InsertBomTable4.return_value = bom_table

    with patch("tools.annotation.SWConnection") as MockSW:
        inst = MockSW.get_instance.return_value
        inst.get_app.return_value = _make_mock_com(ActiveDoc=drawing)

        _insert_bom_table("工程視圖1", 0.0, 0.0, None)
        _insert_bom_table("工程視圖1", 300.5, 420.25, None)

    calls = drawing.InsertBomTable4.call_args_list
    # 第一次：0,0
    assert calls[0][0][1] == pytest.approx(0.0)
    assert calls[0][0][2] == pytest.approx(0.0)
    # 第二次：0.3005, 0.42025
    assert calls[1][0][1] == pytest.approx(0.3005)
    assert calls[1][0][2] == pytest.approx(0.42025)
