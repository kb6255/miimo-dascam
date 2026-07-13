#!/bin/bash
# ============================================================
# DashCam 一键配置脚本（最简方案）
# 用途：在树莓派上直接运行，一步完成所有配置
# 用法：sudo bash setup.sh
# ============================================================

set -euo pipefail

echo "============================================"
echo "  DashCam 一键配置"
echo "============================================"
echo ""

# ---- 检查环境 ----
if [[ $EUID -ne 0 ]]; then
    echo "错误: 请使用 sudo 运行"
    echo ""
    echo "正确用法:"
    echo "  1. 将整个项目复制到树莓派"
    echo "  2. cd 到项目目录"
    echo "  3. sudo bash setup.sh"
    exit 1
fi

APP_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---- 1. 系统依赖 ----
echo "[1/4] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-opencv \
    libxcb1 libxkbcommon-x11-0 libgl1-mesa-glx \
    libegl1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xfixes0 \
    xserver-xorg labwc wlr-randr 2>/dev/null || true

# ---- 2. Python 依赖 ----
echo "[2/4] 安装 Python 依赖..."
pip3 install --break-system-packages -r "$APP_DIR/requirements.txt" 2>/dev/null || \
    pip3 install -r "$APP_DIR/requirements.txt" 2>/dev/null || true

# ---- 3. 静默启动 ----
echo "[3/4] 配置静默启动..."

CMDLINE=""
[[ -f /boot/firmware/cmdline.txt ]] && CMDLINE="/boot/firmware/cmdline.txt"
[[ -f /boot/cmdline.txt ]] && CMDLINE="/boot/cmdline.txt"

if [[ -n "$CMDLINE" ]]; then
    cp "$CMDLINE" "${CMDLINE}.bak"
    cat > "$CMDLINE" << 'EOF'
console=tty1 root=PARTUUID=XXXXXXXX-02 rootfstype=ext4 fsck.repair=yes rootwait quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0
EOF
    # 修复 PARTUUID
    ROOT_DEV=$(mount | grep ' / ' | awk '{print $1}')
    PARTUUID=$(blkid -s PARTUUID -o value "$ROOT_DEV" 2>/dev/null || true)
    [[ -n "$PARTUUID" ]] && sed -i "s/XXXXXXXX-02/$PARTUUID/" "$CMDLINE"
fi

# 禁用 Plymouth
systemctl disable plymouth 2>/dev/null || true
systemctl stop plymouth 2>/dev/null || true

# 清空登录提示
echo "" > /etc/motd
echo "" > /etc/issue

# ---- 4. 开机自启 ----
echo "[4/4] 配置开机自启动..."

chmod +x "$APP_DIR/run.sh"

# systemd 服务（自动获取当前用户名）
APP_USER=$(logname 2>/dev/null || whoami)
APP_HOME=$(eval echo "~$APP_USER")

cat > /etc/systemd/system/dashcam.service << SERVICEEOF
[Unit]
Description=DashCam
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
User=$APP_USER
Environment=DISPLAY=:0
Environment=QT_QPA_PLATFORM=xcb
Environment=XAUTHORITY=$APP_HOME/.Xauthority
Environment=WAYLAND_DISPLAY=wayland-0
WorkingDirectory=$APP_DIR
ExecStartPre=/bin/sleep 5
ExecStart=/bin/bash $APP_DIR/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
SERVICEEOF

systemctl daemon-reload
systemctl enable dashcam.service

# 自动登录
raspi-config nonint do_autologin 0 2>/dev/null || true

# ---- 完成 ----
echo ""
echo "============================================"
echo "  配置完成！"
echo "============================================"
echo ""
echo "  程序目录: $APP_DIR"
echo "  服务:     dashcam.service"
echo ""
echo "  手动控制:"
echo "    sudo systemctl start dashcam"
echo "    sudo systemctl stop dashcam"
echo "    sudo journalctl -u dashcam -f"
echo ""
echo "  重启后自动运行: sudo reboot"
echo ""
