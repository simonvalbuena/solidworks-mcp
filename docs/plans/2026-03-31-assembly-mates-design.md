# read_assembly_mates — 組立件配合分析 Tool 設計

## 概述

Phase 2 第一批：合併原 PRD 的 `read_assembly_mates` + `get_mating_faces` 為單一 tool，
從組立件讀取指定零件的配合關係與配合面幾何資訊。

## Tool 定義

### read_assembly_mates

- 參數：`part_name: str` — 目標零件的 component name
- 檔案：`src/tools/assembly.py`（新增），遵循 `register_tools(mcp, sw)` 模式

### 回傳格式

```json
{
  "part_name": "bracket-1",
  "total_mates": 3,
  "mates": [
    {
      "name": "Coincident1",
      "type": "coincident",
      "entities": [
        {
          "component": "bracket-1",
          "entity_type": "plane",
          "point": [0.0, 0.0, 0.0],
          "vector": [0.0, 0.0, 1.0],
          "radius1": 0.0,
          "radius2": 0.0
        },
        {
          "component": "base_plate-1",
          "entity_type": "plane",
          "point": [0.0, 0.0, 0.0],
          "vector": [0.0, 0.0, -1.0],
          "radius1": 0.0,
          "radius2": 0.0
        }
      ]
    }
  ],
  "bounding_box": {
    "min": [0.0, 0.0, 0.0],
    "max": [100.0, 50.0, 30.0]
  }
}
```

## COM API 路徑

```
1. IModelDoc2 → 確認 GetType == swDocASSEMBLY (2)
2. IAssemblyDoc.GetComponents(false) → IComponent2[]，比對 Name2 找目標
3. IComponent2.GetMates() → IMate2[] (或 IMateInPlace，跳過)
4. 對每個 IMate2：
   a. IMate2.Type → swMateType_e
   b. IMate2.MateEntity(0), MateEntity(1) → IMateEntity2
   c. IMateEntity2.ReferenceComponent.Name2 → component name
   d. IMateEntity2.EntityParams → [pointX, pointY, pointZ, vecI, vecJ, vecK, r1, r2]
   e. IMateEntity2.ReferenceType2 → entity 幾何類型
5. 目標 component 的 GetBody → IBody2.GetBodyBox → bounding_box
```

### EntityParams 解析（依 entity type）

| Entity Type | 有效欄位 |
|---|---|
| swMatePoint | point (xyz) |
| swMateLine | point + vector (方向) |
| swMatePlane | point + vector (法向量) |
| swMateCylinder | point + vector (軸) + radius1 |
| swMateCone | point + vector (軸) + radius1 + radius2 |

### swMateType_e 對應

| 值 | 常數 | 回傳字串 |
|---|---|---|
| 0 | swMateCOINCIDENT | coincident |
| 1 | swMateCONCENTRIC | concentric |
| 2 | swMatePERPENDICULAR | perpendicular |
| 3 | swMatePARALLEL | parallel |
| 4 | swMateTANGENT | tangent |
| 5 | swMateDISTANCE | distance |
| 6 | swMateANGLE | angle |
| 8 | swMateSYMMETRIC | symmetric |
| 16 | swMateLOCK | lock |

其餘類型回傳 `"unknown_<數字>"`。

### 單位

SolidWorks COM 回傳公尺，回傳 JSON 轉為 mm（乘 1000）。

## 錯誤處理

- 活動文件不是 Assembly → ToolError
- part_name 找不到 → ToolError
- 零件無配合 → 正常回傳 `mates: []`
- GetMates 回傳 None → 等同空清單
- IMateInPlace 物件 → 跳過
- bounding_box 取不到 → 省略欄位

## 測試案例

1. 非 Assembly 文件 → ToolError
2. part_name 不存在 → ToolError
3. 零件 0 個配合 → 空清單
4. 混合配合（coincident + concentric）→ 正確解析
5. 單位轉換 m → mm
6. GetMates 含 IMateInPlace → 跳過不報錯
7. bounding_box 失敗 → 省略

## pywin32 注意事項

- EntityParams 可能回傳 tuple 或 SAFEARRAY，需統一處理
- GetComponents/GetMates 回傳 None 時代表空集合
- Component Name2 帶實例號（如 bracket-1），比對用完整名稱
