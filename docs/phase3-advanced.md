# Phase 3 PRD — 進階視圖與標註

## 目標

補完工程圖的進階功能：剖面圖、局部放大圖、自訂角度視圖、手動尺寸標註、氣球標註、BOM 表。Phase 3 完成後，系統可產出接近工程師手動出圖品質的工程圖草稿。

## 前置條件

- Phase 1 + Phase 2 全部完成並驗收通過
- 已有多組不同複雜度的測試零件

## MCP Tools

### 1. insert_custom_view

插入自訂角度的視圖。用於規則引擎或 AI 判斷需要非標準方向的視圖。

- 參數：
  - `source_doc: str` — 來源文件路徑
  - `orientation: dict` — 視角方向 `{"x": float, "y": float, "z": float}`（旋轉角度，度）
  - `position: dict`（選填）— 視圖在 Drawing 上的位置 `{"x": float, "y": float}`（mm）
  - `scale: float`（選填）— 視圖比例
  - `display_mode: str`（選填）— 顯示模式（wireframe / hidden_lines_removed / shaded），預設 hidden_lines_removed
- 回傳：視圖名稱、實際位置
- COM API：
  - `IDrawingDoc::CreateDrawViewFromModelView3` — 使用自訂 orientation
  - `IView::SetDisplayMode`

### 2. insert_section_view

插入剖面圖。

- 參數：
  - `parent_view: str` — 父視圖名稱（在哪個視圖上切剖面）
  - `section_line: dict` — 剖面線定義
    - `start: {"x": float, "y": float}` — 起點（在父視圖座標中，mm）
    - `end: {"x": float, "y": float}` — 終點
  - `label: str`（選填）— 剖面標記（A-A, B-B），預設自動遞增
  - `position: dict`（選填）— 剖面圖在 Drawing 上的位置
  - `scale: float`（選填）— 剖面圖比例，預設與父視圖相同
- 回傳：剖面圖視圖名稱、剖面標記
- COM API：
  - `IDrawingDoc::CreateSectionView` — 建立剖面圖
  - 需先用 `ISketchManager` 繪製剖面線

### 3. insert_detail_view

插入局部放大圖。

- 參數：
  - `parent_view: str` — 父視圖名稱
  - `center: {"x": float, "y": float}` — 放大區域中心點（在父視圖座標中，mm）
  - `radius: float` — 放大區域半徑（mm）
  - `scale: float`（選填）— 放大比例，預設 2:1
  - `label: str`（選填）— 局部圖標記（A, B），預設自動遞增
  - `position: dict`（選填）— 局部圖在 Drawing 上的位置
  - `shape: str`（選填）— 放大區域形狀（circle / rectangle），預設 circle
- 回傳：局部圖視圖名稱、放大標記、實際比例
- COM API：
  - `IDrawingDoc::CreateDetailViewAt4` — 建立局部放大圖

### 4. add_dimension

手動新增尺寸標註。用於補充 insert_model_dimensions 未能自動匯入的尺寸。

- 參數：
  - `view_name: str` — 目標視圖名稱
  - `dimension_type: str` — 尺寸類型：
    - `linear` — 線性尺寸（兩點距離）
    - `diameter` — 直徑
    - `radius` — 半徑
    - `angle` — 角度
    - `ordinate` — 座標尺寸
  - `entities: list` — 標註目標（邊線、點、面的識別資訊）
    - 線性尺寸：兩條邊線或兩個點
    - 直徑/半徑：一條弧線或圓
    - 角度：兩條邊線
  - `position: {"x": float, "y": float}`（選填）— 尺寸文字位置
  - `tolerance: dict`（選填）— 公差 `{"type": "bilateral", "upper": 0.1, "lower": -0.1}`
- 回傳：尺寸名稱、尺寸值、位置
- COM API：
  - `IModelDoc2::AddDimension2` — 新增尺寸
  - `IDisplayDimension` — 設定尺寸顯示屬性
  - 需先用 `IModelDoc2::Extension::SelectByID2` 選取目標邊線
- 注意：邊線識別是最大挑戰，需透過座標 + 幾何特徵定位

### 5. insert_balloon

插入氣球標註（組立件 Drawing 用）。

- 參數：
  - `view_name: str` — 目標視圖名稱
  - `component: str`（選填）— 指定零件名稱，不指定則對視圖中所有零件標註
  - `style: str`（選填）— 氣球樣式（circular / triangle / hexagon），預設 circular
  - `auto_layout: bool`（選填）— 自動排列氣球位置，預設 true
- 回傳：氣球數量、各氣球對應的零件與編號
- COM API：
  - `IDrawingDoc::AutoBalloon5` — 自動氣球標註
  - `INote` — 個別氣球操作

### 6. insert_bom_table

插入 BOM 表（組立件 Drawing 用）。

- 參數：
  - `view_name: str` — 目標視圖名稱（組立件視圖）
  - `position: {"x": float, "y": float}`（選填）— BOM 表位置，預設右上角
  - `bom_type: str`（選填）— BOM 類型（top_level / indented / parts_only），預設 top_level
  - `columns: list[str]`（選填）— 顯示欄位，預設 `["item_no", "part_number", "description", "qty"]`
  - `template_path: str`（選填）— BOM 模板路徑
- 回傳：BOM 表內容（零件清單）、行列數
- COM API：
  - `IDrawingDoc::InsertBomTable3` — 插入 BOM 表
  - `IBomTableAnnotation` — BOM 表操作
  - `ITableAnnotation` — 通用表格操作

## 進階使用情境

### 情境 A：零件有內部結構需要剖面圖

```
AI 在 Phase 2 分析後發現零件有內孔/內部腔體特徵：
1. insert_standard_views → 基本三視圖
2. insert_section_view(parent_view="Front", section_line=沿中心線切)
3. insert_model_dimensions
4. capture_drawing → AI 確認剖面圖是否揭露了重要內部結構
```

### 情境 B：細小特徵需要局部放大

```
AI 從截圖發現某處有密集的小孔或倒角：
1. insert_standard_views → 基本三視圖
2. insert_detail_view(parent_view="Front", center=小孔位置, scale=4.0)
3. add_dimension → 在局部圖上標註小特徵尺寸
4. capture_drawing → 確認
```

### 情境 C：組立件出圖

```
1. Phase 2 分析組立件
2. create_drawing
3. insert_standard_views（來源為 .sldasm）
4. insert_balloon(auto_layout=true)
5. insert_bom_table
6. capture_drawing → AI 確認氣球與 BOM 對應是否正確
```

## 驗收標準

- [ ] 剖面圖可正確切割並顯示內部結構
- [ ] 局部放大圖可正確放大指定區域
- [ ] 自訂角度視圖方向正確
- [ ] 手動標註尺寸可準確定位到目標邊線
- [ ] 氣球標註與零件對應正確
- [ ] BOM 表內容與組立件零件清單一致
- [ ] 完整流程（分析 → 出圖 → 進階標註 → 輸出）可走通
