# Cloudflare Worker（fudan-ai-proxy）

`worker.js` 是部署在 Cloudflare 上 `fudan-ai-proxy` Worker 的**完整原始碼**。GitHub Pages 不會執行這個資料夾。
改 Worker 時以這份為準：在這裡改好 → 整份貼到 Cloudflare → Deploy。

## 功能

- **Gemini 代理**（`POST /`）：AI 助手和公車頁 AI 搜尋用，只接受 visitfudan.com 來源和白名單工具
- **站牌問題回報**（`POST /report`）：公車頁「回報站牌問題」→ 在這個 repo 自動開一個標籤為「站牌回報」的 Issue
- **健康檢查**（Cron Trigger）：定期測試 Gemini，結果回報到 Plausible

## Secrets（Cloudflare → fudan-ai-proxy → Settings → Variables and Secrets）

| 名稱 | 內容 |
|---|---|
| `GEMINI_API_KEY` | Gemini API 金鑰 |
| `GITHUB_TOKEN` | fine-grained token：Only select repositories → `fudan-website`，權限只開 **Issues: Read and write** |

沒設定 `GITHUB_TOKEN` 時，`/report` 會回 503，Gemini 代理不受影響。

## 站牌回報的防護

- 只接受 `visitfudan.com` / `www.visitfudan.com` 來源（沒帶 Origin 也擋）
- 同一個 IP 每 10 分鐘最多 5 則（Worker 記憶體內，盡力而為）
- 蜜罐欄位擋簡單機器人；說明最多 500 字、整包最多 4000 字元
- 使用者文字裡的 `@`、`#數字`、HTML 角括號會被處理，不會 tag 到人或連到其他 Issue

前端開關：`bus-search/index.html` 的 `REPORT_ENABLED`。
