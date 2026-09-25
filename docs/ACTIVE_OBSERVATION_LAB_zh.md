# 主动观察实验包：Windows / Mac

本包与此前约 6.6 GB 的 Windows 离线候选包分开存放。它是**可运行的合成实验与架构文档**，不是现场软件升级；不会覆盖原项目、读取屏幕、启动模型或点击监控客户端。运行时仍以原候选包为准，自动放大尚未接入。

## Windows 三步运行

1. 将新 ZIP 解压到新的文件夹，保留此前离线候选项目和现场修复副本。
2. 双击 `scripts\run-active-observation-lab.cmd`。如果出现选择窗口，选此前已准备好的项目里 `.venv\Scripts\python.exe`，不要选 Mac 环境，也不要选 Ollama。要求 Python 3.12、NumPy、OpenCV；这些来自此前离线候选环境。本包不下载或安装依赖。
3. 运行结束后查看 `results\结果说明.md` 和 `results\experiment.json`。保留失败输出；`PASS` 只表示这些合成逻辑检查通过，现场始终标为 `NOT_TESTED`。

实施人员也可以指定解释器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-active-observation-lab.ps1 -PythonPath "D:\你的候选项目\.venv\Scripts\python.exe"
```

Mac 在实验包目录中用已有项目环境运行：

```sh
/你的候选项目/.venv/bin/python scripts/experiment_active_observation.py --output results
```

## 实验内容与结果边界

- 无人像时，固定参考中的物品矩形移除形成一次持续变化提示；这不是物品类别或偷窃识别。
- 16 路安静镜头在首轮依次得到巡视建议，热点请求不会无限堆积。真正软件仍最多配置 10 路；16 路是调度模块与规划实验范围。
- 错镜头、旧帧、返回失败和超时使调度停在故障态，合成回执不能授予 OS 控制。
- 九/十六宫格像素、源分辨率与总览缺失预算计算，输出未验证假设。不会从“全屏”推断“高清”。
- CPU 时耗只包含 16 个小图场景变化观察器，不含实际采集、YOLO、Qwen、录像或网络。

当前仍缺：Windows 客户端实际校准、每路真实画质与心跳、4060 持续负载、模型准确率、十路真人工复核、15–30 分钟现场演示及 72 小时验证。包内 `evidence/` 是开发 Mac 的记录；在 Windows 运行生成的 `results/` 也只是当地合成实验，不能替代真实画面验收。

## 文件核验

`MANIFEST.json` 列出打包源码提交、文件大小和 SHA-256。ZIP 外的 `.sha256` 与 `.verification.json` 提供整体校验与回读结果。本包只有白名单源码、文档、测试和明确合成的实验记录，不包含监控画面、账号或密钥。

架构与能力边界见 [主报告](ACTIVE_OBSERVATION_ARCHITECTURE_20260925_zh.md)，外部方案证据见 [异构加速及 Frigate/go2rtc 评估](HETEROGENEOUS_STABILITY_20260925_zh.md)。
