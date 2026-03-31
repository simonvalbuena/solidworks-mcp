# capture_view — 3D 模型多角度截圖設計

## 概述

Phase 2 第三批：從 Part 或 Assembly 的 3D 模型截取多角度標準視圖截圖，
存到 SMB 共享資料夾回傳路徑，供 AI 用 Read tool 查看後判斷工程圖視圖配置。

## Tool 介面

### capture_view

- 檔案：`src/tools/export.py`（追加到現有模組）
- 參數：
  - `views: list[str]` — 要截的視圖名稱，預設 `["front", "right", "top", "isometric"]`
  - `doc_name: str | None` — 文件名稱，預設活動文件
  - `resolution: str` — `"low"` (800px) / `"high"` (2000px)，預設 `"low"`
- 回傳：

```json
{
  "doc_name": "bracket.sldprt",
  "total_views": 4,
  "captures": [
    {"view": "front", "path": "U:\\mcp-share\\bracket_front_20260331_143000.jpg", "size_bytes": 152340},
    {"view": "right", "path": "U:\\mcp-share\\bracket_right_20260331_143001.jpg", "size_bytes": 148200},
    {"view": "top", "path": "U:\\mcp-share\\bracket_top_20260331_143002.jpg", "size_bytes": 135600},
    {"view": "isometric", "path": "U:\\mcp-share\\bracket_iso_20260331_143003.jpg", "size_bytes": 167800}
  ]
}
```

可用的 view 名稱：`front`, `back`, `top`, `bottom`, `left`, `right`, `isometric`, `dimetric`, `trimetric`

## COM API 路徑

### 視圖列舉

```python
STANDARD_VIEWS = {
    "front":      1,   # swFrontView
    "back":       2,   # swBackView
    "left":       3,   # swLeftView
    "right":      4,   # swRightView
    "top":        5,   # swTopView
    "bottom":     6,   # swBottomView
    "isometric":  7,   # swIsometricView
    "dimetric":   8,   # swDimetricView
    "trimetric":  9,   # swTrimetricView
}
```

> **注意：enum 值需實機驗證，確認每個值對應的視角方向正確。**

### 每個視圖截圖流程

```
1. doc.ShowNamedView2("", view_enum)   # 第一個參數空字串 = 用 enum 值
2. doc.ViewZoomtofit2()                # 縮放至模型填滿畫面
3. doc.SaveBMP(bmp_path, width, 0)     # 截圖存 BMP
4. _bmp_to_jpeg(bmp_path, jpeg_path)   # 轉 JPEG（複用現有函式）
5. shutil.copy2(jpeg_path, smb_path)   # 存到 SMB 共享
```

### 視角還原

截圖前記錄當前視角，全部拍完後 `ShowNamedView2` 還原，避免改動使用者畫面。

### 文件查找

`doc_name` 指定時，複用 `assembly.py` 中 `_get_feature_tree` 的 `app.GetDocuments` 遍歷比對邏輯。

## 錯誤處理

- `views` 含無效名稱 → 跳過該項，回傳 `"error": "unknown view: xxx"`，不中斷整批
- 文件是 Drawing → ToolError（3D 截圖不適用 Drawing）
- `ShowNamedView2` 或 `SaveBMP` 失敗 → 該項標記 error，繼續下一個
- 全部都失敗 → ToolError

## 測試策略

### 純函式測試（pytest，不需 SW）

- `STANDARD_VIEWS` 字典的 key 完整性（9 個標準視圖都在）
- views 參數驗證（過濾無效名稱、空列表處理）
- 預設 views 值正確

### 實機測試（SW 主機手動驗證）

- **重點：`ShowNamedView2` 的 enum 值是否正確對應視角**
- 每個標準視圖截圖方向是否符合預期
- `ViewZoomtofit2` 後模型是否完整顯示
- BMP→JPEG 壓縮後大小是否在合理範圍
- 截圖前後視角是否正確還原
