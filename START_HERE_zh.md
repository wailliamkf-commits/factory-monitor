# Factory Monitor：从这里开始

这是本地桌面软件的工程交接说明，不是可双击即用的独立安装包，也没有完成现场验收。它不会连接 RTSP/NVR，不会上传摄像头画面，不会自动把候选事件判定为人员过错。

当前有明确未完成项：**自动放大/返回暂不可用**，必须先实现并验证现场客户端的安全点击适配；任何配置都不能启用当前通用点击路径。需要放大时由操作员直接操作原监控客户端，映射不明时新软件暂停相应分析。十个事件同时触发的本地模型复核也尚未达到全部 15 秒内完成的要求。详见 `TEST_REPORT.md`，不能据此版本直接上线。

**原开发 Mac 的本地状态：** 该机当时已准备 Python 环境和本地模型，可打开 `scripts/launch-factory-monitor.command` 进入合成演示；按“开始”才启动测试画面。需要本地视觉复核时，运行 `scripts/start-local-model.command`，并确认界面本地复核地址为 `http://127.0.0.1:11435`。这不是公开克隆或新账户的前提，更不是已验证的可移植安装结果。

模型和运行程序没有打入 wheel，也不进入 Git。新机器不能复制旧电脑的 `.venv`、配置、现场数据或 Mac `.tools` 二进制；应在目标系统创建项目内环境，并按许可证与内部流程手动准备目标系统对应的模型与本地 Ollama。它只可绑定 `127.0.0.1:11435`，不得暴露到局域网或公网。Windows 的完整新账户交接见 [docs/WINDOWS_HANDOFF_zh.md](docs/WINDOWS_HANDOFF_zh.md)。

## macOS 准备

在仓库根目录执行。需要 Python 3.12、Xcode Command Line Tools（用于 Swift/ScreenCaptureKit helper）和 ffmpeg（用于独立检查媒体；当前证据编码使用 OpenCV 并会显式报告编码失败）。

```bash
xcode-select --install                 # 已安装时系统会提示
brew install python@3.12 ffmpeg         # Homebrew 环境；无 Homebrew 时自行安装等价工具
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[desktop,vision]'
.venv/bin/python -m factory_monitor init --config config.json
./scripts/launch-factory-monitor.command
```

首次选择 `screencapturekit` 的已校准实时来源时，程序会在本机编译随 wheel 打包的 `ScreenCaptureKitHelper.swift`，缓存到 `~/Library/Caches/FactoryMonitor/`。这要求 Xcode 工具链和系统框架可用。它不会代替操作员在系统设置中选择屏幕录制权限，也不会自动证明已获得权限；只有实际选择目标窗口并观察到帧后才记录该事实。

本地模型需要时另开一个终端：

```bash
./scripts/start-local-model.command
```

脚本只在 `.tools/ollama/ollama` 和 `models/ollama/` 同时存在时启动，使用 `OLLAMA_HOST=127.0.0.1:11435` 与 `OLLAMA_NO_CLOUD=1`。GUI 启动器仅在**第一次创建** `config.json` 且这两项准备齐全时，将新配置的 review endpoint 改为 11435；已有配置绝不被脚本改写。

## Windows 准备

在目标 Windows 机器上安装 Python 3.12 x64（含 Python Launcher）、ffmpeg，并确认 `py -3.12 --version` 和 `ffmpeg -version` 有输出。WGC 依赖 `windows-capture`，它只会在 Windows 的可选依赖中安装；不要用截图循环冒充 WGC。新账户按 [Windows 交接](docs/WINDOWS_HANDOFF_zh.md) 执行脚本；脚本不改全局设置、不下载模型、不创建配置、不启动抓屏。

在 PowerShell 的仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
.\.venv\Scripts\python.exe -m factory_monitor init --config config.json
.\scripts\launch-factory-monitor.ps1
```

如已**手动**准备 Windows 对应的 `.tools\ollama\ollama.exe` 与 `models\ollama`，另开 PowerShell 运行：

```powershell
.\scripts\start-local-model.ps1
```

它仅绑定 `127.0.0.1:11435`。新建配置会在这个本地模型准备齐全时使用 11435；已有配置保持原来的 endpoint。Windows 没有 Swift helper；实时窗口读取必须由 WGC adapter 在实际 Windows 环境验证。

## 第一次运行与校准

先进行不会读取真实窗口的本地检查：

```bash
.venv/bin/python -m factory_monitor preflight --config config.json --data-dir data
.venv/bin/python -m factory_monitor demo --config config.json --data-dir data --seconds 30 --report reports/demo.json
```

Windows 用 `.venv\Scripts\python.exe` 替换上面的 Python 路径。`demo` 明确是合成画面。预检不会请求权限、选择窗口、启动模型或修改操作系统设置。

要开始实时校准，必须在 GUI 中选择目标客户端窗口并填写稳定的窗口标题、实际像素尺寸、后端和布局版本，然后为每个摄像头配置裁剪、ROI/线、排班/休息、平面位置与关联视图。保存后关闭并重新打开配置，核对这些值。窗口缩放、标题/来源变化或布局变化会使映射失效，必须重新校准。

实时分析还要求每路独立的摄像头编号模板与源画面时钟/心跳区域；客户端自己的公共时钟不能证明十个视频都在更新。GUI 能记录编号模板，其余高级字段见 `docs/LIVE_CALIBRATION.md`，现阶段须由实施人员配置。没有可靠逐路心跳时，该路保持不可判定。

## 可用脚本与验证边界

- `scripts/launch-factory-monitor.command` / `.ps1`：创建新安全配置后启动 GUI。
- `scripts/start-local-model.command` / `.ps1`：显式启动项目内、仅环回的 Ollama 11435。
- `scripts/build-macos.command` / `scripts/build-windows.ps1`：只在各自系统构建本地 wheel/sdist，不是跨平台安装器。
- `scripts/qa_runtime_smoke.py`：约 94 秒的合成多进程证据链路检查；生成的 MP4、SQLite、预览和恢复记录不构成现场证据。

当前阻塞最终 Gate 的事实仍是：没有实际 Windows 客户端证据、没有实际 macOS 客户端证据、没有 100 次已验证切换、没有各系统 72 小时十路运行、没有每类独立的 50 正/100 负样本、没有正常班次误报暴露数据。任何 `demo`、单机模型响应、构建成功或已保存配置都不能把这些项目标为完成。
