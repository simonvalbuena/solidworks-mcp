# Phase 2 PRD — 組立件分析

## 目標

從 .sldasm 讀取零件的配合關係與幾何資訊，讓 AI 能理解零件在組立件中的角色，進而自動判斷最適視圖配置。Phase 2 建立在 Phase 1 之上，補完工作流程中「分析 → 決策」的環節。

## 前置條件

- Phase 1 全部完成並驗收通過
- 已有至少一組測試用 .sldasm + .sldprt

## 工作流程（Phase 2 完成後的完整流程）

```
1. open_document(.sldasm)
2. read_assembly_mates(target_part)     → 取得配合關係清單
3. get_mating_faces(target_part)        → 取得配合面幾何資訊
4. capture_view(angles)                 → 多角度截圖
5. [規則引擎] 根據配合面法向量決定必要視圖方向
6. [AI 輔助] 看截圖 + 配合資料，微調視圖選擇
7. open_document(.sldprt)
8. create_drawing()
9. insert_standard_views(views=規則引擎+AI 決定的視圖)
10. insert_model_dimensions()
11. capture_drawing() → AI 確認
12. save_as_pdf() + save_drawing()
```

## MCP Tools

### 1. read_assembly_mates

讀取指定零件在組立件中的所有配合關係。

- 參數：
  - `part_name: str` — 目標零件名稱（在組立件中的 component name）
- 回傳：
  ```json
  {
    "part_name": "bracket-1",
    "total_mates": 5,
    "mates": [
      {
        "name": "Coincident1",
        "type": "coincident",
        "entities": [
          {"component": "bracket-1", "face_type": "planar", "normal": [0, 0, 1]},
          {"component": "base_plate-1", "face_type": "planar", "normal": [0, 0, -1]}
        ]
      },
      {
        "name": "Concentric1",
        "type": "concentric",
        "entities": [
          {"component": "bracket-1", "face_type": "cylindrical", "axis": [0, 1, 0]},
          {"component": "shaft-1", "face_type": "cylindrical", "axis": [0, 1, 0]}
        ]
      }
    ]
  }
  ```
- COM API：
  - `IAssemblyDoc::GetMates` — 取得配合清單
  - `IMate2::GetMateEntity` — 取得配合實體
  - `IMateEntity::EntityParams` — 取得幾何參數
- 配合類型對應：coincident, concentric, distance, angle, tangent, lock, width, gear, rack_pinion, cam, slot, universal_joint, hinge, screw, path, linear_coupler, symmetric

### 2. get_mating_faces

取得指定零件配合面的詳細幾何資訊，供規則引擎判斷視圖方向。

- 參數：
  - `part_name: str` — 目標零件名稱
- 回傳：
  ```json
  {
    "part_name": "bracket-1",
    "mating_faces": [
      {
        "mate_name": "Coincident1",
        "face_type": "planar",
        "normal": [0, 0, 1],
        "area_mm2": 1250.5,
        "center": [50.0, 25.0, 0.0],
        "partner": "base_plate-1"
      },
      {
        "mate_name": "Concentric1",
        "face_type": "cylindrical",
        "axis": [0, 1, 0],
        "radius_mm": 5.0,
        "center": [30.0, 0.0, 15.0],
        "partner": "shaft-1"
      }
    ],
    "bounding_box": {
      "min": [0, 0, 0],
      "max": [100, 50, 30]
    }
  }
  ```
- COM API：
  - `IFace2::GetNormal` — 面法向量
  - `IFace2::GetArea` — 面積
  - `IFace2::IGetSurface` → `ISurface` — 曲面類型與參數
  - `IBody2::GetBodyBox` — 包圍盒

### 3. get_feature_tree

讀取零件的特徵樹，讓 AI 理解零件建模結構。

- 參數：
  - `doc_name: str`（選填）— 文件名稱，預設為目前活動文件
  - `depth: int`（選填）— 展開深度，預設全部
- 回傳：
  ```json
  {
    "doc_name": "bracket.sldprt",
    "features": [
      {"name": "Extrude1", "type": "extrusion", "suppressed": false},
      {"name": "Cut-Extrude1", "type": "cut", "suppressed": false},
      {"name": "Fillet1", "type": "fillet", "suppressed": false},
      {"name": "Mirror1", "type": "mirror", "suppressed": false},
      {"name": "Hole1", "type": "hole_wizard", "suppressed": false}
    ]
  }
  ```
- COM API：
  - `IModelDoc2::FirstFeature` → `IFeature::GetNextFeature` — 遍歷特徵
  - `IFeature::GetTypeName2` — 特徵類型
  - `IFeature::IsSuppressed` — 是否被壓抑

### 4. capture_view

從指定角度截取 3D 模型截圖，回傳 base64 圖片。

- 參數：
  - `angles: list[str]`（選填）— 預設 `["front", "back", "top", "bottom", "left", "right", "isometric"]`，也可傳自訂角度 `{"x": 30, "y": 45, "z": 0}`
  - `doc_name: str`（選填）— 文件名稱，預設為目前活動文件
- 回傳：MCP image content 陣列（每個角度一張 base64 PNG）
- COM API：
  - `IModelView::RotateAbsoluteCenter` — 旋轉視角
  - `IModelDoc2::ViewZoomtofit2` — 自動縮放
  - `IModelDoc2::SaveBMP` — 截圖
- 注意：每張截圖壓縮至 ~200KB 以內，避免多張回傳時超過 MCP 訊息大小限制

## 視圖判斷規則引擎

Phase 2 的核心邏輯——根據配合關係自動判斷應該出哪些視圖。

### 規則（優先序由高到低）

1. **配合面法向量 → 必要視圖**
   - 有 Z 軸配合面（normal ≈ [0,0,±1]）→ 必出前視圖
   - 有 Y 軸配合面（normal ≈ [0,±1,0]）→ 必出俯視圖
   - 有 X 軸配合面（normal ≈ [±1,0,0]）→ 必出右視圖

2. **同心配合 → 補視圖**
   - 同心軸方向的正交視圖必出（看到圓孔的那個面）

3. **最大配合面 → 主視圖**
   - 面積最大的配合面方向作為主視圖（前視圖）方向

4. **預設保底**
   - 不論規則結果如何，至少出 front + 一個正交視圖 + isometric

### AI 輔助微調

規則引擎產出初步視圖清單後，連同 capture_view 截圖一起交給 AI：
- AI 可增加視圖（看到有重要特徵被遮擋）
- AI 可建議剖面圖位置（Phase 3 才實作）
- AI 不應刪除規則引擎判定的必要視圖

## 驗收標準

- [ ] 可正確讀取組立件中指定零件的所有配合關係
- [ ] 配合面幾何資訊（法向量、面積）數值正確
- [ ] 特徵樹可正確讀取且類型辨識準確
- [ ] 多角度截圖可正確回傳 base64
- [ ] 規則引擎可根據配合面自動產出合理的視圖清單
- [ ] 完整流程（asm 分析 → prt 出圖）可走通
