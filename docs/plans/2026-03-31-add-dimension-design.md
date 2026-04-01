# add_dimension 設計文件

## 概要

Phase 3 標註類第一個 tool：手動新增線性尺寸標註。搭配強化版 probe_drawing_edges 提供邊線座標，Claude 從 probe 結果選取目標邊線。

## 範圍

- 第一版僅支援 `linear`（線性尺寸，兩條邊線之間的距離）
- 不做公差（tolerance）
- 後續再擴充 diameter / radius / angle / ordinate

## 設計決策

### 邊線選取策略：混合匹配（索引 + 座標近鄰）

兩步驟工作流：
1. 呼叫 probe_drawing_edges 取得結構化邊線清單
2. 呼叫 add_dimension 傳入邊線索引 + 座標

匹配邏輯：索引優先，座標校驗，不吻合時 fallback 到近鄰匹配。

## probe_drawing_edges 強化

從「診斷工具」升級為「正式邊線查詢工具」。回傳結構：

```json
{
  "index": 0,
  "type": "line | circle | arc",
  "start": {"x": 10.5, "y": 20.3},
  "end": {"x": 50.1, "y": 20.3},
  "midpoint": {"x": 30.3, "y": 20.3},
  "length": 39.6,
  "params": {}
}
```

- 保持向下相容，新增欄位不破壞現有流程
- view_name 參數篩選特定視圖

## add_dimension 工具介面

```python
add_dimension(
    view_name: str,              # 目標視圖名稱
    edge1: dict,                 # {"index": 0, "x": 10.5, "y": 20.3}
    edge2: dict,                 # {"index": 3, "x": 50.1, "y": 20.3}
    text_position: dict = None,  # {"x": 30.0, "y": 25.0} 選填
)
```

### 內部流程

1. `GetVisibleEntities2()` 取邊線清單
2. 用 `index` 取出候選邊線
3. 驗證候選邊線座標與傳入 `x/y` 是否吻合（容差 0.5mm）
4. 吻合 → 用該邊線；不吻合 → fallback 近鄰匹配
5. 兩條邊線定位後，`SelectEntity` 選取（第二條帶 Ctrl 多選）
6. `AddDimension2(text_x, text_y, 0)` 放置線性尺寸
7. 回傳 `{"dimension_name": "...", "value": 39.6, "position": {...}, "match_method_edge1": "index", "match_method_edge2": "proximity"}`

### 設計決策

- `text_position` 不給時自動放在兩邊線中點偏移 offset 處
- 容差 0.5mm（meter→mm 浮點誤差範圍）
- fallback 最近距離 > 2mm 直接報錯
- 回傳附帶 match_method 讓 Claude 判斷匹配可信度

## 邊線匹配核心邏輯

三個內部函式：

```python
def _match_edge_by_index(edges, index, x, y, tolerance=0.5):
    """索引優先。回傳 (edge, "index") 或 (None, "fallback")"""

def _match_edge_by_proximity(edges, x, y, max_distance=2.0):
    """近鄰 fallback。最近 > max_distance 拋 SWError"""

def _resolve_edge(edges, edge_spec):
    """統一入口：index → proximity fallback"""
```

## 測試策略

### 單元測試（純邏輯，不需 SolidWorks）

- `_match_edge_by_index`：命中、座標偏移超容差、index 超出範圍
- `_match_edge_by_proximity`：正常匹配、取最近、全超過 max_distance 拋錯
- `_resolve_edge`：index 成功、fallback 成功、兩者失敗
- probe 回傳結構完整性驗證

### 整合測試（mock COM）

- probe → add_dimension 兩步驟流程驗證
- SelectEntity 呼叫兩次（第二次帶 Ctrl）
- AddDimension2 參數正確
- text_position 預設值計算
- fallback 觸發時回傳 match_method: "proximity"

預估 12-15 test cases。

## COM API 參考

- `IView::GetVisibleEntities2()` — 取可見邊線
- `IModelDocExtension::SelectByID2` / `SelectEntity` — 選取邊線
- `IModelDoc2::AddDimension2(x, y, z)` — 新增尺寸
- `IDisplayDimension` — 尺寸顯示屬性
