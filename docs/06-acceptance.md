# 06 验收标准、测试矩阵与 CI

## 2026-11-22 实施证据

| 范围 | 结果 | 可复核证据 |
|---|---|---|
| AC-01 设备发现/选择 | PASS | fixture 覆盖多设备和授权错误，真机有一台已授权 API 35 设备 |
| AC-02、AC-15、AC-17 连续采集、导出、幂等 | PASS（卫星状态和事件） | 真机 Logger 两次发送同一事件 ID 返回相同回执。端到端 run `a18d0e94-a9f9-4529-8298-7840c08d0afc` 保存并核验 4,699 行卫星 CSV（含表头）和 14 条事件，覆盖三个有效边界对 |
| AC-03、AC-04 自动视频和游戏 | PASS（调度和资源状态） | 真机分别收到 READY、START/END、STOPPED。音乐和双摄像头另做 READY/STOPPED 冒烟，前摄首次查询超时后复验通过 |
| AC-05、AC-23 半自动门禁 | PASS（自动化和真机） | 首轮未确认时 180 秒后跳过，未写导航 START/END。第二轮 `d7bf17e6-29b3-486a-924d-0f398036f9b4` 由操作员实际确认进入和退出导航。视频与导航共用一个 Logger 会话，13,042 行卫星 CSV（含表头）及 9 条事件核验通过 |
| AC-06–09 时间与异常、AC-10–14 频段/CSV | PASS（固定样本） | 边界包含 START 并排除 END。UTC 跳变、会话纪元错误、重复帧、坏文件和缺少 L5 回归通过 |
| AC-16、AC-18、AC-21 断线、STOP 与 READY | PASS（自动化/已覆盖路径） | 断线不补写结束边界。旧版 STOP 失败通过真机复现。新版真机确认视频和游戏停止 |
| AC-20、AC-22 报告与 A-B-A | PASS（固定样本） | GPS G03 L1，A=43、B=40，差值 +3 dB，A-B-A 基准漂移 +2 dB |
| 真机 C/N0 和时间质量 | 未通过质量门限 | 自动测试 GPS 状态中 1,824 条 L1 载波记录，L5 为 0，GPS C/N0 无有效正值。自动运行最低不确定度为 601.609 ms / 598.402 ms，视频和导航联合运行约 616.7 ms / 619.2 ms，均超过默认 500 ms，报告标记 TIME_SYNC_LOW_CONFIDENCE |
| AC-19 Windows 包 | PASS（CI 与工作机安装冒烟） | Windows CI 构建 x64 安装包与便携包。工作机以仅含 System32 的 PATH 启动安装版和便携版，并用安装版复算真机 Excel，原始 CSV 的哈希前后一致。干净 Win10/11 客户端真机流程 NOT_RUN |
| AC-24 隐私 | PASS（此次运行范围） | 真机模式关闭 Raw 和 NMEA，卫星 CSV 经纬度列为空。真实原始文件仅留本机 |

Android 构建证据为 [Logger PR CI](https://github.com/yunfei00/GnssLoggerApp/actions/runs/45008419883)、
[Logger 分支 CI](https://github.com/yunfei00/GnssLoggerApp/actions/runs/45008415774) 和
[Scene PR CI](https://github.com/yunfei00/AutoTestSceneApp/actions/runs/45008416990)。
两端合并后 [Logger main CI](https://github.com/yunfei00/GnssLoggerApp/actions/runs/45008875827)、
[Scene main CI](https://github.com/yunfei00/AutoTestSceneApp/actions/runs/45008886429) 通过。
Windows 验证与安装产物见 [Windows PR](https://github.com/yunfei00/gnss-cn0-test-tool/pull/1)。

旧版 GNSS CSV 传至工作机的请求被自动审批拒绝。P0 通过手机内统计复核旧样本。
新版设备闭环仅提取位置列留空的状态和事件。Raw/NMEA 真实数据回收 NOT_RUN。

## 测试层次

1. **单元测试（无手机）**：频段分类、单调时钟边界、卫星匹配、状态机、CSV schema、幂等、Excel 计算。
2. **组件集成（无手机）**：Fake ADB / Fake Logger / Fake Scene + 固定 CSV，注入失败、重试、断线和取消。
3. **Android CI**：两个仓库分别运行 lint、unit tests、assembleDebug；适用时加入 instrumentation tests。
4. **真实设备验证**：一台 Android、正常定位权限、两个 APK；在真实 GNSS 环境执行自动视频/游戏 + 半自动导航。
5. **Windows 交付验收**：无 Python 的 Win10/11 x64 环境验证便携版/安装版及导出。
6. **GSS7000 仪表**：P6 单独验收，非 MVP 前置。

## 必测用例

| ID | 主题 | 预期 |
|---|---|---|
| AC-01 | ADB 无设备/未授权/多设备 | 正确报错或选择，绝不误控 |
| AC-02 | 连续 GNSS 采集 | session_id 连续，场景切换不清空数据 |
| AC-03 | 自动视频 | START→READY→窗口起止→STOPPED；留证据 |
| AC-04 | 高负载游戏 | 停止后 CPU/音视频/振动等资源真正释放 |
| AC-05 | 半自动导航 | 用户确认前不计时，允许超时/取消/跳过 |
| AC-06 | START/END 边界 | 包含 START，排除 END，窗口只用 Android 单调时间 |
| AC-07 | PC 与手机 UTC 差 5 分钟 | 分窗分析结果一致 |
| AC-08 | 手机 UTC 跳变 | monotonic 分窗不受影响，显示墙钟警告 |
| AC-09 | 手机重启 | epoch 改变，当前窗口 INVALID/INTERRUPTED |
| AC-10 | 无 L5 | NO_L5_DATA，而不是 0 dB |
| AC-11 | 两场景卫星集合不一致 | 仅相同 GPS SVID/频段计算差值 |
| AC-12 | 同一卫星 L1+L5 | 正确分开 |
| AC-13 | 其他星座同 SVID | 不误归类 GPS |
| AC-14 | CSV 缺列或破损 | 明确错误并保留原文件 |
| AC-15 | 导出完整性 | 文件大小、SHA-256、事件对校验 |
| AC-16 | ADB 中途掉线 | 任务中断，已采集日志可回收 |
| AC-17 | 重复 START/STOP | 事件幂等，不生成重复窗口 |
| AC-18 | 发送 STOP 但场景未退出 | 报失败，不得仅因广播返回成功而通过 |
| AC-19 | 干净 Windows 环境 | 安装版能运行，无预装 Python |
| AC-20 | Excel 结果 | 与固定 CSV 手算完全一致 |
| AC-21 | READY 失败 | 不开始正式采样 |
| AC-22 | A-B-A 对比 | 保存两次基准，明确漂移 |
| AC-23 | 未人工确认 | 半自动不会自动测量 |
| AC-24 | 隐私 | 原始位置日志不被自动提交公共 GitHub |

## 样本验证

构造 GPS G03 L1 在 A 场景取值 42、44，B 场景取值 39、41，预期均值 43 / 40 dB-Hz，A-B=+3 dB。B 独有的 G12 不参与 matched SVID 汇总；其他星座 G03 不能视作 GPS G03。加入缺频率、L5 缺失、重复帧、窗口边界、无共同卫星与坏 CSV。

## CI 门禁

Windows 初始化项目后锁定依赖；建议命令（初始化前不是现成可运行命令）：

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run pytest -q
uv run python -m compileall -q src
```

Android 在各自仓库执行（Windows 使用 gradlew.bat）：

```bash
./gradlew lintDebug testDebugUnitTest assembleDebug
```

- 改动哪个仓库就检验哪个仓库；跨仓库协议变更需要对应兼容性测试。
- 所有必需 CI 绿灯后才合并 PR；不允许跳过失败项或硬编码结果。
- **真机不可访问时标 BLOCKED/NOT_RUN**，不能把模拟测试通过当成真机成功。
- 输出每个阶段的 PR URL、SHA、CI URL、命令、真实设备日志证据及未完成项。

## MVP 验收结束条件

同一台 Android 在真实 GNSS 环境执行“基准 → 自动视频 → 自动高负载游戏 → 手动导航”，手机 Logger 连续采集；每段有 Android START/END 事件，自动场景可确认 READY 和 STOPPED；Windows 完成无 root 数据导出，分窗/频段/匹配卫星分析生成可复算 Excel；必需 CI 全绿且真实设备结果确证。缺任意关键证据不能标 COMPLETE。
