# 📅 工作日誌：MT5 量化環境重建 (2026-03-23)

## 📌 當前狀態：數據鏈路全面打通 (End-to-End Success)
成功實現了 **Ubuntu 24.04 宿主機 -> Python Runner 容器 -> MT5 Server 容器** 的穩定通訊鏈路，並成功抓取實時金價 (XAUUSDm)。

---

## 🔍 問題排查方向與解決方案

### 1. 跨平台序列化衝突 (Connection Reset)
- **問題**：Native Linux Python 連線 Wine Windows Python 時，RPyC 握手會因為對象反序列化失敗而 Reset。
- **解決**：採用 **「雙容器架構」**。建立一個與 MT5 Server 環境完全一致的 `python-runner`，確保通訊兩端都是 Wine-based Python 3.13。

### 2. VNC 畫面黑屏與渲染失效
- **問題**：缺乏視窗管理器導致 Wine 視窗無法正確畫在 Xvfb 緩衝區。
- **解決**：在 Dockerfile 中打包 `openbox` 視窗管理器，並在啟動時優先執行。

### 3. 持久化數據丟失
- **問題**：容器重啟後，手動安裝的 MT5 檔案會消失。
- **解決**：將 `/opt/.../MetaTrader 5` 映射至宿主機 `MT5_Data`，並在 `start_server.sh` 加入自動安裝/修復邏輯。

### 4. 交易品種 (Symbol) 命名差異
- **問題**：標準 `XAUUSD` 抓不到數據。
- **解決**：執行全品種掃描腳本 `list_symbols.py`，確認該券商正確代碼為 **`XAUUSDm`**。

---

## 🏗️ 最終實現架構 (Dual-Container Architecture)

- **MT5 Server (Port 8001/6081)**:
  - 運行 MT5 終端機 + RPyC Proxy Server。
  - 開放 VNC 共享模式 (`-shared`)。
- **Python Runner**:
  - 掛載 `/app` 執行量化策略。
  - 具備 `pandas`, `MetaTrader5`, `pymt5linux` 環境。

---

## 📈 實現結果
- **連線狀態**: READY (LINK_OK)
- **實測數據**: XAUUSDm Bid/Ask 成功獲取。
- **持久化**: MT5 檔案已保存在宿主機，重啟不丟失。

