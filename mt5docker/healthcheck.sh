#!/bin/bash
echo 'Checking display services...'
netstat -antp | grep 5901 || echo 'VNC Server NOT running'
netstat -antp | grep 6081 || echo 'noVNC Proxy NOT running'
netstat -antp | grep 8002 || echo 'MT5 Proxy NOT running'
ps aux | grep -E 'Xvfb|terminal64.exe'
