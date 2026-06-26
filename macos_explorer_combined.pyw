import sys
import os
import ctypes
import psutil
import traceback

# optional Windows-specific modules
try:
    import win32con
except Exception:
    win32con = None

try:
    import winshell
except Exception:
    winshell = None

try:
    import send2trash
except Exception:
    send2trash = None

try:
    import win32com.client
except Exception:
    win32com = None

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QTreeWidget, QTreeWidgetItem, QSplitter, QLineEdit,
                               QPushButton, QHeaderView, QAbstractItemView, QMenu,
                               QFileIconProvider, QMessageBox, QInputDialog,
                               QListView, QStyledItemDelegate, QStyle, QFrame, QToolButton,
                               QLabel, QListWidget, QListWidgetItem)
from PySide6.QtCore import Qt, QDir, QFileInfo, QTimer, QSize, QStandardPaths, QRect, QEvent, QPoint, Signal, QUrl
from PySide6.QtGui import (QIcon, QAction, QKeyEvent, QColor, QPainter, QPen, QBrush,
                           QPixmap, QStandardItemModel, QStandardItem, QKeySequence, QFontMetrics,
                           QImage, QDrag)
import winreg
import glob
import atexit
import subprocess
import shutil

try:
    import pywinstyles
except Exception:
    pywinstyles = None

kernel32 = ctypes.windll.kernel32
winmm = getattr(ctypes.windll, 'winmm', None)

# ======================== 路径工具 ========================

def _app_dir():
    """获取 exe / .py 所在目录，开发与 PyInstaller 打包均适用"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# ======================== Explorer 进程管理 ========================

def _reg_auto_restart_shell():
    """读取 HKLM 或 HKCU 的 AutoRestartShell 值"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows NT\CurrentVersion\Winlogon')
        val, _ = winreg.QueryValueEx(key, 'AutoRestartShell')
        winreg.CloseKey(key)
        return val
    except FileNotFoundError:
        return None

def _set_auto_restart_shell(value):
    """设置 AutoRestartShell, 不存在则创建"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows NT\CurrentVersion\Winlogon',
                            0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, 'AutoRestartShell', 0, winreg.REG_DWORD, value)
        winreg.CloseKey(key)
    except Exception:
        pass

def kill_explorer():
    """结束 explorer.exe 并阻止自动重启, 返回是否需要恢复"""
    killed = False
    try:
        subprocess.run(['taskkill', '/f', '/im', 'explorer.exe'],
                      capture_output=True, timeout=5)
        killed = True
    except Exception:
        pass
    if killed:
        _set_auto_restart_shell(0)
    return killed

def start_explorer():
    """恢复 AutoRestartShell 并启动 explorer.exe"""
    _set_auto_restart_shell(1)
    try:
        subprocess.Popen('explorer.exe')
    except Exception:
        pass

# 安全网：程序退出时确保 explorer 恢复
_explorer_killed = False
_desktop_text_light = False  # False=深色字(默认), True=浅色字(白色)

def _cleanup_explorer():
    global _explorer_killed
    if _explorer_killed:
        start_explorer()
        _explorer_killed = False

atexit.register(_cleanup_explorer)
def _folder_size(path, stop_event=None):
    """递归计算文件夹总字节数。stop_event 可传入 threading.Event 用于中途取消"""
    total = 0
    count = 0
    MAX_FILES = 65536
    try:
        for root, dirs, files in os.walk(path):
            if stop_event and stop_event.is_set():
                return total, count
            depth = root.replace(path, '').count(os.sep)
            if depth > 16:
                del dirs[:]
            for f in files:
                if stop_event and stop_event.is_set():
                    return total, count
                try:
                    total += os.path.getsize(os.path.join(root, f))
                    count += 1
                    if count >= MAX_FILES:
                        return total, count
                except OSError:
                    pass
    except OSError:
        pass
    return total, count

# ======================== 桌面模式辅助函数与类 ========================

def get_wallpaper_path():
    """获取当前 Windows 桌面壁纸路径"""
    transcoded = os.path.join(os.environ.get('APPDATA', ''),
                              'Microsoft', 'Windows', 'Themes', 'TranscodedWallpaper')
    if os.path.exists(transcoded):
        return transcoded
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\Desktop')
        wallpaper, _ = winreg.QueryValueEx(key, 'WallPaper')
        winreg.CloseKey(key)
        if wallpaper and os.path.exists(wallpaper):
            return wallpaper
    except Exception:
        pass
    return None


def _get_recycle_bin_icon(size=48):
    """从 imageres.dll 提取真实回收站图标（HICON → QPixmap）"""
    try:
        from ctypes import wintypes, byref, sizeof, cast, c_void_p, c_byte
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        gdi32 = ctypes.windll.gdi32

        # 确保 64-bit 兼容：为所有 GDI/User/Shell API 设置 argtypes
        LPVOID = ctypes.c_void_p
        HDC = wintypes.HANDLE
        HBITMAP = wintypes.HBITMAP
        HGDIOBJ = wintypes.HGDIOBJ
        UINT = wintypes.UINT

        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.GetIconInfo.argtypes = [wintypes.HICON, LPVOID]
        user32.GetIconInfo.restype = wintypes.BOOL
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL

        shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                                           ctypes.c_void_p, ctypes.c_void_p, UINT]
        shell32.ExtractIconExW.restype = UINT

        gdi32.DeleteObject.argtypes = [HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, LPVOID]
        gdi32.GetObjectW.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [HDC, HBITMAP, UINT, UINT, LPVOID, LPVOID, UINT]
        gdi32.GetDIBits.restype = ctypes.c_int

        dll = r"C:\WINDOWS\system32\imageres.dll"
        # 55=空, 54=满; 优先空回收站图标
        hicon = wintypes.HICON()
        small_icon = wintypes.HICON()
        count = shell32.ExtractIconExW(dll, -55, byref(hicon), byref(small_icon), 1)
        if count <= 0 or not hicon:
            count = shell32.ExtractIconExW(dll, -54, byref(hicon), byref(small_icon), 1)
        if count <= 0 or not hicon:
            return None

        try:
            # GetIconInfo 获取位图
            class ICONINFO(ctypes.Structure):
                _fields_ = [("fIcon", ctypes.c_bool), ("xHotspot", wintypes.DWORD),
                            ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                            ("hbmColor", wintypes.HBITMAP)]
            ii = ICONINFO()
            if not user32.GetIconInfo(hicon, byref(ii)):
                return None

            # 读取 BITMAP 信息
            class BITMAP(ctypes.Structure):
                _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                            ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                            ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                            ("bmBits", ctypes.c_void_p)]
            bm = BITMAP()
            gdi32.GetObjectW(ii.hbmColor, sizeof(bm), byref(bm))
            w = bm.bmWidth
            h = bm.bmHeight

            if h <= 0 or w <= 0:
                return None

            # GetDIBits → BGRA 像素数据
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                            ("biClrImportant", wintypes.DWORD)]
            bih = BITMAPINFOHEADER()
            bih.biSize = sizeof(bih)
            bih.biWidth = w
            bih.biHeight = -h  # top-down DIB
            bih.biPlanes = 1
            bih.biBitCount = 32
            bih.biCompression = 0  # BI_RGB

            buf = (c_byte * (w * h * 4))()
            screen_dc = user32.GetDC(0)
            copy_count = gdi32.GetDIBits(screen_dc, ii.hbmColor, 0, h,
                                          buf, cast(byref(bih), c_void_p), 0)
            user32.ReleaseDC(0, screen_dc)
            if copy_count == 0:
                gdi32.DeleteObject(ii.hbmMask)
                gdi32.DeleteObject(ii.hbmColor)
                return None

            # 清理 GDI 资源（在 QImage 创建前清理，数据已在 buf 中）
            gdi32.DeleteObject(ii.hbmMask)
            gdi32.DeleteObject(ii.hbmColor)

            # BGRA 字节序在 little-endian 下对应 Qt 的 ARGB32
            img = QImage(bytes(buf), w, h, QImage.Format.Format_ARGB32)
            if img.isNull():
                return None
            pix = QPixmap.fromImage(img)
            if size != w:
                pix = pix.scaled(size, size,
                                 aspectMode=Qt.AspectRatioMode.KeepAspectRatio,
                                 mode=Qt.TransformationMode.SmoothTransformation)
            return QIcon(pix)

        finally:
            # 确保 HICON 被释放
            if hicon:
                user32.DestroyIcon(hicon)
            if small_icon:
                user32.DestroyIcon(small_icon)

    except Exception:
        return None


class DesktopFileDelegate(QStyledItemDelegate):
    """桌面图标自定义绘制 — 圆角选中框 + 文字阴影 + 未选中2行省略"""

    # 布局常量
    ICON_SIZE = 48
    GRID_W = 100
    GRID_H = 125          # 未选中时固定高度
    ICON_TOP = 6
    TEXT_TOP = 58         # 图标下方文字起始 Y
    TEXT_MARGIN = 4       # 文字左右边距

    def _elide_to_lines(self, fm, text, max_width, max_lines):
        """将文字限制在 max_lines 行内，最后一行超出则加省略号"""
        if not text:
            return ""
        lines = []
        remaining = text
        for i in range(max_lines):
            if not remaining:
                break
            # 找到当前行能容纳的字符数
            chars = 0
            for ch in remaining:
                test = remaining[:chars + 1]
                if fm.horizontalAdvance(test) > max_width:
                    break
                chars += 1
            if chars == 0:
                chars = 1  # 至少一个字符
            line = remaining[:chars]
            remaining = remaining[chars:].lstrip()
            if i == max_lines - 1 and remaining:
                # 最后一行：加省略号
                line = fm.elidedText(line + remaining[:10], Qt.TextElideMode.ElideRight, max_width)
                lines.append(line)
                break
            lines.append(line)
        return "\n".join(lines)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        name = index.data(Qt.ItemDataRole.DisplayRole) or ""
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        is_selected = option.state & QStyle.StateFlag.State_Selected
        rect = option.rect

        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        fm = QFontMetrics(font)
        line_h = fm.height()

        text_area_w = self.GRID_W - self.TEXT_MARGIN * 2

        if is_selected:
            # —— 选中态：完整文字，背景自适应扩展 ——
            full_lines = []
            remaining = name
            while remaining:
                chars = 0
                for ch in remaining:
                    test = remaining[:chars + 1]
                    if fm.horizontalAdvance(test) > text_area_w:
                        break
                    chars += 1
                if chars == 0:
                    chars = 1
                full_lines.append(remaining[:chars])
                remaining = remaining[chars:].lstrip()
            num_lines = max(len(full_lines), 1)
            text_h = num_lines * line_h + 4

            # 选中背景：扩展到完整文字高度
            sel_rect = QRect(
                rect.left() + 2,
                rect.top() + 2,
                self.GRID_W - 4,
                self.TEXT_TOP - self.ICON_TOP + text_h + 8
            )
            painter.setBrush(QBrush(QColor(0, 122, 255, 45)))
            painter.setPen(QPen(QColor(0, 122, 255, 90), 1.5))
            painter.drawRoundedRect(sel_rect, 8, 8)

            display_text = "\n".join(full_lines)
            text_rect = QRect(
                rect.left() + self.TEXT_MARGIN,
                rect.top() + self.TEXT_TOP,
                text_area_w,
                text_h
            )
        else:
            # —— 未选中态：最多2行 + 省略号 ——
            display_text = self._elide_to_lines(fm, name, text_area_w, 2)
            text_h = min(2, display_text.count("\n") + 1) * line_h + 4
            text_rect = QRect(
                rect.left() + self.TEXT_MARGIN,
                rect.top() + self.TEXT_TOP,
                text_area_w,
                text_h
            )

        # 图标
        if icon:
            pm = icon.pixmap(self.ICON_SIZE, self.ICON_SIZE)
            ix = rect.left() + (self.GRID_W - self.ICON_SIZE) // 2
            iy = rect.top() + self.ICON_TOP
            painter.drawPixmap(ix, iy, self.ICON_SIZE, self.ICON_SIZE, pm)

        # 文字：根据桌面模式切换深色/浅色
        if display_text:
            if _desktop_text_light:
                painter.setPen(QColor(255, 255, 255))
            else:
                painter.setPen(QColor(30, 30, 30))
            painter.drawText(text_rect,
                             Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                             display_text)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(self.GRID_W, self.GRID_H)


# ======================== 开始菜单弹窗 ========================

class StartMenuPopup(QFrame):
    """Windows 11 风格开始菜单 — aero 毛玻璃弹窗 (桌面模式下使用米白色回退)"""
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        if not _explorer_killed:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(580, 640)
        self.icon_provider = QFileIconProvider()
        self._apps = []
        self._filtered = []
        self.init_ui()
        if _explorer_killed:
            self._apply_fallback_style()
        self.load_apps()
        self.installEventFilter(self)

    def init_ui(self):
        self.setStyleSheet("""
            StartMenuPopup {
                background-color: rgba(32, 32, 32, 0.88);
                border: 1px solid rgba(255,255,255,0.08);
                border-radius: 12px;
            }
        """)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 搜索栏
        search_container = QWidget()
        search_container.setFixedHeight(56)
        search_container.setStyleSheet("background: transparent;")
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(24, 10, 24, 10)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("🔍键入关键词搜索")
        self.search_bar.setFixedHeight(36)
        self.search_bar.setStyleSheet("""
            QLineEdit {
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 18px;
                padding: 0 16px;
                background-color: rgba(255,255,255,0.08);
                color: white;
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid rgba(255,255,255,0.35); }
        """)
        self.search_bar.textChanged.connect(self._on_search)
        search_layout.addWidget(self.search_bar)
        main_layout.addWidget(search_container)

        # 已固定区域
        self.pinned_label = QLabel("已固定")
        self.pinned_label.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 13px; font-weight: 600; padding: 8px 28px 4px 28px; background: transparent;")
        main_layout.addWidget(self.pinned_label)

        self.pinned_grid = QListWidget()
        self.pinned_grid.setFixedHeight(130)
        self.pinned_grid.setIconSize(QSize(32, 32))
        self.pinned_grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.pinned_grid.setMovement(QListWidget.Movement.Static)
        self.pinned_grid.setResizeMode(QListWidget.ResizeMode.Fixed)
        self.pinned_grid.setGridSize(QSize(90, 120))
        self.pinned_grid.setSpacing(4)
        self.pinned_grid.setWordWrap(True)
        self.pinned_grid.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                padding: 4px 20px;
            }
            QListWidget::item {
                background: transparent;
                color: white;
                font-size: 11px;
                padding: 4px;
                border-radius: 6px;
            }
            QListWidget::item:hover {
                background-color: rgba(255,255,255,0.08);
            }
            QListWidget::item:selected {
                background-color: rgba(255,255,255,0.12);
            }
        """)
        self.pinned_grid.itemDoubleClicked.connect(self._launch_pinned)
        main_layout.addWidget(self.pinned_grid)

        # 所有应用区域
        self.all_label = QLabel("所有应用")
        self.all_label.setStyleSheet("color: rgba(255,255,255,0.7); font-size: 13px; font-weight: 600; padding: 8px 28px 4px 28px; background: transparent;")
        main_layout.addWidget(self.all_label)

        self.app_list = QListWidget()
        self.app_list.setIconSize(QSize(24, 24))
        self.app_list.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                padding: 2px 12px;
                color: white;
                font-size: 13px;
            }
            QListWidget::item {
                background: transparent;
                padding: 5px 12px;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: rgba(255,255,255,0.08);
            }
            QListWidget::item:selected {
                background-color: rgba(255,255,255,0.14);
            }
        """)
        self.app_list.itemDoubleClicked.connect(self._launch_app)
        main_layout.addWidget(self.app_list)

        # 底部用户区域
        self.footer = QWidget()
        self.footer.setFixedHeight(52)
        self.footer.setStyleSheet("background: transparent; border-top: 1px solid rgba(255,255,255,0.06);")
        footer_layout = QHBoxLayout(self.footer)
        footer_layout.setContentsMargins(24, 0, 24, 0)

        self.user_label = QLabel(os.environ.get('USERNAME', 'User'))
        self.user_label.setStyleSheet("color: white; font-size: 13px; font-weight: 500; background: transparent;")
        footer_layout.addWidget(self.user_label)
        footer_layout.addStretch()

        # 电源按钮
        self.power_btn = QToolButton()
        self.power_btn.setFixedSize(36, 36)
        self.power_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.power_btn.setToolTip("电源")
        _power_ico = os.path.join(_app_dir(), 'winout.ico')
        if os.path.exists(_power_ico):
            self.power_btn.setIcon(QIcon(_power_ico))
            self.power_btn.setIconSize(QSize(20, 20))
        self.power_btn.setStyleSheet("""
            QToolButton {
                border: none;
                border-radius: 18px;
                background: transparent;
                color: white;
                font-size: 16px;
            }
            QToolButton:hover {
                background-color: rgba(255, 255, 255, 0.12);
            }
            QToolButton:pressed {
                background-color: rgba(255, 255, 255, 0.06);
            }
        """)
        self.power_btn.clicked.connect(self._show_power_menu)
        footer_layout.addWidget(self.power_btn)

        main_layout.addWidget(self.footer)

    def load_apps(self):
        """扫描开始菜单目录获取应用列表"""
        self._apps = []
        seen = set()
        scan_dirs = [
            os.path.join(os.environ.get('APPDATA', ''), r'Microsoft\Windows\Start Menu\Programs'),
            os.path.join(os.environ.get('PROGRAMDATA', ''), r'Microsoft\Windows\Start Menu\Programs'),
        ]
        for base in scan_dirs:
            if not os.path.exists(base):
                continue
            for root, dirs, files in os.walk(base):
                for f in files:
                    if f.lower().endswith('.lnk'):
                        full = os.path.join(root, f)
                        name = os.path.splitext(f)[0]
                        if name.lower() in seen:
                            continue
                        seen.add(name.lower())
                        target = self._resolve_lnk(full)
                        self._apps.append((name, full, target))

        self._apps.sort(key=lambda x: x[0].lower())
        self._filtered = list(self._apps)
        self._populate_lists()

    def _resolve_lnk(self, lnk_path):
        try:
            if win32com is not None:
                shell = win32com.client.Dispatch("WScript.Shell")
                return shell.CreateShortCut(lnk_path).Targetpath
        except Exception:
            pass
        return ""

    def _populate_lists(self):
        """填充已固定和应用列表"""
        self.pinned_grid.clear()
        self.app_list.clear()

        # 已固定：优先显示常见的几类应用
        pinned_keywords = ['edge', 'chrome', 'firefox', '微信', 'qq', '钉钉', 'vscode',
                          'terminal', 'powershell', 'cmd', 'settings', '设置', 'store',
                          'mail', 'calendar', 'clock', 'camera', 'notepad', '记事本',
                          'calculator', '计算器', 'explorer', 'word', 'excel', 'powerpoint']

        pinned_added = set()
        for name, full, target in self._filtered:
            low = name.lower()
            if any(kw in low for kw in pinned_keywords) and low not in pinned_added:
                pinned_added.add(low)
                icon = self.icon_provider.icon(QFileInfo(full if full else (target or "")))
                item = QListWidgetItem(icon, name)
                item.setData(Qt.ItemDataRole.UserRole, target or full)
                item.setSizeHint(QSize(80, 90))
                self.pinned_grid.addItem(item)

        # 所有应用
        for name, full, target in self._filtered:
            icon = self.icon_provider.icon(QFileInfo(full if full else (target or "")))
            item = QListWidgetItem(icon, name)
            item.setData(Qt.ItemDataRole.UserRole, target or full)
            self.app_list.addItem(item)

    def _on_search(self, text):
        if not text.strip():
            self._filtered = list(self._apps)
        else:
            t = text.lower()
            self._filtered = [(n, f, tg) for n, f, tg in self._apps if t in n.lower()]
        self._populate_lists()

    def _launch_pinned(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            try:
                os.startfile(path)
            except Exception:
                pass

    def _launch_app(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            try:
                os.startfile(path)
            except Exception:
                pass

    def _show_power_menu(self):
        """电源菜单：关机 / 重启 / 注销 / 退出桌面模式"""
        menu = QMenu(self)
        if _explorer_killed:
            menu.setStyleSheet("""
                QMenu { background: rgba(255,255,255,0.95); border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; padding: 4px; color: #1a1a1a; }
                QMenu::item { padding: 8px 24px; border-radius: 4px; }
                QMenu::item:selected { background-color: #007AFF; color: white; }
            """)
        else:
            menu.setStyleSheet("""
                QMenu { background: rgba(40, 40, 40, 0.94); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 4px; color: white; }
                QMenu::item { padding: 8px 24px; border-radius: 4px; }
                QMenu::item:selected { background-color: #007AFF; color: white; }
            """)

        shutdown_act = QAction("关机", self)
        shutdown_act.triggered.connect(lambda: subprocess.Popen(['shutdown', '/s', '/t', '0'], shell=True))
        menu.addAction(shutdown_act)

        restart_act = QAction("重启", self)
        restart_act.triggered.connect(lambda: subprocess.Popen(['shutdown', '/r', '/t', '0'], shell=True))
        menu.addAction(restart_act)

        logoff_act = QAction("注销", self)
        logoff_act.triggered.connect(lambda: subprocess.Popen(['shutdown', '/l'], shell=True))
        menu.addAction(logoff_act)

        menu.addSeparator()

        exit_desktop_act = QAction("退出桌面模式", self)
        exit_desktop_act.triggered.connect(self._exit_desktop_mode)
        menu.addAction(exit_desktop_act)

        btn_global = self.power_btn.mapToGlobal(QPoint(0, self.power_btn.height()))
        menu.exec(btn_global)

    def _exit_desktop_mode(self):
        """通过信号通知 MacExplorer 退出桌面模式"""
        taskbar = self.parent()
        if taskbar:
            desktop_win = taskbar.window()
            if desktop_win and hasattr(desktop_win, 'exit_requested'):
                desktop_win.exit_requested.emit()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.WindowDeactivate:
            self.close()
        return super().eventFilter(obj, event)

    def _apply_fallback_style(self):
        """桌面模式：米白色实色背景替代暗色毛玻璃"""
        BG = "#F5F0E8"
        tc = "#1a1a1a"
        sc = "#555555"
        self.setStyleSheet(f"""
            StartMenuPopup {{
                background-color: {BG};
                border: 1px solid rgba(0,0,0,0.12);
                border-radius: 12px;
            }}
        """)
        self.search_bar.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid rgba(0,0,0,0.15);
                border-radius: 18px;
                padding: 0 16px;
                background-color: rgba(0,0,0,0.04);
                color: {tc};
                font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid #007AFF; }}
        """)
        self.pinned_label.setStyleSheet(f"color: {sc}; font-size: 13px; font-weight: 600; padding: 8px 28px 4px 28px; background: transparent;")
        self.all_label.setStyleSheet(f"color: {sc}; font-size: 13px; font-weight: 600; padding: 8px 28px 4px 28px; background: transparent;")
        self.pinned_grid.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                padding: 4px 20px;
            }}
            QListWidget::item {{
                background: transparent;
                color: {tc};
                font-size: 11px;
                padding: 4px;
                border-radius: 6px;
            }}
            QListWidget::item:hover {{
                background-color: rgba(0,0,0,0.06);
            }}
            QListWidget::item:selected {{
                background-color: rgba(0,0,0,0.10);
            }}
        """)
        self.app_list.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                padding: 2px 12px;
                color: {tc};
                font-size: 13px;
            }}
            QListWidget::item {{
                background: transparent;
                padding: 5px 12px;
                border-radius: 4px;
            }}
            QListWidget::item:hover {{
                background-color: rgba(0,0,0,0.06);
            }}
            QListWidget::item:selected {{
                background-color: rgba(0,0,0,0.10);
            }}
        """)
        self.footer.setStyleSheet("background: transparent; border-top: 1px solid rgba(0,0,0,0.10);")
        self.user_label.setStyleSheet(f"color: {tc}; font-size: 13px; font-weight: 500; background: transparent;")
        self.power_btn.setStyleSheet(f"""
            QToolButton {{
                border: none;
                border-radius: 18px;
                background: transparent;
            }}
            QToolButton:hover {{
                background-color: rgba(0,0,0,0.08);
            }}
            QToolButton:pressed {{
                background-color: rgba(0,0,0,0.04);
            }}
        """)

    def show_at(self, global_pos):
        self.move(global_pos)
        self.show()
        if not _explorer_killed:
            try:
                if pywinstyles is not None and hasattr(self, 'winId'):
                    hwnd = int(self.winId())
                    pywinstyles.apply_style(self, "aero")
            except Exception:
                pass
        self.search_bar.setFocus()


class TaskBarWidget(QFrame):
    """底部任务栏 — 加载 Windows 固定到任务栏的快捷方式"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.shortcut_buttons = []
        self.icon_provider = QFileIconProvider()
        self.init_ui()
        self.load_pinned_shortcuts()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def init_ui(self):
        self.setStyleSheet("""
            TaskBarWidget {
                background-color: rgba(255, 255, 255, 0.12);
                border-top: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 0px;
            }
        """)
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(4, 4, 8, 4)
        self.main_layout.setSpacing(4)

        # Windows 开始按钮
        self.start_btn = QToolButton(self)
        self.start_btn.setFixedSize(40, 40)
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.setToolTip("开始")
        # 尝试加载 win.ico，失败则回退到 ⊞ 字符
        ico_path = os.path.join(_app_dir(), 'win.ico')
        if os.path.exists(ico_path):
            self.start_btn.setIcon(QIcon(ico_path))
            self.start_btn.setIconSize(QSize(22, 22))
        else:
            self.start_btn.setText("⊞")
        self.start_btn.setStyleSheet("""
            QToolButton {
                border: none;
                border-radius: 6px;
                background: transparent;
                color: white;
                font-size: 18px;
                font-weight: bold;
            }
            QToolButton:hover {
                background-color: rgba(255, 255, 255, 0.18);
            }
            QToolButton:pressed {
                background-color: rgba(255, 255, 255, 0.08);
            }
        """)
        self.start_btn.clicked.connect(self._toggle_start_menu)
        self.main_layout.addWidget(self.start_btn)

        # 钉选快捷方式居中
        self.main_layout.addStretch()
        self.buttons_layout = QHBoxLayout()
        self.buttons_layout.setSpacing(2)
        self.main_layout.addLayout(self.buttons_layout)
        self.main_layout.addStretch()

        self._start_menu = None

    def load_pinned_shortcuts(self):
        taskbar_path = os.path.join(
            os.environ.get('APPDATA', ''),
            'Microsoft', 'Internet Explorer', 'Quick Launch', 'User Pinned', 'TaskBar'
        )
        if not os.path.exists(taskbar_path):
            return

        lnk_files = glob.glob(os.path.join(taskbar_path, '*.lnk'))
        for lnk_path in lnk_files:
            try:
                target = None
                if win32com is not None:
                    try:
                        shell = win32com.client.Dispatch("WScript.Shell")
                        shortcut = shell.CreateShortCut(lnk_path)
                        target = shortcut.Targetpath
                    except Exception:
                        pass
                if not target or not os.path.exists(target):
                    continue

                btn = QToolButton(self)
                btn.setFixedSize(40, 40)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setToolTip(os.path.splitext(os.path.basename(lnk_path))[0])
                btn.setIcon(self.icon_provider.icon(QFileInfo(target)))
                btn.setIconSize(QSize(24, 24))
                btn.setStyleSheet("""
                    QToolButton {
                        border: none;
                        border-radius: 6px;
                        background: transparent;
                    }
                    QToolButton:hover {
                        background-color: rgba(255, 255, 255, 0.18);
                    }
                    QToolButton:pressed {
                        background-color: rgba(255, 255, 255, 0.08);
                    }
                """)
                btn.clicked.connect(lambda checked, t=target: self._launch(t))
                self.buttons_layout.addWidget(btn)
                self.shortcut_buttons.append(btn)
            except Exception:
                pass

    def _launch(self, target):
        try:
            os.startfile(target)
        except Exception:
            pass

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: rgba(40, 40, 40, 0.94); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 4px; color: white; }
            QMenu::item { padding: 8px 24px; border-radius: 4px; }
            QMenu::item:selected { background-color: #007AFF; color: white; }
        """)
        forward_act = QAction("前置所有窗口", self)
        forward_act.triggered.connect(self._bring_windows_forward)
        menu.addAction(forward_act)
        menu.exec(self.mapToGlobal(pos))

    def _bring_windows_forward(self):
        """将桌面窗口降至底层，让其他应用窗口露出"""
        top = self.window()
        if top:
            top.lower()

    def _toggle_start_menu(self):
        """切换开始菜单显示"""
        if self._start_menu and self._start_menu.isVisible():
            self._start_menu.close()
            self._start_menu = None
            return
        try:
            menu = StartMenuPopup(self)
        except Exception:
            traceback.print_exc()
            return
        self._start_menu = menu
        # 定位在开始按钮上方
        btn_global = self.start_btn.mapToGlobal(QPoint(0, 0))
        x = btn_global.x()
        y = btn_global.y() - menu.height()
        menu.show_at(QPoint(x, y))
        menu.destroyed.connect(lambda: setattr(self, '_start_menu', None))


class DesktopModeWindow(QMainWindow):
    """全屏无边框置顶桌面模式窗口"""
    exit_requested = Signal()  # 电源菜单请求退出桌面模式
    open_file_browser = Signal()  # 双击"此电脑"请求打开 Finder
    open_path_in_finder = Signal(str)  # 双击回收站等：携带路径打开 Finder

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.wallpaper_pixmap = None
        self.icon_provider = QFileIconProvider()
        self.auto_sort = True

        self.init_ui()
        self.load_wallpaper()
        self.load_desktop_files()

        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())

    def init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 桌面文件视图
        self.file_model = QStandardItemModel()
        self.file_view = QListView()
        self.file_view.setModel(self.file_model)
        self.file_view.setViewMode(QListView.ViewMode.ListMode)
        self.file_view.setMovement(QListView.Movement.Static)
        self.file_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.file_view.setIconSize(QSize(DesktopFileDelegate.ICON_SIZE, DesktopFileDelegate.ICON_SIZE))
        self.file_view.setGridSize(QSize(DesktopFileDelegate.GRID_W, DesktopFileDelegate.GRID_H))
        self.file_view.setSpacing(4)
        self.file_view.setFlow(QListView.Flow.TopToBottom)
        self.file_view.setWrapping(True)
        self.file_view.setWordWrap(True)
        self.file_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.file_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_view.setDragEnabled(False)
        self.file_view.setItemDelegate(DesktopFileDelegate())
        self.file_view.setStyleSheet("""
            QListView {
                background: transparent;
                border: none;
                padding: 20px;
            }
            QListView::item { background: transparent; }
            QListView::item:hover { background: transparent; }
        """)
        self.file_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.file_view.installEventFilter(self)  # 拦截按键
        self.file_view.doubleClicked.connect(self._on_double_click)
        self.file_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_view.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self.file_view)

        # 窗口级快捷键 (QAction + setShortcut 方式，不受子控件焦点影响)
        act_f5 = QAction(self)
        act_f5.setShortcut(QKeySequence(Qt.Key.Key_F5))
        act_f5.triggered.connect(self.load_desktop_files)
        self.addAction(act_f5)

        act_del = QAction(self)
        act_del.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        act_del.triggered.connect(self._on_delete_shortcut)
        self.addAction(act_del)

        act_enter = QAction(self)
        act_enter.setShortcut(QKeySequence(Qt.Key.Key_Return))
        act_enter.triggered.connect(self._on_enter_shortcut)
        self.addAction(act_enter)

        # Enter 也需要支持小键盘
        act_enter2 = QAction(self)
        act_enter2.setShortcut(QKeySequence(Qt.Key.Key_Enter))
        act_enter2.triggered.connect(self._on_enter_shortcut)
        self.addAction(act_enter2)

        # 底部任务栏
        self.taskbar = TaskBarWidget()
        layout.addWidget(self.taskbar)

    # ---- 壁纸 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        if self.wallpaper_pixmap and not self.wallpaper_pixmap.isNull():
            painter.drawPixmap(self.rect(), self.wallpaper_pixmap)
        else:
            painter.fillRect(self.rect(), QColor(30, 30, 30))

    def load_wallpaper(self):
        wp = get_wallpaper_path()
        if wp:
            self.wallpaper_pixmap = QPixmap(wp)

    # ---- 文件加载与排序 ----
    def load_desktop_files(self):
        self.file_model.clear()

        # —— 固定首位：此电脑 ——
        pc_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        pc_item = QStandardItem(pc_icon, "此电脑")
        pc_item.setData("::THIS_PC::", Qt.ItemDataRole.UserRole)
        pc_item.setData(True, Qt.ItemDataRole.UserRole + 1)  # 标记为目录
        pc_item.setEditable(False)
        self.file_model.appendRow(pc_item)

        # —— 回收站 ——
        trash_icon = _get_recycle_bin_icon()
        if trash_icon is None:
            trash_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        trash_item = QStandardItem(trash_icon, "回收站")
        trash_item.setData("::RECYCLE_BIN::", Qt.ItemDataRole.UserRole)
        trash_item.setData(True, Qt.ItemDataRole.UserRole + 1)
        trash_item.setEditable(False)
        self.file_model.appendRow(trash_item)

        desktop_path = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DesktopLocation)
        if not os.path.exists(desktop_path):
            return

        files = [os.path.join(desktop_path, f) for f in os.listdir(desktop_path)]

        if self.auto_sort:
            files.sort(key=lambda p: (
                not os.path.isdir(p),
                os.path.splitext(p)[1].lower() if not os.path.isdir(p) else '',
                os.path.splitext(os.path.basename(p))[0].lower()
            ))

        for full_path in files:
            info = QFileInfo(full_path)
            item = QStandardItem(self.icon_provider.icon(info), info.fileName())
            item.setData(full_path, Qt.ItemDataRole.UserRole)
            item.setData(info.isDir(), Qt.ItemDataRole.UserRole + 1)
            item.setEditable(False)
            self.file_model.appendRow(item)

    def resort_files(self):
        self.auto_sort = True
        self.load_desktop_files()

    # ---- 交互 ----
    def _on_double_click(self, index):
        path = index.data(Qt.ItemDataRole.UserRole)
        if path == "::THIS_PC::":
            self.open_file_browser.emit()
            return
        if path == "::RECYCLE_BIN::":
            self.open_path_in_finder.emit("C:\\$Recycle.Bin")
            return
        if path and os.path.exists(path):
            try:
                os.startfile(path)
            except Exception:
                pass

    def _delete_file(self, path):
        try:
            if winshell is not None:
                winshell.delete_file(path, no_confirm=True, allow_undo=True)
            elif send2trash is not None:
                send2trash.send2trash(path)
            else:
                os.remove(path)
            self.load_desktop_files()
        except Exception:
            pass

    def _run_as_admin(self, path):
        try:
            if win32con is not None:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", path, None, os.path.dirname(path), win32con.SW_NORMAL)
            else:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", path, None, os.path.dirname(path), 1)
        except Exception:
            pass

    def _rename_file(self, old_path):
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=os.path.basename(old_path))
        if ok and new_name:
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(old_path, new_path)
                self.load_desktop_files()
            except Exception as e:
                QMessageBox.critical(self, "重命名失败", str(e))

    def _show_properties(self, path):
        try:
            info = QFileInfo(path)
            size_str = "-"
            if info.isFile():
                s = info.size()
                if s < 1024:
                    size_str = f"{s} B"
                elif s < 1024 ** 2:
                    size_str = f"{s / 1024:.1f} KB"
                elif s < 1024 ** 3:
                    size_str = f"{s / (1024 ** 2):.1f} MB"
                else:
                    size_str = f"{s / (1024 ** 3):.2f} GB"
            msg = f"名称: {info.fileName()}\n路径: {info.absoluteFilePath()}\n大小: {size_str}\n修改时间: {info.lastModified().toString('yyyy/MM/dd HH:mm')}"
            QMessageBox.information(self, "属性", msg)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _show_context_menu(self, pos):
        index = self.file_view.indexAt(pos)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: rgba(255,255,255,0.95); border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: #007AFF; color: white; }
        """)

        if index.isValid():
            path = index.data(Qt.ItemDataRole.UserRole)
            is_dir = index.data(Qt.ItemDataRole.UserRole + 1)

            # 回收站专用菜单
            if path == "::RECYCLE_BIN::":
                empty_act = QAction("清空回收站", self)
                empty_act.triggered.connect(self._empty_recycle_bin)
                menu.addAction(empty_act)
                menu.addSeparator()
                sort_act = QAction("自动排序 (按类型 → 名称)", self)
                sort_act.triggered.connect(self.resort_files)
                menu.addAction(sort_act)
                menu.exec(self.file_view.viewport().mapToGlobal(pos))
                return

            open_act = QAction("打开", self)
            open_act.triggered.connect(lambda checked, p=path: os.startfile(p))
            menu.addAction(open_act)

            if not is_dir and path:
                ext = os.path.splitext(path)[1].lower()
                if ext in ['.exe', '.msi', '.bat', '.cmd', '.com', '.ps1']:
                    admin_act = QAction("以管理员身份运行", self)
                    admin_act.triggered.connect(lambda checked, p=path: self._run_as_admin(p))
                    menu.addAction(admin_act)

            menu.addSeparator()

            sort_act = QAction("自动排序 (按类型 → 名称)", self)
            sort_act.triggered.connect(self.resort_files)
            menu.addAction(sort_act)

            menu.addSeparator()

            rename_act = QAction("重命名", self)
            rename_act.triggered.connect(lambda checked, p=path: self._rename_file(p))
            menu.addAction(rename_act)

            del_act = QAction("移到废纸篓", self)
            del_act.triggered.connect(lambda checked, p=path: self._delete_file(p))
            menu.addAction(del_act)

            menu.addSeparator()

            prop_act = QAction("属性", self)
            prop_act.triggered.connect(lambda checked, p=path: self._show_properties(p))
            menu.addAction(prop_act)
        else:
            sort_act = QAction("自动排序 (按类型 → 名称)", self)
            sort_act.triggered.connect(self.resort_files)
            menu.addAction(sort_act)

        menu.exec(self.file_view.viewport().mapToGlobal(pos))

    def _empty_recycle_bin(self):
        """调用 PowerShell 清空回收站"""
        reply = QMessageBox.question(
            self, "确认清空", "确定要清空回收站吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                subprocess.Popen(
                    ['powershell', '-Command', 'Clear-RecycleBin', '-Force'],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            except Exception:
                QMessageBox.critical(self, "错误", "清空回收站失败")

    # ---- 键盘 ----
    def _on_delete_shortcut(self):
        """Delete：将当前选中项移到回收站"""
        idx = self.file_view.currentIndex()
        if idx.isValid():
            p = idx.data(Qt.ItemDataRole.UserRole)
            if p and p not in ("::THIS_PC::", "::RECYCLE_BIN::"):
                self._delete_file(p)

    def _on_enter_shortcut(self):
        """Enter：打开选中项 (文件→打开, 此电脑→新Finder窗口)"""
        idx = self.file_view.currentIndex()
        if idx.isValid():
            self._on_double_click(idx)

    def eventFilter(self, obj, event):
        """拦截 file_view 的 F5/Delete/Enter 按键 (双保险)"""
        if obj == self.file_view and event.type() == QEvent.Type.KeyPress:
            k = event.key()
            if k == Qt.Key.Key_Delete:
                self._on_delete_shortcut()
                return True
            elif k == Qt.Key.Key_F5:
                self.load_desktop_files()
                return True
            elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._on_enter_shortcut()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)


# ======================== 撤销管理器 ========================

class UndoManager:
    """文件移动/复制操作撤销栈"""
    def __init__(self, max_undo=50):
        self._stack = []
        self._max = max_undo

    def record(self, action, src, dst):
        """记录操作: action='move'|'copy'"""
        self._stack.append((action, src, dst))
        if len(self._stack) > self._max:
            self._stack.pop(0)

    def undo(self):
        if not self._stack:
            return False
        action, src, dst = self._stack.pop()
        try:
            if action == 'move':
                if os.path.exists(dst):
                    if os.path.isdir(dst):
                        shutil.move(dst, src)
                    else:
                        os.makedirs(os.path.dirname(src), exist_ok=True)
                        shutil.move(dst, src)
            elif action == 'copy':
                if os.path.exists(dst):
                    if os.path.isdir(dst):
                        shutil.rmtree(dst)
                    else:
                        os.remove(dst)
            return True
        except Exception:
            traceback.print_exc()
            return False

    def can_undo(self):
        return len(self._stack) > 0

    @property
    def count(self):
        return len(self._stack)


# ======================== 拖拽支持树形控件 ========================

class DropAwareTreeWidget(QTreeWidget):
    """支持外部 Explorer 拖入 / 拖出文件的 QTreeWidget"""
    external_dropped = Signal(list)
    external_dragged_out = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)

    # ---- 拖入 (从外部 Explorer) ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile()]
            if paths:
                self.external_dropped.emit(paths)
                event.acceptProposedAction()
                return
        super().dropEvent(event)

    # ---- 拖出 (到外部 Explorer) ----
    def startDrag(self, supportedActions):
        items = self.selectedItems()
        if not items:
            return
        # 构建干净拖拽预览：只显示图标+文件名
        mdata = self.mimeData(items)
        drag = QDrag(self)
        drag.setMimeData(mdata)
        # 合成预览图
        pm = self._make_drag_pixmap(items)
        if pm:
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        result = drag.exec(supportedActions)
        if result == Qt.DropAction.MoveAction:
            QTimer.singleShot(200, self.external_dragged_out.emit)

    def _make_drag_pixmap(self, items):
        """生成仅含图标+文件名的拖拽预览图"""
        max_w, total_h = 180, 0
        rows = []
        for it in items[:5]:  # 最多显示5个
            icon = it.icon(0)
            name = it.text(0)
            pm_icon = icon.pixmap(20, 20) if not icon.isNull() else QPixmap(20, 20)
            font = self.font()
            fm = QFontMetrics(font)
            text_w = min(fm.horizontalAdvance(name), 140) + 28
            max_w = max(max_w, text_w)
            rows.append((pm_icon, name))
            total_h += 22
        if not rows:
            return None
        total_h = max(total_h, 22)
        pm = QPixmap(max_w + 8, total_h + 4)
        pm.fill(QColor(255, 255, 255, 0))
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(pm.rect(), QColor(255, 255, 255, 200))
        painter.setPen(QColor(0, 0, 0))
        y = 2
        for pm_icon, name in rows:
            painter.drawPixmap(4, y + 1, pm_icon)
            painter.drawText(28, y + 15, name)
            y += 22
        painter.end()
        return pm

    def mimeTypes(self):
        return ['text/uri-list'] + super().mimeTypes()

    def mimeData(self, items):
        mdata = super().mimeData(items)
        urls = []
        for it in items:
            p = it.data(0, Qt.ItemDataRole.UserRole)
            if p and os.path.exists(p):
                urls.append(QUrl.fromLocalFile(p))
        if urls:
            mdata.setUrls(urls)
        return mdata


class MacExplorer(QMainWindow):
    """Merged: UI from 1.py, shortcut handling and fixes from 3.py/4.py"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Finder")
        self.setGeometry(100, 100, 1050, 700)
        self.icon_provider = QFileIconProvider()
        self.current_drives = set()
        self.history = []
        self.current_path = ""
        self.desktop_window = None
        self._extra_windows = []  # 双击"此电脑"新开的 Finder 窗口

        self.init_ui()
        self.load_drives()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_drives_update)
        self.timer.start(5000)

        self._color_timer = QTimer(self)
        self._color_timer.timeout.connect(self._refresh_colors)
        self._color_timer.start(2000)
        self._last_dark_state = None

    def init_ui(self):
        central_widget = QWidget()
        central_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        central_widget.setStyleSheet("background: transparent;")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        top_bar = QHBoxLayout()

        self.desktop_btn = QPushButton()
        self.desktop_btn.setFixedSize(32, 32)
        self.desktop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desktop_btn.setToolTip("桌面模式")
        _base = _app_dir()
        _ico_in = os.path.join(_base, 'win.ico')
        _ico_out = os.path.join(_base, 'winout.ico')
        if os.path.exists(_ico_in):
            self._desktop_icon = QIcon(_ico_in)
            self._desktop_icon_out = QIcon(_ico_out) if os.path.exists(_ico_out) else QIcon(_ico_in)
            self.desktop_btn.setIcon(self._desktop_icon)
            self.desktop_btn.setIconSize(QSize(18, 18))
        else:
            self._desktop_icon = None
            self._desktop_icon_out = None
            self.desktop_btn.setText("⊞")
        self.desktop_btn.setStyleSheet("""
            QPushButton {
                border-radius: 16px;
                background-color: rgba(0,0,0,0.05);
                font-size: 16px;
                font-weight: bold;
                color: #333;
            }
            QPushButton:hover { background-color: rgba(0,0,0,0.1); }
            QPushButton:pressed { background-color: rgba(0,0,0,0.15); }
        """)
        self.desktop_btn.clicked.connect(self.toggle_desktop_mode)

        self.back_btn = QPushButton("‹")
        self.back_btn.setFixedSize(32, 32)
        self.back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.back_btn.setStyleSheet("""
            QPushButton {
                border-radius: 16px; 
                background-color: rgba(0,0,0,0.05); 
                font-size: 20px; 
                font-weight: bold;
                color: #333;
            }
            QPushButton:hover { background-color: rgba(0,0,0,0.1); }
            QPushButton:pressed { background-color: rgba(0,0,0,0.15); }
        """)
        self.back_btn.clicked.connect(self.go_back)

        self.path_bar = QLineEdit()
        self.path_bar.setFixedHeight(32)
        self.path_bar.setStyleSheet("""
            QLineEdit {
                border: 1px solid rgba(0,0,0,0.1); 
                border-radius: 16px; 
                padding: 0 15px;
                background-color: rgba(255,255,255,0.6);
                font-size: 13px;
            }
            QLineEdit:focus { border: 1px solid #007AFF; }
        """)
        self.path_bar.returnPressed.connect(self.navigate_to_path)

        # 文件夹大小计算开关按钮
        self._folder_size_enabled = False
        self._folder_size_stop = False
        self.folder_size_btn = QPushButton("开启显示文件夹占用存储空间")
        self.folder_size_btn.setFixedHeight(28)
        self.folder_size_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_size_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid rgba(0,0,0,0.15);
                border-radius: 14px;
                padding: 0 14px;
                background-color: rgba(0,0,0,0.04);
                font-size: 12px;
                color: #555;
            }
            QPushButton:hover { background-color: rgba(0,0,0,0.08); }
            QPushButton:pressed { background-color: rgba(0,0,0,0.12); }
        """)
        self.folder_size_btn.clicked.connect(self._toggle_folder_size)

        # 桌面文字深色/浅色切换按钮
        self.desktop_text_btn = QPushButton("桌面文字:深色")
        self.desktop_text_btn.setFixedHeight(28)
        self.desktop_text_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desktop_text_btn.setStyleSheet("""
            QPushButton {
                border: 1px solid rgba(0,0,0,0.15);
                border-radius: 14px;
                padding: 0 14px;
                background-color: rgba(0,0,0,0.04);
                font-size: 12px;
                color: #555;
            }
            QPushButton:hover { background-color: rgba(0,0,0,0.08); }
            QPushButton:pressed { background-color: rgba(0,0,0,0.12); }
        """)
        self.desktop_text_btn.clicked.connect(self._toggle_desktop_text_mode)

        top_bar.addWidget(self.desktop_btn)
        top_bar.addWidget(self.back_btn)
        top_bar.addWidget(self.path_bar)
        top_bar.addWidget(self.folder_size_btn)
        top_bar.addWidget(self.desktop_text_btn)
        main_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setFixedWidth(220)
        self.sidebar.setIconSize(QSize(18, 18))
        self.sidebar.setStyleSheet("""
            QTreeWidget { border: none; background: transparent; font-size: 13px; outline: none; }
            QTreeWidget::item { height: 28px; border-radius: 6px; padding-left: 8px; }
            QTreeWidget::item:selected { background-color: rgba(0, 122, 255, 0.15); color: #007AFF; }
            QTreeWidget::item:hover:!selected { background-color: rgba(0,0,0,0.04); }
        """)
        self.sidebar.itemClicked.connect(self.on_sidebar_click)
        self.sidebar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sidebar.customContextMenuRequested.connect(self.show_context_menu)

        self.undo_mgr = UndoManager()
        self._folder_size_cache = {}  # {abs_path: formatted_size_str}
        self._desktop_style_active = False  # 桌面模式下使用米白色回退样式

        self.file_view = DropAwareTreeWidget()
        self.file_view.setHeaderLabels(["名称", "修改日期", "类型", "大小"])
        self.file_view.setIconSize(QSize(20, 20))
        self.file_view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_view.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.file_view.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.file_view.header().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.file_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_view.setAlternatingRowColors(False)
        self.file_view.setRootIsDecorated(False)
        self.file_view.setStyleSheet("""
            QTreeWidget { border: none; background: transparent; font-size: 13px; outline: none; }
            QHeaderView::section { 
                border: none; border-bottom: 1px solid rgba(0,0,0,0.1); 
                padding: 6px; background: transparent; font-weight: 600; color: #666;
            }
            QTreeWidget::item { height: 32px; border-radius: 4px; }
            QTreeWidget::item:selected { background-color: #007AFF; color: white; }
            QTreeWidget::item:hover:!selected { background-color: rgba(0,0,0,0.04); }
        """)
        self.file_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_view.customContextMenuRequested.connect(self.show_context_menu)
        self.file_view.itemDoubleClicked.connect(self.on_file_double_click)
        self.file_view.external_dropped.connect(self._on_external_drop)
        self.file_view.external_dragged_out.connect(self._on_external_drag_out)

        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.file_view)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

        # Enter 键：打开选中项 (文件区 / 侧边栏驱动器)
        act_enter = QAction(self)
        act_enter.setShortcut(QKeySequence(Qt.Key.Key_Return))
        act_enter.triggered.connect(self._on_enter_action)
        self.addAction(act_enter)

        act_enter2 = QAction(self)
        act_enter2.setShortcut(QKeySequence(Qt.Key.Key_Enter))
        act_enter2.triggered.connect(self._on_enter_action)
        self.addAction(act_enter2)

        self.file_view.installEventFilter(self)
        self.sidebar.installEventFilter(self)

    _enter_handled = False

    def _on_enter_action(self):
        if self._enter_handled:
            return
        self._enter_handled = True
        try:
            if self.sidebar.hasFocus():
                items = self.sidebar.selectedItems()
                if items:
                    path = items[0].data(0, Qt.ItemDataRole.UserRole)
                    if path:
                        self.load_directory(path)
                    return
            items = self.file_view.selectedItems()
            if items:
                self.on_file_double_click(items[0])
        finally:
            QTimer.singleShot(100, lambda: setattr(MacExplorer, '_enter_handled', False))

    def eventFilter(self, obj, event):
        """拦截 file_view / sidebar 的 Enter 按键"""
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if obj in (self.file_view, self.sidebar):
                self._on_enter_action()
                return True
        return super().eventFilter(obj, event)

    def check_drives_update(self):
        try:
            partitions = psutil.disk_partitions(all=False)
            current_paths = {p.mountpoint for p in partitions}
            if current_paths != self.current_drives:
                self.load_drives()
        except Exception:
            pass

    def load_drives(self):
        current_selection = self.current_path
        self.sidebar.clear()
        root_item = QTreeWidgetItem(self.sidebar, ["位置"])
        root_item.setExpanded(True)
        root_item.setForeground(0, Qt.GlobalColor.gray)

        try:
            partitions = psutil.disk_partitions(all=False)
            self.current_drives = {p.mountpoint for p in partitions}

            for partition in partitions:
                drive_path = partition.mountpoint
                info = QFileInfo(drive_path)
                item = QTreeWidgetItem(root_item, [f"{partition.device}"])
                item.setData(0, Qt.ItemDataRole.UserRole, drive_path)
                item.setIcon(0, self.icon_provider.icon(info))
        except Exception:
            pass

        if current_selection in self.current_drives:
            pass

    def on_sidebar_click(self, item, column):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.load_directory(path)

    def format_size(self, size_val):
        try:
            if size_val < 1024:
                return f"{size_val} B"
            elif size_val < 1024 ** 2:
                return f"{size_val / 1024:.1f} KB"
            elif size_val < 1024 ** 3:
                return f"{size_val / (1024 ** 2):.1f} MB"
            else:
                return f"{size_val / (1024 ** 3):.2f} GB"
        except Exception:
            return "-"

    def resolve_shortcut(self, path):
        # handle .lnk files using win32com
        try:
            if win32com is None:
                return None
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(path)
            target = shortcut.Targetpath
            if target and os.path.exists(target):
                return target
        except Exception:
            pass
        return None

    def load_directory(self, path):
        try:
            if self.current_path and self.current_path != path:
                self.history.append(self.current_path)

            self.current_path = path
            self.path_bar.setText(path)
            self.file_view.clear()

            dir_iter = QDir(path)
            dir_iter.setSorting(QDir.SortFlag.DirsFirst | QDir.SortFlag.Name | QDir.SortFlag.Type)
            file_infos = dir_iter.entryInfoList(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)

            for info in file_infos:
                name = info.fileName()
                mod_time = info.lastModified().toString("yyyy/MM/dd HH:mm")
                is_link = info.isSymLink() or info.suffix().lower() == "lnk"
                file_type = "文件夹" if info.isDir() else ("快捷方式" if is_link else (info.suffix().upper() + " 文件" if info.suffix() else "文件"))
                size = "-"

                if info.isFile():
                    size = self.format_size(info.size())
                elif info.isDir():
                    try:
                        abs_path = info.absoluteFilePath()
                        item_count = len(os.listdir(abs_path))
                        if self._folder_size_enabled and not self._folder_size_stop:
                            cached = self._folder_size_cache.get(abs_path)
                            if cached is not None:
                                size = cached
                            else:
                                total_bytes, _ = _folder_size(abs_path)
                                sizestr = self.format_size(total_bytes) if total_bytes > 0 else "0 B"
                                cntstr = f"{item_count} 项" if item_count > 0 else "空"
                                size = f"{cntstr}; {sizestr}"
                                # LRU: 超过 500 条目时清掉最旧的一半
                                if len(self._folder_size_cache) > 500:
                                    keys = list(self._folder_size_cache.keys())
                                    for k in keys[:250]:
                                        del self._folder_size_cache[k]
                                self._folder_size_cache[abs_path] = size
                        else:
                            size = f"{item_count} 项" if item_count > 0 else "空"
                    except Exception:
                        size = "-"

                item = QTreeWidgetItem(self.file_view, [name, mod_time, file_type, size])
                item.setData(0, Qt.ItemDataRole.UserRole, info.absoluteFilePath())
                item.setData(0, Qt.ItemDataRole.UserRole + 1, info.isDir())
                item.setData(0, Qt.ItemDataRole.UserRole + 2, is_link)
                item.setIcon(0, self.icon_provider.icon(info))

        except Exception:
            traceback.print_exc()

    def _on_external_drop(self, paths):
        """处理从外部拖入的文件/文件夹"""
        if not self.current_path or not os.path.isdir(self.current_path):
            return
        dest_dir = self.current_path
        src_drive = os.path.splitdrive(paths[0])[0]
        dst_drive = os.path.splitdrive(dest_dir)[0]
        cross_drive = (src_drive.lower() != dst_drive.lower())

        for src in paths:
            if not os.path.exists(src):
                continue
            name = os.path.basename(src)
            dst = os.path.join(dest_dir, name)

            # 目标已存在则跳过
            if os.path.exists(dst):
                QMessageBox.warning(self, "提示", f"目标已存在，跳过: {name}")
                continue

            try:
                if cross_drive:
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    self.undo_mgr.record('copy', src, dst)
                else:
                    shutil.move(src, dst)
                    self.undo_mgr.record('move', src, dst)
            except Exception as e:
                QMessageBox.critical(self, "错误", f"操作失败: {name}\n{str(e)}")

        self.load_directory(self.current_path)

    def _toggle_desktop_text_mode(self):
        """切换桌面图标文字深色/浅色模式"""
        global _desktop_text_light
        _desktop_text_light = not _desktop_text_light
        if _desktop_text_light:
            self.desktop_text_btn.setText("桌面文字:浅色")
        else:
            self.desktop_text_btn.setText("桌面文字:深色")
        # 刷新桌面视图（如果桌面模式开着）
        if self.desktop_window and self.desktop_window.isVisible():
            self.desktop_window.load_desktop_files()

    def _toggle_folder_size(self):
        """切换文件夹大小计算"""
        if self._folder_size_enabled:
            self._folder_size_stop = True
            self._folder_size_enabled = False
            self.folder_size_btn.setText("开启显示文件夹占用存储空间")
            if self.current_path:
                self.load_directory(self.current_path)
        else:
            reply = QMessageBox.question(
                self, "Finder", "你确定要这样做吗？\n可能导致一段时间的性能开销",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._folder_size_stop = False
                self._folder_size_enabled = True
                self.folder_size_btn.setText("关闭显示文件夹占用存储空间")
                self.load_directory(self.current_path)

    def _on_external_drag_out(self):
        if self.current_path and os.path.isdir(self.current_path):
            cur = self.current_path
            self.current_path = ""
            self.load_directory(cur)

    def navigate_to_path(self):
        path = self.path_bar.text().strip()
        if os.path.exists(path):
            self.load_directory(path)
        else:
            QMessageBox.warning(self, "提示", "路径不存在")

    def toggle_desktop_mode(self):
        global _explorer_killed
        if self.desktop_window and self.desktop_window.isVisible():
            self._do_exit_desktop_mode()
        else:
            _explorer_killed = kill_explorer()
            self._apply_fallback_style()
            try:
                dw = DesktopModeWindow()
                self.desktop_window = dw
                dw.destroyed.connect(self._on_desktop_closed)
                dw.exit_requested.connect(self._do_exit_desktop_mode)
                dw.open_file_browser.connect(self._on_open_file_browser)
                dw.open_path_in_finder.connect(self._on_open_path_in_finder)
                dw.show()
                if self._desktop_icon_out:
                    self.desktop_btn.setIcon(self._desktop_icon_out)
                    self.desktop_btn.setText("")
                else:
                    self.desktop_btn.setIcon(QIcon())
                    self.desktop_btn.setText("✕")
                self.desktop_btn.setToolTip("退出桌面模式")
            except Exception:
                traceback.print_exc()
                self.desktop_window = None
                if _explorer_killed:
                    start_explorer()
                    _explorer_killed = False
                self._restore_aero_style()

    def _do_exit_desktop_mode(self):
        """退出桌面模式统一入口：恢复 explorer / aero / 按钮"""
        global _explorer_killed
        if self.desktop_window:
            try:
                self.desktop_window.destroyed.disconnect(self._on_desktop_closed)
            except Exception:
                pass
            self.desktop_window.close()
            self.desktop_window = None
        if self._desktop_icon:
            self.desktop_btn.setIcon(self._desktop_icon)
            self.desktop_btn.setText("")
        else:
            self.desktop_btn.setText("⊞")
        self.desktop_btn.setToolTip("桌面模式")
        if _explorer_killed:
            start_explorer()
            _explorer_killed = False
        self._restore_aero_style()

    def _on_desktop_closed(self):
        """桌面窗口被用户手动关闭 (Escape) 时调用"""
        global _explorer_killed
        self.desktop_window = None
        if self._desktop_icon:
            self.desktop_btn.setIcon(self._desktop_icon)
            self.desktop_btn.setText("")
        else:
            self.desktop_btn.setText("⊞")
        self.desktop_btn.setToolTip("桌面模式")
        if _explorer_killed:
            start_explorer()
            _explorer_killed = False
        self._restore_aero_style()

    def _on_open_file_browser(self):
        """双击桌面"此电脑"时：新开一个 Finder 窗口"""
        w = MacExplorer()
        w.show()
        if _explorer_killed:
            w._apply_fallback_style()
        self._extra_windows.append(w)
        w.destroyed.connect(lambda win=w: self._extra_windows.remove(win) if win in self._extra_windows else None)

    def _on_open_path_in_finder(self, target_path):
        """双击桌面回收站等：新开 Finder 并导航到指定路径"""
        w = MacExplorer()
        if os.path.exists(target_path):
            w.load_directory(target_path)
        w.show()
        if _explorer_killed:
            w._apply_fallback_style()
        self._extra_windows.append(w)
        w.destroyed.connect(lambda win=w: self._extra_windows.remove(win) if win in self._extra_windows else None)

    def closeEvent(self, event):
        for w in self._extra_windows[:]:
            try:
                w.close()
            except Exception:
                pass
        self._extra_windows.clear()
        self._folder_size_cache.clear()
        super().closeEvent(event)

    # ---- 桌面模式回退样式 (explorer 结束后 aero 失效) ----
    def _apply_fallback_style(self):
        """进入桌面模式：米白色实色背景 + 黑色文字，替代毛玻璃"""
        self._desktop_style_active = True
        self._color_timer.stop()
        BG = "#F5F0E8"
        tc = "#1a1a1a"
        sc = "#666666"
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {BG}; }}
            QLineEdit {{ color: {tc}; background-color: rgba(0,0,0,0.05); }}
            QTreeWidget {{ color: {tc}; }}
            QTreeWidget::item {{ color: {tc}; }}
            QHeaderView::section {{ color: {sc}; }}
        """)
        self.sidebar.setStyleSheet(f"""
            QTreeWidget {{ border: none; background: transparent; font-size: 13px; outline: none; }}
            QTreeWidget::item {{ height: 28px; border-radius: 6px; padding-left: 8px; color: {tc}; }}
            QTreeWidget::item:selected {{ background-color: rgba(0, 122, 255, 0.15); color: #007AFF; }}
            QTreeWidget::item:hover:!selected {{ background-color: rgba(0,0,0,0.06); }}
        """)
        self.file_view.setStyleSheet(f"""
            QTreeWidget {{ border: none; background: transparent; font-size: 13px; outline: none; }}
            QHeaderView::section {{ 
                border: none; border-bottom: 1px solid rgba(0,0,0,0.12); 
                padding: 6px; background: transparent; font-weight: 600; color: {sc};
            }}
            QTreeWidget::item {{ height: 32px; border-radius: 4px; color: {tc}; }}
            QTreeWidget::item:selected {{ background-color: #007AFF; color: white; }}
            QTreeWidget::item:hover:!selected {{ background-color: rgba(0,0,0,0.06); }}
        """)
        self.back_btn.setStyleSheet(f"""
            QPushButton {{
                border-radius: 16px; 
                background-color: rgba(0,0,0,0.06); 
                font-size: 20px; 
                font-weight: bold;
                color: {tc};
            }}
            QPushButton:hover {{ background-color: rgba(0,0,0,0.10); }}
            QPushButton:pressed {{ background-color: rgba(0,0,0,0.15); }}
        """)
        self.desktop_btn.setStyleSheet(f"""
            QPushButton {{
                border-radius: 16px;
                background-color: rgba(0,0,0,0.06);
                font-size: 16px;
                font-weight: bold;
                color: {tc};
            }}
            QPushButton:hover {{ background-color: rgba(0,0,0,0.10); }}
            QPushButton:pressed {{ background-color: rgba(0,0,0,0.15); }}
        """)
        self.path_bar.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid rgba(0,0,0,0.15); 
                border-radius: 16px; 
                padding: 0 15px;
                background-color: rgba(255,255,255,0.7);
                font-size: 13px;
                color: {tc};
            }}
            QLineEdit:focus {{ border: 1px solid #007AFF; }}
        """)

    def _restore_aero_style(self):
        """退出桌面模式：恢复毛玻璃效果"""
        self._desktop_style_active = False
        try:
            pywinstyles.apply_style(self, "aero")
            pywinstyles.set_opacity(self, color="#FFFFFF")
        except Exception:
            pass
        self._color_timer.start()
        self._refresh_colors()

    def go_back(self):
        if self.history:
            prev_path = self.history.pop()
            self.current_path = ""
            self.load_directory(prev_path)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Backspace:
            self.go_back()
        elif event.key() == Qt.Key.Key_Z and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._undo_last()
        else:
            super().keyPressEvent(event)

    def _undo_last(self):
        """Ctrl+Z"""
        if not self.undo_mgr.can_undo():
            QMessageBox.information(self, "提示", "没有可撤销的操作")
            return
        if self.undo_mgr.undo():
            cur = self.current_path
            self.current_path = ""
            self.load_directory(cur)
        else:
            QMessageBox.critical(self, "错误", "撤销失败")

    def on_file_double_click(self, item):
        is_dir = item.data(0, Qt.ItemDataRole.UserRole + 1)
        is_link = item.data(0, Qt.ItemDataRole.UserRole + 2)
        path = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            if is_link:
                resolved = self.resolve_shortcut(path)
                if resolved:
                    if os.path.isdir(resolved):
                        self.load_directory(resolved)
                    else:
                        os.startfile(resolved)
                    return

            if is_dir:
                self.load_directory(path)
            else:
                os.startfile(path)
        except Exception:
            pass

    def show_context_menu(self, pos):
        sender = self.sender()
        item = sender.itemAt(pos) if sender else None
        if not item:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: rgba(255,255,255,0.95); border: 1px solid rgba(0,0,0,0.1); border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 20px; border-radius: 4px; }
            QMenu::item:selected { background-color: #007AFF; color: white; }
        """)

        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return

        is_dir = os.path.isdir(path)

        open_act = QAction("打开", self)
        open_act.triggered.connect(lambda: self.load_directory(path) if is_dir else os.startfile(path))
        menu.addAction(open_act)

        if not is_dir:
            ext = os.path.splitext(path)[1].lower()
            if ext in ['.exe', '.msi', '.bat', '.cmd', '.com', '.vbs', '.ps1']:
                admin_act = QAction("以管理员身份运行", self)
                admin_act.triggered.connect(lambda: self.run_as_admin(path))
                menu.addAction(admin_act)

        menu.addSeparator()

        drive_root = os.path.splitdrive(path)[0] + "\\"
        if self.is_removable_or_cdrom(drive_root):
            eject_act = QAction("弹出", self)
            eject_act.triggered.connect(lambda: self.eject_device(drive_root))
            menu.addAction(eject_act)
            menu.addSeparator()

        rename_act = QAction("重命名", self)
        rename_act.triggered.connect(lambda: self.rename_item(item, path))
        menu.addAction(rename_act)

        del_act = QAction("移到回收站", self)
        del_act.triggered.connect(lambda: self.delete_to_trash(path))
        menu.addAction(del_act)

        prop_act = QAction("属性", self)
        prop_act.triggered.connect(lambda: self.show_properties(path))
        menu.addAction(prop_act)

        menu.exec(sender.viewport().mapToGlobal(pos))

    def run_as_admin(self, path):
        try:
            if win32con is not None:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", path, None, os.path.dirname(path), win32con.SW_NORMAL)
            else:
                ctypes.windll.shell32.ShellExecuteW(None, "runas", path, None, os.path.dirname(path), 1)
        except Exception:
            QMessageBox.critical(self, "错误", "无法获取管理员权限")

    def is_removable_or_cdrom(self, drive_path):
        try:
            DRIVE_REMOVABLE = 2
            DRIVE_CDROM = 5
            drive_type = kernel32.GetDriveTypeW(drive_path)
            return drive_type in (DRIVE_REMOVABLE, DRIVE_CDROM)
        except Exception:
            return False

    def eject_device(self, drive_path):
        reply = QMessageBox.question(self, "确认弹出", f"确定要弹出 {drive_path} 吗？", 
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # try winmm method for cd drives
                if winmm is not None:
                    drive_letter = drive_path.rstrip("\\")
                    cmd = f"open {drive_letter} type cdaudio alias cd_drive"
                    winmm.mciSendStringW(cmd, None, 0, None)
                    winmm.mciSendStringW("set cd_drive door open", None, 0, None)
                    winmm.mciSendStringW("close cd_drive", None, 0, None)
                # fallback to shell eject
                if win32con is not None:
                    ctypes.windll.shell32.ShellExecuteW(None, "eject", drive_path, None, None, win32con.SW_HIDE)
                QMessageBox.information(self, "成功", "设备已准备好安全移除")
                self.load_drives()
            except Exception as e:
                QMessageBox.critical(self, "弹出失败", str(e))

    def rename_item(self, item, old_path):
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=os.path.basename(old_path))
        if ok and new_name:
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            try:
                os.rename(old_path, new_path)
                self.load_directory(self.current_path)
            except Exception as e:
                QMessageBox.critical(self, "重命名失败", str(e))

    def delete_to_trash(self, path):
        try:
            if winshell is not None:
                winshell.delete_file(path, no_confirm=True, allow_undo=True)
            elif send2trash is not None:
                send2trash.send2trash(path)
            else:
                os.remove(path)
            self.load_directory(self.current_path)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", str(e))

    def show_properties(self, path):
        try:
            info = QFileInfo(path)
            size_str = self.format_size(info.size()) if info.isFile() else "-"
            msg = f"名称: {info.fileName()}\n路径: {info.absoluteFilePath()}\n大小: {size_str}\n修改时间: {info.lastModified().toString('yyyy/MM/dd HH:mm')}"
            QMessageBox.information(self, "属性", msg)
        except Exception as e:
            QMessageBox.critical(self, "错误", str(e))

    def _detect_brightness(self):
        """采样窗口区域屏幕像素，返回平均亮度 0~255"""
        try:
            screen = QApplication.primaryScreen()
            geo = self.geometry()
            # 抓取窗口覆盖的整个屏幕区域
            pm = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
            img = pm.toImage()
            total = 0.0
            count = 0
            # 步进采样，每 step 个像素取一个，兼顾性能与准确度
            step = max(4, min(geo.width(), geo.height()) // 50)
            for y in range(0, img.height(), step):
                for x in range(0, img.width(), step):
                    c = img.pixelColor(x, y)
                    total += (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000.0
                    count += 1
            return total / count if count > 0 else 128
        except Exception:
            return 128
    def _refresh_colors(self):
        if self._desktop_style_active:
            return
        b = self._detect_brightness()
        dark = b < 128
        if dark == self._last_dark_state:
            return
        self._last_dark_state = dark
        tc = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
        sc = QColor(200, 200, 200) if dark else QColor(100, 100, 100)
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: transparent; }}
            QLineEdit {{ color: {tc.name()}; background-color: rgba(255,255,255,0.1); }}
            QTreeWidget {{ color: {tc.name()}; }}
            QTreeWidget::item {{ color: {tc.name()}; }}
            QHeaderView::section {{ color: {sc.name()}; }}
        """)
        self.sidebar.setStyleSheet(f"""
            QTreeWidget {{ border: none; background: transparent; font-size: 13px; outline: none; }}
            QTreeWidget::item {{ height: 28px; border-radius: 6px; padding-left: 8px; color: {tc.name()}; }}
            QTreeWidget::item:selected {{ background-color: rgba(0, 122, 255, 0.15); color: #007AFF; }}
            QTreeWidget::item:hover:!selected {{ background-color: rgba(0,0,0,0.04); }}
        """)
        self.file_view.setStyleSheet(f"""
            QTreeWidget {{ border: none; background: transparent; font-size: 13px; outline: none; }}
            QHeaderView::section {{ 
                border: none; border-bottom: 1px solid rgba(0,0,0,0.1); 
                padding: 6px; background: transparent; font-weight: 600; color: {sc.name()};
            }}
            QTreeWidget::item {{ height: 32px; border-radius: 4px; color: {tc.name()}; }}
            QTreeWidget::item:selected {{ background-color: #007AFF; color: white; }}
            QTreeWidget::item:hover:!selected {{ background-color: rgba(0,0,0,0.04); }}
        """)
        self.back_btn.setStyleSheet(f"""
            QPushButton {{
                border-radius: 16px; 
                background-color: rgba(0,0,0,0.05); 
                font-size: 20px; 
                font-weight: bold;
                color: {tc.name()};
            }}
            QPushButton:hover {{ background-color: rgba(0,0,0,0.1); }}
            QPushButton:pressed {{ background-color: rgba(0,0,0,0.15); }}
        """)
        self.desktop_btn.setStyleSheet(f"""
            QPushButton {{
                border-radius: 16px;
                background-color: rgba(0,0,0,0.05);
                font-size: 16px;
                font-weight: bold;
                color: {tc.name()};
            }}
            QPushButton:hover {{ background-color: rgba(0,0,0,0.1); }}
            QPushButton:pressed {{ background-color: rgba(0,0,0,0.15); }}
        """)
        self.path_bar.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid rgba(0,0,0,0.1); 
                border-radius: 16px; 
                padding: 0 15px;
                background-color: rgba(255,255,255,0.6);
                font-size: 13px;
                color: {tc.name()};
            }}
            QLineEdit:focus {{ border: 1px solid #007AFF; }}
        """)
    def showEvent(self, event):
        super().showEvent(event)
        try:
            pywinstyles.apply_style(self, "aero")
            pywinstyles.set_opacity(self, color="#FFFFFF")
        except:
            pass
        self._refresh_colors()
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMessageBox { background-color: #1e1e1e; }
        QMessageBox QLabel { color: #FFFFFF; background: transparent; }
        QMessageBox QPushButton { color: #FFFFFF; background-color: #3a3a3a; border: 1px solid #555; border-radius: 4px; padding: 4px 16px; }
        QMessageBox QPushButton:hover { background-color: #4a4a4a; }
        QInputDialog { background-color: #1e1e1e; }
        QInputDialog QLabel { color: #FFFFFF; background: transparent; }
        QInputDialog QLineEdit { color: #FFFFFF; background-color: #2d2d2d; border: 1px solid #555; border-radius: 4px; padding: 4px 8px; }
        QInputDialog QPushButton { color: #FFFFFF; background-color: #3a3a3a; border: 1px solid #555; border-radius: 4px; padding: 4px 16px; }
        QInputDialog QPushButton:hover { background-color: #4a4a4a; }
    """)
    window = MacExplorer()
    window.show()
    sys.exit(app.exec())