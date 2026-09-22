# 状态（工程预览 v0.1）

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
