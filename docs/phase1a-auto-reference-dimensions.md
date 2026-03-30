# PRD: `auto_add_reference_dimensions` Tool

## Context

現有的 `insert_model_dimensions` 依賴 `InsertModelAnnotations3` 匯入模型的參數化草圖尺寸。但模具零件（Cavity 建模）、匯入幾何（STEP/IGES）、曲面建模零件等，模型內沒有可匯入的尺寸，API 回傳 0 annotations。

解決方案：用 SolidWorks API 取得工程圖視圖中的可見邊線，程式化選取邊線後加上**參考尺寸**。

## Goals

- G1: 自動標註整體外形尺寸（長 x 寬 x 高）— 找最外邊線配對
- G2: 自動標註圓形特徵（孔徑）— 找圓弧邊線加直徑尺寸
- G3: 回傳結構化結果供 LLM 確認或調整
- G4: 與現有 `insert_standard_views` / `insert_standard_views_aligned` 相容

## Non-Goals

- 複雜特徵尺寸（雲形線、倒角角度、螺紋標註）— 未來擴充
- 公差 / GD&T 標註
- 修改 `insert_model_dimensions` 已放置的尺寸

## Probe 驗證結果（2026-03-27）

用 `probe_drawing_edges` 工具對 Papilla 上模具實測，確認 pywin32 COM 環境下各 API 的可用性。

### pywin32 存取規則

SolidWorks COM 在 pywin32 EnsureDispatch 下，`ActiveDoc` 回傳 `IModelDoc2` 介面。
`IDrawingDoc` 專屬方法（如 `GetFirstView`）無法呼叫（DISP_E_MEMBERNOTFOUND）。
但 `IModelDoc2` 上的方法（如 `ActivateView`、`FirstFeature`）可用。

**COM 屬性/方法存取規則**（實測驗證）：

| 物件 | 屬性（直接存取） | 方法（加括號呼叫） |
|------|-----------------|-------------------|
| IModelDoc2.FirstFeature | 屬性 | - |
| IFeature.GetNextFeature | 屬性 | - |
| IFeature.GetTypeName2 | 屬性 | - |
| IFeature.Name | 屬性 | - |
| IFeature.GetFirstSubFeature | - | 方法() |
| IFeature.GetNextSubFeature | - | 方法() |
| IFeature.GetSpecificFeature2 | - | 方法() |
| IView.GetVisibleComponents | 屬性 | - |
| IView.GetVisibleEntities2 | - | 方法(comp, type) |
| IView.GetOutline | 屬性 | - |
| IView.ModelToViewTransform | 屬性（回傳 None） | - |
| IView.SelectEntity | - | 方法(edge, append) |
| IEdge.GetCurve | 屬性(dispatch) | - |
| IEdge.GetStartVertex | 屬性 | - |
| IEdge.GetEndVertex | 屬性 | - |
| ICurve.IsLine | 屬性 | - |
| ICurve.IsCircle | 屬性 | - |
| ICurve.LineParams | 屬性 | - |
| ICurve.CircleParams | 屬性 | - |
| IVertex.GetPoint | 屬性 | - |

### 取得 IView 物件的方式

`IDrawingDoc.GetFirstView` 不可用。改用 FeatureTree 遍歷：

```python
feat = drawing.FirstFeature          # 屬性
while feat is not None:
    if feat.GetTypeName2 == "DrSheet":
        sub = feat.GetFirstSubFeature()   # 方法
        while sub is not None:
            if sub.GetTypeName2 in ("AbsoluteView", "UnfoldedView"):
                view_obj = sub.GetSpecificFeature2()  # 方法 → IView
            sub = sub.GetNextSubFeature()  # 方法
    feat = feat.GetNextFeature        # 屬性
```

視圖 Feature TypeName：
- `AbsoluteView` — 主視圖（Create1stAngleViews2 的第一個）
- `UnfoldedView` — 投影視圖（第二、三個）

### GetVisibleEntities2 使用方式

第一參數必須傳入 component 物件，不能傳 None：

```python
comps = view_obj.GetVisibleComponents    # 屬性，回傳 tuple
edges = view_obj.GetVisibleEntities2(comps[0], 1)  # swViewEntityType_Edge=1
```

實測：前視圖回傳 44 條邊線。

### 邊線資料範例

```
Edge 0: IsLine=True,  LineParams=[0.038, -0.030, 0.005, 0, -1, 0]
         Start=[0.038, -0.013, 0.005] End=[0.038, -0.017, 0.005]  ← 垂直線
Edge 2: IsCircle=True, CircleParams=[0, -0.017, 0, 0, -1, 0]
         Start=[0.038, -0.017, 0.005] End=[-0.037, -0.017, 0.009] ← 圓弧
Edge 3: IsCircle=True, CircleParams=[0, -0.022, 0, 0, 1, 0]
         Start=None End=None                                       ← 完整圓
```

### 座標轉換

`ModelToViewTransform` 回傳 None，無法使用。
改用 `GetOutline`（屬性）取得視圖邊界 `[xMin, yMin, xMax, yMax]`（圖紙公尺），
搭配模型座標的相對位置來定位尺寸文字。

### SelectEntity

`view_obj.SelectEntity(edge, False)` 可用，回傳 True。
確認可以直接選取 edge 物件，不需要用座標選取。

## 分階段實作

### Phase 1: 整體外形尺寸（bbox）

1. 透過 FeatureTree 取得 IView 物件
2. `view.GetVisibleComponents`（屬性）取得 component
3. `view.GetVisibleEntities2(comp, 1)` 取得可見邊線
4. `edge.GetCurve`（屬性）→ `curve.IsLine`（屬性）過濾直線
5. `edge.GetStartVertex`/`GetEndVertex`（屬性）→ `vertex.GetPoint`（屬性）取得座標
6. 在 model space 分類水平/垂直線，找最外對
7. `view.SelectEntity(edge, False)` 選取邊線
8. `drawing.Extension.AddDimension(x, y, 0, direction)` 放置尺寸
9. 用 `view.GetOutline`（屬性）定位尺寸文字在視圖外

結果：每個正投影視圖最多 2 個尺寸（水平 + 垂直範圍）

### Phase 2: 圓形特徵尺寸

1. 同上取得邊線，用 `curve.IsCircle`（屬性）過濾
2. `curve.CircleParams`（屬性）取得圓心和半徑
3. 完整圓：`GetStartVertex` 回傳 None → 選取 edge 後 `AddDimension` 自動建立直徑尺寸
4. 依圓心距離去重（容差 0.1mm）

結果：每個可見完整圓一個直徑尺寸

## 邊線分類邏輯

座標來自 model space（公尺），分類用 `LineParams` 的方向向量判斷。

| 幾何類型 | 偵測方式 | 尺寸類型 |
|---------|---------|---------|
| 水平線 | `IsLine` + 方向向量 dy≈0 且 dz≈0 | 垂直範圍（配對對面水平線）|
| 垂直線 | `IsLine` + 方向向量 dx≈0 且 dz≈0 | 水平範圍（配對對面垂直線）|
| 完整圓 | `IsCircle` + GetStartVertex=None | 直徑 |
| 圓弧 | `IsCircle` + GetStartVertex≠None | 跳過 |
| 其他 | 非直線非圓 | 跳過 |

容差：1e-6 公尺（0.001mm），方向向量分量門檻 0.1。
注意：前視圖的水平/垂直對應 model 的 X/Y 軸，上視圖對應 X/Z 軸，
需根據視圖類型（AbsoluteView vs UnfoldedView 的投影方向）決定哪兩個軸是「水平/垂直」。

## 尺寸定位策略

- 取得 `view.GetOutline`（屬性）→ `[xMin, yMin, xMax, yMax]`（圖紙公尺）
- 水平範圍尺寸：放視圖下方 `y = yMin - offset`
- 垂直範圍尺寸：放視圖右方 `x = xMax + offset`
- 直徑尺寸：圓心 45 度方向偏移
- 多尺寸堆疊間距 10mm

| 常數 | 預設值 | 說明 |
|------|--------|------|
| `DIM_OFFSET_BASE` | 0.015m | 尺寸文字離視圖邊緣 15mm |
| `DIM_OFFSET_STACK` | 0.010m | 堆疊尺寸間距 10mm |
| `DIM_DIAMETER_OFFSET` | 0.008m | 直徑尺寸離圓弧邊緣 8mm |

## Tool 介面

```python
@mcp.tool()
async def auto_add_reference_dimensions(
    view_name: str | None = None,    # 指定視圖或全部
    phase: str = "all",              # "bbox" / "circles" / "all"
    offset_mm: float = 15.0,         # 尺寸文字偏移量（mm）
) -> str:
```

回傳格式：
```json
{
  "status": "done",
  "views_processed": ["工程視圖1", "工程視圖2", "工程視圖3"],
  "dimensions_added": 8,
  "details": [
    {
      "view": "工程視圖1",
      "bbox_dims": [{"type": "horizontal", "value_mm": 120.0}],
      "circle_dims": [{"type": "diameter", "value_mm": 12.0}],
      "edges_found": {"lines": 24, "circles": 3, "arcs": 5, "other": 2}
    }
  ]
}
```

## 風險與對策（更新）

| 風險 | 狀態 | 對策 |
|------|------|------|
| `GetFirstView` 不可用 | 已確認 | 用 FeatureTree 遍歷取得 IView |
| `GetVisibleEntities2` 第一參數 | 已確認 | 必須傳 component，從 `GetVisibleComponents` 取得 |
| `IEdge` 方法存取方式 | 已確認 | 全部用屬性存取（不加括號） |
| `ModelToViewTransform` 回傳 None | 已確認 | 改用 `GetOutline` + 模型座標相對定位 |
| `SelectEntity` 可用性 | 已確認 | OK，不需要用 `SelectByID2` |
| `AddDimension` 尚未驗證 | 待測試 | Phase 1 實作時第一個驗證 |
| 等角視圖無乾淨水平/垂直線 | 設計決策 | 只處理正投影視圖 |
| 邊線太多 (>200) 導致逾時 | 低風險 | 設上限，只處理外圍邊線 |

## 實作檔案

- `src/tools/annotation.py` — 新增 tool 函式 + 同步 COM helper + 邊線分析 helper
- `tests/test_annotation_ref_dims.py` — 邊線分類、外圍計算、定位邏輯的單元測試

不需修改 `server.py`（annotation tools 已透過 `register_annotation` 註冊）

## 驗證方式

1. 用 Papilla 上模具零件測試：`insert_model_dimensions` 回傳 0 → `auto_add_reference_dimensions` 成功標註
2. `capture_drawing` 截圖確認尺寸位置
3. 用簡單方塊零件驗證 bbox 尺寸數值正確性
4. 用帶孔平板驗證直徑標註
