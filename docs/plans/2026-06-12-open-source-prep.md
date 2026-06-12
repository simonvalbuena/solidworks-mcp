# 開源準備 Implementation Plan

Goal: 補齊開源所需的文件（英中 README + banner、LICENSE、CONTRIBUTING）、打包 metadata（pyproject.toml）、CI workflow，更新 CLAUDE.md，並完成轉 public 前的硬編碼掃描。

Architecture: 純文件與設定檔新增，程式碼零改動。執行方式維持 `python src/server.py`，requirements.txt 與離線安裝流程不動。CI 跑 windows-latest（src 頂層 `import pythoncom`，非 Windows import 不起來）。

Tech Stack: Markdown、TOML（PEP 621 metadata）、GitHub Actions

Spec: `docs/specs/open-source-release.md`

Design: `docs/plans/2026-06-12-open-source-prep-design.md`

注意事項（適用所有 task）：
- 本 repo 在 Windows 上開發，所有檔案用 Write/Edit tool 建立（不要用 PowerShell here-string 寫含中文的檔案，cp950 編碼會壞）
- Commit message 用 conventional commits，中文描述
- v0.1.0 git tag 與 GitHub Release 在 PR merge 進 main 後才執行，不在本 plan 範圍
- Repo 轉 public 由維護者手動操作，不在本 plan 範圍

---

### Task 1: LICENSE（MIT）

Implements: `open-source-release.md` #R1

Files:
- Create: `LICENSE`

Step 1: 建立 `LICENSE`，內容如下（標準 MIT 全文，一字不改）：

```
MIT License

Copyright (c) 2026 haunchen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Step 2: 驗證檔案存在且第一行為 `MIT License`
Run: `Get-Content LICENSE -TotalCount 3`
Expected: 第一行 `MIT License`、第三行 `Copyright (c) 2026 haunchen`

Step 3: Commit
Run: `git add LICENSE; git commit -m "docs: 新增 MIT LICENSE"`

---

### Task 2: pyproject.toml（輕量 metadata）

Implements: `open-source-release.md` #R4

Files:
- Create: `pyproject.toml`

Step 1: 建立 `pyproject.toml`，內容如下：

```toml
[project]
name = "solidworks-mcp"
version = "0.1.0"
description = "MCP server for SolidWorks 2021 - drive engineering drawing creation from Claude via COM"
readme = "README.md"
requires-python = ">=3.10"
license = "MIT"
authors = [{ name = "haunchen" }]
keywords = ["mcp", "solidworks", "claude", "com-automation", "cad"]
dependencies = [
    "mcp[server]",
    "pywin32",
    "Pillow",
    "python-dotenv",
]

[project.urls]
Repository = "https://github.com/haunchen/solidworks-mcp"
Issues = "https://github.com/haunchen/solidworks-mcp/issues"
```

注意：刻意不加 `[build-system]` 與 packages 設定 — 本專案不打 wheel、不 pip install 自身，pyproject 只當 metadata 宣告（設計決策 D2）。`requirements.txt` 保留不動（SW 主機離線安裝流程依賴）。

Step 2: 驗證 TOML 語法正確
Run: `.venv/Scripts/python -c "import tomllib; d = tomllib.load(open('pyproject.toml','rb')); print(d['project']['name'], d['project']['version'])"`
Expected: `solidworks-mcp 0.1.0`

Step 3: 跑全測試，確認 pyproject.toml 的存在不影響 pytest（rootdir 偵測改變但 conftest.py 仍生效）
Run: `.venv/Scripts/pytest tests/ -v`
Expected: 144 passed（全綠，數量不減）

Step 4: Commit
Run: `git add pyproject.toml; git commit -m "build: 新增 pyproject.toml 輕量 metadata（v0.1.0）"`

---

### Task 3: Banner 複製進 repo

Implements: `open-source-release.md` #R2

Files:
- Create: `assets/banner-solidworks-mcp.svg`（從 vault 複製）

Step 1: 建立 assets 目錄並複製 banner
Run:
```powershell
New-Item -ItemType Directory -Force assets | Out-Null
Copy-Item "<vault>\solidworks-mcp\banner-solidworks-mcp.svg" assets\banner-solidworks-mcp.svg
```

Step 2: 驗證檔案存在且為 SVG
Run: `Get-Content assets\banner-solidworks-mcp.svg -TotalCount 1`
Expected: 內容含 `<svg` 或 XML 宣告（檔案大小約 5.3KB）

Step 3: Commit
Run: `git add assets/banner-solidworks-mcp.svg; git commit -m "docs: 新增 README banner"`

---

### Task 4: README.md（英文主版）

Implements: `open-source-release.md` #R2, #R3

Files:
- Create: `README.md`

Step 1: 建立 `README.md`，內容如下（完整內容，一字不漏）：

````markdown
![solidworks-mcp banner](assets/banner-solidworks-mcp.svg)

# solidworks-mcp

[![CI](https://github.com/haunchen/solidworks-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/haunchen/solidworks-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

English | [繁體中文](README.zh-TW.md)

An MCP server that lets Claude drive **SolidWorks 2021** to create engineering drawings — over your LAN, through the pywin32 COM API.

Open a part, generate first-angle standard views, add dimensions and balloons, insert a BOM table, then export to PDF — all from a Claude conversation.

## Features

**21 tools** in 5 categories:

| Category | Tools |
|----------|-------|
| File operations (3) | open / close / list documents |
| Drawing views (6) | create drawing from template, standard views, first-angle aligned 3-view, section view, detail view, custom-orientation view |
| Annotation (6) | import model dimensions, probe edges, add dimensions (linear / diameter / radius / angle), auto reference dimensions, balloons, BOM table |
| Export (4) | capture drawing (JPEG), capture model views, save as PDF, save drawing |
| Assembly analysis (2) | read mates, read feature tree |

See the full [tool reference](#tool-reference) below.

## Architecture

```
Claude Code ◄── Streamable HTTP ──► src/server.py (FastMCP)
                                         │
                                    async tool handlers
                                         │
                                    sw.execute(func)  ← asyncio + Future
                                         │
                                    COM Worker Thread (STA, singleton)
                                         │
                                    pywin32 COM → SolidWorks 2021
```

SolidWorks COM must be driven from a single STA thread. All tool handlers are async and dispatch their COM callables onto a dedicated worker thread, bridged back with `concurrent.futures.Future`.

## Requirements

- Windows 10/11 host with **SolidWorks 2021** installed
- **Python 3.10+**
- An MCP client that supports Streamable HTTP (e.g. Claude Code) on the same **trusted LAN**
- Optional: a drawing template (`.drwdot`) and an SMB shared folder for large screenshots / PDFs

> Only SolidWorks 2021 has been tested. Other versions may work since the COM API is largely stable, but no guarantees.

## Quick Start

### 1. Install (on the SolidWorks host)

```powershell
git clone https://github.com/haunchen/solidworks-mcp.git
cd solidworks-mcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

<details>
<summary>Offline installation (host without internet access)</summary>

On a machine with internet access:

```powershell
pip download -d ./wheels -r requirements.txt
```

Copy the repo (including `wheels/`) to the SolidWorks host, then:

```powershell
.venv\Scripts\pip install --no-index --find-links wheels -r requirements.txt
```

</details>

### 2. Configure

```powershell
Copy-Item .env.example .env
# edit .env — see Configuration below
```

### 3. Run

```powershell
.venv\Scripts\python src\server.py
```

### 4. Connect from Claude Code (client machine)

```bash
claude mcp add --transport http solidworks http://<sw-host>:8080/mcp
```

Then just ask Claude: *"Open bracket.sldprt and create a first-angle 3-view drawing with dimensions, then export it as PDF."*

## Configuration

All settings are loaded from `.env` (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SW_MCP_HOST` | `0.0.0.0` | Address the server binds to |
| `SW_MCP_PORT` | `8080` | Server port |
| `SW_MCP_SMB_PATH` | `C:\mcp-share` | Server-side folder for screenshots / PDFs that exceed the base64 limit |
| `SW_MCP_SMB_CLIENT_PATH` | (empty) | The same folder as seen from the client (e.g. `U:\mcp-share`); used to rewrite returned paths |
| `SW_MCP_TEMPLATE` | (empty) | Drawing template (`.drwdot`) used by `create_drawing` |
| `SW_MCP_PAPER_SIZE` | `A3` | Default paper size |
| `SW_MCP_MAX_BASE64` | `1048576` | Max screenshot size (bytes) returned inline as base64; larger images are saved to the shared folder |

## Security

**This server has no authentication.** Anyone who can reach the port can drive your SolidWorks instance and read/write files through it.

- Deploy on a **trusted LAN only** — never expose the port to the internet
- Prefer binding `SW_MCP_HOST` to a specific LAN interface instead of `0.0.0.0`
- Consider OS-level firewall rules restricting access to known client IPs

## Tool Reference

### File operations

| Tool | Description |
|------|-------------|
| `open_document` | Open a SolidWorks document (`.sldprt` / `.sldasm` / `.slddrw`) |
| `close_document` | Close the specified document |
| `list_open_documents` | List all documents currently open in SolidWorks |

### Drawing views

| Tool | Description |
|------|-------------|
| `create_drawing` | Create a new drawing document from a template |
| `insert_standard_views` | Insert independent standard views (no projection alignment, custom scale) |
| `insert_standard_views_aligned` | Create first-angle aligned front / top / right views with auto scale |
| `insert_section_view` | Create a section view on a parent view |
| `insert_detail_view` | Create a detail (magnified) view on a parent view |
| `insert_custom_view` | Insert a named-orientation or arbitrary XYZ-rotation view |

### Annotation

| Tool | Description |
|------|-------------|
| `insert_model_dimensions` | Import model dimensions into drawing views |
| `probe_drawing_edges` | Inspect visible edges (index / type / coordinates in mm) before dimensioning |
| `add_dimension` | Add a linear / diameter / radius / angle dimension between probed edges |
| `auto_add_reference_dimensions` | Auto-add reference dimensions (bounding box + circles) for non-parametric geometry |
| `insert_balloon` | Insert balloons on an assembly drawing view (optionally filtered by component) |
| `insert_bom_table` | Insert a top-level-only BOM table |

### Export

| Tool | Description |
|------|-------------|
| `capture_drawing` | Capture the current drawing as JPEG (inline base64 or shared-folder path) |
| `capture_view` | Capture multi-angle screenshots from the 3D model |
| `save_as_pdf` | Export the current drawing to PDF |
| `save_drawing` | Save the current drawing (`.slddrw`) |

### Assembly analysis

| Tool | Description |
|------|-------------|
| `read_assembly_mates` | Read all mate relationships of a component in an assembly |
| `get_feature_tree` | Read the feature tree of a document |

## Development

```powershell
# run all tests (no SolidWorks needed — the COM layer is fully mocked)
.venv\Scripts\pytest tests/ -v
```

Behavior contracts live in `docs/specs/` — specs with `status: active` describe the expected behavior of shipped features. Design history is in `docs/plans/`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and PR guidelines.

## License

[MIT](LICENSE)
````

Step 2: 驗證 markdown 內部連結目標存在
Run: `Get-Item assets\banner-solidworks-mcp.svg, LICENSE, CONTRIBUTING.md 2>$null; Test-Path README.zh-TW.md`
Expected: banner 與 LICENSE 存在；CONTRIBUTING.md / README.zh-TW.md 可能還不存在（Task 5/6 才建立）— 不阻擋本 task，Task 9 統一驗收

Step 3: Commit
Run: `git add README.md; git commit -m "docs: 新增英文 README"`

---

### Task 5: README.zh-TW.md（繁中副版）

Implements: `open-source-release.md` #R2, #R3

Files:
- Create: `README.zh-TW.md`

Step 1: 建立 `README.zh-TW.md`，內容與 Task 4 英文版逐節對等（完整內容如下，一字不漏）：

````markdown
![solidworks-mcp banner](assets/banner-solidworks-mcp.svg)

# solidworks-mcp

[![CI](https://github.com/haunchen/solidworks-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/haunchen/solidworks-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

[English](README.md) | 繁體中文

讓 Claude 操作 **SolidWorks 2021** 出工程圖的 MCP server — 透過 pywin32 COM API，從區網即可呼叫。

開零件、建第一角法標準視圖、加尺寸標註與氣球、插 BOM 表、輸出 PDF — 全部在 Claude 對話中完成。

## 功能

**21 個 tools**，分 5 類：

| 類別 | Tools |
|------|-------|
| 檔案操作（3） | 開啟／關閉／列出文件 |
| 出圖視圖（6） | 從模板建工程圖、標準視圖、第一角法三視圖、剖面圖、局部放大圖、自訂角度視圖 |
| 標註（6） | 匯入模型尺寸、邊線查詢、加尺寸（線性／直徑／半徑／角度）、自動參考尺寸、氣球標註、BOM 表 |
| 輸出（4） | 工程圖截圖（JPEG）、模型多角度截圖、輸出 PDF、儲存工程圖 |
| 組立件分析（2） | 讀取配合關係、讀取特徵樹 |

完整清單見下方 [Tool 參考](#tool-參考)。

## 架構

```
Claude Code ◄── Streamable HTTP ──► src/server.py (FastMCP)
                                         │
                                    async tool handlers
                                         │
                                    sw.execute(func)  ← asyncio + Future
                                         │
                                    COM Worker Thread (STA, singleton)
                                         │
                                    pywin32 COM → SolidWorks 2021
```

SolidWorks COM 必須在同一個 STA 執行緒操作。所有 tool handler 是 async，將 COM callable 派發到專用的 worker thread，用 `concurrent.futures.Future` 橋接回 async。

## 系統需求

- Windows 10/11 主機，已安裝 **SolidWorks 2021**
- **Python 3.10+**
- 支援 Streamable HTTP 的 MCP client（如 Claude Code），與主機在同一個**信任區網**
- 選用：工程圖模板（`.drwdot`）與 SMB 共享資料夾（放大型截圖／PDF）

> 僅在 SolidWorks 2021 實測過。COM API 大致穩定，其他版本可能可用但不保證。

## 快速開始

### 1. 安裝（在 SolidWorks 主機上）

```powershell
git clone https://github.com/haunchen/solidworks-mcp.git
cd solidworks-mcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

<details>
<summary>離線安裝（主機無網路時）</summary>

在有網路的機器上：

```powershell
pip download -d ./wheels -r requirements.txt
```

把 repo（含 `wheels/`）複製到 SolidWorks 主機後：

```powershell
.venv\Scripts\pip install --no-index --find-links wheels -r requirements.txt
```

</details>

### 2. 設定

```powershell
Copy-Item .env.example .env
# 編輯 .env — 變數說明見下方「設定」
```

### 3. 啟動

```powershell
.venv\Scripts\python src\server.py
```

### 4. 從 Claude Code 連線（client 端）

```bash
claude mcp add --transport http solidworks http://<sw-host>:8080/mcp
```

接著直接對 Claude 說：「開啟 bracket.sldprt，建第一角法三視圖加尺寸標註，輸出 PDF。」

## 設定

所有設定從 `.env` 載入（範本見 `.env.example`）：

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `SW_MCP_HOST` | `0.0.0.0` | Server 綁定位址 |
| `SW_MCP_PORT` | `8080` | Server 埠號 |
| `SW_MCP_SMB_PATH` | `C:\mcp-share` | Server 端資料夾，放超過 base64 上限的截圖／PDF |
| `SW_MCP_SMB_CLIENT_PATH` | （空） | 同一個資料夾在 client 端看到的路徑（如 `U:\mcp-share`），用於改寫回傳路徑 |
| `SW_MCP_TEMPLATE` | （空） | `create_drawing` 使用的工程圖模板（`.drwdot`） |
| `SW_MCP_PAPER_SIZE` | `A3` | 預設圖紙大小 |
| `SW_MCP_MAX_BASE64` | `1048576` | 截圖以 base64 內嵌回傳的大小上限（bytes），超過則存共享資料夾 |

## 安全性

**本 server 沒有任何認證機制。**能連到這個埠的人就能操作你的 SolidWorks 並透過它讀寫檔案。

- 只部署在**信任的區網** — 絕不把埠暴露到公網
- `SW_MCP_HOST` 建議綁定特定區網介面，不要用 `0.0.0.0`
- 可加作業系統防火牆規則，限制只有已知 client IP 能連

## Tool 參考

### 檔案操作

| Tool | 說明 |
|------|------|
| `open_document` | 開啟 SolidWorks 文件（`.sldprt` / `.sldasm` / `.slddrw`） |
| `close_document` | 關閉指定文件 |
| `list_open_documents` | 列出所有目前開啟的文件 |

### 出圖視圖

| Tool | 說明 |
|------|------|
| `create_drawing` | 從模板建立新工程圖文件 |
| `insert_standard_views` | 插入獨立標準視圖（無投影關聯、可自訂比例） |
| `insert_standard_views_aligned` | 第一角法自動建立前／上／右三視圖（投影關聯、自動縮放） |
| `insert_section_view` | 在父視圖上建立剖面圖 |
| `insert_detail_view` | 在父視圖上建立局部放大圖 |
| `insert_custom_view` | 插入具名視角或任意 XYZ 旋轉角度視圖 |

### 標註

| Tool | 說明 |
|------|------|
| `insert_model_dimensions` | 匯入模型尺寸到工程圖視圖 |
| `probe_drawing_edges` | 查詢視圖可見邊線（index／類型／mm 座標），供標尺寸前定位 |
| `add_dimension` | 對查詢到的邊線加線性／直徑／半徑／角度尺寸 |
| `auto_add_reference_dimensions` | 自動加參考尺寸（外形 + 圓形），適用無參數化尺寸的幾何 |
| `insert_balloon` | 在組立件工程圖視圖插入氣球標註（可指定零件過濾） |
| `insert_bom_table` | 插入 BOM 表（Top-Level Only） |

### 輸出

| Tool | 說明 |
|------|------|
| `capture_drawing` | 截取目前工程圖為 JPEG（base64 內嵌或共享資料夾路徑） |
| `capture_view` | 從 3D 模型截取多角度截圖 |
| `save_as_pdf` | 將目前工程圖輸出為 PDF |
| `save_drawing` | 儲存目前工程圖（`.slddrw`） |

### 組立件分析

| Tool | 說明 |
|------|------|
| `read_assembly_mates` | 讀取組立件中指定零件的所有配合關係 |
| `get_feature_tree` | 讀取文件的特徵樹 |

## 開發

```powershell
# 跑全部測試（不需要 SolidWorks — COM 層全部 mock）
.venv\Scripts\pytest tests/ -v
```

行為契約在 `docs/specs/` — `status: active` 的 spec 描述已出貨功能的預期行為。設計歷程在 `docs/plans/`。

開發環境設定與 PR 規範見 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 授權

[MIT](LICENSE)
````

Step 2: 驗證兩版章節數一致（英中各 11 個 `## ` 標題）
Run: `(Select-String -Path README.md -Pattern '^## ').Count; (Select-String -Path README.zh-TW.md -Pattern '^## ').Count`
Expected: 兩個數字相同

Step 3: Commit
Run: `git add README.zh-TW.md; git commit -m "docs: 新增繁體中文 README"`

---

### Task 6: CONTRIBUTING.md

Implements: `open-source-release.md` #R8

Files:
- Create: `CONTRIBUTING.md`

Step 1: 建立 `CONTRIBUTING.md`，內容如下（完整內容，一字不漏）：

````markdown
# Contributing to solidworks-mcp

Thanks for your interest in contributing!

## Development setup

**Windows is required** — the COM layer (`src/sw_connection.py`, `src/tools/file_ops.py`) imports `pythoncom` / `win32com` at module level, and pywin32 only installs on Windows. This applies even if you just want to run the tests.

**SolidWorks is NOT required** for development: the COM layer is fully mocked in the test suite.

```powershell
git clone https://github.com/haunchen/solidworks-mcp.git
cd solidworks-mcp
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pytest
```

## Running tests

```powershell
.venv\Scripts\pytest tests/ -v
```

All tests must pass without SolidWorks installed. Real-machine verification against SolidWorks 2021 is performed by the maintainer before merging behavior-affecting changes.

## Conventions

- **Specs are behavior contracts.** Specs in `docs/specs/` with `status: active` describe the expected behavior of shipped features. If your change alters observable behavior, update the corresponding spec in the same PR.
- **Commits** follow conventional commits (`feat:` / `fix:` / `docs:` / `chore:` / `refactor:`). English or Chinese descriptions are both fine.
- **COM code style**: tool handlers are async and must dispatch COM work via `sw.execute(...)` — never touch COM objects outside the worker thread. See `CLAUDE.md` for architecture constraints.

## Pull requests

1. Fork the repo and create a feature branch from `main`
2. Make your changes; keep tests green (`pytest tests/ -v`)
3. Open a PR against `main` describing what changed and why; reference the spec requirement IDs you touched (e.g. `balloon.md #R3`)

CI runs the full test suite on `windows-latest` for every PR.
````

Step 2: 驗證檔案存在
Run: `Test-Path CONTRIBUTING.md`
Expected: `True`

Step 3: Commit
Run: `git add CONTRIBUTING.md; git commit -m "docs: 新增 CONTRIBUTING"`

---

### Task 7: CI workflow

Implements: `open-source-release.md` #R5

Files:
- Create: `.github/workflows/ci.yml`

Step 1: 建立 `.github/workflows/ci.yml`，內容如下：

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: pip install -r requirements.txt pytest

      - name: Run tests
        run: pytest tests/ -v
```

注意：`pytest` 不在 requirements.txt 內（runtime 依賴不含測試工具），CI 安裝步驟顯式帶上。

Step 2: 驗證 YAML 語法正確
Run: `.venv/Scripts/python -c "import yaml" 2>$null; if (-not $?) { .venv/Scripts/pip install pyyaml | Out-Null }; .venv/Scripts/python -c "import yaml; d = yaml.safe_load(open('.github/workflows/ci.yml')); print(d['jobs']['test']['runs-on'])"`
Expected: `windows-latest`

Step 3: Commit
Run: `git add .github/workflows/ci.yml; git commit -m "ci: 新增 windows-latest pytest workflow"`

---

### Task 8: CLAUDE.md 更新 Development Phases

Implements: `open-source-release.md` #R7

Files:
- Modify: `CLAUDE.md:53-59`

Step 1: 用 Edit tool 將 CLAUDE.md 的 Development Phases 區塊替換。

old_string（精確比對現有內容）:

```
## Development Phases

- Phase 1 (current): 9 tools — 檔案操作 + 基本出圖 + 標註 + 輸出
- Phase 2: 組立件分析 — 配合關係讀取 + 規則引擎自動視圖判斷
- Phase 3: 進階視圖 — 剖面圖、局部放大、氣球標註、BOM 表

詳見 `docs/phase*.md` 和 `docs/plans/`。
```

new_string:

```
## Development Phases

- Phase 1（完成）: 檔案操作 + 基本出圖 + 標註 + 輸出
- Phase 2（完成）: 組立件分析 — 配合關係讀取 + 特徵樹
- Phase 3（完成）: 進階視圖 — 剖面圖、局部放大、自訂視圖、氣球標註、BOM 表

目前共 21 tools。詳見 `docs/phase*.md` 和 `docs/plans/`。
```

Step 2: 驗證替換成功
Run: `Select-String -Path CLAUDE.md -Pattern 'current|21 tools'`
Expected: 無 `current` 命中、有 `21 tools` 一行

Step 3: Commit
Run: `git add CLAUDE.md; git commit -m "docs: CLAUDE.md Development Phases 更新為 Phase 1-3 完成現況"`

---

### Task 9: 驗收 + 硬編碼掃描

Implements: `open-source-release.md` #R2, #R9（S4 的可自動化部分）

Files: 無新增（純驗證）

Step 1: 檔案齊全驗收
Run: `Get-Item LICENSE, pyproject.toml, README.md, README.zh-TW.md, CONTRIBUTING.md, .github/workflows/ci.yml, assets/banner-solidworks-mcp.svg | Select-Object Name`
Expected: 7 個項目全部存在，無錯誤

Step 2: 全測試綠
Run: `.venv/Scripts/pytest tests/ -v`
Expected: 144 passed

Step 3: 硬編碼掃描 — tracked 檔案
Run:
```powershell
git grep -inE "password|passwd|secret|token|api[_-]?key" -- ':!docs/plans'
git grep -inE "\b10\.\d+\.\d+\.\d+\b|\b172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+\b|192\.168\.\d+\.\d+" -- ':!docs/plans'
git grep -inE "\\\\\\\\[A-Za-z]" -- ':!docs/plans'
```
Expected: 命中僅限 (a) `.env.example` 與測試的佔位範例值（如 `192.168.1.100`）(b) 文件中對變數的泛稱說明。任何真實內網主機名、帳密、公司路徑 → 停下回報，不得繼續

Step 4: 硬編碼掃描 — git 全歷史
Run:
```powershell
git log --all -p | Select-String -Pattern "password\s*=|secret\s*=|token\s*=" -CaseSensitive:$false | Select-Object -First 20
git log --all -p | Select-String -Pattern "\b10\.\d+\.\d+\.\d+\b|192\.168\.\d+\.\d+" | Select-Object -First 20
```
Expected: 同 Step 3 標準 — 僅佔位範例值。有真實敏感資訊 → 停下回報（需 history rewrite，超出本 plan 範圍）

Step 5: 回報掃描結果摘要（命中清單 + 逐條判定為何是安全的），不 commit（無檔案變更）

注意：安全性掃描（/security-review）由主對話在所有 task 完成後執行，不在 subagent task 內。
