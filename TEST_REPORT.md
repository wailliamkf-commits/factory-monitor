# 测试与证据报告

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
