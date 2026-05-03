#!/bin/bash
# 從 accounts.json 同步帳密到 mt5cfg.ini
# 在啟動 MT5 前自動帶入最新帳密，避免敏感資訊寫死在 git repo

ACCOUNTS_FILE="/app/service/config/accounts.json"
MT5CFG_FILE="/mt5docker/mt5cfg.ini"

if [ ! -f "$ACCOUNTS_FILE" ]; then
    echo "[SYNC] accounts.json not found at $ACCOUNTS_FILE, skipping"
    exit 0
fi

# 用 container 內的 Linux Python（不需 wine），只做 JSON 解析 + INI 寫入
python3 -c "
import json

with open('$ACCOUNTS_FILE') as f:
    data = json.load(f)

provider = data['providers'][data['active_provider']]

cfg = '''[Common]
Login={login}
Password={password}
Server={server}
ProxyEnable=0
CertPassword=
NewsEnable=0

[Charts]
MaxBars=1000000
PrintColor=0

[Experts]
AllowLiveTrading=1
AllowDllImport=1
Enabled=1
Account=1
Profile=1
'''.format(**provider)

with open('$MT5CFG_FILE', 'w') as f:
    f.write(cfg)

print('[SYNC] mt5cfg.ini synced from accounts.json')
" 2>&1 || echo "[SYNC] sync failed, using existing mt5cfg.ini"
