# 截圖 JPEG 壓縮 + SMB 路徑設計

## 問題

`capture_drawing` 透過 `SaveBMP` 產生的截圖為 BMP 格式（無壓縮），檔案 4MB+。
MCP tool result 有 25K token 上限，ImageContent base64 被當文字 token 計算
（Claude Code issue #9152），即使壓縮至 ~100KB 仍會超限。

## 設計

改動 `_capture_drawing` 截圖管線：BMP → JPEG 壓縮，一律存 SMB 共享資料夾，
回傳 client 端路徑供 Read tool 透過 vision 通道讀取。

### 變更

1. SaveBMP 存到暫存目錄（維持現狀）
2. PIL 開啟 BMP → 轉 RGB → 存 JPEG quality 85
3. 存到 SMB 共享資料夾（`SMB_SHARE_PATH`）
4. 回傳 client 端路徑（`SMB_CLIENT_PATH`），供 Read tool 讀取圖片
5. 移除 `output_mode` 參數和 base64 inline 邏輯
6. `save_as_pdf` 也套用 server→client 路徑轉換

### 新增設定

- `SW_MCP_SMB_CLIENT_PATH`：client 端 SMB 路徑（如 `U:\Frank\mcp-share`）

### 預期效果

4MB+ BMP → ~124KB JPEG，透過 SMB + Read tool 繞過 token 上限。

## 測試策略

- 既有 export 測試確認不 break
- SW 主機實測 capture_drawing 回傳路徑 + Read tool 讀取驗證
