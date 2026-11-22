import csv
import os
import threading
from collections import defaultdict
from dataclasses import fields
from pathlib import Path

from PySide6.QtCharts import QChart, QChartView, QLineSeries
from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gnss_cn0_tool.adb import Adb, DeviceError
from gnss_cn0_tool.reporting import replay
from gnss_cn0_tool.run import RunControls, Runner
from gnss_cn0_tool.store import Plan, RunStore, Scene

SCENE_COLUMNS = [item.name for item in fields(Scene)]


class Window(QMainWindow):
    status_changed = Signal(str, str)
    worker_finished = Signal(str, str)
    devices_found = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("GNSS C/N0 场景测试")
        self.resize(1160, 800)
        local = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        self.store = RunStore(local / "GNSS CN0" / "runs")
        self.controls = RunControls()
        self.worker = None
        self.options = {
            "max_uncertainty_ms": 500, "frequency_tolerance_hz": 2_000_000, "min_samples": 1,
            "collect_nmea": False, "collect_raw": False,
        }
        self.devices = QComboBox()
        self.adb_path = QLineEdit("adb")
        self.test_apps = QCheckBox("并装联调 APK")
        self.refresh = QPushButton("扫描设备")
        self.refresh.clicked.connect(self.refresh_devices)
        self.plan = QTableWidget(0, len(SCENE_COLUMNS))
        self.plan.setHorizontalHeaderLabels(SCENE_COLUMNS)
        self.history = QComboBox()
        self.history.currentIndexChanged.connect(self.show_report)
        self.result = QTableWidget()
        self.chart = QChartView(QChart())
        self.chart.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.status = QLabel("选择设备与测试计划")
        self.status.setWordWrap(True)
        self.start = self.button("开始", self.start_run)
        self.pause = self.button("场景结束后暂停", self.toggle_pause)
        self.cancel = self.button("取消", self.controls.cancel.set)
        self.confirm = self.button("人工确认", self.controls.manual.set)
        self.skip = self.button("跳过", self.controls.skip.set)
        self.status_changed.connect(self.on_status)
        self.worker_finished.connect(self.on_finished)
        self.devices_found.connect(self.apply_devices)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.page(self.row(QLabel("ADB"), self.adb_path, self.devices, self.refresh, self.test_apps),
                                   self.row(self.button("载入计划", self.load_plan),
                                            self.button("保存计划", self.save_plan),
                                            self.button("添加场景", self.add_scene),
                                            self.button("删除所选场景", self.remove_scene)),
                                   self.plan), "设备与计划")
        self.tabs.addTab(self.page(
            self.row(self.history, self.button("离线回放", self.replay_folder),
                     self.button("打开报告目录", self.open_reports)),
            self.result, self.chart,
        ), "历史与结果")
        self.setCentralWidget(self.page(
            self.tabs, self.status, self.row(self.start, self.pause, self.confirm, self.skip, self.cancel)
        ))
        self.set_plan(Plan.default())
        self.load_history()
        self.set_buttons(False)

    def button(self, label, action):
        button = QPushButton(label)
        button.clicked.connect(action)
        return button

    def row(self, *widgets):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        for item in widgets:
            layout.addWidget(item)
        return widget

    def page(self, *widgets):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        for item in widgets:
            layout.addWidget(item)
        return widget

    def refresh_devices(self):
        self.refresh.setEnabled(False)
        executable = self.adb_path.text()

        def find():
            try:
                self.devices_found.emit(Adb(executable).devices(), "")
            except DeviceError as error:
                self.devices_found.emit([], str(error))

        threading.Thread(target=find, daemon=True).start()

    def apply_devices(self, devices, error):
        self.refresh.setEnabled(True)
        self.devices.clear()
        self.devices.addItem("请选择设备", None)
        for device in devices:
            self.devices.addItem(device.label, device)
        self.status.setText(error or ("请选择设备" if devices else "ADB 未发现设备"))

    def set_plan(self, plan):
        self.options = {key: getattr(plan, key) for key in self.options}
        self.plan.setRowCount(0)
        for scene in plan.scenes:
            self.add_scene(scene)

    def add_scene(self, scene=None):
        scene = scene if isinstance(scene, Scene) else Scene("baseline")
        row = self.plan.rowCount()
        self.plan.insertRow(row)
        for column, key in enumerate(SCENE_COLUMNS):
            self.plan.setItem(row, column, QTableWidgetItem(str(getattr(scene, key))))
        self.plan.resizeColumnsToContents()

    def remove_scene(self):
        for row in sorted({index.row() for index in self.plan.selectedIndexes()}, reverse=True):
            self.plan.removeRow(row)

    def current_plan(self):
        scenes = []
        for row in range(self.plan.rowCount()):
            values = {}
            for column, item in enumerate(fields(Scene)):
                value = self.plan.item(row, column).text().strip()
                if item.name in {"stabilize_seconds", "measure_seconds", "manual_timeout_seconds"}:
                    values[item.name] = float(value)
                elif item.name in {"repeat", "particles", "cpu_threads"}:
                    values[item.name] = int(value)
                else:
                    values[item.name] = value
            scenes.append(Scene(**values))
        return Plan(tuple(scenes), **self.options)

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

    def start_run(self):
        device = self.devices.currentData()
        if not device or device.state != "device":
            self.status.setText("请选择已授权的 ADB 设备")
            return
        try:
            plan = self.current_plan()
        except (ValueError, TypeError) as error:
            self.status.setText(str(error))
            return
        for event in vars(self.controls).values():
            event.clear()
        runner = Runner(
            Adb(self.adb_path.text(), device.serial, test_apps=self.test_apps.isChecked()),
            self.store, self.controls,
            self.status_changed.emit,
        )
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
        self.status.setText(f"{state}  {reason}")
        manual = state in {"WAITING_MANUAL_CONFIRM", "WAITING_MANUAL_EXIT"}
        self.confirm.setEnabled(manual)
        self.skip.setEnabled(manual)

    def on_finished(self, run_id, state):
        self.set_buttons(False)
        self.load_history(run_id)
        if run_id and (self.store.path(run_id) / "reports" / "report.xlsx").is_file():
            self.tabs.setCurrentIndex(1)

    def set_buttons(self, running):
        self.start.setEnabled(not running)
        self.pause.setEnabled(running)
        self.pause.setText("场景结束后暂停")
        self.cancel.setEnabled(running)
        self.confirm.setEnabled(False)
        self.skip.setEnabled(False)

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
            self.history.addItem(f"{state}  {run_id}", self.store.path(run_id))
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

    def show_report(self):
        self.result.clear()
        self.result.setRowCount(0)
        chart = QChart()
        chart.setTitle("GPS C/N0（dB-Hz）随 Android 单调时间变化")
        self.chart.setChart(chart)
        folder = self.history.currentData()
        if folder is None:
            return
        report = folder / "reports"
        summary = report / "comparisons.csv"
        if summary.is_file():
            with summary.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream)
                headers = next(reader, [])
                self.result.setColumnCount(len(headers))
                self.result.setHorizontalHeaderLabels(headers)
                for row in reader:
                    index = self.result.rowCount()
                    self.result.insertRow(index)
                    for column, cell in enumerate(row):
                        self.result.setItem(index, column, QTableWidgetItem(cell))
                self.result.resizeColumnsToContents()
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

    def closeEvent(self, event):
        if self.worker and self.worker.is_alive():
            self.controls.cancel.set()
            self.status.setText("等待停止采集与保存报告")
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
