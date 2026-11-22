# 02 系统架构、状态机与数据流

## 1. 分层架构

```text
Windows 上位机 gnss-cn0-test-tool
  PySide6 UI（测试方案 / 设备 / 运行进度 / 结果）
    ↓
  TestOrchestrator（唯一状态机、重试、事件审计）
    ├─ AdbDeviceManager（设备 ID、连接诊断、命令超时）
    ├─ ScenarioAdapter（AutoTestSceneApp / 第三方 APP / 人工）
    ├─ LoggerAdapter（GnssLoggerApp 状态 / Mark Event / 导出）
    ├─ ClockMapper（PC 与手机单调时钟映射与质量）
    ├─ ArtifactImporter（原始日志、校验、schema 版本）
    ├─ WindowSlicer（按 Android monotonic ns 分窗）
    ├─ Cn0Analyzer（GPS L1/L5、PRN 配对）
    ├─ ReportGenerator（CSV、Excel、测试摘要）
    └─ RunStore（SQLite + 文件目录 + 事件日志）
        ↓ USB ADB shell / push/pull / app 专用导出接口
Android:
  AutoTestSceneApp —— 业务场景执行、READY/STOPPED/ERROR
  GnssLoggerApp —— GNSS 状态/Raw/NMEA + 测试事件同一时基
环境：真实卫星（MVP）或 GSS7000（后续）
```

## 2. 模块边界

- 上位机只做**业务流程与日志分析**，不通过解析可变 `logcat` 文本作为唯一事实来源。
- Scene App 不负责 GNSS 采集；Logger App 不负责启动第三方业务 APP。
- 场景 READY 必须来源于真实执行条件，不以 `adb am start` 返回码替代。
- Logger 内的 GNSS 采样与 MARK_EVENT 在**同一 Android 设备、同一开机纪元单调时钟域**；PC 不依赖两端墙钟一致。
- 自动、半自动的区别由 `ScenarioAdapter` 抽象，进入测量阶段后路径一致。

## 3. 测试状态机

```text
IDLE -> PREFLIGHT -> LOGGER_START -> LOGGER_READY
  -> PREPARING_SCENE
     -> STARTING_AUTO -> WAIT_SCENE_READY
     -> WAITING_MANUAL_CONFIRM
  -> STABILIZING -> MEASURING -> ENDING_WINDOW
  -> STOPPING_SCENE -> NEXT_SCENE | FINALIZING
  -> LOG_EXPORT -> VALIDATING -> ANALYZING -> REPORTING -> COMPLETED
```

任意活动态可以转移到 FAILED / CANCELLED / INTERRUPTED，写入 reason、event_id、设备状态、重试次数。状态转移必须持久化，幂等，进程重启后不可凭猜测复原正在进行的测量。

## 4. 计划与数据模型

- `test_run_id`：上位机一次运行唯一 UUID；
- `device_serial`：ADB 设备选择标识，入报告可脱敏；
- `logger_session_id`：Android GNSS 采集会话 UUID；
- `scene_id`：稳定场景类型，如 `video`、`game_heavy`、`navigation_manual`；
- `scene_instance_id`：场景每次执行的唯一 UUID，可区分循环与重试；
- `event_id`：每个事件唯一 ID，防重放/幂等；
- `schema_version` / `protocol_version`：跨版本兼容校验。

测试计划示例（仅拟议格式）：

```json
{
  "schema_version": 1,
  "test_run_id": "generated-at-runtime",
  "device_serial": "selected-at-runtime",
  "clock_sync": {"samples": 9, "max_uncertainty_ms": 500},
  "logger": {"collect_satellites": true, "collect_raw": true, "collect_nmea": false},
  "scenes": [
    {"scene_id": "baseline", "mode": "auto", "stabilize_seconds": 20, "measure_seconds": 60, "repeat": 1},
    {"scene_id": "video", "mode": "auto", "stabilize_seconds": 20, "measure_seconds": 60, "repeat": 1},
    {"scene_id": "game_heavy", "mode": "auto", "params": {"particles": 250, "cpu_threads": 2}, "stabilize_seconds": 20, "measure_seconds": 60, "repeat": 1},
    {"scene_id": "navigation_manual", "mode": "manual", "stabilize_seconds": 20, "measure_seconds": 60, "repeat": 1}
  ]
}
```

时间和频率范围、采样是否足够必须由数据质量规则判断，不能仅凭计划时间判断成功。

## 5. 数据输出目录

```text
runs/<run-id>/
  test_plan.json
  environment.json
  clock_calibration.json
  events.jsonl
  raw/                 # 从 Android 只读复制的原始文件
  derived/             # 按窗口生成的中间产物
  reports/             # summary.csv / by_satellite.csv / report.xlsx
  diagnostics/         # ADB、异常、校验日志
  manifest.sha256
```

应创建 schema_version 字段，Windows 和 Android 端都保留原始时间戳、会话号及导出来源。原始数据绝不因重新分析而覆盖。

## 6. 可靠性与可用性

- ADB 调用均设置 timeout 与 serial；多设备时必须显式选择。
- 先写本地任务状态，再发可重试命令；按 event_id 做幂等确认。
- 手机断开时暂停推进，已结束的窗口可保留；正在执行的窗口设 INTERRUPTED。
- UI 操作不能阻塞采集或主线程；使用 worker/async 适配。
- 测试结果中记录场景就绪方式：`app_ack`、`manual_confirm`、`unverified`。
- 内置基准 A-B-A 计划模板，支持重复与随机化次序作为后续扩展。
