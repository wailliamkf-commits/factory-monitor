# 状态（工程预览 v0.1）

本轮仓库实现：已在独立审查分支导入已核验的 9 月 23 日停止/证据/清理修复，修正 WGC 时间戳并新增有界诊断工具；非 GUI 回归 140 passed / 1 skipped，退出码补修后定向 18 passed，构建与静态检查通过。Mac GUI 插件故障仍需独立环境验证；分支不含 9 月 24 日最新查看保护，不能覆盖现场副本。

2026-09-25 显示器对照更新：同 HWND 仍单回调；一次授权的显示器 WGC 在 8.109 秒有 248 回调/248 个捕获时间戳（跨度 7.703 秒、最大间隔 .047 秒），但未证明视频 ROI 新鲜或 248 视频 fps。当前入口为 [Windows 采集恢复与草案审查交接](docs/SEETONG_RECOVERY_2026-09-25_zh.md)：先核对 raw 日志/库版本/最新源码，主线改为授权后的 display WGC + Seetong ROI 校准、遮挡保护与 5 分钟单路新鲜度验证；成功同轮进 1→3→10、模型/提醒/录像。正式裁剪/guard 尚未实现或验证，分支仍只供 draft 审查，现场 Gate FAIL。

2026-09-23 大陆无 VPN 补充：固定本机 Ollama `qwen3-vl:2b-instruct`、项目端口 11435、禁云及无云 API Key 的主方案，现有 Windows 环境优先复用，缺失资源按固定 Windows 本地包交付。见 [大陆部署说明](docs/MAINLAND_DEPLOYMENT_zh.md) 与 [当前实景执行任务单](docs/superpowers/plans/2026-09-23-phase2b-to-demo.md)。介绍片改为 15—30 分钟，建议约 20 分钟，包含真实十路分析、可见提醒和连续 300 秒无人过程。只完成文档及现有脚本/接口核对；没有制作完成离线安装包、修改业务代码、连接 Windows、测试大陆线路或生成新视频。正式现场 Gate 仍为 FAIL。

2026-09-23 用户明确调整下一步优先级：**先交付十路 Seetong 真实输入 → 人员检测/跟踪与规则 → 本地视觉复核 → 屏幕提醒 → 实际功能录屏。** 不再把全部 Q1—Q7、超过 512 条逐帧持久账目和完整回传审计设为有保护的短期实景演示前置。隔离数据、事件保存、可停止、故障可见及防止触发未验证清理的有效容量保护仍必须先落实；账目/恢复/循环清理的完整实现随功能推进，并在无人值守长测前完成。采用已更新的 [功能优先执行任务单](docs/superpowers/plans/2026-09-23-phase2b-to-demo.md)，覆盖下方旧顺序；冻结的完整 V1 验收要求未降低。旧 Mac 合成录屏仅保留为原型资料，不满足新增的实际场景录屏任务。本轮为重新安排与源码静态核对，尚未在 Windows 开始该功能阶段。

2026-09-23 第 2 步与补查报告已对照审查：采纳补查对前次 PASS 的更正。111 passed / 2 skipped / 1 deselected、单批十路各 181 帧和已实现的跨进程 EOS 屏障保留为报告限定结果；完整 Q1—Q7 仍 **FAIL**。此次仅收到两份报告和截图，新候选源码、原始日志与媒体仍未回传。优先补持久逐项账目、可恢复清理，再验证原参数 5+5 重叠、第 11 件拒收、独立视频内容反例和 Windows 可见桌面。已形成 [第 2B 至现场、录屏和长测的条件推进单](docs/superpowers/plans/2026-09-23-phase2b-to-demo.md)：输入与 Gate 齐备才进入下一已授权阶段，不以重复空跑代替实现。本轮未修改业务源码、未控制 Windows 或发布修复版；本机审查记录在 `reports/local/phase2b-intake-20260923/REVIEW_zh.md`。先行功能录屏使用旧 Mac 预览与合成输入，视觉模型关闭，不算真实动作识别或新版 Windows 验收。以下保留历史状态。

2026-09-23 停止协议第 1 步报告已审查：Windows 端报告顺序排空、独立 drained 确认、写入所有权、关窗及清理失败重试已修正，最终 95 passed / 2 skipped / 1 deselected（真实 YOLO）。本机只收到报告及截图，报告中的旧补丁指纹与此前回传包匹配；新候选三个源码、测试与原始日志尚未回传，不登记为本机独立验收。第 1 步 PASS 为对方限定范围结论，完整 P3/现场 Gate 保持 FAIL。已整理 [第 2 步执行任务单](docs/superpowers/plans/2026-09-23-stop-accounting-phase2.md)：先封存新候选工件，继续精确账目、非零合成帧积压、跨进程晚命令、受控重叠/容量及重启恢复；不重写旧实现，不用旧 30 分钟替新代码验收。本机未改业务、未运行 Windows、未启动模型/Seetong或发布新版本。最新修复仍在 Windows 独立副本，GitHub 主版本尚不包含它。报告核验见本机 `reports/local/stop-protocol-step1-review-20260923/REVIEW_zh.md`；以下保留历史状态。

2026-09-22 第二轮完整回传包已接收并完成离线核验：3,767 个 payload 文件的 SHA-256 与大小均匹配，manifest 自哈希匹配；root 在 Mac 独立完整解码现存 20 个 MP4（共 3,620 帧）及 3,620 个原帧 JPEG，20 段视频的 hash、帧数和时间戳均与原始逐批审计一致。84 个源发布文件与本地 Git `4abaa0ba60ff61af4418a1f27be0d0941a67263b` 逐项内容相同，提供的修复文件哈希也与记录一致。150 个 ID、15 批及 357 条资源记录自洽；第一批来自合成规则，后 14 批/140 个候选为测试注入，模型关闭且批次错开。故原报告的**合成阶段 PASS**可按此已核验范围登记；它不表示 150 段当前均可播放（130 段旧媒体仅有历史审计）、停止时零丢弃，亦不表示已完成新的 Windows 测试、修复正式接纳或现场 Gate。新架构发现为 capture 对 evidence queue 也调用 `cancel_join_thread`，退出机制尚缺有序 drain/ACK 与精确丢失账目；`scandir` 的局部性能改进证据成立，但仍是全目录扫描。修复正式接纳与整体现场 Gate 继续 **FAIL**。详见本机忽略目录 `reports/local/soak2-package-audit-20260922/REVIEW_zh.md`；以下保留历史记录。

2026-09-22 持续运行交接：用户确认另一台 Windows 正在执行半小时**合成画面**测试。对方报告此前在独立修复副本改过两处业务代码，分别处理录像慢和停止卡住，原安装未覆盖；本次长测/收尾仅增加测试工具、日志和报告，没有再改业务代码或降低标准。当前尚未回收核验这两处差异、测试工具、最终日志及事件负载记录，不能登记修复通过；先审计现有修复与结果，再按缺项补测，不自动从头重做 P0/P1。新增 [稳定运行与版本交付流程](docs/STABLE_OPERATION_zh.md) 和 [持续测试记录模板](scripts/acceptance/stability-run.template.json)，将固定版本源码交接、测试负载、更新/回退及后续长测衔接起来。当前提供源码与项目内环境，尚无独立 EXE/便携成品；本轮未修改采集、证据、模型或运行时，也未干预另一台设备。Laya/Jev 保留开发期可选对照，未接入监控链路。整体 Gate 继续 FAIL。

2026-09-22 新证据：目标 Windows 提供的十路**合成**复测为 **FAIL**。本机离线复算并完整解码十段交付视频：每段 101 帧/50.5 秒/35 个缺口，capture 停止后被强制终止。包内 1,059 项文件校验通过，33 项源码指纹与发布版及当前运行时代码一致。旧托管 CI 通过不代表该设备通过。见 [复测审计与下一步方案](docs/WINDOWS_RETEST_AUDIT_2026-09-22_zh.md)：先定位证据热点和退出栈；队列上报 81 的口径存在歧义，不能当作精确或严格下限丢包数。本轮未运行 Windows、未修改业务代码、未接入 Seetong；整体 Gate 继续 FAIL。

2026-09-21 客户端补充：用户提供 Seetong 1.0.13.4 安装文件，RAR 全量读取及已解压 EXE 哈希一致；确认是 Windows 安装器，不能在当前 Apple M5 Mac 原生运行。苹果商店列出可供 Apple 芯片 Mac 获取的 iPad 版本，但十路显示与采集未验证。见 [Seetong 预检](docs/SEETONG_CLIENT_PREFLIGHT_zh.md)。尚未安装客户端、登录或读取摄像头，现场测试仍待开始指令。

Laya 已完成 [资料及源代码评估](docs/LAYA_RESOURCE_ASSESSMENT_zh.md)，仅作为开发期合成/脱敏文字任务的可选影子分类候选；未下载权重、调用推理服务或接入监控，不能替代视觉复核或证明十五秒门槛达标。

2026-09-21：已准备 [现场测试矩阵](docs/superpowers/plans/2026-09-21-field-test-matrix.md) 与会话记录模板。当前等待用户获得在线摄像头画面并明确下达开始指令；未启动新的现场采集、跨机服务或付费 API 调用。优化路线中尚未实现的组件须先通过各自工程检查，再进入现场对照；原验收门槛不变。

**公开版本双平台工程检查已通过。** 代码提交 `379d7a9` 的 [GitHub Actions 实测](https://github.com/wailliamkf-commits/factory-monitor/actions/runs/35515661344)：Windows 78 passed / 3 skipped，macOS 80 passed / 1 skipped；两边均完成 94 秒十路合成并发证据检查及 wheel/sdist 构建。Windows 实际执行了 PowerShell 5.1 安装脚本。跳过项为平台专属检查与未随仓库发布的 YOLO 样本。真实 Windows 客户端、模型吞吐与现场 Gate 仍未通过。

公开迁移准备：新增 Windows 安装脚本、交接技能和双平台 CI；修正 Windows 采集提供方的 BGR 转换接口。迁移修正后的本机回归为 **81 passed in 12.81s**，Ruff 与差异检查通过。以下 79 项结果及容量报告为此前工程基线；公开提交的托管检查以 GitHub Actions 为准，均不替代现场验收。见 [Windows 交接](docs/WINDOWS_HANDOFF_zh.md) 和 [公开发布边界](docs/PUBLICATION.md)。

T1–T5 工程检查已完成：最终全套测试为 **79 passed in 13.31s**，wheel/sdist 已构建并回读到原生 Swift helper，`pip check`、Ruff、差异检查和 shell 语法检查通过。详细证据见 [最终测试日志](reports/final-pytest.log) 与 [最终验证](reports/final-review-verification.md)。

软件链路的合成容量证据：94 秒十路 demo 在同一时刻产生 10 个候选；10 个证据 MP4 均为 181 帧、2 FPS、90.5 秒、无 gap，控制器及子进程峰值 RSS 约 632 MiB。真实本地 Qwen 队列测量中，10 个合成候选仅 1 个在 15 秒内完成，9 个为已保留的超时/删失结果；15,006 ms 是 watchdog 终态通知 p95，不是模型完成 p95。见 [十路证据测量](reports/qa-ten-camera-smoke.md) 与 [Qwen 队列测量](reports/qa-ten-camera-review-smoke.md)。

独立诊断请求并行度 4，但服务明确拒绝当前 qwen3vl 并行，实际 runner 为 `-np 1`。预热且重复相同输入时的 5/10 结果不构成 A/B 结论；本地服务 RSS 约 6.25 GiB、VRAM 约 5.09 GiB。已确认运行时单 worker 和模型服务单槽两处串行限制，不能据此给出硬件采购建议或价格。当前最小必要硬件档与实时报价均**未提供**，原因是 Windows 现场证据缺失且软件瓶颈尚未解决。

整体 Gate：**FAIL**。自动跨视图 OS 客户端点击功能**未交付**，并且在客户端特定命中、遮挡和 DPI 证明前无条件禁用。实时手动客户端映射会在不可靠/缺失的每摄像头 heartbeat 下暂停规则连续性。待完成：实际 Windows 后实际 Mac 客户端校准；每类 50 正/100 负独立数据；正常班次误报暴露；100 次切换与故障注入；各系统 72 小时十路运行。

职责：根 Astra 负责关键 Gate 与最终集成；Terra 负责常规核心/GUI/CLI；Sol 负责运行时与诊断。Harness 不可用，未纳入交付或验收。
