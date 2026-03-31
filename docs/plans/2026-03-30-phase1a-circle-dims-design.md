# Design: Phase 1a — 圓形特徵尺寸（孔徑標註）

## 概述

在 `auto_add_reference_dimensions` 的 `phase="circles"` / `"all"` 路徑中，偵測工程圖視圖中的完整圓邊線，自動加上直徑參考尺寸。

## 設計決定

1. **同半徑去重**：同一視圖中半徑相同的圓（容差 0.1mm = 1e-4m）只標一個代表
2. **代表選取**：選離視圖中心最遠的圓（尺寸引線不穿過其他特徵）
3. **文字位置**：圓心 45 度方向偏移，就近標註（不統一排列到視圖外側）

## 資料流

```
_get_view_edges(view_obj)
    → _classify_edges(edges)        # 已實作，回傳 circles: [(edge, center, radius), ...]
    → _dedupe_circles(circles, view_outline)   # 新增
        1. 按 radius 分組（容差 1e-4m ≈ 0.1mm）
        2. 每組取離視圖中心最遠的圓
    → _add_circle_dims(drawing, view_obj, deduped, offset)  # 新增
        1. view.SelectEntity(edge, False)
        2. drawing.AddDimension(text_x, text_y, 0)
           text_x = center_x + offset * cos(45°)
           text_y = center_y + offset * sin(45°)
           offset = DIM_DIAMETER_OFFSET (0.008m)
```

## 去重邏輯

```python
def _dedupe_circles(circles, view_outline):
    """同半徑只保留離視圖中心最遠的圓。

    circles: [(edge, (cx,cy,cz), radius), ...]
    view_outline: [xMin, yMin, xMax, yMax]（圖紙公尺）
    回傳: [(edge, (cx,cy,cz), radius), ...]  去重後
    """
    # 視圖中心（用 model space 的 x, y 近似）
    # 注意：center 是 model space，outline 是 sheet space
    # 用 model space 圓心之間的相對距離即可

    RADIUS_TOL = 1e-4  # 0.1mm

    # 1. 按 radius 分組
    groups: dict[float, list] = {}
    for item in circles:
        _, center, r = item
        matched = False
        for key_r in groups:
            if abs(r - key_r) < RADIUS_TOL:
                groups[key_r].append(item)
                matched = True
                break
        if not matched:
            groups[r] = [item]

    # 2. 每組取離幾何重心最遠的
    if not circles:
        return []
    all_cx = sum(c[0] for _, c, _ in circles) / len(circles)
    all_cy = sum(c[1] for _, c, _ in circles) / len(circles)

    result = []
    for r, items in groups.items():
        farthest = max(items, key=lambda it: (it[1][0] - all_cx)**2 + (it[1][1] - all_cy)**2)
        result.append(farthest)

    return result
```

## 尺寸放置

```python
import math

DIM_DIAMETER_OFFSET = 0.008  # 8mm，圖紙公尺

def _add_circle_dims(drawing, view_obj, circles, offset):
    """對每個圓 SelectEntity → AddDimension。"""
    added = []
    outline = view_obj.GetOutline  # [xMin, yMin, xMax, yMax]
    # 視圖中心（圖紙空間）
    vc_x = (outline[0] + outline[2]) / 2
    vc_y = (outline[1] + outline[3]) / 2

    for edge, (cx, cy, cz), radius in circles:
        # SelectEntity
        ok = view_obj.SelectEntity(edge, False)
        if not ok:
            continue

        # 文字位置：圓心在圖紙上的近似位置 + 45° 偏移
        # 因為 ModelToViewTransform 不可用，
        # 用 view outline 中心 + model space 相對偏移近似
        # （與 bbox 相同的近似策略）
        text_x = vc_x + offset * math.cos(math.radians(45))
        text_y = vc_y + offset * math.sin(math.radians(45))
        # 但這樣所有圓的文字會重疊在同一點
        # → 改用圓心相對位置偏移

        # 策略：利用圓心在所有圓中的相對位置，
        #   把文字分散在視圖不同象限
        # 簡化版：直接用 AddDimension 傳 (0,0,0)，
        #   讓 SW 自動放置，然後不調整位置
        # → 實測 bbox 時 AddDimension 的座標是圖紙空間，
        #   需要有效座標才能正確放置

        # 最終策略：圓心偏移
        # 圓心的圖紙座標 ≈ vc + (model_center - model_centroid)
        # 但無法精確轉換，改用 outline 比例映射
        # → 先用固定偏移，實測再調

        dim = drawing.Extension.AddDimension(text_x, text_y, 0, 0)
        # swSmartDimensionDirection_e: 0 = swSmartDimensionDirection_Default
        if dim is not None:
            added.append({
                "type": "diameter",
                "radius_m": radius,
                "value_mm": round(radius * 2 * 1000, 3),
            })
        drawing.ClearSelection2(True)

    return added
```

## 座標轉換問題

`ModelToViewTransform` 回傳 None（Phase 1a PRD 已記錄），所以沒辦法精確把 model space 圓心轉成圖紙座標。

實際策略和 bbox 一樣：用 `GetOutline` 取得視圖邊界，在邊界外圍偏移放置。對直徑尺寸來說，**SelectEntity 選中圓後，AddDimension 會自動建立穿過圓心的直徑尺寸線**，文字位置只是建議，SW 會自動微調到合理位置。

所以簡化為：文字座標用視圖 outline 外側 + 按圓的索引堆疊偏移，不需要精確的圓心圖紙座標。

```
text_x = xMax + DIM_DIAMETER_OFFSET + (i * DIM_OFFSET_STACK)
text_y = (yMin + yMax) / 2
```

每個直徑尺寸的文字往右堆疊，和 bbox 的垂直範圍尺寸共用右側空間但再往外偏移。

## 修改範圍

僅修改 `src/tools/annotation.py`：

1. 新增 `_dedupe_circles(circles)` 函式
2. 新增 `_add_circle_dims(drawing, view_obj, circles, offset, start_index)` 函式
3. 修改 `_auto_add_ref_dims` 中 `phase in ("circles", "all")` 的分支，呼叫上述函式
4. 回傳結構 `circle_dims` 欄位補上實際資料

測試：`tests/test_annotation_ref_dims.py` 新增去重邏輯和代表選取的單元測試。

## 風險

| 風險 | 對策 |
|------|------|
| SelectEntity 對圓形 edge 不確定是否可用 | Phase 1 probe 已驗證 SelectEntity 可用，但只測了直線。需實測圓形 edge |
| AddDimension 對圓形選取是否自動建立直徑尺寸 | 需在 SW 主機實測。若不行，備案是 AddRadialDimension |
| 文字位置堆疊可能和 bbox 尺寸重疊 | start_index 參數從 bbox 已用的數量開始往外偏移 |
