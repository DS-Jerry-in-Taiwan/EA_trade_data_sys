FROM hpdeandrade/mt5docker:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    novnc websockify net-tools x11vnc tightvncserver openbox curl \
    tint2 pcmanfm autocutsel xclip dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

# Lightweight desktop shell: window taskbar/menu plus desktop icons. Openbox
# remains the window manager; this does not add a second session manager.
COPY desktop/tint2rc /root/.config/tint2/tint2rc

# The RPyC server is launched by Wine's Windows Python. Its packages are
# independent of the Linux runner requirements and must be built into the
# image. The requirements file pins the validated Windows runtime versions.
COPY wine-requirements.txt /tmp/wine-requirements.txt
RUN wine C:/Python/python.exe -m pip install --no-cache-dir -r Z:/tmp/wine-requirements.txt \
    && wine C:/Python/python.exe -c "import MetaTrader5, pymt5linux, rpyc"

# 將密碼檔存放在 /root/.vnc/ (不受掛載影響的地方)
ARG VNC_PWD=jerry1234
RUN mkdir -p /root/.vnc &&     printf "${VNC_PWD}\n${VNC_PWD}\n" | vncpasswd /root/.vnc/passwd &&     chmod 600 /root/.vnc/passwd
