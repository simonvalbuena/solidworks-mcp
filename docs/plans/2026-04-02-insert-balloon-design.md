# insert_balloon 設計

## 概要

在組立件工程圖的視圖上插入氣球標註。包裝 `IDrawingDoc::AutoBalloon5`。

## 參數

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `view_name` | str | 是 | — | 目標視圖名稱 |
| `component` | str | 否 | None | 指定零件名稱，不指定則標全部 |
| `style` | str | 否 | "circular" | 氣球樣式 |
| `auto_layout` | bool | 否 | true | 自動排列氣球位置 |

### style 對映

| 參數值 | swBalloonStyle_e | 常數值 |
|--------|-----------------|--------|
| circular | swBS_Circular | 1 |
| triangle | swBS_Triangle | 2 |
| hexagon | swBS_Hexagon | 4 |

### auto_layout 對映

| 參數值 | swBalloonLayoutStyle_e | 常數值 |
|--------|----------------------|--------|
| true | swDetailingBalloonLayout_Right | 4 |
| false | swDetailingBalloonLayout_None | 0 |

## COM 流程

1. `ActivateView(view_name)` 啟動目標視圖
2. `SelectByID2(view_name, "DRAWINGVIEW", ...)` 選取視圖
3. `AutoBalloon5(layout, ...)` 建立氣球標註
   - `UpperTextContent` = 1（Item Number）
   - `IgnoreHiddenParts` = True
   - `InsertMagneticLine` = False
4. 遍歷回傳的 Note 陣列，取得零件名稱與 item number
5. 若指定 `component`，刪除不相關的氣球

## 回傳

```json
{
  "status": "done",
  "balloon_count": 5,
  "balloons": [
    { "component": "bracket-1", "item_number": "1" },
    { "component": "shaft-1", "item_number": "2" }
  ]
}
```

## 程式碼位置

- 工具註冊：`src/tools/annotation.py`
- 測試：`tests/test_balloon.py`

## 錯誤處理

- 找不到 view_name → SWError
- 無效 style → SWError
- AutoBalloon5 回傳 None（視圖沒有元件）→ balloon_count: 0
- component 過濾後全部刪除 → balloon_count: 0

## 測試案例

1. style/layout 參數對映正確性
2. component 過濾：全標 5 個，指定後剩 2 個
3. 找不到 view → SWError
4. AutoBalloon5 回傳 None → 空結果
5. 無效 style → SWError
