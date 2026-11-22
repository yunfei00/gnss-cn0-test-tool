# 04 时间同步、测试分窗与 C/N0 算法

## 1. 时间事实与风险

跨设备时间存在：UTC 偏移、时区差异、ADB 请求往返延迟、手机时钟调整、PC 暂停、Android 设备重启。**不能**直接使用 PC 的墙钟时间筛选手机 CSV，也不应通过 root 或更改手机系统时间强制同步。

Android 端 `SystemClock.elapsedRealtimeNanos()` 表示从启动以来的单调时间；GnssLoggerApp CSV 已提供 `elapsed_realtime_nanos` 字段（代码确认具体数据来源与采样点）。**测量边界必须在 Logger APP 内使用同一时基记录**；外部 Scene App 若发 READY 应包含同设备 Android 单调时间并由 Logger 关联保存，或在 Logger 收到事件时单独标记（将传输延迟计入不确定度）。

## 2. 优先方案：手机本地事件标记

- Logger 从任务开始到结束连续写 GNSS CSV。
- 上位机发 `MARK_EVENT(MEASUREMENT_START)`；Logger 落盘 `elapsed_realtime_nanos` 并返回事件 ID 与时间。
- 采样持续时间由 PC 单调计时；结束时发 `MARK_EVENT(MEASUREMENT_END)`，实际截止点以 Logger 记录时间为准。
- 按 `start_elapsed_ns <= sample_elapsed_ns < end_elapsed_ns` 从原始 GNSS 状态记录提取窗口。
- 不以 scene_name 文字或文件名作为唯一时间源，scene_name 只是辅助标签。
- 同一开机周期内无需 PC 与手机 UTC 一致；重启、clock epoch 改变或 `end <= start` 使当次窗口 INVALID。

半自动：人工点击“已进入场景”后，上位机请求 Logger 写 `MANUAL_CONFIRMED`，稳定等待，再发测量 START。人工确认存在主观/操作延迟，必须写 `readiness_source=manual_confirm`，不可夸大精度。

自动：Scene App 确认活动真实 READY 后才开始稳定等待，失败或超时禁止测量。

## 3. 跨端时钟标定（用于审计与诊断）

在建立 ADB 会话后及每个批次开始/结束，执行 9 次（可配置）时钟探测：

- PC 在发送前记录 `pc_monotonic_t1` 和 UTC；
- Android 处理命令时记录 `android_elapsed_ns` 和 UTC；
- PC 收到响应时记录 `pc_monotonic_t3`；
- 估算配对：`pc_mid=(t1+t3)/2`，`offset=android_elapsed - pc_mid`；
- `rtt=t3-t1`，保留 RT T 样本分布，优先低延迟样本，给出误差上界/估算不确定度；
- 多次标定检查长任务漂移，必要时建立分段线性映射，但**只用于跨端展示/分析**，真实窗口仍优先使用 Logger Android 单调时间。

不得声称“毫秒级精准同步”直至真实 RTT/不确定度验收。任何跨端估计都不能替代手机内事件作为测试事实。若超出 `max_uncertainty_ms`，结果标记 TIME_SYNC_LOW_CONFIDENCE，而非静默通过。

v1 上位机使用普通 ADB shell 读取 Android `/proc/uptime` 与 UTC，并在采样前后用
Logger 时钟核对开机纪元及单调区间。样本按 RTT/2 加开机时长显示量化间隔估算
不确定度。设备不支持命令或读数无法对齐时回退到 Logger 时钟探测。
分窗仍直接使用 Logger 的 MEASUREMENT_START/END。

## 4. 时钟异常处理

| 异常 | 处理 |
|---|---|
| 手机 UTC 突然跳变 | 以 Android monotonic 分窗，墙钟显示警告 |
| PC UTC/时区改动 | PC 单调时间计时，UTC 仅展示 |
| 手机重启 | 新 boot epoch，当前场景 INVALID，中断或重启整个会话 |
| APP 进程被杀 | 通过 session_id 和事件连续性判断是否缺失 |
| START 或 END 事件丢失 | 窗口 INVALID，禁止以计划时间代填 |
| ADB 断连 | 停止推进，标记 INTERRUPTED，保留可回收文件 |
| 数据 CSV 部分写入 | 哈希/结构/行数校验，报告不完整 |

## 5. GPS L1/L5 分类

仅分析 `constellation_name == GPS`（或匹配 Android GPS 星座常量）；频段由 `carrier_frequency_hz` 决定：

- GPS L1 中心频率 `1575_420_000 Hz`
- GPS L5 中心频率 `1176_450_000 Hz`

**容差必须配置与验证**，不能把无频率记录推断成 L1；容差之外记为 UNKNOWN。明确 `cn0_dbhz`（天线端接收估计）与可选 `baseband_cn0_dbhz` 是不同指标，不得混算。以 Status CSV 为 MVP 主数据源，Raw Measurement 仅用于对照与诊断，不自动和 Status 混合。

数据键：`(logger_session_id, scene_instance_id, constellation, svid, band, timestamp/frame)`，保证同一帧重复项处理合理、记录缺失/异常标志。

## 6. 统计方法

单场景某颗卫星某频段：
`mean_cn0(scene,svid,band) = mean(valid cn0_dbhz samples within measurement window)`

A/B 同星同频段：
`delta_cn0(svid,band) = mean_cn0(A,svid,band) - mean_cn0(B,svid,band)`

频段整体：
`delta_band = mean(delta_cn0(svid,band) for matched SVIDs)`

**只对 A/B 共同有有效数据的卫星计算整体差值**，不把缺席卫星当 0，也不把场景 A 的全部卫星均值减场景 B 的不同卫星均值当做正式差值。记录 matched/unmatched SVID 集、每颗卫星样本数、有效帧数、覆盖率、均值、中位数、std、最小/最大值。

推荐两种呈现：`delta A-B`（正值表示 B 更低）与 `delta B-A`（如用户需要方向调整须在报告显式注明）；默认使用前者。

## 7. 样本质量门禁（初始建议，可配置且要经真机校准）

- 每个窗口至少 1 个有效同频段样本才可展示观察值；正式判定需要最低样本数和覆盖率阈值，首版**默认门槛待实测设定**，不使用魔法常数伪装。
- 数据必须处于有效边界内，有可信 session_id/clock epoch。
- GNSS 回调间隔不保证正好每秒一次；按实际时间计算覆盖率，而非按“60 行”推断。
- 无 L5 时输出 `NO_L5_DATA`；无共同 SVID 输出 `NO_MATCHED_SATELLITES`。
- 记录卫星数、used_in_fix 率、场景名称、基准测量时间，防止业务干扰与卫星环境变化混淆。
- L1/L5 分开呈现，缺频率 UNKNOWN 不归类。

## 8. 实验设计与可重复性

使用交错基准：`A1 → B → A2 → C → A3 → D → A4`。单次演示结果仅是观察，不证明 APP 的因果影响。控制手机位置/朝向/温度/充电/屏幕亮度/网络状态，记录模拟器配置（若使用），多轮重复并检查基准漂移。

## 9. 测试集合

- PC 比手机 UTC 快/慢 5 分钟、时区不同，但 monotonic 分窗结果相同；
- Android 记录墙钟跳变，窗口不受影响；
- 无 L5/无 carrier frequency/同一卫星多个频段/多星座同 SVID；
- 相同 SVID 不同场景不重合，必须输出无匹配数据；
- 边界恰好等于 START 或 END 时只纳入 START；
- 中间断采、非连续帧、重复帧、异常 cn0、坏 CSV；
- 手机重启后旧事件与新 GNSS 日志不得混合；
- 根据固定小样本手算结果验证均值、差值方向与 report.xlsx。
