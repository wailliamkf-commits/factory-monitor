# 架构升级调研（仅建议，2026-09-20）

2026-09-21 新资源：[Laya 专项评估](LAYA_RESOURCE_ASSESSMENT_zh.md)。仅列入 OPTIONAL 的开发任务分流/日志分类影子对照；已核对本地推理代码和模型限制，未安装或接入实时监控链路。

## 结论

当前 Gate 仍为 **FAIL**：真实 Windows/Mac 客户端、独立留出集、72 小时十路等证据未完成，不能宣称吞吐或成本收益。本轮最低成本路径不是更换模型或重写框架，而是先减少送入本地 Ollama 的像素/帧数并量化每阶段耗时。现有 `YoloPersonDetector` 按摄像头各建一个 YOLO 实例（`inference.py:58-68`）；review 只有一个串行进程、每候选最多 6 帧（`runtime.py:385-389, 502, 750`）。已有 schema 校验、checkpoint/健康事件、原始证据录像、队列 deadline；它们不是本次新增能力。

|来源（已钉版本）|可直接借鉴|不应现在采用|
|---|---|---|
|[NVlabs/SoL-Pi](https://github.com/NVlabs/SoL-Pi/tree/bd005888b9b8a3fcdb511feb91fc27d3dfa8f2b1)（main `bd00588`）|ObservationPack 的“原文可分页回读”、证据保留 reducer 的可核验摘要、按调用/输入/输出记成本计数，作为**开发调试 harness**理念|它是 Pi `0.85.1` 的独立扩展，需 Node/Pi；不是 Codex/Ollama 推理后端或吞吐插件。不要安装，不把本地视频、事件证据或日志发往远程 reducer；不启用自动压缩来替代审计。|
|[Build-A-Large-Language-Model-CN](https://github.com/skindhu/Build-A-Large-Language-Model-CN/tree/7074262f7d042de570aa157851fcfeb69d4658ce)（main `7074262`）|Transformer、数据、微调/LoRA 的学习材料；未来已有标注集时可帮助审查训练假设|其 README 定位为教材中文翻译/实践代码，不是视觉监控 harness、部署或推理优化方案；当前不建议从零训练；微调需要另备训练数据，不能复用留出验收集。|

## 有界实验（只用本地、禁远程图片）

以当前配置和同一冻结的调试事件集做基线；独立验收集不参与调参，选定方案后才用于验收。调试期间，保留每个事件的原视频、原帧时间戳、ROI 坐标、模型/配置 hash、决策和失败原因。逐步比较：

1. **基线**：记录 capture→预处理→检测→入队→编码→Ollama→落库时延、队列深度、超时率、每事件帧/像素/模型调用与 token/耗时代理成本。
2. **关键帧+ROI**：只在候选时取覆盖触发前后的关键帧；ROI 与带上下文的全帧并存，原视频不改、不用合成细节。量化召回/误报、人工复核一致性和成本。
3. **检测和服务端对照实验**：先单独比较共享检测权重/有界批处理，保持 10 个独立 ByteTrack 状态，绝不混摄像头身份；再单独比较复核后端或并行能力。每次只改变一个变量，保留原 deadline 锚点和超时记录，不购买 API。

通过条件是独立留出集无泄漏、每项证据可回放、关键安全指标不退化且成本计数下降；否则回退基线。SoL-Pi 的原始材料强调 opt-in、保留原始 observation，并警告 reducer 可能经 Pi 认证发送日志，恰好支持上述本地优先边界。

## 原始资料

- SoL-Pi [README](https://github.com/NVlabs/SoL-Pi/blob/bd005888b9b8a3fcdb511feb91fc27d3dfa8f2b1/README.md)、[配置](https://github.com/NVlabs/SoL-Pi/blob/bd005888b9b8a3fcdb511feb91fc27d3dfa8f2b1/docs/configuration.md)、[安全说明](https://github.com/NVlabs/SoL-Pi/blob/bd005888b9b8a3fcdb511feb91fc27d3dfa8f2b1/SECURITY.md)。
- 中文项目 [README](https://github.com/skindhu/Build-A-Large-Language-Model-CN/blob/7074262f7d042de570aa157851fcfeb69d4658ce/README.md)；其指向的原书配套 [LLMs-from-scratch](https://github.com/rasbt/LLMs-from-scratch)。

## 根代理补充判断

- 现场输入信息优先：确认现有客户端是否允许独立细节窗口，或录像机是否有获授权的独立读取接口。后者仅为可选适配，不要求换摄像头，也不替代现有屏幕采集基线。主窗口放大仍需记九路盲区；仅放大像素无法生成真实取证细节。
- 复核接口可拆为统一 ReviewBackend，保留 Ollama 基线；真实并行必须由模型后端与设备证明。队列变多不会凭空增加算力。冷启动与预热、不同事件与重复帧分开测量；不得重复同图制造缓存收益并外推现场吞吐。
- Windows Intel 检测侧可试 [Ultralytics OpenVINO 导出](https://docs.ultralytics.com/integrations/openvino)，当前 YOLO11 导出、设备驱动和 UHD 730 性能均需实测。Mac 复核侧可试 [MLX-VLM](https://github.com/Blaizzy/mlx-vlm)，只作为对照候选，不承诺提升。未来有适合的 GPU 部署环境时，才评估 [Qwen 官方支持的 vLLM/SGLang 路径](https://github.com/QwenLM/Qwen3-VL#deployment)。不同时引入所有后端。
- [Qwen3-VL 官方处理器](https://github.com/QwenLM/Qwen3-VL#pixel-control-via-official-processor) 提供图像/视频像素预算与批处理方法；该接口不是现有 Ollama API 的直接参数，需由具体后端适配并验证真实视觉 token/延迟。
- 保持原有候选 p95 ≤3 秒、模型复核 p95 ≤15 秒、召回/误报及十路要求。完成率、未知和超时均计入评价；输入压缩不得以漏检换速度。开发额度单独记录，不能与现场本地模型耗时混算。

本记录为升级建议，未安装这些框架，未执行性能对照实验，未更改已发布程序。

## 2026-09-21 补充：异构 GPU PD 与 Jev（候选，未接入）

- [heterogeneous-gpu-pd-lab](https://github.com/Soulmate-Halo/heterogeneous-gpu-pd-lab)：作者公开的是异构设备协作、Prefill/Decode 等实验报告与数据；README 明确部署命令、补丁、端点和层分配策略仍未公开。其现有硬件/模型/输入长度与本项目的 2B 多帧视觉复核不同，不能外推加速比或据此采购。列入 BACKLOG；进入实验前须证明本地瓶颈确在可拆分计算、后端支持当前视觉模型、部署可复现，且端到端收益超过传输/同步成本。现阶段借鉴分阶段测时与同负载对照方法。
- Jev 名称待用户确认。若指 [TypeSafe Jev](https://docs.typesafe.ai/introduction)，它提供 Choice/Score/Noul 等类型化决策；[社区 CLI](https://github.com/okooo5km/jev) 经 TypeSafe API 或 OpenRouter 调用，装技能不等于模型已本地化。暂列 OPTIONAL 的开发辅助实验：仅合成/脱敏任务文本，比较固定规则、已有低成本模型和 Jev 的路由错误、漏升级、延迟及实际调用/返工总成本。未确认具体技能、数据出境与调用预算前不执行外部请求。
- 初期只能影子评估，不能据其概率丢弃事件、修改告警或驱动客户端点击。概率须在本任务上验证，结构化输出不代表事实正确；上游视觉遗漏也不能靠下游文字分类找回。当前云端画面复核关闭、十路验收门槛与现场 Gate 不变。
