#!/bin/bash
# ============================================================
# DashCam 首次启动脚本
# 用途：在烧录镜像后的首次启动时自动执行配置
# 位置：/usr/local/bin/dashcam-first-boot.sh
# ============================================================

LOG="/var/log/dashcam-first-boot.log"
APP_DIR="/home/pi/dashcam"
MARKER="/var/lib/dashcam-first-boot-done"

exec > >(tee -a "$LOG") 2>&1
echo "============================================"
echo "  DashCam First Boot - $(date)"
echo "============================================"

# 如果已经执行过，跳过
if [[ -f "$MARKER" ]]; then
    echo "首次启动已执行过，跳过配置"
    exit 0
fi

echo "[1/4] 检查网络连接..."
# 等待网络就绪（最多 60 秒）
for i in $(seq 1 60); do
    if ping -c 1 8.8.8.8 &>/dev/null; then
        echo "  ✓ 网络已连接"
        break
    fi
    sleep 1
done

echo "[2/4] 安装依赖..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-opencv \
    libxcb1 libxkbcommon-x11-0 libgl1-mesa-glx 2>/dev/null || true

pip3 install --break-system-packages opencv-python-headless numpy PyQt5 2>/dev/null || \
    pip3 install opencv-python-headless numpy PyQt5 2>/dev/null || true

echo "[3/4] 配置系统..."
# 确保自动登录
raspi-config nonint do_autologin 0 2>/dev/null || true

# 确保程序目录存在
if [[ -d "$APP_DIR" ]]; then
    chmod +x "$APP_DIR/run.sh"
    chown -R pi:pi "$APP_DIR"
fi

echo "[4/4] 启用 DashCam 服务..."
systemctl daemon-reload
systemctl enable dashcam.service 2>/dev/null || true

# 标记完成
touch "$MARKER"
echo ""
echo "首次启动配置完成！"
