# 07 Codex 一次性完成 P0–P5 的执行指令

> 2026-11-22 更新。**用户已确认：本地 ADB 可以直接控制手机。** 请把这视为已知前提，但必须在 Codex 的实际执行环境重新验证 ADB 设备可见、授权、权限、APK 以及 GNSS 数据，不得仅凭用户确认即判定真机测试已通过。

## 直接执行指令（复制下面整个代码块交给 Codex）

```text
你现在是 GNSS C/N0 自动化测试平台的主开发者、联调工程师、测试负责人。不要仅输出计划，必须直接开始实施并连续推进 P0、P1、P2、P3、P4、P5，直到形成真实可验收的独立闭环。

用户已经确认：当前开发电脑的 ADB 可以直接控制 Android 手机。这是本次任务的关键新信息。不要重新把“能否用 ADB 控制手机”作为需要向用户询问的前置问题。你首先自行运行 adb devices -l 确认，在有权限的前提下自行部署/运行/测试两个 APK，并主动读取实际 GNSS 日志。若你运行的 Codex 环境不是那台连接手机的电脑，必须先检测 adb 是否可见及是否有授权设备；若无法接触真实手机，继续完成所有可离线实施的 P0-P5 代码与自动化测试，但将真机验收标注为 BLOCKED，不得伪造通过。

三个仓库：
- yunfei00/gnss-cn0-test-tool ：Windows 上位机（Python + PySide6）
- yunfei00/GnssLoggerApp ：GNSS 采集 Android APP（Kotlin）
- yunfei00/AutoTestSceneApp ：业务场景 Android APP（Kotlin）

第一步完整阅读上位机仓库的 README.md、AGENTS.md、docs/01~08，并核对三个仓库的 main 最新源码、SHA、已有 CI。文档中“拟议协议”并不等于已经实现。不要仅根据 README 认定已有广播接口生效。

交付目标：在 Windows 上位机一次性选择测试场景（包括自动和半自动），上位机可靠控制手机、后台 GNSS 连续采集、可靠记录时间窗口、取回实际日志、分析 GPS L1/L5 C/N0 场景差值、显示趋势和报告，并能够生成 Windows 独立运行的发布包。无需接 GSS7000 即可借助手机真实 GNSS 信号完成 MVP；保留后续 GSS7000 扩展点。

执行要求（不要在各阶段完成后停下来等待我再发下一条指令）：

P0：执行真实 ADB 预检（adb devices -l；设备 serial/Android API/定位授权/两个包的版本；启动/停止 GNSS Logger；启动视频、游戏场景；检查状态/日志/导出/频段）。确认 GnssLoggerApp 的 scene_name、MARK_EVENT、CSV、私有目录导出能力与源码；核对 AutoTestSceneApp 的 StopSceneReceiver、动态广播和资源清理。把事实、问题、设备结果写回 docs/08。只有真实证据才算真机 PASS。

P1：在 GnssLoggerApp 实现可靠 LOGGER_START、LOGGER_STATUS、MARK_EVENT、LOGGER_STOP、LOGGER_EXPORT、协议能力查询（采用经验证的可调用接口）；记录同一 Android 单调时钟基准的事件与 GNSS 数据；保留 session ID 和 boot epoch，普通非 root ADB 可导出完整 CSV/事件与 SHA-256 清单。兼容已有行为；修正 README/CSV 字段不一致。
在 AutoTestSceneApp 实现可确认 START/READY/STATUS/STOP/STOPPED/ERROR，修好 STOP 到各场景 Activity 的传递和资源释放；不能把命令提交成功当成 READY。验证至少视频、音乐、游戏高负载等实际已有场景；未实现的场景不能报成功。

P2：建立上位机 Python + uv + PySide6 项目；设备检测与选择、ADB 客户端、Android APP 能力与协议校验、自动导出、CSV/schema/时钟字段解析、SQLite、运行数据目录、离线日志回放与测试。

P3：实现持久化状态机和“一次选好场景”的测试方案；自动模式调用场景 APP 并确认就绪，半自动模式弹出操作提示并等待用户确认；稳定等待 20s（可配置）后写手机端 MEASUREMENT_START，再测试 60s（可配置），写 MEASUREMENT_END，随后停止场景并确认。整个测试方案中 Logger 持续采集；中断、暂停、取消、重试、断连与恢复都要有明确状态。绝不在半自动场景未确认时自动开测。真机至少完成自动视频 + 半自动场景的端到端测量；半自动确实需要操作员时允许并清楚提示用户，仅该人工动作需要配合。

P4：实现跨端时钟探测与审计，但按 Logger 端 elapsedRealtimeNanos 的 [START, END) 窗口读取 GNSS 日志；处理 UTC 偏移、跨时区、重启、事件丢失、重复帧；GPS L1=1575.42MHz、L5=1176.45MHz 按载波频率归类，同 GPS SVID+频段才计算场景 A-B 差值。无 L5、无频率、无共同卫星、无有效窗口时返回明确错误/无数据，不得写成零。使用确定性样本、真实文件和 A-B-A 漂移检查自测。

P5：完成 Windows 桌面 UI（设备、测试计划、场景配置、手动确认、实时运行状态、历史、结果、曲线）、Excel/CSV 输出及只读原始证据、配置与用户指南、Windows x64 独立安装包/便携包构建。在无预装 Python 的 Windows 环境验证运行（若环境不具备，标 NOT_RUN 并提供可复现步骤，不可虚称完成）。

验证与交付硬约束：
1. **每阶段实现功能 + 单元/集成测试 + 自检/证据**，不是单纯输出规划。能自动执行的验证全部自动执行，包括手机 ADB 控制、安卓 APK 安装与日志导出、固定 CSV 精确复算。
2. 修改后执行本地 lint/测试/构建，创建对应仓库分支与 PR，持续检查 GitHub Actions；失败则定位、修复、重新运行，直到所有必需 CI 通过才合并 main。不可通过删除测试、跳过检查或改写真实结果刷绿。
3. 三仓库改动需要一致的协议版本、能力握手与兼容矩阵；先合并向后兼容的 Android 端接口，再推进上位机依赖。记录每个 PR/commit/CI 证据。
4. 禁止将 fixtures/mocks、ADB 命令成功返回、日志文件存在、绿色 CI 等同真实手机完整闭环 PASS。每条验收必须明确 PASS/FAIL/BLOCKED/NOT_RUN 并有具体证据。凡没有设备或无法进行的测试，诚实注明。
5. 不 root 手机、不修改手机系统时钟、不恢复出厂设置/清空用户数据；只操作这次测试相关的已授权 APP，不上传真实位置/敏感日志到公共仓库；GNSS 模拟器仅在合规屏蔽/有线环境中使用。
6. 测试结果必须可追溯：test_run_id、session_id、scene_instance_id、event_id、Android 单调时间、PC/Android UTC、场景准备与采样边界、版本、原始文件哈希。
7. 一次提交所有可完成工作，不要完成 P0 就停在“下一步建议 P1”。自行调试、修改和回归，持续推进到 P5。只有真正无法自行解决的设备、权限、人工场景操作、账户授权等外部阻断才提出**具体最小协助事项**。
8. 最终输出：三个仓库的 PR/合并 SHA/CI 结果、P0-P5 各阶段验收报告、真实设备记录、可运行 GUI/安装包路径或发布链接、执行/安装命令、未实现项及 BLOCKED 原因；同步更新 docs/05、docs/06、docs/08。

现在直接开始执行 P0 真实接口检查，然后持续完成 P1-P5 和全部能执行的验收。不要将上面的任务仅整理成开发计划，不需要我分阶段重复下达指令。
```

## 交付与真实性提醒

- 本文件已授权 Codex 在有仓库/设备访问权限的执行环境内**连续推进 P0-P5**，不是说本聊天已经启动 Codex 或完成开发。
- 自动场景可无人值守，半自动场景需要用户在手机上操作并确认；不要假装完成需要实际人工操作的步骤。
- 如果 Codex 所在运行环境不能访问手机或 Windows GUI，代码开发、离线回归、CI 仍应持续；真实设备/安装包验收保持 BLOCKED/NOT_RUN。
- 项目仍按 AGENTS.md 的分支、PR 和 CI 绿灯规则实施；不以“全部交给 Codex”为理由豁免验收。

---

## 原交接指导（保留参考）


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
