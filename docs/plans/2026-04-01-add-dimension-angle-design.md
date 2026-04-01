# add_dimension angle 擴充設計文件

## 概要

擴充 `add_dimension` tool，新增 `angle` 尺寸類型。選取兩條非平行直線邊線自動產生角度標註。

## COM 行為驗證結果

2026-04-01 實機測試確認：
- 兩條 line edge `SelectEntity（append）→ AddDimension` 可行
- SolidWorks 自動判定為角度尺寸（dim name 前綴 `RD`，Type2=3=swAngularDimension）
- `dim.Value` 在 `SetUnits2(False, 0, ...)` 後回傳度數（swDEGREES=0 跟 swMM=0 同值）
- `dim.SystemValue` 回傳弧度（system units）
- `ext.AddDimension` 四方向都回傳 None，`AddDimension2` fallback 成功

## Tool 介面變更

```python
@mcp.tool()
async def add_dimension(
    view_name: str,
    edge1: dict,
    edge2: dict | None = None,
    dimension_type: str = "linear",  # "linear" | "diameter" | "radius" | "angle"
    text_position: dict | None = None,
) -> str:
```

### 驗證邏輯

- `"linear"` → edge2 必填
- `"diameter"` → edge2 忽略，edge1 必須是 circle
- `"radius"` → edge2 忽略，edge1 必須是 arc
- `"angle"` → edge2 必填，edge1 和 edge2 都必須是 line
- 其他 → ToolError

### 回傳結構

```json
{
    "status": "done",
    "dimension_type": "angle",
    "value_deg": 89.0,
    "text_position": {"x": 102.35, "y": 109.8},
    "match_method_edge1": "index",
    "match_method_edge2": "proximity"
}
```

注意：angle 回傳 `value_deg`（度數），不回傳 `value_mm`。

## COM 操作流程（_add_angle_dimension）

1. 取 drawing / views / edges（共用 _get_drawing_views + _get_view_edges）
2. `_resolve_edge` 匹配 edge1 和 edge2
3. 驗證兩條邊線 `edges_info[idx]["type"] == "line"`
4. `SelectEntity(edges[idx1], False)` + `SelectEntity(edges[idx2], True)`（append）
5. text_position：有傳直接用；沒傳 → 兩條邊線 midpoint 的中間點
6. `ext.AddDimension(x, y, 0, d)` 四方向嘗試 + `AddDimension2` fallback
7. `SetUnits2(False, 0, ...)` — swDEGREES=0
8. 取 `value_deg = dim.Value`（度數）
9. 回傳結果

### 設計決策

- 獨立函式 `_add_angle_dimension`，不跟 `_add_linear_dimension` 合併 — 回傳欄位不同（value_deg vs value_mm）、邊線驗證邏輯不同
- SetUnits2 不需改動，swDEGREES=0 跟 swMM=0 同值
- 文字位置預設用兩條邊線 midpoint 的中間點

## Handler 分派

```python
elif dimension_type == "angle":
    if edge2 is None:
        raise ToolError("angle 尺寸需要 edge2")
    result = await sw.execute(
        _add_angle_dimension, view_name, edge1, edge2, text_position,
    )
```

## 測試策略

### 新增 3 個測試

- `test_add_dim_angle_resolve_line_edges` — 兩條 line edge 正確 resolve + type check 通過
- `test_add_dim_angle_rejects_non_line` — edge type 不是 line 時報 SWError
- `test_add_dim_angle_com_flow` — mock COM 完整流程，SelectEntity 兩次（第二次 append=True）、回傳 dimension_type="angle" + value_deg
