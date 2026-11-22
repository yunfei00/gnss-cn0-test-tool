# 03 跨仓库接口协议 v1（设计草案）

> 本文为 **TO-BE 协议**，不是当前 APP 已实现 API 清单。任何新增命令在代码落地并真机验收前必须标记为“待实现”。`docs/08-repository-audit.md` 区分已有能力和差距。

## 1. 基本原则

- 上位机持有 test_run_id，Logger 每次连续采集产生 logger_session_id；每场景创建 scene_instance_id。
- Android 端记录事实型状态（STARTING/READY/STOPPING/STOPPED/ERROR）及同一手机单调时钟事件；不得用“ADB 返回码 0”代表场景就绪。
- 请求带 `protocol_version`、`request_id`、`scene_instance_id`；查询或事件回执也包含 request_id，实现重试幂等。
- 所有数字使用明确单位：Android `elapsed_realtime_nanos` 为 ns；UTC `timestamp_ms` 为毫秒；`cn0_dbhz` 为 dB-Hz；`carrier_frequency_hz` 为 Hz。
- 不依赖隐式广播的系统行为；首期使用**显式组件**入口，返回真实状态通过专用导出状态文件或只读查询接口核实。
- 设备来自 ADB 绑定序列号；所有命令加 `adb -s SERIAL`，防误控。
- 协议字段增加只能向后兼容；破坏性变化提升主版本并要求两端能力握手。

## 2. Scene App 计划接口

现有可用入口：`com.yunfei.autotestscene/.SceneEntryActivity`，接收 `--es scene video`、`game_heavy`、`--ei particles 250`、`--ei cpu_threads 2` 等。

```powershell
# 已有入口（执行之前请验证安装包名和设备）
adb -s <SERIAL> shell am start -n com.yunfei.autotestscene/.SceneEntryActivity --es scene video
adb -s <SERIAL> shell am start -n com.yunfei.autotestscene/.SceneEntryActivity --es scene game_heavy --ei particles 250 --ei cpu_threads 2

# 已有 STOP 广播入口，但目前不可据此认定能真正停止：实现与回执待修复
adb -s <SERIAL> shell am broadcast -n com.yunfei.autotestscene/.scene.StopSceneReceiver -a com.yunfei.autotestscene.STOP
```

**新增标准协议**（以下是拟议能力，不能直接复制执行）：

| 命令 | 参数 | 响应/状态 |
|---|---|---|
| SCENE_CAPABILITIES | protocol_version | supported_scene_ids, supported_modes, version |
| SCENE_START | request_id, scene_instance_id, scene_id, params | accepted/rejected; 后续 READY 或 ERROR |
| SCENE_STATUS | scene_instance_id | STARTING/READY/ACTIVE/STOPPING/STOPPED/ERROR、采样时刻 |
| SCENE_STOP | request_id, scene_instance_id | accepted，后续 STOPPED；确保媒体/CPU/相机资源释放 |
| SCENE_HEALTH | 无 | 进程/权限/前台或可观测场景证据 |

建议优先**最小改动复用已有 SceneEntryActivity**，补齐可核实状态和停止可靠性，而不是重新写场景逻辑。对动态注册的未导出 Receiver，外部 ADB 向 manifest Receiver 广播不保证被场景 Activity 接收，必须显式桥接或使用同 APP 内部的停止调度。

各场景可能受权限、ROM、硬件、媒体资源影响；只有 READY 和 STOPPED 经实际检查后才能算能力通过。

## 3. Logger App 计划接口

现有：`GnssCommandReceiver` 的 `ACTION_START_LOGGING`、`ACTION_STOP_LOGGING`；`GnssCollector` 调用 `GnssStatus`/Raw/NMEA；卫星 CSV 有频率、卫星编号和时间信息。**当前 main 未实现 README 宣称的 ACTION_UPDATE_SCENE。**

```powershell
# 已有显式 Receiver 接口
adb -s <SERIAL> shell am broadcast -n com.example.gnsslogger/.GnssCommandReceiver -a com.example.gnsslogger.ACTION_START_LOGGING
adb -s <SERIAL> shell am broadcast -n com.example.gnsslogger/.GnssCommandReceiver -a com.example.gnsslogger.ACTION_STOP_LOGGING
```

拟增加：

| 命令 | 核心参数 | 必须返回/落盘 |
|---|---|---|
| LOGGER_CAPABILITIES | protocol_version | app_version, schema_version, status/raw/nmea 支持 |
| LOGGER_START | test_run_id, request_id, options | logger_session_id, STARTED/ERROR, elapsed_ns |
| LOGGER_STATUS | logger_session_id | 当前状态、文件写入计数、最近 GNSS 回调时间、错误 |
| MARK_EVENT | test_run_id, scene_instance_id, event_id, event_type | logger 处理时刻 `elapsed_realtime_nanos`、UTC、持久化 ack |
| LOGGER_STOP | logger_session_id, request_id | STOPPED 且文件 flush/close 完成 |
| LOGGER_EXPORT | logger_session_id, request_id | 可从普通 ADB 获取的成套文件、manifest/大小/哈希 |
| LOGGER_RESET_ERROR | 可选 | 清除非破坏性错误状态 |

`MARK_EVENT` 类型至少包括 `SCENE_READY`、`MANUAL_CONFIRMED`、`STABILIZATION_START`、`MEASUREMENT_START`、`MEASUREMENT_END`、`SCENE_STOPPED`、`ERROR`。需记录事件到达 Android 端的**实际时刻**，而非 Windows 发送前预估时间。

推荐事件持久化 JSONL：

```json
{
  "protocol_version": 1,
  "test_run_id": "test-run-uuid",
  "logger_session_id": "logger-session-uuid",
  "scene_instance_id": "scene-instance-uuid",
  "event_id": "event-uuid",
  "event_type": "MEASUREMENT_START",
  "timestamp_ms": 1795334400000,
  "elapsed_realtime_nanos": 987654321000,
  "source": "logger_receiver",
  "ack": "persisted"
}
```

上述时间数字为**示例数据**，不能用于报告。

## 4. 文件导出与可访问性

代码核查发现 Logger 的 `LogFileManager` 使用 `context.filesDir/gnss`；普通非 root ADB **不能直接假设**可以 `adb pull` 私有目录。实现时必须选定一种确实可行的方式：

1. 优先：APP 提供受控导出到其可获取的公共/共享目录并返回路径，PC `adb pull`；
2. 或：使用受控 ContentProvider / 文件流（需验证 ADB 访问方案）；
3. 仅在 debuggable 构建下允许 `run-as` 进行诊断，不能作为发行版必备导出渠道。

避免将个人轨迹文件永久暴露在公共目录；测试结束后提供可控清理与校验。上传数据到任何服务都不是 MVP 需求。

建议导出清单：

```json
{
  "schema_version": 1,
  "logger_session_id": "uuid",
  "files": [
    {"name": "satellites.csv", "size": 12345, "sha256": "computed-at-export"},
    {"name": "events.jsonl", "size": 1234, "sha256": "computed-at-export"}
  ],
  "clock_epoch": {"android_boot_id": "if-available"}
}
```

## 5. 兼容矩阵与版本协商

- PC 开始任务前读取 Logger/Scene 版本和能力，拒绝缺少必需 MARK_EVENT、LOGGER_EXPORT、SCENE_STATUS 等能力的自动完整运行；
- 半自动场景可不依赖 SCENE_START，但必须具备 LOGGER_START、MARK_EVENT、EXPORT；
- 协议未知字段忽略，必需字段缺少即报错，保留原始诊断；
- APP 版本与 git commit（可获取时）写入环境清单；
- 两 APP 独立升级，先发布兼容接口，再启用上位机新功能。

## 6. 安全要求

公开的广播 Receiver 可能被其他 Android APP 调用；设计受控导出/状态接口时应限制命令范围、严格校验参数，避免任意文件读取或命令执行。能通过 ADB 启动并不等于接口具备身份认证；测试机优先使用 USB 可信连接，后续按需引入配对令牌或签名权限。所有对外接口必须经过安全审查。
