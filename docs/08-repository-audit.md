# 08 现有代码事实与待解决缺口

## P0 实测记录（2026-11-22）

上位机基线 `9c2f9cd8d86aabd2d8926de7615c220bd58dba02`，
Logger `e367782ffaeea9f5290a444f29e27bdd1b7ca249`，
Scene `692495560b01d6bd875d3fe430e5fa5a39295e89`。手机上的旧版 APK 均报告 1.0，
安装版本不能据此关联到源码 SHA。

| 项目 | 结果 | 证据 |
|---|---|---|
| ADB / 安装 / 授权 | PASS | 一台已授权设备，API 35，定位已开启，Logger 有精确和后台定位权限，Scene 有相机权限 |
| 旧版视频和高负载游戏启动 | PASS | `am start -W` 后对应 Activity 位于前台，媒体和计算负载状态未由旧协议确认 |
| 旧版显式 STOP | FAIL | 视频和游戏收到广播后仍为 top resumed Activity，随后仅停止了测试 Scene APP |
| 旧版 Logger 前台服务 | PASS | 先打开 Logger 主界面再发 START，`dumpsys activity services` 确认定位服务处于前台，然后发送 STOP |
| 直接读取内部目录 | FAIL | 普通 shell 读取内部目录得到 Permission denied。当前源码优先使用 APP 专用外部目录，内部目录是回退路径 |
| 旧版卫星样本 | PASS | 手机端读取 APP 专用外部目录，统计 182 条卫星记录，其中 GPS L1 112 条、L5 0 条。原始轨迹没有离开手机 |
| 旧版 L5 和跨文件导出完整性 | NOT_RUN | 本次样本无 L5。旧接口没有会话导出清单 |

基线 Actions 已核对。两个 Android main 最近一次构建成功。上位机当时没有工作流，
三个 main 当时均无强制状态检查。

> 核查日期：2026-11-22。依据 GitHub 三仓库 main 的 README、Manifest、Kotlin 源文件与目录树。本报告是**代码静态核查**，并非真机/构建结果。后续变更需重新核查并补上 commit SHA。

## A. gnss-cn0-test-tool

- 新建 public 上位机仓库，2026-11-22 检查时 `main` 无内容。
- 本次新增 README / AGENTS / docs 规划文件；上位机 Python/GUI/ADB/分析代码**尚未实现**。
- 不应将计划中待开发特性标识为已交付。

## B. GnssLoggerApp

仓库：[GnssLoggerApp](https://github.com/yunfei00/GnssLoggerApp)。

已核查：

- `app/src/main/java/com/example/gnsslogger/gnss/GnssCollector.kt` 使用 `GnssStatus.Callback` 获取 constellation、svid、cn0、elevation、azimuth、usedInFix、carrierFrequency、basebandCn0；注册 Raw Measurements 与 NMEA。
- `GnssCommandReceiver.kt` 只接收 START_LOGGING、STOP_LOGGING。
- `AppActions.kt` 仅定义 START_LOGGING、STOP_LOGGING；**未实现 README 中声称的 ACTION_UPDATE_SCENE**。
- `AndroidManifest.xml` Receiver 的 intent-filter 只列 START、STOP。
- `CsvGnssWriter.kt` 的 `SATELLITE_HEADER` 含 `elapsed_realtime_nanos`、`session_id`、`constellation_name`、`svid`、`cn0_dbhz`、`carrier_frequency_hz`，**不含 scene_name**。
- `GnssLoggerService.kt` 位置记录写入 scene="default"，未见一致可用的场景切换标记。
- `LogFileManager.kt` 使用 `File(context.filesDir, "gnss")`，属 APP 私有内部文件目录；非 root ADB 的直接 pull 不可默认依赖。
- README 提到分享/保存与 CSV 导出；但必须核对真实运行能否提供**机器可控导出**，不能直接把 UI 分享等同自动导出。

首要待修：

1. 真实可靠的 MARK_EVENT 和采样事件落盘；
2. 已声明却未实现的场景更新接口与 CSV schema；
3. APP 状态查询、持久化 session 和 ACK；
4. 普通 ADB 可用的自动化完整导出与校验；
5. Logger 连续采集在负载/后台切换中的真机稳定性。

## C. AutoTestSceneApp

仓库：[AutoTestSceneApp](https://github.com/yunfei00/AutoTestSceneApp)。

已核查：

- `SceneEntryActivity.kt` 被 Manifest 标为 exported=true，读取 `scene`、`duration`、`particles`、`cpu_threads` 等参数并调用 `SceneRouter.openScene()`。
- `SceneIds.kt` 定义 motor、music、video、game_heavy、flashlight、recorder、mirror、compass、ambient_light、white_screen、dynamic_wallpaper、front_camera、rear_camera 等。
- `SceneRouter.kt` 把 `game_light`、`game_2d` 视作**未实现**；其余场景映射至 Activity，`duration` 仅为兼容字段，**场景不依赖该参数自动超时退出**。
- `StopSceneReceiver.kt` 目前收到 ACTION_STOP 仅 Log/Toast，无直接停止业务场景的实现。
- 某些 Activity（已核对 Video / Music / GameHeavy）内部有动态注册的未导出 Receiver 处理 ACTION_STOP 并进行资源清理；但向 Manifest Receiver 的外部 ADB 广播**不等于一定送到 Activity 内部 Receiver**。
- 现状无统一可查询 READY/STOPPED 的机器接口，需补齐状态确认和错误判断。

首要待修：

1. START 的真实 READY/ACTIVE 状态暴露；不能按 ADB 返回码当 READY；
2. STOP 命令桥接所有内部场景、确保资源释放并有 STOPPED ACK；
3. 非法/未实现的 scene_id 明确 UNSUPPORTED；
4. 采样前场景状态核实、前后台生命周期、权限与硬件失败诊断。

## D. 已验证与待确认的区别

| 项目 | 事实级别 |
|---|---|
| 两个 Android 仓库存在源码和 Gradle 工程 | 已通过 GitHub 静态核查 |
| GnssStatus 数据字段读取代码存在 | 已通过源码核查 |
| SceneEntryActivity 支持指定场景启动参数 | 已通过源码核查 |
| ACTION_UPDATE_SCENE 当前 main 可执行 | **否**，源码不支持 |
| STOP 广播可靠停止所有场景 | **未验证且存在缺口** |
| RELEASE APK 无 root 自动导出内部日志 | **未验证且存在缺口** |
| 手机 GPS L5 实际产生数据 | **需要真机** |
| 三仓库 CI 与真机闭环通过 | **尚未验收** |

## E. 代码参考链接

- [Logger README](https://github.com/yunfei00/GnssLoggerApp/blob/main/README.md)
- [Logger Commands](https://github.com/yunfei00/GnssLoggerApp/blob/main/app/src/main/java/com/example/gnsslogger/GnssCommandReceiver.kt)
- [Logger CSV](https://github.com/yunfei00/GnssLoggerApp/blob/main/app/src/main/java/com/example/gnsslogger/storage/CsvGnssWriter.kt)
- [Logger Paths](https://github.com/yunfei00/GnssLoggerApp/blob/main/app/src/main/java/com/example/gnsslogger/storage/LogFileManager.kt)
- [Scene Router](https://github.com/yunfei00/AutoTestSceneApp/blob/main/app/src/main/java/com/yunfei/autotestscene/scene/SceneRouter.kt)
- [Scene STOP](https://github.com/yunfei00/AutoTestSceneApp/blob/main/app/src/main/java/com/yunfei/autotestscene/scene/StopSceneReceiver.kt)

下一次 Codex 执行应在 P0 将仓库真实 SHA 和源码差异更新到此文件，避免本快照变成过时事实。
