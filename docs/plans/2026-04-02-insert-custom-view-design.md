# insert_custom_view 設計文件

## 目標

插入自訂角度的視圖。支援兩種模式：具名視角（SW 預設方向）和任意 XYZ 旋轉角度。

## 介面

```python
async def insert_custom_view(
    source_doc: str,
    view_name: str | None = None,
    orientation: dict | None = None,  # {"x": float, "y": float, "z": float} 度
    position: dict | None = None,     # {"x": mm, "y": mm}
    scale: float | None = None,
    display_mode: str = "hidden_lines_removed",
) -> str:
```

### 參數驗證

- `view_name` 和 `orientation` 必須二擇一，兩個都給或都不給則報錯
- `view_name` 只接受 SW_VIEW_NAMES 的 key（front/back/top/bottom/left/right/isometric/trimetric/dimetric）
- `orientation` 的 x/y/z 為繞各軸旋轉角度（度），extrinsic rotation 順序 X → Y → Z
- `position` 不給時預設紙張中央
- `display_mode`: wireframe / hidden_lines_removed / shaded

### 回傳

```json
{
  "status": "done",
  "view_name": "Drawing View1",
  "position": {"x": 210.0, "y": 148.5},
  "scale": "1:2",
  "display_mode": "hidden_lines_removed"
}
```

## COM 實作流程

### 路徑 1 — 具名視角（view_name）

直接呼叫，與 insert_standard_views 相同模式：

```
drawing.CreateDrawViewFromModelView3(source_doc, SW_VIEW_NAMES[view_name], x, y, 0)
```

### 路徑 2 — 自訂角度（orientation）

1. `app.ActivateDoc3(source_doc)` — 切到來源模型
2. 取得 `ModelView = model.ActiveView`，用 `MathUtility` 建構旋轉 `MathTransform`
3. 設定 `ModelView.Orientation3 = transform`
4. `model.NameView("_mcp_custom_temp")` — 命名暫存視圖
5. `app.ActivateDoc3(drawing_title)` — 切回 Drawing
6. `drawing.CreateDrawViewFromModelView3(source_doc, "_mcp_custom_temp", x, y, 0)`
7. 切回來源模型，`model.DeleteNamedView("_mcp_custom_temp")` 清理

### 旋轉矩陣

純 Python `math` 模組，不引入 numpy。

`_euler_to_transform_array(x_deg, y_deg, z_deg)` → 16 元素 list，餵給 `MathTransform`。

Euler 角度（度）→ 弧度 → XYZ extrinsic rotation matrix。

### 共用後處理

- `view.SetDisplayMode3` 設定顯示模式
- `view.ScaleRatio = (1.0, scale)` 設定比例
- `drawing.EditRebuild3()` 重建

## display_mode 映射

```python
SW_DISPLAY_MODES = {
    "wireframe": 1,              # swWIREFRAME
    "hidden_lines_removed": 6,   # swHIDDEN_LINES_REMOVED
    "shaded": 3,                 # swSHADED
}
```

## 錯誤處理

- 參數互斥檢查在 tool handler 層
- COM ActivateDoc3 失敗 → SWError
- 暫存視圖清理放 try/finally，確保失敗也會刪除
- CreateDrawViewFromModelView3 回傳 None → SWError

## 測試策略

- 單元測試：mock COM，驗證參數驗證、分支邏輯、旋轉矩陣計算
- `_euler_to_transform_array` 獨立測試：純函式，驗證已知角度矩陣正確性（如 90° 繞 Z = front → right）
- 具名視角路徑：驗證 CreateDrawViewFromModelView3 參數正確
- 自訂角度路徑：驗證 COM 呼叫順序（ActivateDoc → NameView → ActivateDoc → CreateView → DeleteNamedView）
- 暫存視圖清理：模擬建立視圖失敗，驗證 DeleteNamedView 仍被呼叫

## 檔案結構

- 實作：`src/tools/drawing.py`
- 測試：`tests/test_custom_view.py`
