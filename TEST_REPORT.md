# 测试与证据报告

## 2026-09-25 本分支实现与本机验证

从已核验 9 月 23 日候选导入 19 项源码/脚本/测试差异，并保留公版 Swift 采集助手。新增 WGC 回调 wall-clock/monotonic 透传，避免出队时重标时间；不宣称修复 Seetong 单帧故障。新增显式目标、限时子进程、仅元数据的诊断脚本，单帧/零帧返回非零，多回调的退出 0 也不证明源视频新鲜。

代码修改后的非 GUI 集成回归为 **140 passed / 1 skipped（真实 YOLO 素材未安装），27.36 秒**。最终审查发现单帧退出码的问题，修正后针对采集/诊断的 **18 项定向测试通过**；该最后修正未再重复本机整套。GUI 测试受此 Mac 的 Qt 平台插件加载故障阻断，独立 QApplication 最小程序也 abort，不能登记 GUI 通过。Ruff、pip check、差异检查通过；wheel/sdist 已构建，读回验证包含 Swift 助手和本次 windows.py。GitHub 双平台检查另以对应提交的 Actions 记录为准。以上均不含 Windows 现场执行。

## 2026-09-25 Seetong 显示器对照与恢复交接

审计包的 10,309 个 payload 哈希、大小和 manifest 自哈希均匹配；它记录 2026-09-23 合成 Phase 2 候选与 147 passed / 2 skipped / 1 deselected，不能当作 9 月 24 日查看保护（报告称 216 passed）或 9 月 25 日 Seetong 诊断的原始证据。候选四个业务文件、2 个脚本和 13 个测试改动可进入 draft 审查；Mac 非 GUI 检查 128 passed、1 skipped，导出漏 Swift 的一项在完整公版工作树补测 1 passed。GUI 离屏 Qt 插件 abort，未取得 GUI 断言结果。

Windows 同 Seetong HWND 产品读取外层 4.385 秒（第 2 次 `read()` 3 秒超时）和 provider-only 直连内部 8.125 秒/外层 8.799 秒均仅 1 回调。新增一次授权显示器直连：内部 8.109 秒、248 回调/248 个捕获时间戳、跨度 7.703 秒、最大间隔 .047 秒、2560×1440，外层 8.947890 秒正常退出；最低可用约 4.12 GiB 只是短诊断读数。未比视频 ROI 像素、视频时钟或真实动作，故不等于 248 视频 fps/新鲜源；授权已结束，报告未附 raw result。正式 display 裁剪/guard 尚未实现或验证。

下一步为核对现有 raw 日志、库版本和少量最新源码；授权后进行 5 分钟单路 CH9 的视频时钟、受控可见动作、确认 ID 和 ROI 变化验证，静止不等于冻结，未确认即 unknown。通过后同轮推进 1→3→10 的 YOLO/Qwen、`event_id` 通知/DB 回读、证据和 15—30 分钟原片；仅 ROI 验证失败才回到 native WGC/版本隔离/DXGI。未新增 Windows 运行结果，现场 Gate 仍 FAIL。

## 2026-09-23 大陆网络部署静态核对（无新增运行结果）

依据 Ollama、pip、TUNA 和阿里云官方文档确定 [大陆部署补充](docs/MAINLAND_DEPLOYMENT_zh.md)。本地 API 无 Key、关闭云功能及多图请求有官方接口依据；不意味着目标宽带、现有模型和吞吐已通过实测。现有 `start-local-model.ps1` 使用 11435 和项目模型目录，配置 init 默认 11434，故交接必须明确保存/读回端点；`setup-windows.ps1` 仍联网安装和升级 pip，尚非离线安装器，所需离线路径及完整包列入 Windows 交付。国内云接口协议不同，当前未启用或测试。

同步任务单与状态，真实介绍片要求改为 15—30 分钟，连续 300 秒无人场景保留。未修改运行时、启动模型、安装依赖、测试 Windows/Seetong、大陆线路或录制新视频；此次文档核验不替代目标设备证据，未以软件回归测试数量宣称功能实现。 本次文档差异检查及相对链接检查通过，src/scripts 无跟踪差异；独立 Sol 只读核对两份当前任务文档，无阻止文档交付的明确矛盾。此结论只覆盖文档。

## 2026-09-23 功能优先顺序调整（无新增 Windows 测试结果）

依用户最新要求，下一阶段以十路真实人物分析、屏幕提醒和实际录屏为直接产出；全量逐帧账目/Q1—Q7 不再全部作为受控实景演示的前置。两份 Windows 报告的原始结论与指纹不变，也没有把未完成的工程项改成 PASS。可接受的最小试运行保护及后续正式要求见 [更新任务单](docs/superpowers/plans/2026-09-23-phase2b-to-demo.md) 和 D012。

本机静态读取旧公开源码确认：`inference.py` 的 `YoloPersonDetector.detect` 按摄像头分别创建 YOLO 对象，`_resolve_device(auto)` 未查 CUDA；`gui/app.py` 的 `handle_runtime_message` 仅对 uncertain/timeout/error 的分析结果再次提醒，supported/dismissed 不触发该通知。上述是本机源码事实，不是 Windows 新候选实测性能结论。执行者先核对现场版本，再按功能收益修改或配置；无需为已在 Windows 修复的问题重复改动。本轮没有修改业务源码、执行 Windows 模型或生成新的真实监控录屏。

另核实旧 `capture/windows.py` 在回调中保留 monotonic 时刻，但 `read()` 以出队时的 wall clock 作为业务 timestamp；积压可能使旧画面得到较新的业务时间。此为静态风险，Windows 新候选先核对是否已修，再保证同一帧的采集 wall/monotonic 时间贯穿检测、事件、提醒与证据，不把出队时间当采集时间。旧 `_prune_completed` 存在启动、停止、证据更新、模型更新及 watchdog 多个入口；临时试运行保护必须统一覆盖，不能只拦第 21 个候选。上述核对用于直接约束实时功能实施，不是新的 Windows 故障复现结果。

## 2026-09-23 第 2 步两份报告核验（完整 Q1—Q7 未通过）

本机收到第二步报告 11,076 字节（SHA-256 `793a86a6c497e4dc885d7a4730167d6d43c00016d4194858b7ee13765fe2d4d0`）及补查报告 8,605 字节（`cde0b485a112a752ee1ee61685215d26df3e8e0c04337931247e8132deb6d282`）。已保存原文、报告内四个候选源码指纹和接收记录；没有收到最新源码、测试或原始工件，因此不称源码独立审查或 Windows 复验。

报告结果 111 passed / 2 skipped / 1 deselected、35.80 秒，及原参数单批十路各 181 帧、2 FPS、90.5 秒保留为有限基线。补查已确认 Q3 为真实子进程 EOS 消费屏障，应复用；此前单路短窗口重叠、容量 1 拒收第 2 件、offscreen GUI，不能代表原任务要求的组合。报告还指出 512 条审计截断/最后快照可能发送失败，以及先删 DB 后删媒体的中断恢复风险。后者尚未故障复现，不能写成已经发生数据丢失。

完整 Q1—Q7 和现场 Gate 保持 **FAIL**。下一步见 [第 2B 至现场与演示任务单](docs/superpowers/plans/2026-09-23-phase2b-to-demo.md)：补最小实现后集中验证账目守恒、10 路原参数 5+5/10+1、可恢复清理、独立像素内容反例和可见桌面；封装完成、窗口完整、停止确认分别判定。本机未修改运行时代码、未执行 Windows 或接入 Seetong。接收与审查位于忽略目录 `reports/local/phase2b-intake-20260923/`。

同日本机完成**旧 Mac 合成功能录屏**：公开源码 `0de7f2ee75622773c99a951ee968b671f51298e5`，视觉模型关闭，原生 ScreenCaptureKit 只抓指定实际窗口。最终 MP4 108.4 秒、1320×848、5 FPS，542 帧全量解码成功；SHA-256 `33b37ee53f9667dabd82fd03d9e95617987c3c2e465d40e63b2da569395269d8`。实际应用触发 1 个 CAM01 越线候选；机器结果 uncertain（模型关闭）、人工点击后 confirmed、DB 封装状态 complete。Root 目视核对候选/确认状态栏通知与重新选中后的 complete 详情。没有系统横幅或音频；不算实时模型、Windows 第 2B、证据窗口正式验收或现场结果。首版裁切及详情未自动刷新保留记录；重录仅调整独立演示显示布局，未修业务代码。源片/成片、时间戳、配置、库与清单在本机忽略目录 `reports/local/phase2b-intake-20260923/demo/`。

## 2026-09-23 停止协议第 1 步报告核验（新源码/原始工件待回传）

已读取完整报告与截图，保存原文快照。报告 SHA-256：`ebd03e75fe66eef5250313ab69ca52c95b80c22eaadbdae7ecb1e29e213399b9`，11,915 字节。本机把报告中的旧 runtime.py/evidence.py 哈希与此前第二轮回传包的实际文件重新比较，均一致；三个新文件只有报告提供的指纹，未收到文件，不能称源码审查或独立复验。Windows 端记载最后 95 passed / 2 skipped / 1 deselected、21.50 秒，真实 YOLO 明确排除；本机没有重新执行这些测试。

报告记录的改进覆盖顺序 EOS/drained、消息处理串行化、晚命令、异常 stopped 不冒充成功、写入方存活保护、关窗及清理失败重试。立即停止 confirmed=true 的示例为 0 帧/incomplete，只支持该中断状态处理；多帧积压、真实跨进程竞态、精确账目及容量/恢复组合仍需验证。时限可能嵌套，不能把 1.5/2/2/5 秒简单相加宣称必然冲突，需下一阶段计时与源码核对。报告 PASS 可作为继续受控第 2 步的候选依据；本机独立接纳尚未完成，完整 P3/现场 Gate：FAIL。

已生成 [第 2 步执行计划](docs/superpowers/plans/2026-09-23-stop-accounting-phase2.md)；报告审查和接收记录位于本机忽略目录 `reports/local/stop-protocol-step1-review-20260923/`。未修改业务代码、安装依赖、恢复自动化、接入 Seetong、调用模型、启动长测或推送修复。此前 30 分钟证据不覆盖新停止版本。

## 2026-09-22 第二轮半小时完整包审计

完整回传包已收到。3,767 个 payload 文件的 SHA-256 与大小全部匹配，manifest 自哈希匹配；84 个源发布文件与本地 Git `4abaa0ba60ff61af4418a1f27be0d0941a67263b` 逐项内容相同，提供的修复文件哈希与记录一致。150 个 ID、15 批、357 条资源记录自洽。root 在 Mac 独立完整解码现存 20 个 MP4（共 3,620 帧）及 3,620 个原帧 JPEG；20 段的 hash、帧数及时间戳均与原始 batch 审计一致。该解码只覆盖当前留存的 20 段，不推断 130 段已清理媒体仍可播放。

原报告的合成阶段 PASS 可以按已核验范围登记：同一运行时、十路 2 FPS、15 批/150 个事件、保留 20 段；第一批来自合成规则，后 14 批/140 个候选为测试注入，模型关闭且批次错开。它不是重叠连续压力、YOLO/Qwen、Seetong、人员动作纠错、现场验收或新的 Windows 测试证据。正常 worker 退出与最终状态也不证明停止阶段零丢弃：130 段仅有历史审计，停止队列尚无精确全生命周期账目。

修复的架构复核发现 capture 对 evidence queue 亦调用 `cancel_join_thread`；退出仍缺有序 drain/ACK 和精确在途丢失账目，下一步应做受控在途停止验证，不能用重复空跑替代。`scandir` 的局部性能改进证据成立，但它仍全目录扫描。修复正式接纳和整体现场 Gate 保持 **FAIL**；详见本机忽略目录 `reports/local/soak2-package-audit-20260922/REVIEW_zh.md`。本轮未执行 Windows 测试、未改业务代码、未提交或推送。

## 2026-09-22 持续运行交接材料检查

本轮补齐 [稳定运行与版本交付](docs/STABLE_OPERATION_zh.md)、持续测试 JSON 模板及 Laya/Jev 资料刷新。模板可解析，初始现场 Gate 为 FAIL；队列、延迟、资源和覆盖的未测值保留为 null/UNKNOWN，未填造测量结果。文档相对文件链接已核对。18 个已跟踪 `src/` 文件逐项 SHA-256 与交接前 `f9018b24cecdcd39a69d45886f1addaaf6279a5a` 相同；仅改交接文档、状态与记录模板，不重新运行软件回归冒充修复。下载/启动命令对照现有脚本做静态核对，本轮未在 Windows 执行，也未生成 EXE。

GitHub 发布列表回读：只有 `v0.1.0-preview.1` 预发布，上传资产为空；当前可交接源码，模型、Python 环境与本地工具仍需目标系统准备。Laya 刷新固定到 `573e5b62696ba441230cd6be71d593331b5d23af`，没有安装/推理或性能证明。用户确认另一台 Windows 正在跑半小时合成画面，并转述两处修复已在独立副本实现，原安装未覆盖，长测中未再改业务代码。当前尚未收到这两处代码差异、新增测试工具、最终日志和负载数据，故只记为“用户报告待核验”，不能覆盖以下失败证据，更不能计作真实 Seetong 验收。整体 Gate：FAIL。

## 2026-09-22 目标 Windows 提供的失败证据

用户交付十路合成复测包，本轮在 Mac 上进行离线审计，没有重跑 Windows 或执行附件脚本。包内清单 1,059 文件哈希全部通过；baseline 记录的 33 项源码与 `4abaa0b`、`8f3ae84` 一致。十段媒体完整解码均为 101 帧、2 FPS、50.5 秒，源时间跨度约 90.988 秒且有 35 个相同缺口；在各自首尾窗口内，检测有 183 个源时间点，证据仅保留 101 个。采集进程 stopped 后仍被强制终止为 -15，另两进程退出 0。

原日志计数 81 只能描述 `_put_bounded` 返回 False 的已上报累计结果；该路径混合替换旧包、拒收新包及可能无真实丢失的重试，不能视为实际丢包的精确数或严格下限。evidence 的 65.328 CPU 秒是采样窗口差值，非完整生命周期计量。详细核验、代码假设、待执行的 P0—P3 见 [复测审计](docs/WINDOWS_RETEST_AUDIT_2026-09-22_zh.md)。**目标设备合成容量 FAIL**；现场、YOLO/Qwen 和 Seetong 均未在该包中测试。以下 CI 通过是历史、不同环境证据，不能覆盖此次失败。

## 公开迁移回归

代码提交 `379d7a9` 的 [双平台托管实测](https://github.com/wailliamkf-commits/factory-monitor/actions/runs/35515661344) 已通过：Windows 78 passed / 3 skipped，macOS 80 passed / 1 skipped；均通过安装、依赖检查、Ruff、测试、94 秒十路合成证据检查与 wheel/sdist 构建。Windows 10 段证据各 90.5 秒，Mac 各 90 秒，均读回成功。复核关闭，不构成模型延迟或识别精度证据。Windows 跳过 Mac helper 编译、非 Windows 主机报错检查与未下载的 YOLO 样本；Mac 仅跳过 YOLO 样本。

首轮 Windows 测试失败（4 项同源错误）保留在 Actions 历史：早期 epoch 时间经 naive datetime 转换触发 Windows CRT 错误。修正为 UTC-aware 再转本地时区后，原测试不变并通过；没有降低门槛。

本机 macOS：Windows BGR 提供方接口兼容修正后，全套 **81 passed in 12.81s**，Ruff 和差异检查通过。新增回归检查实际接口形状下的色彩通道与转换错误报告。Windows 安装脚本和托管双平台检查已配置；运行结果以公开提交的 GitHub Actions 记录为准。托管检查不包含现场画面、本地模型性能或 72 小时运行。

## 最终工程检查

- `.venv/bin/python -m pytest -q`：**79 passed in 13.31s**（[日志](reports/final-pytest.log)）。
- wheel/sdist 构建、原生 Swift helper 打包回读、`pip check`、Ruff、`git diff --check` 与 shell 语法检查均通过（[构建日志](reports/final-build.log)）。
- 并发 store 读者、pending/orphan 分析终结、锁 stop/start、最终保留、每摄像头 heartbeat 与原始帧流式编码均有最终回归覆盖；见 [最终验证](reports/final-review-verification.md)。

## 合成运行时证据（非现场）

- 94 秒十路合成运行：10 个同时候选，10 个 181 帧/2 FPS/90.5 秒可解码 MP4，无 gap；峰值 controller+child RSS 约 632 MiB（[报告](reports/qa-ten-camera-smoke.md)）。
- 本地 `qwen3-vl:2b-instruct` 11435 队列：10 个合成候选，1 个在 15 秒内完成，9 个 watchdog 超时/删失。成功模型完成 p95 只有一个样本，不能外推；15,006 ms 是终态通知 p95，不能称为模型完成 p95（[报告](reports/qa-ten-camera-review-smoke.md)）。本地服务约 6.25 GiB RSS / 5.09 GiB VRAM，但这不是采购规格或报价。
- 独立并行诊断请求为 4；服务拒绝该模型并行，实际 runner 为 `-np 1`。预热且重复相同输入的 5/10 结果不构成 A/B 或全链路吞吐结论（[诊断](reports/review-capacity-diagnosis.md)）。

最后恢复和流式编码修正后执行了完整 79 项回归；94 秒容量报告来自对应较早工作树，没有冒称再次完成长测。测试窗口与本轮专用 Ollama 服务已关闭，需要演示时使用启动脚本重新启动。

## Gate

软件工程检查通过；整体生产/现场 Gate：**FAIL**。自动 OS 点击未交付并无条件禁用，十事件并发复核未满足 15 秒；手动客户端映射遇到每摄像头 heartbeat 不可靠时暂停。仍缺 Windows 后 Mac 真实客户端校准、独立数据集、正常班次误报暴露、100 次切换/故障和各系统 72 小时十路证据。Harness 不可用且未作为回退路径。硬件最小档和当前报价未提供：Windows 现场和软件突发队列瓶颈尚未解决，不能编造采购结论。
