# 状态（工程预览 v0.1）

公开迁移准备：新增 Windows 安装脚本、交接技能和双平台 CI；修正 Windows 采集提供方的 BGR 转换接口。迁移修正后的本机回归为 **81 passed in 12.81s**，Ruff 与差异检查通过。以下 79 项结果及容量报告为此前工程基线；公开提交的托管检查以 GitHub Actions 为准，均不替代现场验收。见 [Windows 交接](docs/WINDOWS_HANDOFF_zh.md) 和 [公开发布边界](docs/PUBLICATION.md)。

T1–T5 工程检查已完成：最终全套测试为 **79 passed in 13.31s**，wheel/sdist 已构建并回读到原生 Swift helper，`pip check`、Ruff、差异检查和 shell 语法检查通过。详细证据见 [最终测试日志](reports/final-pytest.log) 与 [最终验证](reports/final-review-verification.md)。

软件链路的合成容量证据：94 秒十路 demo 在同一时刻产生 10 个候选；10 个证据 MP4 均为 181 帧、2 FPS、90.5 秒、无 gap，控制器及子进程峰值 RSS 约 632 MiB。真实本地 Qwen 队列测量中，10 个合成候选仅 1 个在 15 秒内完成，9 个为已保留的超时/删失结果；15,006 ms 是 watchdog 终态通知 p95，不是模型完成 p95。见 [十路证据测量](reports/qa-ten-camera-smoke.md) 与 [Qwen 队列测量](reports/qa-ten-camera-review-smoke.md)。

独立诊断请求并行度 4，但服务明确拒绝当前 qwen3vl 并行，实际 runner 为 `-np 1`。预热且重复相同输入时的 5/10 结果不构成 A/B 结论；本地服务 RSS 约 6.25 GiB、VRAM 约 5.09 GiB。已确认运行时单 worker 和模型服务单槽两处串行限制，不能据此给出硬件采购建议或价格。当前最小必要硬件档与实时报价均**未提供**，原因是 Windows 现场证据缺失且软件瓶颈尚未解决。

整体 Gate：**FAIL**。自动跨视图 OS 客户端点击功能**未交付**，并且在客户端特定命中、遮挡和 DPI 证明前无条件禁用。实时手动客户端映射会在不可靠/缺失的每摄像头 heartbeat 下暂停规则连续性。待完成：实际 Windows 后实际 Mac 客户端校准；每类 50 正/100 负独立数据；正常班次误报暴露；100 次切换与故障注入；各系统 72 小时十路运行。

职责：根 Astra 负责关键 Gate 与最终集成；Terra 负责常规核心/GUI/CLI；Sol 负责运行时与诊断。Harness 不可用，未纳入交付或验收。
