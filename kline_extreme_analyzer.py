"""
A股日内 5 分钟 K 线极值频率统计工具
===================================
统计日内最高价 / 最低价首次出现在第几根 5 分钟 K 线，并给出
「第 N 根 K 线收盘时极值已出现」的累积概率。

界面：
  🕐 日内极值概率  →  📊 关键数据统计 / 📋 每日极值明细
  📈 日线涨跌概率  →  📊 涨跌概率概览 / 📋 每日涨跌明细

性能设计（启动慢问题的修复）：
  * pandas / numpy / pyqtgraph / akshare 全部延迟导入，窗口立即出现；
  * 图表在首次分析时才创建，并在后台线程提前预热重依赖；
  * 股票列表预加载放到后台线程，缓存有效时完全不联网。

GUI 框架：PyQt6 + pyqtgraph
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDateEdit, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QSpinBox, QSplitter, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from kline_data import (
    A_STOCK_BARS,
    BAR_TIME_LABELS,
    build_stock_bar_labels,
    fetch_stock_5min,
    fetch_stock_daily,
    is_stock_cache_fresh,
    preload_stock_cache,
    search_stocks,
)
from kline_stats import (
    ANALYSIS_MODES,
    analyze_daily_extremes,
    compute_daily_updown_stats,
    compute_statistics,
)

# ═══════════════════════════════════════════════════════════════════════════
# 日志与常量
# ═══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("KlineExtremeAnalyzer")

_CN_TZ = ZoneInfo("Asia/Shanghai")

THEME = {
    "bg": "#0a0e14", "surface": "#161b22", "surface2": "#1c2128",
    "border": "#30363d", "fg": "#e6edf3", "fg2": "#8b949e", "fg3": "#6e7681",
    "accent": "#2dd4bf", "accent2": "#38bdf8",
    "up": "#22c55e", "down": "#ef4444", "warning": "#f59e0b",
}

WEEKDAYS_CN = ["一", "二", "三", "四", "五", "六", "日"]

# ── 应用图标 / Windows 任务栏分组 ─────────────────────────────────────────
_APP_ID = "WorkBuddy.KlineExtremeAnalyzer.v3"
_ICON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kline_analyzer.ico")


def set_app_user_model_id() -> None:
    """
    Windows 专用：给进程设置 AppUserModelID。

    不设置的话，任务栏会把窗口归到 python.exe.exe 名下，显示的是 Python 的图标；
    设置后任务栏才会用窗口自己的图标（与桌面快捷方式一致）。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_ID)
    except Exception as e:  # pragma: no cover
        logger.debug("设置 AppUserModelID 失败: %s", e)


def app_icon() -> QIcon:
    """读取程序图标（文件缺失时返回空图标，不影响运行）。"""
    if os.path.exists(_ICON_FILE):
        return QIcon(_ICON_FILE)
    return QIcon()


# ═══════════════════════════════════════════════════════════════════════════
# 后台预热：把重依赖的导入开销挪出启动路径
# ═══════════════════════════════════════════════════════════════════════════

def warmup_heavy_imports() -> None:
    """在后台线程预导入 pandas / numpy / pyqtgraph。"""
    for mod in ("numpy", "pandas", "pyqtgraph"):
        try:
            __import__(mod)
        except Exception as e:  # pragma: no cover - 仅记录，不影响启动
            logger.warning("预加载 %s 失败: %s", mod, e)


# ═══════════════════════════════════════════════════════════════════════════
# 后台工作线程
# ═══════════════════════════════════════════════════════════════════════════

class SearchWorker(QThread):
    """标的搜索（后台线程）。"""
    results_ready = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, keyword: str):
        super().__init__()
        self.keyword = keyword

    def run(self):
        try:
            self.results_ready.emit(search_stocks(self.keyword))
        except Exception as e:
            logger.exception("搜索失败")
            self.error.emit(str(e))


class FetchWorker(QThread):
    """日内极值分析（后台线程）。"""
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object, object, str)  # daily_df, stats, mode_label
    error = pyqtSignal(str)

    def __init__(self, symbol: str, start_date: str, end_date: str,
                 check_bars: list[int], mode: str = "ext"):
        super().__init__()
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.check_bars = check_bars
        self.mode = mode

    def run(self):
        try:
            self.progress.emit("正在获取5分钟K线数据...")
            df = fetch_stock_5min(self.symbol, self.start_date, self.end_date)
            self.progress.emit(f"获取到 {len(df)} 条数据，{df['date'].nunique()} 个交易日")

            self.progress.emit("正在分析日内极值分布...")
            daily_df = analyze_daily_extremes(df)
            self.progress.emit(f"分析完成，共 {len(daily_df)} 个有效交易日")

            self.progress.emit("正在计算统计数据...")
            stats = compute_statistics(
                daily_df, self.check_bars, self.mode, A_STOCK_BARS,
                labels=build_stock_bar_labels(),
            )
            stats["warnings"] = []
            self.finished_ok.emit(daily_df, stats, ANALYSIS_MODES.get(self.mode, self.mode))
        except Exception as e:
            logger.exception("分析失败")
            self.error.emit(str(e))


class DailyFetchWorker(QThread):
    """日线涨跌概率分析（后台线程）。"""
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(object, object)  # daily_df, stats
    error = pyqtSignal(str)

    def __init__(self, symbol: str, start_date: str, end_date: str):
        super().__init__()
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date

    def run(self):
        try:
            self.progress.emit("正在获取日线数据...")
            df = fetch_stock_daily(self.symbol, self.start_date, self.end_date)
            self.progress.emit(f"获取到 {len(df)} 个交易日数据")

            self.progress.emit("正在计算涨跌概率...")
            stats = compute_daily_updown_stats(df)
            self.finished_ok.emit(stats["daily_df"], stats)
        except Exception as e:
            logger.exception("日线分析失败")
            self.error.emit(str(e))


# ═══════════════════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("K线极值频率统计 — A股")
        self.setWindowIcon(app_icon())
        self.resize(1200, 850)

        self.daily_df = None
        self.stats = None
        self.daily_stats = None

        self.search_worker: SearchWorker | None = None
        self.fetch_worker: FetchWorker | None = None
        self.daily_fetch_worker: DailyFetchWorker | None = None

        self.pg = None
        self._charts_ready = False
        self._daily_charts_ready = False

        self._apply_theme()
        self._build_ui()

    # ── 主题 ────────────────────────────────────────────────────────────────
    def _apply_theme(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background-color: {THEME["bg"]}; color: {THEME["fg"]}; }}
            QGroupBox {{
                border: 1px solid {THEME["border"]}; border-radius: 8px; margin-top: 12px;
                padding: 16px 12px 12px 12px; font-weight: bold; font-size: 13px; color: {THEME["fg2"]};
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
            QLineEdit, QSpinBox, QComboBox, QDateEdit {{
                background: {THEME["surface2"]}; border: 1px solid {THEME["border"]};
                border-radius: 4px; padding: 4px 8px; color: {THEME["fg"]};
            }}
            QComboBox::drop-down {{ border: none; }}
            QComboBox QAbstractItemView {{
                background: {THEME["surface"]}; border: 1px solid {THEME["border"]};
                selection-background-color: {THEME["accent"]}; selection-color: {THEME["bg"]};
            }}
            QPushButton {{
                background: {THEME["surface2"]}; border: 1px solid {THEME["border"]};
                border-radius: 4px; padding: 5px 16px; color: {THEME["fg"]}; font-weight: bold;
            }}
            QPushButton:hover {{ background: {THEME["border"]}; }}
            QPushButton#primary {{
                background: {THEME["accent"]}; color: {THEME["bg"]}; border: none;
                font-size: 14px; padding: 6px 24px;
            }}
            QPushButton#primary:hover {{ background: #5ee8d4; }}
            QPushButton#primary:disabled {{ background: {THEME["surface2"]}; color: {THEME["fg3"]}; }}
            QRadioButton {{ color: {THEME["fg"]}; spacing: 4px; }}
            QRadioButton::indicator {{ width: 14px; height: 14px; }}
            QTableWidget {{
                background: {THEME["surface"]}; border: 1px solid {THEME["border"]};
                gridline-color: {THEME["border"]}; color: {THEME["fg"]};
            }}
            QTableWidget::item {{ padding: 4px 8px; }}
            QHeaderView::section {{
                background: {THEME["surface2"]}; border: 1px solid {THEME["border"]};
                padding: 4px; color: {THEME["fg2"]}; font-weight: bold;
            }}
            QTabWidget::pane {{ border: 1px solid {THEME["border"]}; background: {THEME["surface"]}; }}
            QTabBar::tab {{
                background: {THEME["surface2"]}; border: 1px solid {THEME["border"]};
                padding: 8px 20px; margin-right: 2px; color: {THEME["fg2"]};
                border-top-left-radius: 6px; border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{
                background: {THEME["accent"]}; color: {THEME["bg"]}; font-weight: bold;
            }}
            QTextEdit {{
                background: {THEME["surface"]}; border: 1px solid {THEME["border"]};
                color: {THEME["fg"]}; font-family: "Consolas", "Microsoft YaHei UI", monospace;
            }}
            QProgressBar {{
                border: 1px solid {THEME["border"]}; border-radius: 4px;
                background: {THEME["surface2"]}; text-align: center; color: {THEME["fg"]};
            }}
            QProgressBar::chunk {{ background: {THEME["accent"]}; border-radius: 3px; }}
        """)

    # ── 整体布局 ────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(12, 10, 12, 10)

        self.top_tabs = QTabWidget()
        root.addWidget(self.top_tabs, 1)

        self.top_tabs.addTab(self._build_intraday_tab(), "🕐 日内极值概率")
        self.top_tabs.addTab(self._build_daily_tab(), "📈 日线涨跌概率")

    # ── Tab1：日内极值概率 ──────────────────────────────────────────────────
    def _build_intraday_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._build_control_panel())

        self.status_label = QLabel("就绪 — 输入股票代码或名称开始分析")
        self.status_label.setStyleSheet(f"color: {THEME['fg2']}; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        # ── 子Tab：关键数据统计 ──
        self.result_tabs = QTabWidget()
        self.result_tabs.setVisible(False)
        self.result_tabs.currentChanged.connect(self._on_tab_changed)

        stats_tab = QWidget()
        stats_layout = QVBoxLayout(stats_tab)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(8)

        self.cards_layout = QHBoxLayout()
        self.cards_layout.setSpacing(12)
        stats_layout.addLayout(self.cards_layout)

        # 图表容器：pyqtgraph 延迟创建
        self.chart_container = QWidget()
        self.chart_layout = QVBoxLayout(self.chart_container)
        self.chart_layout.setContentsMargins(0, 0, 0, 0)
        self.chart_placeholder = QLabel("图表将在首次分析时加载…")
        self.chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chart_placeholder.setStyleSheet(f"color: {THEME['fg3']}; font-size: 12px;")
        self.chart_layout.addWidget(self.chart_placeholder)
        stats_layout.addWidget(self.chart_container, 1)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(280)
        stats_layout.addWidget(self.summary_text)

        self.result_tabs.addTab(stats_tab, "📊 关键数据统计")

        # ── 子Tab：每日极值明细 ──
        detail_tab = QWidget()
        detail_layout = QVBoxLayout(detail_tab)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        self.detail_table = QTableWidget()
        self.detail_table.setColumnCount(8)
        self.detail_table.setHorizontalHeaderLabels([
            "日期", "星期", "最高价", "最低价",
            "最高出现\nK线序号", "最低出现\nK线序号",
            "极值出现\nK线序号", "极值类型",
        ])
        self.detail_table.horizontalHeader().setStretchLastSection(True)
        detail_layout.addWidget(self.detail_table)

        self.result_tabs.addTab(detail_tab, "📋 每日极值明细")

        layout.addWidget(self.result_tabs, 1)
        return tab

    # ── 参数面板 ────────────────────────────────────────────────────────────
    def _build_control_panel(self) -> QWidget:
        group = QGroupBox("参数设置")
        grid = QGridLayout(group)
        grid.setSpacing(8)

        # 行0：代码搜索
        grid.addWidget(QLabel("代码/名称:"), 0, 0)
        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("股票如 600519 / 贵州茅台")
        self.symbol_input.setFixedWidth(230)
        self.symbol_input.returnPressed.connect(self._on_search)
        grid.addWidget(self.symbol_input, 0, 1)

        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self._on_search)
        grid.addWidget(self.search_btn, 0, 2)

        grid.addWidget(QLabel("选择:"), 0, 3)
        self.symbol_combo = QComboBox()
        self.symbol_combo.setMinimumWidth(260)
        grid.addWidget(self.symbol_combo, 0, 4)

        # 行1：日期范围
        grid.addWidget(QLabel("日期:"), 1, 0)
        date_box = QHBoxLayout()
        self.recent_radio = QRadioButton("最近")
        self.custom_radio = QRadioButton("自定义")
        self.recent_radio.setChecked(True)
        self.recent_radio.toggled.connect(lambda v: self.days_spin.setEnabled(v))
        self.custom_radio.toggled.connect(lambda v: self.start_date_edit.setEnabled(v))
        self.custom_radio.toggled.connect(lambda v: self.end_date_edit.setEnabled(v))
        date_box.addWidget(self.recent_radio)

        self.days_spin = QSpinBox()
        self.days_spin.setRange(5, 500)
        self.days_spin.setValue(60)
        self.days_spin.setSuffix(" 天")
        self.days_spin.setToolTip("回测自然日数")
        date_box.addWidget(self.days_spin)

        date_box.addWidget(self.custom_radio)
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addDays(-90))
        self.start_date_edit.setEnabled(False)
        date_box.addWidget(self.start_date_edit)

        date_box.addWidget(QLabel("~"))
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        self.end_date_edit.setEnabled(False)
        date_box.addWidget(self.end_date_edit)
        date_box.addStretch()
        grid.addLayout(date_box, 1, 1, 1, 4)

        # 行2：分析模式 + 检查点 + 分析按钮
        grid.addWidget(QLabel("分析模式:"), 2, 0)
        mode_box = QHBoxLayout()
        self.mode_ext = QRadioButton("极值(最高或最低)")
        self.mode_high = QRadioButton("仅最高价")
        self.mode_low = QRadioButton("仅最低价")
        self.mode_ext.setChecked(True)
        for rb in (self.mode_ext, self.mode_high, self.mode_low):
            mode_box.addWidget(rb)
        mode_box.addStretch()
        grid.addLayout(mode_box, 2, 1, 1, 2)

        grid.addWidget(QLabel("检查点K线:"), 2, 3)
        self.check_bars_input = QLineEdit("1,7,18")
        self.check_bars_input.setFixedWidth(100)
        self.check_bars_input.setToolTip("逗号分隔的K线序号，如: 1,7,18,24")
        grid.addWidget(self.check_bars_input, 2, 4)

        self.analyze_btn = QPushButton("  ▶  开始分析  ")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.clicked.connect(self._on_analyze)
        grid.addWidget(self.analyze_btn, 2, 5, Qt.AlignmentFlag.AlignRight)

        return group

    # ── Tab2：日线涨跌概率 ──────────────────────────────────────────────────
    def _build_daily_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        ctrl = QGroupBox("日线涨跌概率 — 参数设置")
        grid = QGridLayout(ctrl)
        grid.setSpacing(8)

        grid.addWidget(QLabel("代码/名称:"), 0, 0)
        self.daily_symbol_input = QLineEdit()
        self.daily_symbol_input.setPlaceholderText("股票如 600519 / 贵州茅台")
        self.daily_symbol_input.setFixedWidth(230)
        self.daily_symbol_input.returnPressed.connect(self._on_daily_search)
        grid.addWidget(self.daily_symbol_input, 0, 1)

        self.daily_search_btn = QPushButton("搜索")
        self.daily_search_btn.clicked.connect(self._on_daily_search)
        grid.addWidget(self.daily_search_btn, 0, 2)

        grid.addWidget(QLabel("选择:"), 0, 3)
        self.daily_symbol_combo = QComboBox()
        self.daily_symbol_combo.setMinimumWidth(260)
        grid.addWidget(self.daily_symbol_combo, 0, 4)

        grid.addWidget(QLabel("起始日期:"), 1, 0)
        self.daily_start_date = QDateEdit()
        self.daily_start_date.setCalendarPopup(True)
        self.daily_start_date.setDate(QDate.currentDate().addYears(-1))
        grid.addWidget(self.daily_start_date, 1, 1)

        grid.addWidget(QLabel("结束日期:"), 1, 2)
        self.daily_end_date = QDateEdit()
        self.daily_end_date.setCalendarPopup(True)
        self.daily_end_date.setDate(QDate.currentDate())
        grid.addWidget(self.daily_end_date, 1, 3)

        self.daily_analyze_btn = QPushButton("  ▶  开始分析  ")
        self.daily_analyze_btn.setObjectName("primary")
        self.daily_analyze_btn.clicked.connect(self._on_daily_analyze)
        grid.addWidget(self.daily_analyze_btn, 1, 4, Qt.AlignmentFlag.AlignRight)

        layout.addWidget(ctrl)

        self.daily_status_label = QLabel("就绪 — 输入股票代码或名称，选择日期范围开始分析")
        self.daily_status_label.setStyleSheet(f"color: {THEME['fg2']}; font-size: 12px;")
        layout.addWidget(self.daily_status_label)

        self.daily_progress_bar = QProgressBar()
        self.daily_progress_bar.setVisible(False)
        self.daily_progress_bar.setFixedHeight(6)
        self.daily_progress_bar.setTextVisible(False)
        layout.addWidget(self.daily_progress_bar)

        self.daily_result_tabs = QTabWidget()
        self.daily_result_tabs.setVisible(False)

        # 子Tab 1：涨跌概览
        overview_tab = QWidget()
        overview_layout = QVBoxLayout(overview_tab)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(8)

        self.daily_cards_layout = QHBoxLayout()
        self.daily_cards_layout.setSpacing(12)
        overview_layout.addLayout(self.daily_cards_layout)

        self.daily_chart_container = QWidget()
        self.daily_chart_layout = QVBoxLayout(self.daily_chart_container)
        self.daily_chart_layout.setContentsMargins(0, 0, 0, 0)
        self.daily_chart_placeholder = QLabel("图表将在首次分析时加载…")
        self.daily_chart_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.daily_chart_placeholder.setStyleSheet(f"color: {THEME['fg3']}; font-size: 12px;")
        self.daily_chart_layout.addWidget(self.daily_chart_placeholder)
        overview_layout.addWidget(self.daily_chart_container, 1)

        self.daily_summary_text = QTextEdit()
        self.daily_summary_text.setReadOnly(True)
        self.daily_summary_text.setMaximumHeight(280)
        overview_layout.addWidget(self.daily_summary_text)

        self.daily_result_tabs.addTab(overview_tab, "📊 涨跌概率概览")

        # 子Tab 2：每日涨跌明细
        daily_detail_tab = QWidget()
        daily_detail_layout = QVBoxLayout(daily_detail_tab)
        daily_detail_layout.setContentsMargins(0, 0, 0, 0)

        self.daily_detail_table = QTableWidget()
        self.daily_detail_table.setColumnCount(7)
        self.daily_detail_table.setHorizontalHeaderLabels([
            "日期", "星期", "收盘价", "前日收盘", "涨跌额", "涨跌幅", "方向",
        ])
        self.daily_detail_table.horizontalHeader().setStretchLastSection(True)
        daily_detail_layout.addWidget(self.daily_detail_table)

        self.daily_result_tabs.addTab(daily_detail_tab, "📋 每日涨跌明细")

        layout.addWidget(self.daily_result_tabs, 1)
        return tab

    # ── 图表（延迟创建） ────────────────────────────────────────────────────
    def _ensure_charts(self):
        """首次需要时创建 pyqtgraph 图表（避免拖慢启动）。"""
        if self._charts_ready:
            return
        import pyqtgraph as pg
        pg.setConfigOption("antialias", True)
        self.pg = pg

        self.chart_splitter = QSplitter(Qt.Orientation.Vertical)
        self.freq_plot = pg.PlotWidget(title="极值出现频率分布")
        self.cum_plot = pg.PlotWidget(title="累积概率曲线")
        for pw in (self.freq_plot, self.cum_plot):
            pw.setBackground(THEME["bg"])
            pw.showGrid(x=True, y=True, alpha=0.15)
            self.chart_splitter.addWidget(pw)
        self.chart_splitter.setSizes([300, 250])

        while self.chart_layout.count():
            item = self.chart_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.chart_layout.addWidget(self.chart_splitter)
        self._charts_ready = True

    def _ensure_daily_charts(self):
        """首次需要时创建日线图表。"""
        if self._daily_charts_ready:
            return
        import pyqtgraph as pg
        pg.setConfigOption("antialias", True)
        self.pg = pg

        self.daily_chart_splitter = QSplitter(Qt.Orientation.Vertical)
        self.daily_pie_plot = pg.PlotWidget(title="涨跌概率分布")
        self.daily_weekday_plot = pg.PlotWidget(title="按星期涨跌统计")
        for pw in (self.daily_pie_plot, self.daily_weekday_plot):
            pw.setBackground(THEME["bg"])
            pw.showGrid(x=True, y=True, alpha=0.15)
            self.daily_chart_splitter.addWidget(pw)
        self.daily_chart_splitter.setSizes([250, 250])

        while self.daily_chart_layout.count():
            item = self.daily_chart_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self.daily_chart_layout.addWidget(self.daily_chart_splitter)
        self._daily_charts_ready = True

    # ── 搜索 ────────────────────────────────────────────────────────────────
    def _on_search(self):
        keyword = self.symbol_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入股票代码或名称")
            return

        self.search_btn.setEnabled(False)
        self.symbol_combo.clear()
        self.symbol_combo.addItem("搜索中...")
        self.status_label.setText("正在搜索...")

        self.search_worker = SearchWorker(keyword)
        self.search_worker.results_ready.connect(self._on_search_results)
        self.search_worker.error.connect(self._on_search_error)
        self.search_worker.start()

    def _on_search_results(self, results):
        self.search_btn.setEnabled(True)
        self.symbol_combo.clear()
        if results:
            for r in results:
                self.symbol_combo.addItem(f"{r['code']} — {r['name']}", r)
            self.status_label.setText(f"搜索到 {len(results)} 个结果")
        else:
            self.symbol_combo.addItem("未找到结果，可直接输入代码分析")
            self.status_label.setText("未找到结果，请尝试其他关键词")

    def _on_search_error(self, err):
        self.search_btn.setEnabled(True)
        self.symbol_combo.clear()
        self.symbol_combo.addItem("搜索失败，可直接输入代码分析")
        self.status_label.setText(f"搜索失败: {err}")
        logger.error("搜索失败: %s", err)

    # ── 开始分析（日内极值） ────────────────────────────────────────────────
    def _on_analyze(self):
        idx = self.symbol_combo.currentIndex()
        if idx >= 0 and self.symbol_combo.itemData(idx):
            data = self.symbol_combo.itemData(idx)
            symbol = data["code"] if isinstance(data, dict) else str(data)
        else:
            symbol = self.symbol_input.text().strip()
            if not symbol:
                QMessageBox.warning(self, "提示", "请先搜索并选择标的，或直接输入代码")
                return

        if self.recent_radio.isChecked():
            end_dt = datetime.now(tz=_CN_TZ)
            days = self.days_spin.value()
            start_dt = end_dt - timedelta(days=int(days * 1.6) + 10)
            start_date = start_dt.strftime("%Y-%m-%d")
            end_date = end_dt.strftime("%Y-%m-%d")
        else:
            sd = self.start_date_edit.date()
            ed = self.end_date_edit.date()
            if sd > ed:
                QMessageBox.warning(self, "提示", "起始日期不能晚于结束日期")
                return
            start_date = sd.toString("yyyy-MM-dd")
            end_date = ed.toString("yyyy-MM-dd")

        try:
            check_bars = [max(1, int(x.strip()))
                          for x in self.check_bars_input.text().split(",") if x.strip()]
        except ValueError:
            QMessageBox.warning(self, "提示", "检查点格式错误，请用逗号分隔数字，如: 1,7,18")
            return
        if not check_bars:
            check_bars = [1, 7, 18]

        mode = "high" if self.mode_high.isChecked() else ("low" if self.mode_low.isChecked() else "ext")

        self.analyze_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("准备分析...")
        self.result_tabs.setVisible(False)

        self.fetch_worker = FetchWorker(symbol, start_date, end_date, check_bars, mode)
        self.fetch_worker.progress.connect(self._on_progress)
        self.fetch_worker.finished_ok.connect(self._on_analysis_done)
        self.fetch_worker.error.connect(self._on_analysis_error)
        self.fetch_worker.start()

    def _on_progress(self, msg: str):
        self.status_label.setText(msg)

    def _on_analysis_error(self, err: str):
        self.analyze_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"分析失败: {err}")
        QMessageBox.critical(self, "分析失败", str(err))

    def _on_analysis_done(self, daily_df, stats: dict, mode_label: str):
        self.analyze_btn.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.daily_df = daily_df
        self.stats = stats

        self.result_tabs.setVisible(True)
        self._update_stat_cards()
        self._update_detail_table()
        self._refresh_charts()
        self._update_summary_text()

        symbol_text = self.symbol_combo.currentText() or self.symbol_input.text().strip()
        self.status_label.setText(
            f"分析完成 — {stats['total_days']} 个交易日, 标的: {symbol_text}, 模式: {mode_label}")

    def _on_tab_changed(self, index: int):
        """切换到统计页时重绘图表（pyqtgraph 需要重新适配尺寸）。"""
        if index == 0 and self.stats is not None:
            self._refresh_charts()

    # ── 统计卡片 ────────────────────────────────────────────────────────────
    @staticmethod
    def _make_card(title: str, value: str, subtitle: str, color: str, width: int = 190) -> QFrame:
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {THEME["surface"]}; border: 1px solid {THEME["border"]};
                border-radius: 10px; padding: 10px;
            }}
            QFrame:hover {{ border-color: {THEME["accent"]}; }}
        """)
        card.setFixedWidth(width)
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(2)
        card_layout.setContentsMargins(14, 10, 14, 10)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {THEME['fg2']}; font-size: 12px; font-weight: bold;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        value_lbl = QLabel(value)
        value_lbl.setStyleSheet(f"color: {color}; font-size: 26px; font-weight: bold;")
        value_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet(f"color: {THEME['fg3']}; font-size: 11px;")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card_layout.addWidget(title_lbl)
        card_layout.addWidget(value_lbl)
        card_layout.addWidget(sub_lbl)
        return card

    def _update_stat_cards(self):
        """显示各检查点的累积概率卡片 + 方向定型时点卡片。"""
        for i in reversed(range(self.cards_layout.count())):
            w = self.cards_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        # 首张卡：方向定型时点（P 首次达到 50% 的 K 线）
        t50 = self.stats.get("percentiles", {}).get("T50")
        labels = BAR_TIME_LABELS
        if t50:
            clock = f"({labels[t50 - 1]})" if t50 <= len(labels) else ""
            card = self._make_card(
                "方向定型时点 P≥50%",
                f"第 {t50} 根",
                clock.strip("()") or f"第{t50}根K线",
                THEME["accent2"],
                width=170,
            )
            self.cards_layout.addWidget(card)

        for cb in sorted(self.stats.get("check_points", {}).keys()):
            cp = self.stats["check_points"][cb]
            card = self._make_card(
                f"第 {cb} 根 K 线",
                f"{cp['prob']:.1%}",
                f"{cp['count']}/{cp['total']} 天",
                THEME["accent"],
            )
            self.cards_layout.addWidget(card)

        self.cards_layout.addStretch()

    # ── 每日明细表格 ────────────────────────────────────────────────────────
    def _update_detail_table(self):
        import pandas as pd

        if self.daily_df is None or self.daily_df.empty:
            return
        df = self.daily_df.sort_values("date", ascending=False)
        self.detail_table.setRowCount(len(df))

        for i, (_, row) in enumerate(df.iterrows()):
            date_val = row["date"]
            if isinstance(date_val, date):
                wd = WEEKDAYS_CN[date_val.weekday()]
            else:
                try:
                    wd = WEEKDAYS_CN[date.fromisoformat(str(date_val)).weekday()]
                except Exception:
                    wd = ""

            high_bar = int(row["high_bar"]) if pd.notna(row["high_bar"]) else 0
            low_bar = int(row["low_bar"]) if pd.notna(row["low_bar"]) else 0
            ext_bar = int(row["ext_bar"])

            if high_bar == low_bar:
                ext_type = "同时出现"
            elif ext_bar == high_bar:
                ext_type = "最高价先现"
            else:
                ext_type = "最低价先现"

            labels = BAR_TIME_LABELS

            def bar_text(n: int) -> str:
                if not labels or n > len(labels):
                    return f"第{n}根"
                return f"第{n}根 ({labels[n - 1]})"

            self._set_cell(i, 0, str(date_val))
            self._set_cell(i, 1, f"周{wd}")
            self._set_cell(i, 2, f"{row['day_high']:.2f}", THEME["down"])
            self._set_cell(i, 3, f"{row['day_low']:.2f}", THEME["up"])
            self._set_cell(i, 4, bar_text(high_bar))
            self._set_cell(i, 5, bar_text(low_bar))
            self._set_cell(i, 6, bar_text(ext_bar), THEME["accent"])
            self._set_cell(i, 7, ext_type, THEME["warning"])

        self.detail_table.resizeColumnsToContents()
        for j in range(8):
            self.detail_table.setColumnWidth(j, max(self.detail_table.columnWidth(j), 100))

    def _set_cell(self, row: int, col: int, text: str,
                  color: str | None = None, align=Qt.AlignmentFlag.AlignCenter):
        item = QTableWidgetItem(text)
        item.setTextAlignment(align)
        if color:
            item.setForeground(QColor(color))
        self.detail_table.setItem(row, col, item)

    # ── 图表 ────────────────────────────────────────────────────────────────
    def _refresh_charts(self):
        if self.stats is None:
            return
        self._ensure_charts()
        pg = self.pg
        s = self.stats
        dist = s["distribution"]
        cum = s["cumulative"]
        max_bars = s["max_bars"]
        x = list(range(1, max_bars + 1))

        # 频率分布
        self.freq_plot.clear()
        self.freq_plot.addItem(pg.BarGraphItem(x=x, height=dist, width=0.85, brush=THEME["accent"]))
        for cb in sorted(s.get("check_points", {}).keys()):
            if 1 <= cb <= max_bars:
                self.freq_plot.plot([cb], [dist[cb - 1]], pen=None, symbol="o",
                                    symbolSize=10, symbolBrush=THEME["warning"])
        self.freq_plot.setTitle(f"极值出现频率分布（模式: {s.get('mode_label', '')}）")
        self.freq_plot.setLabel("left", "出现天数")
        self.freq_plot.setLabel("bottom", "K线序号")

        # 累积概率
        self.cum_plot.clear()
        self.cum_plot.plot(x, cum, pen=pg.mkPen(color=THEME["accent2"], width=2))
        self.cum_plot.addItem(pg.InfiniteLine(
            pos=0.5, angle=0, pen=pg.mkPen(color=THEME["fg3"], style=Qt.PenStyle.DashLine)))
        for cb in sorted(s.get("check_points", {}).keys()):
            cp = s["check_points"][cb]
            if 1 <= cb <= max_bars:
                self.cum_plot.addItem(pg.ScatterPlotItem(
                    [cb], [cp["prob"]], symbol="s", size=12,
                    brush=THEME["warning"], pen=pg.mkPen(width=2, color=THEME["warning"])))
                label = pg.TextItem(f"{cp['prob']:.1%}", color=THEME["warning"], anchor=(0, 1))
                label.setPos(cb, cp["prob"] + 0.02)
                self.cum_plot.addItem(label)
        self.cum_plot.setTitle("累积概率曲线")
        self.cum_plot.setLabel("left", "累积概率")
        self.cum_plot.setLabel("bottom", "K线序号")
        self.cum_plot.setYRange(0, 1.1)

    # ── 统计报告 ────────────────────────────────────────────────────────────
    def _update_summary_text(self):
        if self.stats is None or self.daily_df is None:
            return
        s = self.stats
        df = self.daily_df
        bar_col = {"ext": "ext_bar", "high": "high_bar", "low": "low_bar"}.get(s["mode"], "ext_bar")
        labels = BAR_TIME_LABELS

        check_lines = []
        for cb in sorted(s.get("check_points", {}).keys()):
            cp = s["check_points"][cb]
            time_str = f"({labels[cb - 1]})" if 0 < cb <= len(labels) else ""
            check_lines.append(
                f"  │  第{cb:2d}根K线{time_str}收盘时: {cp['prob']:.2%}  "
                f"({cp['count']}/{cp['total']} 天)\n")

        detail_for_checks = []
        for cb in sorted(s.get("check_points", {}).keys()):
            days_in = sorted(df[df[bar_col] <= cb]["date"].tolist())
            if days_in:
                dates_str = ", ".join(str(d) for d in days_in[:15])
                if len(days_in) > 15:
                    dates_str += f" ... (共{len(days_in)}天)"
                detail_for_checks.append(f"  第{cb}根K线内已出现: {dates_str}\n")

        def at_label(value: float) -> str:
            idx = int(value) - 1
            return f" ({labels[idx]})" if 0 <= idx < len(labels) else ""

        # 方向定型时点
        pct = s.get("percentiles", {})
        settle_lines = []
        for key, label in (("T50", "P≥50%"), ("T80", "P≥80%"), ("T90", "P≥90%")):
            bar = pct.get(key)
            if bar:
                clock = labels[bar - 1] if bar <= len(labels) else ""
                settle_lines.append(f"  │  {label:6s} 第{bar:2d}根K线 ({clock}) — 方向基本定型\n")
            else:
                settle_lines.append(f"  │  {label:6s} 样本内未达到\n")

        symbol_text = self.symbol_combo.currentText() or self.symbol_input.text().strip()
        text = f"""
═══════════════════════════════════════════════════════════════
  K线极值出现频率统计报告
═══════════════════════════════════════════════════════════════
【基本信息】
  标的: {symbol_text}
  市场: A股（每日 {A_STOCK_BARS} 根 5 分钟 K 线）
  分析时段(交易日): {df['date'].min()} ~ {df['date'].max()}
  有效交易日: {s['total_days']} 天
  分析模式: {s.get('mode_label', '')}

【方向定型时点 — 最关键的一行结论】
{"".join(settle_lines)}
【核心统计 — 累积概率】
{"".join(check_lines)}

【描述统计】
  平均出现K线: {s['mean_val']:.1f} 根{at_label(s['mean_val'])}
  中位数:     {s['median_val']:.0f} 根{at_label(s['median_val'])}
  最早出现:   第 {s['min_val']} 根
  最晚出现:   第 {s['max_val']} 根
  标准差:     {s['std_val']:.1f} 根

【核对用 — 各检查点已满足的具体日期】
{"".join(detail_for_checks)}
═══════════════════════════════════════════════════════════════
"""
        self.summary_text.setPlainText(text)

    # ═══════════════════════════════════════════════════════════════════════
    # 日线涨跌概率
    # ═══════════════════════════════════════════════════════════════════════
    def _on_daily_search(self):
        keyword = self.daily_symbol_input.text().strip()
        if not keyword:
            QMessageBox.warning(self, "提示", "请输入股票代码或名称")
            return

        self.daily_search_btn.setEnabled(False)
        self.daily_symbol_combo.clear()
        self.daily_symbol_combo.addItem("搜索中...")
        self.daily_status_label.setText("正在搜索...")

        self.daily_search_worker = SearchWorker(keyword)
        self.daily_search_worker.results_ready.connect(self._on_daily_search_results)
        self.daily_search_worker.error.connect(self._on_daily_search_error)
        self.daily_search_worker.start()

    def _on_daily_search_results(self, results):
        self.daily_search_btn.setEnabled(True)
        self.daily_symbol_combo.clear()
        if results:
            for r in results:
                self.daily_symbol_combo.addItem(f"{r['code']} — {r['name']}", r)
            self.daily_status_label.setText(f"搜索到 {len(results)} 个结果")
        else:
            self.daily_symbol_combo.addItem("未找到结果，可直接输入代码分析")
            self.daily_status_label.setText("未找到结果，请尝试其他关键词")

    def _on_daily_search_error(self, err):
        self.daily_search_btn.setEnabled(True)
        self.daily_symbol_combo.clear()
        self.daily_symbol_combo.addItem("搜索失败，可直接输入代码分析")
        self.daily_status_label.setText(f"搜索失败: {err}")

    def _on_daily_analyze(self):
        idx = self.daily_symbol_combo.currentIndex()
        if idx >= 0 and self.daily_symbol_combo.itemData(idx):
            data = self.daily_symbol_combo.itemData(idx)
            symbol = data["code"] if isinstance(data, dict) else str(data)
        else:
            symbol = self.daily_symbol_input.text().strip()
            if not symbol:
                QMessageBox.warning(self, "提示", "请先搜索并选择标的，或直接输入代码")
                return

        sd = self.daily_start_date.date()
        ed = self.daily_end_date.date()
        if sd > ed:
            QMessageBox.warning(self, "提示", "起始日期不能晚于结束日期")
            return

        self.daily_analyze_btn.setEnabled(False)
        self.daily_progress_bar.setVisible(True)
        self.daily_progress_bar.setRange(0, 0)
        self.daily_status_label.setText("准备分析...")
        self.daily_result_tabs.setVisible(False)

        self.daily_fetch_worker = DailyFetchWorker(
            symbol, sd.toString("yyyy-MM-dd"), ed.toString("yyyy-MM-dd"))
        self.daily_fetch_worker.progress.connect(self._on_daily_progress)
        self.daily_fetch_worker.finished_ok.connect(self._on_daily_analysis_done)
        self.daily_fetch_worker.error.connect(self._on_daily_analysis_error)
        self.daily_fetch_worker.start()

    def _on_daily_progress(self, msg: str):
        self.daily_status_label.setText(msg)

    def _on_daily_analysis_error(self, err: str):
        self.daily_analyze_btn.setEnabled(True)
        self.daily_progress_bar.setVisible(False)
        self.daily_status_label.setText(f"分析失败: {err}")
        QMessageBox.critical(self, "分析失败", str(err))

    def _on_daily_analysis_done(self, daily_df, stats: dict):
        self.daily_analyze_btn.setEnabled(True)
        self.daily_progress_bar.setVisible(False)
        self.daily_stats = stats

        self.daily_result_tabs.setVisible(True)
        self._update_daily_cards()
        self._update_daily_detail_table()
        self._refresh_daily_charts()
        self._update_daily_summary_text()

        self.daily_status_label.setText(
            f"分析完成 — {stats['total_days']} 个交易日, "
            f"涨: {stats['up_count']} ({stats['up_prob']:.1%}), "
            f"跌: {stats['down_count']} ({stats['down_prob']:.1%})")

    def _update_daily_cards(self):
        for i in reversed(range(self.daily_cards_layout.count())):
            w = self.daily_cards_layout.itemAt(i).widget()
            if w:
                w.deleteLater()

        s = self.daily_stats
        cards = [
            ("上涨概率", f"{s['up_prob']:.1%}", f"{s['up_count']}/{s['total_days']} 天", THEME["up"]),
            ("下跌概率", f"{s['down_prob']:.1%}", f"{s['down_count']}/{s['total_days']} 天", THEME["down"]),
            ("平盘概率", f"{s['flat_prob']:.1%}", f"{s['flat_count']}/{s['total_days']} 天", THEME["fg2"]),
            ("最大连涨", f"{s['max_up_streak']} 天", "连续上涨", THEME["up"]),
            ("最大连跌", f"{s['max_down_streak']} 天", "连续下跌", THEME["down"]),
            ("平均涨幅", f"{s['avg_pct']:+.2f}%", "日均涨跌幅", THEME["accent"]),
        ]
        for title, value, subtitle, color in cards:
            self.daily_cards_layout.addWidget(
                self._make_card(title, value, subtitle, color, width=170))
        self.daily_cards_layout.addStretch()

    def _update_daily_detail_table(self):
        s = self.daily_stats
        df = s["daily_df"].sort_values("date", ascending=False).reset_index(drop=True)
        self.daily_detail_table.setRowCount(len(df))

        for i, (_, row) in enumerate(df.iterrows()):
            date_val = row["date"]
            wd = WEEKDAYS_CN[date_val.weekday()] if hasattr(date_val, "weekday") else ""
            direction = row["direction"]
            color = THEME["up"] if direction == "涨" else (THEME["down"] if direction == "跌" else THEME["fg2"])

            self._set_daily_cell(i, 0, str(date_val))
            self._set_daily_cell(i, 1, f"周{wd}")
            self._set_daily_cell(i, 2, f"{row['close']:.2f}")
            self._set_daily_cell(i, 3, f"{row['prev_close']:.2f}")
            self._set_daily_cell(i, 4, f"{row['change']:+.2f}", color)
            self._set_daily_cell(i, 5, f"{row['pct']:+.2f}%", color)
            self._set_daily_cell(i, 6, direction, color)

        self.daily_detail_table.resizeColumnsToContents()
        for j in range(7):
            self.daily_detail_table.setColumnWidth(j, max(self.daily_detail_table.columnWidth(j), 100))

    def _set_daily_cell(self, row: int, col: int, text: str,
                        color: str | None = None, align=Qt.AlignmentFlag.AlignCenter):
        item = QTableWidgetItem(text)
        item.setTextAlignment(align)
        if color:
            item.setForeground(QColor(color))
        self.daily_detail_table.setItem(row, col, item)

    def _refresh_daily_charts(self):
        self._ensure_daily_charts()
        pg = self.pg
        s = self.daily_stats

        # 涨跌概率分布
        self.daily_pie_plot.clear()
        labels = ["上涨", "下跌", "平盘"]
        values = [s["up_count"], s["down_count"], s["flat_count"]]
        colors = [THEME["up"], THEME["down"], THEME["fg2"]]

        for x, v, c, lbl in zip([0, 1, 2], values, colors, labels):
            self.daily_pie_plot.addItem(pg.BarGraphItem(x=[x], height=[v], width=0.6, brush=c))
            text = pg.TextItem(f"{lbl}\n{v}天\n{v / s['total_days']:.1%}", color=c, anchor=(0.5, 0.5))
            text.setPos(x, v / 2)
            self.daily_pie_plot.addItem(text)

        self.daily_pie_plot.setTitle("涨跌概率分布")
        self.daily_pie_plot.setLabel("left", "天数")
        self.daily_pie_plot.setLabel("bottom", "方向")
        self.daily_pie_plot.setXRange(-0.5, 2.5)
        self.daily_pie_plot.getAxis("bottom").setTicks([[(i, labels[i]) for i in range(3)]])

        # 按星期统计
        self.daily_weekday_plot.clear()
        wd_stats = s.get("weekday_stats", {})
        if wd_stats:
            wds = sorted(wd_stats.keys())
            up_vals = [wd_stats[wd]["up"] for wd in wds]
            down_vals = [wd_stats[wd]["down"] for wd in wds]
            wd_labels = [wd_stats[wd]["weekday"] for wd in wds]
            x_wd = list(range(len(wds)))

            self.daily_weekday_plot.addItem(pg.BarGraphItem(
                x=[x - 0.2 for x in x_wd], height=up_vals, width=0.35, brush=THEME["up"]))
            self.daily_weekday_plot.addItem(pg.BarGraphItem(
                x=[x + 0.2 for x in x_wd], height=down_vals, width=0.35, brush=THEME["down"]))

            self.daily_weekday_plot.setTitle("按星期涨跌统计")
            self.daily_weekday_plot.setLabel("left", "天数")
            self.daily_weekday_plot.setLabel("bottom", "星期")
            self.daily_weekday_plot.getAxis("bottom").setTicks(
                [[(i, wd_labels[i]) for i in range(len(wds))]])

    def _update_daily_summary_text(self):
        s = self.daily_stats
        wd_lines = []
        for wd in sorted(s.get("weekday_stats", {}).keys()):
            ws = s["weekday_stats"][wd]
            wd_lines.append(
                f"  周{WEEKDAYS_CN[wd]}: {ws['total']:3d}天  "
                f"涨{ws['up']:3d}({ws['up_prob']:.1%})  跌{ws['down']:3d}")

        symbol_text = self.daily_symbol_combo.currentText() or self.daily_symbol_input.text()
        text = f"""
═══════════════════════════════════════════════════════════════
  日线涨跌概率统计报告
═══════════════════════════════════════════════════════════════
【基本信息】
  标的: {symbol_text}
  分析时段: {s['date_range']}
  有效交易日: {s['total_days']} 天

【核心统计 — 涨跌概率】
  上涨: {s['up_count']:4d} 天  ({s['up_prob']:.2%})
  下跌: {s['down_count']:4d} 天  ({s['down_prob']:.2%})
  平盘: {s['flat_count']:4d} 天  ({s['flat_prob']:.2%})

【连续涨跌统计】
  最大连续上涨: {s['max_up_streak']} 天
  最大连续下跌: {s['max_down_streak']} 天

【涨跌幅统计】
  日均涨跌幅: {s['avg_pct']:+.3f}%
  最大单日涨幅: {s['max_up_pct']:+.2f}%
  最大单日跌幅: {s['max_down_pct']:+.2f}%

【按星期统计】
{chr(10).join(wd_lines) if wd_lines else "  无数据"}

═══════════════════════════════════════════════════════════════
"""
        self.daily_summary_text.setPlainText(text)


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════

def main():
    t0 = datetime.now()

    # 必须在创建窗口之前设置，任务栏才会显示程序自己的图标
    set_app_user_model_id()

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("K线极值频率统计")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    window = MainWindow()
    window.show()
    logger.info("窗口已显示，耗时 %.2fs", (datetime.now() - t0).total_seconds())

    # 后台预热：重依赖导入 + 股票列表刷新（均不阻塞界面）
    threading.Thread(target=warmup_heavy_imports, daemon=True).start()

    def _refresh_stock_list():
        if not is_stock_cache_fresh():
            preload_stock_cache()

    threading.Thread(target=_refresh_stock_list, daemon=True).start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
