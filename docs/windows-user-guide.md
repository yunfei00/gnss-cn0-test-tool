# Windows x64 使用说明

## 安装和首次运行

运行 `GNSS-CN0-Setup-x64.exe` 安装到当前用户，或解压便携 ZIP 后打开其中的
`GNSS-CN0.exe`。通过发布旁的 `.sha256` 核对下载文件。正式签名尚未配置。

预先安装 Android Platform Tools，在上位机填写 `adb.exe` 的完整路径或加入 PATH。
两个 Android APP 均需支持协议 v1。用 USB 连接测试机，解锁并在手机上确认 USB 调试。
打开 Logger，按正常权限流程授予精确定位及后台定位，开启系统定位。
若已装 APK 的签名不同，可在两个 Android 源码仓库使用
`gradlew.bat assembleDebug -PgnssTestSideBySide` 构建并装包。安装后在 Windows
工具中勾选“并装联调 APK”。命令行对应选项为 `--test-apps`。

## 测试

1. 打开上位机后自动扫描设备，每 3 秒更新一次列表，也可以点击“扫描设备”立即更新。
   首次发现的设备只有一台且已授权时会自动选中。多台设备由操作员从列表选择，
   后续接入设备会加入列表，已选设备断开时需重新选择。列表使用脱敏标识。
2. 载入或编辑计划。默认交错基准、自动视频、自动游戏和半自动导航，稳定等待 20 秒、测量 60 秒。
   每行通过“执行”复选框选择，开始测试和保存计划时按表格顺序采用已勾选的行。
   自动场景能力由手机查询。游戏参数范围为 20–1000 个粒子和 1–8 个 CPU 工作线程。
3. 点击“开始”。进入半自动环节后，在手机上实际完成提示操作，再点击“人工确认”。
   测量完成后按提示退出。可跳过人工环节、在完整场景结束后暂停或取消整个任务。
4. 在“历史与结果”查看结果和趋势。报告保存在 `%LOCALAPPDATA%\GNSS CN0\runs`。

卫星状态和事件默认存档。计划 JSON 可以通过 `collect_raw: true` 启用
Raw Measurements，通过 `collect_nmea: true` 启用 NMEA。
Raw 和 NMEA 联采在当前测试手机上出现超时，正式测量使用默认采集选项。
位置列在自动化采集中的卫星 CSV 留空。Raw 和 NMEA 可能包含敏感信息，
运行目录仅供本机分析。日志在传回之后按大小、SHA-256 和行数核验。
没有可比卫星或 L5 数据时报告显示对应原因，差值单元格留空。

## 开发、回放和回收

```powershell
uv sync --locked --extra dev --python 3.12
uv run gnss-cn0 gui
uv run gnss-cn0 replay C:\path\to\run
uv run gnss-cn0 recover C:\path\to\run --serial YOUR_ADB_SERIAL
uv run gnss-cn0 run examples/auto-smoke.json --serial YOUR_ADB_SERIAL
```

断线后用原设备回收同一个 run。未收到结束标记的窗口标记为无效。
丢失会话或 Android 开机纪元改变时，应另开任务。
离线回放读取 `raw/manifest.json` 及其中列出的文件，重新生成 `reports/`。

## 构建

```powershell
uv sync --locked --extra dev --python 3.12
.\packaging\build.ps1 -Python .\.venv\Scripts\python.exe -Iscc 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

在干净的 Windows 10/11 x64 环境验收时，准备 Platform Tools 和两款 APP，
先分别安装和运行安装版及便携版并执行一次离线回放，再用实际手机跑默认计划。
