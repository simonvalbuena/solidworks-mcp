# add_dimension radius 擴充設計文件

## 概要

擴充 `add_dimension` tool，新增 `radius` 尺寸類型。選取一條圓弧（arc）邊線自動產生半徑標註。

## COM 行為驗證結果

2026-04-01 實機測試確認：
- arc 邊線 `SelectEntity → AddDimension` 可行
- SolidWorks 自動判定為半徑尺寸（dim name 前綴 `RD`）
- COM 流程與 diameter 完全一致

## 重構方案

把 `_add_diameter_dimension` 改名為 `_add_radial_dimension`，加 `dim_kind` 參數區分 diameter/radius：

```python
def _add_radial_dimension(
    view_name: str,
    edge1: dict,
    text_position: dict | None,
    dim_kind: str,  # "diameter" | "radius"
) -> dict:
```

內部差異用 `dim_kind` 切換：
- edge type 驗證：`"circle" if dim_kind == "diameter" else "arc"`
- 錯誤訊息：`f"{dim_kind} 尺寸"`
- 回傳 `dimension_type: dim_kind`

## Tool 介面變更

```python
@mcp.tool()
async def add_dimension(
    view_name: str,
    edge1: dict,
    edge2: dict | None = None,
    dimension_type: str = "linear",  # "linear" | "diameter" | "radius"
    text_position: dict | None = None,
) -> str:
```

### 驗證邏輯

- `"linear"` → edge2 必填
- `"diameter"` → edge2 忽略，edge1 必須是 circle
- `"radius"` → edge2 忽略，edge1 必須是 arc
- 其他 → ToolError

### 回傳結構

與 diameter 一致：

```json
{
    "status": "done",
    "dimension_type": "radius",
    "value_mm": 38.5,
    "text_position": {"x": 80.0, "y": 30.0},
    "match_method_edge1": "index"
}
```

## Handler 分派

```python
elif dimension_type == "radius":
    result = await sw.execute(
        _add_radial_dimension, view_name, edge1, text_position, "radius",
    )
```

既有 diameter 分派改為：

```python
elif dimension_type == "diameter":
    result = await sw.execute(
        _add_radial_dimension, view_name, edge1, text_position, "diameter",
    )
```

## 命名調整

- `_add_diameter_dimension` → `_add_radial_dimension`
- `_calc_diameter_text_pos` → `_calc_radial_text_pos`

## 測試策略

### 新增 3 個測試

- `test_add_dim_radius_resolve_arc_edge` — arc edge 正確 resolve + type check 通過
- `test_add_dim_radius_rejects_non_arc` — edge type 不是 arc 時報 SWError
- `test_add_dim_radius_com_flow` — mock COM 完整流程，回傳 dimension_type="radius" + value_mm

### 既有 diameter 測試

4 個測試的 import/呼叫隨重構改名，邏輯不變。
