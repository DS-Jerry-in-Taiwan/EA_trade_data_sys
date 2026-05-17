# MT5 Trade Data Downloader (Linux/Docker)

**版本**: v2.0 (2026-05-17)
**描述**: Docker 雙容器架構的 MT5 量化交易數據服務，提供 REST API + WebSocket 即時報價與歷史 K 線。

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
│                 python-runner                        │
│  ┌────────────────────┐   │                         │
│  │   MT5Client        │◄──┘                         │
│  │   (thread-safe)    │                             │
│  └──┬──────┬──────┬───┘                             │
│     │      │      │                                 │
│  ┌──▼──┐┌──▼──┐┌──▼──────────┐  ┌────────────────┐ │
│  │Tick ││Hist ││Account      │  │ ChartService   │ │
│  │Service│Service│Service      │  │ (K線圖生成)    │ │
│  └──┬──┘└──┬──┘└──────┬───────┘  └────────────────┘ │
│     │      │          │                              │
│     └──────┴──────────┘                              │
│               │                                      │
│        ┌──────▼──────┐                               │
│        │ API Gateway │  REST :8090 + WebSocket       │
│        │ (Flask-SIO) │  /api/v1/*                    │
│        └─────────────┘                               │
└──────────────────────────────────────────────────────┘
```

### 核心元件

| 元件 | 語言 | 角色 |
|:-----|:-----|:-----|
| MT5 Terminal | Wine/Python | 數據源，透過 MetaTrader5 Python API |
| RPyC Proxy (pymt5linux) | Python | 跨容器 RPC 橋接 (Port 8001) |
| MT5Client | Python | thread-safe 統一連線管理 (service/core/mt5_client.py) |
| TickService | Python | 60s 輪詢即時報價 (bid/ask) 寫入 JSON |
| HistoryService | Python | 60s 增量抓取 K 線，copy_rates_from_pos(0,100)，CSV 去重合併 |
| AccountService | Python | 帳戶資訊、持倉、委託、成交歷史查詢 |
| ChartService | Python | K 線圖生成 (matplotlib → base64 PNG) |
| API Gateway | Python (Flask) | REST API + WebSocket (Flask-SocketIO) Port 8090 |

---

## REST API 端點

| 方法 | 端點 | 說明 |
|:----|:-----|:------|
| GET | `/api/v1/health` | 健康檢查 |
| GET | `/api/v1/ticks/<symbol>` | 即時報價 (Bid/Ask) |
| GET | `/api/v1/rates/<symbol>?timeframe=M5&days=7&limit=100` | K 線歷史（CSV 快取，支援分頁） |
| GET | `/api/v1/rates/<symbol>/query?start_time=...&end_time=...&timeframe=M5` | 直接查詢 MT5 指定時間範圍（回測用） |
| GET | `/api/v1/symbols` | 列出所有追蹤商品（含 MT5 描述與位數） |
| GET | `/api/v1/openapi.yaml` | OpenAPI v3 規格文件 |
| WS | `/ws` | 連線後可 subscribe/unsubscribe 即時 tick 推送 |

---

## 追蹤商品

| Symbol | Tick | M5 | M15 | H1 |
|:-------|:----:|:--:|:---:|:--:|
| XAUUSDm | ✅ | ✅ | ✅ | ✅ |
| BTCUSDm | ✅ | ✅ | ✅ | ✅ |
| EURUSDm | ✅ | ✅ | ✅ | ✅ |
| GBPUSDm | ✅ | ✅ | ✅ | ✅ |

---

## 快速啟動

```bash
cd mt5docker
docker compose up -d
```

## 遠端監看 (VNC)

- **網址**: `http://localhost:6081/vnc.html`
- **密碼**: `jerry1234` (VNC 驗證時請輸入 `jerry123`)
- **注意**: 登入後請確保已勾選 "Allow DLL imports"

## 執行策略驗證

```bash
docker exec python-runner wine python /app/download.py
```

---

## 目錄結構

| 路徑 | 說明 |
|:-----|:------|
| `mt5docker/` | 容器設定與啟動腳本 |
| `service/` | Python 微服務（tick/history/account/chart/api_gateway） |
| `service/core/` | 共用核心（MT5Client）— namespace package |
| `service/config/` | YAML 設定檔 |
| `service/data/` | 運行時資料輸出（ticks/history） |
| `docs/` | API 規格、架構文件、開發日誌 |
| `tests/` | E2E 測試套件（pytest） |
| `core/` | 底層 MT5 連線管理 (connection_manager.py) |

---

## 關鍵配置 (Broker Info)

- **黃金代碼**: `XAUUSDm`
- **服務埠號**: `8001` (RPyC Bridge)
- **API Port**: `8090`

---

## 版本歷程

| 版本 | 日期 | 亮點 |
|:----|:----:|:------|
| v1.0 | 2026-03-23 | 初始版本：Docker + MT5 + VNC |
| v1.1 | 2026-05-04 | API 閘道層 (Flask REST + WebSocket) |
| v1.2 | 2026-05-16 | E2E 測試框架 (50 tests) |
| **v2.0** | **2026-05-17** | MT5Client 統一連線管理、HistoryService 穩定化 (83 行, -98% cycle time)、4 商品全追蹤 |

---

*README v2.0 — Updated 2026-05-17*
