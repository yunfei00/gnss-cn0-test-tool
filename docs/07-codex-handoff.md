# 07 Codex 全量交接指令

## 使用方式

将下面的指令复制给具备这三个仓库读写权限的 Codex。Codex 应按阶段在三个仓库分别开 PR、修复 CI 并记录进度。**本仓库的文档已完成初始化；这条指令不是已执行的承诺。**

## 可复制指令

```text
你现在负责 GNSS C/N0 测试平台三个 GitHub 仓库的完整闭环开发：
1) yunfei00/gnss-cn0-test-tool（Windows 上位机主仓库）
2) yunfei00/GnssLoggerApp（Android GNSS 采集 App）
3) yunfei00/AutoTestSceneApp（Android 业务场景 App）

先读取 gnss-cn0-test-tool 的 README.md、AGENTS.md、docs/01~08。
严格以 docs/03 的“拟议协议”和 docs/08 的“已验证事实”区分现有与待开发能力，禁止凭 README 猜测接口已经存在。

目标：
- 上位机 Python + PySide6，Windows 10/11 x64 可独立运行；通过 USB ADB 选择 Android 手机。
- 一次选择多个业务场景、自动或半自动方式、等待时间/测量时长/循环次数。
- 自动调用 AutoTestSceneApp 场景、确认真实 READY/STOPPED；半自动弹出操作提示，用户确认前绝不开始测量。
- GnssLoggerApp 保持全程连续记录 Android GnssStatus / Raw，提供可靠 MARK_EVENT 和无需 root 的日志导出。
- Android 单调时钟标记有效 MEASUREMENT_START/END；按窗口读取并切割卫星 CSV，不依赖 PC 与 Android UTC 相同。
- GPS L1 1575.42 MHz / L5 1176.45 MHz 按 carrier_frequency_hz 分类；同 GPS SVID+频段求 C/N0 A-B 差值。缺 L5/频率/无共同卫星须明确显示不可计算，不得以零填补。
- 输出原始证据、CSV、Excel、可重算结果和 Windows 安装/便携包。
- 不需要 GSS7000 即可在真实卫星环境完成首个闭环；为未来仪表适配保留接口。

开发方式：
A. 先 P0 盘点各 main 的 SHA，校验 GNSS Logger scene_name/Receiver/CSV 不一致、内部文件导出、Scene App StopSceneReceiver 未把 STOP 可靠传入动态 Activity 等已知缺口；给出每项真实证据。
B. 先冻结版本化协议，再分别在两个 Android 仓库补齐兼容接口与单测，使用最小必要改动复用现有代码。
C. 上位机从离线 fixture/假 ADB 端口起步，完成设备、协议、状态机、日志、时间、分析、GUI、报表、打包，逐步增加真实端到端。
D. 每阶段编写单元/集成测试并记录失败诊断，修复至该仓库必需 CI 全绿后才能合并 PR；禁止为刷绿删测试、降低门槛、伪造结果。
E. 无设备时只声明模拟验证通过并给出真机执行步骤，硬件验收标 BLOCKED，不得称真机 PASS；不能无证据宣称 GSS7000 可控制。
F. 三仓库分别管理 PR 和 CI，维护版本兼容矩阵；更新 docs/05 里程碑表及 docs/08 代码审计记录；报告最终 SHA、PR、CI、运行命令、遗留问题。
G. 不 root、不修改手机系统时间、不把敏感定位日志提交公共仓库。不需要用户反复确认既有需求；按计划独立推进到可完成的最大阶段。

先给出 P0 审计与小步实施计划，再实际修改仓库。所有阶段按文档验收，不得只写计划没有代码。需要真实手机验证或仪表不可得时，明确列出阻断与单条操作命令。
```

## 实施与协作注意

- 三仓库各自独立发布；先合并向后兼容 Android 接口，再让 Windows 端要求更高协议版本。
- 每次 PR 写明：改动范围、真实测试/模拟测试区别、CI 状态、跨仓库影响、回退策略。
- 主仓库计划为长期源事实；发现与代码不符时先修 docs/08，再更新设计/测试。
- 若实际 CI 没有稳定通过，不允许直接合并到 main。
