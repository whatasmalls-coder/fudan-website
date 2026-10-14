# Cloudflare Worker 補充程式碼

這個資料夾放的是要貼到 Cloudflare 上 `fudan-ai-proxy` Worker 的程式碼。GitHub Pages 不會執行這裡的檔案。

## 站牌問題回報（`report-endpoint.js`）

公車頁每條路線的「回報站牌問題」按鈕會 `POST https://fudan-ai-proxy.whatasmalls.workers.dev/report`，
Worker 收到後在這個 repo 自動開一個標籤為「站牌回報」的 Issue。

### 安裝（約 5 分鐘）

1. **建立 GitHub token**
   GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
   - Repository access：Only select repositories → `fudan-website`
   - Permissions → Repository permissions → **Issues: Read and write**（其他都不用開）

2. **把 token 存成 Worker Secret**
   Cloudflare Dashboard → Workers & Pages → `fudan-ai-proxy` → Settings → Variables and Secrets → Add
   - Type：Secret
   - Name：`GITHUB_TOKEN`
   - Value：剛剛的 token

3. **貼上程式碼**
   Worker → Edit code：
   - 把 `report-endpoint.js` 的全部內容貼到 Worker 檔案的最上面（`export default` 之前）
   - 在 `fetch(request, env, ...)` 的**第一行**加上：

     ```js
     const reportRes = await handleReport(request, env);
     if (reportRes) return reportRes;
     ```

     這樣 `/report` 會先被處理，其他請求照舊轉給 Gemini。
   - Deploy

4. **在 repo 建立「站牌回報」標籤**（Issues → Labels → New label）。
   沒有這個標籤也能開 Issue，只是不會自動加標籤。

5. **打開前端開關**：`bus-search/index.html` 裡把 `REPORT_ENABLED` 改成 `true`。
   在這之前按鈕不會出現，避免使用者按了卻送不出去。

### 防護

- 只接受 `visitfudan.com` / `www.visitfudan.com` 來源
- 同一個 IP 每 10 分鐘最多 5 則（Worker 記憶體內，盡力而為）
- 蜜罐欄位擋簡單機器人；說明最多 500 字、整包最多 4000 字元
- 使用者文字裡的 `@`、`#數字`、HTML 角括號會被處理，不會 tag 到人或連到其他 Issue
