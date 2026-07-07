#!/bin/bash
# 双摄行车记录仪启动脚本
# 使用X11后端以兼容labwc/Wayland环境
export QT_QPA_PLATFORM=xcb
export XAUTHORITY=~/.Xauthority
cd "$(dirname "$0")"
exec python3 app.py
