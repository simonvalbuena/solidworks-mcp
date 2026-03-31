# capture_view Implementation Plan

Goal: 在 export.py 新增 capture_view tool，從 3D 模型截取多角度標準視圖截圖存 SMB。

Architecture: 追加到現有 `src/tools/export.py`，複用 `_bmp_to_jpeg`、`_server_to_client_path` 等現有函式。ShowNamedView2 設視角 → ViewZoomtofit2 → SaveBMP → JPEG → SMB。

Tech Stack: pywin32 COM, PIL/Pillow, FastMCP

---

### Task 1: 常數與驗證函式 + 測試

Files:
- Modify: `src/tools/export.py:1-3`（新增常數和驗證函式）
- Modify: `tests/test_export_logic.py`（新增測試）

Step 1: 在 `src/tools/export.py` 頂部（`SW_SAVE_AS_CURRENT_VERSION` 之前）新增常數和驗證函式

在 `logger = logging.getLogger(__name__)` 之後、`SW_SAVE_AS_CURRENT_VERSION` 之前插入：

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

DEFAULT_VIEWS = ["front", "right", "top", "isometric"]

SW_DOC_DRAWING = 3


def validate_views(views: list[str]) -> tuple[list[str], list[str]]:
    """驗證 view 名稱，回傳 (valid, invalid)。"""
    valid = []
    invalid = []
    for v in views:
        name = v.strip().lower()
        if name in STANDARD_VIEWS:
            if name not in valid:
                valid.append(name)
        else:
            invalid.append(v)
    return valid, invalid
```

Step 2: 在 `tests/test_export_logic.py` 追加測試

修改 import 區塊，新增匯入：

```python
try:
    from tools.export import (
        should_use_base64,
        STANDARD_VIEWS,
        DEFAULT_VIEWS,
        validate_views,
    )
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False
```

在檔案末尾追加：

```python
class TestStandardViews:

    def test_all_nine_views_present(self):
        expected = {"front", "back", "top", "bottom", "left", "right",
                    "isometric", "dimetric", "trimetric"}
        assert set(STANDARD_VIEWS.keys()) == expected

    def test_enum_values_are_unique(self):
        values = list(STANDARD_VIEWS.values())
        assert len(values) == len(set(values))

    def test_enum_values_range(self):
        for name, val in STANDARD_VIEWS.items():
            assert 1 <= val <= 9, f"{name} has invalid enum value {val}"


class TestDefaultViews:

    def test_default_views_are_valid(self):
        for v in DEFAULT_VIEWS:
            assert v in STANDARD_VIEWS

    def test_default_views_count(self):
        assert len(DEFAULT_VIEWS) == 4


class TestValidateViews:

    def test_all_valid(self):
        valid, invalid = validate_views(["front", "top", "isometric"])
        assert valid == ["front", "top", "isometric"]
        assert invalid == []

    def test_mixed_valid_invalid(self):
        valid, invalid = validate_views(["front", "diagonal", "top"])
        assert valid == ["front", "top"]
        assert invalid == ["diagonal"]

    def test_all_invalid(self):
        valid, invalid = validate_views(["xxx", "yyy"])
        assert valid == []
        assert invalid == ["xxx", "yyy"]

    def test_empty_list(self):
        valid, invalid = validate_views([])
        assert valid == []
        assert invalid == []

    def test_case_insensitive(self):
        valid, invalid = validate_views(["Front", "TOP", "Isometric"])
        assert valid == ["front", "top", "isometric"]
        assert invalid == []

    def test_deduplication(self):
        valid, invalid = validate_views(["front", "front", "top"])
        assert valid == ["front", "top"]
        assert invalid == []

    def test_whitespace_trimmed(self):
        valid, invalid = validate_views(["  front  ", "top"])
        assert valid == ["front", "top"]
        assert invalid == []
```

Step 3: 跑測試確認通過

Run: `.venv/Scripts/pytest tests/test_export_logic.py -v`
Expected: PASS（原有 5 + 新增 12 = 17 tests）

Step 4: Commit

```
feat: capture_view 常數與 validate_views 驗證函式
```

---

### Task 2: capture_view COM 函式與 tool handler

Files:
- Modify: `src/tools/export.py`（register_tools 內新增 tool handler + 新增 _capture_view 函式）

Step 1: 在 `register_tools` 函式內（`save_drawing` 之後）新增 tool handler

```python
    @mcp.tool()
    async def capture_view(
        views: list[str] | None = None,
        doc_name: str | None = None,
        resolution: str = "low",
    ) -> str:
        """從 3D 模型截取多角度標準視圖截圖。
        views: 視圖名稱列表，預設 front/right/top/isometric。
           可用值: front, back, top, bottom, left, right, isometric, dimetric, trimetric。
        doc_name: 文件名稱，預設活動文件。
        resolution: low（800px）/ high（2000px）。
        回傳每個視圖的截圖路徑。"""
        try:
            result = await sw.execute(
                _capture_view,
                views if views is not None else DEFAULT_VIEWS,
                doc_name,
                resolution,
            )
            return json.dumps(result, ensure_ascii=False)
        except SWError as e:
            raise ToolError(f"capture_view 失敗: {e}")
```

Step 2: 在 `_capture_drawing` 函式之前新增 `_capture_view` 函式

```python
def _capture_view(views: list[str], doc_name: str | None, resolution: str) -> dict:
    """COM 操作：多角度 3D 模型截圖。"""
    sw_conn = SWConnection.get_instance()

    # --- 取文件 ---
    if doc_name is None:
        doc = sw_conn.get_active_doc()
    else:
        app = sw_conn.get_app()
        docs = app.GetDocuments
        if docs is None:
            raise SWError(f"找不到文件: {doc_name}")
        found = None
        for d in docs:
            title = d.GetTitle
            path_name = d.GetPathName
            basename = os.path.basename(path_name) if path_name else ""
            if title == doc_name or basename == doc_name:
                found = d
                break
        if found is None:
            raise SWError(f"找不到文件: {doc_name}")
        doc = found

    # --- 確認非 Drawing ---
    if doc.GetType == SW_DOC_DRAWING:
        raise SWError("capture_view 不適用 Drawing，請用 capture_drawing")

    # --- 驗證 views ---
    valid_views, invalid_views = validate_views(views)
    if not valid_views:
        raise SWError(f"沒有有效的視圖名稱: {views}")

    # --- 文件名稱 ---
    path_name = doc.GetPathName
    if path_name:
        doc_display_name = os.path.basename(path_name)
        name_stem = os.path.splitext(doc_display_name)[0]
    else:
        doc_display_name = doc.GetTitle
        name_stem = doc_display_name.replace(" ", "_")

    width = 800 if resolution == "low" else 2000
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    captures = []
    tmp_dir = tempfile.mkdtemp(prefix="sw_mcp_view_")

    try:
        for view_name in valid_views:
            view_enum = STANDARD_VIEWS[view_name]
            try:
                doc.ShowNamedView2("", view_enum)
                doc.ViewZoomtofit2()

                bmp_path = os.path.join(tmp_dir, f"{view_name}.bmp")
                jpeg_path = os.path.join(tmp_dir, f"{view_name}.jpg")

                doc.SaveBMP(bmp_path, width, 0)

                if not os.path.exists(bmp_path):
                    captures.append({"view": view_name, "error": "SaveBMP 未產生檔案"})
                    continue

                _bmp_to_jpeg(bmp_path, jpeg_path)

                smb_filename = f"{name_stem}_{view_name}_{timestamp}.jpg"
                smb_path = os.path.join(config.SMB_SHARE_PATH, smb_filename)
                os.makedirs(config.SMB_SHARE_PATH, exist_ok=True)
                shutil.copy2(jpeg_path, smb_path)

                client_path = _server_to_client_path(smb_path)
                captures.append({
                    "view": view_name,
                    "path": client_path,
                    "size_bytes": os.path.getsize(smb_path),
                })
            except Exception as e:
                logger.warning("capture_view %s failed: %s", view_name, e)
                captures.append({"view": view_name, "error": str(e)})

        # 還原視角到 isometric
        try:
            doc.ShowNamedView2("", STANDARD_VIEWS["isometric"])
            doc.ViewZoomtofit2()
        except Exception:
            pass

        # 加入無效 view 名稱
        for inv in invalid_views:
            captures.append({"view": inv, "error": f"unknown view: {inv}"})

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    success_count = sum(1 for c in captures if "path" in c)
    if success_count == 0:
        raise SWError("所有視圖截圖都失敗")

    return {
        "doc_name": doc_display_name,
        "total_views": success_count,
        "captures": captures,
    }
```

Step 3: 跑測試確認沒破壞既有功能

Run: `.venv/Scripts/pytest tests/ -v`
Expected: 全部 PASS

Step 4: Commit

```
feat: capture_view — 3D 模型多角度標準視圖截圖
```

Step 5: 部署到 SW 主機實機測試

Run: `bash deploy.sh`

實機測試項目：
1. **ShowNamedView2 enum 值驗證** — 逐一呼叫 9 個 view，確認截圖方向正確
2. ViewZoomtofit2 後模型是否完整
3. JPEG 壓縮大小是否合理（< 300KB）
4. 截完後視角是否回到 isometric
5. Drawing 文件呼叫時是否正確回傳錯誤
