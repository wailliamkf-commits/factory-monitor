# Factory Monitor 停止账目第 2 步实施任务单

> **给执行代理：**本任务已冻结，无需再做设计确认。用户会附本任务单和第 1 步报告；先完整阅读二者及候选副本内对应代码，再逐项勾选执行。不得把本任务单写成已实测结论。

**任务头：**在 Windows 的独立第 2 步副本中，补齐合成链路停止边界的精确账目、命令终态、可审计清理和有界组合验证。第 1 步报告描述了有限范围 drain/ACK 验证，本机尚未独立核验；本步不继承旧 30 分钟结果，也不宣称现场、模型或生产 Gate 通过。

**Architecture / Tech Stack：**保留 Python 3.12、multiprocessing、SQLite、PySide6 及既有采集/检测/证据进程。仅补身份、账目、终态及恢复，不新增服务或依赖。

**Spec：**沿用仓库 [MASTER_PLAN.md](../../../MASTER_PLAN.md) 和用户附带的《停止协议第1步修复与验证_2026-09-23.md》。后者只作版本及测试报告输入，其中结果不得替代实际工件。

**目标：**正常工程负载下停止后没有静默证据丢失，且每个事件、帧、命令和介质 ID 可由独立来源对账；过载或注入故障必须明确 `false`、`incomplete` 或 `deferred` 并留下报警证据。

**保留与版本：**保留原安装、`two-faults-20260922` 和第 1 步的两个旧修复；不替换启动入口、不恢复已删除媒体、不提交、不推送。新建独立第 2 步副本、独占临时目录和独占测试 data/SQLite，绝不共享可写 DB。第 1 步报告的最终候选 SHA-256 原样保留为：`runtime.py` `1548676792fe563ac81c53721d60578c9fc7a28e0cf28d595b388cbe3ec06a8e`；`evidence.py` `41494faf7242a3d9ce414cc9aa891fbb2ab880255071be1599bdbc36edd4c4c3`；`gui/app.py` `234c690783834137e6386fcbc82ff77a8cf53ca9004112678ad06c09d0f66e60`。旧修复的已报告基线仅有 `runtime.py` `abc30dc3d58df319bbb1ecfbe3da3c6e33831df0c5519d16e57887e824c25f03` 与 `evidence.py` `f5382509a933f2317afe5104c3a1d252a44219be7e4e4c39f5a5036f97b6b6e7`；未提供旧 `gui/app.py` 基线时必须报告 `unknown`，不可臆补。

**范围：**仅 synthetic，不启动 Seetong、YOLO、Qwen、长测、真实抓屏或云服务；不升级依赖、不换模型、不改变原负载参数/队列上限/生产磁盘保护阈值、不做自动点击，也不改其他应用或全局设置。允许的业务触点仅为候选副本中的 `src/factory_monitor/runtime.py`、`src/factory_monitor/evidence.py`、`src/factory_monitor/store.py`、`src/factory_monitor/gui/app.py`；测试范围为 `tests/test_stop_protocol.py` 及必要的针对性新增/变更，均须纳入清单。当前 Mac 没有新候选源码，故本单不虚构行号、现有函数签名或导入路径；执行者必须以 Windows 候选的实际导入链和签名作最小适配。

**风险与口径：**`run_id + frame_id` 是整幅帧单位，`camera_id` 是逐路投影，不能把十路投影的十倍数当作 lost。每 channel 的结果要满足 `attempted = accepted + rejected`，`accepted = consumed + evicted + remaining + shutdown_discarded`；不明值保留 `unknown`，不能填 0。消费不等于落盘：结束后必须以独立 source/oracle ID 集合与 manifest、JPEG、可完整解码的视频交叉对照。采集正常取样与真正丢失分开计数。审计记录不能是唯一证据且放进可丢弃队列；不逐帧 fsync 拖慢录像。已入库事件和 start command 必须有 ACK，或能追责为 `failed/deferred`。清理要记 intent/result、event ID、hash、原因，重启后可对账；保护 inflight。写入方仍拥有记录时不得强写 manifest，保留锁/所有权。

**验收与模型：**每个 case 要记录 actual/count/lost/unknown/durations/exit/manifest/DB 与哈希。`confirmed` 仅指停止协议确认，绝不等同录像完整；0 帧立即 stop 不能替代多帧证据。全套 pytest 要明确真实 YOLO 为 deselect，逐项保留 skip；报告中的 `95 passed / 2 skipped / 1 deselected` 是对方第 1 步报告，不是本机复验。Sol 负责并发/停止和整合，Terra 负责确定性测试，Luna 整理工件，Astra 仅做关键 Gate；只有一次集中审查，不做每小任务审查。首个失败先按证据修复；第二次同类失败必须读真实代码并做根因诊断，不作第三次猜测。新框架、扫描式全重构均为本步外的 optional。

## 文件与接口边界

| 文件 | 本步可承担的接口责任 | 不可假设的内容 |
|---|---|---|
| `runtime.py` | 生产停止、frame/command EOS、ACK、deadline、进程退出和按 run/channel 的账目汇总 | 不假定现有类名、队列类型或 timeout 实现 |
| `evidence.py` | 录像命令消费、帧/事件 ID 证据、manifest 终态、写入方所有权和清理结果 | 不把 message dequeue 当作介质已落盘 |
| `store.py` | command/event 终态、SQLite 只读回查、重启幂等和所有权/锁可追责状态 | 不复制活动 DB 漏掉 WAL |
| `gui/app.py` | 真实 GUI 的 Stop/关窗安全入口和结果呈现 | offscreen 覆盖不能声称实机桌面验证 |
| `test_stop_protocol.py` | synthetic oracle、同步栅栏、故障注入和结果矩阵 | 不用 sleep 制造偶然时序，不共享可写 DB |

## 执行任务

### Task 1：只读冻结、导入确认与可复核基线

**Files:** 只读候选副本的上述五类文件、测试配置和实际导入路径；在候选父目录新建本步 diagnostics/manifest，不改原安装或旧副本。

- [ ] 对三个业务文件计算 SHA-256，逐项同上表新候选 SHA 对比；再与两项已知旧基线比对。输出每项的 candidate hash、reported hash、关系（same/different/unknown）及实际 `PYTHONPATH`/解释器/导入文件绝对路径。
- [ ] 若候选与报告新哈希不符，先登记真实改动并取得同版本的对应日志；在版本关系明确前不在不明副本上继续业务修改。不要覆盖已有差异来强行配成报告哈希。
- [ ] 记录新候选实际 `max_inflight` 原值；已知本地基线 `config.py` 默认值为 10，但候选如不同只能登记，未经新的证据不得调整。GitHub 本地 `main` 当前仅为 `0de7f2e` 文档状态，绝不可重新拉取或用它替换 Windows 新候选后继续测试。
- [ ] 同时冻结并导出测试配置：整幅尺寸、每路裁剪尺寸、实际帧载荷字节、FPS、前后窗、队列容量、磁盘与在途上限。压力用例采用该尺寸的合成图像数组，不能以几个整数消息替代真实图像载荷；低于 1GiB 可用内存时停止新增负载，启动前沿用至少 2GiB 保护线，不关闭用户其他应用。
- [ ] 只读列出第 1 步 3 源文件、所有新增/修改测试、最终 pytest/ruff 原始 stdout/stderr、红绿关键日志、stop JSON、SQLite 一致性快照、版本信息和 manifest；不得跑旧诊断，也不得改动或恢复历史媒体。
- [ ] SQLite 快照只允许在连接已 closed 后读取，或以 SQLite backup API/一致备份取得；禁止直接复制活动 `.db` 而漏 WAL。为每个纳入文件记录路径、大小、SHA-256，生成 manifest 和其自身 SHA-256。
- [ ] 复核独立第 2 步 data/DB/临时目录没有指向原安装、旧修复或现存运行目录；发现共享可写路径即停止该次执行，先换独占目录。

### Task 2：账目、终态与清理的最小实现和确定性测试

**Files:** Modify（按实际签名最小适配）`runtime.py`、`evidence.py`、`store.py`；Test `test_stop_protocol.py` 及实际新增/修改测试。

- [ ] 先写 synthetic failing tests：逐帧 `run_id/frame_id` oracle 和按 camera 投影的 oracle 分开断言；验证 attempted/accepted/consumed/evicted/remaining/shutdown_discarded/rejected/unknown 的守恒式，且 unknown 不可被归零。
- [ ] 再以实际代码最小接入账目事件。每个 accepted 项的最终去向必须唯一可审计；命令与已入库事件必须收到 ACK 或落到 failed/deferred。审计自身保存到不可随证据队列淘汰的路径。
- [ ] 每个事件 manifest 按实际帧覆盖独立判 complete/incomplete。控制器补写终态前必须确认原写入方已停止；仍活时只在独立停止报告记 deferred、保留所有权，不并发改 DB/manifest。清理另记 intent/result/event ID/hash/why；最终停止报告的 confirmed=true 必须等必要排空、清理和所有权释放全部成功后才能发布，不能反过来要求单个事件的 manifest 等待目录锁释放才落盘。
- [ ] 以同步事件/屏障确认 `frameEOS` 已被真实消费后，才发送晚 start/command EOS；不得用固定 sleep。消费、JPEG/manifest 写入与视频完整解码须分别断言。
- [ ] 每段证据同时核对帧数、顺序和重复 ID，不能只比较集合或计数。合成图像携带可回读的序号标记，或提供等效的内容核验，让视频实际内容与 source/oracle 对应；不能仅用程序自己写出的 manifest 自证没有错序/重复帧。

### Task 3：Q1—Q7 synthetic 矩阵

**Files:** Test `test_stop_protocol.py`；仅在必要时按 Task 2 的既有接口改四个运行时文件。

- [ ] **Q1：**预置非零、多帧积压后 stop，且正常余量足以排空；预期所有 accepted 有唯一终态、ID 集合相等、无静默 remaining。另保留 0 帧立即 stop 作为边界，不代替本 case。
- [ ] **Q2：**DB 已建但 command 尚待处理时 stop；预期每个 command 最终有 ACK/failed/deferred，DB、stop JSON 和 manifest 的事件状态一致。
- [ ] **Q3：**真实多进程，用可观察同步确认 frameEOS 被消费后再送晚 start/commandEOS；预期晚命令不越过终态。禁止概率性 sleep。
- [ ] **Q4a 正常基线：**十路同时输入、单次十事件，原 2fps、pre=30/post=60；先预填至少 30 秒缓存，在约第 31 秒触发事件，约第 94 秒收尾。十段完整窗口均不中断；对每个媒体做完整解码和 ID/oracle 对照。记录候选来自正常规则还是明确标记的测试注入，注入只证明证据路径。
- [ ] **Q4b 有界重叠：**十路仍同时输入，先预填至少 30 秒缓存，约第 31 秒触发 5 路事件，第 51 秒触发另 5 路；两批后窗重叠，活动上限始终不超过 10，参数不变，约第 114 秒收尾，记录实际持续时间与每段完整性。不得缩短后窗或串行化批次。
- [ ] **Q4c 独立过载：**已有 10 个在途时触发第 11 个事件；预期按原 limit 明确拒收并可审计。它只证明 overlimit 行为，不能计入 Q4a/Q4b 的全部业务成功；不得调大 `max_inflight` 刷通过。
- [ ] **Q5：**以虚拟限额或独立小配额目录触发容量边界，绝不填满系统盘；预期拒收/不完整/清理账目可追责，生产保护阈值不变。
- [ ] **Q5 留存补充：**独占测试目录内预置 25 条具有不同完成时间、有效合成证据的已完成事件及 2 条在途事件。执行原留存逻辑后，核对最新 20 条完成事件及 2 条在途仍在、5 条清理 intent/result/哈希一致；再注入一次删除中断并重启对账，不重复删除/计数。预置夹具只验证清理/恢复，不算作 25 段实时录制或长测证据。
- [ ] **Q6：**分别注入 stop 中断、强停、ACK timeout、cleanup 失败、写入方存活；预期 failed/deferred、锁保留、无重复计数/双写/永久 recording。写入方停止后，重复 stop 与 restart 必须幂等。
- [ ] **Q7：**真实 GUI Stop 按钮和关窗路径；进程活着时阻止失控关窗，安全退出但录像 incomplete 时不得永久卡窗。offscreen 单元测试可覆盖逻辑，但报告必须标为 offscreen，不能称实机桌面。

### Task 4：阶段预算、回归与工件交付

**Files:** diagnostics 下的日志、stop JSON、SQLite 安全快照、媒体、manifest 和最小回传 ZIP；不将结果回写为生产 Gate。

- [ ] 逐阶段记录采集确认 1.5 秒、主要停止 5 秒、两条 EOS 各最多 2 秒、强停和消息收尾各 0.5 秒的实际 duration；不得机械相加，因为阶段可能嵌套/并发。若正常负载频繁超时，先定位共享 deadline、发送阻塞位置和 flush 所有权，再作最小修复；不得调大数值刷 PASS。
- [ ] 运行针对性红/绿用例、全套 pytest 和 ruff，保存完整原始输出。pytest 命令明确 deselect 真实 YOLO，保留每项 skip/deselect 原因；失败注入 case 可作为 case PASS，但整体 production 性能不得写 PASS。
- [ ] 制作最小回传 ZIP：分别保存第 1 步基线和第 2 步最终业务源码、全部实际新增/修改测试及工具、可读差异、最终 pytest/ruff 原始日志、关键红绿日志、每 case stop JSON、closed/backup SQLite 快照、oracle/manifest、必要的合成 MP4/JPEG、版本与所有 SHA-256。必须包含第 2 步最终实际执行的源码及对应哈希，不能只交第 1 步源码和新测试。ZIP 外另给可读结果表，列出每 case 的 actual/count/lost/unknown/durations/exit/manifest/DB/hash 和结论。
- [ ] 进行一次集中审查：检查单位没有混淆、守恒式、视频解码、命令终态、WAL 安全、写入所有权和 GUI 证据措辞。仅在所有 Q1—Q7 与工件完整时标为“第 2 步 synthetic 通过”；完成后 STOP，Windows 真实模型/纠错集/72h 留给下一阶段，不自动发布。

## 交付判定

本步只交付可审计的第 2 步 synthetic 结果和最小 ZIP。正常负载没有静默丢失且 ID 集合/介质/数据库可闭合，才能通过本步；任何 overlimit 或故障情况只要如实失败、incomplete 或 deferred 并报警，相关 case 可通过。它不改变整体现场 Gate 的 FAIL 状态。

## 转交 Windows 对话的启动指令

请执行随附《Factory Monitor 停止账目第 2 步实施任务单》，从 2026-09-23 报告对应的 Windows 停止协议新候选继续。先核对源码哈希并只读封存第 1 步证据，再创建独立第 2 步副本，按 Q1—Q7 完成必要实现、测试和一次集中审查。不要从 GitHub main 覆盖新候选，不碰其他应用、旧安装或旧证据；本轮不启真实摄像头、模型和长测。故障处理正确可判对应故障用例通过，但不能把它写成正常业务成功。按任务单输出最终源码/测试/原始日志/一致数据库快照/合成媒体及哈希清单的最小回传包。完成本阶段或发现无法安全继续的阻断时停止并报告，不自动发布或进入下一阶段。
