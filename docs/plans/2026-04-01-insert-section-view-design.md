# insert_section_view 設計文件

## 概要

新增 `insert_section_view` MCP tool，在工程圖中建立剖面圖。在父視圖上繪製剖面線，自動產生對應的剖面視圖。

## COM 行為驗證結果

2026-04-01 實機測試確認：

- `ActivateView → SketchManager.CreateLine → CreateSectionViewAt5` 流程可行
- CreateLine 自動 select sketch segment（type=10 = swSelSKETCHSEGS），不需額外選取
- CreateSectionViewAt5 options=0 即可建立基本剖面圖
- 座標系統：sheet 絕對座標（meters），跟 view Position/Outline 同座標系
- View name 為中文："剖面視圖 A-A"
- Scale 自動繼承父視圖
- GetSection() 方法呼叫失敗（DISP_E_MEMBERNOTFOUND），但基本功能不需要它

## Tool 介面

```python
@mcp.tool()
async def insert_section_view(
    parent_view: str,
    section_line: dict,
    label: str = "A",
    position: dict | None = None,
    scale: float | None = None,
) -> str:
```

### 參數

- `parent_view: str` — 父視圖名稱
- `section_line: dict` — 剖面線定義，sheet 絕對座標（mm）
  - `{"start": {"x": float, "y": float}, "end": {"x": float, "y": float}}`
- `label: str` — 剖面標記，預設 "A"（產生 "A-A" 標記）
- `position: dict | None` — 剖面圖在 sheet 上的位置（mm），預設父視圖右側 +50mm
- `scale: float | None` — 比例分母，預設繼承父視圖

### position 預設邏輯

```python
outline = parent_view.GetOutline  # [xmin, ymin, xmax, ymax] meters
pos_x = (outline[2] + 0.05) * 1000  # 右邊 + 50mm
pos_y = ((outline[1] + outline[3]) / 2) * 1000  # 垂直居中
```

### 回傳結構

```json
{
    "status": "done",
    "view_name": "剖面視圖 A-A",
    "label": "A",
    "position": {"x": 564.8, "y": 462.6},
    "scale": "1:5",
    "parent_view": "工程視圖1"
}
```

## COM 流程

```
1. 取得 drawing，驗證 doc type = 3
2. 找到父視圖（用既有 _get_drawing_views）
3. ActivateView(parent_view)
4. ClearSelection2(True)
5. SketchManager.CreateLine(start_x/1000, start_y/1000, 0, end_x/1000, end_y/1000, 0)
6. CreateSectionViewAt5(pos_x/1000, pos_y/1000, 0, label, 0, None, 0)
7. 如果 scale 有指定 → view.ScaleRatio = (1, scale)
8. EditRebuild3()
```

## 錯誤處理

- 父視圖不存在 → SWError
- CreateLine 回傳 None → SWError "無法繪製剖面線"
- CreateSectionViewAt5 回傳 None → SWError "無法建立剖面圖"

## 實作位置

- `src/tools/views.py` — `_insert_section_view` 同步 COM 函式 + `insert_section_view` async handler
- `tests/test_views.py` — 3 個測試

## 測試策略

### 3 個測試

- `test_insert_section_view_com_flow` — mock 完整 COM 流程，驗證回傳結構
- `test_insert_section_view_auto_position` — 不提供 position，驗證自動計算
- `test_insert_section_view_parent_not_found` — 父視圖不存在時 raise SWError
