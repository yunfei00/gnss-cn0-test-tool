import csv
import json
import os
import threading
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCharts import QChart, QChartView, QLineSeries
from PySide6.QtCore import QPoint, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gnss_cn0_tool.adb import Adb, DeviceError
from gnss_cn0_tool.gui_style import WORKSPACE_STYLE
from gnss_cn0_tool.reporting import replay
from gnss_cn0_tool.run import RunControls, Runner, RunProgress
from gnss_cn0_tool.store import Plan, RunStore, Scene

DEFAULT_SELECTION = {"screen_on", "video", "screen_off"}
SCENE_NAMES = {
    "screen_on": "亮屏", "screen_off": "灭屏", "baseline": "空闲基准", "video": "视频", "music": "音乐",
    "game_heavy": "高负载游戏", "front_camera": "前置摄像头",
    "rear_camera": "后置摄像头", "navigation_manual": "第三方导航",
    "motor": "马达", "flashlight": "手电筒", "recorder": "录音机", "white_screen": "白屏",
    "manual_scene": "自定义手动场景",
}
RUN_STATES = {
    "PREPARING_DISPLAY": "正在唤醒手机并检查锁屏",
    "PREPARING_HOME": "正在按 Home 返回系统桌面",
    "WAITING_DISPLAY_OFF": "请按手机电源键灭屏",
    "PREFLIGHT": "正在检查设备和测试应用", "LOGGER_START": "正在启动采集",
    "LOGGER_READY": "采集已启动，正在标定时钟", "PREPARING_SCENE": "正在准备场景",
    "WAIT_SCENE_READY": "正在等待场景就绪", "WAITING_MANUAL_CONFIRM": "等待确认进入场景",
    "WAITING_MANUAL_EXIT": "等待确认退出场景", "STABILIZING": "等待场景稳定",
    "MANUAL_CAPTURE_PREPARED": "采集已准备好",
    "MEASURING": "正在采样", "STOPPING_SCENE": "正在结束场景", "PAUSED": "任务已暂停",
    "SKIPPED": "场景已跳过", "FINALIZING": "正在停止采集并回收数据",
    "REPORTING": "正在生成报告", "COMPLETED": "测试完成", "COMPLETED_WITH_WARNINGS": "测试完成，有质量提示",
    "FAILED": "测试未完成", "INTERRUPTED": "测试中断", "CANCELLED": "已取消测试",
}
DEVICE_STATES = {"device": "已授权", "offline": "离线", "unauthorized": "待授权"}
QUALITY_STATES = {
    "OBSERVED": "有有效数据", "NO_L5_DATA": "无有效 L5 数据",
    "NO_L1_DATA": "无有效 L1 数据", "NO_MATCHED_SATELLITES": "无匹配卫星",
    "INSUFFICIENT_SAMPLES": "有效样本不足", "INVALID_WINDOW": "采样窗口无效",
}


class WorkspaceCombo(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#496580" if self.isEnabled() else "#8c9db0"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        x, y = self.width() - 14, self.height() // 2
        painter.drawPolyline([QPoint(x - 4, y - 2), QPoint(x, y + 2), QPoint(x + 4, y - 2)])


class SecondsInput(QDoubleSpinBox):
    def textFromValue(self, value):
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        return text.rstrip("0").removesuffix(separator) if separator in text else text


class Window(QMainWindow):
    status_changed = Signal(str, str)
    worker_finished = Signal(str, str)
    devices_found = Signal(str, object, str)
    progress_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GNSS C/N₀ 测试工作台")
        self.resize(1240, 820)
        self.setMinimumSize(880, 760)
        self.setStyleSheet(WORKSPACE_STYLE)
        local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        self.store = RunStore(local / "GNSS CN0" / "runs")
        self.controls = RunControls()
        self.worker = None
        self.options = {
            "max_uncertainty_ms": 500, "frequency_tolerance_hz": 2_000_000, "min_samples": 1,
            "collect_nmea": False, "collect_raw": False,
        }
        self.devices = WorkspaceCombo()
        self.devices.addItem("请选择设备", None)
        self.devices.setMinimumWidth(185)
        self.auto_select_device = True
        self.scanned_adb_path = None
        self.adb_path = QLineEdit("adb")
        self.adb_path.setPlaceholderText("输入 adb.exe 的完整路径")
        self.adb_path.setToolTip("默认用系统 PATH 中的 adb.exe，也可指定完整路径")
        self.adb_path.editingFinished.connect(self.refresh_devices)
        self.adb_browse = self.button("浏览…", self.browse_adb_path)
        self.device_status = self.label("正在扫描设备…", "pill")
        self.device_status.setMinimumWidth(170)
        self.device_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.app_variant = WorkspaceCombo()
        for title, value in [("自动识别", "auto"), ("正式版", "standard"), ("并装联调版", "test")]:
            self.app_variant.addItem(title, value)
        self.refresh = QPushButton("重新扫描")
        self.refresh.clicked.connect(self.refresh_devices)
        self.scene_grid = QWidget()
        self.scene_grid_layout = QGridLayout(self.scene_grid)
        self.scene_grid_layout.setContentsMargins(0, 0, 0, 0)
        self.scene_grid_layout.setSpacing(7)
        for column in range(4):
            self.scene_grid_layout.setColumnStretch(column, 1)
        self.scenes: dict[str, Scene] = {}
        self.scene_checks: dict[str, QCheckBox] = {}
        self.stabilize = self.seconds_input(0)
        self.duration = self.seconds_input(0.001)
        self.repeats = QSpinBox()
        self.repeats.setRange(1, 100)
        self.repeats.setSuffix(" 次")
        self.plan_count = self.label("", "muted")
        self.automatic_mode = QCheckBox("自动测试")
        self.automatic_mode.setChecked(True)
        self.mode_hint = self.label("", "muted")
        self.mode_hint.setWordWrap(True)
        self.running = False
        self.run_log = QPlainTextEdit()
        self.run_log.setObjectName("runLog")
        self.run_log.setReadOnly(True)
        self.run_log.setMaximumBlockCount(500)
        self.run_log.setFixedHeight(80)
        self.run_log.setPlaceholderText("任务开始后，记录场景、进度和结果。")
        self.last_progress_log = None
        self.history = WorkspaceCombo()
        self.report_status = self.label("选择一项历史测试查看结果。", "muted")
        self.history.currentIndexChanged.connect(self.show_report)
        self.result = QTableWidget()
        self.configure_table(self.result)
        self.result.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.chart = QChartView(QChart())
        self.chart.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.chart_empty = self.label("暂无 C/N₀ 趋势数据", "chartEmpty")
        self.chart_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chart_stack = QStackedWidget()
        self.chart_stack.addWidget(self.chart_empty)
        self.chart_stack.addWidget(self.chart)
        self.status_title = self.label("待开始", "statusTitle")
        self.status = self.label("选择手机和场景后即可开始测试。", "muted")
        self.status.setWordWrap(True)
        self.progress_text = self.label("", "muted")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(420)
        self.progress_bar.hide()
        self.progress_text.hide()
        self.start = self.button("开始测试", self.start_run, "primary")
        self.prepare = self.button("准备采集", lambda: self.start_run(prepare=True))
        self.pause = self.button("场景结束后暂停", self.toggle_pause)
        self.cancel = self.button("取消", self.controls.cancel.set)
        self.confirm = self.button("人工确认", self.controls.manual.set, "primary")
        self.skip = self.button("跳过", self.controls.skip.set)
        self.status_changed.connect(self.on_status)
        self.progress_changed.connect(self.show_progress)
        self.worker_finished.connect(self.on_finished)
        self.devices_found.connect(self.apply_devices)
        self.tabs = QStackedWidget()
        self.tabs.addWidget(self.plan_page())
        self.tabs.addWidget(self.results_page())
        self.setCentralWidget(self.workspace())
        self.set_plan(Plan.default(), defaults=True)
        self.automatic_mode.toggled.connect(self.change_test_mode)
        self.load_history()
        self.show_report()
        self.set_buttons(False)
        self.update_test_mode()
        self.device_timer = QTimer(self)
        self.device_timer.setInterval(3000)
        self.device_timer.timeout.connect(self.refresh_devices)
        self.device_timer.start()
        QTimer.singleShot(0, self.refresh_devices)

    def label(self, text, name):
        label = QLabel(text)
        label.setObjectName(name)
        return label

    def seconds_input(self, minimum):
        field = SecondsInput()
        field.setDecimals(3)
        field.setRange(minimum, 3600)
        field.setSingleStep(1)
        field.setSuffix(" 秒")
        return field

    def button(self, label, action, name=""):
        button = QPushButton(label)
        button.setObjectName(name)
        button.clicked.connect(action)
        return button

    def row(self, *widgets):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        for item in widgets:
            if item is None:
                layout.addStretch()
            else:
                layout.addWidget(item)
        return widget

    def column(self, *widgets, name="", margins=0):
        widget = QFrame() if name in {"card", "runBar"} else QWidget()
        widget.setObjectName(name)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(margins, margins, margins, margins)
        layout.setSpacing(10)
        for item in widgets:
            if item is None:
                layout.addStretch()
            else:
                layout.addWidget(item)
        return widget

    def configure_table(self, table):
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(37)
        table.horizontalHeader().setMinimumHeight(35)

    def workspace(self):
        mark = self.label("GN", "brandMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(42, 42)
        rail = self.column(mark, self.label("GNSS C/N₀", "brand"), name="rail", margins=18)
        rail.setFixedWidth(178)
        navigation = QButtonGroup(rail)
        for index, name in enumerate(("测试工作台", "历史与报告")):
            button = QToolButton()
            button.setObjectName("navigate")
            button.setText(name)
            button.setCheckable(True)
            button.setChecked(index == 0)
            navigation.addButton(button, index)
            rail.layout().addWidget(button)
        navigation.idClicked.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(lambda index: navigation.button(index).setChecked(True))
        rail.layout().addStretch()
        status = self.column(self.status_title, self.status, self.progress_text, self.progress_bar)
        status.layout().setSpacing(4)
        controls = self.row(status, self.confirm, self.skip, self.pause, self.cancel, self.prepare, self.start)
        controls.layout().setStretch(0, 1)
        run_bar = self.column(controls, name="runBar", margins=14)
        log_panel = self.column(self.label("运行日志", "sectionTitle"), self.run_log, name="card", margins=12)
        main = self.column(self.tabs, log_panel, run_bar, name="workspace", margins=18)
        main.layout().setStretch(0, 1)
        shell = self.row(rail, main)
        shell.layout().setSpacing(0)
        return shell

    def plan_page(self):
        path_row = self.row(self.adb_path, self.adb_browse)
        path_row.layout().setStretch(0, 1)
        path_settings = self.column(self.label("ADB 可执行文件（默认从系统 PATH 查找）", "fieldName"), path_row)
        path_settings.hide()
        advanced_connection = QPushButton("连接设置")
        advanced_connection.setObjectName("quiet")
        advanced_connection.setCheckable(True)
        advanced_connection.toggled.connect(path_settings.setVisible)
        advanced_connection.toggled.connect(
            lambda expanded: advanced_connection.setText("收起连接设置" if expanded else "连接设置")
        )
        device_fields = self.row(
            self.column(self.label("测试手机", "fieldName"), self.devices),
            self.column(self.label("测试应用", "fieldName"), self.app_variant),
            self.refresh, None, advanced_connection,
        )
        device_fields.layout().setAlignment(self.refresh, Qt.AlignmentFlag.AlignBottom)
        device_fields.layout().setAlignment(advanced_connection, Qt.AlignmentFlag.AlignBottom)
        device_card = self.column(
            self.row(self.label("设备连接", "sectionTitle"), None, self.device_status),
            device_fields, path_settings, name="card", margins=14,
        )
        shared_parameters = self.row(
            self.column(self.label("稳定等待", "fieldName"), self.stabilize),
            self.column(self.label("采样时长", "fieldName"), self.duration),
            self.column(self.label("每场景次数", "fieldName"), self.repeats),
            self.automatic_mode, None,
        )
        shared_parameters.layout().setAlignment(self.automatic_mode, Qt.AlignmentFlag.AlignBottom)
        self.plan_card = self.column(
            self.row(self.label("测试场景", "sectionTitle"), self.plan_count, None,
                     self.button("导入", self.load_plan), self.button("保存", self.save_plan),
                     self.button("添加手动场景", self.add_manual_scene, "quiet")),
            self.scene_grid,
            shared_parameters,
            self.mode_hint,
            name="card", margins=14,
        )
        page = self.column(device_card, self.plan_card, None)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        page.setObjectName("workspace")
        scroll.setWidget(page)
        return scroll

    def results_page(self):
        panel = self.column(
            self.row(self.history, None, self.button("打开每秒数据", self.open_timed_data),
                     self.button("离线回放", self.replay_folder),
                     self.button("打开报告目录", self.open_reports)),
            self.report_status, self.result, self.chart_stack, name="card", margins=14,
        )
        panel.layout().setStretch(2, 1)
        panel.layout().setStretch(3, 2)
        return panel

    def selected_scenes(self):
        return [self.scenes[scene_id] for scene_id, box in self.scene_checks.items() if box.isChecked()]

    def select_only_scene(self, scene_id):
        for key, box in self.scene_checks.items():
            with QSignalBlocker(box):
                box.setChecked(key == scene_id)

    def change_test_mode(self, automatic):
        selected = [scene.scene_id for scene in self.selected_scenes()]
        if not automatic and len(selected) > 1:
            self.select_only_scene("screen_off" if "screen_off" in selected else selected[0])
        self.update_test_mode()

    def change_scene_selection(self, scene_id, checked):
        if checked and not self.automatic_mode.isChecked():
            self.select_only_scene(scene_id)
        self.update_test_mode()

    def update_test_mode(self):
        selected = self.selected_scenes()
        self.plan_count.setText(f"{len(selected)} / {len(self.scenes)} 已选")
        self.automatic_mode.setEnabled(not self.running)
        manual = not self.automatic_mode.isChecked()
        manual_screen_off = manual and len(selected) == 1 and selected[0].scene_id == "screen_off"
        self.prepare.setVisible(manual and len(selected) == 1 and not manual_screen_off and not self.running)
        self.start.setText("开始手动采样" if manual else "开始自动测试")
        if manual_screen_off:
            hint = "先点开始，看到日志提示后按手机电源键灭屏，检测到全灭后自动采样。"
        elif manual:
            hint = "单选模式。先把手机切到目标场景再开始；若手机限制后台启动，请先点「准备采集」。"
        elif any(scene.mode == "manual" for scene in selected):
            hint = "外部场景仅支持手动采样。请只勾选该场景，再关闭「自动测试」。"
        elif len(selected) > 1:
            hint = "已选多个场景，将按从左到右、从上到下的顺序自动测试。"
        else:
            hint = "勾选场景后开始自动测试。关闭「自动测试」可切换到单场景手动采样。"
        self.mode_hint.setText(hint)

    def log_message(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.run_log.appendPlainText(f"{timestamp}  {text}")

    def show_progress(self, progress: RunProgress):
        remaining = progress.remaining_seconds
        if remaining is not None and progress.phase_seconds:
            self.progress_bar.setRange(0, progress.phase_seconds)
            self.progress_bar.setValue(progress.phase_seconds - remaining)
            self.progress_bar.setFormat("本阶段 %v / %m 秒")
        else:
            self.progress_bar.setRange(0, progress.total)
            self.progress_bar.setValue(progress.processed)
            self.progress_bar.setFormat("已处理 %v / %m 次")
        details = [f"已处理 {progress.processed} / {progress.total} 次"]
        if progress.scene:
            name = SCENE_NAMES.get(progress.scene, progress.scene)
            details.append(f"第 {progress.processed + 1} 项 · {name}")
        if progress.repeats > 1:
            details.append(f"本场景第 {progress.repetition} / {progress.repeats} 次")
        if remaining is not None:
            details.append(f"剩余 {remaining} 秒")
        self.progress_text.setText(" · ".join(details))
        self.progress_text.setVisible(bool(details))
        self.progress_bar.show()
        if remaining is not None and (
            remaining in {0, progress.phase_seconds} or remaining % 10 == 0
        ):
            entry = (self.status_title.text(), progress.processed, remaining)
            if entry != self.last_progress_log:
                self.log_message(" · ".join(details))
                self.last_progress_log = entry

    def browse_adb_path(self):
        current = Path(self.adb_path.text().strip())
        directory = str(current.parent) if current.is_absolute() else ""
        path, _ = QFileDialog.getOpenFileName(self, "选择 adb.exe", directory, "ADB (adb.exe)")
        if path:
            self.adb_path.setText(str(Path(path)))
            self.refresh_devices()

    def refresh_devices(self):
        if not self.refresh.isEnabled():
            return
        self.refresh.setEnabled(False)
        executable = self.adb_path.text().strip()

        def find():
            try:
                devices = Adb(executable).devices()
            except DeviceError as error:
                self.devices_found.emit(executable, [], str(error))
            except (OSError, ValueError):
                self.devices_found.emit(executable, [], "无法运行 ADB，请检查可执行文件路径")
            else:
                self.devices_found.emit(executable, devices, "")

        threading.Thread(target=find, daemon=True).start()

    def apply_devices(self, executable, devices, error):
        self.refresh.setEnabled(True)
        if executable != self.adb_path.text().strip():
            self.refresh_devices()
            return
        self.scanned_adb_path = executable
        selected = self.devices.currentData()
        selected_serial = selected.serial if selected else None
        by_serial = {device.serial: device for device in devices}
        if self.auto_select_device and len(by_serial) == 1 and devices[0].state == "device":
            selected_serial = devices[0].serial
            self.auto_select_device = False
        elif len(by_serial) > 1 or selected is not None:
            self.auto_select_device = False

        # Update rows in place so polling preserves the list order and current selection.
        for index in range(self.devices.count() - 1, 0, -1):
            previous = self.devices.itemData(index)
            updated = by_serial.pop(previous.serial, None)
            if updated is None:
                self.devices.removeItem(index)
            elif updated != previous:
                self.devices.setItemText(index, self.device_label(updated))
                self.devices.setItemData(index, updated)
        for device in by_serial.values():
            self.devices.addItem(self.device_label(device), device)
        index = next(
            (i for i in range(1, self.devices.count())
             if self.devices.itemData(i).serial == selected_serial),
            0,
        )
        self.devices.setCurrentIndex(index)
        self.device_status.setToolTip(error)
        self.device_status.setText("设备扫描失败" if error else (
            f"{len(devices)} 台设备 · {sum(device.state == 'device' for device in devices)} 台已授权"
            if devices else "等待连接手机"
        ))

    def device_label(self, device):
        code = device.label.split()[0]
        return f"{code.upper()} · {DEVICE_STATES[device.state]}"

    def set_plan(self, plan, *, defaults=False):
        selected = {scene.scene_id: scene for scene in plan.scenes}
        if len(selected) != len(plan.scenes):
            raise ValueError("旧计划包含重复场景。请将重复项合并为一个场景，再用次数表示。")
        self.options = {key: getattr(plan, key) for key in self.options}
        for box in self.scene_checks.values():
            self.scene_grid_layout.removeWidget(box)
            box.hide()
            box.deleteLater()
        self.scenes.clear()
        self.scene_checks.clear()
        catalog = {scene.scene_id: scene for scene in Plan.default().scenes}
        for scene_id, scene in selected.items():
            if scene_id in catalog:
                scene = replace(scene, mode=catalog[scene_id].mode)
            self.add_scene_tile(scene, scene_id in DEFAULT_SELECTION if defaults else True)
        for scene_id, scene in catalog.items():
            if scene_id not in selected:
                self.add_scene_tile(scene, False)
        first = plan.scenes[0]
        self.stabilize.setValue(first.stabilize_seconds)
        self.duration.setValue(first.measure_seconds)
        self.repeats.setValue(first.repeat)
        self.automatic_mode.setChecked(len(plan.scenes) > 1 or first.mode == "auto")
        self.update_test_mode()
        if any(
            (s.stabilize_seconds, s.measure_seconds, s.repeat)
            != (first.stabilize_seconds, first.measure_seconds, first.repeat)
            for s in plan.scenes
        ):
            self.log_message("导入的各场景参数不同，公用参数已取第一项，可在开始前调整。")

    def add_scene_tile(self, scene, checked):
        name = SCENE_NAMES.get(scene.scene_id, scene.scene_id)
        box = QCheckBox(name)
        box.setObjectName("sceneTile")
        if scene.mode == "manual":
            box.setToolTip("外部场景，仅支持手动采样")
        elif scene.scene_id == "recorder":
            box.setToolTip("需要在手机上授予场景应用麦克风权限")
        box.setChecked(checked)
        box.toggled.connect(lambda checked: self.change_scene_selection(scene.scene_id, checked))
        self.scene_grid_layout.addWidget(box, *divmod(len(self.scene_checks), 4))
        self.scenes[scene.scene_id] = scene
        self.scene_checks[scene.scene_id] = box

    def add_manual_scene(self):
        name, accepted = QInputDialog.getText(self, "添加手动场景", "场景名称")
        name = name.strip()
        if not accepted or not name:
            return
        if name in self.scenes or name in SCENE_NAMES.values():
            QMessageBox.warning(self, "场景已存在", "请直接勾选列表中的场景。")
            return
        for box in self.scene_checks.values():
            box.setChecked(False)
        self.add_scene_tile(Scene(name, "manual"), True)
        self.automatic_mode.setChecked(False)
        self.update_test_mode()

    def current_plan(self):
        scenes = self.selected_scenes()
        if not scenes:
            raise ValueError("请至少勾选一个要执行的场景")
        automatic = self.automatic_mode.isChecked()
        if automatic and any(scene.mode == "manual" for scene in scenes):
            raise ValueError("外部场景仅支持手动采样。请只选一个外部场景，并关闭「自动测试」。")
        return Plan(tuple(replace(
            scene, mode="auto" if automatic else "manual",
            stabilize_seconds=self.stabilize.value(), measure_seconds=self.duration.value(),
            repeat=self.repeats.value(),
        ) for scene in scenes), **self.options)

    def load_plan(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入计划", filter="JSON (*.json)")
        if path:
            try:
                self.set_plan(Plan.load(Path(path)))
            except (ValueError, OSError, TypeError, KeyError) as error:
                QMessageBox.warning(self, "计划无效", str(error))

    def save_plan(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存计划", filter="JSON (*.json)")
        if path:
            try:
                self.current_plan().save(Path(path))
            except (ValueError, OSError, TypeError) as error:
                QMessageBox.warning(self, "保存失败", str(error))

    def start_run(self, *, prepare=False):
        executable = self.adb_path.text().strip()
        if executable != self.scanned_adb_path:
            self.refresh_devices()
            self.on_status("待开始", "等待扫描当前 ADB 路径的设备。")
            return
        device = self.devices.currentData()
        if not device or device.state != "device":
            self.on_status("待开始", "请选择已授权的手机。待授权时请在手机上确认 USB 调试。")
            return
        try:
            plan = self.current_plan()
            manual = plan.scenes[0].mode == "manual"
            if prepare and not manual:
                raise ValueError("准备采集需要只选一项并关闭「自动测试」。")
        except (ValueError, TypeError) as error:
            self.on_status("请检查场景计划", str(error))
            return
        for event in vars(self.controls).values():
            event.clear()
        variant = self.app_variant.currentData()
        runner = Runner(
            Adb(executable, device.serial, test_apps=variant == "test"),
            self.store, self.controls,
            self.status_changed.emit,
            automatic_apps=variant == "auto",
            manual_ready=manual,
            prepare_manual=prepare,
            progress=self.progress_changed.emit,
        )
        self.run_log.clear()
        self.last_progress_log = None
        self.set_buttons(True)

        def work():
            try:
                self.worker_finished.emit(*runner.run(plan))
            except Exception as error:
                self.status_changed.emit("FAILED", str(error))
                self.worker_finished.emit("", "FAILED")

        self.worker = threading.Thread(target=work, name="GNSS run")
        self.worker.start()

    def on_status(self, state, reason):
        title = RUN_STATES.get(state, state)
        detail = SCENE_NAMES.get(reason, reason)
        self.status_title.setText(title)
        self.status.setText(detail)
        self.status.setVisible(bool(reason))
        self.log_message(f"{title} · {detail}" if detail else title)
        confirmation = state in {"WAITING_MANUAL_CONFIRM", "WAITING_MANUAL_EXIT"}
        prepared = state == "MANUAL_CAPTURE_PREPARED"
        self.confirm.setText("开始手动采样" if prepared else "人工确认")
        self.confirm.setEnabled(confirmation or prepared)
        self.confirm.setVisible(confirmation or prepared)
        self.skip.setEnabled(confirmation)
        self.skip.setVisible(confirmation)

    def on_finished(self, run_id, state):
        self.set_buttons(False)
        self.load_history(run_id)
        if run_id and (self.store.path(run_id) / "reports" / "report.xlsx").is_file():
            self.tabs.setCurrentIndex(1)

    def set_buttons(self, running):
        self.running = running
        self.start.setVisible(not running)
        self.pause.setVisible(running)
        self.cancel.setVisible(running)
        self.pause.setText("场景结束后暂停")
        self.confirm.hide()
        self.skip.hide()
        self.devices.setEnabled(not running)
        self.app_variant.setEnabled(not running)
        self.adb_path.setEnabled(not running)
        self.adb_browse.setEnabled(not running)
        self.plan_card.setEnabled(not running)
        self.update_test_mode()
        if running:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.show()
            self.progress_text.hide()

    def toggle_pause(self):
        if self.controls.pause.is_set():
            self.controls.pause.clear()
            self.pause.setText("场景结束后暂停")
        else:
            self.controls.pause.set()
            self.pause.setText("继续")

    def load_history(self, selected=None):
        self.history.clear()
        for run_id, state, _ in self.store.history():
            self.history.addItem(f"{RUN_STATES.get(state, state)} · {run_id[:8]}", self.store.path(run_id))
        if selected:
            index = self.history.findData(self.store.path(selected))
            if index >= 0:
                self.history.setCurrentIndex(index)

    def replay_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择包含 raw 子目录的运行目录")
        if not path:
            return
        folder = Path(path)
        try:
            replay(folder)
        except (DeviceError, ValueError, OSError, KeyError) as error:
            self.status.setText(str(error))
            return
        index = self.history.findData(folder)
        if index < 0:
            self.history.addItem(f"离线  {folder.name}", folder)
            index = self.history.count() - 1
        self.history.setCurrentIndex(index)
        self.show_report()

    def open_reports(self):
        folder = self.history.currentData()
        if folder and (folder / "reports").is_dir():
            os.startfile(folder / "reports")

    def open_timed_data(self):
        folder = self.history.currentData()
        path = folder / "reports" / "observations_1s.csv" if folder else None
        if path and path.is_file():
            os.startfile(path)
        elif folder:
            self.report_status.setText("此记录没有每秒表。离线回放该运行目录即可补生成。")

    def show_window_summary(self, path):
        self.result.setColumnCount(4)
        self.result.setHorizontalHeaderLabels(["测试场景", "频段", "卫星数", "数据质量"])
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for index, row in enumerate(csv.DictReader(stream)):
                scene = row.get("scene_id") or row.get("scene_instance_id", "")[:8]
                quality = row.get("quality", "")
                self.result.insertRow(index)
                for column, value in enumerate((
                    SCENE_NAMES.get(scene, scene), row.get("band") or "—", row.get("satellites") or "—",
                    QUALITY_STATES.get(quality, quality),
                )):
                    self.result.setItem(index, column, QTableWidgetItem(value))
        if self.result.rowCount():
            self.report_status.setText(f"{self.result.rowCount()} 项频段采样结果")

    def show_report(self):
        self.chart_stack.setCurrentWidget(self.chart_empty)
        self.result.clear()
        self.result.setRowCount(0)
        self.result.setColumnCount(6)
        self.result.setHorizontalHeaderLabels([
            "参照场景", "对比场景", "频段", "匹配卫星", "差值 A − B (dB)", "数据质量",
        ])
        self.result.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        chart = QChart()
        chart.setBackgroundBrush(QColor("#ffffff"))
        chart.setTitle("GPS C/N₀ 趋势")
        self.chart.setChart(chart)
        folder = self.history.currentData()
        if folder is None:
            self.report_status.setText("选择一项历史测试查看结果。")
            return
        report = folder / "reports"
        summary = report / "comparisons.csv"
        self.report_status.setText("暂无可比较的场景结果。")
        if summary.is_file():
            scenes_path = folder / "scenes.json"
            try:
                scenes = json.loads(scenes_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                scenes = {}

            def scene_name(instance):
                scene = scenes.get(instance, {}).get("scene_id", instance[:8])
                return SCENE_NAMES.get(scene, scene)

            with summary.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    index = self.result.rowCount()
                    self.result.insertRow(index)
                    delta = row.get("mean_a_minus_b_db")
                    quality = row.get("quality", "")
                    cells = [
                        scene_name(row.get("a_scene_instance_id", "")),
                        scene_name(row.get("b_scene_instance_id", "")),
                        row.get("band", ""), row.get("matched_svid") or "—",
                        f"{float(delta):+.2f}" if delta else "—",
                        QUALITY_STATES.get(quality, quality),
                    ]
                    for column, cell in enumerate(cells):
                        self.result.setItem(index, column, QTableWidgetItem(cell))
            if self.result.rowCount():
                self.report_status.setText(f"{self.result.rowCount()} 项频段比较")
        windows = report / "windows.csv"
        if not self.result.rowCount() and windows.is_file():
            self.show_window_summary(windows)
        quality_file = report / "quality.csv"
        if quality_file.is_file():
            with quality_file.open(encoding="utf-8-sig", newline="") as stream:
                reference = next(csv.DictReader(stream), {}).get("reference_status")
            if reference == "MISSING_SCREEN_OFF_REFERENCE":
                self.report_status.setText("缺少有效灭屏基准，已保留各场景数据。")
            elif reference == "SCREEN_OFF" and summary.is_file() and self.result.columnCount() == 6:
                self.report_status.setText(f"以灭屏为基准 · {self.result.rowCount()} 项频段比较")
        trend = report / "trend.csv"
        if not trend.is_file():
            return
        points = defaultdict(list)
        with trend.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                points[row["band"], row["svid"]].append(
                    (int(row["elapsed_ns"]), float(row["cn0_dbhz"]))
                )
        if not points:
            return
        first = min(min(values)[0] for values in points.values())
        for (band, svid), values in sorted(points.items()):
            series = QLineSeries()
            series.setName(f"G{int(svid):02} {band}")
            for clock, cn0 in sorted(values):
                series.append((clock - first) / 1e9, cn0)
            chart.addSeries(series)
        chart.createDefaultAxes()
        chart.axes()[0].setTitleText("秒")
        chart.axes()[1].setTitleText("C/N₀ (dB-Hz)")
        self.chart_stack.setCurrentWidget(self.chart)

    def closeEvent(self, event):
        if self.worker and self.worker.is_alive():
            self.controls.cancel.set()
            self.on_status("正在结束测试", "等待停止采集与保存报告。")
            event.ignore()
        else:
            event.accept()


def launch(*, smoke=False):
    app = QApplication([])
    window = Window()
    window.show()
    if smoke:
        QTimer.singleShot(50, app.quit)
    return app.exec()
