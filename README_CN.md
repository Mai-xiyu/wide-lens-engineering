# Wide-Lens Engineering

[English](README.md) | **简体中文**

用弹性多代理团队实现、调试、重构、迁移和审查软件，同时保留唯一 canonical writer 与外部锚定验收。

[![Codex Skill](https://img.shields.io/badge/Codex-Skill-111827)](https://learn.chatgpt.com/docs/customization/overview)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Runtime dependencies](https://img.shields.io/badge/runtime%20dependencies-0-2ea44f)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<!-- section:overview -->
## 这是什么

Wide-Lens Engineering 是用于真实仓库工作的 Codex Skill，不是只做 review 的提示词。它组合了：

- 面向普通编码的低开销 `practical` 工作流；
- 只有其他 Agent 存在正边际收益时才创建的弹性任务 DAG；
- shared deliberation 的密封首轮与封存后挑战；
- 宿主实际强制隔离边界时才启用的候选实现；assured 声明还必须有外部 attestation；
- 面向高风险交付、依赖外部 authority、receipt 与 fail-closed gate 的 `assured` v5 协议。

Skill 不规定 Agent 数量、固定团队、模型或角色阵容。当前主模型根据实际宿主能力与冻结任务派生执行模式，不能根据产品名猜测能力。

安装 Skill **不会**让每个编码会话都自动进入这套流程。Codex metadata 设置了 `policy.allow_implicit_invocation: false`，普通任务继续走宿主默认路径；只有显式调用 `$wide-lens-engineering` 后才加载完整 Skill body，router 随后也只加载选中的 practical 或 assured reference。

| 路径 | 适用场景 | 证据能说明什么 |
|---|---|---|
| `practical` | 局部、可回滚、范围清晰的工作 | checkpoint、命令与观察到的 Git diff 一致 |
| `assured` v5 | 高风险或要求审计的弹性交付 | 外部 controller artifact 与最终状态一致 |
| legacy `assured` v4 | 已有 v4 集成 | 原 packet v4 行为保持字节兼容 |

<!-- invariant:assured-external-trust-root -->
> [!IMPORTANT]
> 缺少真实外部 controller、独立摘要通道、固定 verifier、隔离工件和 OS sandbox 时，Skill 必须报告 assured 前置条件不成立。

[快速开始](#quick-start) · [弹性团队](#shared-subagents) · [信任边界](#trust-boundaries) · [安装](#installation) · [测试](#testing)

<!-- section:build-week -->
<a id="build-week"></a>
## OpenAI Build Week 来源

- **类别：** Developer Tools
- **仓库：** [github.com/Mai-xiyu/wide-lens-engineering](https://github.com/Mai-xiyu/wide-lens-engineering)
- **主要 `/feedback` 会话：** `019f67c4-9bd9-7581-8ae9-3cdd4453d9f7`
- **演示：** [Wide-Lens Engineering — GPT-5.6 + Codex](https://youtu.be/rg-BmgUnxL4)
- **记录的构建模型：** `gpt-5.6-sol`

模型标签和会话 ID 只用于来源说明，不是正确性证明。60 秒 legacy 检查路径：

```bash
python -B tests/run_eval.py --threshold 1.0 --json
python -B tests/run_forward_eval.py --threshold 1.0 --require-no-skips --json
```

这些确定性测试不需要样本数据、API key、账号、第三方 Python 包或网络调用。

<!-- section:quick-start -->
<a id="quick-start"></a>
## 快速开始

通过 Codex 的 Skill installer 安装仓库根 Skill：

```text
Use $skill-installer to install this GitHub skill:
repo: Mai-xiyu/wide-lens-engineering
path: .
name: wide-lens-engineering
```

然后显式调用它来编码：

```text
Use $wide-lens-engineering to fix the failing parser behavior.
Choose assurance, depth, and coordination independently.
Let the active main model decide whether delegation has marginal value.
Keep the final implementation minimal and run the frozen acceptance checks.
```

高风险任务应显式请求 assured v5，并提供外部 controller artifact。缺少任一锚点时，Skill 必须拒绝高保证声明。

<!-- section:how-it-works -->
<a id="how-it-works"></a>
## 工作方式

<!-- invariant:axes-independent -->
每个任务分别选择一个 intent 和三个互相独立的轴。

| 维度 | 取值 | 决定什么 |
|---|---|---|
| Intent | `change`、`debug`、`review` | 主线程获得什么操作权限 |
| Assurance | `practical`、`assured` | 如何建立完成声明 |
| Depth | `focused`、`full` | 因果与风险分析的宽度 |
| Coordination | `independent`、`shared` | 独立形成证据，还是由 peer 交叉挑战 |

`full` 不自动等于 `assured`，`assured` 也不自动要求 `shared`。Agent 数量不决定任何公共轴。

执行模式由运行时派生，不是第四个用户必选轴：

```text
main-only
read-only-proposals
isolated-candidates
```

主模型先记录宿主实际提供的 spawn、join、steering、peer-message、atomic-claim、只读、workspace 隔离、canonical write blocking、独立 verifier、per-spawn model 与 depth-control 能力。未知能力为 `false`。随后构造无环任务 DAG；子任务目标、路径、验收 ID 和能力只能收窄冻结父合同。

```mermaid
flowchart LR
    A["只读映射"] --> B["冻结范围与验收"]
    B --> C["观察宿主能力"]
    C --> D["派生执行模式"]
    D --> E["构造动态任务 DAG"]
    E --> F["密封证据或隔离候选"]
    F --> G["主集成者选择并写入"]
    G --> H["独立 verifier 检查最终状态"]
```

Ponytail 同时约束委派与实现：预期信息增益不足就不建团队；实现达到验收后立即停止增加复杂度。

<!-- section:practical -->
<a id="practical-workflow"></a>
## Practical Elastic

Practical checkpoint 写明目标、非目标、允许路径、精确验收命令、宿主能力、任务 DAG、execution mode 与降级原因。

| 观察到的宿主支持 | 派生行为 |
|---|---|
| 不值得委派，或无有效能力 | `main-only` |
| 能强制只读，但没有隔离 workspace | Peer 返回证据或 patch 文本，由主线程应用 |
| 具备真实 workspace 隔离与 canonical write block | Candidate worker 只能修改隔离副本 |
| 没有 peer-message，但可以 steer child | Root 在密封后转发一份完整 peer board |
| peer-message 与 child steering 都没有 | 降级为密封的 independent evidence，并记录原因 |
| 没有 atomic task claim | Root 分配 ready DAG node |

Git worktree 可以减少 practical 文件冲突，但 linked worktree 共享 Git common metadata，不能作为 assured sandbox。Candidate 自测只是建议；冻结验收必须在集成后的 canonical state 上重跑。候选写入重叠时只能串行、择一或退回主线程，禁止 last-writer-wins。

规范见 [references/practical.md](references/practical.md)。

<!-- section:assured -->
<a id="assured-workflow"></a>
## Assured Elastic（protocol v5）

Assured v5 保留完整 authority contract v1 与 baseline manifest v2，并在首次 spawn 之前绑定编排：

1. packet v5 嵌入完整冻结合同与精确 orchestration policy；
2. `orchestration-envelope/v1` 绑定 controller、capability、DAG、resource、sandbox 与 lineage digest；
3. controller 原子 lease 限制每个 actor；
4. candidate bundle 是来自隔离 workspace 的 inert blob；
5. main integrator 是 canonical checkout 唯一写入者；
6. `execution-receipt/v2` 记录 actor、lease、event、candidate、integration、state、diff 与 resource use；
7. 身份不相交的 verifier 在 fresh context 中生成 `verification-receipt/v1`；
8. gate 在执行任何冻结命令前检查 schema、digest、path、actor、lease、report 与 state，执行后再次哈希。

缺少 controller、独立摘要、隔离 workspace、完整事件捕获、独立 verifier 或 OS sandbox 时必须失败，不能静默降级后继续声明 `assured`。

protocol v5 的任务图修订只能发生在 dispatch 之前：修订必须保留 `version`、`packet_sha256`、`mode`、`execution`、`dispatch`、`communication`、所有先前任务与 assignment，只能追加新节点。Controller 必须 attest `predecessor_execution_started=false`。任何 actor 一旦 spawn，就应开启新的 assured execution epoch，不得在同一 receipt 中混用两个 envelope authority。

阅读 [references/protocol-v5.md](references/protocol-v5.md)。冻结 legacy 格式见 [references/protocol.md](references/protocol.md)；v5 artifact 与参数对 v4 CLI 必须非法。

<!-- section:shared-subagents -->
<a id="shared-subagents"></a>
## 弹性多代理团队

<!-- invariant:main-model-selects-subagents -->
是否需要 subagent，以及采用哪些身份、数量和 lane assignments，**只由当前主模型**决定。

<!-- invariant:no-fixed-participant-count -->
Skill 不包含精确、默认或最大参与者数量。

Task 不等于 Agent：同一 Agent 可以顺序执行多个 ready DAG node，任务也可以留在主线程。首版禁止递归委派；所有 child 都由主模型直接管理。

`shared` coordination 的 Round 1 必须密封。只有封存后，controller 才能公开完整 peer board 或允许 peer messaging。Peer message 只是未可信证据，不是 authority；裁决依靠判别性检查，而不是投票或置信度。protocol v5 中，存在依赖关系的 shared task 使用 `root-relay`；`peer-message` 只用于无依赖 round，因为每个发送者在交流结束前必须持续持有有效 lease。

<!-- invariant:subagents-read-only -->
<!-- invariant:no-recursive-delegation -->
<!-- invariant:main-thread-only-writer -->
在 legacy v4 与 `read-only-proposals` 中：Subagent 保持只读，禁止递归委派，主线程是唯一写入者和集成者。

在 `isolated-candidates` 中，analysis worker 仍然只读。Candidate worker 只能写宿主提供的隔离 workspace；该 workspace 不得挂载目标仓库、共享 `.git`、artifact store、凭据或 verifier 输入。Assured v5 还要求 controller lease 与对该隔离的外部 attestation。Main integrator 仍是 canonical checkout 唯一写入者。

<!-- section:ponytail -->
<a id="ponytail"></a>
## 从结构上保持最小

定位最早公共原因后，在第一个充分的 rung 停止：

```text
not-needed → reuse → stdlib → native → existing-dependency → minimal-custom
```

最小化会删除推测性抽象、依赖和无收益团队活动，但不会删除信任检查、必要失败路径、数据损失保护、可访问性或最小有效回归测试。

<!-- section:examples -->
<a id="examples"></a>
## 请求示例

```text
Use $wide-lens-engineering to implement this cross-module feature.
Use practical/full unless a hard assured boundary is discovered.
Derive the team and task DAG from actual capabilities; do not prescribe a count.
```

```text
Use $wide-lens-engineering in assured v5 for this authorization migration.
Fail closed unless the controller, sandbox, leases, receipts, and independent verifier are real.
```

```text
Use $wide-lens-engineering to debug this race.
Require sealed independent hypotheses, a discriminating reproduction, and one canonical writer.
```

<!-- section:trust-boundaries -->
<a id="trust-boundaries"></a>
## 信任边界

| 声明 | Practical 证据 | Assured v5 证明 |
|---|---:|---:|
| 已公开范围与验收 | 是 | 外部冻结并绑定 digest |
| 已观察最终命令与 diff | 当前会话观察 | controller 与独立 verifier receipt |
| Child 无法写 canonical state | 只有宿主实际强制时成立 | 必须有隔离 workspace 与 canonical write block |
| Actor 身份与时序已认证 | 否 | 只有外部设施认证或签名时成立 |
| 网络、凭据、子进程与仓库外写入被限制 | 否 | 依赖已证明的 OS sandbox |
| 软件普遍正确 | 否 | 否 |

Hash 只能证明内容一致，不能自行证明身份、时间、独立性或 confinement。Hook 检查消息形状，不是安全边界。Gate 不执行或自动应用 candidate bundle。

Assured v5 会拒绝整个 canonical repository（包括 Git metadata）和 candidate workspace 中的 hard-linked file。无法稳定提供文件身份与 link count 的文件系统属于显式 fail-closed 兼容边界。

v5 checker 是外部 artifact 的参考 gate。本仓库不提供 controller、lease service、隔离 workspace runtime、签名服务或 OS sandbox。

<!-- section:installation -->
<a id="installation"></a>
## 安装

要求：Codex、Git、Python 3.10+；无第三方 Python runtime 依赖。正式 controller 签名验证还会使用 OpenSSH `ssh-keygen`。

### 1. 只安装 Skill

使用[快速开始](#quick-start)中的 installer 请求，或将仓库 clone 到 Codex 识别的 Skills 目录。仓库根目录是唯一 canonical Skill，不存在嵌套重复的 `SKILL.md`。Router 保持精简，practical、assured 与 legacy 细节分别位于独立 reference 中，只在选中时加载。

### 2. Codex 项目适配器

先预览，再安装中性只读 peer profile：

```bash
python scripts/install_codex_adapter.py --target /path/to/project
python scripts/install_codex_adapter.py --target /path/to/project --apply
```

适配器只写 `.codex/config.toml` 的 `agents.max_depth = 1` 与 `.codex/agents/wide-lens-peer.toml`。它不配置 `max_threads`、model、reasoning、nickname、MCP、固定角色或参与者数量。不同的 config 永不覆盖；`--force` 只作用于不同的 peer profile。详见 [references/hosts/codex.md](references/hosts/codex.md)。

### 3. Codex Plugin artifact

从 canonical 根 Skill 构建确定性 archive：

```bash
python scripts/build_codex_plugin.py --version 0.1.0 --output-dir dist \
  --validator scripts/validate_codex_plugin.py --force
python scripts/validate_codex_plugin.py \
  dist/wide-lens-engineering-marketplace-0.1.0.zip \
  --expected-version 0.1.0
```

发布验证器按版本固定完整 Plugin manifest、hook 注册、hook 实现和每个运行时文件。它可以脱离本源码 checkout 独立复制运行；未知发布版本会 fail closed。

解压 archive，然后注册其自包含的本地 marketplace：

```bash
codex plugin marketplace add /absolute/path/to/unpacked-marketplace
codex plugin marketplace list
```

重启 ChatGPT desktop，并从 Plugins 中安装 `wide-lens-engineering`。Plugin 分发最小 runtime Skill 与可选的 `SubagentStart`/`SubagentStop` result-contract hook，且有意不包含仓库测试与打包工具。它不会自动注册 `.codex/agents`，因此项目适配器需单独安装。

安装或启用 Plugin 不代表其 hook 已受信。应先使用 `/hooks` 检查来源、命令与摘要，再显式信任；正常安装不应使用 `--dangerously-bypass-hook-trust`。`SubagentStart` 注入输出合同，`SubagentStop` 校验结果形状并可要求重试一次。两者都不能证明只读执行、身份、时序或 workspace 隔离；没有项目适配器时，其 `wide_lens_peer` matcher 不会运行。

生成的 archive 位于被忽略的 `dist/`，因此打包不会制造第二套维护源码。

### 版本规则

首个公开预览包目标是 `0.1.0`。Package SemVer 与 wire schema 相互独立：packet v5 仍是协议 `version: 5`，冻结兼容路径仍是 packet v4。确定性、跨平台、性能与可复现打包门禁通过后，可以发布明确标记为未获 attestation 的 `0.x` GitHub Prerelease；外部 controller receipt 限制的是 assured 声明，而不是普通预览包的可用性。公共安装与工作流合同稳定后再使用 `1.0.0`；不能为了匹配 package 而改名 protocol v5。

<!-- section:testing -->
<a id="testing"></a>
## 测试

完整维护者测试矩阵、可复现打包命令与发布规则位于运行时 Skill 之外的 [CONTRIBUTING.md](CONTRIBUTING.md)。快速本地检查为：

```bash
python -B tests/run_eval.py --threshold 1.0 --json
python -B tests/run_forward_eval.py --threshold 1.0 --require-no-skips --json
python -B scripts/validate_skill.py .
```

150-task suite 在 `authority-packet-lineage`、`capabilities-dag-envelope`、`resources-sandbox-events`、`candidate-isolation-conflict`、`verifier-report-gate` 和 `compatibility-path-artifact` 各冻结 25 个语义唯一任务。30 个正向完整链和 120 个负向/故障链都在 fresh Python process 中运行完整 CLI gate，并绑定随机 challenge、验收 marker 与仓库状态。150/150 的单侧 95% 精确二项下界约为 98.02%。

该统计只适用于冻结协议/controller benchmark 与固定配置，不是通用模型准确率、真实编码任务成功率、缺陷召回率或独立安全审计。固定同仓测试仍然只是证据，不是外部 assurance。

独立的 live runner 见 [benchmarks/codex-live-v1](benchmarks/codex-live-v1/README.md)。Local 模式会启动真实、fresh 的 Codex 进程并检查功能性变更，但始终返回 `release_eligible=false`。External 模式要求 controller 签署绑定当前 commit 的 anchor，并在 `local`、`security`、`concurrency`、`data`、`api`、`distributed` 六层完成 150/150 个 live coding task；runner 只报告 receipt 是否有效，不能自行批准 Release。只有受保护的 `assured-v5-release` environment 可以授权该 receipt。本仓库不分发隐藏 suite、controller key 或受保护的 release challenge，因此不得用协议 benchmark 替代该门禁。

<!-- section:repository-map -->
<a id="repository-map"></a>
## 仓库结构

```text
wide-lens-engineering/
├── SKILL.md                         # canonical router 与工程工作流
├── README.md / README_CN.md         # 面向读者的双语文档
├── CONTRIBUTING.md                  # 维护者测试、版本与发布规则
├── .github/workflows/               # CI、预览打包与 assured 发布门禁
├── .codex/                          # 可选项目适配器
├── agents/openai.yaml               # Codex Skill UI metadata
├── references/
│   ├── practical.md                 # Practical Elastic
│   ├── protocol.md                  # 冻结 assured v4
│   ├── protocol-v5.md               # Assured Elastic v5
│   ├── hosts/codex.md               # 已验证 Codex 映射
│   └── lenses.json                  # 冻结分析 catalog
├── packaging/codex-plugin-src/      # Plugin-only manifest 与 hook
├── benchmarks/codex-live-v1/        # 外部 live-coding 门禁合同
├── scripts/                         # v4 冻结工具、v5 工具、installer/builder
└── tests/                           # 确定性、分发、统计与性能门禁
```

`scripts/diverge.py`、`scripts/check_delivery.py`、`references/lenses.json`、两个 golden packet v4 digest 与 legacy CLI 行为保持冻结。

<!-- section:references -->
<a id="references"></a>
## 参考与关键词

- [Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Codex hooks](https://learn.chatgpt.com/docs/hooks)
- [Build Codex plugins](https://learn.chatgpt.com/docs/build-plugins)
- [Git worktree](https://git-scm.com/docs/git-worktree)
- [NIST proportion intervals](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)
- [Anthropic agent teams](https://code.claude.com/docs/en/agent-teams)
- [GitHub deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [OpenSSH signed-data verification](https://man.openbsd.org/ssh-keygen)

搜索关键词：Codex Skill、elastic agent teams、adaptive multi-agent coding、dynamic task DAG、isolated candidates、capability negotiation、capability leases、single canonical writer、independent verifier、assured software delivery、zero-trust agent protocol、sealed deliberation、adversarial debugging、root-cause analysis、code generation、refactoring、migration、Ponytail、YAGNI。
