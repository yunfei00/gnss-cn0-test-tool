# GNSS C/N0 自动化测试平台

面向 Android 手机的 **Windows GNSS C/N0 场景测试上位机**。平台通过 USB ADB 编排手机业务场景与 GNSS 日志采集，按**实际有效测试窗口**分析 GPS L1/L5 的 C/N0 差值，支持全自动与半自动混合测试，并预留 GSS7000 模拟器对接。

> **状态：规划中。** 本仓库当前为新建上位机仓库；本文所述软件功能是待实现目标，不代表已经完成或通过真机测试。

## 三仓库协同

| 仓库 | 定位 | 核心职责 |
|---|---|---|
| [gnss-cn0-test-tool](https://github.com/yunfei00/gnss-cn0-test-tool) | Windows 上位机（本仓库） | 场景计划、ADB 调度、任务状态机、同步与数据回收、日志分窗、C/N0 分析、图表与 Excel、Windows 打包 |
| [GnssLoggerApp](https://github.com/yunfei00/GnssLoggerApp) | Android GNSS 采集端 | GnssStatus、Raw Measurements、NMEA，持续记录与事件标记、状态查询和数据导出 |
| [AutoTestSceneApp](https://github.com/yunfei00/AutoTestSceneApp) | Android 场景执行端 | 视频、音乐、游戏负载、相机等内置场景的启动、就绪状态、停止与资源释放 |

**约束：** 保持三仓库独立；上位机是唯一测试编排者；日志在整次测试中持续采集；自动/半自动只影响场景进入方式，**数据截取与分析完全一致**。

## MVP 最小闭环

1. ADB 检测手机与两个 APP（可用真实卫星，不要求 GSS7000）。
2. 启动 GNSS 连续采集，按需校验 L1/L5 能力。
3. 上位机选择“空闲基准 → 视频自动 → 游戏自动 → 第三方导航半自动”，设置稳定等待、采样时长和次数。
4. 自动场景收到可验证的 READY；半自动场景等待人工确认，然后进入统一稳定等待。
5. 在 **GNSS Logger 内以 Android 单调时钟**记录 MEASUREMENT_START / END，按事件窗口筛选卫星日志。
6. 输出逐 GPS 卫星/频段的均值、差值、有效样本及数据质量判定，并生成 Excel/CSV；保留原始日志、事件与环境元数据。
7. 异常及测试暂停/停止均有明确状态；不能获取真实数据时禁止伪造“成功”。

## 开发文档

- [01 - 产品需求与范围](docs/01-requirements.md)
- [02 - 系统架构、状态机与数据流](docs/02-architecture.md)
- [03 - 跨仓库接口协议（设计草案）](docs/03-interface-protocol.md)
- [04 - 时间同步、测试分窗与 C/N0 算法](docs/04-time-cn0.md)
- [05 - 分阶段开发计划](docs/05-development-plan.md)
- [06 - 自动化验收与 CI](docs/06-acceptance.md)
- [07 - Codex 执行交接说明](docs/07-codex-handoff.md)
- [08 - 现有代码核查与缺口](docs/08-repository-audit.md)
- [AGENTS.md](AGENTS.md)

## 建议技术栈

- Windows 10/11 x64；Python 3.12、PySide6、pytest、ruff、openpyxl、SQLite、PyInstaller；使用 uv 管理环境（在实施时锁定版本）。
- Android 两端继续沿用现有 Kotlin/Gradle 项目；不新建第三个 Android APP。
- USB ADB 优先，首期采用**命令 + 状态确认 + 完整文件导出**，实时数据流后续独立迭代。
- GitHub Actions 分别在三个仓库持续验证；真实设备冒烟测试单独标识、不得以 mock 代替。

## 重要现状提示

2026-11-22 代码核对发现：GnssLoggerApp README 声称支持 ACTION_UPDATE_SCENE，但 main 的 AppActions / Receiver 未实现，卫星 CSV 也缺少 scene_name；AutoTestSceneApp 的 SceneEntryActivity 已可按 scene 启动，但 STOP 的 manifest Receiver 目前仅记录消息，且 duration 没有执行超时。详见 [代码审计](docs/08-repository-audit.md)。**不要把文档声明当成已实现接口。**

## 安全与测试环境

- GNSS 数据可能包含精确位置；默认仅存本机，测试产物和敏感日志不提交公共仓库。
- 真机测试采用正常卫星接收或合规屏蔽/有线耦合的模拟器环境；禁止未屏蔽地辐射 GNSS 模拟信号。
- 不要求 root、不修改手机系统时间；通过单调时钟映射解决跨端时间偏差。
