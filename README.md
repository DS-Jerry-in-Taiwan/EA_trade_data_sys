# MT5 Trade Data Downloader (Linux/Docker)

**版本**: v3.0 (2026-09-28)
**描述**: 單一 repository、單一 application image 的 MT5 資料服務。部署使用 `mt5-server` 與 `trade-data-service` 兩個容器；後者由 supervisor 管理三個職責分離的 process。

---

## 系統架構

```
┌─────────────────────────────────────────────────────┐
│                   mt5-server                        │
│  ┌──────────┐  ┌──────────────────────────────┐    │
│  │   MT5    │  │  RPyC Proxy (pymt5linux)     │    │
│  │  Terminal │◄─┤  Port 8001                   │    │
│  │  (Wine)   │  │  wine C:/Python/python.exe  │    │
│  └──────────┘  └──────────┬───────────────────┘    │
│    VNC Port 6081          │                         │
└───────────────────────────┼─────────────────────────┘
                            │ RPyC
┌───────────────────────────┼─────────────────────────┐
│ trade-data-service (one container, supervised)       │
│  ┌───────────────────┐    │  tick NDJSON             │
│  │ Tick Service      │◄───┘──▶ Unix socket ─────┐    │
│  │ sole tick poller  │                         │    │
│  └───────────────────┘                         ▼    │
│  ┌───────────────────┐    atomic CSV       ┌────────┐│
│  │ History Worker    │───────────────┐     │Gateway ││
│  │ background writer │               ├────▶│ :8090  ││
│  └───────────────────┘               │read └────────┘│
│                         persisted history             │
│  Supervisor: starts all 3, forwards shutdown, fails   │
│  the container lifecycle when any child exits.        │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│              Trade Analytics Pipeline                │
│  MT5 Bridge API ─→ service/etl/trade_etl.py ─→        │
│  PostgreSQL trade_analytics ─→ Grafana Dashboard      │
└──────────────────────────────────────────────────────┘
```

### 核心元件

| 元件 | 語言 | 角色 |
|:-----|:-----|:-----|
| MT5 Terminal | Wine/Python | 數據源，透過 MetaTrader5 Python API |
| RPyC Proxy (pymt5linux) | Python | 容器網路內的 MT5 橋接（8001 不對 host 公開） |
| MT5Client | Python | 各 process 自有、thread-safe 的統一連線管理 (`service/infrastructure/mt5/client.py`) |
| TickService | Python | 唯一週期性 tick poller，透過 `/run/trade-data/ticks.sock` 發佈 versioned NDJSON |
| HistoryService | Python | 獨立背景 worker；增量抓取、合併、去重並原子發佈 CSV 與 ready marker |
| AccountService | Python | 帳戶資訊、持倉、委託、成交歷史查詢 |
| ChartService | Python | K 線圖生成 (matplotlib → base64 PNG) |
| API Gateway | Python (Flask) | REST API + WebSocket (Flask-SocketIO) Port 8090 |
| Trade ETL | Python | 只讀交易資料 + OHLC CSV 匯入 PostgreSQL (`service/etl/trade_etl.py`) |
| PostgreSQL | SQL | `trade_analytics` 交易分析資料庫 |
| Grafana | Dashboard | 交易績效與 K 線報表 |

### 資料與連線生命週期

1. `mt5-server` 等待 Wine terminal ready，才啟動容器內的 RPyC bridge。
2. `trade-data-service` 啟動時從 bind-mounted `mt5docker/requirements.txt` 安裝依賴；依賴不是 baked 進 image。
3. PID 1 supervisor 啟動 Tick Service、History Worker、API Gateway 三個 process。各 process 經 `MT5Client` 自行建立及重試 RPyC 連線。
4. Tick Service 建立 Unix socket 並持續輪詢 MT5。Gateway 的 consumer 連線後先收到 snapshot，再接收 live tick；斷線時以 bounded backoff 重連。外部 client 只連 Gateway：REST 讀取最新 snapshot，Socket.IO client `subscribe` 後由 Gateway push，WebSocket 本身不會輪詢。
5. History Worker 寫入暫存檔並原子替換 persisted CSV；Gateway 只透過 repository 讀取已完成資料，不會在歷史查詢 request 中同步呼叫 MT5。
6. 收到 `SIGTERM`/`SIGINT` 時 supervisor 要求三個 child graceful shutdown，逾時才強制終止；任何 child 意外退出會停止其餘 child 並讓容器非零退出。

`/api/v1/health` 的 `ready` 只由即時資料 critical path 決定：IPC 已連線、Tick Service status 新鮮健康，且所有設定 symbol 都有 fresh tick。History Worker 異常會回報 `status: degraded`，但不阻塞即時資料 readiness；即時路徑未 ready 則 HTTP 503。health handler 不會觸發 MT5/RPyC request。

---

## REST API 端點

| 方法 | 端點 | 說明 |
|:----|:-----|:------|
| GET | `/api/v1/health` | 健康檢查 |
| GET | `/api/v1/ticks/<symbol>` | 即時報價 (Bid/Ask) |
| GET | `/api/v1/rates/<symbol>?timeframe=M5&days=7&limit=100` | K 線歷史（CSV 快取，支援分頁） |
| GET | `/api/v1/rates/<symbol>/query?start_time=...&end_time=...&timeframe=M5` | 查詢已持久化歷史資料的時間範圍（不直連 MT5） |
| GET | `/api/v1/symbols` | 列出所有追蹤商品（含 MT5 描述與位數） |
| GET | `/api/v1/openapi.yaml` | OpenAPI v3 規格文件 |
| Socket.IO | `/socket.io` | 連線後以 `subscribe`/`unsubscribe` 管理即時 tick 推送 |

### 只讀交易查詢 API

以下端點需要 `X-API-Key` header，key 由 `READONLY_API_KEY` 環境變數提供。

| 方法 | 端點 | 說明 |
|:----|:-----|:------|
| GET | `/api/v1/account` | 帳戶資訊（balance/equity/margin/leverage） |
| GET | `/api/v1/positions?symbol=XAUUSDm` | 當前持倉，可選 symbol filter |
| GET | `/api/v1/history/deals?days=7&summary=true` | 歷史成交，支援 `days/from/to/summary` |
| GET | `/api/v1/history/orders?days=7` | 歷史委託 |

限制：`days` 預設 7 天，最大 90 天；超過會回 HTTP 400。

### API 存取位址

```
主機位址: http://<YOUR_HOST_IP>:8090
```

> **注意**：請將 `<YOUR_HOST_IP>` 替換為實際主機 IP（可使用 `hostname -I` 或 `ip route get 8.8.8.8 | grep src` 查詢）。

**範例 URL：**

```bash
# 健康檢查
curl http://<YOUR_HOST_IP>:8090/api/v1/health

# 即時報價
curl http://<YOUR_HOST_IP>:8090/api/v1/ticks/XAUUSDm

# K 線歷史（CSV 快取）
curl "http://<YOUR_HOST_IP>:8090/api/v1/rates/XAUUSDm?timeframe=M5&limit=100"

# 查詢已持久化的歷史範圍（回測用）
curl "http://<YOUR_HOST_IP>:8090/api/v1/rates/XAUUSDm/query?timeframe=M5&start_time=2025-01-01&end_time=2025-01-07"

# 商品列表
curl http://<YOUR_HOST_IP>:8090/api/v1/symbols

# OpenAPI 規格文件
curl http://<YOUR_HOST_IP>:8090/api/v1/openapi.yaml

# 帳戶資訊（需 API key）
curl -H "X-API-Key: $READONLY_API_KEY" http://<YOUR_HOST_IP>:8090/api/v1/account

# 近 90 天成交摘要（需 API key）
curl -H "X-API-Key: $READONLY_API_KEY" \
  "http://<YOUR_HOST_IP>:8090/api/v1/history/deals?days=90&summary=true"
```

> **注意**：Symbol 名稱需與 `settings.yaml` 中設定一致（如 `XAUUSDm`、`EURUSDm`、`GBPUSDm`、`BTC`），API 會自動解析為 Broker 實際商品名稱。

---

## 追蹤商品

| Symbol | Tick | M5 | M15 | H1 | 說明 |
|:-------|:----:|:--:|:---:|:--:|:------|
| XAUUSDm | ✅ | ✅ | ✅ | ✅ | 黃金（fuzzy → XAUUSD） |
| EURUSDm | ✅ | ✅ | ✅ | ✅ | 歐元（fuzzy → EURUSD） |
| GBPUSDm | ✅ | ✅ | ✅ | ✅ | 英鎊（fuzzy → GBPUSD） |
| BTC | ✅ | ✅ | ✅ | ✅ | 比特幣指數（Exness 專屬） |

---

## 快速啟動

```bash
cd mt5docker
export READONLY_API_KEY='<your-readonly-api-key>'
docker compose up -d
```

> `READONLY_API_KEY` 不應提交到 Git。建議放在本機 shell 環境或部署平台的 secret/env 管理中。

## 交易分析管道

Phase 1 已新增 PostgreSQL ETL：

```bash
# 安裝 ETL 依賴（container 內）
docker exec trade-data-service pip install -r /app/service/etl/requirements-etl.txt --break-system-packages

# 建立 schema
docker exec -i bcas-postgres psql -U postgres -d trade_analytics < service/etl/schema.sql

# 執行 ETL
docker exec trade-data-service python3 /app/service/etl/trade_etl.py
```

資料表：

| Table | 說明 |
|:------|:-----|
| `trade_account_snapshots` | 帳戶快照 |
| `trade_deals` | 成交明細 |
| `trade_orders` | 歷史委託 |
| `price_ohlc` | OHLC K 線資料（支援 H1/M5） |

## Grafana Dashboard

若 Grafana/PostgreSQL 已啟動，可使用 `trade_analytics` DB 建立報表。

已驗證的 Dashboard 內容：

- Balance / Trading P&L / Win Rate / Profit Factor
- XAUUSDm 價格圖，支援 H1/M5 切換
- 交易進出場 annotations
- Monthly P&L
- Trade History table

> Grafana 原生 Candlestick panel 互動能力有限，適合績效監控；若要做 TradingView/MT5 風格交易復盤，建議另建前端 app。

## 遠端監看 (VNC)

- **網址**: `http://localhost:6081/vnc.html`
- **密碼**: 使用部署環境提供的 `VNC_PWD`，不要寫入 repository
- **注意**: 登入後請確保已勾選 "Allow DLL imports"

## 執行策略驗證

```bash
docker exec trade-data-service wine python /app/download.py
```

---

## 目錄結構

| 路徑 | 說明 |
|:-----|:------|
| `mt5docker/` | 容器設定與啟動腳本 |
| `service/` | 模組化 application（Gateway、Realtime、History、Trade Query） |
| `service/etl/` | 交易分析 ETL 與 PostgreSQL schema |
| `service/infrastructure/` | MT5、IPC、status 與 observability adapters |
| `service/runtime/` | 三 process supervisor 與容器生命週期 |
| `service/entrypoints/` | Gateway、Tick Worker、History Worker composition roots |
| `service/config/` | YAML 設定檔 |
| `service/data/` | 運行時資料輸出（ticks/history） |
| `docs/` | API 規格、架構文件、開發日誌 |
| `tests/` | E2E 測試套件（pytest） |
| `core/` | 底層 MT5 連線管理 (connection_manager.py) |

---

## 關鍵配置 (Broker Info)

- **追蹤商品**: `XAUUSDm`, `EURUSDm`, `GBPUSDm`, `BTC`
- **內部服務埠號**: `8001`（RPyC Bridge，僅 Docker network）
- **API Port**: `8090`（請使用實際主機 IP 訪問）
- **Broker**: Exness（SymbolResolver 自動解析）
- **Readonly API Key**: 由 `READONLY_API_KEY` 環境變數注入，不提交 Git

---

## 版本歷程

| 版本 | 日期 | 亮點 |
|:----|:----:|:------|
| v1.0 | 2026-03-23 | 初始版本：Docker + MT5 + VNC |
| v1.1 | 2026-05-04 | API 閘道層 (Flask REST + WebSocket) |
| v1.2 | 2026-05-16 | E2E 測試框架 (50 tests) |
| **v2.0** | **2026-05-17** | MT5Client 統一連線管理、HistoryService 穩定化 (83 行, -98% cycle time)、4 商品全追蹤 |
| **v2.1** | **2026-06-13** | 只讀交易查詢 API、PostgreSQL ETL、Grafana 交易分析管道 |

---

*README v2.1 — Updated 2026-06-13*
