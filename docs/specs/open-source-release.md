---
domain: open-source-release
status: active
created: 2026-06-12
last_modified: 2026-06-12
---

# Open Source Release

定義 solidworks-mcp 作為公開開源專案應具備的文件、授權、打包 metadata、CI 與 release 狀態。

## Requirements

### R1: MIT 授權
- **Level**: MUST
- **Description**: Repo 根目錄含 `LICENSE` 檔案，內容為 MIT License，Copyright (c) 2026 haunchen。

### R2: 英中雙版 README 對等且互連
- **Level**: MUST
- **Description**: `README.md`（英文主版）與 `README.zh-TW.md`（繁體中文副版）內容對等（章節結構與資訊量一致），兩版頂部互附語言切換連結，並引用 `assets/banner-solidworks-mcp.svg` banner。

### R3: 安全警示
- **Level**: MUST
- **Description**: README 明確警示 server 無認證機制，僅限信任區網部署，不可暴露至公網。

### R4: 打包 metadata
- **Level**: MUST
- **Description**: `pyproject.toml` 宣告專案 metadata（名稱、版本、授權、依賴、requires-python >= 3.10）。`requirements.txt` 保留，離線安裝流程（`pip download -d ./wheels`）不受影響，執行方式維持 `python src/server.py` 不變。

### R5: CI 品質訊號
- **Level**: MUST
- **Description**: push 到 main 與所有 pull request 時，CI 在 Windows 環境執行完整測試套件；README 顯示 CI badge。

### R6: v0.1.0 Release
- **Level**: MUST
- **Description**: main 上存在 `v0.1.0` git tag 與對應 GitHub Release，notes 含功能總覽（21 tools）、系統需求、已知限制（僅 SolidWorks 2021 實測、無認證機制）。

### R7: CLAUDE.md 反映現況
- **Level**: MUST
- **Description**: CLAUDE.md 的 Development Phases 區塊反映 Phase 1-3 全部完成（21 tools）的現況，不再標示 Phase 1 為 current。

### R8: 無 SolidWorks 的貢獻路徑
- **Level**: MUST
- **Description**: `CONTRIBUTING.md` 說明測試 COM 層全 mock、沒有 SolidWorks 也能開發與跑測試（仍需 Windows，pywin32 限定），實機驗證由 maintainer 執行；並說明 spec 行為契約與 PR 流程。

### R9: 公開前無敏感資訊
- **Level**: MUST
- **Description**: Repo 轉 public 前，tracked 檔案與 git 全歷史不含真實內網 IP、主機名、帳密或公司內部路徑（硬編碼掃描 + 安全性掃描為轉 public 前的必經步驟）。

## Scenarios

### S1: 新使用者從 README 完成部署
- **Given**: 一台有 SolidWorks 2021 的 Windows 主機與一台區網內的 Claude Code 客戶端
- **When**: 依 README Quick Start 操作（含離線 wheels 安裝路徑）
- **Then**: server 啟動成功，Claude Code 可註冊並呼叫 21 個 tools
- **Implements**: #R2, #R4

### S2: 無 SolidWorks 的貢獻者跑測試
- **Given**: 一台沒有安裝 SolidWorks 的 Windows 開發機
- **When**: 依 CONTRIBUTING 設定環境並執行 `pytest tests/ -v`
- **Then**: 全部測試通過，不需要 SolidWorks
- **Implements**: #R8

### S3: PR 觸發 CI
- **Given**: 任一 fork 或 branch 開 PR 到 main
- **When**: PR 建立或更新
- **Then**: CI 於 windows-latest 跑完整測試並回報狀態
- **Implements**: #R5

### S4: 轉 public 前掃描
- **Given**: 開源準備檔案全部 merge 進 main
- **When**: 維護者準備將 repo 轉 public
- **Then**: 先完成硬編碼掃描（全歷史）與安全性掃描且無發現，才執行轉 public
- **Implements**: #R9

## Design Decisions

### D1: 不上 PyPI，只發 GitHub Release
- **Decision**: v0.1.0 僅 git tag + GitHub Release，不發佈 PyPI 套件。
- **Rationale**: 受眾窄（Windows + SolidWorks 2021 COM），使用情境是 clone 後跑而非 pip install 當函式庫；PyPI 帳號與發佈流程維護成本高於曝光效益。
- **Date**: 2026-06-12

### D2: 輕量 pyproject，不做 package 重構
- **Decision**: pyproject.toml 只宣告 metadata 與依賴，保留 `src/` 平面結構與 `python src/server.py` 執行方式；`src/solidworks_mcp/` 正式 package + console script 留到 v0.2.0。
- **Rationale**: 開源準備的重點是文件與品質訊號；package 重構要改 17 個檔案的 import 並在 SW 主機重驗離線部署流程，風險與本次目標不成比例。
- **Date**: 2026-06-12

### D3: CI 限定 windows-latest 單版本
- **Decision**: CI 只跑 windows-latest + Python 3.12 單一 job，不跑 OS/Python matrix。
- **Rationale**: `src/sw_connection.py` 與 `src/tools/file_ops.py` 頂層 `import pythoncom`，pywin32 僅 Windows 可安裝，非 Windows 連 import 都失敗；Windows runner 較慢，單版本已足夠當品質訊號。
- **Date**: 2026-06-12

### D4: 內部開發文件原樣公開
- **Decision**: docs/plans/ 36 份中文設計文件與 docs/phase*.md 原樣保留在公開 repo。
- **Rationale**: 內容為技術設計紀錄無敏感資訊，保留可展示 spec-driven 開發歷程；翻譯或清理的維護成本無對應效益。
- **Date**: 2026-06-12
