# 异构 GPU / Prefill-Decode 分离方案对十路监控的适配评估

日期：2026-09-25
评估范围：`Soulmate-Halo/heterogeneous-gpu-pd-lab`，官方 vLLM PD 与 SGLang PD 两个替代方向。只读检查；未安装或运行仓库代码、未下载模型、未接触生产摄像头或凭据。

## 结论

这份异构实验仓库值得借鉴的是“按 Prefill/Decode 瓶颈分配设备”和严格标注吞吐口径的思路；它不是能拿来部署十路监控的项目。审查的公开快照没有可运行推理服务、安装/启动配置或测试套件，核心高性能跨引擎部署命令、补丁与分层方案明确没有公开。其最新演示需要两张 RTX 6000D 加四台 DGX Spark；这与一张 8GB RTX 4060、约一万元整机的约束不在同一数量级。

vLLM/SGLang 的PD分离也没有解决眼前已观察到的问题：Mac M5上的十个Qwen候选由公开公交静帧裁成不同ROI、每件两帧，并非十路实时摄像头；含排队15秒内完成2件、8件超时，完成样本太少，不能报告完成p95。两件结果都误报这张公开公交负例。阶段分离优化资源分工和队列干扰，不能自动提高视觉判断正确率；单4060又没有第二张GPU承接另一阶段，反而可能增加进程、模型副本、KV搬运与显存压力。当前最可信的路线是先把单GPU链路做成有界、可恢复、可审计的服务：每路最多保留一个最新候选，按危险度/新鲜度调度，限制并行与输入尺寸，给请求设硬截止时间，隔离超时/崩溃并保留原始失败计数。之后用真实多路负载证明瓶颈仍是模型Prefill或Decode，再评估拆分到第二个计算设备。

## 证据口径

- **已验证代码/仓库事实**：审阅本地只读克隆的 README、中文 README、CHANGELOG、`VERSION`、逐批实验记录、CSV/JSON证据和全部跟踪文件清单；以快照 `327c326d85170921113607750e4a3a1a320a9335` 为准。快照页面：[该提交](https://github.com/Soulmate-Halo/heterogeneous-gpu-pd-lab/tree/327c326d85170921113607750e4a3a1a320a9335)。
- **作者报告/声明**：仓库中的实验数据、成功率、设备规格和性能数值由作者记录。JSON列出原始报告/逐请求文件的路径与SHA-256，但这些源文件不随当前仓库提供；本次未能从该仓库独立复算完整测量过程。
- **推断**：由拓扑、显存与运行时要求推演本项目适配性，须在目标机器验证。
- **现场待测**：Windows/Mac十路屏幕捕获、无VPN装载本地模型、固定样本判别率、队列延迟、崩溃恢复、长时间显存/内存增长。当前没有新的设备实测。

## 目标仓库：代码、配置、实验可信边界

### 实际检查结果

- 快照提交 `327c326d85170921113607750e4a3a1a320a9335`；最近提交日期为2026-09-21，标题是更新DS4.1 Flash V8部署章节。`VERSION`仍为`1.6`，首页记录另有V8 seq32后续实验。
- 跟踪文件主体是README/变更记录、实验报告、CSV和脱敏JSON；Python文件只有两个绘图脚本 `make_base_architecture.py`、`make_dense_region_structure.py`。脚本生成图表，不实现模型推理或模型服务。
- 仓库没有`tests/`、pytest/unittest套件、依赖锁定文件、服务启动脚本、容器文件或可复现的vLLM/SGLang launch config。JSON确实记录部分关键配置，但属于结果说明，不是部署配置。
- 首页明确称V8用双6000D前段vLLM TP2、四Spark后段SGLang TP4/EP4，以跨引擎NIXL交接；且README明言部署命令、代码补丁、服务地址和切层策略不公开。读者无法只靠该仓库复现V8服务。
- 仓库根目录没有LICENSE/COPYING文件或README许可声明。本报告不把“公开可读”推断为允许复用、修改或再分发；在授权不明时应按未授予开源许可处理。

### 作者记录的数据说明了什么

- README把C16/C24/C32每档一批都过16,000 tok/s称为当前Prefill稳定表现，同时明确“稳定”只指三个并发档各一批，不是长时间重复压测。短代码C32的442.02 tok/s来自另一负载；不是同一工作负载的阶段吞吐。
- seq32证据JSON标明模型DeepSeek-V4.1-Flash、硬件“2 RTX 6000Dpro + 4 DGX Spark”、架构为跨引擎PP2；C32矩阵216/216请求成功，另有4个正确性门和16次热身后请求。JSON也明示每档一次、非长测、没有匹配V7/H20同口径对照。原始证据路径/哈希可供作者本地回溯，但原始请求和指标文件不在快照内。
- 因而这些数据是有方法说明、可进一步审计的作者实验记录；不是独立复现结果，也没有给本地4060、Windows、Qwen3-VL、各路独立ROI图像或无VPN部署提供实测证据。

### 与十路监控的贴合度

目标系统当前是十路屏幕画面经检测筛选后，少量候选再交给视觉语言模型；不是长文本Prefill服务器。M5之前的Qwen实测中，十件并发候选每件两帧，图像来自公开公交静帧的不同裁剪，不是十路实时摄像头；其中2件在含排队的15秒门槛内完成、8件超时，完成延迟样本不足以估算完成p95，2件完成结果都把公开公交负例误报。PD分离通常针对Prefill吞吐/首字延迟与Decode持续生成之间资源争用；即使吞吐提高，也不直接提高公交图像判断正确率。

| 项目 | 目标仓库实际证据 | 对本项目的判断 |
|---|---|---|
| 是否有可复用服务实现 | 没有：推理代码、启动脚本与部署补丁未随库发布 | 只能借架构观点，不能集成该仓库 |
| 是否分离prefill与decode | 作者报告V7同引擎vLLM PD；V8为vLLM→SGLang跨引擎KV/状态适配 | 在六卡及高端显存设备上有作者报告，代码和适配层不可复现 |
| 是否实证支持视觉输入的VLM PD | 没有。仓库模型实验集中在文本LLM；列出的DS4.1实验并非Qwen3-VL图像输入 | 不能声称支持各路独立ROI图像的P/D交接 |
| Windows/Mac | 实验是多GPU服务器架构；无Windows/Mac启动或恢复测试 | 无兼容证据 |
| 显存与成本 | 最新V8基线为2×RTX 6000Dpro + 4×DGX Spark；README已标注主机成本未齐 | 远超8GB/约万元约束，且没有4060轻量拓扑证据 |
| 网络 | NIXL跨引擎KV状态传输；部署具体接口和网络参数未公开 | 两进程同卡也需要本地IPC/设备内存传输与状态协调，不会凭空多出计算资源；局域网/跨机还增加故障面 |
| 故障恢复 | 结果报告提供成功计数，未见可审查的代码实现、自动重试/清理策略或长测 | 服务级恢复行为未知，必须另做故障注入 |

## 官方替代方案一：vLLM disaggregated prefilling

### 一手资料与成熟度

[vLLM官方PD文档](https://docs.vllm.ai/en/latest/features/disagg_prefill/)明确把该功能标记为“experimental and subject to change”，并直说disaggregated prefilling **does not improve throughput**；其用途主要是分开调TTFT和ITL、减少Prefill打断Decode。文档结构是运行两个vLLM实例，并用KV connector传KV cache。官方[LMCache单机示例](https://github.com/vllm-project/vllm/tree/main/examples/disaggregated/lmcache)的前置条件写明“at least 2 GPUs”，另需LMCache、NIXL和可用模型权重。

当前vLLM主分支只作版本定位，提交 `8b365ff949260dbfee92bb319b45333eaff03f6a`；项目采用Apache-2.0，见[官方LICENSE](https://github.com/vllm-project/vllm/blob/main/LICENSE)。该分支会继续变化，不能把它等同于已冻结的生产版本。

### VLM与平台边界

vLLM有独立的[多模态输入接口文档](https://docs.vllm.ai/en/latest/features/multimodal_inputs/)，列有Qwen3-VL示例；这证明通用vLLM支持部分视觉模型/图像请求。PD文档说明prefill和decode会各自处理`messages`、token化，并通过connector传KV，但没有给出Qwen3-VL图像输入跨实例PD的端到端示例或通过的图像正确性测试。因此“支持VLM”与“本项目VLM图像可在PD模式无损运行”不是同一证据。

官方[GPU安装页](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/)要求Linux作为CUDA运行平台；RTX 4060的计算能力高于页面列出的CUDA最低要求，但8GB容量仍是独立约束。Windows不是原生支持，官方路径为WSL。Apple Silicon走vLLM-Metal/MLX优化模型，是另一运行路径，不是与NVIDIA CUDA PD配置无缝共用。故无法满足“Windows另机 + Mac兼容的同一服务配置”。

### 单张4060判断

**不是可用的PD加速方案。** 两个角色若在单张卡上各运行一份完整VLM，会竞争同一GPU且很可能额外装载模型权重；若只启动一个进程同时承担两阶段，则回到同GPU调度，不会得到另一块GPU的计算。官方示例需要两GPU，vLLM本身也提示PD不增加吞吐。8GB下复制模型/缓存很可能造成显存溢出或将KV空间压得更小，这是适配推断，须用目标模型测量确认。

## 官方替代方案二：SGLang PD disaggregation

### 一手资料与成熟度

[SGLang官方PD文档](https://docs.sglang.ai/advanced_features/pd_disaggregation.html)给出prefill worker、decode worker和PD router的部署方式，支持NIXL或Mooncake搬运KV；单机示例为prefill服务占一块GPU、decode服务使用`--base-gpu-id 1`，再由router配对。官方也说明Prefill/Decode是独立服务进程，stateful Responses、后台响应与内建web/code工具在PD模式有限制。

当前SGLang主分支用于版本定位的提交为 `37ebcac3c4be4a7704cff5a0d53bcdf721a8673a`；许可证为Apache-2.0，见[官方LICENSE](https://github.com/sgl-project/sglang/blob/main/LICENSE)。文档描述的是可用能力和规模化路由，不代表对这套十路客户端链路的现场认证。项目在持续发布，但PD和多模态兼容性应按具体版本/模型验收。

### VLM、网络与故障恢复边界

SGLang单独公开了[Qwen3-VL视觉编码DP文档](https://github.com/sgl-project/sglang/blob/main/docs_new/docs/advanced_features/dp_for_multi_modal_encoder.mdx)，列出Qwen3-VL支持，并说明该功能是复制模型处理不同批次的视觉编码并行；示例是`--tp 2`。它不是prefill/decode KV迁移的证明。PD文档所给的单机样例是文本Llama模型，多机样例需网络初始化、NIXL/Mooncake传输配置和router。未找到官方文档证明Qwen3-VL-2B的图片请求在PD模式已过端到端精度和故障恢复门槛。

官方路由器支持负载分发，文档称可为大规模PD部署提供负载均衡与容错；但是此能力意味着增加router、两个服务进程、健康状态和传输backend。发生Prefill已完成而KV交接/Decode失败时，需处理请求归属、部分状态丢弃、重试及超时；公开页面并未替本项目定义这套恢复语义，需现场故障注入验证。该成本对追求短突发处理稳定性是额外变量。

SGLang官方GPU安装和部署以CUDA/Linux为中心，未见原生Windows或Apple Silicon GPU服务支持材料。单4060同样缺少独立设备来承载两个阶段；官方单节点例子用不同GPU编号，且两种角色都作为独立模型服务加载。Mac不是CUDA/NIXL部署的替代节点。精确的量化、GPU显存切分与双实例行为必须以本机实测为准。

## 更贴近码流场景的候选：Frigate + go2rtc

Frigate是带录像、运动检测、对象检测与事件浏览的NVR；go2rtc负责接入、转发及按需转码。两仓库均标MIT许可；Frigate商标/标志另受商标政策约束，组合部署的模型、依赖也须分别核对许可。[Frigate代码与许可证](https://github.com/blakeblackshear/frigate)、[go2rtc代码与许可证](https://github.com/AlexxIT/go2rtc)。官方代码结构和文档表明这是完整摄像流服务栈，不是screen capture适配器。

Frigate每路可把低分辨率子流分配给`detect`、主流分配给`record`；detect宽高是检测帧尺寸，官方安装向导会提示过低分辨率可能影响可靠检测。go2rtc可把同一输入流转成RTSP restream，让Frigate和多个查看端共享一个摄像机侧连接；但双码流仍至少有子流与主流两路上游。无重编码时以转发为主；需要FFmpeg兼容转码时会新增计算开销，多客户端也增加下游连接/带宽，须实测。[摄像头角色与分辨率](https://docs.frigate.video/configuration/cameras/)、[go2rtc restream](https://docs.frigate.video/configuration/restream/)。

Frigate对象检测给出模型已知类别、位置与置信度；其GenAI功能是在对象缩略图/事件图像上生成描述、回顾摘要或辅助威胁分类。它适合快速浏览和检索，不等于工厂物料/行为分类模型，也不保证公交、线缆或动作语义正确。配置GenAI时还必须明确provider；若接远端provider会发送事件图像，隐私约束下应禁用远端并仅用获批本地服务。[对象检测](https://docs.frigate.video/configuration/object_detectors/)、[GenAI配置与用途](https://docs.frigate.video/configuration/genai/genai_config/)。

部署限制与本项目相关：官方称Windows不受支持，WSL/虚拟机可能运行但GPU/Coral硬件透传困难；Apple Silicon可用Docker容器并配主机侧Apple Silicon Detector。这不证明Windows RTX 4060与M5有同一套已验证加速路径。[官方安装与平台边界](https://docs.frigate.video/frigate/installation/)。只有新增**获授权**RTSP/ONVIF只读源时，Frigate才是可单独评估的可选入口；当前没有可用源凭据或授权时，产品继续screen-only，维持WGC/ScreenCaptureKit路线，不连接公开视频地址试跑。

| 可借机制 | 不能直接替换/保证 |
|---|---|
| record与detect选不同码流、go2rtc单上游复用、对象轨迹与回顾 | 当前屏幕采集；源分辨率/小目标细节；工厂异常语义和15秒十事件门槛 |

据此，Frigate适合作为“有授权码流时”的独立候选原型，先用合成录像检查两种角色、录像保留和本地GenAI边界；不应为它改造或暂停屏幕基线。若将来获得RTSP授权，先单路验证输入分辨率、丢包重连与源端连接数，再对照屏幕路径做盲测和负载测试。

## 两个替代的对照

| 维度 | vLLM PD | SGLang PD | 本项目意义 |
|---|---|---|---|
| 官方稳定性声明 | 明确实验性、可能变化 | 官方功能与部署文档齐全，需按版本核验具体功能 | 都未证明目标十路现场稳定性 |
| 视觉模型支持 | vLLM支持Qwen3-VL图像请求；PD跨实例视觉交接缺直接官方样例 | 有Qwen3-VL视觉encoder DP文档；与PD KV交接为不同能力 | 对各路独立ROI图像仍须单独试验 |
| 推荐拓扑 | 两个实例 + KV connector；官方LMCache例子至少2 GPU | Prefill/D服务 + router + KV backend；单机示例用不同GPU编号 | 一块4060不满足阶段独立计算条件 |
| 8GB单卡 | 两实例可能复制权重、KV进一步挤占显存 | 两个服务也需分别驻留/分配资源，且增加router和传输 | 风险是OOM、排队更复杂，而非预期吞吐翻倍 |
| Windows/Mac | Windows需WSL；Mac Metal为不同backend/模型路径 | 当前部署文档面向CUDA/Linux，未证实原生Windows/Mac兼容 | 不可作为同一套可移植运行栈 |
| 网络/安装 | 单机NIXL/LMCache组件；独立阶段仍需传KV | NIXL或Mooncake、router、模型双服务 | 中国无VPN可离线运行的前提是本地预置全部模型/依赖；首次获取需单独解决，不能假定HF在线可达 |
| 恢复面 | connector与双实例状态需运维 | router、两个worker、KV backend的健康与状态需运维 | 组件越多，恢复验证面越大 |

## 推荐试验路线：先稳定单GPU服务

对“十路持续运行、发现事件后复核各路独立ROI图像”，优先做可用性和视觉语义门槛的受控实验，而不是先拆P/D。对ROI图像裁切后放大只会放大已有像素，不能创造源画面未保留的细节：

1. **固定候选输入合同**：主检测/ROI负责为每路生成独立ROI图像及原始坐标。对告警再截取对应源画面局部并调整显示尺寸，保留完整上下文；每张请求带相机ID、时间戳、原始帧哈希和ROI变换记录。需要更细小目标时必须提高源分辨率或改善取景，单纯放大不能补回细节。
2. **每摄像头有界最新值队列**：每路至多一项待处理候选，新帧到来时可替换旧待处理项并记录`coalesced/superseded`计数；已开始分析的项不静默丢弃。这样防止突发把旧画面排成超过15秒的长队，同时可按危险度、画面新鲜度和轮转公平策略派发。
3. **一个GPU仲裁器、单个模型驻留副本**：明确最大并发为1作为基线；若引擎支持批处理，再用固定小批对照，不以启动第二服务副本代替并行。限制图像分辨率、输入张数、上下文和输出长度，按测得的显存峰值留余量。
4. **故障隔离与恢复**：请求设硬截止时间、调用幂等ID、看门狗、进程健康探测；超时只终结该事件，限制重试一次并带退避；runner失活时保留原始候选、转人工确认/规则告警，服务进程单独拉起，不阻塞画面捕获/证据录制。启动/关闭、取消、OOM及坏图均写可核对的原始终态。
5. **分开测性能与准确率**：按真实突发顺序测10候选延迟分布、15秒完成/删失数、队列龄、VRAM峰值、CPU/RAM、掉帧及72小时趋势；用独立标注集验证公交/路牌/树枝/反光/电缆等困难负例和近似正例，分别报告召回、误报和人工复核率。不能用合成视频吞吐替代语义正确性。
6. **达到触发条件再测PD**：若压测确认Prefill长图编码是瓶颈、且有第二个可支持CUDA/NIXL的设备，再用vLLM或SGLang做小规模离线试验。固定同一Qwen3-VL版本、图像字节和预处理结果，比较统一服务与PD的端到端完成时间/正确率/显存，并注入Prefill退出、KV传输中断、Decode超时、router重启和重复提交；全程只用合成或脱敏画面。

这条单GPU路线不能保证10件都能在15秒内完成；它的价值是让拥塞有界、每次取舍留证据、捕获链路不被模型挂死，并找出真正限制。若单卡实测算力不足，结论应是需要第二计算设备或更强本地部署机，而不是把阶段名拆开就算获得加速。

## 仍须实测的问题

- 目标Windows机器、RTX 4060 8GB上的实际驱动/运行时、模型权重与各路独立ROI图像显存峰值。
- 无VPN环境下，从已预置本地权重和离线依赖启动服务是否可行；不得依赖生产时拉取外网图像或模型。
- 一张GPU上并行两个实例会不会OOM，以及是否比一个受控队列改善端到端时限。按官方拓扑推测，大概率不能形成计算并行。
- 原Qwen3-VL 2B对公交、线缆、遮挡、夜间、缩放后局部仍误判的频率；需要评估模型/提示词/分辨率/两阶段候选策略，PD不替代这项验证。
- 超时、服务重启和证据写入失败后的真实恢复时间与遗漏率；Mac若需要支持，另做适配与端到端回归，不能从Windows或CUDA文档推断。

## 一手资料

- [目标实验仓库：固定快照](https://github.com/Soulmate-Halo/heterogeneous-gpu-pd-lab/tree/327c326d85170921113607750e4a3a1a320a9335)、[中文README](https://github.com/Soulmate-Halo/heterogeneous-gpu-pd-lab/blob/327c326d85170921113607750e4a3a1a320a9335/README_ZH.md)、[V8 seq32 JSON记录](https://github.com/Soulmate-Halo/heterogeneous-gpu-pd-lab/blob/327c326d85170921113607750e4a3a1a320a9335/data/deepseek-v4.1-flash-six-gpu-v8-seq32-evidence.json)。
- [vLLM PD文档](https://docs.vllm.ai/en/latest/features/disagg_prefill/)、[vLLM LMCache PD示例](https://github.com/vllm-project/vllm/tree/main/examples/disaggregated/lmcache)、[vLLM多模态输入](https://docs.vllm.ai/en/latest/features/multimodal_inputs/)、[vLLM GPU安装/平台支持](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/)、[vLLM许可证](https://github.com/vllm-project/vllm/blob/main/LICENSE)。
- [SGLang PD文档](https://docs.sglang.ai/advanced_features/pd_disaggregation.html)、[SGLang多模态encoder DP](https://github.com/sgl-project/sglang/blob/main/docs_new/docs/advanced_features/dp_for_multi_modal_encoder.mdx)、[SGLang许可证](https://github.com/sgl-project/sglang/blob/main/LICENSE)。
