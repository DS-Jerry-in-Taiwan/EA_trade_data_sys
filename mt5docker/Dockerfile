FROM hpdeandrade/mt5docker:latest
USER root
RUN apt-get update && apt-get install -y novnc websockify net-tools x11vnc tightvncserver openbox curl && apt-get clean

# 將密碼檔存放在 /root/.vnc/ (不受掛載影響的地方)
ARG VNC_PWD=jerry1234
RUN mkdir -p /root/.vnc &&     printf "${VNC_PWD}\n${VNC_PWD}\n" | vncpasswd /root/.vnc/passwd &&     chmod 600 /root/.vnc/passwd
