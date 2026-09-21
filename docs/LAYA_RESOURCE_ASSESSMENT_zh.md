# Laya 资源评估（仅开发期影子试验，2026-09-21）

## 结论

Laya 与 TypeSafe Jev 的相似处是：两者面向类型化的快速文字决策；它**不是**图像/视频模型、视觉复核后端或 agent 运行时。因此不能直接改善漏拍、视频证据或多帧视觉判断。对当前 factory-monitor，建议位置是开发期的**影子文本分类器**：先用合成开发任务、脱敏测试日志评估任务分流和故障分类，与既有确定性规则及一个低成本 LLM 对照。它不得抑制告警、丢弃事件、修改人工复核/证据链，或控制客户端/摄像头。安装 Laya 不会自动改变当前 Codex 的模型选择，真正分派任务还需要调度适配；该适配本轮未实施。

本次未安装包、未下载权重、未调用 API、未做集成或性能试验。现场 Gate、十路验收与本地优先边界不变。

## 已钉住的第一方证据

核查时 `main` 为 [`42626c348753fbb17572a813127df2278a1ec527`](https://github.com/NandhaKishorM/laya/commit/42626c348753fbb17572a813127df2278a1ec527)（2026-09-20 17:44 UTC；包版本 0.3.4）。以下链接均固定到该提交，避免把未来 README 修改误读为本结论的证据。

- [README](https://github.com/NandhaKishorM/laya/blob/42626c348753fbb17572a813127df2278a1ec527/README.md) 与 [实际推理实现](https://github.com/NandhaKishorM/laya/blob/42626c348753fbb17572a813127df2278a1ec527/laya/agent.py)：输入是字符串、dict 或对话列表，经 tokenizer/编码器后一次前向计算；输出是 `choice` 概率、序数 `score` 或二值 `noul` 概率及 confidence。没有图像解码、视频帧、检测器、工具调用或行动规划代码。它不能从视觉漏检中恢复事实。
- [Router](https://github.com/NandhaKishorM/laya/blob/42626c348753fbb17572a813127df2278a1ec527/laya/router.py)：默认 checkpoint 是 English（421M、512 token）；非拉丁脚本路由到 multilingual（322M、1024 token）。`typed-decisions`（421M、1024 token）只有显式 `model/task` 或 `auto_task_detection=True` 且问题 ID **精确**匹配四个预设合成 workflow 才会自动选中。因此中文摘要通常会到 multilingual；它不是针对本项目中文事件 schema 的训练保证。长摘要会受 512/1024 token 上限和问题/选项的 head token 预算截断；`choice` 选项多时更差，官方基准建议不超过约 20 项。
- [Agent 装载路径](https://github.com/NandhaKishorM/laya/blob/42626c348753fbb17572a813127df2278a1ec527/laya/agent.py)：若未提供本地模型目录，首次加载会以 `snapshot_download` 从 Hugging Face 获取 checkpoint（可能使用 `HF_TOKEN`）；这是一项默认外部网络请求和权重下载。权重已在可信本地目录时，`system_one` 的实现只在本机 PyTorch 中执行，未见把 `state` 发送到外部服务的调用。任何试验应先断网复核本地路径，并禁止将真实画面、原始证据或可识别日志交给首次下载/外部服务链路。
- [设备选择](https://github.com/NandhaKishorM/laya/blob/42626c348753fbb17572a813127df2278a1ec527/laya/agent.py)：自动优先 CUDA，其次 Apple MPS，最后 CPU；指定但不可用的 CUDA/MPS 会回落 CPU；模型放置或推理内存失败也可回落 CPU。CPU/MPS 采用 float32；代码中的“约 200–500 ms、比 GPU 慢 10–15 倍”只是作者注释，不能当作本机延迟、内存或十路容量承诺。README 的 32.8–39.5 ms 单问/6.8–7.2 ms 每题批量数据来自单张 Tesla T4，不是当前 Windows/Mac 主机证明。
- [BENCHMARKS](https://github.com/NandhaKishorM/laya/blob/42626c348753fbb17572a813127df2278a1ec527/BENCHMARKS.md)：基础 `laya`/`laya-multilingual` 在 typed-decisions 零样本为 0.361/0.342，低于多数类 0.461；0.766 属于针对四个 workflow 微调的 typed-decisions checkpoint。多语模型在 MASSIVE `zh-CN` 只有 0.630（该基准、20 选项），不是工厂中文告警的验证。作者还明确指出 shipped base checkpoints 过度自信、multilingual 未拟合温度；confidence 不能直接作为自动升级/忽略阈值。Jev 的 0.727 和 236–276 ms 是第三方已发表数字，仓库没有 TypeSafe API 访问，提示词、样本与硬件不相同，不能构成 Laya 对 Jev 的直接优劣或本机成本比较。

## 可进入影子实验的前提与准入

仅在以下条件同时满足时，才做一个冻结、可回放的小试验；任一失败即停止并保持现有基线。

1. **输入与保密**：只用合成或已脱敏文本（规则命中、时间、摄像头匿名 ID、队列状态），不含图像、视频、原始录像、人员身份或可回识别证据；先证明权重已在批准的本地路径且运行过程无外联。保存输入 schema、模型/包/配置 hash、路由结果、截断标记、输出和人工真值。
2. **对照与分割**：冻结调试集和独立中文留出集；同一文本同时交给确定性规则、Laya、既有低成本 LLM 和人工标注。调参、温度拟合和阈值选择不得接触留出集；每个 `choice` 保持少量、互斥选项，记录选项顺序变化。
3. **安全指标**：预先写清“漏升级/漏人工复核”的可接受上限。Laya 在独立中文集的漏升级率不得高于确定性规则或当前 LLM 基线；unknown、超时、截断和低置信度均视为需要人工复核，绝不当作无事件。
4. **真实净成本**：先在实际开发电脑上比较被节省的模型调用、分类错误导致的返工、加载与执行时间、CPU/GPU/MPS 内存。如果未来与监控进程共用目标 Windows/Mac，再用同一十路回放负载测量资源争用、p50/p95、队列 deadline、冷启动和失败重试。只有计入这些开销后端到端**净节省**为正，且没有恶化原任务质量、监控延迟或漏升级率，才值得保留；T4 图表和单问吞吐不算证据。
5. **输出边界**：即使通过，也仅用于开发分析/建议标签；由现有规则、人工复核和原始证据决定告警。要将其接入运行时，须另行设计、独立审查并完成现场/跨平台验收。

## 判断

把 Laya 当成“Jev 式、可在本地运行的文本决策候选”是合理的；把它当成监控视觉模型、低成本替代视觉复核，或以其 confidence 自动跳过人工复核，则是错误且风险很高。当前最有价值的动作不是接入，而是用上述影子对照证明它是否能在中文、短上下文、真实资源争用下带来可量化的净收益。
