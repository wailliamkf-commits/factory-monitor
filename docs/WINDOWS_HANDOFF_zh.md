# Windows 新账户交接（工程预览 v0.1）

**2026-09-23 当前执行优先级：**大陆无 VPN 设备及最新 Windows 修复候选，按 [真实功能任务单](superpowers/plans/2026-09-23-phase2b-to-demo.md) 与 [大陆部署说明](MAINLAND_DEPLOYMENT_zh.md) 执行。下文旧版在线安装和历史测试顺序不覆盖新任务单；不要用旧 main 覆盖现场修复。禁止随意搬运个人环境/数据，不妨碍按部署说明交付经过校验、仅含本项目公开权重的专用模型库和匹配 Windows 资源包。

已有测试设备正在运行、需要升级或交付半小时测试记录时，优先按 [稳定运行与版本交付](STABLE_OPERATION_zh.md) 操作；本页的干净账户安装步骤不用于覆盖现有测试目录。2026-09-22 目标 Windows 十路合成复测失败，须先完成 [定向修复与回归](WINDOWS_RETEST_AUDIT_2026-09-22_zh.md)，再升级真实客户端测试范围。

这是源代码和本地开发环境的交接，不是安装器、`.exe` 成品或远程代操作服务。它不能证明 Windows 现场可用，整体 Gate 仍为 **FAIL**。

2026-09-21 已收到 Seetong 1.0.13.4 的 Windows 客户端安装文件；格式、平台与后续检查见 [Seetong 预检](SEETONG_CLIENT_PREFLIGHT_zh.md)。它是原监控客户端的安装器，与本项目工程预览是两套软件。原客户端须先能正常出图，实际布局和窗口捕获仍待现场验证。

## 1. 干净账户安装

在新 Windows 账户中，从公开仓库克隆或解压干净源代码。不要复制任何旧电脑的 `.venv`、`.tools`、`config.json`、`data`、模型、数据库、录像或校准截图；它们可能带有平台二进制、私密配置或现场信息。

先安装 Python 3.12 x64（包含 Python Launcher），打开新 PowerShell 后确认：

```powershell
py -3.12 --version
```

安装 `ffmpeg` 并确认：

```powershell
ffmpeg -version
```

进入仓库根目录，执行项目脚本。它只创建此仓库的 `.venv`，安装 `dev, desktop, vision, windows` 依赖并做导入检查；不会改 PATH/注册表/执行策略等全局设置，不会创建或覆盖配置，不会开始抓屏，也不会下载任何模型。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
```

脚本报错时先处理报错指向的 Python、依赖或网络/代理问题。它发现旧 `.venv` 不完整或不是 Python 3.12 时会停止，避免覆盖未知内容；确认该目录可丢弃后由维护者手动删除再重跑。

## 2. 手动准备模型与本地复核

合成演示不需要模型。实时检测前，按模型自身许可证和内部交接方式手动下载并校验指定权重。以下是本工程记录的官方来源与 SHA-256，不是安装脚本会自动执行的下载：

```text
models\yolo11n.pt
```

```powershell
New-Item -ItemType Directory -Force .\models | Out-Null
Invoke-WebRequest -Uri "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n.pt" -OutFile .\models\yolo11n.pt
$ExpectedSha256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
if ((Get-FileHash .\models\yolo11n.pt -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedSha256) { throw "yolo11n.pt SHA-256 mismatch; delete the file and investigate the source." }
```

本地视觉复核可以跳过**合成演示**，但不能从真实候选的最终人工复核流程中省略。真实测试前，手动下载 [Ollama 官方 Windows AMD64 压缩包 v0.34.2](https://github.com/ollama/ollama/releases/download/v0.34.2/ollama-windows-amd64.zip)，将**整个压缩包**解压到 `.tools\ollama`，保留全部 DLL；不要只复制 `ollama.exe`，也不要复制 Mac 二进制。新账户可明确执行：

```powershell
$OllamaZip = Join-Path $env:USERPROFILE "Downloads\ollama-windows-amd64.zip"
if (Test-Path .\.tools\ollama) { throw ".tools\ollama already exists; inspect it rather than overwriting it." }
New-Item -ItemType Directory -Force .\.tools, .\models\ollama | Out-Null
Expand-Archive -LiteralPath $OllamaZip -DestinationPath .\.tools\ollama
```

结果应至少包含：

```text
.tools\ollama\ollama.exe
models\ollama\
```

不要把模型目录、实际画面、配置或凭据提交到公开仓库。模型准备好后，另开一个 PowerShell 手动启动本地服务：

```powershell
.\scripts\start-local-model.ps1
```

它仅监听 `127.0.0.1:11435`，窗口关闭即停止。脚本不会下载模型，也不会连接云端。

在服务运行的情况下，第三个 PowerShell 窗口手动拉取指定模型；该动作需要网络，且会写入项目本地 `models\ollama`，所以绝不由安装脚本自动执行：

```powershell
$env:OLLAMA_HOST = "127.0.0.1:11435"
$env:OLLAMA_MODELS = (Resolve-Path .\models\ollama)
& .\.tools\ollama\ollama.exe pull qwen3-vl:2b-instruct
& .\.tools\ollama\ollama.exe list
```

## 3. 创建配置并运行合成演示

首次创建配置只做一次；命令拒绝覆盖已有 `config.json`。

```powershell
.\.venv\Scripts\python.exe -m factory_monitor init --config config.json
.\.venv\Scripts\python.exe -m factory_monitor preflight --config config.json --data-dir data
.\scripts\launch-factory-monitor.ps1
```

若真实测试需要本地复核，在 GUI 中将本地复核 endpoint 明确设为 `http://127.0.0.1:11435`、模型设为 `qwen3-vl:2b-instruct` 并启用本地复核；保存、关闭并重新打开配置，确认读回。`init` 总是默认写入 11434；只有启动器在它自己首次创建配置且本地模型文件已存在时才会改写为 11435，因此不能假定端口会自动正确。

启动器固定以 `demo` 打开 GUI。只有操作员在 GUI 里选择来源并按“开始”，程序才会尝试读取画面；合成演示不读取真实窗口。可做 30 秒无 GUI 演示并保留报告：

```powershell
.\.venv\Scripts\python.exe -m factory_monitor demo --config config.json --data-dir data --seconds 30 --report reports\local\demo.json
```

`preflight` 是只读检查，不会请求权限、选择窗口、启动模型或更改系统设置。`demo` 是合成画面；两者都不构成准确率、十路吞吐、切换或现场验收证据。

## 4. 手动 1 → 3 → 10 路测试

真实客户端测试只在获授权的 Windows 现场由操作员执行。先在 GUI 中为目标窗口填写精确标题、像素尺寸、WGC 后端和布局版本；保存、关闭并重新打开，核对读回。逐路配置相机裁剪、ROI/线、排班、编号模板以及每路独立心跳。缺少可靠的逐路心跳时，该路必须保持不可判定。

按下列顺序扩展，每一级都记录窗口/布局、相机编号、开始结束时间、事件、缺口和人工复核结果：

1. **1 路：** 仅启用一个已校准相机，由操作员手动切换客户端视图，确认该路心跳、画面和证据记录；异常即停止并保留诊断。
2. **3 路：** 三路同时启用，分别确认每路心跳，检查一条停滞或映射失效会仅让该路不可判定。
3. **10 路：** 全部十路均有独立编号和心跳后才运行；记录同时候选的排队、超时和证据缺口，绝不删掉失败样本。

不要启用或尝试配置自动放大、自动返回、原生点击或跨视图切换：当前功能未交付且无条件禁用。操作员必须在原监控客户端中手动切换。不要把 `pyautogui` 已安装、GUI 可见、模型单次响应、合成报告或已保存校准写成现场通过。

## 已知限制与下一步证据

- Windows 托管测试机已执行安装脚本、78 项回归和十路合成证据测试；[记录](https://github.com/wailliamkf-commits/factory-monitor/actions/runs/35515661344)。现场 Windows 电脑、真实 WGC 窗口、客户端和模型吞吐仍未验证。
- 十个同时本地模型复核目前只有 1/10 在 15 秒内完成，9 个超时/删失；不能把模型或硬件称为达标。
- 自动原生点击/放大/返回未交付；必须等客户端专用命中测试、遮挡与 DPI 证明及读回验证完成后另行评估。
- 仍需 Windows 和 macOS 分别完成实际客户端校准、独立 50 正/100 负数据、正常班次误报暴露、100 次切换/故障和 72 小时十路证据。详见 [验收协议](ACCEPTANCE.md)。

## 交给新 Codex 代理的提示词

```text
你接手 Factory Monitor 工程预览 v0.1 的 Windows 现场验证准备。先阅读 STATUS.md、TEST_REPORT.md、START_HERE_zh.md、docs/WINDOWS_HANDOFF_zh.md、docs/ACCEPTANCE.md 和 docs/LIVE_CALIBRATION.md。不要把 demo、构建、依赖安装、已保存 config、单次模型响应或 GUI 可见称为现场验收。

边界：不上传摄像头画面、配置或凭据；云端分析保持关闭；不修改全局 Python、PATH、注册表或执行策略；不自动下载模型；不自动启动真实抓屏。若当前会话具备且获授权使用本地电脑操作权限，可以协助安装和读取本机状态；选择真实监控窗口、开始抓屏与手动切换仍由现场操作员明确执行。自动原生点击、自动放大和返回在当前版本无条件禁用。十事件模型复核 15 秒目标当前失败（1/10 完成，9 超时）。

目标：在获授权 Windows 现场，用 Python 3.12 项目内 .venv 完成 1→3→10 路的手动、可审计试验，逐路验证编号和独立心跳，保留超时/缺口/失败。现场验收仍需独立 50 正/100 负、正常班次误报、100 次切换/故障和 72 小时十路证据；没有这些证据就保持 Gate FAIL。先报告实际观察和阻塞，不要实现新运行时功能或作硬件采购承诺。
```
