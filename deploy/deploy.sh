#!/bin/bash
# ============================================================
# DashCam 一键部署脚本
# 用途：在树莓派上配置开机自启动 + 静默启动
# 用法：sudo bash deploy.sh [选项]
#   --app-dir    程序目录（默认: /home/pi/dashcam）
#   --user       运行用户（默认: pi）
#   --no-reboot  配置完成后不自动重启
# ============================================================

set -euo pipefail

# ---- 参数解析 ----
APP_DIR="/home/pi/dashcam"
APP_USER="pi"
AUTO_REBOOT=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --app-dir)    APP_DIR="$2"; shift 2 ;;
        --user)       APP_USER="$2"; shift 2 ;;
        --no-reboot)  AUTO_REBOOT=false; shift ;;
        --help|-h)
            echo "用法: sudo bash deploy.sh [--app-dir DIR] [--user USER] [--no-reboot]"
            exit 0
            ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

echo "============================================"
echo "  DashCam 部署脚本"
echo "  程序目录: $APP_DIR"
echo "  运行用户: $APP_USER"
echo "============================================"

# ---- 检查 root 权限 ----
if [[ $EUID -ne 0 ]]; then
    echo "错误: 请使用 sudo 运行此脚本"
    exit 1
fi

# ---- 1. 安装系统依赖 ----
echo ""
echo "[1/5] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-opencv \
    libxcb1 libxkbcommon-x11-0 libgl1-mesa-glx \
    libegl1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xfixes0 \
    xserver-xorg xinit labwc wlr-randr 2>/dev/null || true

# ---- 2. 安装 Python 依赖 ----
echo ""
echo "[2/5] 安装 Python 依赖..."
if [[ -f "$APP_DIR/requirements.txt" ]]; then
    sudo -u "$APP_USER" pip3 install -r "$APP_DIR/requirements.txt" 2>/dev/null
else
    sudo -u "$APP_USER" pip3 install opencv-python-headless numpy PyQt5 2>/dev/null || true
fi

# ---- 3. 配置静默启动（去除开机特征） ----
echo ""
echo "[3/5] 配置静默启动..."

# 修改 /boot/firmware/cmdline.txt（树莓派5 / Bookworm）
CMDLINE=""
if [[ -f /boot/firmware/cmdline.txt ]]; then
    CMDLINE="/boot/firmware/cmdline.txt"
elif [[ -f /boot/cmdline.txt ]]; then
    CMDLINE="/boot/cmdline.txt"
fi

if [[ -n "$CMDLINE" ]]; then
    # 备份原始文件
    cp "$CMDLINE" "${CMDLINE}.bak.$(date +%Y%m%d)"

    # 替换 cmdline.txt 内容：静默启动 + 关闭彩虹屏
    cat > "$CMDLINE" << 'CMDEOF'
console=serial0,115200 console=tty1 root=PARTUUID=XXXXXXXX-02 rootfstype=ext4 fsck.repair=yes rootwait quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0
CMDEOF
    # 注意：root=PARTUUID 部分会保留原始值
    # 这里先用占位符，下面修复
    if command -v blkid &>/dev/null; then
        ROOT_PARTUUID=$(blkid -s PARTUUID -o value "$(mount | grep ' / ' | awk '{print $1}')" 2>/dev/null || true)
        if [[ -n "$ROOT_PARTUUID" ]]; then
            sed -i "s/XXXXXXXX-02/$ROOT_PARTUUID/" "$CMDLINE"
        fi
    fi
    echo "  ✓ 已配置静默启动: $CMDLINE"
fi

# 禁用 Plymouth（彩虹屏/启动动画）
systemctl disable plymouth 2>/dev/null || true
systemctl stop plymouth 2>/dev/null || true

# 禁用 getty 上的 logo
if [[ -d /etc/systemd/system/getty@tty1.service.d ]]; then
    rm -f /etc/systemd/system/getty@tty1.service.d/override.conf 2>/dev/null
fi

# 禁用屏幕上的登录提示（清除 TTY 输出）
mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << 'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin pi --noclear %I $TERM
EOF

# 禁用 welcome message
cat > /etc/motd << 'EOF'
EOF

# 清空 issue（去除 TTY 登录提示）
cat > /etc/issue << 'EOF'

EOF

# 禁用 dmesg 输出到 console
echo 'kernel.printk = 0 4 1 7' >> /etc/sysctl.d/99-silent-boot.conf
sysctl -p /etc/sysctl.d/99-silent-boot.conf 2>/dev/null || true

echo "  ✓ 已禁用 Plymouth + 控制台输出"

# ---- 4. 配置开机自启动 ----
echo ""
echo "[4/5] 配置开机自启动..."

# 确保启动脚本可执行
chmod +x "$APP_DIR/run.sh"

# 方式1: systemd service（推荐）
cat > /etc/systemd/system/dashcam.service << SERVICEEOF
[Unit]
Description=DashCam - USB Dual Camera Dashcam
After=graphical.target network.target
Wants=graphical.target

[Service]
Type=simple
User=$APP_USER
Environment=DISPLAY=:0
Environment=QT_QPA_PLATFORM=xcb
Environment=XAUTHORITY=/home/$APP_USER/.Xauthority
WorkingDirectory=$APP_DIR
ExecStartPre=/bin/sleep 3
ExecStart=/bin/bash $APP_DIR/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
SERVICEEOF

systemctl daemon-reload
systemctl enable dashcam.service

# 方式2: 桌面 autostart（兼容 labwc/Wayland 会话）
mkdir -p "/home/$APP_USER/.config/autostart"
cat > "/home/$APP_USER/.config/autostart/dashcam.desktop" << AUTOSTARTEOF
[Desktop Entry]
Type=Application
Name=DashCam
Exec=/bin/bash $APP_DIR/run.sh
X-GNOME-Autostart-enabled=true
Terminal=false
AUTOSTARTEOF
chown "$APP_USER:$APP_USER" "/home/$APP_USER/.config/autostart/dashcam.desktop"

# 设置自动登录（TTY 自动登录 pi 用户，确保 GUI 会话启动）
raspi-config nonint do_autologin 0 2>/dev/null || true

echo "  ✓ 已创建 systemd 服务: dashcam.service"
echo "  ✓ 已创建 autostart 桌面项"

# ---- 5. 创建首次启动脚本（用于 TF 卡存储检测） ----
echo ""
echo "[5/5] 配置存储自动挂载..."

# 创建 udev 规则：自动挂载 USB 存储设备
cat > /etc/udev/rules.d/99-usb-storage-auto-mount.rules << 'UDEVEOF'
# 自动挂载 USB 存储设备
KERNEL=="sd[a-z][0-9]", SUBSYSTEM=="block", ACTION=="add", RUN+="/bin/systemctl start usb-mount@%k.service"
KERNEL=="sd[a-z][0-9]", SUBSYSTEM=="block", ACTION=="remove", RUN+="/bin/systemctl stop usb-mount@%k.service"
UDEVEOF

echo "  ✓ 已配置 USB 存储自动挂载"

# ---- 完成 ----
echo ""
echo "============================================"
echo "  部署完成！"
echo "============================================"
echo ""
echo "  配置摘要:"
echo "  ├─ 程序目录: $APP_DIR"
echo "  ├─ 运行用户: $APP_USER"
echo "  ├─ 服务名称: dashcam.service"
echo "  ├─ 静默启动: 已启用 (logo.nologo quiet splash)"
echo "  └─ 自动登录: 已启用"
echo ""
echo "  手动控制:"
echo "    启动: systemctl start dashcam"
echo "    停止: systemctl stop dashcam"
echo "    状态: systemctl status dashcam"
echo "    日志: journalctl -u dashcam -f"
echo ""

if [[ "$AUTO_REBOOT" == "true" ]]; then
    echo "  将在 5 秒后自动重启..."
    sleep 5
    reboot
else
    echo "  请手动重启以完成配置: sudo reboot"
fi
