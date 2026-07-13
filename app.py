import cv2
import numpy as np
import sys
import os
import json
import time
import subprocess
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QHBoxLayout, QVBoxLayout, QWidget,
    QPushButton, QListWidget, QListWidgetItem, QFrame, QSlider, QStyle,
    QDialog, QSpinBox, QCheckBox, QProgressBar, QComboBox, QMenu, QMessageBox
)
from PyQt5.QtGui import QImage, QPixmap, QFont
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal

# ====================== 配置 ======================
CONFIG_PATH = "/home/kongbin/usbcamra/config.json"
DEFAULT_CFG = {
    "video_width": 1280,
    "video_height": 720,
    "split_minute": 1,
    "enable_watermark": True,
    "warn_space_gb": 5,
    "play_mode": "single",
}

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_CFG, f, indent=2)
    return DEFAULT_CFG.copy()

def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def get_usb_dir():
    try:
        result = subprocess.run(["lsblk", "-o", "MOUNTPOINT,FSTYPE", "--noheadings"],
                                capture_output=True, text=True, timeout=3)
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            mp, fs = parts[0], parts[1]
            if fs in ("vfat", "exfat", "ntfs", "fat32") and mp.startswith("/media/"):
                test_file = os.path.join(mp, ".write_test.tmp")
                try:
                    with open(test_file, "w") as f:
                        f.write("test")
                    os.remove(test_file)
                    return os.path.join(mp, "record")
                except Exception:
                    pass
    except Exception:
        pass
    return os.path.expanduser("~/record_backup")

def cover_scale(pixmap, target_w, target_h):
    """等比缩放并裁剪居中，铺满目标区域"""
    if pixmap.isNull() or target_w <= 0 or target_h <= 0:
        return pixmap
    pw, ph = pixmap.width(), pixmap.height()
    scale = max(target_w / pw, target_h / ph)
    new_w = int(pw * scale)
    new_h = int(ph * scale)
    scaled = pixmap.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    x = (new_w - target_w) // 2
    y = (new_h - target_h) // 2
    return scaled.copy(x, y, target_w, target_h)


def clean_old_videos(save_root, target_free_gb):
    """循环录制：删除最旧的非锁定视频，直到剩余空间 >= target_free_gb"""
    for _ in range(100):  # 最多清理100轮，防止死循环
        free_gb = _get_free_space_gb(save_root)
        if free_gb >= target_free_gb:
            break
        # 收集所有非锁定视频，按创建时间排序（最旧在前）
        all_videos = []
        for fname in os.listdir(save_root):
            if fname.endswith(".mp4") and not fname.startswith("LOCK_"):
                fpath = os.path.join(save_root, fname)
                try:
                    fsize = os.path.getsize(fpath)
                    ctime = os.path.getctime(fpath)
                    all_videos.append((ctime, fpath, fsize))
                except OSError:
                    pass
        if not all_videos:
            break
        all_videos.sort()
        _, fpath, fsize = all_videos[0]
        try:
            os.remove(fpath)
        except OSError:
            pass


def _get_free_space_gb(path):
    try:
        stat = os.statvfs(path)
        return (stat.f_frsize * stat.f_bavail) / (1024 ** 3)
    except Exception:
        return 0

# ====================== 摄像头线程 ======================
class CameraThread(QThread):
    frame_ready = pyqtSignal(object)
    def __init__(self, device, name="cam"):
        super().__init__()
        self.device = device
        self.name = name
        self.running = True
        self.frame = None
        self.ready = False

    def run(self):
        retry = 0
        while self.running and retry < 5:
            cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
            if not cap.isOpened():
                retry += 1
                time.sleep(1)
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
            retry = 0
            self.ready = True
            while self.running:
                ret, frame = cap.read()
                if ret:
                    self.frame = frame
                    self.frame_ready.emit(frame)
                else:
                    time.sleep(0.01)
            cap.release()

    def stop(self):
        self.running = False


# ====================== 设置对话框 ======================
class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.result_cfg = {}
        self.setWindowTitle("设置")
        self.setFixedSize(400, 480)
        self.setStyleSheet("""
            QDialog{background:#222;color:#fff;}
            QLabel{font-size:14px;}
            QSpinBox{background:#333;color:#fff;border:1px solid #555;border-radius:4px;
                     padding:4px 8px;font-size:14px;}
            QComboBox{background:#333;color:#fff;border:1px solid #555;border-radius:4px;
                      padding:4px 8px;font-size:14px;}
            QCheckBox{font-size:14px;spacing:8px;}
            QCheckBox::indicator{width:18px;height:18px;}
            QProgressBar{background:#333;border:1px solid #555;border-radius:4px;
                         text-align:center;color:#fff;font-size:12px;}
            QProgressBar::chunk{background:#36c;border-radius:3px;}
            QPushButton{font-size:14px;padding:8px 20px;border-radius:6px;font-weight:bold;}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- 设置1：录制每段时长 ---
        lbl1 = QLabel("1. 录制每段时长")
        lbl1.setStyleSheet("font-weight:bold;font-size:15px;color:#58f;")
        layout.addWidget(lbl1)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("分段时长："))
        self.spin_split = QSpinBox()
        self.spin_split.setRange(1, 10)
        self.spin_split.setValue(cfg.get("split_minute", 1))
        self.spin_split.setSuffix(" 分钟")
        row1.addWidget(self.spin_split)
        row1.addStretch()
        layout.addLayout(row1)

        # --- 设置2：水印开关 ---
        lbl2 = QLabel("2. 时间水印")
        lbl2.setStyleSheet("font-weight:bold;font-size:15px;color:#58f;")
        layout.addWidget(lbl2)

        self.chk_watermark = QCheckBox("显示时间水印")
        self.chk_watermark.setChecked(cfg.get("enable_watermark", True))
        layout.addWidget(self.chk_watermark)

        # --- 设置3：播放模式 ---
        lbl3 = QLabel("3. 视频播放模式")
        lbl3.setStyleSheet("font-weight:bold;font-size:15px;color:#58f;")
        layout.addWidget(lbl3)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("播放方式："))
        self.combo_play = QComboBox()
        self.combo_play.addItems(["单次播放", "连续播放"])
        if cfg.get("play_mode", "single") == "continuous":
            self.combo_play.setCurrentIndex(1)
        else:
            self.combo_play.setCurrentIndex(0)
        row3.addWidget(self.combo_play)
        row3.addStretch()
        layout.addLayout(row3)

        # --- 设置4：TF卡空间 ---
        lbl4 = QLabel("4. 存储空间")
        lbl4.setStyleSheet("font-weight:bold;font-size:15px;color:#58f;")
        layout.addWidget(lbl4)

        # 获取磁盘空间
        save_root = get_usb_dir()
        total_gb, free_gb = self._get_disk_space(save_root)

        # Windows 风格的容量显示
        disk_info = QHBoxLayout()
        disk_info.setSpacing(12)

        # 图标区
        icon_lbl = QLabel("💾")
        icon_lbl.setStyleSheet("font-size:32px;")
        icon_lbl.setFixedWidth(40)
        disk_info.addWidget(icon_lbl)

        # 详情区
        detail = QVBoxLayout()
        detail.setSpacing(4)

        used_gb = total_gb - free_gb
        used_pct = int((used_gb / total_gb * 100)) if total_gb > 0 else 0

        # 容量文字
        cap_text = f"可用 {free_gb:.1f} GB / 共 {total_gb:.1f} GB"
        cap_lbl = QLabel(cap_text)
        cap_lbl.setStyleSheet("font-size:13px;color:#ccc;")
        detail.addWidget(cap_lbl)

        # 进度条（蓝色 = 已用，灰色 = 剩余）
        self.progress_disk = QProgressBar()
        self.progress_disk.setRange(0, 100)
        self.progress_disk.setValue(used_pct)
        self.progress_disk.setFixedHeight(20)
        self.progress_disk.setFormat(f"已用 {used_pct}%")
        detail.addWidget(self.progress_disk)

        # 路径
        path_lbl = QLabel(f"路径: {save_root}")
        path_lbl.setStyleSheet("font-size:11px;color:#888;")
        detail.addWidget(path_lbl)

        disk_info.addLayout(detail, 1)
        layout.addLayout(disk_info)

        # 清空存储空间按钮
        btn_clear_row = QHBoxLayout()
        btn_clear_row.addStretch()

        btn_clear_storage = QPushButton("清空存储空间")
        btn_clear_storage.setFixedHeight(40)
        btn_clear_storage.setStyleSheet("""
            QPushButton{background:#c33;color:#fff;font-size:14px;font-weight:bold;
                        border-radius:6px;padding:0 16px;}
            QPushButton:hover{background:#e44;}
        """)
        btn_clear_storage.clicked.connect(lambda: self._clear_storage(save_root))
        btn_clear_row.addWidget(btn_clear_storage)
        btn_clear_row.addStretch()
        layout.addLayout(btn_clear_row)

        # --- 保存按钮 ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet("background:#555;color:#fff;")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("保存")
        btn_save.setStyleSheet("background:#36c;color:#fff;")
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)

        layout.addLayout(btn_row)

    def _get_disk_space(self, path):
        try:
            stat = os.statvfs(path)
            total = (stat.f_blocks * stat.f_frsize) / (1024 ** 3)
            free = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
            return total, free
        except Exception:
            return 0, 0

    def _clear_storage(self, save_root):
        """安全清空存储空间，只删除视频文件，保留目录结构"""
        from PyQt5.QtWidgets import QMessageBox
        
        def create_styled_msgbox(parent, title, text, icon, buttons):
            """创建带样式的 QMessageBox"""
            from PyQt5.QtWidgets import QPushButton as QPushButtonType
            
            msg_box = QMessageBox(parent)
            msg_box.setWindowTitle(title)
            msg_box.setText(text)
            msg_box.setIcon(icon)
            msg_box.setStandardButtons(buttons)
            
            # 设置对话框样式
            msg_box.setStyleSheet("""
                QMessageBox {
                    background-color: #222;
                }
                QLabel {
                    color: #fff;
                    font-size: 14px;
                    min-width: 300px;
                }
            """)
            
            # 使用 findChildren 查找所有 QPushButton 并设置样式
            for button in msg_box.findChildren(QPushButtonType):
                button.setStyleSheet("""
                    QPushButton {
                        background-color: #36c;
                        color: white;
                        border: none;
                        border-radius: 4px;
                        padding: 8px 20px;
                        font-size: 14px;
                        font-weight: bold;
                        min-width: 80px;
                    }
                    QPushButton:hover {
                        background-color: #48e;
                    }
                    QPushButton:pressed {
                        background-color: #25a;
                    }
                """)
            
            return msg_box
        
        # 确认对话框
        msg_box = create_styled_msgbox(
            self,
            "确认清空",
            "确定要清空所有存储的视频吗？\n\n此操作将删除：\n- 所有普通录像文件 (.mp4)\n- 所有截图文件 (.jpg, .png)\n\n注意：锁定的视频将被保留。",
            QMessageBox.Question,
            QMessageBox.Yes | QMessageBox.No
        )
        msg_box.setDefaultButton(QMessageBox.No)
        
        reply = msg_box.exec_()
        
        if reply != QMessageBox.Yes:
            return
        
        deleted_count = 0
        deleted_size = 0
        
        try:
            # 1. 删除普通录像文件（不以LOCK_开头的.mp4文件）
            if os.path.exists(save_root):
                for fname in os.listdir(save_root):
                    if fname.endswith(".mp4") and not fname.startswith("LOCK_"):
                        fpath = os.path.join(save_root, fname)
                        try:
                            fsize = os.path.getsize(fpath)
                            os.remove(fpath)
                            deleted_count += 1
                            deleted_size += fsize
                        except Exception:
                            pass
            
            # 2. 删除截图文件
            photos_dir = os.path.join(save_root, "photos")
            if os.path.exists(photos_dir):
                for fname in os.listdir(photos_dir):
                    if fname.endswith((".jpg", ".png")):
                        fpath = os.path.join(photos_dir, fname)
                        try:
                            fsize = os.path.getsize(fpath)
                            os.remove(fpath)
                            deleted_count += 1
                            deleted_size += fsize
                        except Exception:
                            pass
            
            # 更新显示
            deleted_mb = deleted_size / (1024 * 1024)
            msg_box = create_styled_msgbox(
                self,
                "清空完成",
                f"已成功删除 {deleted_count} 个文件\n释放空间: {deleted_mb:.1f} MB",
                QMessageBox.Information,
                QMessageBox.Ok
            )
            msg_box.exec_()
            
            # 刷新磁盘空间显示
            total_gb, free_gb = self._get_disk_space(save_root)
            used_gb = total_gb - free_gb
            used_pct = int((used_gb / total_gb * 100)) if total_gb > 0 else 0
            
            self.progress_disk.setValue(used_pct)
            self.progress_disk.setFormat(f"已用 {used_pct}%")
            
            # 更新容量文字（找到并更新cap_lbl）
            for i in range(self.layout().count()):
                item = self.layout().itemAt(i)
                if item and item.layout():
                    for j in range(item.layout().count()):
                        child = item.layout().itemAt(j)
                        if child and child.widget() and isinstance(child.widget(), QLabel):
                            lbl = child.widget()
                            if "可用" in lbl.text() and "GB" in lbl.text():
                                lbl.setText(f"可用 {free_gb:.1f} GB / 共 {total_gb:.1f} GB")
                                break
            
        except Exception as e:
            msg_box = create_styled_msgbox(
                self,
                "清空失败",
                f"清空存储空间时出错：{str(e)}",
                QMessageBox.Warning,
                QMessageBox.Ok
            )
            msg_box.exec_()

    def _save(self):
        self.result_cfg = {
            "split_minute": self.spin_split.value(),
            "enable_watermark": self.chk_watermark.isChecked(),
            "play_mode": "continuous" if self.combo_play.currentIndex() == 1 else "single",
        }
        self.accept()


# ====================== 播放器覆盖层 ======================
class PlayerOverlay(QWidget):
    """覆盖在监控画面上的视频/图片播放器"""
    file_changed = pyqtSignal(str)  # 播放新文件时发出
    playback_finished = pyqtSignal()  # 播放结束时发出

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        self.cap = None
        self.timer = None
        self.playing = False
        self._last_rgb = None
        self._seeking = False  # 用户正在拖动进度条
        self._total_frames = 0
        self._fps = 30
        self._current_path = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部信息栏
        self.info_bar = QHBoxLayout()
        self.info_bar.setContentsMargins(10, 5, 10, 5)
        self.info_label = QLabel()
        self.info_label.setStyleSheet(
            "color:#fff;font-size:14px;background:rgba(0,0,0,180);padding:6px;border-radius:4px;")
        self.info_bar.addWidget(self.info_label)
        self.info_bar.addStretch()
        layout.addLayout(self.info_bar)

        # 视频/图片画面
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("background:#000;")
        layout.addWidget(self.preview, 1)

        # ---- 底部控制条：[播放/暂停] [当前时间] [进度条] [总时间] [关闭] ----
        self.ctrl_bar = QWidget()
        self.ctrl_bar.setStyleSheet("background:rgba(0,0,0,200);")
        ctrl_layout = QHBoxLayout(self.ctrl_bar)
        ctrl_layout.setContentsMargins(8, 4, 8, 4)
        ctrl_layout.setSpacing(6)

        # 播放/暂停图标按钮
        self.btn_play = QPushButton("\u23f8")  # 暂停符号
        self.btn_play.setFixedSize(44, 44)
        self.btn_play.setStyleSheet("""
            QPushButton{background:transparent;color:#fff;font-size:22px;
                        border:none;border-radius:22px;}
            QPushButton:hover{background:rgba(255,255,255,30);}
        """)
        self.btn_play.clicked.connect(self._toggle_play)
        ctrl_layout.addWidget(self.btn_play)

        # 当前时间
        self.time_current = QLabel("00:00")
        self.time_current.setStyleSheet("color:#aaa;font-size:13px;")
        self.time_current.setFixedWidth(48)
        ctrl_layout.addWidget(self.time_current)

        # 进度条
        self.seek_bar = QSlider(Qt.Horizontal)
        self.seek_bar.setStyleSheet("""
            QSlider::groove:horizontal {height:6px;background:#555;border-radius:3px;}
            QSlider::handle:horizontal {width:14px;height:14px;margin:-5px 0;
                background:#36c;border:2px solid #58f;border-radius:8px;}
            QSlider::sub-page:horizontal {background:#36c;border-radius:3px;}
        """)
        self.seek_bar.setMinimum(0)
        self.seek_bar.sliderPressed.connect(self._on_seek_start)
        self.seek_bar.sliderReleased.connect(self._on_seek_end)
        self.seek_bar.sliderMoved.connect(self._on_seek_move)
        ctrl_layout.addWidget(self.seek_bar, 1)

        # 总时间
        self.time_total = QLabel("00:00")
        self.time_total.setStyleSheet("color:#aaa;font-size:13px;")
        self.time_total.setFixedWidth(48)
        ctrl_layout.addWidget(self.time_total)

        # 关闭图标按钮
        self.btn_close = QPushButton("\u2715")  # x 符号
        self.btn_close.setFixedSize(44, 44)
        self.btn_close.setStyleSheet("""
            QPushButton{background:transparent;color:#f55;font-size:20px;
                        border:none;border-radius:22px;}
            QPushButton:hover{background:rgba(255,60,60,40);}
        """)
        self.btn_close.clicked.connect(self._close)
        ctrl_layout.addWidget(self.btn_close)

        layout.addWidget(self.ctrl_bar)

    def setup_video(self, path, play_list=None):
        self._stop()
        self.mode = "video"
        self.playing = True
        self._play_list = play_list or [path]
        self._play_index = 0
        # 清空监控画面
        main_win = self.window()
        if hasattr(main_win, 'preview'):
            main_win.preview.clear()
        self._open_file(path)

    def _open_file(self, path):
        """打开并开始播放单个视频文件"""
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            self.cap = None
            # 文件无法打开，尝试播放下一个
            self._skip_to_next()
            return
        self._current_path = path
        self.file_changed.emit(path)  # 通知外部当前播放文件
        self._fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.frame_ms = int(1000 / self._fps)
        self._total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        dur = self._total_frames / self._fps if self._fps else 0

        name = os.path.basename(path)
        idx = f" [{self._play_index + 1}/{len(self._play_list)}]" if len(self._play_list) > 1 else ""
        self.info_label.setText(f"  {name}{idx}  |  {self._fps:.0f}fps  |  {dur:.1f}s")
        self.info_label.setVisible(True)
        self.ctrl_bar.setVisible(True)
        self.btn_play.setText("\u23f8")
        self.seek_bar.setMaximum(self._total_frames)
        self.seek_bar.setValue(0)
        self.time_current.setText("00:00")
        self.time_total.setText(self._fmt_time(dur))
        self.preview.clear()

        if self.parent():
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

        # 确保定时器正确启动
        if self.timer:
            self.timer.stop()
            self.timer.timeout.disconnect()
        self.timer = QTimer()
        self.timer.timeout.connect(self._next_frame)
        self.timer.start(self.frame_ms)

    def _skip_to_next(self):
        """跳转到下一个视频"""
        play_list = getattr(self, '_play_list', [])
        idx = getattr(self, '_play_index', 0)
        # 尝试下一个有效视频
        start_idx = idx
        while True:
            next_idx = idx + 1 if idx + 1 < len(play_list) else 0
            if next_idx == start_idx:
                # 已经遍历完所有视频
                break
            if next_idx < len(play_list) and os.path.exists(play_list[next_idx]):
                self._play_index = next_idx
                self._open_file(play_list[next_idx])
                return
            idx = next_idx
        # 没有有效视频，关闭播放器
        self._stop()
        self.setVisible(False)

    def setup_photo(self, path):
        self._stop()
        self.mode = "photo"

        self.info_label.setText(f"  {os.path.basename(path)}")
        self.info_label.setVisible(True)
        self.ctrl_bar.setVisible(False)
        self.preview.clear()

        # 清空监控画面
        main_win = self.window()
        if hasattr(main_win, 'preview'):
            main_win.preview.clear()

        pixmap = QPixmap(path)
        if not pixmap.isNull():
            pw = self.parent().width() if self.parent() else self.width()
            ph = self.parent().height() if self.parent() else self.height()
            self.preview.setPixmap(cover_scale(pixmap, pw, ph))

        if self.parent():
            self.setGeometry(self.parent().rect())
        self.show()
        self.raise_()

    def _stop(self):
        if self.timer:
            self.timer.stop()
            self.timer = None
        if self.cap:
            self.cap.release()
            self.cap = None
        self._last_rgb = None
        self._seeking = False

    def _next_frame(self):
        if not self.playing or not self.cap:
            return
        if self._seeking:
            return
        ret, frame = self.cap.read()
        if not ret:
            # 视频播放结束
            play_list = getattr(self, '_play_list', [])
            idx = getattr(self, '_play_index', 0)
            print(f"[DEBUG] video ended, idx: {idx}, play_list count: {len(play_list)}")
            print(f"[DEBUG] current video: {os.path.basename(play_list[idx]) if idx < len(play_list) else 'None'}")
            if idx + 1 < len(play_list):
                # 连续播放：下一个视频
                self._play_index = idx + 1
                print(f"[DEBUG] next video: {os.path.basename(play_list[self._play_index])}")
                self._open_file(play_list[self._play_index])
            else:
                # 播放结束（单次播放或连续播放到最后一段）
                print(f"[DEBUG] playback finished, stopping")
                self.playing = False
                self.btn_play.setText("\u25b6")
                self._stop()
                self.playback_finished.emit()
            return
        # 更新进度条
        cur = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        self.seek_bar.blockSignals(True)
        self.seek_bar.setValue(cur)
        self.seek_bar.blockSignals(False)
        cur_sec = cur / self._fps if self._fps else 0
        self.time_current.setText(self._fmt_time(cur_sec))
        # 显示帧
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        self._last_rgb = rgb.copy()
        qimg = QImage(self._last_rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        pw = self.preview.width()
        ph = self.preview.height()
        if pw > 10 and ph > 10:
            pix = cover_scale(pix, pw, ph)
        self.preview.setPixmap(pix)

    def _toggle_play(self):
        self.playing = not self.playing
        self.btn_play.setText("\u23f8" if self.playing else "\u25b6")

    def _skip(self, seconds):
        """快进/快退指定秒数"""
        if not self.cap:
            return
        cur = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        offset = int(seconds * self._fps)
        new_pos = max(0, min(cur + offset, self._total_frames - 1))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, new_pos)
        self.seek_bar.setValue(new_pos)
        cur_sec = new_pos / self._fps if self._fps else 0
        self.time_current.setText(self._fmt_time(cur_sec))

    def _on_seek_start(self):
        self._seeking = True

    def _on_seek_move(self, pos):
        sec = pos / self._fps if self._fps else 0
        self.time_current.setText(self._fmt_time(sec))

    def _on_seek_end(self):
        if self.cap:
            pos = self.seek_bar.value()
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        self._seeking = False

    def _close(self):
        self._stop()
        self._current_path = ""
        self.setVisible(False)
        # 恢复监控画面
        main_win = self.window()
        if hasattr(main_win, 'update_frame'):
            main_win.update_frame()

    def _fmt_time(self, seconds):
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m:02d}:{s:02d}"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.parent():
            self.setGeometry(self.parent().rect())


# ====================== 主窗口 ======================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.W = self.cfg["video_width"]
        self.H = self.cfg["video_height"]
        self.setWindowTitle("USB 双摄行车记录仪")
        self.resize(self.W, self.H)

        # 存储
        self.save_root = get_usb_dir()
        self.lock_root = os.path.join(self.save_root, "lock_video")
        self.photos_dir = os.path.join(self.save_root, "photos")
        os.makedirs(self.save_root, exist_ok=True)
        os.makedirs(self.lock_root, exist_ok=True)
        os.makedirs(self.photos_dir, exist_ok=True)

        # 录像状态
        self.is_rec = False
        self.writer = None
        self.rec_start_time = None
        self.current_video_path = ""
        self.need_lock = False
        self.car_mode = 0
        self.current_frame = None

        # 摄像头
        self.cam0 = None
        self.cam1 = None
        self.init_cameras()

        # UI
        self.init_ui()

        # 定时器
        self.frame_timer = QTimer()
        self.frame_timer.timeout.connect(self.update_frame)
        self.frame_timer.start(33)

        self.disk_timer = QTimer()
        self.disk_timer.timeout.connect(self.check_disk)
        self.disk_timer.start(30000)

        self.file_timer = QTimer()
        self.file_timer.timeout.connect(self.refresh_files)
        self.file_timer.start(5000)

        self.showFullScreen()

        # 开机自动开始录制（延迟2秒等摄像头就绪）
        QTimer.singleShot(2000, self.start_rec)

    def init_ui(self):
        central = QWidget()
        central.setStyleSheet("background:#000;")
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---- 左侧：监控画面 + 按钮 ----
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        # 画面区域（相对定位，方便覆盖层叠加）
        self.preview_frame = QFrame()
        self.preview_frame.setStyleSheet("background:#000;")
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)

        self.preview = QLabel("等待摄像头...")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet("color:#888;font-size:18px;background:#000;")
        preview_layout.addWidget(self.preview)

        # 播放器覆盖层（叠加在 preview_frame 上）
        self.player_overlay = PlayerOverlay(self.preview_frame)
        self.player_overlay.file_changed.connect(self._highlight_playing)

        # 设置按钮（左上角齿轮图标，叠加在 preview_frame 上）
        self.btn_settings = QPushButton("\u2699", self.preview_frame)
        self.btn_settings.setFixedSize(48, 48)
        self.btn_settings.setStyleSheet("""
            QPushButton{background:rgba(0,0,0,150);color:#ccc;font-size:28px;
                        border:none;border-radius:24px;}
            QPushButton:hover{background:rgba(80,80,80,200);color:#fff;}
        """)
        self.btn_settings.move(6, 6)
        self.btn_settings.clicked.connect(self.open_settings)

        left_layout.addWidget(self.preview_frame, 1)

        # 控制按钮行
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        self.btn_rec = QPushButton("开始录像")
        self.btn_rec.setFixedHeight(52)
        self.btn_rec.setStyleSheet("""
            QPushButton{background:#c33;color:#fff;font-size:17px;font-weight:bold;
                        border-radius:6px;padding:0 20px;}
            QPushButton:hover{background:#e44;}
        """)
        self.btn_rec.clicked.connect(self.toggle_rec)
        ctrl.addWidget(self.btn_rec)

        self.btn_mode = QPushButton("前进模式")
        self.btn_mode.setFixedHeight(52)
        self.btn_mode.setStyleSheet("""
            QPushButton{background:#36c;color:#fff;font-size:17px;font-weight:bold;
                        border-radius:6px;padding:0 20px;}
            QPushButton:hover{background:#48e;}
        """)
        self.btn_mode.clicked.connect(self.switch_mode)
        ctrl.addWidget(self.btn_mode)

        btn_snap = QPushButton("截图")
        btn_snap.setFixedHeight(52)
        btn_snap.setStyleSheet("""
            QPushButton{background:#c93;color:#fff;font-size:17px;font-weight:bold;
                        border-radius:6px;padding:0 20px;}
            QPushButton:hover{background:#ea4;}
        """)
        btn_snap.clicked.connect(self.take_snapshot)
        ctrl.addWidget(btn_snap)

        btn_lock = QPushButton("锁定视频")
        btn_lock.setFixedHeight(52)
        btn_lock.setStyleSheet("""
            QPushButton{background:#93c;color:#fff;font-size:17px;font-weight:bold;
                        border-radius:6px;padding:0 20px;}
            QPushButton:hover{background:#b5e;}
        """)
        btn_lock.clicked.connect(self.lock_video)
        ctrl.addWidget(btn_lock)

        btn_gallery = QPushButton("相册/录像")
        btn_gallery.setFixedHeight(52)
        btn_gallery.setStyleSheet("""
            QPushButton{background:#555;color:#fff;font-size:17px;font-weight:bold;
                        border-radius:6px;padding:0 20px;}
            QPushButton:hover{background:#777;}
        """)
        btn_gallery.clicked.connect(self.show_gallery)
        ctrl.addWidget(btn_gallery)

        left_layout.addLayout(ctrl)

        # 状态文字
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color:#0c0;font-size:12px;padding:2px 4px;")
        left_layout.addWidget(self.status_label)

        main_layout.addWidget(left_widget, 3)

        # ---- 右侧：文件列表 ----
        self.gallery_panel = QWidget()
        self.gallery_panel.setStyleSheet("background:#1a1a1a;")
        g_layout = QVBoxLayout(self.gallery_panel)
        g_layout.setContentsMargins(4, 4, 4, 4)
        g_layout.setSpacing(4)

        # 标签页切换按钮
        tab_bar = QHBoxLayout()
        tab_bar.setSpacing(4)
        tab_bar.setContentsMargins(0, 0, 0, 0)

        self.tab_video = QPushButton("录像文件")
        self.tab_video.setFixedHeight(36)
        self.tab_video.setStyleSheet("""
            QPushButton{background:#36c;color:#fff;font-size:14px;font-weight:bold;
                        border-radius:4px;padding:0 16px;}
        """)
        self.tab_video.clicked.connect(lambda: self._switch_tab("video"))
        tab_bar.addWidget(self.tab_video)

        self.tab_photo = QPushButton("截图文件")
        self.tab_photo.setFixedHeight(36)
        self.tab_photo.setStyleSheet("""
            QPushButton{background:#444;color:#aaa;font-size:14px;font-weight:bold;
                        border-radius:4px;padding:0 16px;}
        """)
        self.tab_photo.clicked.connect(lambda: self._switch_tab("photo"))
        tab_bar.addWidget(self.tab_photo)

        g_layout.addLayout(tab_bar)

        # 文件列表（共用一个区域，切换显示）
        self.video_list = QListWidget()
        self.video_list.setStyleSheet("""
            QListWidget{background:#222;color:#fff;font-size:14px;border:none;}
            QListWidget::item{padding:8px 6px;border-bottom:1px solid #333;}
            QListWidget::item:selected{background:#36c;}
            QListWidget::item:hover{background:#333;}
        """)
        self.video_list.itemDoubleClicked.connect(self.play_video)
        self.video_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.video_list.customContextMenuRequested.connect(self._video_ctx_menu)
        g_layout.addWidget(self.video_list, 1)

        self.photo_list = QListWidget()
        self.photo_list.setStyleSheet("""
            QListWidget{background:#222;color:#fff;font-size:14px;border:none;}
            QListWidget::item{padding:8px 6px;border-bottom:1px solid #333;}
            QListWidget::item:selected{background:#36c;}
            QListWidget::item:hover{background:#333;}
        """)
        self.photo_list.itemDoubleClicked.connect(self.view_photo)
        self.photo_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.photo_list.customContextMenuRequested.connect(self._photo_ctx_menu)
        self.photo_list.setVisible(False)
        g_layout.addWidget(self.photo_list, 1)

        btn_back = QPushButton("返回预览")
        btn_back.setFixedHeight(40)
        btn_back.setStyleSheet("""
            QPushButton{background:#555;color:#fff;font-size:14px;font-weight:bold;
                        border-radius:6px;}
        """)
        btn_back.clicked.connect(self.hide_gallery)
        g_layout.addWidget(btn_back)

        self.gallery_panel.setVisible(False)
        main_layout.addWidget(self.gallery_panel, 1)

        self.refresh_files()

    def init_cameras(self):
        usb = []
        try:
            result = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True)
            current_name = ""
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "camera" in line.lower():
                    current_name = line
                elif line.startswith("/dev/video") and current_name:
                    dev_num = int(line.replace("/dev/video", ""))
                    usb.append(dev_num)
                    current_name = ""
        except Exception:
            pass
        if len(usb) >= 2:
            dev_ids = usb[:2]
        else:
            dev_ids = [0, 2]
        self.cam0 = CameraThread(dev_ids[0], "cam1")
        self.cam1 = CameraThread(dev_ids[1], "cam2")
        self.cam0.start()
        self.cam1.start()

    def add_watermark(self, frame):
        if not self.cfg["enable_watermark"]:
            return frame
        dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        h, w = frame.shape[:2]
        # 文字尺寸
        font_scale = 0.6
        thickness = 2
        (tw, th), baseline = cv2.getTextSize(dt_str, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        # 居中放置，底部留边距
        tx = (w - tw) // 2
        ty = h - 50
        # 半透明黑色背景
        overlay = frame.copy()
        cv2.rectangle(overlay, (tx - 8, ty - th - 6), (tx + tw + 8, ty + baseline + 4), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
        cv2.putText(frame, dt_str, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 255, 255), thickness)
        return frame

    def composite_frame(self, f0, f1):
        if f0 is None:
            return None
        if f1 is None:
            f1 = np.zeros_like(f0)
        small_w, small_h = self.W // 3, self.H // 3
        if self.car_mode == 0:
            main = f0.copy()
            small = cv2.resize(f1, (small_w, small_h))
        else:
            main = f1.copy()
            small = cv2.resize(f0, (small_w, small_h))
        cv2.rectangle(small, (0, 0), (small_w - 1, small_h - 1), (255, 255, 255), 3)
        margin = small_h // 8
        main[margin:margin + small_h, self.W - small_w:self.W, :] = small
        mode_text = "FWD" if self.car_mode == 0 else "BACK"
        cv2.putText(main, mode_text, (self.W - 80, self.H - 50), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 0), 2)
        return self.add_watermark(main)

    def update_frame(self):
        # 播放器覆盖层显示时，暂停监控画面刷新
        if self.player_overlay.isVisible():
            return
        f0 = self.cam0.frame if self.cam0 else None
        f1 = self.cam1.frame if self.cam1 else None
        comp = self.composite_frame(f0, f1)
        if comp is None:
            return
        self.current_frame = comp.copy()
        if self.is_rec and self.writer and self.writer.isOpened():
            self.writer.write(comp)
            if time.time() - self.rec_start_time > self.cfg["split_minute"] * 60:
                self.split_video()
        rgb = cv2.cvtColor(comp, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        pw = self.preview.width()
        ph = self.preview.height()
        if pw > 10 and ph > 10:
            pix = cover_scale(pix, pw, ph)
        self.preview.setPixmap(pix)

    # ---- 录像 ----
    def toggle_rec(self):
        if self.is_rec:
            self.stop_rec()
        else:
            self.start_rec()

    def start_rec(self):
        dt = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_video_path = os.path.join(self.save_root, f"{dt}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(self.current_video_path, fourcc, 30, (self.W, self.H))
        self.rec_start_time = time.time()
        self.is_rec = True
        self.btn_rec.setText("停止录像")
        self.btn_rec.setStyleSheet("""
            QPushButton{background:#3a3;color:#fff;font-size:17px;font-weight:bold;
                        border-radius:6px;padding:0 20px;}
            QPushButton:hover{background:#4c4;}
        """)
        self.status_label.setStyleSheet("color:red;font-size:12px;font-weight:bold;padding:4px;")
        self.status_label.setText(f"录像中: {self.current_video_path}")

    def stop_rec(self):
        self.is_rec = False
        if self.writer:
            self.writer.release()
            self.writer = None
        if self.need_lock and self.current_video_path:
            fname = os.path.basename(self.current_video_path)
            dst = os.path.join(self.lock_root, f"LOCK_{fname}")
            try:
                subprocess.run(["cp", self.current_video_path, dst], check=True)
            except Exception:
                pass
            self.need_lock = False
        self.btn_rec.setText("开始录像")
        self.btn_rec.setStyleSheet("""
            QPushButton{background:#c33;color:#fff;font-size:17px;font-weight:bold;
                        border-radius:6px;padding:0 20px;}
            QPushButton:hover{background:#e44;}
        """)
        self.status_label.setStyleSheet("color:#0c0;font-size:12px;padding:4px;")
        self.status_label.setText("录像已停止")
        self.refresh_files()

    def split_video(self):
        if self.writer:
            self.writer.release()
            self.writer = None
        if self.need_lock and self.current_video_path:
            fname = os.path.basename(self.current_video_path)
            dst = os.path.join(self.lock_root, f"LOCK_{fname}")
            try:
                subprocess.run(["cp", self.current_video_path, dst], check=True)
            except Exception:
                pass
            self.need_lock = False
            self.status_label.setText(f"锁定已保存: {fname}")
        dt = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_video_path = os.path.join(self.save_root, f"{dt}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(self.current_video_path, fourcc, 30, (self.W, self.H))
        self.rec_start_time = time.time()
        if self.is_rec:
            self.status_label.setStyleSheet("color:red;font-size:12px;font-weight:bold;padding:4px;")
            self.status_label.setText(f"录像中: {self.current_video_path}")
        # 录完新视频后立即检查空间，不够则删旧视频
        try:
            free_gb = _get_free_space_gb(self.save_root)
            if free_gb < self.cfg["warn_space_gb"]:
                clean_old_videos(self.save_root, self.cfg["warn_space_gb"])
        except Exception:
            pass

    # ---- 模式 ----
    def switch_mode(self):
        self.car_mode = 1 - self.car_mode
        self.btn_mode.setText("前进模式" if self.car_mode == 0 else "后退模式")

    # ---- 截图 ----
    def take_snapshot(self):
        if self.current_frame is None:
            return
        dt = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.photos_dir, f"shot_{dt}.jpg")
        cv2.imwrite(path, self.current_frame)
        self.status_label.setText(f"截图已保存: {path}")
        self.refresh_files()

    # ---- 锁定 ----
    def lock_video(self):
        if not self.is_rec or not self.current_video_path:
            self.status_label.setText("未在录像，无法锁定")
            return
        self.need_lock = True
        self.status_label.setText("已标记锁定，分段结束时自动保存")

    # ---- 相册 ----
    def show_gallery(self):
        self.gallery_panel.setVisible(True)
        self.refresh_files()

    def hide_gallery(self):
        self.player_overlay._close()
        self._clear_highlight()
        self.gallery_panel.setVisible(False)

    def _clear_highlight(self):
        """清除所有高亮"""
        for i in range(self.video_list.count()):
            item = self.video_list.item(i)
            item.setBackground(Qt.transparent)
            item.setForeground(Qt.white)

    def _highlight_playing(self, path):
        """高亮显示当前播放的视频"""
        for i in range(self.video_list.count()):
            item = self.video_list.item(i)
            if item.data(Qt.UserRole) == path:
                item.setBackground(Qt.darkGreen)
                item.setForeground(Qt.white)
                self.video_list.setCurrentItem(item)
            else:
                item.setBackground(Qt.transparent)
                item.setForeground(Qt.white)

    def _video_ctx_menu(self, pos):
        item = self.video_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu{background:#333;color:#fff;border:1px solid #555;}
            QMenu::item{padding:8px 24px;color:#fff;}
            QMenu::item:selected{background:#36c;}
        """)
        act_del = menu.addAction("删除")
        act_lock = menu.addAction("锁定")
        fpath = item.data(Qt.UserRole)
        row = self.video_list.row(item)
        act_del.triggered.connect(lambda: self._delete_file(fpath, row, self.video_list))
        act_lock.triggered.connect(lambda: self._lock_file(fpath))
        menu.popup(self.video_list.mapToGlobal(pos))

    def _lock_file(self, fpath):
        if not fpath:
            return
        fname = os.path.basename(fpath)
        dst = os.path.join(self.lock_root, f"LOCK_{fname}")
        try:
            subprocess.run(["cp", fpath, dst], check=True)
            self.status_label.setText(f"已锁定: {fname}")
        except Exception as e:
            self.status_label.setText(f"锁定失败: {e}")

    def _photo_ctx_menu(self, pos):
        item = self.photo_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu{background:#333;color:#fff;border:1px solid #555;}
            QMenu::item{padding:8px 24px;color:#fff;}
            QMenu::item:selected{background:#36c;}
        """)
        act_del = menu.addAction("删除")
        fpath = item.data(Qt.UserRole)
        row = self.photo_list.row(item)
        act_del.triggered.connect(lambda: self._delete_file(fpath, row, self.photo_list))
        menu.popup(self.photo_list.mapToGlobal(pos))

    def _delete_file(self, fpath, row, list_widget):
        if not fpath or not os.path.exists(fpath):
            return
        fname = os.path.basename(fpath)
        try:
            os.remove(fpath)
            list_widget.takeItem(row)
            self.status_label.setText(f"已删除: {fname}")
        except Exception as e:
            self.status_label.setText(f"删除失败: {e}")

    def _switch_tab(self, tab):
        if tab == "video":
            self.video_list.setVisible(True)
            self.photo_list.setVisible(False)
            self.tab_video.setStyleSheet(
                "QPushButton{background:#36c;color:#fff;font-size:14px;font-weight:bold;"
                "border-radius:4px;padding:0 16px;}")
            self.tab_photo.setStyleSheet(
                "QPushButton{background:#444;color:#aaa;font-size:14px;font-weight:bold;"
                "border-radius:4px;padding:0 16px;}")
        else:
            self.video_list.setVisible(False)
            self.photo_list.setVisible(True)
            self.tab_video.setStyleSheet(
                "QPushButton{background:#444;color:#aaa;font-size:14px;font-weight:bold;"
                "border-radius:4px;padding:0 16px;}")
            self.tab_photo.setStyleSheet(
                "QPushButton{background:#36c;color:#fff;font-size:14px;font-weight:bold;"
                "border-radius:4px;padding:0 16px;}")

    def refresh_files(self):
        # 保存当前高亮的路径
        current_highlight = self.player_overlay._current_path if hasattr(self, 'player_overlay') else ""
        self.video_list.clear()
        self.photo_list.clear()
        # 正常视频：上新下旧（最新在最上面，最旧在最下面）
        if os.path.exists(self.save_root):
            for f in sorted([f for f in os.listdir(self.save_root) if f.endswith(".mp4")], reverse=True):
                fpath = os.path.join(self.save_root, f)
                size_mb = os.path.getsize(fpath) / (1024 * 1024)
                item = QListWidgetItem(f"{f}  ({size_mb:.1f}MB)")
                item.setData(Qt.UserRole, fpath)
                self.video_list.addItem(item)
        # 锁定视频放在最下面（上新下旧）
        if os.path.exists(self.lock_root):
            for f in sorted([f for f in os.listdir(self.lock_root) if f.endswith(".mp4")], reverse=True):
                fpath = os.path.join(self.lock_root, f)
                size_mb = os.path.getsize(fpath) / (1024 * 1024)
                item = QListWidgetItem(f"[锁定] {f}  ({size_mb:.1f}MB)")
                item.setData(Qt.UserRole, fpath)
                self.video_list.addItem(item)
        # 恢复高亮
        if current_highlight:
            self._highlight_playing(current_highlight)
        if os.path.exists(self.photos_dir):
            for f in sorted([f for f in os.listdir(self.photos_dir) if f.endswith((".jpg", ".png"))], reverse=True):
                fpath = os.path.join(self.photos_dir, f)
                item = QListWidgetItem(f)
                item.setData(Qt.UserRole, fpath)
                self.photo_list.addItem(item)

    def open_settings(self):
        try:
            dlg = SettingsDialog(self.cfg.copy(), self)
            dlg.setModal(True)
            dlg.exec_()
            # 无论设置是否保存，都刷新文件列表（因为可能在设置中清空了存储）
            self.refresh_files()
            if dlg.result_cfg:
                self.cfg.update(dlg.result_cfg)
                save_config(self.cfg)
                self.status_label.setText("设置已保存")
        except Exception as e:
            self.status_label.setText(f"设置出错: {e}")

    def play_video(self, item):
        fpath = item.data(Qt.UserRole)
        if not fpath or not os.path.exists(fpath):
            return
        # 排除正在录制的视频
        if fpath == self.current_video_path:
            self.status_label.setText("该视频正在录制中，无法播放")
            return
        # 连续播放：从点选的视频开始，向上播放已完成的视频
        play_list = [fpath]
        if self.cfg.get("play_mode") == "continuous":
            # video_list 是上新下旧，找到点选位置，往上播放（更新的已完成视频）
            for i in range(self.video_list.count()):
                path = self.video_list.item(i).data(Qt.UserRole)
                if path == fpath:
                    # 从点选位置往上（索引减小），即更新的视频
                    play_list = []
                    for j in range(i, -1, -1):
                        p = self.video_list.item(j).data(Qt.UserRole)
                        # 跳过正在录制的视频
                        if p and os.path.exists(p) and p != self.current_video_path:
                            play_list.append(p)
                    break
        if play_list:
            self.player_overlay.setup_video(play_list[0], play_list)

    def view_photo(self, item):
        fpath = item.data(Qt.UserRole)
        if not fpath or not os.path.exists(fpath):
            return
        self.player_overlay.setup_photo(fpath)

    # ---- 磁盘 ----
    def check_disk(self):
        try:
            free_gb = _get_free_space_gb(self.save_root)
            if free_gb < self.cfg["warn_space_gb"]:
                self.status_label.setStyleSheet("color:#fa0;font-size:12px;font-weight:bold;padding:4px;")
                self.status_label.setText(f"空间不足 {self.cfg['warn_space_gb']}GB！剩余 {free_gb:.1f}GB")
                clean_old_videos(self.save_root, self.cfg["warn_space_gb"])
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 覆盖层跟随 preview_frame 大小
        if hasattr(self, 'player_overlay') and self.preview_frame:
            if self.player_overlay.isVisible():
                self.player_overlay.setGeometry(self.preview_frame.rect())

    def closeEvent(self, event):
        self.cam0.stop() if self.cam0 else None
        self.cam1.stop() if self.cam1 else None
        if self.writer:
            self.writer.release()
        event.accept()


# ====================== 启动 ======================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        QMainWindow{background:#000;}
        QWidget{background:#1a1a1a;}
        QPushButton{font-size:14px;}
        QMessageBox{background:#222;}
        QMessageBox QLabel{color:#fff;font-size:14px;}
        QMessageBox QPushButton{background:#36c;color:white;border:none;border-radius:4px;padding:8px 20px;font-weight:bold;}
        QMessageBox QPushButton:hover{background:#48e;}
        QMessageBox QPushButton:pressed{background:#25a;}
    """)
    win = MainWindow()
    sys.exit(app.exec_())
