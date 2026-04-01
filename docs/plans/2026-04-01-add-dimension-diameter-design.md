# add_dimension diameter 擴充設計文件

## 概要

擴充現有 `add_dimension` tool，新增 `diameter` 尺寸類型支援。選取一條完整圓形邊線自動產生直徑標註。

## 範圍

- 新增 `dimension_type` 參數："linear"（預設）| "diameter"
- diameter 只需 edge1，edge2 改為 optional
- 不做 radius（arc 半徑）— 等 COM 行為實測後再加
- 順便補齊回傳結構的 `dimension_type` 和 `value_mm`（PR #6 DEFER 項目）

## Tool 介面變更

```python
async def add_dimension(
    view_name: str,
    edge1: dict,
    edge2: dict | None = None,       # linear 必填，diameter 不傳
    dimension_type: str = "linear",   # "linear" | "diameter"
    text_position: dict | None = None,
) -> str:
```

### 驗證邏輯

- `dimension_type="linear"` → `edge2` 必須有值
- `dimension_type="diameter"` → 忽略 `edge2`；resolve edge1 後檢查 `edges_info[idx]["type"] == "circle"`
- 不認識的 `dimension_type` → 報錯

### 回傳結構

```json
{
    "status": "done",
    "dimension_type": "diameter",
    "value_mm": 25.0,
    "text_position": {"x": 80.0, "y": 30.0},
    "match_method_edge1": "index"
}
```

`value_mm` 從 `disp_dim.GetDimension2(0).Value * 1000` 取得。

## COM 操作流程（_add_diameter_dimension）

1. 取 drawing / views / edges（共用 _get_drawing_views + _get_view_edges）
2. `_resolve_edge` 匹配 edge1
3. 驗證 `edges_info[idx]["type"] == "circle"`
4. `SelectEntity(edges[idx], False)` — 單條，不帶 append
5. text_position：有傳直接用；沒傳 → 圓心 x + 視圖右邊界偏移，y 用圓心 y
6. `ext.AddDimension(x, y, 0, d)` 四方向嘗試 + `AddDimension2` fallback
7. `SetUnits2` / `SetPrecision3`
8. 取 `value_mm`
9. 回傳結果

### 設計決策

- 不跟 `_add_linear_dimension` 抽共用 — 後半段差異大（選取方式、驗證、文字位置），各自獨立清晰
- text_position 預設用視圖右邊界偏移，跟 `_add_circle_dims` 策略一致
- `_add_linear_dimension` 同步補上 `dimension_type: "linear"` 和 `value_mm`

## Tool Handler 分派

```python
if dimension_type == "linear":
    if edge2 is None:
        raise ToolError("linear 尺寸需要 edge2")
    result = await sw.execute(_add_linear_dimension, ...)
elif dimension_type == "diameter":
    result = await sw.execute(_add_diameter_dimension, ...)
else:
    raise ToolError(f"不支援的 dimension_type: {dimension_type}")
```

## 測試策略

### 單元測試

- `test_add_dim_diameter_resolve_edge` — diameter 正確 resolve circle edge
- `test_add_dim_diameter_rejects_non_circle` — edge type 不是 circle 時報 SWError
- `test_add_dim_diameter_auto_text_position` — 自動計算文字位置有 x/y
- `test_add_dim_linear_missing_edge2` — linear 但 edge2=None 時報錯
- `test_add_dim_linear_returns_value_mm` — linear 回傳含 dimension_type + value_mm

### 整合測試（mock COM）

- `test_add_dim_diameter_com_flow` — SelectEntity 呼叫一次、AddDimension 正確、回傳 value_mm

預估 6 個新 test cases。
