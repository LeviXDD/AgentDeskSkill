# 核心协议与状态机

本参考定义任务卡式多 Agent 项目的控制面、权威状态、状态迁移、并发、幂等和 Git 交付语义。实施或审查工作流时，以这里的不变量为准。

## 目录

- [协议目标](#1-协议目标)
- [控制面与权威数据](#2-控制面与唯一写入权)
- [岗位模型策略](#31-岗位模型策略与运行时绑定)
- [岗位身份与真实会话](#32-岗位身份与真实会话)
- [任务、交付与集成状态](#4-三个正交维度)
- [状态迁移与阻塞恢复](#6-状态迁移表)
- [依赖与并发调度](#8-依赖与解锁规则)
- [PM 租约、派发与幂等](#10-pm-租约与-fencing)
- [Git 双提交交付](#13-git-与-worktree-的双提交交付)
- [一致性不变量](#15-最小一致性不变量)

本页定义任务自动推进系统的强制语义：谁能改状态、什么才是真相、任务怎样迁移、并发与回调怎样不重复执行。

## 1. 协议目标

这套协议自动化的是**项目协作与推进**，不是绕过审查自动合并代码。任意新 PM 应能只读仓库恢复状态；角色执行、消息投递和回调允许安全重试；“交付”“验收”“进入公共基线”必须分别证明；只有持权控制面可以迁移状态，易失运行信息不进入仓库真相。

Standard / Automated 必须同时实现可观察的真实闭环：PM/Leader 真实任务把卡投递到规范命名的独立岗位任务，岗位在独立 worktree 执行并持久化报告，岗位主动跨任务回调来源 PM，PM 验收后推进。仓库账本、outbox 或逻辑 role 只保存事实和意图，不能替代真实任务或真实消息；heartbeat 只兜底漏通知。

## 2. 控制面与唯一写入权

### 2.1 唯一写入者

**PM 或代表 PM 运行的自动化进程，是控制面状态的唯一写入者。**

它独占 `docs/pm/state/tasks.yaml`、`docs/pm/events/`、`docs/pm/outbox/`、`docs/pm/acceptances/`、生成视图 `BOARD.md`/`STATUS.md`，以及卡片状态、派发次数、验收与集成信息等控制字段的写入权。

角色会话不得直接修改上述状态。它只能在自己的 worktree 实现代码、创建版本化交付报告、发送带 `dispatch_id` 的完成或阻塞回调，并更新任务卡明确允许的业务文件。

控制面始终使用一个逻辑 PM lease。Lite 模式可以把它实现为人工接力棒；存在多个进程或自动化写入时，必须使用带有效期和 fencing 的实现，详见 [PM 租约与 fencing](#10-pm-租约与-fencing)。

### 2.2 权威层级

仓库内信息的权威顺序如下：

| 层级 | 载体 | 作用 | 能否直接驱动调度 |
|---|---|---|---|
| 1 | `tasks.yaml` | 当前机器权威快照 | 是 |
| 2 | `events/*.yaml` | 迁移、批准与撤销的审计记录及恢复依据 | 是，用于核对或重建 |
| 3 | `tasks/TC-*.md` | 版本化任务规格 | 仅提供约束，不代表当前状态 |
| 4 | `reports/TC-*.md` | 角色交付证据 | 否，须经 PM 验收 |
| 5 | `acceptances/TC-*.md` | PM 验收证据 | 否，验收结果已反映到快照 |
| 6 | `BOARD.md`、`STATUS.md` | 从快照生成的人类视图 | 否 |
| 7 | 聊天与通知 | 传输和提醒 | 否 |

冲突处理规则：

1. `BOARD.md` 或 `STATUS.md` 与 `tasks.yaml` 不一致时，以 `tasks.yaml` 为准并重新生成视图。
2. `tasks.yaml` 与事件尾部不一致时，停止调度，按 [运行手册与故障恢复](runbooks-and-recovery.md) 做一致性恢复。
3. report、回调或聊天中的“完成”不得直接覆盖任务状态。
4. 任务卡规格如被修订，必须产生新 `revision`；正在执行的派发继续绑定旧版本，除非显式取消或替代。

### 2.3 一次迁移必须是一个原子提交

每一次状态迁移必须同时产生：

1. 一个不可变的 event；
2. 更新后的 `tasks.yaml` 快照；
3. 由快照重新生成的 `BOARD.md` 与 `STATUS.md`；
4. 必要时新增 acceptance 或其他证据引用。

以上内容必须进入**同一个 Git commit**。这个 commit 是一次状态迁移的原子边界。

派发还有更强的历史边界：`current_dispatch` 首次出现在 `tasks.yaml` 的 commit，必须同时新增唯一且匹配的 `TASK_DISPATCHED` event 与 `task.dispatch` outbox。进入 `returned`、`accepted`、`integrated`，以及已有 report 的 `cancelled` / `superseded` 后，即使活动派发已清空，仍须通过 report 的 `dispatch_id` 与 `executor_model` 追溯这组原始提交证据；不得用终态自述替代它。

禁止：

- 先改 `BOARD.md`，稍后再改快照；
- 只写 event、不更新快照；
- 修改历史 event；
- 将两次语义不同的迁移压成一个含糊事件；
- 在状态 commit 中夹带业务实现代码。

## 3. 仓库状态与运行时状态分离

仓库真相只保存可审计、可迁移、可复现的数据。PM/角色 `threadId`、本机 worktree 绝对路径、PID、锁、心跳、消息游标、重试计时器、凭据和连接信息都放入 `.agentdesk/runtime/`，且该目录必须加入 `.gitignore`。仓库中只保存 `role_id`、分支约定和远端仓库标识等稳定逻辑标识。

运行时目录丢失不应改变项目事实。恢复时由控制面根据 `tasks.yaml`、Git 分支与事件重新发现会话；在真实 route、标题、worktree 与 PM callback 目标重新验证前停止派发，不能用占位 ID 或同会话切岗代替。

### 3.1 岗位模型策略与运行时绑定

`docs/pm/ROLE-POLICIES.yaml` 是岗位执行要求的仓库权威，由 Project Leader 负责制定；当前 PM lease holder 只能按 Leader 授权提交变更。策略只描述厂商无关的等级、推理深度和能力，不绑定 provider 或具体模型。等级固定为 `basic < standard < advanced < expert`，推理深度固定为 `efficient < balanced < deep`。

等级是项目内的执行能力分级，不是跨厂商通用排行榜；Leader 必须按仓库任务校准 binding，不可只凭模型名称自报等级：

| tier | 适合的任务上限 |
| --- | --- |
| `basic` | 低风险、边界清楚、上下文很小的机械性修改或信息整理 |
| `standard` | 常规单模块实现、测试、文档与已知模式下的缺陷修复 |
| `advanced` | 跨模块实现、复杂调试、模糊约束收敛、安全或兼容性敏感工作 |
| `expert` | 架构、关键迁移、高风险审查或失败成本很高的复杂决策支持 |

`efficient` 用于清晰且可局部验证的问题，`balanced` 用于常规工程权衡，`deep` 用于多约束、长链推理或高风险审查。模型等级与推理深度彼此独立，且任何等级都不替代人工批准。

每个 Active role 必须有：

- `default_tier`：正常选择目标；
- `minimum_tier`：该岗位不可突破的硬下限；
- `deliberation_tier`：执行所需的最低推理深度；运行时适配器再映射为 provider 专属设置；
- `required_capabilities`：硬性能力集合；
- `degradation_policy`：`block`、`require_pm_approval` 或 `allow_to_minimum`。

一次任务修订版的要求按以下规则计算：

```text
required_model_tier = max(role.minimum_tier,
                          task.min_model_tier unless inherit,
                          risk_floors[task.risk])
preferred_model_tier = max(role.default_tier, required_model_tier)
required_model_capabilities = union(role.required_capabilities,
                                    task.required_model_capabilities)
```

任务和风险只能抬高硬下限，不能降低岗位要求。所选 binding 必须 `enabled: true`，tier 不低于 `required_model_tier`，deliberation tier 不低于岗位要求，并覆盖能力并集。`degradation_policy` 只处理 `selected_model_tier < preferred_model_tier` 且仍满足硬下限的情况：`block` 禁止降级；`allow_to_minimum` 允许降到硬下限；`require_pm_approval` 除要求 selection ID 出现在账本 `granted_approval_ids` 外，还要求同一 dispatch commit 或其祖先已新增全仓唯一、匹配当前 task/revision/attempt、所选 tier 和首选 tier 的 `MODEL_DEGRADATION_APPROVED` event。派发时该批准必须由 PM 授予、尚未到期且未被独立 event 撤销；字段格式见 [事件、Outbox 与校验](events-outbox-and-validation.md)。任何策略都不能允许低于硬 tier、deliberation 或能力要求；模型降级批准也不授权其他外部动作。

批准 event 永不可改写，撤销只能追加 `MODEL_DEGRADATION_REVOKED` event，且同一 approval 最多一个撤销。派发前/时已撤销的批准无效；执行中撤销要停止并走恢复流程；完成后撤销只约束未来使用，不反向修改历史。dispatch、approval、revocation event 都携带整数 `lease_epoch >= 1`；同 commit 的 approval/dispatch epoch 相同，更早 approval commit 是 dispatch 的祖先且 epoch 不大于 dispatch，revocation commit 是 approval 的严格后代且其时间/epoch 不早于批准。

任务卡及当时的 `ROLE-POLICIES.yaml` 必须同时存在于 `task_card_commit`；该 commit 中的策略对该 revision 冻结。后续修改岗位策略只影响新提交的 revision，不得静默改变在途派发。

`.agentdesk/runtime/model-bindings.yaml` 将本机 `binding_id` 映射到 provider、model ID、tier、deliberation tier、capabilities 和 enabled 状态；它是 gitignored 的环境配置。派发前用完整 `task_card_commit` 运行确定性 `scripts/select_model.py`，确保读取该 commit 的冻结策略，再把其 JSON 原样复制到 `current_dispatch.model_selection` 和不可变 outbox；角色把同一快照写入 report 的 `executor_model`。精确 provider/model 是非秘密审计证据；移动 alias 在 provider 支持时须解析为稳定 revision，凭据、token、session/thread ID 仍只允许留在 runtime。

runtime binding 中的 tier/capability 是受信运行环境的能力声明，不是模型名称天然携带的事实。若本机 binding 维护者不在 Leader 的信任边界内，必须由 Leader 在版本化决策或受控模型 registry 中校准后才能宣称强制执行；否则只能作为 audit-only 映射。

selector 与 validator 是派发 guard，不负责启动 provider 模型。调度器/runtime adapter 必须在投递前证明能按所选 binding、稳定 model revision 与 deliberation tier 启动执行；不能指定模型的运行时只能把该策略当审计要求，禁止宣称已强制路由。report 的 `executor_model` 是执行者声明；只有受信 adapter/provider receipt 能把它提升为独立的实际路由证明。

一个 dispatch 只冻结一个 primary execution binding。未经声明不得把实质工作转交给更弱或未记录的子 Agent；对目标、实现或验收证据有实质贡献的 subagent 必须有自己的 dispatch/execution evidence。

模型等级只说明执行资源，不授予产品决策、控制面写入、凭据、外部副作用、验收、合并或发布权限，也不能绕过任何 Owner/PM approval 或 capability gate。

### 3.2 岗位身份与真实会话

每个 `Active` 岗位（包括 PM）必须在 `ROLES.md` 同时登记：

- `role_no`：稳定且唯一的人类编号；
- `role_id`：任务卡与账本使用的稳定逻辑 ID；
- `role_name`：稳定、非空的岗位名称。

`ROLES.md` 还必须有机器可读的 `expected_thread_title` 派生列，其值严格等于 `<role_no> . <role_name>`。

三者不是可互换别名。一个 `role_id` 只能映射一个 `role_no`；真实任务标题严格为 `<role_no> . <role_name>`，点号两侧各一个 ASCII 空格。任务创建后的自动标题、提示词摘要、工作树名称或英文缩写都不能替代规范标题。

Standard / Automated 的会话不变量：

1. PM/Leader 与 Worker 使用不同、真实可读取的 task/thread ID；逻辑岗位、subagent、同任务人格切换或单独 worktree 均不构成独立岗位任务。
2. 新建 Codex 可见任务必须有用户对具体岗位或明确批次的显式授权；“初始化/开始开发/使用 Skill”不隐含此授权。授权缺失时停在 `Ready` 并询问，禁止静默降级。
3. gitignored runtime route 必须同时证明 `role_no`、`role_name`、expected/actual title、transport、host、真实 thread ID、worktree、状态和验证时间。只有 `status: verified` 可用于派发。
4. PM callback route 也必须 verified；派发消息携带运行时 delegation `source_thread_id`，Worker 完成前优先向该来源回调，并与 PM route 核对。
5. 派发与 callback 都必须由真实 transport 执行并产生本机 receipt。最终答复、报告文件、outbox 意图或 heartbeat 观察都不是主动回调 receipt。
6. runtime adapter/thread tools 不可用时，只能明确使用 Lite/manual 或阻塞；不得宣称 Standard / Automated 闭环。

Codex 的确定性工具顺序见 [Codex 任务运行时适配器](codex-runtime-adapter.md)。

## 4. 三个正交维度

不要用一个“完成”字段同时表达执行、交付与集成。

### 4.1 任务状态 `task_state`

任务状态描述控制面流程：

`Draft → Ready → Dispatched → InProgress → ReviewReady → Accepted → Integrated`

以上是文档中的概念名；YAML 使用对应的小写蛇形值：`draft`、`ready`、`dispatched`、`in_progress`、`review_ready`、`accepted`、`integrated`。旁路状态同理写作 `returned`、`blocked`、`cancelled`、`superseded`。

返工、阻塞和终止通过 `Returned`、`Blocked`、`Cancelled`、`Superseded` 表达。

### 4.2 交付状态 `delivery_state`

交付状态描述某次 `dispatch_id` 是否形成了可审查证据：

| 状态 | 含义 |
|---|---|
| `None` | 尚无派发交付 |
| `Working` | 本轮实现中 |
| `Submitted` | 已提供 implementation 与 report 两个提交 |
| `Invalid` | 证据缺失、基线错误或提交不可读取 |
| `Accepted` | 该交付已通过验收 |
| `Rejected` | 该交付被退回，不再作为当前候选 |

每次重新派发可产生新的 delivery attempt。旧 attempt 保留用于审计，但不再拥有控制权。

### 4.3 集成状态 `integration_state`

集成状态描述已验收实现相对目标基线的位置：

| 状态 | 含义 |
|---|---|
| `NotApplicable` | 文档、调查等无需进入代码基线 |
| `Pending` | 已有候选或已验收，但目标基线尚未包含它 |
| `Integrated` | `integrated_commit` 可用祖先关系或经批准的等价性证据证明目标基线包含已验收变更 |
| `Failed` | 集成尝试失败，等待人工处理或新迁移 |

约束：

- `task_state: accepted` 时，交付必须为 `delivery_state: accepted`。
- 代码任务在 `Accepted` 时通常仍是 `integration_state: pending`。
- 只有 `task_state: integrated` 才表示后继 worktree 默认可从公共基线获得成果。
- 无需集成的任务可由 `Accepted` 直接转为 `Integrated`，并记录 `NotApplicable` 原因。

## 5. 任务状态定义

| 状态 | 定义 | 是否终态 |
|---|---|---|
| `Draft` | 目标或验收条件尚未冻结 | 否 |
| `Ready` | 规格完整、依赖满足、可进入调度 | 否 |
| `Dispatched` | 已针对派发前验证过的真实岗位 route 建立唯一派发并写入 outbox；尚不等于角色已接单 | 否 |
| `InProgress` | 角色已确认接单或出现可验证工作活动 | 否 |
| `ReviewReady` | 本轮双提交交付证据完整，等待 PM 验收 | 否 |
| `Returned` | PM 拒绝当前交付，等待修订或重派 | 否 |
| `Blocked` | 存在明确、可解除的阻塞条件 | 否 |
| `Accepted` | PM 固定了通过验收的实现提交 | 否 |
| `Integrated` | 验收成果已进入任务声明的目标基线 | 是，正常完成 |
| `Cancelled` | 任务被主动终止，不再执行 | 是 |
| `Superseded` | 任务被另一张卡或新规格替代 | 是 |

`Cancelled` 与 `Superseded` 都不能静默复活。需要继续工作时创建新任务，或产生带理由的显式恢复事件并递增规格版本。

## 6. 状态迁移表

表中的“操作者”均指持有有效 lease 的 PM 控制面；角色只发送请求或证据，不直接执行迁移。

| 当前状态 | 事件 | 操作者 | 前置条件 | 后态 | 必要副作用 |
|---|---|---|---|---|---|
| `Draft` | `TASK_SPECIFIED` | PM | 规格字段完整；验收条件可验证；路径边界明确 | `Ready` | 固定 `revision`；计算依赖与并发条件 |
| `Ready` | `TASK_DISPATCHED` | PM | 依赖满足；WIP 有容量；冲突与模型策略 guard 通过；PM/岗位真实任务、精确标题、PM callback route、host、独立 worktree 均 verified；创建新可见任务已有显式授权；已生成新 `dispatch_id` | `Dispatched` | 创建 attempt；冻结 `model_selection`；写 outbox；记录不可变 `base_commit`；随后向已验证 route 真实发送并记录 receipt |
| `Dispatched` | `DISPATCH_ACKNOWLEDGED` | PM | ack 的 `dispatch_id` 与当前 attempt 一致 | `InProgress` | 记录 ack 时间与角色逻辑 ID |
| `Dispatched` | `WORK_OBSERVED` | PM | 未收到 ack，但能验证当前 attempt 已开始工作 | `InProgress` | 记录观察证据；不得仅凭 heartbeat 推断 |
| `InProgress` | `DELIVERY_SUBMITTED` | PM | 回调 ID 当前有效；`delivery_status: completed`；双提交与全部验收证据完整；report 可解析；基线匹配 | `ReviewReady` | 固定交付引用；标记 `delivery_state: submitted` |
| `ReviewReady` | `DELIVERY_ACCEPTED` | PM | 验收命令通过；acceptance 已写；实现提交与审查对象一致 | `Accepted` | 固定 `accepted_commit`；交付状态改为 `accepted`；关闭活动派发 |
| `ReviewReady` | `DELIVERY_RETURNED` | PM | acceptance 明确列出未通过项 | `Returned` | 当前 attempt 的交付状态改为 `rejected`；保存返工要求；关闭活动派发 |
| `Returned` | `TASK_REQUEUED` | PM | 返工要求已明确；若规格变化则已创建新 revision | `Ready` | 使旧派发失效并清空活动 `dispatch_id`；下一次派发时再递增 attempt |
| `Accepted` | `CHANGE_INTEGRATED` | PM | 目标基线通过祖先关系或等价性证据包含已验收变更；集成验证通过 | `Integrated` | 固定 `integrated_commit` 与映射证据；重新计算后继任务 |
| `Accepted` | `INTEGRATION_FAILED` | PM | cherry-pick、merge 或验证失败 | `Blocked` | 记录阻塞种类 `integration_conflict` 与恢复目标 `Accepted` |
| 任意非终态 | `TASK_BLOCKED` | PM | 阻塞事实、责任方、解除条件明确 | `Blocked` | 保存 `resume_state`、`blocker`、`owner`、复查时点 |
| `Blocked` | `BLOCKER_RESOLVED` | PM | 解除证据存在；原状态前置条件仍成立 | `resume_state` 或安全重算状态 | 清除活动阻塞；保留历史；重新运行 guards |
| `Blocked` | `BLOCKER_RESCOPED` | PM | 原规格不再可执行，需要变更范围 | `Draft` | 递增 `revision`；使旧派发与交付失效 |
| `Blocked` | `BLOCKER_CANCELLED` | PM | Owner/PM 明确停止；理由已记录 | `Cancelled` | 关闭派发与 outbox；释放 WIP/冲突锁 |
| `Draft`、`Ready`、`Dispatched`、`InProgress`、`ReviewReady`、`Returned`、`Blocked`、`Accepted` | `TASK_CANCELLED` | PM | 有明确终止理由；处理在途消息策略已记录；已验收成果尚未整合或已另行处置 | `Cancelled` | 撤销当前派发；释放资源；忽略后续陈旧回调 |
| `Draft`、`Ready`、`Dispatched`、`InProgress`、`ReviewReady`、`Returned`、`Blocked`、`Accepted` | `TASK_SUPERSEDED` | PM | `superseded_by` 指向有效替代任务 | `Superseded` | 建立双向引用；撤销派发；释放资源 |

### 6.1 禁止的捷径

- `Draft → Dispatched`：跳过规格冻结与依赖检查。
- `InProgress → Accepted`：跳过完整交付和审查证据。
- `ReviewReady → Integrated`：验收与集成必须是两个可审计事件。
- `Accepted → Ready`：如验收失效，应以新事件说明回滚或另开修复卡。
- heartbeat 直接推动任何业务状态。

## 7. Blocked 的恢复语义

`Blocked` 不是“暂时不知道怎么办”的垃圾桶。进入它必须提供 `resume_state`、阻塞种类与可核实描述、责任方、解除证据、复查时间，以及当前 attempt 是否仍有效。

解除时不能无条件回到 `InProgress`。控制面应按以下顺序重算：

1. 原规格是否仍有效；无效则 `BLOCKER_RESCOPED → Draft`。
2. 当前派发是否仍有效；租约过期或角色已消失则回 `Ready` 重派。
3. 若交付证据已经完整，则进入 `ReviewReady`。
4. 若是集成阻塞且验收仍有效，则回 `Accepted`。
5. 其余情况才回 `resume_state`，并重新运行其前置条件。

阻塞时间不自动等于失败。超时只产生告警或升级事件，不得自行取消任务。

## 8. 依赖与解锁规则

任务卡用 `depends_on` 声明前置任务，并可为每项依赖指定要求。

默认规则：

```yaml
depends_on:
  - task_id: TC-001
    required_state: integrated
```

也就是说，**默认只有前置任务 `Integrated` 才能解锁后继任务。**

允许依赖 `Accepted` 的唯一情形是：后继任务的 `base_commit` 已经包含前置任务的 `accepted_commit`。控制面必须用 Git ancestry 验证，而不是相信字段声明。

这种例外适用于：

- 后继任务直接从前置角色分支或集成候选分支创建 worktree；
- 一组明确编排的串行任务共享同一临时基线。

例外必须记录：

- `required_state: accepted`；
- 被依赖的 `accepted_commit`；
- 已验证包含它的后继 `base_commit`；
- 验证时间与验证命令结果摘要。

如果前置任务后续更换了 `accepted_commit`，原依赖验证立即失效，后继任务必须阻塞、重基线或显式接受偏差。

## 9. 并发调度约束

依赖满足不等于可以立刻派发。PM 还必须同时通过以下 guards。

### 9.1 WIP 限制

至少设置：

- 项目级最大活动任务数；
- 每个角色的最大活动任务数；
- 高风险任务的独立上限。

活动任务通常指 `Dispatched`、`InProgress`、`ReviewReady`。`Blocked` 是否占 WIP 必须由项目策略明确；默认占用，避免用阻塞状态绕过容量。

### 9.2 `conflict_surfaces`

任务规格必须声明潜在冲突面，例如：

```yaml
conflict_surfaces:
  paths: [packages/api/src/auth]
  symbols: [SessionService]
  contracts: [openapi/auth.yaml]
  migrations: [database]
```

控制面在派发前比较所有活动任务：

- 同一 migration surface 默认互斥；
- contract 变更与其消费者并发时，必须先冻结契约或建立依赖；
- 路径重叠不一定禁止，但必须有明确 owner 或合并顺序；
- 风险无法自动判断时，保守地保持 `Ready` 并请求 PM 决策。

guard 结果应写入派发事件，不能只存在于聊天。

## 10. PM 租约与 fencing

为了防止两个 PM 同时写状态，控制面写入前必须持有逻辑 lease：

- `holder_id`：可区分不同任期的逻辑 PM 持有人 ID，不能只写恒定角色名 `PM`；
- `lease_epoch`：单调递增的 fencing token；
- `mode`：`manual` 或 `timed`；
- `expires_at`：仅 timed 模式必需的短期有效时间；
- `last_snapshot_commit`：取得 lease 时观察到的快照提交。

manual lease 用于 Lite 人工接力棒，有效至显式 handoff 或 revoke；每次接力都必须递增 epoch 并写 event。timed lease 用于自动化或多进程场景，必须有 expires_at、heartbeat 与 fencing。

每次状态 commit 必须携带当前 `lease_epoch`。写入前同时检查：

1. holder 与 epoch 仍为最新；
2. timed 模式下 lease 未过期；manual 模式下尚未 handoff/revoke；
3. 仓库头部仍等于预期的 `last_snapshot_commit`。

任一检查失败，旧 PM 立即成为只读实例，重新读取快照，不得凭本地记忆继续写。

lease 的本机锁可放 `.agentdesk/runtime/`，但 epoch 的审计事实必须进入事件。若使用远程协调存储，其值也必须在迁移事件中留下引用。

## 11. Outbox、派发与幂等

### 11.1 先提交意图，再发送消息

派发采用 transactional outbox 思路：

1. PM 生成全局唯一 `dispatch_id`。
2. 在任何状态迁移前，验证真实 PM route、真实且独立的岗位 route、规范标题、host/worktree、single-flight 和 callback 目标；若须创建 Codex 可见任务，先取得显式用户授权并按适配器完成创建/改名/读取核验。
3. 完成不可变 `task.dispatch` outbox；其九字段 `model_selection` 与账本逐字段相同。
4. 计算 outbox 文件仓库中 UTF-8/LF 原始字节的 SHA-256，写入匹配 `TASK_DISPATCHED.payload_digest`。
5. 在首次加入该 `current_dispatch` 的同一状态 commit 中迁移到 `Dispatched`，并新增 event、outbox、快照与视图。
6. strict validator 证明原子提交后，由 runtime adapter 向 verified `thread_id` 发送；保存 `dispatch_receipt`。收到角色 ack 后，以新事件迁移状态。

这样即使进程在 commit 后、发消息前崩溃，恢复后仍能补发。

### 11.2 幂等键

所有派发、ack、进度、完成与阻塞消息必须携带：

- `task_id`
- `dispatch_id`
- `attempt`
- `revision`

三类 ID 使用不同命名空间：状态迁移使用 `event_id`，PM 发出的 outbox 消息使用 `message_id`，角色回调使用 `callback_id`。每个 event ID 和 outbox message ID 在全仓库分别唯一；控制面按对应 ID 去重，并校验 `dispatch_id + message_type + payload_digest` 没有发生冲突。

同一 `dispatch_id` 的消息重放必须复用完全相同的模型 requirement/selection 快照；binding 后续被禁用或修改不允许改写历史派发。若必须更换模型，关闭旧执行并按恢复规则建立新 attempt。

处理规则：

- 完全相同的重复消息：返回已处理结果，不新增业务迁移。
- 同一 ID、不同 payload：视为协议冲突并告警。
- 非当前 `dispatch_id` 的回调：记录为 stale，不改变任务状态。
- 已终态任务的迟到消息：仅记审计，不复活任务。

## 12. Heartbeat 只负责对账

heartbeat 是主动 callback 之后的兜底巡检，不是主通知机制或隐式调度器。它可以重发未确认 outbox、发现失败/缺失 receipt、发现超时或失联、检查快照/视图、验证提交可读性并告警；不得把扫描到 report 当作 Worker 已主动回调，不得因静默判定成败、根据新 commit 自动验收、绕过 lease，或重复派发仍有效的 attempt。

任何由巡检触发的状态变化，仍须经过普通前置条件并形成原子状态 commit。

## 13. Git 与 worktree 的双提交交付

### 13.1 派发时固定基线

每次派发必须记录不可变 `base_commit`，而不是只记分支名。角色 worktree 必须从该提交或已证明等价的提交开始。

分支名称只用于发现，commit 才用于验证。

### 13.2 两个提交解决 report 自引用问题

角色完成时按顺序创建：

1. **`implementation_commit`**：只包含实现、测试和任务允许的文档变更；
2. **`report_commit`**：以 implementation commit 为父提交，新增或更新结构化 report，并在 report 内引用 `implementation_commit`。

report 不尝试记录包含自身的 commit SHA。回调同时携带两个 SHA：

```yaml
task_id: TC-042
dispatch_id: dsp_...
implementation_commit: abc123...
report_commit: def456...
```

两个提交必须发布到 PM 可读取且受保留策略保护的 ref 或 artifact；在任务进入终态并超过审计保留期前，不得删除唯一可达引用。

控制面验证：

- 两个 commit 均可读取；
- `report_commit` 的历史包含 `implementation_commit`；
- 实现相对 `base_commit` 的变更不越过 `allowed_paths`；
- report 内容与回调摘要一致；
- 当前派发 ID、attempt、规格版本完全匹配。

### 13.3 验收固定对象

PM 的 acceptance 必须固定：

- 被审查的 `implementation_commit`；
- 对应 `report_commit`；
- 实际运行的检查与结果；
- 风险与豁免；
- 结论和时间。

通过后，`accepted_commit` 默认等于该 `implementation_commit`。若 PM 在验收过程中补做修复，必须形成新的实现提交并重新生成或补充 report，不能悄悄把 acceptance 指向未审查内容。

角色在 `ReviewReady` 后继续向原分支提交，不会改变已固定的审查对象。若要让新增提交进入审查，PM 必须先退回或撤销当前交付，再以新 attempt 和新 `dispatch_id` 重新派发。

### 13.4 集成后才默认解锁

集成可以是 merge、cherry-pick、rebase 后等价应用或其他项目批准的方式，但必须记录 `integrated_commit`。若 `accepted_commit` 不是其祖先，还必须在 integration event 中记录来源提交、目标提交、patch/tree 等价性证据和验证结果。

正常代码任务的顺序是：

```text
implementation_commit
→ report_commit
→ acceptance 固定 accepted_commit
→ 集成并固定 integrated_commit
→ 任务进入 Integrated
→ 默认解锁后继
```

`Accepted` 只代表“这份实现通过审查”，不代表其他 worktree 已经能看到它。

## 14. 规格修订、取消与替代

任务卡是版本化规格，不是可随意覆盖的便签。目标、验收条件、依赖、允许路径、基线或安全约束变化时必须递增 `revision`；不影响执行语义的文案修正可保留版本，但要记录修订 commit。派发永远绑定确定版本；重大改向应将旧任务 `Superseded`，再用新任务承接。

取消或替代在途任务时，PM 必须先使当前 `dispatch_id` 失效，再通知角色停止。迟到交付只作为审计材料，不进入 ReviewReady。

## 15. 最小一致性不变量

控制面每次提交前必须验证：

1. 每个任务只有一个当前 `task_state`。
2. 每个活动任务至多有一个当前有效 `dispatch_id`。
3. `ReviewReady` 必须有可读的双提交交付。
4. `Accepted` 必须有 acceptance 与不可变 `accepted_commit`。
5. `Integrated` 必须有 ancestry/等价性证据与 `integrated_commit`。
6. `Ready` 的所有依赖和 guards 均已满足。
7. 终态任务没有活动 outbox、WIP 占用或冲突锁。
8. 状态迁移 event 的前态等于上一快照状态，后态等于新快照状态。
9. 同一状态 commit 中 event、snapshot 与生成视图一致。
10. 所有写入使用当前有效的 `lease_epoch`。
11. 每个活动或已有 report 的历史派发都有首次状态 commit 中的匹配 event/outbox；其九字段 model snapshot 与账本或 report 完全一致，event/outbox ID 分别唯一，outbox 原始字节摘要匹配。
12. `require_pm_approval` 的降级派发有同 commit 或更早、未过期且派发前/时未撤销的结构化批准 event；approval ID 本身不构成证据。
13. 每个 Active role 都有唯一 `role_no + role_id + role_name`，规范标题可确定且无冲突。
14. Standard / Automated 的每个活动 dispatch 都绑定 distinct、真实、`verified` 的 PM/Worker route；actual/expected title 严格匹配，host/worktree 可达且 worktree 没有复用。
15. 每个真实 dispatch transport 都有以 `dispatch_id` 为键、与 `message_id + role_id + source_thread_id + destination_thread_id` 匹配的本机 receipt；receipt 缺失时不能宣称已送达或进入闭环运行。outbox `payload_digest` 仍由仓库 event/outbox 证据独立校验。
16. 每个 Worker 完成声明都有以 `callback_id` 为键、与 `dispatch_id + source/destination role + source/destination thread` 匹配且 `status: received` 的 callback receipt，目标等于 delegation source 与 verified PM route；只有 report 没有 receipt 属于漏回调恢复场景。
17. heartbeat 只纠正/告警 transport 差异，不替代 Worker 主动 callback，也不直接验收、实现、合并或新建 attempt。

校验失败时，系统应停止新派发，保留现状并进入恢复流程；不得通过手改生成视图掩盖问题。

协议不绑定编排平台、消息系统或 Git 托管服务，但兼容实现必须保留本页全部不变量；缺少 dispatcher/runtime adapter 时只能提供仓库控制面，不能宣称多任务自动推进闭环。任务、交付与验收字段见 [数据模型与模板](schemas-and-templates.md)，事件、消息与校验格式见 [事件、Outbox 与校验](events-outbox-and-validation.md)；Codex 真实任务操作见 [Codex 任务运行时适配器](codex-runtime-adapter.md)；接管、卡死、重复回调、快照损坏和集成失败的操作步骤见 [运行手册与故障恢复](runbooks-and-recovery.md)。
