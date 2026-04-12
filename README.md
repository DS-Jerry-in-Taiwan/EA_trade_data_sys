# 📉 MT5 Trade Data Downloader (Linux/Docker)

## 🌟 專案簡介
本專案提供一套在 Ubuntu Linux 環境下穩定執行 MetaTrader 5 並進行量化數據抓取的完整方案。

## 🏗️ 快速啟動
```bash
cd mt5docker
docker compose up -d
```

## 🖥️ 遠端監看 (VNC)
- **網址**: `http://localhost:6081/vnc.html`
- **密碼**: `jerry1234` (VNC 驗證時請輸入 `jerry123`)
- **注意**: 登入後請確保已勾選 "Allow DLL imports"。

## 🧪 執行策略驗證
```bash
# 叫 Runner 容器執行宿主機上的腳本
docker exec python-runner wine python /app/download.py
```

## 📊 關鍵配置 (Broker Info)
- **黃金代碼**: `XAUUSDm`
- **服務埠號**: `8001` (RPyC Bridge)

## 📂 目錄結構
- `mt5docker/`: 容器設定與啟動腳本。
- `MT5_Data/`: 持久化後的 MT5 安裝檔案。
- `docs/`: 詳細排查日誌與開發手冊。

---
*Created by Gemini CLI - 2026-03-23*
