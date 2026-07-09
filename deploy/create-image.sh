#!/bin/bash
# ============================================================
# 创建 DashCam 自定义镜像
# 用途：基于官方树莓派镜像，预装 DashCam 软件
# 前置要求：树莓派官方镜像 (2024-xx-xx-raspios-bookworm-arm64.img.xz)
# ============================================================

set -euo pipefail

echo "============================================"
echo "  DashCam 自定义镜像构建工具"
echo "============================================"
echo ""

# ---- 参数 ----
BASE_IMAGE="${1:-}"
OUTPUT_IMAGE="dashcam-os.img"

if [[ -z "$BASE_IMAGE" ]]; then
    echo "用法: sudo bash create-image.sh <基础镜像路径>"
    echo ""
    echo "示例:"
    echo "  sudo bash create-image.sh 2024-11-19-raspios-bookworm-arm64.img"
    echo ""
    echo "步骤："
    echo "  1. 下载官方镜像: https://www.raspberrypi.com/software/operating-systems/"
    echo "  2. 解压: xz -d *.img.xz"
    echo "  3. 运行本脚本"
    exit 1
fi

if [[ $EUID -ne 0 ]]; then
    echo "错误: 请使用 sudo 运行"
    exit 1
fi

if [[ ! -f "$BASE_IMAGE" ]]; then
    echo "错误: 镜像文件不存在: $BASE_IMAGE"
    exit 1
fi

# ---- 找到镜像中的分区信息 ----
echo ""
echo "[1/5] 挂载基础镜像..."

# 使用 loop 设备
LOOP=$(losetup --find --show "$BASE_IMAGE")
echo "  镜像挂载为: $LOOP"

# 等待设备就绪
partprobe "$LOOP" 2>/dev/null || true
sleep 1

# 判断分区布局（boot 分区和 root 分区）
BOOT_PART="${LOOP}p1"
ROOT_PART="${LOOP}p2"

# 如果是旧版镜像（boot 和 root 在同一个分区）
if [[ ! -b "$BOOT_PART" ]]; then
    BOOT_PART=""
    ROOT_PART="${LOOP}p1"
    # 旧版镜像，boot 在 root 分区内
fi

# 挂载 root 分区
MOUNT_POINT=$(mktemp -d)
mkdir -p "$MOUNT_POINT"
mount "$ROOT_PART" "$MOUNT_POINT"

if [[ -n "$BOOT_PART" && -b "$BOOT_PART" ]]; then
    mkdir -p "$MOUNT_POINT/boot"
    mount "$BOOT_PART" "$MOUNT_POINT/boot"
fi

echo "  ✓ 挂载完成: $MOUNT_POINT"

# ---- 2. 安装软件 ----
echo ""
echo "[2/5] 安装 DashCam 软件..."

# 复制程序文件
cp -r ./app.py "$MOUNT_POINT/home/pi/dashcam/"
cp -r ./config.json "$MOUNT_POINT/home/pi/dashcam/"
cp -r ./run.sh "$MOUNT_POINT/home/pi/dashcam/"
cp -r ./requirements.txt "$MOUNT_POINT/home/pi/dashcam/"
chmod +x "$MOUNT_POINT/home/pi/dashcam/run.sh"
chown -R 1000:1000 "$MOUNT_POINT/home/pi/dashcam"

echo "  ✓ 已复制程序文件"

# ---- 3. 配置系统 ----
echo ""
echo "[3/5] 配置静默启动..."

# 修改 cmdline.txt
CMDLINE_FILE=""
if [[ -f "$MOUNT_POINT/boot/firmware/cmdline.txt" ]]; then
    CMDLINE_FILE="$MOUNT_POINT/boot/firmware/cmdline.txt"
elif [[ -f "$MOUNT_POINT/boot/cmdline.txt" ]]; then
    CMDLINE_FILE="$MOUNT_POINT/boot/cmdline.txt"
fi

if [[ -n "$CMDLINE_FILE" ]]; then
    cat > "$CMDLINE_FILE" << 'EOF'
console=serial0,115200 console=tty1 root=PARTUUID=XXXXXXXX-02 rootfstype=ext4 fsck.repair=yes rootwait quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0
EOF
    echo "  ✓ 已配置静默启动"
fi

# 禁用 Plymouth
chroot "$MOUNT_POINT" systemctl disable plymouth 2>/dev/null || true

# 清空登录提示
echo "" > "$MOUNT_POINT/etc/motd"
echo "" > "$MOUNT_POINT/etc/issue"

# 禁用控制台输出
mkdir -p "$MOUNT_POINT/etc/sysctl.d/"
cat > "$MOUNT_POINT/etc/sysctl.d/99-silent-boot.conf" << 'EOF'
kernel.printk = 0 4 1 7
EOF

echo "  ✓ 已配置系统"

# ---- 4. 配置自启动 ----
echo ""
echo "[4/5] 配置开机自启动..."

cat > "$MOUNT_POINT/etc/systemd/system/dashcam.service" << 'EOF'
[Unit]
Description=DashCam - USB Dual Camera Dashcam
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
User=pi
Environment=DISPLAY=:0
Environment=QT_QPA_PLATFORM=xcb
Environment=XAUTHORITY=/home/pi/.Xauthority
WorkingDirectory=/home/pi/dashcam
ExecStartPre=/bin/sleep 3
ExecStart=/bin/bash /home/pi/dashcam/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical.target
EOF

# 创建 autostart 桌面项
mkdir -p "$MOUNT_POINT/home/pi/.config/autostart"
cat > "$MOUNT_POINT/home/pi/.config/autostart/dashcam.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=DashCam
Exec=/bin/bash /home/pi/dashcam/run.sh
X-GNOME-Autostart-enabled=true
Terminal=false
EOF
chown -R 1000:1000 "$MOUNT_POINT/home/pi/.config"

echo "  ✓ 已配置自启动"

# ---- 5. 清理和卸载 ----
echo ""
echo "[5/5] 清理和卸载..."

# 清理包缓存
chroot "$MOUNT_POINT" apt-get clean 2>/dev/null || true
rm -rf "$MOUNT_POINT/var/lib/apt/lists/*" 2>/dev/null || true

# 清理日志
rm -rf "$MOUNT_POINT/var/log/"* 2>/dev/null || true

# 卸载分区
sync
if [[ -n "$BOOT_PART" && -b "$BOOT_PART" ]]; then
    umount "$MOUNT_POINT/boot"
fi
umount "$MOUNT_POINT"
rmdir "$MOUNT_POINT"

# 释放 loop 设备
losetup -d "$LOOP"

echo ""
echo "============================================"
echo "  镜像构建完成！"
echo "============================================"
echo ""
echo "  输出文件: $OUTPUT_IMAGE"
echo "  大小: $(du -h "$OUTPUT_IMAGE" 2>/dev/null | awk '{print $1}')"
echo ""
echo "  使用方法："
echo "    1. 刷入 TF 卡: sudo dd if=$OUTPUT_IMAGE of=/dev/sdX bs=4M status=progress"
echo "    2. 插入树莓派，开机即用"
echo ""
echo "  预装功能："
echo "    ✓ DashCam 开机自启动"
echo "    ✓ 静默启动（无 logo/控制台输出）"
echo "    ✓ 自动登录 pi 用户"
echo ""
