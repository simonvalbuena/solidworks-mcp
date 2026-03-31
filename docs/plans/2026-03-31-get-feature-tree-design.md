# get_feature_tree — 零件特徵樹 + Bounding Box 設計

## 概述

Phase 2 第二批：從零件/組立件讀取特徵樹和 bounding box，
供 AI 理解零件建模結構，並作為無 Mates 時的 fallback 視圖判斷依據。

## Tool 定義

### get_feature_tree

- 參數：
  - `doc_name: str | None` — 文件名稱，預設活動文件
  - `include_all: bool` — 預設 False（只回傳建模特徵），True 回傳全部
- 檔案：`src/tools/assembly.py`（追加到現有模組）

### 回傳格式

```json
{
  "doc_name": "bracket.sldprt",
  "doc_type": "part",
  "feature_count": 5,
  "features": [
    {"name": "Extrude1", "type": "extrusion", "suppressed": false},
    {"name": "Cut-Extrude1", "type": "cut", "suppressed": false},
    {"name": "Fillet1", "type": "fillet", "suppressed": false},
    {"name": "Mirror1", "type": "mirror", "suppressed": false},
    {"name": "Hole1", "type": "hole_wizard", "suppressed": false}
  ],
  "bounding_box": {
    "min": [0.0, 0.0, 0.0],
    "max": [100.0, 50.0, 30.0]
  }
}
```

## COM API 路徑

```
1. 取文件（指定 doc_name → app.GetDocuments 遍歷比對，或活動文件）
2. IModelDoc2.FirstFeature → GetNextFeature 遍歷
3. 過濾系統資料夾（include_all=False 時）
4. 對每個 Feature：Name, GetTypeName2, IsSuppressed
5. Bounding box：
   - Part: IPartDoc.GetBodies2(0) → IBody2.GetBodyBox
   - Assembly: 遍歷 component bbox 取聯集
   - Drawing: 省略
```

## 特徵過濾

`include_all=False` 時排除的 GetTypeName2 值：

```python
SYSTEM_FEATURE_TYPES = {
    "CommentsFolder", "FavoriteFolder", "HistoryFolder",
    "SelectionSetFolder", "SensorFolder", "LiveSectionFolder",
    "DocsFolder", "DetailCabinet", "EnvFolder",
    "InkMarkupFolder", "EqnFolder", "MaterialFolder",
    "RefPlane", "RefAxis", "OriginProfileFeature",
    "MateGroup", "Reference",
}
```

## Bounding Box

- Part → `IPartDoc.GetBodies2(0)` 取第一個 visible body → `GetBodyBox`
- Assembly → 遍歷所有 component bbox 取 min/max 聯集
- Drawing → 省略
- 單位：公尺 → mm（乘 1000）
- 取不到 → 省略欄位

## 指定文件查找

`doc_name` 非 None 時：
- `app.GetDocuments` 遍歷已開啟文件
- 比對 `GetTitle` 或檔名部分
- 找不到 → ToolError

## 錯誤處理

- 文件找不到 → ToolError
- 特徵遍歷失敗 → 跳過該特徵
- bbox 取不到 → 省略

## 測試

- `is_system_feature(type_name)` 過濾邏輯
- include_all=True vs False 的過濾行為
- 純函式測試，COM 操作靠實機驗證
