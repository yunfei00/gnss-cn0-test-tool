# 05 分阶段开发计划（Codex 交付路线图）

> 更新：2026-11-22。本文件是待实施计划，并不表示功能已经完成。目标是三仓库协同实现 Windows 独立闭环：选择场景 → 自动/人工切换 → 采集 → 按实际时间分窗 → C/N0 分析 → 报告。

## 0. 执行授权与环境已知信息（2026-11-22）

用户已确认：**开发电脑上 ADB 可以直接控制 Android 手机**。因此 Codex 在具有该设备访问权限的执行环境中，应直接连续实施并自行验证 **P0–P5**，而不是每个阶段停止等待新的指令。首要任务是在自身环境执行 `adb devices -l` 验证实际可访问设备、授权、应用权限和 GNSS 信号；用户确认 ADB 可控不等于全部设备场景和 GNSS 日志已经验证。

以 [docs/07-codex-handoff.md](07-codex-handoff.md) 顶部“直接执行指令”为启动入口：可以并行推进离线工作和两个 Android APP，但跨仓库契约必须兼容，阶段验收需要独立证据。每个修改仓库的所有必需 CI 绿灯后才能合并。除真正需要用户手动操作的半自动场景、权限/设备不可访问等实质阻断外，不要反复要求用户逐阶段批准。

若 Codex 运行环境不能访问这部手机，则先完成所有可自主开发和自动测试的部分，明确把真机项标为 BLOCKED；**不得声称已经替用户启动了 Codex 或完成了真实闭环**。

## 1. 最小可交付闭环 MVP

Windows 选定 Android 设备，创建一次测试方案（基准空闲、自动视频、自动高负载游戏、手动导航），GNSS Logger 持续记录，场景每次执行均以 Android 单调时钟写入 MEASUREMENT_START/END 事件；完成后无需 root 导出真实卫星 CSV、事件，按同 GPS 卫星/频段计算 L1/L5 C/N0 差值，并生成 Excel。若 L5 缺失要注明无数据。

**本阶段无需 GSS7000。** 信号源先使用真实卫星，GSS7000 作为未来扩展，不得阻碍 MVP 发布。

## 2. 路线与依赖

| 阶段 | 仓库 | 内容 | 完成门槛 |
|---|---|---|---|
| P0 现状与设备验证 | 三个 | 代码/协议差异审计；ADB/权限/手机 GNSS L1/L5 和内部目录导出验证；真机样本 | 形成可复现诊断、真实样本和缺口表；无手机标 BLOCKED |
| P1-A 场景 APP | AutoTestSceneApp | START/STATUS/STOP/READY/STOPPED、超时、资源清理、能力列表与参数验证 | 视频、音乐、游戏高负载、相机逐项启动/停止并留下可核实状态 |
| P1-B Logger APP | GnssLoggerApp | 补齐场景更新、MARK_EVENT、STATUS、持续采集、同一单调时基、普通 ADB 可取的导出与校验清单 | 一段 GNSS 日志与 START/END 事件正确关联、导出完整 |
| P2 上位机框架 | gnss-cn0-test-tool | Python/uv、PySide6、ADB 设备发现、协议客户端、CSV 解析、数据模型、SQLite、离线回放 | 真实与 fixture CSV 可导入、设备连接错误可诊断 |
| P3 测试编排 | 上位机+两个 APP | 自动与半自动统一状态机；稳定等待、计时、暂停/停止/失败恢复、跨端事件 | 真机视频自动+导航半自动完成一个完整测试闭环 |
| P4 时间与统计 | 上位机 | 单调时钟分窗、RTT 标定、频率分类、同 SVID 匹配、数据质量与报告计算 | 固定样本的精确结果、时间错位与异常样本回归全通过 |
| P5 产品交付 | 上位机 | 配置/任务/结果 GUI，趋势、Excel、CSV、操作说明、Windows 打包 | 干净 Win10/11 x64 无预装 Python 可启动与导出真机报告 |
| P6 扩展 | 三个 | USB 实时流、第三方场景适配、GSS7000 仪表接口、多设备 | 独立评审；非 MVP 阻断 |

P1-A 和 P1-B 可以并行，但先冻结 docs/03 接口；P2 的离线算法可与 P1 并行，真机闭环必须等跨仓库接口完成。P0 的真实设备验证如无设备可以先 BLOCKED，允许做不依赖设备的开发，不能宣称 P0 实机 PASS。

## 3. Android 场景 APP 任务细化

- 保留 SceneIds 和 SceneEntryActivity 已有入口；已有 video、music、game_heavy 等场景尽量复用。
- 修复当前 manifest StopSceneReceiver 只记录日志而无法确认停止的问题：协调 Activity 内部动态注册的 Stop Receiver，并真实结束媒体播放/CPU worker/相机/手电筒/录音/振动。
- 增加 SCENE_STATUS 和可核实的 READY/STOPPED 事件，START 接受不等于 READY。
- `duration` 在现有 SceneRouter 中被忽略，测试时长由上位机掌控，不假设 APP 会超时结束。
- 未实现的 game_light/game_2d 返回 UNSUPPORTED；第三方 APP 场景不在本 APP 中强制实现。
- 权限不足、资源忙、活动前台失败必须暴露真实错误。

## 4. Android Logger APP 任务细化

- 保留现有 START/STOP 广播，补齐真实场景更新接口、scene_name CSV 列或使用同一时间轴的事件表标注。
- 增加 MARK_EVENT：event_id、test_run_id、scene_instance_id、type、Android elapsedRealtimeNanos、UTC、session_id；事件写盘后返回 ACK。
- 原始 satellites/raw/nmea/事件一同导出；选择普通非 root ADB 可获取方案，不能依赖直接 pull 私有 filesDir；提供哈希/文件大小/行数校验。
- 记录 Logger 健康状态、最后 GNSS 回调时间、会话状态、设备时基变化；支持断线后完整导出。
- 添加兼容版本/能力查询；README 必须和实际代码保持一致。
- 注重权限、安全与地理位置隐私；不要默认上传日志。

## 5. Windows 上位机实现

建议包结构：

```text
src/gnss_cn0_tool/
  ui/                   # PySide6: device, plan, run, result
  core/                 # run state machine, scheduler, models
  devices/              # adb, logger client, scene client
  scenarios/            # auto/manual adapters and registry
  sync/                 # device monotonic markers, calibration
  ingestion/            # CSV reader, schema, export integrity
  analysis/             # frequency, windows, cn0, quality
  storage/              # SQLite and immutable raw files
  reporting/            # CSV, Excel and audit manifest
tests/unit/
tests/integration/
tests/fixtures/
.github/workflows/
```

- ADB 命令必须指定 serial、超时、重试和诊断；多设备不能默认控制任意一台。
- 自动场景必须确认 READY；半自动必须等待人工“已进入场景”，未确认不得进入 STABILIZING。
- 采样窗口起止以 Logger 端手机单调时钟事件为准，PC 用单调时间执行目标时长；不能以计划时间替代实际事件。
- 场景停止要验证 STOPPED 和资源释放；取消/断连保留原始采集资料和阶段状态。
- 原始日志只读保存，重新分析生成衍生文件；报告明确计算公式与窗口质量。
- 接口可被 Fake ADB/固定 CSV 替换，允许无手机执行所有可单测逻辑。

## 6. 实验与数据规则

- 默认演示计划 A1 基准 → B 视频 → A2 基准 → C 游戏 → A3 基准 → D 手动导航 → A4 基准，以减少卫星随时间变化的混杂。
- 初始等待 20 秒、采样 60 秒是可配置实验默认值，不是经过验证的固定阈值。
- C/N0 主计算来自 GnssStatus 的 cn0_dbhz，GPS L1/L5 根据 carrier_frequency_hz 分类，不允许缺失频率硬判为 L1。
- 差值优先相同 SVID / 相同 band 的均值 A-B，正值表示 B 更低；GPS L1、L5 分开报告。
- 无 L5、无交集、无有效窗口、时钟不可靠时报告清楚原因，不能制造数值。

## 7. 产品交付与测试

- UI：设备、版本/权限检查、计划管理、自动/手动模式选择、进度、人工确认、错误回溯、报告与历史查询。
- Excel 至少包含摘要、场景窗口、逐卫星/频段、时钟/质量、原始文件索引；CSV 同步输出。
- Windows x64 安装/便携包，打包时处理 ADB 的安装与授权说明；用户不需要安装 Python。
- 三仓库分别运行 lint/单测/集成/构建 CI；必要的真机验证另列证据，不能用 CI 或模拟样本代替。
- PR 仅在必需 CI 全绿后合并 main，记录对应 SHA 与跨仓库协议兼容版本。不得为了通过 CI 删测试或硬编码结果。

## 8. 实施与验收状态

| 阶段 | 状态 |
|---|---|
| 规划文档 | DOCUMENTED |
| P0 真实设备验证 | ADB、权限、旧版采集和 L1 载波有真机证据。旧版 STOP 失败已复现。有效 L5 尚未验收 |
| P1-A 场景接口 | Android CI 通过并合并。视频、音乐、游戏、前摄和后摄 READY/STOPPED 实测通过 |
| P1-B Logger 接口 | Android CI 通过并合并。真机状态采集、幂等事件与脱敏状态导出通过。Raw/NMEA 联采实测超时，导出未验收 |
| P2 框架 | ADB、能力握手、SQLite、校验和离线回放已实现。真机状态数据回收及导入通过 |
| P3 编排 | 自动基准、视频和游戏端到端通过。真机未确认路径按时跳过，重试经操作员确认完成自动视频和导航联合测量 |
| P4 时间/算法 | 固定样本、边界、重启校验和 Excel 复算通过。低延迟标定复验通过 500 ms 门限。真机有效 GPS C/N0 与 L5 差值 BLOCKED |
| P5 GUI/交付 | Windows CI 及工作机安装冒烟通过。无 Python 的干净 Windows 10/11 客户端真机验收 NOT_RUN |
| P6 扩展 | BACKLOG |

交付每个阶段后更新状态与具体可访问的 CI/PR/测试证据链接。源码中已有部分功能不代表该阶段通过验收。

Android 接口先于上位机合并。Logger 合并 SHA 为
`b70d127f63bcf75976a161b05d6ec41549e51205`，
Scene 为 `91f81111ddd1bdfa0219952e6136920417dc97fe`。
各阶段证据见 [验收记录](06-acceptance.md)。
