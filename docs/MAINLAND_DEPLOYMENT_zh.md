# 中国大陆无 VPN 部署补充：十路真实分析与提示

2026-09-23。本文是当前 [功能优先任务单](superpowers/plans/2026-09-23-phase2b-to-demo.md) 的部署补充，**不是目标设备已装好、离线安装包已生成或大陆线路已实测的报告**。优先沿用 Windows 最新独立修复副本和已可用环境，不从旧 main 覆盖。目标仍是十路在线人物分析、屏幕提示和 15—30 分钟实际功能介绍片。

## 一、已经确定的主方案和接口

```text
Seetong 取得十路在线画面（依赖现场实际摄像头网络）
  → Windows 原生窗口采集、逐路身份和新鲜度确认
  → 本机 YOLO11n 人物检测 + 每路独立跟踪/区域/无人规则
  → 立即候选提示，同时缓存/保存证据
  → 本机 Ollama 的 Qwen3-VL-2B Instruct 实际多帧复核
  → 模型结果更新屏幕提醒 → 对应事件/证据 → 人工确认
```

**不需要再加一个“国内大模型”才能适配大陆网络。** 当前视觉复核已选择 Qwen。YOLO、规则、Qwen、SQLite 和桌面提醒留在设备内；运行不以 Codex 在线、GitHub 可访问、VPN 或国外云 API 为前提。Seetong 是否依赖互联网/P2P、能否稳定取得十路画面必须在现场验证，不能把“本地 AI 不需外网”说成“远程摄像头断网仍在线”。

| 项目 | 当前明确选择 |
|---|---|
| 本地视觉模型 | `qwen3-vl:2b-instruct`，记录实际 tag、digest/文件清单和 Ollama 版本；不得换成文本模型或 cloud tag |
| 服务入口 | 已有 `scripts/start-local-model.ps1`，项目内完整 Windows Ollama 程序和 `models\ollama` |
| 配置中的 `review.endpoint` | `http://127.0.0.1:11435`，只写 base URL，不能再附 `/api/chat` |
| 推理请求 | 现有 `OllamaReviewer.review` 自动 `POST /api/chat`；`stream=false`，按时间排列的 1—6 张本地 JPEG Base64，正文同时包含时间戳和规则上下文 |
| 请求/输出 | `format=json`；读取外层 `message.content`，再校验 supported/dismissed/uncertain、reason、visible_evidence；物料另校验 target_visible/target_type，沿用现有严格校验 |
| 身份凭证 | 本地公开模型推理不需 API Key、不需购买云服务；Seetong 自身账号仍由用户登录 |
| 本地限制 | `review.enabled=true`、`cloud_enabled=false`；服务 `OLLAMA_HOST=127.0.0.1:11435`、`OLLAMA_NO_CLOUD=1`，不开放到局域网 |

Ollama 官方确认本地 API 不要求身份认证；本项目选择 11435 以隔离默认 11434 服务，不能据官方默认端口覆盖项目配置。[官方本地认证说明](https://docs.ollama.com/api/authentication)、[Qwen3-VL-2B 模型条目](https://ollama.com/library/qwen3-vl:2b-instruct)。Ollama 支持关闭云功能和指定模型存储目录。[官方 FAQ](https://docs.ollama.com/faq)

多张图片使用同一消息的 `images` 数组传入；REST 接口接收 Base64 图片，实际 Qwen 多帧辨识效果仍须在目标设备验证。[Ollama 官方视觉接口](https://docs.ollama.com/capabilities/vision)

本机源码已核对：启动脚本设置上述本地地址、模型目录及禁云变量；`inference.py` 的 HTTP 客户端使用 `trust_env=False`，避免模型请求受代理环境转发。**Windows 执行端须核对实际导入版本及配置读回**。现有 `init` 默认 11434，必须显式保存本轮 11435 并重开确认；不修改用户全局代理/VPN、PATH、Python 或其他程序的服务。

## 二、把下载问题在交付阶段解决

首次安装、获取模型和日常推理是三件事。官方站点、GitHub、PyPI 或模型仓库在目标宽带上的可达性没有在本机证明；不把反复下载失败当作监控系统运行步骤。

**主路径：优先复用现有已校验环境；缺的资源用固定版本本地安装包补齐。** 准备机收集官方资源，交付通过 U 盘、局域网文件传输或经确认可用的国内文件渠道完成；GitHub 用于源码版本管理，不是唯一下载途径。包中不得带账号、个人 Ollama 密钥、校准截图、真实录像或旧运行数据库。

| 必须准备的内容 | 执行要求 |
|---|---|
| 最新已核对的候选源码/差异与启动入口 | 标清候选版本；保留旧安装；新独占 data，目标机可直接启动 |
| Python 3.12 Windows x64 与项目依赖 | 已有可用环境先复用；需要重建时在匹配的 Windows/Python/架构准备固定依赖和所有传递依赖的 wheelhouse，不复制 Mac `.venv` |
| PyTorch 与 GPU 依赖 | 根据现场驱动及已验证 CUDA 组合固定包，不能用国内镜像装成功就认定支持 GPU；不为本任务盲目升级显卡驱动 |
| YOLO11n 权重 | 准备确切文件及 SHA-256；运行时用实际本地路径，缺失须可见报错，不悄悄换模型 |
| Windows Ollama | 完整匹配版本程序与随包 DLL/GPU 运行文件，不能仅复制 `ollama.exe`，不能复制 Mac 可执行程序 |
| Qwen 多模态模型库 | 从只含本项目已选模型的专用存储目录准备，包含所需 manifest 和所有引用 blob/视觉相关资产；源/目标均校验清单。不得只复制一块 GGUF，也不能把 ModelScope/Hugging Face 的 safetensors 改名当作 Ollama 模型 |
| 录像/播放工具及恢复说明 | 本地 ffmpeg/ffprobe 或已验证的实际播放器；安装失败明确缺项，能回退原入口而不覆盖现场数据 |

Ollama 官方 Windows 文档提供原生 Windows 安装及独立命令行发行方式；选择并冻结现场通过实测的版本。[Windows 官方文档](https://docs.ollama.com/windows)。完整模型库存储迁移属于本项目交付办法，须在 Windows 目标端以列出模型和**实际图片请求**验证，不能只凭目录存在宣布成功。

当前 `scripts/setup-windows.ps1` 会联网更新 pip 并安装依赖，**尚非离线安装器**。执行者需为交付补项目内离线路径：从已固定 wheelhouse 安装、禁止临时拉取或升级；缺包时停止并列出具体缺包，不更改全局 pip 配置。pip 官方支持本地目录安装与预下载依赖；跨平台准备还必须指定匹配的目标平台/解释器，不能将 Mac 下载结果直接当 Windows 完整包。[pip 下载依赖](https://pip.pypa.io/en/stable/cli/pip_download/)、[pip 本地安装](https://pip.pypa.io/en/stable/user_guide/#installing-from-local-packages)

若当前准备机可使用国内镜像，普通 PyPI 依赖可临时指定清华 TUNA 的 HTTPS 索引；只作用于本次项目命令，不写全局配置。镜像不是全部 CUDA 包、Ollama 或视觉权重的通用替代源，也不保证目标线路永久可用。[TUNA 官方用法](https://mirror.tuna.tsinghua.edu.cn/help/pypi/)

## 三、就地完成的最少核验，然后进入十路实景

本项由 Windows 执行端与功能整合同轮完成，已经通过的环境不重复安装，不另外开启一轮空跑长测。

1. 准备/核验完整本地资源、文件清单和版本，启动项目内 Ollama；读日志确认禁云、监听 loopback 和指定模型目录。`GET http://127.0.0.1:11435/api/tags` 与 `POST /api/show` 核对模型身份及视觉能力，然后以真实图片输入完成一次现有 `OllamaReviewer.review`。仅有文本回复或模型列表不证明视觉推理可用。
2. 显式保存并读回 `review.endpoint`、模型名、enabled/cloud_enabled；检查实际 YOLO 推理设备及 `ollama ps` 的加载位置。单模型回复成功不代替十路并发及 15 秒目标；冷热启动分别记录，不把模型预热时间藏在计时之外。
3. 启动软件不自动安装或拉取模型；GUI 明确区分“服务未启动、模型缺失、视觉请求失败、推理超时、摄像头断流”。证据保全、错误可见等沿用主任务单最小保护，不另建账本平台。
4. 在大陆网络且没有 VPN 的目标设备上，一路核通、三路短检查后同轮接十路真实 Seetong。保留真实检测、复核、通知点击和录像证据，并录制 15—30 分钟介绍片。Codex 或 GitHub 不在线不能成为软件停止分析的原因；不通过关闭整个网络来测试 AI，从而意外切断摄像头。
5. 本地准备环境可单独验证不访问外网的冷启动/图片推理，或者在不影响 Seetong 的隔离环境中检查资源完整性。该项仅证明 AI 安装与推理独立性；现场十路画面持续到达须另由实景运行证明。软件和安装脚本未改动时不反复重装验证。

## 四、国内云 API 的位置：备用，当前不启用

用户补充大陆网络环境不等于授权上传监控画面或新增付费服务。本地部署**不需要**以下服务。只有后续目标设备证据显示本地方案不能满足要求，且用户明确授权云端画面处理及费用时，才评估阿里云百炼北京地域的 Qwen-VL，不自动回退。

官方当前给出的北京兼容接口为 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions`，`WorkspaceId` 来自用户业务空间；旧 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` 仍受支持。需要对应地域/业务空间的百炼 API Key、可调用的视觉模型（文档示例 `qwen3-vl-plus`）、账户额度与现场实际连通性。密钥只由目标机私密配置注入，不写入仓库或回传日志。[百炼官方视觉 API](https://help.aliyun.com/zh/model-studio/qwen-vl-compatible-with-openai)

国内云视觉接口与本地 Ollama 的请求、响应协议不同；不能只替换 `review.endpoint`。当前配置刻意拒绝非 loopback 和 cloud_enabled=true；未来如获授权，单独实现复核适配，沿用 camera/event ID、证据、时限、结果校验和提醒，采集/检测/规则结构不必因此重做。本轮不实现该云适配、不采购 API、不发送画面，也不将国内云服务当作低延迟保证。

## 五、本轮交付与可证明范围

Windows 执行端交付：已核对的本地启动入口、目标机实际使用的配置摘要/包清单/模型身份、十路真实结果与 15—30 分钟介绍片、本地安装资源或可复原资源清单。完整介绍片是可见功能交付，不能用测试项数量或旧合成视频替代。

本补充本身只完成接口、部署路线和执行要求的确定；尚未代表资源包制作、Windows 无 VPN 实测或真实视频已完成。主任务单中候选 3 秒、模型复核 15 秒、十路质量/准确率及最终长期运行的标准均保持原值。
