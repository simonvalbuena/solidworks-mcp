# insert_detail_view 設計文件

## 目標

新增 `insert_detail_view` MCP tool，在父視圖上畫放大圓建立局部放大圖。

## Tool 介面

| 參數 | 型別 | 說明 |
|------|------|------|
| `parent_view` | str | 父視圖名稱 |
| `center` | dict | 放大區域中心點 `{"x": mm, "y": mm}`，sheet 絕對座標 |
| `radius` | float | 放大區域半徑（mm） |
| `label` | str (選填) | 標記，預設 "A" |
| `scale` | float (選填) | 放大比例分子，預設 2（即 2:1） |
| `position` | dict (選填) | detail view 放置位置 `{"x": mm, "y": mm}`，預設父視圖右上方 |

## COM 流程

```
ActivateView(parent_view)
  → ClearSelection2
  → SketchManager.CreateCircle(center_x, center_y, 0, center_x + radius, center_y, 0)
  → CreateDetailViewAt4(pos_x, pos_y, 0,
       style=0,          # swDetViewSTANDARD
       scale1=scale,     # 分子（如 2）
       scale2=1.0,       # 分母（固定 1）
       label=label,
       showtype=1,       # swDetCircleCIRCLE
       fullOutline=True,
       jaggedOutline=False,
       noOutline=False,
       shapeIntensity=5)
```

## API 參考

### CreateDetailViewAt4 簽名

```
CreateDetailViewAt4(
    X, Y, Z,              # detail view 放置座標（sheet 絕對，meters）
    Style,                 # swDetViewStyle_e（0=STANDARD）
    Scale1, Scale2,        # 比例分子/分母
    LabelIn,               # 標記字串
    Showtype,              # swDetCircleShowType_e（1=CIRCLE）
    FullOutline,           # 顯示完整輪廓線
    JaggedOutline,         # 鋸齒輪廓線
    NoOutline,             # 不顯示輪廓線
    ShapeIntensity         # 鋸齒強度 1~5
)
```

回傳 View 物件，失敗回傳 None。

### 列舉值

- swDetViewSTANDARD = 0
- swDetCircleCIRCLE = 1

## Auto Position

父視圖右上方：
- `x = outline[2] + 0.05`（右邊 +50mm）
- `y = outline[3]`（上緣齊平）

## 錯誤處理

- drawing is None → SWError
- 父視圖找不到 → SWError
- CreateCircle 回傳 None → SWError "無法繪製放大區域圓"
- CreateDetailViewAt4 回傳 None → SWError "無法建立局部放大圖"
- scale <= 0 → SWError

## 測試策略

3 個 mock 測試，放在既有的 `tests/test_drawing.py`：

1. 完整 COM 流程 — 驗證呼叫順序、座標轉換、回傳結構
2. auto position — position=None 時驗證放置在父視圖右上方
3. parent view not found — raise SWError

## 範圍限制

- 只支援圓形放大區域（不做 rectangle）
- shape 參數不暴露，固定 swDetCircleCIRCLE
