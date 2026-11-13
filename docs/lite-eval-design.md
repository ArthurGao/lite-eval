# lite-eval 设计文档

> 状态：设计稿（2026-11-13），实现尚未开始。按第 16 节的阶段实施；某个阶段改变了行为，就在同一次提交里更新本文。
>
> **给新 session 的说明**：这份文档是实现 lite-eval 的唯一依据。它在 llm-steward 的会话里写成，汇总了
> lite-rag、lite-mem、llm-steward 和 tenderted 几个项目做评测时的全部经验。先读 §0 和 §2、§3，
> 再按 §16 从 E0 开始。根目录的 `AGENTS.md` 是给所有 agent 的规则，`backend/` 和 `frontend/` 各有补充。

## 0. 怎么读

| 你要做的事 | 读 |
| --- | --- |
| 弄清楚要做什么 | §1 概述、§2 需求清单 |
| 弄清楚为什么这样设计 | §3 经验教训（每条都有出处和数字） |
| 写代码 | §4–§12 的契约和行为，**§18 规格细节（有歧义时以它为准）**，§15 仓库结构，§16 当前阶段的验收 |
| 迁移已有项目的评测 | §13，以及各项目里的原始代码（路径在 §1“来源”） |

## 1. 概述

lite-eval 是一个**通用的 AI 应用评测模块**：给任何 LLM 应用（RAG、记忆、抽取、评审、写作、agent，以及以后
新做的应用）提供同一套评测方法。它回答一个问题：**这次改动让系统变好了还是变差了？这个结论可信吗？**

由两部分组成：

- `backend/`：Python 包 `lite_eval`，包括契约、执行、评分、分析、CLI、pytest 插件，以及 Viewer 用的只读 API；
- `frontend/`：Viewer 单页应用，用来看分数、对比两次运行、逐条看用例并给 miss 归类。

和 [lite-rag](https://github.com/ArthurGao/lite-rag)、[lite-mem](https://github.com/ArthurGao/lite-mem) 是同一系列：
lite-rag 管文档知识，lite-mem 管关于人的记忆，lite-eval 衡量它们，以及之后所有应用的质量。

### 来源（迁移时去这些地方读原始代码）

| 来源 | 内容 | 迁入后归属 |
| --- | --- | --- |
| `~/Code/AI/lite-rag/src/lite_rag/eval.py`（315 行）、`eval/golden*.jsonl`、`eval/corpus*/`、`eval/configs/*.yaml`、`eval/crosscheck_ranx.py` | 检索评测：按证据句判对错；hit@k、MRR@10、延迟；同一索引只建一次；计时前预热；用 ranx 交叉核对 | retrieval pack（§7） |
| `~/Code/AI/lite-mem/eval/multihop.py`、`eval/multihop.json`、`eval/dataset.json`、`eval/results/*.json` | 两跳检索；抽取、冲突消解、敏感信息 | memory pack |
| `~/Code/AI/llm-steward/backend/tests/eval/`（`test_recall.py`、`corpus.py`） | 联合检索 recall@8 的 pytest 门禁，阈值 0.92，基线 0.960 | 门禁（§8.4） |
| KB `~/Docs/Knowledgebase/global/llm-evals/`（`methodology.md`、`evals/`、`results.jsonl`、`tasks/`） | 方法论、评测卡片、跨项目结果表 | 方法论写进默认行为（§3、§8、§9）；结果可以导出（§13） |
| KB `projects/lite-rag/reference/eval-results.md`、`projects/lite-mem/reference/eval-results.md` | 每次评测的结果和解读 | 经验教训（§3） |

### 目标

- **通用**：被测对象只要能“给一个输入、返回一个输出”，不管用什么语言写、怎么部署，都能测（§5.3 的三种接入方式）。
- **契约稳定**：长期不变的是数据格式和接口，不是实现。所有记录带 `schema_version`，以后只加字段、不改含义，
  旧的运行记录永远能读。
- **结论可信**：方法论是默认行为，不靠人记着（§3）。
- **便宜**：生成输出和打分分开做，输出缓存起来；确定性评分优先，模型评审只在确定性评分做不到时才用。
- **看得见**：本地 Viewer（§10）。

### 非目标（v1）

- **不做线上监控和追踪平台**：不采集生产流量，不做告警。需要时接 Phoenix、Langfuse 这类工具，lite-eval 只消费它们导出的数据集。
- **不跑通用能力基准**：MMLU 这类测的是模型本身，是 lm-evaluation-harness 的事。
- **不做多用户服务**：Viewer 只监听本机（loopback），没有账号。
- **不替代各项目的单元测试**：lite-eval 衡量质量，正确性仍由各项目自己的测试保证。

## 2. 需求清单（这个模块要做到的事）

每一条都要在某个阶段（§16）有自动化的验收。括号里是对应的经验教训（§3）。

| # | 需求 | 依据 |
| --- | --- | --- |
| R1 | 被测系统可以是 Python 对象、HTTP 服务或命令行子进程；lite-eval 只是开发依赖，不进被测应用的生产代码 | 通用 |
| R2 | 用例、输出、分数、运行记录都是带 `schema_version` 的 pydantic 契约；只加字段，旧记录永远能读 | 长期可用 |
| R3 | 两段式：先生成，后打分。输出按“被测配置 + 用例内容”缓存；只改评分器时不调用被测系统 | L7、L23 |
| R4 | 每次运行留一份不可变记录，记下完整出处：配置、引擎和模型版本（含厂商、通道、effort、是否开 thinking）、数据集哈希、评分器版本、git sha、种子、费用 | L19、L20、L21 |
| R5 | 出错、超时、被截断的用例照样记录并计为失败；费用和 token 在失败时也照记 | L6、L18 |
| R6 | 汇总时按标签分组；和基线比较时逐用例配对，给出显著性；用例少时明确说“差异不显著” | L4、L12 |
| R7 | 支持重复运行（`repeats`），报告均值和波动；非确定性的被测系统默认至少跑 2 次 | L4、L14 |
| R8 | 每次运行都输出 miss 列表；可以在 Viewer 或 CLI 里归类（真漏、标注错、题目排除、不清楚），归类单独保存 | L1 |
| R9 | 数据集（含标注）有版本和哈希；改了标注就是新版本，和旧运行比较时按 §18.3 处理；人的归类（Annotation）只追加、可更正，不改数据集 | L1、L9 |
| R10 | 确定性评分器优先；模型评审必须先用带标签的校准集测出一致率，并把评审的模型、提示词版本和温度固定写进记录 | L2、L3 |
| R11 | 门禁：CLI 以非零码退出，pytest 断言失败时打印退步的用例；退出码必须如实 | L22 |
| R12 | 预算：费用和调用次数有上限；CI 模式下拒绝一切不能确认免费的调用，除非环境变量和 Suite 同时放行（§18.13） | L7、L24 |
| R13 | 自检：配置差别很大但分数完全一样时给出警告；所有配置都没触发被测功能时提示“数据集可能没覆盖这个功能”；排名指标用第二种实现交叉核对 | L10、L13 |
| R14 | 可比性：相同索引配置共用一次 setup；计时前预热；延迟口径由被测系统声明；配置顺序不影响结果 | L15、L16 |
| R15 | Trace 记录决策，不只记最终输出：检索命中、工具调用、模型调用，以及“为什么没做”（例如判为重复而丢弃） | L17 |
| R16 | Viewer：运行列表、运行详情、对比、用例详情、miss 归类、数据集浏览、趋势；另外能导出单个 HTML | 需求 |
| R17 | 按任务类型提供 pack：retrieval、memory、extraction、judging、grounded、agent、cost | 通用 |
| R18 | 外部基准的加载器：LongMemEval、LoCoMo，以后加 BEIR 子集；记下许可证 | L10 |
| R19 | 迁移已有评测时，迁移前后的数字必须逐项相同 | 可信 |
| R20 | 数据集不放客户资料、真实姓名和密钥；写入运行记录前可以配置脱敏规则，Viewer 只监听本机 | L30 |
| R21 | 结果能导出成 KB `global/llm-evals/results.jsonl` 的行 | 跨项目 |

## 3. 经验教训（为什么这样设计）

每条格式是：**发生了什么 → 结论 → lite-eval 怎么落实**。出处写在括号里：KB 方法论指
`global/llm-evals/methodology.md`；lite-rag、lite-mem 的结果页指 KB `projects/<name>/reference/eval-results.md`。

### 3.1 评测方法

- **L1 先怀疑标注，再怀疑模型。**
  - 发生了什么：
    - 问题抽取先测出召回 50–65%，参照答案是另一个模型多次运行的共识。逐条读 miss 后发现，这个共识把同一条要求算了两次，还收进了提示词明确排除的条目；按真正要的 14 条算，召回是 100%（KB 方法论 §1，tenderted）。
    - lite-rag 基线经 Codex 审查后，收紧了 8 条证据句，让每条都直接表达答案。
    - 评审模型“判错”的一个回答，其实是标注错了：回答里用了“她”，但原文没给出性别（方法论 §3）。
  - 结论：报告差距之前，先读完每一个 miss 并归类。
  - 落实：R8 miss 归类；R9 标注改动要出新版本。
- **L2 用构造出来的标签，但要用真实输出抽查。**
  - 发生了什么：
    - 拿一个合格答案，每次只按一种已知方式改坏（编造事实、多加一段、答成别的题、加禁用语）。13 个这样的用例，就把真实草稿分不开的几个评审模型分开了。
    - 但构造继承了原答案的形态：原答案短而干净，这一点掩盖了问题。新加的评审问题在 52 个正常长度的答案里标了 45 个（方法论 §2）。
  - 落实：judging pack 支持“改坏”式的构造用例；新的评审维度上线前，必须在真实输出的样本上看一次标记率（R10）。
- **L3 评审模型是一件仪器：先校准，然后固定。**
  - 发生了什么：
    - 不同评审给同一批草稿打的分不能比：gpt-5-mini 在 52 份里标出约 11 份无依据，Sonnet 在 26 份里标出 18–20 份。
    - 评审还会奖励错的东西：它判错了一个正确标出“缺证据”的回答，却放过了一个自行推断的回答。
    - 评审结论不稳定，往往是评分标准有缺口（方法论 §3）。
  - 落实：R10；评审的模型、提示词、温度任何一项变了，就算换了评分器。
- **L4 重复跑，看波动；一两题之差不算结论。**
  - 发生了什么：
    - 同一配置连跑三次，覆盖率是 83%、83%、92%（方法论 §4）。
    - lite-mem S7 的五个配置行为完全相同，但单次结果在“过期事实”上是 7 个里 2–4 个，hit@3 是 11 个里 8–10 个，每次写入 7.9–73.9 秒。
    - lite-rag 的 44 题集上，一题是 0.023，所以差距小于约 0.05 都算平手；32 题太少，没法给接近的配置排序。
  - 落实：R6 显著性检验；R7 重复运行。报告里差异不显著时明说，不能写成“提升”。
- **L5 按“完成一次任务的成本”算，而不是按 token 单价。**
  - 发生了什么：
    - 把 gpt-5 的 effort 调低，写作输出少了 63%，无依据的说法却从 5 处涨到 14 处（52 份里）。
    - 在摘要任务上调低 effort 反而修好了一个失败：默认 effort 的回复超过 4000 token 上限，什么都没返回（方法论 §5）。
  - 落实：cost pack 默认随每个 pack 一起出，质量和费用放在同一张表里。
- **L6 隐藏的成本。**
  - 发生了什么：
    - 推理（thinking）token 按输出计费，却不出现在回复里，占了 gpt-5 写作输出的 88%。
    - 在 `max_tokens` 处被截断的回复照样全额计费；有一个 SDK 在记下用量之前就抛了异常，这笔花费就看不见了（方法论 §6）。
  - 落实：R5，Trace 里的 `usage` 在出错时也要记，并区分推理 token。
- **L7 评测本身也要有预算。**
  - 发生了什么：在 gpt-5 上跑三次完整的写作评测，一天就用掉了 10 美元余额里的大部分。只改了评分标准时，应该给已存的输出重新打分（方法论 §7）。
  - 落实：R3 两段式和缓存；R12 预算。

### 3.2 数据与标注

- **L8 按证据标注，不按实现细节标注。**
  - lite-rag 用“文档 + 原文证据句”判对错，不用 chunk id：chunk id 换一种分块方式就变，证据句不会。
  - 证据句被切到两个 chunk 里时算没答对：这是分块方式真实的弱点，不是标注集的问题。
  - 落实：§5.1 规定 `expected` 只能引用被测系统之外也成立的东西。
- **L9 指标要配合题目的形状。**
  - 多跳题的 hit@1 天然是 0：第 1 名是链条的第一环，答案在第 2–3 名，所以要看 hit@5（lite-rag S11）。
  - lite-mem 的两跳题用“每一跳的关键词组都在前 k 名里”来判。
  - 落实：pack 决定默认指标；用例可以用标签声明自己该看哪个 k。
- **L10 合成数据集可能根本没测到要测的功能。**
  - 发生了什么：
    - lite-mem S7 扫描了合并阈值 0.80、0.85、0.90、0.95，四个配置都一条没合并。原因是冲突消解在写入时已经丢掉了重复，合成集没给合并留下任何东西，阈值最后只能从相似度表里定。
    - 所有系统（包括 mem0）都把银行卡密码 `482915` 存成了记忆，正好说明数据集里要有“不该发生”的用例。
  - 落实：
    - R13：所有配置结果一样时，提示数据集可能没覆盖这个功能；
    - `expected` 支持否定期望（`must_not`）；
    - 合成数据在报告里标明；
    - 外部基准（LongMemEval、LoCoMo）用来检验结论能不能推广（R18）。
- **L11 小数据集的数字只说明一件事：大差距是真的，小差距是噪声。**
  - 在 lite-mem S5 的 19 项检查上，mem0 留下 7 个过期事实里的 7 个，lite-mem 只留 1 个：这是大差距，可以下结论。
  - 一两项之差就不能下结论。
  - 落实：报告里给每个结论标上证据强度，例如 `measured, one run, small set`，用词和 KB 一致。
- **L12 用例数决定能分辨多小的差距。**
  - 落实：报告的表头写出“1 个用例 = 多少分”，让读者自己判断。

### 3.3 被测系统与可比性

- **L13 被测系统的 bug 会伪装成评测结果。**
  - 发生了什么：MiniLM 的分词器文件自带 128 token 截断，固定分块悄悄丢掉了后面的文字。结果是所有检索模式（包括 BM25）的 hit@1 都是 0.25（lite-rag 2026-11-11 矩阵）。
  - 结论：差别很大的配置得到完全相同的分数，是红色警报，不是结论。
  - 落实：R13 的“完全相同”警告；排名指标用 ranx 交叉核对（lite-rag 用 ranx 0.3.21 核对过全部六个配置）。
- **L14 非确定性的系统，每次运行都会变。**
  - 图抽取用到本地模型，有一题在两次运行之间换了结果；qwen3:8b 偶尔返回 500 或超时。
  - 落实：R7；出错的用例计入失败，同时单独计数。
- **L15 一个组件的效果取决于其他组件。**
  - rerank 抵消了图检索带来的提升：cross-encoder 只拿问题和单个 chunk 比较，更喜欢长得像的兄弟 chunk。
  - 用了 rerank 之后，换哪个 embedding 几乎没有区别。
  - 落实：Suite 支持配置矩阵，相同索引配置共用一次 setup（R14）。
- **L16 延迟的口径要说清楚，并排除冷启动。**
  - lite-rag 第一次运行的第一个配置显示 40 ms，其实是没缓存的查询向量，不是分词器加载。
  - 改成：查询向量在计时前算好，再做一次不计时的预热搜索。
  - 落实：Target 声明延迟口径；runner 在 setup 后做预热（R14）。
- **L17 “没做的决定”也要留下痕迹。**
  - lite-mem 里被判为重复（NOOP）而丢掉的事实，不写任何事件，历史里看不出它为什么不见了。
  - 找出“上海被当成北京的重复”这个问题，靠的是其他事件上记下的理由。
  - 落实：Trace 的 span 可以记录决策和理由（R15），Viewer 的用例详情里展示出来。

### 3.4 模型、通道与本机

- **L18 本地模型慢且不稳。**
  - qwen3:8b 每次写入 7.9–73.9 秒，偶尔 500 或超时。
  - llm-steward 用本地模型同步时，单次调用超过 worker 的 30 秒上限，要靠重试才能完成。
  - qwen3:8b 有一次抽取出“上周搬家了”，城市却丢了。
  - 落实：每个用例都有超时设置，超时算失败、照样记录；默认并发不高（4）。
- **L19 思考模式会改变质量和速度，必须当作配置记下来。**
  - qwen3:8b 关掉推理时，把“住在上海”判成了“住在北京”的重复并丢掉；开着推理时判对了，但慢了约 5 倍（每段对话 47 秒对 10 秒）。
  - 落实：Target 的 `config` 里必须包含 thinking 和 effort 的设置（R4）。
- **L20 订阅通道和 API 的结果不一样。**
  - 落实：运行记录里写明调用通道（例如 llm-gateway profile `claude-sub` 和 `claude-api` 是两回事）。不能只凭订阅通道的结果就上线（KB `global/llm-gateway/README.md`）。
- **L21 要可复现，就得记下精确的版本。**
  - 每个结果页都写着引擎版本（mem0 2.2.1、ranx 0.3.21、bge-m3:567m、pydantic-ai-slim 2.61.0），换了就重测。
  - 落实：R4。
- **L24 绝不在测试里调用订阅和付费模型。**
  - 单元测试和集成测试用假的模型；真实的本地模型只在可选的 marker 下运行。
  - llm-gateway 的订阅 profile（`gpt-sub`、`claude-sub`）和按 token 计费的 profile，只在 owner 要求时才调用。
  - 落实：R12 CI 模式；lite-eval 自己的测试只用 fake Target 和 fake 评审。

### 3.5 工程与流程

- **L22 管道的退出码会撒谎。**
  - `pytest | rg | tail && git commit` 两次在测试失败时照样提交了（lite-mem），因为管道的退出码是最后一个命令（`tail`）的。
  - 落实：
    - 门禁的退出码必须如实（R11），lite-eval 的 CLI 不能把失败吞成 0；
    - 自己的开发流程中，先跑测试、读汇总行，再单独一步提交。
- **L23 新测试必须见过一次失败。**
  - 写完测试，故意改坏被测的那一行，确认关键断言会失败，再恢复。
  - 被中断的改坏脚本会把改动留在源码里，提交前要先看 diff。
- **L25 CI 红了没人注意。**
  - llm-steward 从 P5 到 P7 的 CI 一直是红的：本地的 `.env` 掩盖了一个测试。
  - 落实：每次推送后读 CI 结果（`gh run list`）；测试不依赖本地 `.env`。
- **L26 `uv sync` 不带参数会丢掉 extras**，失败看起来像回归。一律用 `uv sync --locked --all-extras`（CI 同样）。
- **L27 共用数据库的测试不能同时跑两套。** 每个项目用自己的测试 schema。lite-eval 的 SQLite 索引在测试里用临时目录。
- **L28 pydantic-settings 的字段别名会让带前缀的环境变量失效。**
  - lite-mem 里，测试设的 `LITEMEM_SCHEMA` 没生效，迁移跑在了应用 schema 上。
  - 落实：settings 字段不用别名，并写测试确认环境变量确实生效。
- **L29 第二个实现能找出第一个藏住的问题。**
  - lite-mem 加了 SQLite 存储后，才发现同一毫秒生成的 uuid7 排序是随机的。
  - 落实：运行目录（文件）和 SQLite 索引要跑同一套读取测试；`run_id` 的排序要稳定（§12）。
- **L30 运行记录里会有提示词和输出，可能含敏感内容。**
  - lite-mem 的脱敏审查了四轮才补全（`sslpassword`、带 `&` 的密码、`PGPASSWORD` 这类前缀）。
  - KB 规定不记录客户资料、人名和密钥。
  - 落实：R20，并沿用 lite-mem 的脱敏思路（整段连接串按 libpq 解析，自由文本按规则处理），每种形式都有测试。
- **L31 指数退避的指数要封顶。**
  - llm-steward 的轮询延迟大约在第 47 次时让 `timedelta` 溢出，修法是把翻倍次数限制在 `ceil(log2(上限 / 起始))`。
  - 落实：runner 的重试照此实现。
- **L32 测试的时间预算要宽。** 共享的 CI 机器上，一次假搜索用了 800 多毫秒。默认给 10 秒，专门测超时的测试自己设。
- **L33 端口和进程。**
  - 杀进程、释放端口之前，先看是谁在占用（`lsof -ti tcp:<port> -sTCP:LISTEN` + `ps -o command= -p <pid>`）。
  - 新端口登记到 KB `global/machine/ports.md`。
  - Ollama 用 `brew services run ollama` 启动、`brew services stop ollama` 停止，不用 `start` 或 `restart`，默认保持停止。
- **L34 第三方 API 不凭记忆写。** ranx、FastAPI、pydantic、Inspect AI 这类库，先查 context7 或已安装的源码，把核对过的版本写进注释。

## 4. 核心概念与数据流

```
Suite（一次评测的声明：数据集 + 被测系统及其配置 + 评分器 + 指标 + 门禁 + 预算）
  │
  ▼
Dataset ──▶ Target ──▶ Output + Trace ──▶ Scorer ──▶ Run Record ──▶ Analysis
 (cases)    (被测系统)   (输出 + 中间过程)    (打分)      (不可变记录)    (汇总 / 对比 / 门禁 / 报告 / Viewer)
                │                              ▲
                └──── 输出缓存（按配置 + 用例）───┘   改评分标准：只重跑打分
```

| 概念 | 含义 |
| --- | --- |
| Case | 一个用例：输入、期望、标签、来源 |
| Dataset | 一组用例，有名字、版本和内容哈希 |
| Target | 被测系统的一个配置：`run(case) → Output（含 Trace）` |
| Trace | 被测系统的中间过程：检索命中、工具调用、模型调用、决策和理由、token、耗时、费用 |
| Scorer | 评分器：`score(case, output) → Score` |
| Pack | 一种任务类型的默认组合：`expected` 的格式 + 默认评分器 + 汇总指标 + Viewer 里的展示方式 |
| Suite | 一份声明（YAML 或 Python） |
| Run | Suite 里一个 Target 配置跑完的结果，写成不可变的运行记录 |
| Baseline | 被指定为参照的某次 Run；门禁和对比都以它为准 |
| Annotation | 人对某个用例结果的归类和备注，单独存放 |

## 5. 契约

所有契约都是 `backend/src/lite_eval/contracts.py` 里的 pydantic 模型，序列化为 JSON 或 JSONL，每条记录带
`schema_version`（从 1 开始）。前端只通过 OpenAPI 生成的客户端读取这些契约。

### 5.1 Case

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str | 在数据集内唯一且稳定：改内容不改 id |
| `input` | JSON | 结构由 pack 约定。检索：`{"query": …}`；记忆：`{"history": […], "query": …}`；agent：`{"task": …}` |
| `expected` | JSON \| null | 结构由 pack 约定。检索：`{"doc": …, "evidence": …}`；两跳：`{"hops": [[…], […]]}`；否定：`{"must_not": […]}` |
| `tags` | dict[str, str] | 分组统计用：`kind`、`lang`、`difficulty`、`split`、`k`（这道题该看哪个 k） |
| `provenance` | `{source, synthetic, author, created, license}` | 出处 |

`expected` 只能引用被测系统之外也成立的东西，例如原文证据句、文档名、事实关键词，不能引用 chunk id 这类实现细节（L8）。

### 5.2 Dataset

- 一个目录：`dataset.yaml`（名字、版本、描述、pack、语料位置、许可证、`synthetic`）+ `cases.jsonl` + 可选的 `corpus/`。
- **内容哈希**：用例按 id 排序、规范化 JSON 后计算 sha256，语料文件一起算进去。哈希写进每次运行记录。
- **加载器**：本地目录；外部基准 LongMemEval、LoCoMo（E4），以后加 BEIR 子集。加载器负责转成 Case，并记下原始出处和许可证。

### 5.3 Target

```python
class Target(Protocol):
    name: str
    def config(self) -> dict[str, Any]: ...            # 决定输出的一切：模型、thinking/effort、参数、索引配置；进缓存键
    def versions(self) -> dict[str, str]: ...          # 影响输出的版本：引擎、模型快照、提示词；进缓存键（§18.5）
    def info(self) -> dict[str, str]: ...              # 只记录：git sha、主机、调用通道；不进缓存键
    def billing(self) -> Literal["free", "subscription", "per_token", "unknown"]: ...   # CI 守卫（§18.13）
    def setup_identity(self) -> dict[str, Any]: ...    # 相同的值共用一次 setup（例如索引配置；§18.18）
    async def setup(self, dataset: Dataset) -> None: ...   # 建索引、灌数据；不计时
    # 可选：async def share_setup_from(self, leader: "Target") -> None   # 接过另一个实例建好的 setup（§18.18）
    async def run(self, case: Case) -> Output: ...
    async def teardown(self) -> None: ...
```

三种接入方式：

| 方式 | 适用 | 说明 |
| --- | --- | --- |
| Python | Python 应用 | 实现上面的协议；lite-eval 只作为开发依赖 |
| HTTP | 任何有 API 的应用（TS、Go……） | Suite 里写 URL、请求模板（Jinja2，变量是 `case.input`），以及从响应里取 `output` 和 `trace` 的 JSONPath |
| 子进程 | 命令行工具、其他语言 | 每个用例往 stdin 写一行 JSON `{"case": …, "repeat": n}`，从 stdout 读一行 `{"case_id": …, "repeat": n, "output": …, "trace": …}`；进程常驻、逐个用例串行，跑完所有用例（§18.11） |

### 5.4 Output 与 Trace

```
Output: {schema_version, case_id, repeat: int, value: JSON | null, trace: Trace, error: {kind, message} | null}
Trace:  {spans: [Span], usage: {input_tokens, output_tokens, reasoning_tokens, cost_usd}, latency_ms, latency_scope}
Span:   {kind: "retrieve" | "rerank" | "llm" | "tool" | "memory.write" | "memory.read" | "decision" | str,
         name, start_ms, duration_ms, input, output, attrs}
```

- `repeat` 从 0 开始（§18.1）。
- `error.kind` 取值 `timeout`、`exception`、`truncated`、`invalid_output`、`http_status`：都计为失败，在报告里分类计数（R5）。
  出错时 `trace.usage` 和 `latency_ms` 仍然填写已知的部分（§18.8）。
- `kind="decision"` 记录被测系统的决策和理由，例如 `{"op": "NOOP", "reason": "duplicate of …"}`（L17）。
- `latency_scope` 由 Target 声明口径，例如 `"retrieval+rerank, query vectors precomputed"`（L16）。

### 5.5 Score 与 Scorer

```
Score: {schema_version, case_id, repeat, scorer, scorer_version, value: float, passed: bool | null,
        reason: str, evidence: JSON, judge: JudgeRecord | null, error: {kind, message} | null}
JudgeRecord: {role, model, channel, prompt_version, temperature, raw}
```

- `Score.value` 取值 [0, 1]，越大越好；组合评分器的结果也在这个范围内。延迟、费用、token 不是 Score，是从 Trace 算出的汇总指标（越小越好），门禁对它们用 `max`（§18.19）。

```python
class Scorer(Protocol):
    name: str
    version: str                       # 评分逻辑一改就升版本
    def score(self, case: Case, output: Output) -> Score: ...
```

| 类 | 例子 | 说明 |
| --- | --- | --- |
| 确定性 | 证据句包含（规范化空白，大小写按 pack 设置）、关键词组、精确匹配、集合召回和精确率、排名指标（ranx）、JSON schema、正则、`must_not` | 首选 |
| 模型评审 | 忠实度、是否答非所问、规则遵守 | 校准之后才能用（§9） |
| 人工 | Viewer 里逐条打标 | 存为 Annotation，用于校准集和抽查 |

可以组合：`all_of`、`any_of`、加权平均。每个评分器的结果都单独保留。

### 5.6 Run Record

一次运行一个目录（§12）：

- `run.json`：
  - 标识：`schema_version`、`run_id`、`suite`、`target.name`；
  - 被测系统：`target.config`、`target.versions`；
  - 数据集：`dataset{name, version, hash, synthetic}`；
  - 评分器：`scorers[{name, version}]`；
  - 运行参数：`repeats`、`seed`、`redact`（生效的脱敏规则名和自定义正则，导出时复用，§18.12）；
  - 运行环境：`started_at`、`finished_at`、`host`、`lite_eval_version`、`git_sha`、`git_dirty`；
  - 结果：`status`（`complete`、`partial`、`failed`）、`summary`、`cost`、`errors`、`warnings`；
  - 来源：`rescored_from`。
- `outputs.jsonl`：每个用例（每次重复）一行 Output。
- `scores.jsonl`：每个用例、每个评分器一行 Score。

运行记录写成后**不再改动**。重新打分生成一个新 run，并用 `rescored_from` 指向原来的 run。

## 6. 执行（runner）

- **两段式**：
  - 先生成：调用 Target，结果写进缓存。缓存键见 §18.5（配置、影响输出的版本、用例哈希、重复序号）。
  - 再打分。换评分器、改阈值、改评审模型时，只重跑打分（`lite-eval rescore`）。
  - `--no-cache` 强制重新生成。
- **重复**：`repeats: n`。报告给出均值、最小值、最大值；Target 声明自己是非确定性的时，默认 `n = 2`。
- **并发**：用例之间并行（`concurrency`，默认 4）；子进程 Target 例外，一个进程内严格串行（§18.11）。每个用例有超时（`timeout_s`，默认 120），超时计为 `error.kind=timeout`。
- **可比性**：
  - `setup_identity` 相同的配置共用一次 setup（生命周期见 §18.18）；
  - setup 之后做一次不计时的预热（§18.18）；
  - 配置的执行顺序不影响结果（测试里打乱顺序验证）。
- **重试**：只对传输层错误重试，指数退避且指数封顶（L31），最多 3 次。被测系统返回的业务错误不重试。
- **预算**：`budget.max_cost_usd`、`budget.max_calls`。到上限就停，已完成的部分照常记录，`status=partial`。
- **CI 模式**（`--ci` 或 `CI=true`）：
  - 计费守卫见 §18.13：凡是不能确认免费的都拒绝运行；
  - 不读本地 `.env`。
- **自检**（R13）：运行结束后检查以下情况，写进 `warnings`：
  - 多个配置的逐用例分数完全相同；
  - 所有用例都失败；
  - 某个标签分组没有用例；
  - 数据集是合成的。

## 7. Pack（按任务类型）

| Pack | `expected` | 默认评分器与指标 | 首个使用者 |
| --- | --- | --- | --- |
| `retrieval` | `{doc, evidence}` 或 `{relevant: [ids]}` | 证据命中；hit@k、MRR@10、nDCG@k、recall@k（ranx）；延迟 P50/P95 | lite-rag、llm-steward |
| `memory` | 多跳：`{hops: [[kw…]…]}`；抽取：`{facts: […]}`；更新：`{superseded: […], kept: […]}`；`must_not` | 每跳命中、全部命中；事实召回和精确率；取代是否正确；过期事实数；拒答 | lite-mem、llm-steward；LongMemEval、LoCoMo |
| `extraction` | `{items: […]}` | 召回优先；miss 列表；重复计数 | tenderted 类 |
| `judging` | `{label}` | 一致率、混淆矩阵；支持“改坏”式的构造用例 | 评审模型的校准（§9） |
| `grounded` | `{must: […], must_not: […]}` | 无依据的说法、规则遵守、是否切题（模型评审 + 规则） | RAG 写作 |
| `agent` | `{goal, checks: […]}` | 任务完成、工具调用正确、步数、费用 | 以后的 agent 应用（E6） |
| `cost` | — | 每个用例的 token（含推理）、费用、延迟；所有 pack 都带 | 全部 |

## 8. 分析

### 8.1 汇总

- 每个指标都给出整体值，并按 `tags` 分组。
- 报告的表头写明“1 个用例 = x 分”（L12），出错和 partial 的用例单独计数。
- 延迟分位数全局统一：`p50` = `statistics.median`，`p95` = 最近秩法（排序后取第 `ceil(0.95·n)` 个值），不插值；和 §18.16 的 lite-rag 定义一致。
- 每个结论附上证据强度（L11）。

### 8.2 对比

`compare(baseline, candidate)`：

- **只比可比的**：数据集哈希和评分器版本都相同才直接比；不同时给出警告，只比两边都有的用例。
- **逐用例配对**：新失败、新修好、两边都失败、两边都通过。**退步先列**。
- **显著性**：
  - 二值指标用 McNemar 精确检验；
  - 连续指标用配对 bootstrap（10 000 次，固定种子），给出 95% 区间。
  - 不显著时直接写“差异不显著（n=20，不一致 1 例）”。
- **重复运行**：用每个用例的均值配对；波动大于差值时给出警告。

### 8.3 Miss 归类

- 每次运行都输出 miss 列表（未通过的用例）。
- 在 Viewer 或 `lite-eval triage` 里逐条归类：`real_miss`、`label_wrong`、`excluded`、`unclear`，可以加备注。
- 归类存为 Annotation，挂在 `(dataset name, case id, dataset hash)` 上，以后的运行自动显示。
- 归为 `label_wrong` 的用例进入“待修标注”列表，修好后出新的数据集版本，不能靠“忽略”带过。

### 8.4 门禁

- **写在 Suite 里**：
  - 绝对阈值：`gate: [{metric: hit@5, min: 0.92, baseline_note: "0.960 on 2026-11-09"}]`；
  - 相对阈值：`{compare_to: baseline, max_drop: 0.02, require_significant: true}`。
  - 精确语义（方向、`max`、重复运行、`require_significant`）见 §18.19。
- **阈值的定法**：比实测基线低一点，留出约一个用例的余量；把基线值和日期写在旁边（来自 llm-steward 的 recall 门禁）。
- **入口**：
  - CLI：`lite-eval run --gate`，不通过以退出码 1 退出，配置错误以退出码 2 退出；
  - pytest：`lite_eval.assert_gate(run)`，失败信息列出退步的用例；
  - Python API。

## 9. 模型评审

- **调用方式**：通过角色调用（`judge.<name>`），角色到模型的映射在配置里。
  - 本地开发走 llm-gateway（`http://127.0.0.1:8787/v1`）或 Ollama；
  - 代码里不写死厂商或模型名；
  - 不做 fallback 链：调用失败就算评分错误。
- **先校准才能用**：
  - 每个评审维度要有一个带人工标签的校准集，可以用 L2 的构造法得到确定的标签。
  - 校准就是一次 `judging` pack 的运行；一致率达到 Suite 里的阈值，才允许在正式 Suite 里引用这个评审。
  - 新维度上线前，还要在真实输出的样本上看一次标记率（L2）。
- **固定**：模型、通道、提示词版本、温度都写进 `JudgeRecord`；任何一项变了，评分器版本就变。
- **结构化输出**：评审返回 `{verdict, score, reason, evidence}`，由 pydantic 校验；解析失败记为评分错误，不算通过。
- **提示词**放在 `backend/src/lite_eval/prompts/*.j2`，不写成内联字符串。
- **测试**：用 fake 评审；真实模型只在可选的 marker（`-m ollama`）下运行。

## 10. Viewer（前端）

`lite-eval view` 在本机启动：FastAPI 提供只读 API（加上 Annotation 的写入），同时托管构建好的单页应用。
默认 `127.0.0.1:8720`，只监听 loopback，非 loopback 的地址拒绝启动。

### 10.1 页面

| 页面 | 内容 |
| --- | --- |
| **Runs** | 所有运行：suite、target 配置、数据集版本、主要指标、费用、时间、状态；按 suite、pack、标签筛选；标出 baseline、合成数据集和带警告的运行 |
| **Run 详情** | 汇总指标和分组指标（表头写“1 个用例 = x 分”）；费用和延迟分布；完整的配置和版本记录；错误分类；自检警告 |
| **对比** | 选两次运行：指标差值、显著性和区间；逐用例变化，先列退步；数据集或评分器不一致时给出警告 |
| **用例详情** | 输入、期望、输出并排；Trace 时间线（检索命中、工具调用、模型调用、决策和理由、耗时、token）；每个评分器的分数、理由和证据；评审的原始回复；同一用例在其他运行里的结果 |
| **Miss 归类** | 逐条处理 miss，归类并加备注；“待修标注”列表 |
| **数据集** | 浏览用例、出处、许可证、版本和哈希；检查标注对不对，可以加一条“标注有误”的 Annotation（不改数据集文件，§18.9） |
| **趋势** | 某个 suite 的某个指标随时间或 git 提交的变化，标出 baseline 切换和数据集换版本的位置 |

### 10.2 技术

- **前端**：React + Vite + TypeScript、TanStack Router 和 Query、shadcn/ui + Tailwind、Vitest、Playwright，和 llm-steward 的
  Admin 一致（参考 `~/Code/AI/llm-steward/frontend/`）。客户端从后端的 OpenAPI 生成（`@hey-api/openapi-ts`）。
- **打包**：构建出的静态文件复制进 Python 包（`lite_eval/server/static/`）。`pip install lite-eval` 后直接 `lite-eval view`，
  使用者的机器上不需要 Node。
- **数据**：
  - 文件（运行目录）是真相源；
  - 启动时扫描运行目录，建一个 SQLite 索引（放在 `.lite-eval/index.sqlite`），只用于筛选和趋势查询，可以随时删掉重建；
  - 运行目录有新内容时增量更新索引。
- **唯一的写操作是 Annotation**：追加写进 `annotations.jsonl`，不改运行记录和数据集。
- **静态导出**：`lite-eval report --html` 生成单个自包含的 HTML 文件（数据内嵌，不连服务端），可以发给别人或发布成 Artifact。
- **设计方向**：密集的数据工具，参考 KB 的 `design-direction` skill；页面以表格和并排对比为主。

## 11. 接口

### 11.1 Suite（YAML）

```yaml
schema_version: 1
name: lite-rag-retrieval
pack: retrieval
dataset: datasets/lite-rag-golden          # 或 {loader: longmemeval, path: …, variant: s}
targets:
  - python: lite_rag_eval_target:Target    # 或 http: {...} / command: [...]
    configs:                               # 一个 Target、多组配置 → 多个 run
      - {name: hybrid, mode: hybrid}
      - {name: hybrid-rerank, mode: hybrid, rerank: true}
scorers: [evidence_hit]                    # 不写就用 pack 的默认评分器
metrics: [hit@1, hit@3, hit@5, mrr@10, latency_p50, latency_p95]
repeats: 1
concurrency: 4
timeout_s: 120
gate:
  - {metric: hit@5, min: 0.90, baseline_note: "0.95 on 2026-11-08"}
budget: {max_cost_usd: 0}
redact: [connection_strings, api_keys]
```

### 11.2 CLI（`lite-eval`）

```
lite-eval run <suite.yaml> [--config NAME] [--no-cache] [--gate] [--crosscheck] [--ci]
lite-eval rescore <run> [--scorer …]          # 只重跑打分
lite-eval compare <baseline> <candidate>
lite-eval triage <run>                        # 在终端里逐条归类 miss
lite-eval baseline set <run>                  # 运行记录里有 suite 名
lite-eval view [--root DIR] [--host 127.0.0.1] [--port 8720] [--check]
lite-eval report --html <run | baseline..candidate> -o out.html [--redact RULE]
lite-eval export kb <run>                     # KB results.jsonl 的行（§13）
lite-eval datasets list | show <name> | hash <path>
```

退出码：0 通过；1 门禁未通过；2 配置或输入错误；3 运行失败。精确定义和优先级见 §18.4。

### 11.3 Python 与 pytest

```python
from lite_eval import run_suite, assert_gate
run = run_suite("evals/suites/retrieval.yaml", config="hybrid")
assert_gate(run)                     # 在 pytest 里调用；失败时打印退步的用例
```

## 12. 存储与目录

**默认放在使用方的仓库里**，运行记录跟着代码走，便于按提交追溯：

```
<使用方仓库>/evals/
  suites/*.yaml
  datasets/<name>/{dataset.yaml, cases.jsonl, corpus/…}
  runs/<suite>/<run_id>/{run.json, outputs.jsonl, scores.jsonl}
  baselines.yaml              # suite → 作为 baseline 的 run_id
  baselines.log.jsonl         # 每次 `baseline set` 追加一行（E3 起），趋势页据此标出切换
  annotations.jsonl
  .lite-eval/                 # gitignored
    cache/                    # 输出缓存
    index.sqlite              # Viewer 的索引，可以重建
```

- `run_id` 的格式和排序见 §18.7。
- baseline 对应的运行提交进仓库，其余 gitignore（§17）。

## 13. 与现有项目和 KB 的关系

- **迁移**：每接入一个项目，迁移前后的数字必须逐项相同（同一配置、同一数据）。迁完后删除原项目的评测脚本，改为调用 lite-eval：
  - lite-rag：E2（已迁移，2026-11-14，lite-rag S12：`literag eval` 是 lite-eval 上的薄包装）；
  - lite-mem 和 llm-steward：E4。
- **被测系统适配器放在使用方的仓库里**（例如 lite-rag 的 `src/lite_rag/eval.py` 里的 `LiteRagTarget`），lite-eval 不依赖任何被测项目。
- **KB**：
  - `lite-eval export kb <run>` 生成 `global/llm-evals/results.jsonl` 格式的行（字段以 KB README 里的 schema 为准），然后按 KB 的规则写评测卡片；
  - 模型选型的结论仍然记在 KB，lite-eval 只负责算出数字。
- **lite-eval 自己的项目 KB** 是 `projects/lite-eval/`，在 E0 建立，放决定、经验和踩过的坑。写入用 `kb-maintain` skill。

## 14. 开源参考与取舍

| 项目 | 取什么 | 不取什么 |
| --- | --- | --- |
| Inspect AI（英国 AISI，MIT） | Dataset → Solver → Scorer 的分层；日志加查看器；agent 沙箱 | 核心不依赖它：它以“调模型”为中心，我们的确定性检索和记忆评测套进去不划算。E6 做 agent pack 时作为可选的执行后端：把 Case 和 Scorer 转成 Inspect Task，再把它的日志转回运行记录 |
| promptfoo（MIT） | 声明式配置、对比视图、CI 用法 | Node 实现，偏 prompt 层 |
| DeepEval | pytest 风格的门禁 | 指标大多靠 LLM 评审 |
| Ragas（Apache-2.0） | RAG 生成端指标的定义：忠实度、上下文精确率和召回率 | 维护状态待核实（有报道说 2026-02 之后就没有提交），不作依赖 |
| ranx | **直接依赖**：排名指标（对照 TREC Eval 测过）、统计检验 | — |
| LongMemEval（ICLR 2025）、LoCoMo、BEIR | 外部基准，做成数据集加载器 | 不能直接和别人自报的分数比：评审模型、token 预算都不同 |
| Phoenix、Langfuse | 以后可以从它们导出的追踪里取数据集 | 不做追踪平台 |

## 15. 仓库结构

```
lite-eval/
  AGENTS.md  CLAUDE.md  README.md  LICENSE
  docs/lite-eval-design.md
  backend/                         # Python 包 lite_eval（uv）
    AGENTS.md  CLAUDE.md
    pyproject.toml
    src/lite_eval/
      contracts.py                 # Case, Dataset, Output, Trace, Span, Score, JudgeRecord, RunRecord, Annotation
      config.py                    # pydantic-settings，前缀 LITE_EVAL_，字段不用别名
      datasets/                    # 本地目录加载、哈希；loaders/（longmemeval, locomo）
      targets/                     # python.py, http.py, command.py, fake.py（测试用）
      scorers/                     # deterministic.py, ranking.py（ranx）, judge.py, compose.py
      packs/                       # retrieval, memory, extraction, judging, grounded, agent, cost
      runner.py                    # 两段式、缓存、并发、超时、重试、预算、setup 共享、预热、自检
      store.py                     # 运行目录读写、baseline、annotation
      index.py                     # Viewer 的 SQLite 索引
      analysis/                    # summary.py, compare.py（McNemar、bootstrap）, gate.py
      report/                      # terminal.py, html.py, kb.py
      redact.py
      server/                      # FastAPI 只读 API + static/（前端构建产物，构建时复制进来）
      prompts/                     # 评审提示词 *.j2
      cli.py                       # Typer
      pytest_plugin.py
    tests/{unit,integration,e2e}/  # e2e：真实的子进程和 HTTP Target（本地起的假服务）
    examples/                      # 每种接入方式一个最小示例 + 示例数据集
  frontend/                        # Viewer（pnpm）
    AGENTS.md  CLAUDE.md
    package.json
    src/{client/（生成）, routes/, components/, lib/}
    tests/（Playwright）
```

## 16. 阶段与验收

每个阶段结束时，以下检查都要通过：

- backend：`uv run ruff check`、`uv run ruff format --check`、`uv run mypy`、`uv run pytest`；
- 有前端的阶段，再加：`pnpm typecheck`、`pnpm lint`、`pnpm test`、`pnpm build`、`pnpm e2e`；
- 每个新测试至少见过一次失败（L23）。

然后做阶段收尾（见 `CLAUDE.md`）：

1. 对整个阶段的 diff 跑 `/simplify`，并应用修改；
2. 用 Codex 只读审查同一个 diff；
3. 修掉成立的问题（bug 先写失败的测试），不成立的写下理由；
4. 重跑全部检查并报告结果；经 owner 同意后提交、推送，然后读 CI 结果；
5. 在下面的“阶段记录”里追加一条。

收尾完成之前不开始下一阶段。

**v1 的完成线是 E3 结束**：E0–E3 交付核心功能（契约、两段式 runner、确定性评分、对比和门禁、lite-rag 迁移）和 Viewer。
Viewer 留在 v1 里，是因为 miss 归类（L1，第一条经验）要靠它逐条做；趋势页和 HTML 导出建在 E3 已有的索引和 API 上，
增量不大，不拆出去。E4 及以后是 v1 之后的扩展。

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| E0 环境 | 仓库骨架（backend 的 uv 项目、frontend 的 pnpm 项目、CI）；用 context7 或安装的源码核对并锁定 pydantic、pydantic-settings、typer、ranx、scipy、fastapi、uvicorn、jinja2、httpx2 的版本和 API（用 httpx2 不用 httpx：openai SDK 和 Starlette 的 TestClient 都用它，见 E0 阶段记录）；端口 8720（Viewer）、8721（Vite dev）登记进 KB `global/machine/ports.md`；建项目 KB `projects/lite-eval/` | `lite-eval --help` 可用；CI 绿；KB 建好并通过校验 |
| E1 契约与执行 | `contracts`；本地数据集和规范哈希（§18.6）；Python Target 和 fake Target；确定性评分器；两段式 runner（缓存、重复、并发、超时、重试、预算、自检、脱敏）；运行目录（原子写入）；`run`、`rescore`；终端报告；cost pack | 同一 Suite 跑两次，第二次全部命中缓存且结果相同；`versions()` 变了缓存失效（§18.5）；改了评分器后 `rescore` 不调用 Target；契约 round-trip，旧版本记录可读；出错、超时、截断、评分器出错都被记录并计入分母（§18.8）；预算到了以后 `status=partial`，退出码按 §18.4；配置顺序打乱后结果相同；CI 模式对未声明计费方式的 Target 和评审拒绝运行（§18.13）；`identical_scores` 和 `feature_not_exercised` 两个自检各有触发和不触发的测试（§18.17）；脱敏规则覆盖输出、trace、错误、缓存（§18.12）；cost pack 报出 token（含推理）和费用的汇总；CI 放行付费只在环境变量和 Suite 同时允许时生效，只满足一个仍拒绝，放行写进 `run.json`（§18.13）；数据集里疑似密钥导致加载报错，按匹配项豁免后通过并写进 `run.json`（§18.12） |
| E2 检索 pack 与对比 | retrieval pack（证据命中、ranx 指标、延迟）、setup 共享和预热、`compare`（§18.1–§18.3）、门禁（CLI 和 pytest）、baseline、排名指标交叉核对；**迁移 lite-rag** | 按 §18.16 的 lite-rag 条目：逐题 `answer_rank`、`doc_rank` 相同，聚合指标误差 ≤ 1e-9；McNemar 和 bootstrap 用手算的例子核对；不兼容的对比拒绝做门禁；门禁的退出码按 §18.4；门禁语义按 §18.19（`min`/`max`、`max_drop` 加或不加 `require_significant`、重复运行）各有测试；同一 `setup_identity` 的两个配置 setup 只调用一次、预热不计时且不进记录，teardown 在组内全部跑完后（§18.18）；自写指标和 ranx 在测试里一致；lite-rag 改为调用 lite-eval |
| E3 Viewer | 只读 API、SQLite 索引（§18.15）、前端各页面（§10.1）、Annotation（§18.9、§18.10）、HTML 导出、打包进 Python 包（§18.14） | Playwright e2e：列出运行 → 运行详情 → 对比 → 用例详情 → 给 miss 归类；数据集页加一条“标注有误”；趋势页显示 baseline 切换；导出的单次运行和对比两种 HTML 不连服务端都能打开；Viewer 拒绝非 loopback 地址、非 loopback 的 Host 和 Origin、没有 token 的写入，任何响应都不带 CORS 头（§18.10）；导出用 run 记录的脱敏规则，用例内容含 `</script>` 时导出的 HTML 不被截断、不执行脚本（§18.12）；两个 Viewer 不能同时用一个目录；文件和索引跑同一套读取测试；干净 venv 里装 wheel 后 `lite-eval view --check` 成功 |
| E4 记忆 pack 与接入 | memory pack；HTTP 和子进程 Target（§18.11）；LongMemEval 和 LoCoMo 加载器；**迁移 lite-mem 的两跳集和 llm-steward 的 recall 门禁** | 按 §18.16 的 lite-mem、llm-steward 条目逐项相同；llm-steward 的 `-m eval` 改用 lite-eval 的门禁；HTTP 和子进程 Target 的每种失败（状态码、超时、超长、非 JSON、进程退出）都有测试；子进程 Target 在 `concurrency=4` 下每个回复对应正确的用例，回复的 `case_id`/`repeat` 不符记为 `invalid_output`（§18.11）；两个加载器用各自的小样例文件测试转换结果和许可证记录；LongMemEval-S 用本地模型跑通一个小子集（可选 marker） |
| E5 模型评审 | 评审评分器、校准流程；judging、extraction、grounded 三个 pack；KB 导出 | 校准集上测出一致率；构造的反例被正确识别；评审解析失败记为评分错误；未校准的评审不能被正式 Suite 引用；KB 导出用固定的样例运行生成，和提交的期望文件逐字相同，并通过 KB 的 `check.mjs`（导出格式写明对应 KB README 的哪个 schema 版本） |
| E6 agent（以后） | agent pack；Inspect AI 桥接（可选） | 等有 agent 应用要测时再定 |
| E7 收尾 | 全仓库 `/simplify` 和 Codex 审查；README；发布方式 | 审查结论记录在案 |

### 阶段记录

（每个阶段完成时在这里追加：做了什么、/simplify 改了什么、Codex 的结论、没采纳的理由。）

- **设计（2026-11-13，在 llm-steward 的会话里完成）**：写了设计文档和各级 agent 指南。只有文档，不跑 /simplify。
  - Codex 第一轮提出 6 个高、13 个中优先级的问题：重复运行、迁移的“数字一致”、退出码优先级、Annotation 写入的安全和并发、HTTP 和子进程 Target 的细节、脱敏范围、缓存键与版本、统计细节、run_id 排序、前端打包、SQLite 索引、CI 计费守卫、哈希、失败的记录、验收覆盖、可比性。全部采纳，写成 §18，并相应修改 §2、§5、§6、§10–§12、§16。
  - Codex 第二轮又提出 4 个问题：缓存键缺数据集和语料哈希；HTTP 和子进程 Target 没有声明版本的办法；Annotation 的更正链可能分叉；指南和 §18.13 的说法不一致。全部采纳。
- **设计审查第三轮（2026-11-13，lite-eval 仓库内，开工前）**：Codex 对整份设计做只读审查，提出 12 条（4 高、6 中、2 低）。
  - 采纳：
    - 子进程 Target 和并发冲突 → §18.11 串行、回复带 `case_id`/`repeat`，超时后重启；
    - setup 共享没有归属 → 新增 §18.18（分组、leader/follower、teardown 顺序、只读、预热）；
    - 门禁语义不清 → 新增 §18.19，`Score.value` 定为 [0, 1]（§5.5）；
    - 分位数只在迁移节定义 → §8.1 全局统一；
    - 阶段收尾写着“提交、推送” → 改为经 owner 同意后；
    - 导出用哪套脱敏规则、内嵌 JSON 转义 → §18.12，`run.json` 记录 `redact`（§5.6）；
    - 数据集密钥豁免太宽 → §18.12 按匹配项豁免；
    - 显式 `cache: on` 的风险 → §18.5 每次运行都带警告；
    - 验收缺项 → §16 E1–E4 补上 CI 放行路径、密钥扫描、setup 共享、门禁语义、CORS、导出转义、子进程并发。
  - 部分采纳：读接口跨域。浏览器的同源策略已经挡住跨域读取，DNS rebinding 由 Host 检查挡住；只补“不发 CORS 头”和对应测试（§18.10），不给读接口加 token。
  - 不采纳：bootstrap 退化情况。区间为 [0, 0] 时按“区间不含 0 才显著”的规则自然判为不显著；NaN 已被规范 JSON 的 `allow_nan=False` 挡住。不需要新规则。
  - v1 范围（Codex 建议砍到核心 + 基础 Viewer）：定为 E0–E3，理由写在本节开头。
  - 另外修正：R12 和 §18.13 对齐；§4 的 `run(input)` 改为 `run(case)`；`backend/AGENTS.md` 的规则编号。
- **E0 环境（2026-11-13）**：
  - 做了什么：
    - backend：uv 项目，Python 3.12，hatchling；全部依赖精确锁定。
    - `lite-eval` CLI：只有 `--help` 和 `--version`，命令在实现它的阶段才加。
    - `tests/unit/test_dependencies.py`：用手算值锁住后面阶段要用的第三方 API，在安装的源码上核对过。
    - `tests/unit/test_packaging.py`：wheel 和 sdist 带上 LICENSE。
    - frontend：单包 pnpm 骨架，占位页面加一个组件测试。
    - CI：backend 和 frontend 两个 job，action 锁到 commit SHA，`permissions: contents: read`。
    - KB：建了 `projects/lite-eval/`，含决定表和 `reference/dependencies.md`；端口 8720、8721 登记进 `ports.md`。
    - 每个新测试都见过一次失败。
  - 设计改动：E0 行 httpx → httpx2。
    - 原因：openai SDK 3.50 依赖 httpx2；Starlette 1.10 的 TestClient 优先用 httpx2。
    - 一个进程只用一个 HTTP 客户端：ruff banned-api（TID251）禁止 `import httpx`，`test_lockfile_has_one_http_client` 确认锁文件里没有它。
  - /simplify 采纳 9 条：
    - CLI 帮助文案去重；
    - hatchling `artifacts` 合并到 `[tool.hatch.build]`；
    - mypy 也检查 `tests`；
    - httpx 检查从运行时断言改为 lint 规则加锁文件测试；
    - `tests/conftest.py` 固定 `TERMINAL_WIDTH`，CLI 测试不再合并空白；
    - 删掉断言不了任何东西的 uvicorn 测试（E3 用 `view --check` 验证）；
    - 前端 build 不再重复跑 tsc；
    - CI 的 pnpm 版本从 `packageManager` 读；
    - 新增 `backend/README.md` 作为包的说明。
  - /simplify 未采纳：
    - ranx 测试约 5 秒：numba 首次编译的真实开销，改成可选标记会让 API 失去保护；
    - setup-uv 缓存：v10.3.3 默认 `enable-cache: auto`，在 GitHub 托管的机器上已开启；
    - KB 依赖页里重复的版本号：这一页就是“哪个版本、哪天核对过”的记录。
  - Codex 4 条，全部采纳：
    - action 改为锁 commit SHA；
    - wheel 和 sdist 缺 LICENSE：先写失败的 `test_packaging.py`，再复制 `backend/LICENSE`，测试同时断言两份内容相同；
    - 删掉无意义的 `binomtest(0, 1)` 断言；
    - 阶段状态和阶段记录对齐。
  - 第一次 CI 是红的（`3484fe0`）：`test_help_exits_zero_and_names_the_tool` 失败。
    - 原因：typer 0.29.1 在设置了 `GITHUB_ACTIONS`、`FORCE_COLOR` 或 `PY_COLORS` 时强制输出彩色终端，ANSI 码把 `Usage: lite-eval` 隔开了；本地输出不是终端，没有颜色。
    - 本地用 `GITHUB_ACTIONS=true uv run pytest` 复现了失败。
    - 修法：`tests/conftest.py` 设 `_TYPER_FORCE_DISABLE_TERMINAL=1`。typer 在 import 时读这个开关，和 `TERMINAL_WIDTH` 放在同一处。
    - 修完后三种变量下测试都通过。
- **E1 契约与执行（2026-11-13）**：
  - 做了什么：
    - 契约：Case、Output、Trace、Score、RunRecord、Annotation（§5）；规范 JSON 和哈希（§18.6）；数据集目录、加载时的密钥扫描和逐项豁免（§18.12）。
    - Target：Python Target 和 FakeTarget。
    - 评分器：7 个确定性评分器，以及 all_of、any_of、weighted 组合。
    - 两段式 runner：缓存、重复、并发、超时、重试、整个 Suite 共用的预算、自检、脱敏、CI 计费守卫。
    - 运行目录（原子写入）、`run` 和 `rescore`、终端报告、cost pack。
    - `backend/examples/`：一个最小的 Python Target 加上 3 条用例。
    - 新增 §18.20，写明设计没定死的接入方式和 runner 细节。
  - 验证：
    - pytest 153 passed；在 `CI=true GITHUB_ACTIONS=true` 下也通过。
    - 每个新测试都见过一次失败：脚本 60 处改坏，每一处都让对应测试失败。
    - 改坏脚本第一轮有两处“假绿”：Python 按源文件的修改时间（精确到秒）和大小判断 `.pyc` 是否过期，同一秒内两次大小相同的改坏会测到旧字节码。脚本改为不写字节码并每次清掉 `__pycache__`。
    - CI 里出现 skip 就失败（`tests/conftest.py`），实际试过：`CI=true` 时有 skip，退出码 1。
  - /simplify 的四个审查找出 4 个 bug，都先写失败的回归测试再修：
    - 缓存命中的输出没有按当前规则脱敏（Suite 加了规则后会漏出）；
    - 预算按配置计算，3 个配置能花 3 倍预算；
    - FakeTarget 靠调用顺序推断重复序号，部分缓存命中时会错位，改成 runner 设置的上下文变量 `current_repeat`；
    - 同一个组合评分器写法不同时 id 不同，改成规范化后再生成 id。
    - 修复过程中另一个已有测试抓到 `from_spec` 把生成器遍历两遍的 bug。
  - /simplify 采纳的简化：
    - 原子写入和 JSONL 读取合并到 `files.py`，脱敏规则只编译一次，Output 和 Score 的脱敏收进 `Redactor`；
    - `execute` 拆成运行单个配置和生成记录两步，自检分成只看生成结果的和依赖分数的两类（`SCORE_DERIVED`）；
    - 配置名唯一、未知 pack、`redact` 写法都在加载 Suite 时检查；
    - 删掉没用到的代码，评分器 id 缓存起来不再重复计算。
  - /simplify 未采纳：
    - p50 改用最近秩法：§8.1 定为 `statistics.median`，迁移 lite-rag 时数字才能一致；
    - 缓存读写放到线程里：在几千个用例的规模下，切换线程的开销比省下的还多；
    - 多个配置并发执行：会改变并发、预算、setup 的语义，需要另做设计决定；
    - 运行目录流式写入：目前用不上。
  - Codex 5 条：
    - 采纳 4 条，都先写失败的测试：
      - 预算用完时，最后一次传输错误的失败结果被丢掉；
      - Suite 在根目录之外时 `rescore` 用不了，改为记录带 `..` 的相对路径；
      - `token=abc}` 会把右括号一起替换掉；
      - 运行目录里重复的输出会悄悄覆盖，改为读取时拒绝。
    - 不采纳 1 条（“并发时 `max_calls` 会被超出”）：检查预算和计数之间没有 `await`，asyncio 是单线程的，不会被插进来。加了 `test_max_calls_is_exact_under_concurrency` 证明这一点。
- **E2a 检索 pack、对比、门禁（2026-11-14，lite-eval 仓库）**：E2 分两片：E2a 在 lite-eval 里做，E2b 迁移 lite-rag。
  - 做了什么：
    - pack 注册表和 retrieval pack；检索评分器 `hit@k`、`doc_hit@k`、`mrr@k`、`ndcg@k`、`recall@k`，证据句判定和 lite-rag 的 `judge` / `_norm` 逐字一致，和 ranx 0.3.21 交叉核对，并提供 `--crosscheck`。
    - setup 共享和预热（§18.18）。
    - 对比（McNemar、bootstrap、可比性、`within_noise`）；门禁、baseline；`compare`、`baseline set`、`run --gate` 命令，以及 `assert_gate`。
    - 契约只加字段：`RunRecord.case_hashes`、`ScorerRef.verdict`、`Comparison` 等，`RunRecord.gate` 改为有类型的 `GateSpec`。
    - 新增 §18.21 记录接入细节；`backend/examples/` 加了一个检索示例。
  - 验证：
    - pytest 244 passed；改坏脚本 E2a 43/43、E1 64/64 都变红。
    - 手动用检索示例走了一遍：两个配置 → `compare` → `baseline set` → `run --gate`，退出码 0 和 1 都按预期。
  - 实现中发现并修掉的 bug（都先写失败的测试）：
    - 降幅正好等于 `max_drop` 时浮点误差会判失败，改为留 1e-9 容差；
    - 二值评分器的 `:pass_rate` 和值重复；
    - 通过率按数据推断，导致 MRR 有一个出错用例就冒出无意义的 `:pass_rate`，改为评分器声明。
  - /simplify（四个审查）：
    - 3 个 bug：
      - compare 从值去猜二值，连续指标在小数据集上全是 0/1 时选错检验，改为记录 `verdict`；
      - cost 指标的相对门禁要等运行花完钱才报错，改为加载 Suite 时拒绝；
      - 门禁可比性规则写了两份且已不一致，合成一个 `gate_problems`。
    - 采纳的改进：bootstrap 改为差值重抽样并分块（固定种子的结果先钉住，改完不变）；门禁直接用 runner 手里的分数，baseline 只读一次且跳过 `outputs.jsonl`；检索指标两种模式一套公式；`normalise` 加缓存；交叉核对和记录里的值比；`assert_gate` 复用终端格式；`root_of`；测试辅助函数合并。
    - 未采纳：
      - `warming_up` 上下文变量留在协议里（已写进文档）；
      - `share_setup_from` 暂不单独抽成子协议；
      - ranx 不合并成一次调用：`--crosscheck` 是可选诊断；
      - 不按评分器预先分组分数：规模内省不了多少时间。
  - Codex：
    - 采纳 2 条，都先写了失败的测试：
      - follower 的 teardown 抛错会跳过 leader，并顶替掉原来的错误；
      - 预热抛出带用量的 `TargetError` 时费用丢失。
    - 不采纳 1 条：“差值和波动都为 0 时也该报 `within_noise`”。只跑一次的运行波动总是 0，这样每次打平都会警告，没有信息量；§18.1 已写明只在波动 > 0 时警告。
    - Codex 说的三处覆盖缺口，有两处已经有测试：阈值正好相等由 `test_a_drop_within_max_drop_passes` 覆盖，id 去重和退回用 doc 由 `test_relevance_mode_metric_values`、`test_relevance_mode_prefers_a_hit_id_over_its_doc` 覆盖；第三处（出错路径上的 teardown）已随上面的修复补上测试。
- **E2b 迁移 lite-rag（2026-11-14，lite-rag 仓库，作为 lite-rag 的 S12）**：
  - 依赖规则改了（AGENTS 规则 13，owner 2026-11-14 定，`b1a49a9`）：运行时依赖声明版本区间，下限是测试通过的最旧版本，由 CI 任务 `lowest` 检查，上限是下一个大版本；`uv.lock` 仍锁精确版本。起因：lite-rag 的 docling extra 要求 typer < 0.28，和精确的 `typer==0.29.1` 解析不出来。
  - 做了什么：
    - lite-rag 加可选 extra `eval`，按提交锁定 lite-eval（`b1a49a9`）；`src/lite_rag/eval.py` 改写成适配器 `LiteRagTarget`（§5.3），删掉自写的 `judge`、`metrics`、`_norm`、`_nearest_rank`、`run_configs` 和 `eval/crosscheck_ranx.py`。
    - `literag eval` 参数和表格不变，另加 `--root`、`--crosscheck`；金标集和语料转成 lite-eval 数据集，每个配置一次 lite-eval 运行，表格下打印每个运行的状态和警告；lite-eval 的错误在 CLI 里是一行错误、退出码 1。
    - 同一索引的配置共用一次 setup（§18.18）；setup 不计时地算好查询向量；每个运行在 `target.info.latency_scope` 记下延迟口径。
  - 验证（§18.16 两层）：
    - 评分器：2000 组随机命中喂给冻结的旧 `judge`（`tests/legacy_eval.py`）和 lite-eval 的评分器，逐题排名相同；
    - 端到端：同一进程、同一 store，4 套金标集逐题 `answer_rank`、`doc_rank` 相同，整体和按 `kind` 分组的四个指标误差 ≤ 1e-9（FakeEmbedder 进 CI；bge-m3 用 `-m ollama`，本地通过）；
    - 本机真实运行三个配置，和 lite-rag S5 基线逐项相同；
    - lite-rag pytest 480 passed，改坏脚本 19/19 变红。
  - /simplify 和 Codex 的结论记在 lite-rag 设计的 S12 实现说明里。和 lite-eval 有关的不采纳项：
    - 不公开 `run_suite(targets=…)`：lite-rag 直接用 `runner.execute`（它的 target 共用一个 store 和 embedder，`python: module:factory` 表达不了）；等第二个使用方再定公开 API，在此之前 lite-rag 按提交锁定，每次升级跑等价测试；
    - lite-eval 不在 setup 失败后调用 teardown：那要改 Target 协议；setup 可能半途失败的适配器在 setup 里自己清理（lite-rag 就这么做）。
- **E3a Viewer 后端（2026-11-14，lite-eval 仓库）**：E3 分两片：E3a 后端，E3b 前端、e2e 和打包。
  - owner 定（2026-11-14）：用例内容从数据集按哈希取，不在运行目录里另存；`baseline set` 追加写 `baselines.log.jsonl`；`lite-eval triage` 在 E3 一起做。
  - 做了什么（细节见 §18.22）：
    - annotation 的读写（§18.9）；
    - SQLite 索引和文件两种 catalog（§18.15）；
    - `views`，以及 FastAPI 的只读 API 和 annotation 写入，安全规则按 §18.10；
    - `view`（含 `--check`）、`triage`、`report --html`；
    - 提交 `backend/openapi.json`，过期时测试失败。
  - 验证：
    - pytest 322 passed；在 `CI=true` 下、以及按最低声明版本安装的 venv 里（同 CI 的 `lowest` 任务）也都通过。
    - 改坏脚本 63/63 变红。
      - 一条等价变异已删掉，原因：只追加且持锁写入时，"没被取代的那条"总是该键最后写入的那条。
      - 两条变异会让 `view` 真的启动起来挂住：对应测试改为服务一旦启动就失败，脚本超时时杀掉整个进程组。
    - 用 lite-rag 真实的 `eval/lite-eval/` 走了一遍 API：3 个运行、miss、按哈希取回的用例内容、显著性对比、趋势。
  - 实现中发现并修掉的问题：
    - fastapi 0.133 加 pydantic 2.9（声明的下限）生成的规范，对只有一个值的 `Literal` 会多写一个 `enum`。导出规范时去掉与 `const` 重复的单元素 `enum`，锁定版本和最低版本得到同一份文件。
  - /simplify（四个审查）：
    - 3 个 bug，都先写了失败的测试，或写明为什么测不了：
      - partial 运行里没生成的用例原来不算 miss，分数也缺失，违反规则 4。现在逐用例结果由 `analysis.summary.case_results` 按运行汇总的同一口径计算，记为 `not_generated`、得 0 分；
      - 导出对整份数据做盲脱敏，会改写 `case_id` 这类 id，也违反 §18.12 的"数据集不脱敏"。现在按类型脱敏，和 Viewer 是同一套代码；
      - baseline 日志追加后没有 fsync。现在共用 `files.append_jsonl`；fsync 本身没有测试能观察到。
    - 采纳的改进：
      - 加载和组装分开：导出和 triage 每个运行只加载一次（原来每个用例都要重载运行、所有数据集和其他运行）；
      - 按名字找数据集只读 yaml 和用例，不再哈希语料；"其他运行"先读 run.json，哈希对不上就不解析 outputs；
      - 两个 catalog 共用一条查询路径（原来 SQL 和 Python 各有一份过滤）；
      - 新增 `annotate()`、`store.run_path` / `read_record`、`errors.NotFound`；
      - 去掉 `_Only`、各种 limit 哨兵值和死代码；地址校验改成纯函数并直接测试；测试辅助函数集中到 `tests/helpers.py`。
    - 不采纳：
      - 按版本条件比较 OpenAPI 规范：会削弱检查，CI 又不许跳过测试；
      - `report A..B` 改成两个参数：§11.2 定的公开语法，留给 owner 决定；
      - 查询参数模型、SQL 分页、后台刷新线程、数据集摘要缓存：在几百个运行的规模下收益很小。
  - Codex：1 条成立，已修（先红后绿）：对比导出把两个运行都放在第一个运行的评测根目录里找；现在各自从自己的目录加载。其余方面（安全、加锁、索引、转义、脱敏、逐用例口径）没有发现问题。
- **E3b Viewer 前端、e2e、打包（2026-11-14）**：E3 完成，**v1（E0–E3）完成**。
  - 做了什么（细节见 §18.22）：
    - 七个页面：Runs、运行详情、用例详情、对比、miss 归类、数据集、趋势；
    - 数据都经过 `src/data/source.ts`（实时 API 和导出页两种数据源）；
    - Playwright e2e 跑在 fake target 生成的夹具上，端口 8722；
    - `scripts/build_frontend.py`；CI 新增 `package` 任务（干净 venv 里装 wheel 后跑 `view --check`）和 `release` 任务（打 `v*` tag 时发布）。
  - 视觉：
    - 方向 dense-data 由任务指定。owner 回答了两轮访谈，写成 `PRODUCT.md`；
    - 方向说明写在 `.impeccable/surfaces/frontend-src.md`；`DESIGN.md` 和 `.impeccable/design.json` 由 Impeccable 的 documenter 从构建出来的界面整理；
    - 状态色用 dataviz 的校验脚本验证过两种主题；标志性设计是区间条和用例条码。
  - Impeccable 完成审查：三轮。
    - 第一轮 8 组问题，前两轮之后全部解决：区间条的斜线纹理和空心点、手机宽度下结论和差值并排、趋势图的整数刻度、固定首列和滚动渐隐、用例页和归类页的信息、运行详情的版式；
    - 修复中引入的一个回归（手机宽度下页面多出 20px）也已解决；
    - 剩一条等 owner 决定：方向说明里没有 seed，因为方向由任务指定、没有抽方向。审查规则要求 owner 明确认可这条。
  - 验证：
    - 后端 pytest 328 passed（锁定版本、`CI=true`、最低声明版本三种环境）；
    - 前端：单元测试 14 条、e2e 8 条（验收流程、离线导出、主题）；
    - 改坏脚本：前端 20/20 变红（单元 + e2e），后端这轮新增测试 7/7 变红；
    - 在干净的 venv 里装 wheel 后，`view --check` 返回两个 200；用 lite-rag 的真实数据截了 bootstrap 区间的图。
  - /simplify（四个审查），采纳：
    - bug：
      - 用例超过 1000 条时静默截断：上限提到 100000，用例详情带上一个 / 下一个；
      - 导出页的筛选只认一半：导出页不再提供筛选；
      - 变化方向由浏览器推断：改为 `CaseChange.direction`；
    - 导出数据改为 pydantic 模型并写进 OpenAPI，起始路由由后端给出；
    - 保存 annotation 后只让相关查询失效；运行相关的查询永不过期；用例条码做 memo；
    - 共用 `useShortcuts`、`rowProps`、`queries.ts`、`Select` / `KeyHints` / `WarningsList` / `RunLink` / `ContentNote`；
    - `light-dark()` 让颜色变量只写一份；"pass"格子提到 3:1；
    - CI 的 e2e 不重复构建，只装 `[server]` extra，缓存 uv 和 Playwright 的浏览器。
  - /simplify 不采纳：
    - 把 e2e 的期望写进夹具生成的清单：夹具一变 e2e 就会大声失败，收益低；
    - 把用例条码移出用例表、做 `Th` / `Td` 表格组件：收益小；
    - 给趋势页做专门的选项接口：当前规模下 memo 就够了。
  - Codex：
    - 采纳 3 条，都先写了失败的测试：
      - 趋势表的行缺少运行标记：`TrendPoint` 加 `status`、`warnings`、`synthetic`；
      - 很小的数显示成 0（一个用例的权重、显著但很小的差值）：三位小数会舍成 0 时改用两位有效数字；
      - CI 没检查 sdist 带不带前端。
    - 不采纳 1 条："焦点在勾选框上时按 X 会切换那一行"。勾选框是 `input`，键盘守卫会忽略它，补了 `format.test.ts` 钉住这个行为。焦点在链接上时 J/K 仍然移动列表是有意为之。

## 17. 已定的开工问题（2026-11-13）

开工前问 owner 的四个问题。第一条由 owner 定，其余由 owner 交给实现方按设计的建议决定。

- **公开**：公开在 GitHub `ArthurGao/lite-eval`，许可证 Apache-2.0（和 llm-steward 一致）。
- **运行记录**：baseline 对应的运行提交进使用方的仓库，其余 gitignore。`lite-eval baseline set` 时提示提交那个运行目录。
- **E4 的评审模型**：只用本地 Ollama。付费模型只在 owner 针对某一次运行明确要求时才用，并按 §18.13 声明计费方式。
- **发布方式**：v1 不发 PyPI。前端构建产物在 gitignore 里，直接从 git 安装会缺少 Viewer，所以 CI 打包任务（§18.14）把带前端的 wheel
  作为 GitHub Release 的附件发布，使用方按版本号安装这个 wheel。仅从 git 按提交安装也可以，但只能用 API 和 CLI，`lite-eval view`
  会给出 §18.14 的“先构建前端”错误。E7 再决定是否发 PyPI。

## 18. 规格细节（实现时以此为准）

前面各节说明要做什么、为什么；这一节把容易有歧义的地方定死。两处说法不一致时，以本节为准（并修正前面的说法）。

### 18.1 重复运行

- `repeat` 从 0 开始，写在每条 Output 和 Score 上，同时进缓存键。
- **每个用例的值**：对一个评分器，取该用例所有重复的 `value` 的均值。出错的重复按失败计（`value=0`、`passed=false`），不是缺失。
- **运行级指标**：先算每个用例的值，再对所有用例取平均。排名指标（hit@k、MRR）先按每次重复分别算，再按用例取平均。
- **波动**：每次重复分别算一遍运行级指标，报告最小值、最大值和均值。
- **对比时重复次数不同**：用每个用例的均值配对，这时一律用 bootstrap（§18.2），不用 McNemar。
- **波动警告**：两次运行中，只要有一方的运行级指标波动范围（最大值 − 最小值）大于 0、并且大于等于两者均值之差，就给出 `within_noise` 警告。波动为 0 时不警告：只跑一次的运行波动总是 0，和 baseline 打平时警告不提供任何信息（E2 Codex 审查讨论后定）。

### 18.2 显著性检验

只用两边都有、且可比（§18.3）的用例。出错的用例是失败，不算缺失。

- **McNemar**：两边都只跑一次（`repeats=1`）、指标是二值时使用。
  - 计数：`b` = 基线通过、候选失败的用例数；`c` = 基线失败、候选通过的用例数。
  - 精确二项检验，双侧，不做连续性校正：`p = min(1, 2 · BinomCDF(min(b, c); b + c, 0.5))`；`b + c = 0` 时 `p = 1`。
  - `p < 0.05` 算显著。
- **配对 bootstrap**：其他所有情况使用。
  - 重抽样单位是用例：有放回地抽 n 个用例，两边取同一批用例；`B = 10000`。
  - 统计量 = 候选的指标均值 − 基线的指标均值。
  - 区间用百分位法 95%；区间不含 0 算显著。
  - 种子 = `int(sha256(baseline_run_id + "|" + candidate_run_id + "|" + metric)[:16], 16)`，保证可复现。
- 报告里总是写出 n、不一致的用例数和 p 值（或区间）。

### 18.3 可比性

- **数据集名字不同**：不能比，报错（退出码 2）。
- **数据集哈希不同**：按用例 id 对齐，并比较每个用例自身的哈希（规范 JSON 的 sha256）。
  - 只比内容相同的用例；
  - 改过、新增、删除的用例分别列出来；
  - 结果标为 `partial_overlap`。
- **评分器不同**：同一个指标的评分器名字或版本不同时，这个指标标为 `incomparable`，不显示差值。
- **门禁的规定**：
  - 门禁引用了 `incomparable` 的指标时，以退出码 2 结束（配置错误），不能悄悄通过；
  - `partial_overlap` 的对比默认不能做相对门禁，除非 Suite 写了 `gate.allow_partial_overlap: true`，并且报告里写明。
- **重复的用例 id**：加载数据集时直接报错。

### 18.4 退出码与优先级

按下面的顺序判断，命中第一条就停：

1. **2 配置或输入错误**：Suite 或数据集不合法、对比不可比（§18.3）、CI 守卫拒绝运行（§18.13）。这些都在调用 Target 之前检查。
2. **3 运行失败**：runner 自身崩溃，或者一个用例都没有打上分（例如还没开始预算就用完了）。
3. **1 门禁未通过**：任何一个门禁条件不满足。
   - 用例出错算失败，会拉低指标，但不会单独导致退出码 3。
   - `status=partial` 的运行默认算门禁失败，除非写了 `gate.allow_partial: true`。
4. **0**：其他情况。

pytest 的 `assert_gate` 对应抛出 `ConfigError`、`RunError`、`GateFailed`（继承 `AssertionError`）。

### 18.5 缓存

- **Target 分开声明两类信息**：
  - `versions()`：会影响输出的版本，例如引擎版本、模型 id 和它对应的具体快照、提示词版本；**进缓存键**。
  - `info()`：只做记录，例如 git sha、主机名；不进缓存键。
- **缓存键** = `sha256(canonical({"target": name, "config": config(), "versions": versions(), "setup": setup_identity(), "dataset": dataset_hash, "case": case_hash, "repeat": repeat}))`。
  - 数据集哈希包含语料（§18.6），所以 `setup` 用到的语料一改，所有用例的缓存都会失效。宁可多重算，也不能拿旧输出冒充新结果。
- **HTTP 和子进程 Target 的版本**：在 Suite 里写 `versions: {…}`，这是静态声明。另外可以写 `versions_from`，二选一：
  - `versions_from: {url: …, path: "$.versions"}`：在 setup 时请求一次；
  - `versions_from: {command: [...]}`：在 setup 时运行一次，从 stdout 读 JSON。
  - 两者都没写时，这个 Target 的缓存默认关闭（`cache: off`），并给出 `unversioned_target` 警告；Suite 显式写 `cache: on` 才会打开。
  - 显式打开后，`unversioned_target` 警告仍然写进每一次运行的 `warnings`：被测代码换了而 URL 或命令没变时，缓存会返回旧输出，这个风险要一直看得见。
- **存储**：每条缓存存一份脱敏后的 Output（§18.12），位置是 `.lite-eval/cache/<键的前两位>/<键>.json`。运行记录复制一份 Output，不引用缓存。
- **读取**：读到损坏的缓存条目就当作没有命中，并给出警告。

### 18.6 规范 JSON 与哈希

- **规范 JSON**：
  - 所有字符串先做 Unicode NFC 规范化；
  - 然后 `json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)`，按 UTF-8 编码。
- **用例哈希** = 规范 JSON 的 sha256，覆盖 `{id, input, expected, tags}`（不含 `provenance`）。
- **数据集哈希** = sha256 依次喂入：
  1. 字节串 `b"lite-eval-dataset-v1\n"`；
  2. `dataset.yaml` 里 `name`、`version`、`pack` 三个字段的规范 JSON；
  3. 按 id 排序后每个用例的哈希；
  4. 对语料目录里的每个文件，按 POSIX 相对路径排序，依次喂入 `path + "\0" + sha256(文件字节)`。
- 语料里不允许符号链接，遇到就报错；修改时间等元数据不参与哈希。

### 18.7 run_id 与排序

- **格式**：`run_id = <UTC 时间，精确到微秒，YYYYMMDDTHHMMSSffffffZ>-<配置名 slug>-<6 位随机十六进制>`。
- **排序**：一律按 `(started_at 的 UTC 微秒, run_id)`。同一微秒内的先后没有意义，只是保证顺序稳定，不承诺反映真实时间顺序。

### 18.8 失败的记录

- **Target 出错**：抛 `TargetError(kind, message, usage=…, latency_ms=…)`，runner 把已知的用量和耗时写进 Output。
  - 其他未捕获的异常记为 `exception`，用量为空。
  - 被截断的输出（例如 `finish_reason=length`）记为 `truncated`，`value` 仍然保留已经生成的部分。
- **评分器或评审出错**：Score 的 `error` 有值，同时 `value=0`、`passed=false`。
- **汇总**：单独计数 `target_errors`、`scoring_errors`；分母始终是全部用例 × 重复次数。

### 18.9 Annotation

- **字段**：

  ```
  Annotation: {schema_version, annotation_id (uuid7), dataset, case_id, case_hash, run_id | null,
               category: real_miss | label_wrong | excluded | unclear, note (≤ 4 KB), author, created_at,
               supersedes: annotation_id | null}
  ```

- **只追加**：更正时写一条新记录，用 `supersedes` 指向旧记录。
- **当前有效的归类**：对某个 `(dataset, case_id, case_hash)`，是没有被任何记录取代的那一条。
  - 写入时，必须在持有文件锁的情况下，确认 `supersedes` 指向的记录仍然有效；不是的话拒绝写入（HTTP 409，CLI 提示重新加载），所以不会出现分叉的更正链。
  - 新建（`supersedes` 为空）时，如果这个键已经有有效记录，也拒绝写入，要求改为更正那一条。
  - 因此每个键任何时候最多只有一条有效记录，不需要靠时间戳排序。
- **用例内容变了**（`case_hash` 不同）：旧的归类不自动带到新内容上，Viewer 里显示为“旧版本的归类”。
- **不改数据集**：真正修标注，是在 git 里改数据集文件，于是哈希变化（R9）。Annotation 不需要版本号。
- **写入**：追加一行到 `annotations.jsonl`，写前持有排他文件锁（`fcntl.flock`，锁文件 `annotations.jsonl.lock`），写完 `fsync`。Viewer 和 `lite-eval triage` 用同一个锁。
- **补充（E3 定）**：
  - `note` 的上限是 UTF-8 的 4096 字节，不是 4096 个字符（中文一个字三个字节）。E3 之前没有写过 annotation，所以这条收紧不影响旧记录。
  - `author` 依次取 `LITE_EVAL_AUTHOR`、`git config user.name`、登录名；Viewer 的请求里不带作者。
  - `annotation_id` 是手写的 UUIDv7（Python 3.12 没有 `uuid.uuid7`）。
  - 更正必须和被更正的那条是同一个键，否则拒绝。
  - **miss** 的定义：该用例有输出出错、有重复没有生成输出（partial 运行，错误记为 `not_generated`），或者有评分器给出 `passed=false` 或评分出错。逐用例的分数和运行汇总同一口径（§18.1：缺的重复算 0），由 `analysis.summary.case_results` 计算。Miss 归类页和 `triage` 只列 miss。

### 18.10 Viewer 的安全

- 只监听 loopback 地址；请求的 `Host` 必须是 loopback（防 DNS rebinding）。
- **写请求**（只有 annotation）要满足三点：
  - `Origin` 必须是 Viewer 自己的 loopback 地址；
  - 请求头带上 `X-Lite-Eval-Token`：这是每个进程启动时随机生成的 token，注入在页面里，用来防 CSRF；
  - 请求体由 pydantic 校验，大小上限 16 KB。
- 读接口不需要 token，但同样要检查 `Host`。
- **不发任何 CORS 头**（不装 CORS 中间件，`OPTIONS` 预检不放行）：浏览器的同源策略因此挡住其他网站读取 Viewer 的响应。测试确认响应里没有 `Access-Control-Allow-*`。
- 本机的其他进程能读到 token，这在威胁模型之内可以接受（Viewer 只给本人用）。
- 参考 llm-gateway 的 `server/guard.py`。

### 18.11 HTTP 与子进程 Target

**Suite 是受信任的本地配置**：它和代码一样，能执行命令、访问网络。不要从不可信的来源加载 Suite。
runner 开始时会把要执行的命令或 URL 打印出来。

**HTTP Target**

- **配置项**：
  - `method`（默认 POST）、`url`；
  - `headers`：值可以写 `${ENV_NAME}`，运行时从环境变量取，永不写进日志或记录；
  - `body`：Jinja2 模板，用 `SandboxedEnvironment` 渲染，变量是 `case`；
  - `output` 和可选的 `trace`：JSONPath，支持 RFC 9535 的子集 `$.a.b`、`$.a[0]`、`$.a[*].b`；
  - `timeout_s`；`max_response_bytes`，默认 10 MB。
- **响应处理**：
  - 2xx：按 JSON 解析，用 JSONPath 取值；
  - 其他状态码：`error.kind=http_status`；
  - 不是 JSON，或 JSONPath 取不到值：`invalid_output`；
  - 超过大小上限：`truncated`；
  - 传输层错误：按 §6 重试。

**子进程 Target**

- **启动**：在 `setup` 里用 argv 列表启动，不经过 shell。配置项有 `cwd` 和环境变量白名单 `env_allow`。
- **就绪**：进程启动后，必须在 `startup_timeout_s` 内输出一行 `{"ready": true}`。
- **每个用例**：往 stdin 写一行 `{"case": …, "repeat": n}`，在超时时间内从 stdout 读一行 `{"case_id": …, "repeat": n, "output": …, "trace": …}`；一行超过 `max_line_bytes`（默认 10 MB）记为 `truncated`。
- **串行**：一个进程同一时间只处理一个用例，runner 用锁保证写一行、读一行之间没有别的用例插进来；`concurrency` 对子进程 Target 不生效。回复里的 `case_id` 或 `repeat` 和当前用例不符，记为 `invalid_output`。
- **超时后**：进程里可能还留着迟到的回复，所以超时的用例记为 `timeout` 之后，重启进程再继续。这种重启不计入下面“进程中途退出只重启一次”的次数。
- **stderr**：脱敏后写进运行目录的 `target.stderr.log`，大小上限 5 MB。
- **进程中途退出**：当前用例记为 `exception`，进程重启一次；再退出，剩下的用例都记为 `exception`，运行标为 `partial`。
- **结束**：`teardown` 时关闭 stdin，等待 5 秒，然后 `terminate`，最后 `kill`。

### 18.12 脱敏

- **范围**：所有写到磁盘或显示出来的字符串：
  - Output 的 `value`；
  - trace 里每个 span 的 `input`、`output`、`attrs`；
  - 错误信息；
  - Score 的 `reason` 和 `evidence`；
  - `JudgeRecord.raw`；
  - 缓存条目、`target.stderr.log`、各种导出。
- **时机**：runner 在写缓存和写运行记录之前执行一次；server 和导出在输出之前再执行一次。
- **导出用哪套规则**：run 的 `run.json` 里记下的 `redact`，加上导出命令的 `--redact` 追加的规则；只能加，不能减。对比导出用两个 run 规则的并集。
- **内嵌数据的转义**：HTML 导出把数据放进 `<script type="application/json">`，序列化后把 `<`、`>`、`&`、U+2028、U+2029 转成 `\uXXXX`，用例内容里的 `</script>` 不能结束这个标签。
- **规则**：
  - 内置 `connection_strings`：URL 形式和 `key=value` 形式的连接串都按 libpq 的规则解析，名字里含 `password` 的参数一律打码（沿用 lite-mem 的做法）；
  - 内置 `api_keys`：`sk-…`、`stw_…`、`Bearer …`、`AKIA…`，以及 `api[_-]?key`、`token`、`secret` 后面跟 `=` 或 `:` 的值；
  - 内置 `emails`；
  - Suite 自定义的正则。
  - 替换成 `«redacted:<规则名>»`。
  - 脱敏必须是线性时间：被测系统的输出不受我们控制，平方级的规则会在对抗性的长文本上卡住（实测 16k 字符 287 ms，1 MB 估计十几分钟）。
    - 可能从很多位置起步匹配的部分都有长度上限：URL 的 scheme ≤ 32、用户名 ≤ 256、主机名 ≤ 255；邮箱本地部分 ≤ 64、域名 ≤ 255；参数名 ≤ 64。
    - URL 里的密码**没有**长度上限，也可以含未编码的 `@`、`/`：先找出所有 `scheme://user:` 起点和所有合法的 `@host` 位置，每个起点用二分查找它后面、同一个无空白 token 里的第一个 `@host`。
    - 测试确认 100 KB 的几种对抗文本都在 2 秒内完成，1500 字符的密码整段被替换。
- **数据集不脱敏**：数据集本来就不该有敏感内容（R20）。加载时会扫描一遍，发现疑似密钥就报错。
  - 误报按匹配项逐条豁免：`dataset.yaml` 里写 `secret_scan_allow: [{case_id, rule, match_sha256, reason}]`，`match_sha256` 是命中文本的 sha256，`reason` 必填。没有整个数据集一次豁免的写法。
  - 用到的豁免写进 `run.json` 的 `warnings`；列了却没命中的豁免也给出警告，提醒删掉。
- **测试**：每种形式至少一个测试用例，并写明哪些部分应该保持可读。

### 18.13 CI 中的计费守卫

- **每个会调用模型的地方都要声明计费方式**：`billing: free | subscription | per_token`。
  - 评审：在角色映射里写；
  - Python Target：`billing()` 方法；
  - HTTP 和子进程 Target：在 Suite 里写。
- **没有声明**：评审一律按 `per_token` 处理（宁严勿宽）；Target 记为 `unknown`。
- **CI 模式下**（`--ci` 或 `CI=true`）：只要有一处不是 `free`，就拒绝运行，退出码 2。例外需要两个条件同时满足：
  - 环境变量 `LITE_EVAL_ALLOW_PAID=1`；
  - Suite 里写了 `allow_paid_in_ci: true`。
  - 放行这件事会写进 `run.json`。
- **本地默认**：loopback 上 11434 端口（Ollama）默认算 `free`；llm-gateway 的 profile 必须显式声明计费方式。

### 18.14 打包前端

- `backend/scripts/build_frontend.py`：运行 `pnpm -C ../frontend install --frozen-lockfile && pnpm -C ../frontend build`，把 `frontend/dist/` 复制到 `backend/src/lite_eval/server/static/`。
- **打进包里**：用 hatchling 的 `artifacts` 配置，让 `src/lite_eval/server/static/**` 同时进 wheel 和 sdist，即使它在 gitignore 里。
- **CI 的打包任务**：
  1. 构建前端；
  2. `uv build`；
  3. 在干净的 venv 里安装 wheel；
  4. 运行 `lite-eval view --check`：在临时端口上启动，请求 `/` 和 `/api/health` 都返回 200，然后退出。
- **发布**（§17）：打 `v*` tag 时，CI 打包任务通过上面四步后，把 wheel 和 sdist 作为这个 tag 的 GitHub Release 附件上传。
- **没有前端构建产物时**：`lite-eval view` 报出清楚的错误（提示先构建前端），API 仍然可以单独使用。

### 18.15 SQLite 索引

- **同一时间只有一个写入者**：Viewer 启动时对 `.lite-eval/index.lock` 加排他锁；第二个 Viewer 用同一个目录时拒绝启动。
- **版本**：索引里有 `meta(schema_version)`。版本对不上，就整个重建：先建到临时文件，再原子重命名。
- **增量更新**：每 5 秒（或者有请求进来时）扫描 `runs/*/*/run.json` 的修改时间和大小：
  - 新出现的：加入索引；
  - 变了的：重新索引；
  - 不见了的：从索引里删掉。
- **runner 的写入顺序**：先写 `outputs.jsonl` 和 `scores.jsonl`，最后用“写临时文件 + `os.replace`”写 `run.json`。没有 `run.json` 的目录表示还在运行中，索引跳过它。

### 18.16 迁移时必须一致的数字

每个迁移都分两层验证：

1. **评分器等价**：单元测试里，把原函数和新评分器喂同一批命中结果，输出必须相同。
2. **端到端等价**：同一份构建好的索引或数据、同一个进程里，原脚本和 lite-eval 分别跑一遍。延迟不比较。

| 项目 | 原定义（必须原样移植） | 必须相同的输出 |
| --- | --- | --- |
| lite-rag | 见下 | 每题的 `answer_rank` 和 `doc_rank`；整体和按 `kind` 分组的 `hit@1`、`hit@5`、`mrr@10`、`doc_hit@5`，误差 ≤ 1e-9 |
| llm-steward | 见下 | 每条查询的召回值、均值、miss 列表；有 rerank 和没有 rerank 两种 |
| lite-mem | 见下 | 每题的 `hop1`、`hop2`；汇总的 `hop1`、`hop2`、`both` 计数 |

- **lite-rag**（`src/lite_rag/eval.py` 的 `judge`、`metrics`、`_norm`、`_nearest_rank`）：
  - 每题检索前 10 名，从上往下扫：第一个来自目标文档的命中记为 `doc_rank`；第一个来自目标文档、并且规范化文本包含规范化证据句的命中记为 `answer_rank`。
  - 文本规范化（`_norm`）的步骤：
    1. 去掉 Markdown 标题符号、`**`、`__` 和转义符；
    2. 把连续空白合并成一个空格；
    3. 去掉两个中日韩字符之间的空白。
  - `hit@k` = `answer_rank ≤ k` 的比例；`mrr@10` = Σ(1/answer_rank) / n；`doc_hit@5` 用 `doc_rank` 算。
  - `p50` = `statistics.median`；`p95` = 最近秩法：排序后取第 `ceil(0.95·n)` 个值。
  - 按 `kind` 分组。
- **llm-steward**（`backend/tests/eval/test_recall.py` 的 `_recall`）：
  - 每条查询 `top_k=8`、`include_pinned=False`；
  - 召回 = |期望的 key ∩ 返回的 key| / |期望的 key|；
  - 整体 = 所有查询召回的平均；召回 < 1 的查询进 miss 列表；
  - 门禁阈值 0.92；
  - `degraded` 不为空时，这次运行视为出错。
- **lite-mem**（`eval/multihop.py`）：
  - 记忆用 `infer=False` 直接写入，不调用模型；
  - 每题检索前 k 名（默认 5，可以带 `follow_up`）；
  - 某一跳算命中的条件：存在一条记忆，忽略大小写后包含这一跳的全部关键词；
  - 汇总 `hop1`、`hop2`、`both` 的计数。
  - 端到端需要 Ollama 上的 bge-m3，放在可选 marker 下；评分器等价的测试不需要模型。

### 18.17 自检的定义

- **`identical_scores`**：
  - 条件：同一个 Suite、同一个数据集哈希下，两次运行的 `target.config` 不同，但所有评分器的逐用例分数完全相同。
  - 动作：给两次运行都加上这个警告。
  - 范围：只比较**同一次 `lite-eval run` 调用**里的各个配置。运行记录写成后不可变，不能回头给旧运行加警告（E1 定）。
- **`feature_not_exercised`**：
  - Suite 可以声明 `expect_features: [merge, …]`；
  - Target 在 trace 里用 `kind="decision"` 的 span 加 `attrs.feature` 标明实际触发了哪个功能；
  - 运行结束时，如果某个声明的功能在任何用例的 trace 里都没出现，就给出警告。
- **排名指标交叉核对**：
  - 测试里，自写的 hit@k、MRR、nDCG 必须和 ranx 在一组固定的样例运行上一致；
  - `lite-eval run --crosscheck` 会在运行时用 ranx 再算一遍，差值超过 1e-9 就给出警告。
- **`within_noise`**：定义见 §18.1。
- **其他**：
  - `all_failed`：有输出，但没有一个分数通过或大于 0。
  - `empty_group`：Suite 用 `expect_tags: {kind: [fact, multihop]}` 声明期望的分组，某个值没有用例时警告（分组来自标签，不声明就无从知道“该有却没有”）。
  - `synthetic_dataset`：`dataset.yaml` 写了 `synthetic: true`，或有用例的 `provenance.synthetic` 为真。
  - `unversioned_target`：`versions()` 为空，引擎或模型换了缓存也看不出来。
  - `budget_exhausted`、`cache_corrupt`、`secret_scan_allowed`、`secret_scan_allow_unused`：见 §6、§18.5、§18.12。

### 18.18 setup 共享与预热

- **分组**：runner 按 `(target 名, canonical(setup_identity()))` 给配置分组，一次只跑一个组；组内各配置按 Suite 里的顺序跑，组之间的顺序不影响结果。
- **生命周期**：
  1. 组内第一个配置（leader）调用 `setup(dataset)`；
  2. 其余配置（follower）如果实现了 `share_setup_from(leader)`，就调用它接过 leader 的 setup；没实现的，各自调用 `setup(dataset)`，并给出 `setup_not_shared` 警告（结果仍然正确，只是慢）；
  3. 每个配置跑完自己的用例后，先 teardown follower；组内全部跑完，最后 teardown leader。
  4. 每个 Target 都尝试 teardown，一个失败不影响后面的。运行已经出错时，teardown 的失败作为附注挂在原来的错误上，不顶替它；运行没出错时，teardown 失败报为 `RunError`（退出码 3）。
- **只读**：共享的 setup 在 `run` 期间只读。会修改共享状态的 Target（例如记忆系统在 `run` 里写入）不能共享：`setup_identity()` 必须包含配置名，让它自成一组。
- **预热**：每个配置在 setup 之后、计时之前，对按 id 排序的第一个用例调用一次 `run`，结果丢弃：不进缓存、不进 `outputs.jsonl`、不算延迟；调用次数和费用照样计入预算和 `cost`（L6），预热抛出带用量的 `TargetError` 时这笔用量也算。缓存全部命中、不需要调用 Target 时，不做预热。

### 18.19 门禁语义

- **指标的值**：运行级指标（§18.1：先算每个用例在所有重复上的均值，再对用例取平均）。出错的用例按失败计。
- **绝对阈值**：`{metric, min}` 要求值 ≥ `min`；`{metric, max}` 要求值 ≤ `max`（延迟、费用这类越小越好的指标）。两者都写时都要满足。
- **相对阈值**：`{compare_to: baseline, max_drop, require_significant}`，只用可比的用例（§18.3）。
  - `delta` = 候选 − 基线（越小越好的指标取相反数，统一成“越大越好”）。
  - `require_significant: false`（默认）：`delta < −max_drop` 就失败。
  - `require_significant: true`：`delta < −max_drop`，**并且**这次下降按 §18.2 显著，才失败。用来避免小样本上的噪声拦下合并；报告里照样列出不显著的下降。
- **重复运行**：只看均值，不看单次重复；`within_noise` 警告（§18.1）只报告，不改变门禁结论。

### 18.20 Suite 与 Python Target 的接入（E1 定）

- **评测根目录**：
  - Suite 在 `<root>/suites/x.yaml` 时，根目录是 `<root>`；否则是 Suite 文件所在目录。可以用 `--root` 或 `LITE_EVAL_ROOT` 覆盖。
  - 运行目录在 `<root>/runs/<suite>/<run_id>/`，缓存在 `<root>/.lite-eval/cache/`。
  - Suite 里 `dataset:` 的相对路径按 Suite 文件所在目录解析。
  - `run.json` 里的 `suite.path` 记相对根目录的路径（Suite 在根目录之外时带 `..`），不写本机的绝对路径；`rescore` 靠它找回 Suite。
- **Python Target**：
  - 写法：`python: module:factory`。`configs:` 的每一项是一个 dict，`name` 必填（只能是小写字母、数字、`.`、`_`、`-`，它进 run_id 和目录名），其余键作为关键字参数调用 `factory(**params)`。
  - 模块从根目录的上一级（使用方的仓库根）导入，只在导入期间放进 `sys.path`。
  - 返回值必须满足 §5.3 的协议；可选属性 `deterministic: bool`（默认 True），为 False 时、Suite 没写 `repeats` 就跑 2 次。
  - 当前是第几次重复：runner 在每次调用 `run(case)` 前设置上下文变量 `lite_eval.targets.base.current_repeat`（从 0 开始），想按重复次数取种子的 Target 读它；`run` 的签名不变。
- **Suite 字段（E1）**：
  - 字段：`schema_version, name, pack?, dataset, targets[{python, configs}], scorers[], metrics[], repeats?, concurrency, timeout_s, seed, budget{max_cost_usd, max_calls}, redact[], expect_features[], expect_tags{}, allow_paid_in_ci, cache (auto|on|off), gate`。
  - 未知字段报错（退出码 2）。
  - `gate` 原样记进 `run.json`，从 E2 起才执行。
  - 不写 `pack` 时必须写 `scorers`。E1 只有 cost pack，它随每次运行出。
- **评分器的写法**：
  - 字符串，例如 `exact_match`；或带参数的 dict，例如 `{name: contains, case_sensitive: false, field: answer}`；组合写成 `{all_of: […]}`、`{any_of: […]}`、`{weighted: [{scorer: …, weight: 2}, …]}`。
  - 规范形式：没有参数时是名字本身，有参数时是 `{名字: 参数}`，组合是 `{op: [子评分器的规范形式]}`、`{weighted: [{scorer, weight}]}`（weight 记为浮点数）。Suite 写法 `{name: …}` 和记录写法 `{名字: 参数}` 都先规范化，所以同一个评分器不管怎么写，id 都一样（E2 按 id 配对）。
  - 记录里的评分器名是规范形式的规范 JSON（没有参数时就是名字）。`rescore` 靠它重建评分器。
  - E1 的确定性评分器：`exact_match`、`contains`、`regex`、`keywords`、`must_not`、`set_recall`、`set_precision`。共同参数：`field`（输出里的路径）、`expected`（`case.expected` 里的路径）、`case_sensitive`。
  - `all_of`：值取最小、全部通过才算通过；`any_of`：值取最大、任一通过即通过；`weighted`：加权平均，`passed` 为空。每个子评分器的结果放在组合分数的 `evidence` 里。
- **runner 的细节**：
  - 出错的输出不写缓存，下次重新生成；超时、偶发错误不会被缓存固定下来。
  - 每个输出（包括缓存命中的）都按**当前** Suite 的规则脱敏后才写进运行记录：规则增加后，旧缓存里的内容不会原样漏出；脱敏改变了内容时，缓存条目同时被替换。
  - 评分用的是脱敏之后的输出，所以 `rescore`（读运行目录里已脱敏的输出）和第一次打分结果一致。
  - 预算：整个 Suite（一次 `run` 调用的所有配置）共用一份。`max_calls` 按每次调用计数（重试也算），在并发下也是精确的（检查和计数之间没有 `await`）；`max_cost_usd` 在花费**超过**上限后停，所以 `max_cost_usd: 0` 仍然允许免费调用，并发时已经发出的调用可能让花费略超上限。
  - 传输错误后，预算不允许再试就不等待、不重试，这次失败照样记为 `exception` 输出并计入分母。
  - 一个输出都没有的配置记为 `status=failed`，照样写运行目录，退出码 3。
  - 各配置按名字顺序依次执行，配置内的用例并发执行。
- **`rescore`**：
  - 从运行目录读输出，从 `suite.path` 找回 Suite 和数据集。数据集哈希变了就拒绝（退出码 2），应该重新生成。
  - 读运行目录时，同一个 `(case_id, repeat)` 出现两次就拒绝（退出码 2）：被改过的记录不能悄悄得出一个错的汇总。
  - 新 run 的 `cost.calls` 为 0，`cache` 清零，`rescored_from` 指向原 run。
  - `--scorer` 可以重复给，写法是名字或 JSON（Suite 写法或记录里的写法都行）。

### 18.21 检索 pack、对比、门禁的接入（E2 定）

- **检索评分器**：
  - 输出约定：`value`（或 `field` 指向的部分）是按名次排好的命中列表 `[{doc, text, id?}]`。
  - `expected` 有两种：证据句模式 `{doc, evidence}`，判定和 lite-rag 的 `judge`、`_norm` 逐字一致（§18.16）；相关集合模式 `{relevant: [ids]}`，命中的 id 取 `id`，没有就取 `doc`，重复的 id 只算第一次出现，和 ranx 看到的一样。
  - 评分器名是 `<指标>@<k>`：`hit`、`doc_hit`、`mrr`、`ndcg`、`recall`。每个用例的分数就是这个指标在这一题上的值，所以运行级指标就是各题均值，汇总、分组、对比都走通用路径。证据句模式下只有一个相关项，`recall@k` 等于 `hit@k`。
  - retrieval pack 默认 `hit@1, hit@3, hit@5, mrr@10, doc_hit@5`；相关集合模式通常再加 `ndcg@10, recall@10`。
- **评分器声明 `verdict`**（写进 `ScorerRef.verdict`，E2 加的字段）：
  - `value`：值是 1 当且仅当通过（`exact_match`、`contains`、`regex`、`must_not`、`hit@k`、`doc_hit@k`，以及子评分器全是 `value` 的 `all_of`、`any_of`）。不单列 `:pass_rate`，它只会重复值。
  - `separate`：值是比例，另有通过与否（`keywords`、`set_recall`、`set_precision`，其他 `all_of`、`any_of`）。有 `:pass_rate` 指标。
  - `none`：没有通过与否（`mrr@k`、`ndcg@k`、`recall@k`、`weighted`）。
  - 有没有 `:pass_rate`、对比用 McNemar 还是 bootstrap、逐用例变化叫“退步 / 修好”还是“变化”，都只看这个声明，不从某次运行碰巧得到的值去猜：连续指标在小数据集上恰好全是 0 或 1 时，从值去猜会选错检验（E2 审查发现）。E2 之前的记录没有这个字段，才退回看值。
- **对比**：
  - `RunRecord.case_hashes` 记每个用例的哈希（E2 加的字段；E1 的记录没有，不能参与对比，要重新生成）。
  - McNemar 用整数精确计算（不依赖 scipy）；bootstrap 对逐用例差值重抽样，用 numpy 的 `default_rng(种子)`，每次抽 1000 行（和一次抽 10000 行读到的随机数顺序相同，内存只要十分之一），区间用 `numpy.percentile` 的默认（线性插值）百分位。numpy 是核心依赖。
  - 逐用例变化：所有变差的用例在前（降幅大的先列），然后是变好的。
- **门禁**：
  - Suite 里 `gate` 可以写成规则列表（§8.4 的写法），也可以写成 `{rules: [...], allow_partial, allow_partial_overlap}`。
  - 规则二选一：`min` / `max`，或者 `compare_to: baseline` 加 `max_drop`（可选 `require_significant`）。`latency_p50`、`latency_p95`、`cost_usd` 取自 cost 汇总，越小越好，用 `max`；对它们写相对规则在加载 Suite 时就报错（否则要等运行花完钱才发现）。
  - `RunRecord.gate` 记的是有类型的 `GateSpec`（E1 记录里是 `null`，照样能读）。
  - 门禁能不能用（指标是否存在、baseline、数据集、`partial_overlap`、评分器版本）由同一个函数 `analysis.gate.gate_problems` 判断：运行前用 Suite 声明的东西调一次，运行后用记录里的东西再调一次，两边不会不一致。
  - 所有阈值比较留 1e-9 的容差：降幅正好等于 `max_drop` 时，0.95 − 1.0 在浮点数里是 −0.050000000000000044，不能因此判失败。
  - `--gate` 先做配置检查（门禁不存在、指标不是评分器给出的、缺 baseline、数据集名不同、`partial_overlap` 未允许、评分器版本不同），全部在调用 Target 之前，退出码 2；然后运行，有运行一个都没打分就退出码 3；最后判门禁，失败退出码 1。
  - `lite-eval baseline set <run>` 写 `<root>/baselines.yaml`（suite 名 → run_id），并提示把运行目录和这个文件一起提交（§17）。
- **setup 共享**：Target 可以实现 `share_setup_from(leader)`；预热时 runner 把上下文变量 `lite_eval.targets.base.warming_up` 设为真，Target 可以据此不把预热算进自己的统计。
- **`--crosscheck`**：对每个检索评分器，从每个分数的 evidence 里取出名次，用 ranx 0.3.21 重算（每次重复分别算再平均），和 lite-eval 的值相差超过 1e-9 就给 `crosscheck_mismatch` 警告；没装 `[ranking]` extra 时报配置错误。

### 18.22 Viewer 的接入（E3 定）

- **模块**：
  - `index.py`：运行目录（`FileCatalog`）和 SQLite 索引（`IndexCatalog`）实现同一个 `RunCatalog` 接口（`list_runs`、`trend`），同一套读取测试跑两种实现。
  - `views.py`：页面要的数据（运行详情、逐用例、用例详情、数据集、对比）。加载和组装分开：`open_run` 把一个运行和它的数据集用例加载一次，`rows`、`detail` 从加载好的数据组装；HTTP 每个请求加载一次，导出和 `triage` 只加载一次再逐个用例组装。按名字找数据集只读 `dataset.yaml` 和 `cases.jsonl`（不哈希语料）。server 和 HTML 导出都从这里取，所以导出的页面和 Viewer 显示的一样。
  - `annotations.py`：§18.9 的读写。
  - `server/`：`guard.py`（§18.10）、`app.py`（路由）、`run.py`（启动和 `--check`）、`paths.py`（构建产物的位置；不依赖 `[server]` extra，导出也要用）。
- **索引**：
  - 只存每个运行的列表行（`RunRow`）和用来筛选的列；哪个运行是 baseline，每次查询时现读 `baselines.yaml`，不进索引（切换 baseline 不改任何 run.json）。
  - 有请求进来、且距上次扫描超过 5 秒时，增量扫描一次（§18.15 的"每 5 秒或有请求时"取后者，不另开后台线程）。
  - 读不了的 run.json（手改坏了、正在被替换）跳过，不让 Viewer 报错。
- **运行列表的筛选**：suite、config、status、pack、数据集名和版本、是否 baseline。运行本身没有标签，§10.1 说的"按标签筛选"落到这些字段上。
- **用例内容**：按运行记录里的 `case_hashes` 去 `<root>/datasets/*/` 里找同名数据集（owner 2026-11-14 定，运行目录里不另存用例）：
  - 哈希相同：`current`；
  - 用例还在但内容变了：`changed`，显示今天的内容并标出来；
  - 数据集或用例不在了：`missing`。
  - "同一用例在其他运行里的结果"只列内容哈希相同的运行，最多 20 个，新的在前。
- **API**（路径都在 `/api` 下；规范提交在 `backend/openapi.json`，测试 `test_openapi_spec_is_current` 在它过期时失败）：
  - 读：`health`、`runs`、`runs/{suite}/{run_id}`、`…/cases`、`…/cases/{case_id}`、`compare?baseline=&candidate=`（`<suite>/<run_id>`）、`datasets`、`datasets/{name}/cases`、`trends?suite=&metric=[&config=]`、`annotations`；
  - 写：`POST annotations`，服务端补上 id、作者和时间；
  - 错误统一为 `{"error": {"message", "code"}}`：404 `not_found`、409 `conflict`、400 `bad_request`、403 `forbidden_host` / `forbidden_origin` / `missing_token`、413 `too_large`；请求校验失败是 FastAPI 默认的 422。
  - 规范导出时去掉与 `const` 重复的单元素 `enum`：pydantic 2.9（声明的下限）会多写这一项，2.14（锁定版本）不写，两者含义相同。
- **安全（§18.10 的实现）**：
  - 写请求的请求体在中间件里先读完再交给应用，超过 16 KB 就返回 413，不管 `Content-Length` 写的是多少；
  - token 每个进程随机生成，返回 `/` 时注入到 `<meta name="lite-eval-token">`，页面带 `Cache-Control: no-store`。
- **`lite-eval view`**：
  - `--root` 默认取当前目录（有 `runs/` 或 `suites/` 时），否则 `./evals`，都没有就报错（退出码 2）。
  - `--check` 不给 `--root` 时用一个空的临时目录，所以不会抢正在运行的 Viewer 的锁；`/` 或 `/api/health` 不是 200 时退出码 3。
  - 隐藏选项 `--static` 指定别的前端构建目录（测试和 e2e 用）。
  - `fastapi`、`uvicorn` 在 `[server]` extra 里；没装时 `view` 给出一行安装提示。
- **`lite-eval triage <run>`**：逐条列出还没有当前归类的 miss（输入、期望、输出，各截到 400 字符），按 r / l / e / u 归类，s 跳过，q 退出；别人刚好先归类了同一条时，提示后继续下一条。
- **`lite-eval report --html`**：
  - 参数是一个运行目录，或者 `<baseline 目录>..<candidate 目录>`（在两边都是目录的那个 `..` 处拆开，所以 `../runs/x` 这样的相对路径照样能用）。
  - 页面就是 Viewer 自己的构建：CSS 内联成 `<style>`；唯一的 JS 文件变成 `data:` URL 的 module script，所以 JS 里出现 `</script>` 也不会结束标签；数据按 §18.12 转义后放进 `#lite-eval-data`。
  - 数据格式是 `views.ExportPayload`（pydantic 模型）：`{kind: run | compare, generated_at, lite_eval_version, start, runs: {"<suite>/<run_id>": {row, detail, rows, cases: {case_id: CaseDetail}}}, comparison}`。`start` 是页面打开时所在的路由（单个运行打开在它的运行页，对比打开在对比页）。这个模型作为组件写进 OpenAPI 规范（`server/app.py` 里的 `openapi()`），前端的类型由它生成，不手写。用例详情里的"其他运行"只在导出的运行之间找。
  - 脱敏按类型进行，和 Viewer 是同一套代码：输出、分数、警告、target 的 config 和 info 用导出运行的规则并集加 `--redact`；id、哈希和数据集内容不改（§18.12：数据集不脱敏，加载时扫描）。所以对比导出里，一个运行的文本也要满足另一个运行的规则。
- **前端（E3b 定）**：
  - 路由用 hash history：Viewer 和从磁盘打开的导出文件共用一套路由，服务端不需要把任意路径改写回 `index.html`。
  - 不做代码分割（rolldown 1.2.19 的 `output.codeSplitting: false`），构建结果只有一个 JS 和一个 CSS；只用系统字体，不打包字体文件。这样导出才能是单个文件。
  - 数据只经过 `src/data/source.ts`：`liveSource` 走生成的 client，`embeddedSource` 读 `#lite-eval-data`。导出页面只读：不能写 annotation，也没有数据集页和趋势页。导出的对比打开时直接落在对比页。
  - 后端 operationId 取路由函数名（`generate_unique_id_function`），生成的 client 函数名是 `listRuns()`、`getCase()` 这样。
  - 视觉方向：dense-data（`PRODUCT.md`、`.impeccable/surfaces/frontend-src.md`）。只用状态色：变差为橙色、变好为蓝色（Okabe–Ito 配色，两个主题都用 dataviz 的校验脚本验证过），每种状态同时带图标和文字；不显著的差值是中性灰。
  - 标志性设计：区间条（所有差值共用一个以零为中心的刻度；跨过零的区间画成中性灰）和用例条码（每个用例一格，出错和没生成的格子加斜线纹理，点一格打开对应用例）。
  - 键盘：列表 J/K 移动、Enter 打开、X 勾选对比；用例页 [ ] 切换上一个 / 下一个用例、T 归类；对话框里 R/L/E/U 选类别、Cmd/Ctrl+Enter 保存。
  - e2e：`backend/tests/e2e/viewer_fixture.py` 用 fake target 跑出真实的运行记录，生成两种导出，在端口 8722 上启动 Viewer；Playwright 覆盖 §16 E3 的验收流程。
- **打包（E3b 定）**：CI 的 `package` 任务按 §18.14 构建前端 → `uv build` → 检查 wheel 里有 `server/static/index.html` → 在干净的 venv 里装 `wheel[server]` → `view --check`。打 `v*` tag 时，`release` 任务（唯一有 `contents: write` 权限的任务）用 `gh release create` 把 wheel 和 sdist 发布出去。
- **E3b 的 /simplify 带来的 API 补充**（都只加字段）：
  - `CaseDetail` 带 `dataset`、`miss`、`error` 以及 `prev_case_id` / `next_case_id`（按运行里用例的顺序），用例页只发一个请求，不需要整份用例列表；
  - `CaseChange.direction`（worse / better）和 `Comparison.baseline_config` / `candidate_config`。哪边算变差由后端给出，浏览器只负责显示，不做判断（前端规则 2）；
  - 用例列表的 `limit` 上限是 100000，不再静默截在 1000 条；
  - 前端所有查询的 key 和参数集中在 `src/data/queries.ts`。运行记录不可变，所以和运行相关的查询永不过期；只有 annotation 会让 `case`、`cases`、`dataset-cases` 这三类查询失效。

