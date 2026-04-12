# Troubleshooting Guide: MT5 Docker & VNC

## Issue: Black Screen in VNC Viewer (noVNC)
**Symptoms:** 
- The SSH tunnel (Local Port 6081) is connected.
- The noVNC web interface loads, but the screen is entirely black.
- Error codes like `-10005 (IPC timeout)` or `-10001 (Send Failed)` occur in Python scripts.

### Root Cause: Xvfb Lock File (.X100-lock)
The virtual display server (`Xvfb`) creates a lock file at `/tmp/.X100-lock` when it starts. If the container or process crashes, this file remains and prevents new Xvfb instances from properly initializing the display (`:100`). Without a functional X server, Wine cannot render the MT5 GUI, resulting in a black frame.

### Resolution Steps:
1. **Force Remove Lock File:**
   ```bash
   sudo rm -f /tmp/.X100-lock
   ```
2. **Restart Container Services:**
   Ensure `Xvfb`, `x11vnc`, and `websockify` (noVNC) are restarted in the correct order:
   ```bash
   # Inside the container
   Xvfb :100 -ac -screen 0 1024x768x24 &
   x11vnc -display :100 -forever -rfbport 5901 -rfbauth /mt5docker/passwd &
   websockify --web /usr/share/novnc 6081 localhost:5901 &
   ```
3. **Verify MT5 GUI:**
   Refresh the browser at `http://localhost:6081/vnc.html`.

## Issue: IPC Timeout / Connection Failed in Python
**Symptoms:** 
- MetaTrader5 library fails to initialize inside the container.

### Resolution:
- Ensure MT5 Terminal is running inside Wine.
- Enable **"Allow DLL imports"** and **"Allow automated trading"** in MT5 Options.
- Use `mt5.initialize(path="C:/Program Files/MetaTrader 5/terminal64.exe", portable=True)`.

---
*Last Updated: 2026-03-23*
