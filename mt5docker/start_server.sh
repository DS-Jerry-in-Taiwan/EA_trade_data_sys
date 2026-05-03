#!/bin/bash
export DISPLAY=:100
# 強制清理殘留鎖定檔與進程
echo '>>> Cleaning up environment...'
rm -f /tmp/.X100-lock
pkill -9 -f Xvfb; pkill -9 -f x11vnc; pkill -9 -f websockify; pkill -9 -f terminal64.exe; pkill -9 -f python || true

# 啟動螢幕渲染服務
echo '>>> Starting GUI services (Xvfb, VNC)...'
Xvfb :100 -ac -screen 0 1024x768x24 &
sleep 2; openbox &
x11vnc -display :100 -forever -shared -dontdisconnect -rfbport 5901 -rfbauth /root/.vnc/passwd &
websockify --web /usr/share/novnc 6081 localhost:5901 &

# 動態偵測 MT5 路徑
MT5_EXE=$(find /opt/wineprefix/drive_c/ -name terminal64.exe | head -n 1)
echo ">>> Found MT5 at: $MT5_EXE"

if [ -z "$MT5_EXE" ]; then
    echo '>>> MT5 missing, triggering silent install...'
    curl -L -o /tmp/mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.ltd/mt5/mt5setup.exe
    wine /tmp/mt5setup.exe /S
    until [ -f "$(find /opt/wineprefix/drive_c/ -name terminal64.exe | head -n 1)" ]; do sleep 5; done
    MT5_EXE=$(find /opt/wineprefix/drive_c/ -name terminal64.exe | head -n 1)
fi

# 同步帳密
bash /mt5docker/sync_mt5cfg.sh
# 啟動 MT5
cd "$(dirname "$MT5_EXE")"
echo '>>> Launching MT5 terminal...'
wine "${MT5_EXE}" /portable /config:/mt5docker/mt5cfg.ini &

# API Proxy 監控循環
echo '>>> Starting API Proxy...'
wine C:/Python/python.exe -m pymt5linux --host 0.0.0.0 --port 8001 C:/Python/python.exe &
sleep 3
echo '>>> Starting API Proxy monitoring loop...'
while true; do
  if ! pgrep -f "pymt5linux" > /dev/null; then
    echo "$(date): API Proxy is down, restarting..."
    wine C:/Python/python.exe -m pymt5linux --host 0.0.0.0 --port 8001 C:/Python/python.exe &
  fi
  sleep 30
done
