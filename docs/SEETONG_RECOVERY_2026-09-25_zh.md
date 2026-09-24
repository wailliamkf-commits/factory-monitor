# Seetong Windows 采集恢复与草案审查交接

日期：2026-09-25。现场先恢复真实采集，再推进 YOLO/Qwen、提醒和录屏。此分支仅供 draft 审查，不得覆盖 9 月 24 日现场副本。

## 结论

| 当前证据/报告依据 | 未证实/不能外推 | 现在做什么 |
| --- | --- | --- |
| 9 月 23 日包的 10,309 个 payload 哈希、大小和 manifest 自哈希均匹配；包内最终回归记录为 147 passed / 2 skipped / 1 deselected。 | 这是合成 Phase 2 候选；Q6 控制器中断、Q7 实机桌面、Seetong、YOLO/Qwen、长测均未通过。 | 可建立草案审查分支，不能以该包覆盖现场。 |
| 9 月 23 日候选四个业务文件、2 个脚本和 13 个测试改动可进入审查；Mac 非 GUI 候选检查为 128 passed、1 skipped、1 个因导出漏 Swift 失败，完整公版补该项后 1 passed。 | GUI 本机 Qt 插件 abort，未得到 GUI 测试断言；216 passed 只是 9 月 24 日报告，原始源码/测试/日志不在包内。 | 先核对现场最新 SHA 与实际 `windows-capture` 版本/接口签名。 |
| 同 HWND 产品读取外层 4.385 秒、provider-only 直连内部 8.125 秒均仅 1 回调；经一次性授权的显示器直连内部 8.109 秒有 248 回调/248 个捕获时间戳，跨度 7.703 秒、最大间隔 .047 秒、2560×1440。 | 未比视频 ROI 像素、视频时钟或真实动作；248 不等于视频 fps、新鲜源或识别能力。原报告无 raw result，8 秒授权已结束。 | 主线改为授权的 display WGC + Seetong 视频 ROI 验证；窗口/native 留作备用诊断。 |

本次核对的 GitHub `main` 是 `0de7f2e`。它不能覆盖 9 月 24 日 Windows 现场副本，也不能推断 Windows 当前 Git 状态。9 月 25 日 raw trace/result 尚未进入审计 ZIP；147/2/1 是包内报告结果，不能改写成现场通过。

## 现场先做：版本冻结与最小补包

在 Windows 独立副本中执行，不上传画面、账号、密钥或 SQLite。记录实际 import 源码、解释器、`windows-capture` 版本/签名、配置、诊断脚本/测试的 SHA-256；保留旧安装/data，新运行独占 data/SQLite/媒体目录。

```powershell
git rev-parse HEAD
& .\.venv\Scripts\python.exe -c "import importlib.metadata,inspect; from windows_capture import WindowsCapture; print(importlib.metadata.version('windows-capture')); print(inspect.signature(WindowsCapture))"
& .\.venv\Scripts\python.exe -m pip freeze | Select-String 'windows-capture|torch|ultralytics|PySide6'
Get-FileHash .\src\factory_monitor\capture\windows.py -Algorithm SHA256
```

最少补包：最新源码、改动测试、诊断脚本、依赖摘要、窗口/显示器对照的 `result/trace`、`run.json`、stdout/stderr 和 SHA-256 清单。不附真实画面、账号、密钥或运行数据库。先核对现有 raw 日志、库版本和少量最新源码；完整则复用，不重跑 8 秒。

## 显示器 WGC 主线与授权

显示器持续回调已使“受控 display WGC + Seetong 视频区域裁剪与可观测保护”成为主推进方向，但**尚未实现或验证正式 display 裁剪/guard**；root 本轮仅实现 timestamp 修复、metadata probe 及 9 月 23 日候选整合。以下是 Windows 具体改动任务，不是已交付功能。

事后 crop 不会消除同屏捕获风险。新授权前只做离线适配编码/配置核验；授权需明确持续范围、显示器、时长、内存处理和“可能捕获同屏内容”。采集仅限内存，禁止保存全屏图；日志只留元数据。报告的最低可用约 4.12 GiB 是本次短诊断读数，**不是硬件推荐**。

授权后先做 5 分钟单路 CH9：以现场视频时钟、受控可见动作和 ROI 内变化验证已确认 ID 的视频新鲜度。不能只计 callback 或鼠标变化；静止不等于冻结，无法确认则为 `unknown`。通过后才接真实检测。

**可复制 Windows 现场授权说明：**

> 请求授权：在指定显示器上持续 5 分钟，仅采集 Seetong CH9 已标定视频 ROI，用视频时钟、受控可见动作和 ROI 内变化确认新鲜度。采集在内存中处理，不保存或上传全屏图像；但裁剪发生在采集后，同屏内容仍可能短暂进入内存。若有遮挡、最小化、锁屏、源切换或 ROI 不可见，系统暂停并标记 unknown。完成后仅保留元数据、版本和测试结果。

```powershell
& .\.venv\Scripts\python.exe -m factory_monitor preflight --config config.json --data-dir data-display
```

实现/核验 `source.backend=display` 时，冻结目标显示器、Seetong 客户区几何、DPI、位置/大小、映射版本及每路 ROI。跨 backend 必须使旧映射失效；遮挡、最小化、锁屏、源切换或 ROI 不可见时暂停并重置 absence。通知不得覆盖监控 ROI；单屏冲突时分区或显式 pause，不能 loop-capture 自己的提示。

若 display 视频 ROI 仍失败，才备用 `diagnose_windows_capture.py`：它只计元数据，窗口用 `--window-hwnd`，显示器需新授权及 `--monitor-index`/`--allow-display-capture`。再有歧义才用同 HWND native WGC 或隔离版本/DXGI；不盲升驱动/依赖。

[`FrameArrived`](https://learn.microsoft.com/en-us/uwp/api/windows.graphics.capture.direct3d11captureframepool.framearrived) 只在帧池有新帧时触发，不是固定频率心跳。窗口和显示器分别经 [`CreateForWindow`](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow) / [`CreateForMonitor`](https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createformonitor) 创建，结果只能缩小路径，不能单独判定 Seetong 错误。`minimum_update_interval` 不产生新帧；`dirty_region` 的版本行为和该现场的对照结果未核实，二者都不能先盲改当修复。若必须比较版本，在隔离环境明确固定版本；不要就地升级现场可用环境。

## 采集恢复后的功能路径

display 视频 ROI 新鲜度通过后，同轮确认实际 GPU 对 Python/YOLO 可用并推进 1 → 3 → 10 路真实输入。每路独立 tracker；只有现场最新版本仍显示重复加载 YOLO 且对照显示收益时，才共享一个 YOLO 权重实例。不要盲加 GPU、agent 或模型并发。callback 的采集 wall-clock/monotonic 时间已修为贯穿；帧年龄、队列等待、heartbeat 与 camera ID 仍须在 Windows 读回。

稳定采集后，同一轮完成真实候选、Qwen 复核、证据和人工确认。模型 15 秒要求仍含排队，超时/unknown 留在分母。系统 toast 必须携 `event_id`，点击后从 DB 回读对应事件、相机和证据；旧托盘点击只开主窗口，不算完成。这项与采集诊断并行补，不抢采集恢复第一优先级。

仅当稳定采集后再录制 15--30 分钟（建议约 20 分钟）的真实 Windows 原片：十路、真实 YOLO/Qwen、候选/模型提醒、事件证据和人工动作；其中包含 1 倍连续 300 秒的非休息无人过程及物料事件。原片留本地，不上传 GitHub。随后按 30 分钟、2 小时、正常班次、72 小时推进；不重跑已证明的合成 Q4。

接不上时只报告具体阻塞（ROI/视频时钟/动作证据、库版本、错误类别、授权状态）；不要新建测试大项目或无限重复合成测试。直接 NVR/RTSP 仍只在已有接口和授权下备用。

## 长测前的剩余风险

停止路径一次读全部 JSONL 是长测风险；只改逐行读、却把所有 ID 留于 Counter/list，仍非有界。无人值守前用分段或磁盘索引取得精确对账与内存上限，日志也计入磁盘预算。它不阻塞短诊断/采集恢复。

**当前 Gate：FAIL。** 通过本页任何单项只缩小相应问题；只有真实 Windows 证据可更新 Windows 项，合成报告和 Mac 测试不能代替它。
