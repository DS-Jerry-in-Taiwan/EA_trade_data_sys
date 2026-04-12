#!/bin/bash
export DISPLAY=:100
pkill -9 -f Xvfb; pkill -9 -f x11vnc; pkill -9 -f websockify; pkill -9 -f terminal64.exe; pkill -9 -f mt5setup.exe; rm -f /tmp/.X100-lock

# 1. 啟動顯示與 VNC
Xvfb :100 -ac -screen 0 1024x768x24 &
sleep 2
openbox &
x11vnc -display :100 -forever -shared -dontdisconnect -rfbport 5901 -rfbauth /root/.vnc/passwd &
websockify --web /usr/share/novnc 6081 localhost:5901 &

echo '[SYSTEM] VNC Layer Online. Checking MT5...'

# 2. 自動檢查/安裝 MT5
MT5_EXE='/opt/wineprefix/drive_c/Program Files/MetaTrader 5/terminal64.exe'
if [ ! -f "$MT5_EXE" ]; then
    echo '[INSTALL] MT5 missing! Reinstalling...'
    curl -L -o /tmp/mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.ltd/mt5/mt5setup.exe
    wine /tmp/mt5setup.exe /S
    until [ -f "$MT5_EXE" ]; do sleep 5; done
    echo '[INSTALL] DONE.'
fi

# 3. 啟動 MT5
cd '/opt/wineprefix/drive_c/Program Files/MetaTrader 5'
wine terminal64.exe /portable /config:/mt5docker/mt5cfg.ini &

# 4. 啟動 Proxy (核心修復：綁定 0.0.0.0 以供宿主機連線)
sleep 15
wine python -m pymt5linux --host 0.0.0.0 --allow-all-attrs --port 8001 C:/Python/python.exe &

echo '[SYSTEM] All services online. Watchdog active.'
while true; do
    if ! pgrep -f terminal64.exe > /dev/null; then wine "$MT5_EXE" /portable /config:/mt5docker/mt5cfg.ini &; fi
    sleep 30
done
