@echo off
title Cinema SSH Tunnel
setlocal
set "CINEMA_SSH_TARGET="
set /p "CINEMA_SSH_TARGET=SSH target (user@IP or user@hostname): "
powershell -NoProfile -Command "if ($env:CINEMA_SSH_TARGET -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*$') { exit 1 }"
if errorlevel 1 (
  echo Invalid SSH target. Example: root@203.0.113.10
  pause
  exit /b 1
)
echo Enter the VPS SSH password if prompted, then keep this window open.
echo Movie wall: http://127.0.0.1:8095
echo Find movies: http://127.0.0.1:8097/discover/
echo OpenList:   http://127.0.0.1:5244/openlist/
echo Prowlarr:   http://127.0.0.1:9696/search/
echo Sonarr:     http://127.0.0.1:8989/sonarr/
echo qB:         http://127.0.0.1:8080
echo Emby admin: http://127.0.0.1:8096
ssh -N -T -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:8097:127.0.0.1:8097 -L 127.0.0.1:8095:127.0.0.1:8095 -L 127.0.0.1:5244:127.0.0.1:5244 -L 127.0.0.1:9696:127.0.0.1:9696 -L 127.0.0.1:8989:127.0.0.1:8989 -L 127.0.0.1:8080:127.0.0.1:8080 -L 127.0.0.1:8096:127.0.0.1:8096 "%CINEMA_SSH_TARGET%"
echo SSH tunnel closed. VPS services are still running.
pause
