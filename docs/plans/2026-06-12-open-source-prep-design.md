# 開源準備 — 設計文件

日期：2026-06-12
Branch：feat/open-source-prep
Spec：docs/specs/open-source-release.md

## 目標

把 solidworks-mcp 從私有開發狀態整理成可公開的開源專案：補齊文件、授權、打包 metadata、CI 品質訊號，並準備 v0.1.0 release。Repo 轉 public 由維護者手動操作，不在本次自動化範圍。

## 範圍決策（brainstorm 拍板）

| 決策點 | 結論 |
|--------|------|
| README 語言 | 英文主版 README.md + 繁中副版 README.zh-TW.md，頂部互連 |
| License | MIT，Copyright (c) 2026 haunchen |
| Release 通路 | GitHub Release 即可，不上 PyPI（受眾窄：Windows + SolidWorks COM） |
| 打包深度 | 輕量 pyproject.toml（metadata + 依賴宣告），不做 package 重構，執行方式維持 `python src/server.py`；完整 package 化留到 v0.2.0 |
| CI | windows-latest 單一 job（src 頂層 `import pythoncom`，非 Windows import 不起來） |
| 內部文件 | docs/plans、docs/phase*.md 原樣保留公開 |
| Repo public 化 | 維護者手動操作，本設計只備妥 metadata 文案 |

## 產出物（7 新檔 + 1 修改）

1. `README.md` — 英文主版
2. `README.zh-TW.md` — 繁中副版，內容與英文版對等
3. `LICENSE` — MIT，Copyright (c) 2026 haunchen
4. `CONTRIBUTING.md` — 英文
5. `pyproject.toml` — 輕量 metadata
6. `.github/workflows/ci.yml` — windows-latest pytest
7. `assets/banner-solidworks-mcp.svg` — README banner（來源：vault `<vault>/solidworks-mcp/banner-solidworks-mcp.svg`，複製進 repo）
8. `CLAUDE.md` — 更新過時的 Development Phases 區塊

## README 結構（11 節，英中兩版對等）

1. Banner（`assets/banner-solidworks-mcp.svg`）+ 標題 + badges（CI / license / python）+ 語言切換連結
2. 簡介：Claude Code 經區網 Streamable HTTP MCP 操作 SolidWorks 2021 出工程圖（pywin32 COM）
3. 功能：21 tools 分 5 類（檔案操作 3 / 出圖 6 / 標註 6 / 輸出 4 / 組立件 2）
4. 架構 ASCII 圖（async tool handler → STA COM worker thread → SolidWorks，沿用 CLAUDE.md 既有圖）
5. Requirements：Windows + SolidWorks 2021 + Python 3.10+
6. Quick Start：clone → venv → pip install → `.env` 設定 → 啟動 server → Claude Code 端 `mcp add`；含離線 wheels 安裝小節（`pip download -d ./wheels` → SW 主機 `pip install --no-index`）
7. Configuration：`.env` 7 個變數表（對應 `.env.example`）
8. Security：無認證機制，僅限信任區網部署；不要暴露到公網
9. Tools 參考表：21 tools 名稱 + 一句話說明
10. Development：測試指令、docs/specs 行為契約說明
11. License

## CONTRIBUTING.md（英文，四大塊）

1. **Dev setup**：Windows 必要（pywin32 限定，連跑測試都需要）；venv + `pip install -r requirements.txt`
2. **Testing**：`pytest tests/ -v`；COM 層全 mock，沒有 SolidWorks 也能貢獻；實機 SW 驗證由 maintainer 執行
3. **Conventions**：docs/specs/ 下 active spec 是行為契約，改行為須同步更新 spec；conventional commits（feat:/fix:/chore:），語言英中皆可
4. **PR flow**：fork → feature branch → tests 綠 → PR

## CI（.github/workflows/ci.yml）

- trigger：push to main + 所有 pull_request
- runs-on：windows-latest
- Python 單一版本 3.12（不跑 matrix，Windows runner 慢，單版本夠當品質訊號）
- steps：checkout → setup-python → `pip install -r requirements.txt` → `pytest tests/ -v`
- README 掛 CI badge

## pyproject.toml（輕量 metadata）

```toml
[project]
name = "solidworks-mcp"
version = "0.1.0"
requires-python = ">=3.10"
license = "MIT"
dependencies = ["mcp[server]", "pywin32", "Pillow", "python-dotenv"]
```

- requirements.txt 保留（SW 主機離線安裝流程依賴它，零改動）
- 程式碼 0 改動，deploy.sh 不動

## 版本與 Release（merge 進 main 後執行）

1. `git tag v0.1.0` → push tag
2. `gh release create v0.1.0` — Release notes 含：21 tools 功能總覽、需求、已知限制（僅 SolidWorks 2021 實測、無認證機制）

## Repo metadata（維護者手動轉 public 時用）

- description：`MCP server for SolidWorks 2021 — drive drawing creation from Claude via COM`
- topics：`mcp`, `solidworks`, `claude`, `python`, `com-automation`, `cad`, `engineering-drawings`

## CLAUDE.md 更新

- Development Phases 區塊改為現況：Phase 1-3 全部完成（21 tools），移除「Phase 1 (current)」字樣
- 其餘（Commands / Architecture / Key Constraints）不動

## 後續提醒（本次完成後、repo 轉 public 前必做）

- [ ] 硬編碼掃描：全 tracked 檔案 + git 全歷史掃硬編碼的內網 IP、主機名、帳密、路徑（如 trufflehog / gitleaks 或手動 git grep 全歷史）
- [ ] 安全性掃描：跑 /security-review 之類的安全審查（重點：HTTP server 無認證面、檔案路徑處理、COM 呼叫輸入驗證）

## 敏感資訊盤點結果（brainstorm 階段已驗證）

- `.env` / `deploy.sh` / `OpenSSH-Win64.zip` / `wheels/` 從未進過 git 歷史（`git log --all --diff-filter=A` 為空）
- `.env.example` 全為佔位範例值（192.168.1.100 等通用範例）
- tracked 檔案中無真實內網主機名 / 帳密
