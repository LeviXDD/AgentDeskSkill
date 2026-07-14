# 运行手册与故障恢复

本参考规定 PM、开发、QA 与整合角色的操作顺序、风险审批、对账方式和故障恢复动作。日常接管、派卡、验收或现场恢复时按相应 Runbook 执行。

## 目录

- [运行原则与开工不变量](#1-运行原则)
- [Definition of Ready](#3-definition-of-ready)
- [Definition of Done](#4-definition-of-done)
- [PM Runbook](#5-pm-runbook)
- [开发、QA 与整合 Runbook](#6-开发角色-runbook)
- [风险分层与审批](#9-风险分层与审批)
- [Heartbeat 对账](#11-heartbeat只做-reconcile)
- [故障场景与恢复](#12-故障场景与恢复表)
- [实施成熟度与迁移](#13-实施成熟度)
- [Validator 检查](#15-validator--ci-检查列表)
- [反模式与最小恢复口令](#16-反模式)

本参考回答“谁在什么时候做什么，以及出错后怎样安全恢复”。字段定义与完整状态枚举以 [核心协议与状态机](protocol.md) 为准，任务、交付与验收模板见 [数据模型、Schema 与模板](schemas-and-templates.md)，事件、消息和校验格式见 [事件、Outbox 与校验](events-outbox-and-validation.md)；本参考不另造第二套协议。

## 1. 运行原则

1. **仓库和控制面记录是事实，聊天只是传输。**但仓库中的 outbox 意图不等于真实任务已创建或消息已送达；Standard / Automated 还必须有 verified route 与 transport receipt。
2. **PM 是任务终态的唯一裁决者。**开发、QA、整合角色都只能提交证据和建议。
3. **同一任务修订版同一时刻只能有一个有效执行 attempt。**
4. **同一工作重试必须可幂等。**出站消息重发复用 `message_id + dispatch_id`，回调重发复用 `callback_id`；只有明确重启执行才递增 `attempt` 并创建新 `dispatch_id`。
5. **验收通过不等于已进入公共基线。**`Accepted` 与 `Integrated` 必须分开判断。
6. **Heartbeat 只做 reconcile。**它发现差异、补状态、补通知，不直接重跑实现、测试、合并或发布。
7. **先审批，后执行高风险动作。**生产、凭据、支付、迁移、权限和不可逆外部动作不得事后补批。
8. **恢复优先保全证据。**不得因为超时、不可见或冲突而删除分支、worktree、report 或日志。
9. **Leader 定策略，runtime 做选择。**任务/风险只能抬高模型硬下限；模型强度永不授予审批、凭据、验收或发布权限。
10. **真实岗位任务和主动 callback 不可降级。**Standard / Automated 使用独立、规范命名的真实任务；Worker 主动回调是主路径，heartbeat 只兜底。

## 2. 开工前的最小不变量

每次派卡、接卡、验收和恢复都先核对以下不变量：

- `task_id + revision` 唯一标识任务说明。
- `task_id + revision + attempt` 唯一标识一次执行；旧 attempt 不会因消息重试重新生效。
- `dispatch_id` 唯一标识一次逻辑派发；传输重试不改变它。
- `base_commit` 是不可变提交，不使用会移动的分支名代替。
- `implementation_commit` 是本次交付中冻结的实现提交。
- 一个 worktree 在同一时间只绑定一个活动 attempt。
- 一个角色真实任务默认只处理一个活动 attempt，且与 PM/Leader 真实任务使用不同 `thread_id`。
- 每个 Active role 同时具有唯一 `role_no`、`role_id`、`role_name`；实际标题严格为 `<role_no> . <role_name>`。
- PM 与 Worker route 均为 `agentdesk.routes/v2` 的 `verified`，标题、host、worktree 与真实 task/thread 已用 provider read/list 核对。
- 每个真实 dispatch 有以 `dispatch_id` 为键的 receipt；每个完成通知有以 `callback_id` 为键且 `status: received` 的 receipt。
- 控制面只有当前持有有效 lease 的 PM 可以写。
- report、回调、测试结果都必须能追溯到同一 revision、attempt 和 head。
- 下游任务的 base 必须真实包含所有已声明依赖，而不只看依赖是否 `Accepted`。
- `task_card_commit` 同时固定任务卡与当时的 `ROLE-POLICIES.yaml`；当前派发的 model selection 与 report executor snapshot 一致。
- 每个活动或已有 report 的终态 dispatch 都能追溯到 first-state commit 同时新增的唯一 `TASK_DISPATCHED` event 与 `task.dispatch` outbox。

任一不变量无法证明时，停止自动推进，进入 reconcile 或人工确认。

## 3. Definition of Ready

任务满足以下条件才可从草稿进入 `Ready`：

- [ ] `task_id`、`revision`、`type`、目标 `role_id` 完整且通过 schema 校验。
- [ ] 目标角色处于 Active，并具有唯一 `role_no` / `role_id` / `role_name`；`expected_thread_title` 精确等于 `<role_no> . <role_name>`。
- [ ] Standard / Automated 的 PM 与目标岗位是真实、彼此独立、可读取的任务；实际标题精确匹配 `<role_no> . <role_name>`，route 为 `verified`，线程和独立 worktree 容量可用。
- [ ] 需要创建新的 Codex 可见任务时，用户已对具体岗位或明确批次显式授权；没有用“开始开发/初始化”推定授权。
- [ ] PM callback source route 已 verified；dispatcher 能真实发送并保存 dispatch/callback receipt。
- [ ] 目标角色在 `ROLE-POLICIES.yaml` 有策略；任务与风险只抬高 floor，能力并集与 deliberation 硬要求可由 enabled binding 满足。
- [ ] dispatcher/runtime adapter 能真实启动该 binding 并固定 provider/model/deliberation；否则该策略只能审计，不能宣称已强制路由。
- [ ] 目标、范围、禁止区、验收标准均可观察和验证。
- [ ] `base_commit` 已固定且在执行环境可见。
- [ ] `depends_on` 已声明，且依赖已进入当前 base；否则明确等待整合。
- [ ] `allowed_paths`、`blocked_paths`、`conflict_surfaces` 无未解决冲突。
- [ ] 必跑检查使用受控命令 ID 或可信脚本，不包含任意 shell 片段。
- [ ] report 路径或独立 artifact 通道已授权，不与路径限制矛盾。
- [ ] 风险等级、所需 capability、审批人和审批结果已明确。
- [ ] 涉及迁移或外部副作用时，回滚方案、检查点和责任人已写明。
- [ ] 无尚待 Owner 决策的产品方向、凭据或生产权限问题。
- [ ] PM 已提交任务卡，并固定其 `task_card_commit`。

DoR 未满足时只能保持 Draft/Blocked，不得靠“角色先做起来再说”绕过。

## 4. Definition of Done

### 4.1 执行角色的交付完成

- [ ] 改动只来自声明的 revision 和 attempt。
- [ ] `implementation_commit` 已提交、冻结并可被 PM 读取。
- [ ] report 可被 PM 从明确的 commit/ref/artifact 读取。
- [ ] 验收标准逐项附证据，不以“已完成”代替证据。
- [ ] 必跑检查有完整结果；失败、跳过和 flaky 均如实记录。
- [ ] 偏离、契约变化、外部副作用和遗留问题已披露。
- [ ] 完成回调引用正确的 `dispatch_id`、attempt、revision 和 head。
- [ ] Worker 已主动向 delegation `source_thread_id` 发送回调；它与 verified PM route 一致，并已有 `status: received` 的 callback receipt。

达到这里只表示可以进入 `ReviewReady`，不表示任务完成。

在 Worker 自己任务中输出总结、只写 report 或等待 heartbeat 扫描，都不满足主动回调条件。若 report 已持久化而 callback receipt 缺失，按 F05 恢复并明确报告通知链路缺陷。

若 report 为 `partial` 或 `blocked`，本节完成条件不成立：PM 应保持 InProgress、进入 Blocked，或退回/修订任务，不能直接进入 ReviewReady。

### 4.2 卡片完成

- [ ] PM 独立读取 task、report、精确 diff 和关键源码。
- [ ] PM 在隔离环境复核风险相称的测试与质量门。
- [ ] 路径边界、提交祖先关系、审批记录和契约变化均通过检查。
- [ ] PM 写入不可变验收记录，结论为 `Accepted`。
- [ ] 若需要整合，任务为 `state: accepted` 且 `integration_state: pending`。

### 4.3 阶段或依赖完成

- [ ] 所需 `accepted_commit` 已进入指定 integration baseline。
- [ ] 集成测试和必要 E2E 通过。
- [ ] `Integrated` 记录包含 integration commit 与来源 commit 列表。
- [ ] 下游任务的 `base_commit` 已更新到包含这些来源的提交。

只有达到这一层，依赖方才可把该能力视为可用。

## 5. PM Runbook

### 5.1 接管与开班

1. 获取 PM lease，记录 owner、epoch、开始时间和过期时间。
2. 读取 canonical state、未完成 outbox、未处理 inbox 和活动 attempt。
3. 对照 Git refs、worktree、reports、线程路由执行一次 reconcile。
4. 发现双活、漂移或孤儿 attempt 时，先冻结自动派卡。
5. 只在不变量成立后恢复队列推进。

### 5.2 准备与派卡

1. 按 DoR 校验任务卡，冻结 `revision`、`task_card_commit` 及该 commit 的岗位策略，并确认 PM lease。
2. 从 `ROLES.md` 取得目标的 `role_no`、`role_id`、`role_name` 与派生 `expected_thread_title`；标题列不等于 `<role_no> . <role_name>`、字段缺失、重复或非 Active 时停止。
3. 验证当前 PM/Leader 真实任务及 delegation `source_thread_id`，使 PM route 为 `verified`。没有可靠 PM callback route 时不得派卡。
4. 分配唯一 worktree、branch、next attempt、`dispatch_id` 和角色 lease；一次 dispatch 只绑定一个 primary execution model。worktree HEAD 等于冻结 base，且不与其他 attempt 共用。
5. 复验目标岗位 task/thread。若没有 verified route，先检查用户是否明确授权创建该可见任务；没有则保持 Ready 并询问。获得授权后严格执行 `list_projects → create_thread → set_thread_title → read_thread/list_threads verify`，再写 `routes/v2 status: verified`。随后运行 `validate_runtime.py --project <repo> --role-id <role-id>`；失败时不得派卡。完整步骤见 [Codex 任务运行时适配器](codex-runtime-adapter.md)。
6. 使用完整 `task_card_commit` 运行 `scripts/select_model.py`。若 `require_pm_approval` 选中低于 preferred 的 tier，分配全仓唯一 `APR-*` 并加入 task 的 `granted_approval_ids`，由 PM 创建范围匹配、未过期的 `MODEL_DEGRADATION_APPROVED` event；它必须在派发 commit 中新增或已存在于其祖先，原记录保持 `revoked_at: null`，approval epoch 不大于 dispatch epoch，且派发前/时不存在匹配 revocation event。
7. 把 selector JSON 的恰好九字段原样复制到 `current_dispatch.model_selection` 和 outbox `model_selection`；两者必须深度完全相等。失败时保持 Ready/Blocked。
8. 完成含匹配 `event_id` 与四元组的 `task.dispatch` outbox；对其仓库 UTF-8/LF 原始字节计算 SHA-256，写入 `TASK_DISPATCHED.payload_digest`，之后不再改 outbox。
9. 生成视图并运行 `validate_project.py --pre-commit`；它只做拟提交结构检查且应提示不能证明原子 Git 历史。检查 scoped diff。
10. 用一个 commit 首次加入该 `current_dispatch`，并同时新增 `TASK_DISPATCHED` event、outbox、更新后的账本/视图以及 same-commit approval event（如有）。dispatch 与 approval 的 `lease_epoch` 均为整数且不小于 1；同 commit 时必须相同。
11. 不带迁移 flag 运行 strict validator 与 view `--check`，证明 first-state 原子提交、ID 唯一、摘要、approval 和九字段一致；通过后才用 `send_message_to_thread` 把自包含任务投递到 verified worker route，并写入以 `dispatch_id` 为键的 transport receipt。
12. 收到与当前派发一致的角色 ack 后，把 receipt 更新为 `acknowledged` 并迁移到 `InProgress`；receipt 证明 transport，ack 证明接单。发送失败时不写成功 receipt、不进入 InProgress，只重放原 outbox并复用全部 IDs、目标与 snapshot。每次活动态 transport 更新后运行 `validate_runtime.py --project <repo> --role-id <role-id> --check-active`。

### 5.3 验收

1. 将回调当作唤醒信号，不把它当作可信验收结论。
2. 核对 report 与当前 revision、attempt、base、head 及 executor model snapshot 是否一致。
3. 验证 `base_commit` 是 `implementation_commit` 的祖先，并冻结待审提交。
4. 计算精确 diff，检查越界文件、控制面文件和冲突面。
5. 阅读关键实现，复核运行命令和风险证据。
6. 在最小权限、无生产凭据的隔离环境运行所需检查。
7. 写验收记录，给出 Accepted、Returned 或 Blocked。
8. 只有 `Integrated` 条件满足时才解锁依赖该实现的任务。

### 5.4 收班

1. 确认 outbox 无悬空发送，inbox 无未处理有效事件。
2. 记录活动 lease、下一次检查时间和明确阻塞人。
3. 提交控制面变更并验证新 PM 可从仓库恢复。
4. 释放或续租 PM lease；不得让过期 lease 继续写状态。

## 6. 开发角色 Runbook

### 6.1 接卡

1. 验证任务属于自己的 Active role；从 `ROLES.md` 核对 `role_no`、`role_id`、`role_name`，并确认当前真实任务标题精确匹配且无其他活动 attempt。
2. 比对消息与任务卡的 revision、`task_card_commit`、`base_commit` 和 allowed paths。
3. 验证 worktree 唯一、干净、可写，HEAD 等于 `base_commit`；当前 worker route 为 `verified`。
4. 读取委派的 PM `source_thread_id`，与 verified PM route 核对。任一字段陈旧、冲突或 callback route 不一致时停止，不自行猜测最新版或另找 PM 任务。
5. 确认实际 primary executor 与派发模型快照一致；不得把实质工作暗中委派给更弱或未记录的 subagent。

### 6.2 执行

1. 只修改卡片授权路径；控制面文件默认只读。
2. 不擅自改任务目标、验收标准、base、依赖或风险等级。
3. 遇到契约变化先报告，未经授权不扩大范围。
4. 测试使用任务指定的受控命令；不读取或复制无关凭据。
5. 外部副作用必须有匹配 capability 和事前 approval。
6. 需要 material subagent 时先让 PM 创建独立 dispatch/execution evidence；模型更强也不能代替该授权。

### 6.3 交付

1. 固化实现提交，记录 `implementation_commit`。
2. 写 attempt 专属 report，不覆盖旧 attempt 的报告。
3. 明确记录通过、失败、跳过和 flaky 的所有检查。
4. 发布 PM 可读取的 commit/ref/artifact 后再发送回调。
5. 使用 `send_message_to_thread` 主动向 delegation `source_thread_id` 回调；它必须与 verified PM route 相同。只在本任务输出 final answer 或写 report 不算回调。
6. transport/PM 收到后，写入以同一 `callback_id` 为键、`status: received` 的 callback receipt。回调重试复用同一 ID、payload 和目标；不得为了“确保收到”新建交付事实。
7. receipt 未确认时不得宣称 Worker 生命周期完整；保留 durable commits/report 并报告 callback 失败，等待 PM/heartbeat 按 F05 恢复。
8. report 的 `executor_model` 逐字段复制派发 `model_selection`；provider 支持时记录移动 alias 解析出的稳定 model revision。
9. callback receipt 入账后运行 `validate_runtime.py --project <repo> --role-id <role-id> --check-active`；失败时保持待恢复，不把 final answer 当作通过。

在 `DELIVERY_SUBMITTED` 之前，角色可在同一 attempt 内自修并以新的 `implementation_commit` 交付；进入 `ReviewReady` 后审查对象被冻结。只要 PM 已产生 `DELIVERY_RETURNED`，旧 attempt 就永久关闭，返工必须由 PM 以同一 revision 的新 attempt 和新 `dispatch_id` 重新派发。

## 7. QA Runbook

QA 是合法 card type 和合法 role，可以拥有独立任务卡、worktree、测试改动和 report；它不是 PM 的附属备注步骤。

### 7.1 接卡与设计

1. 验证 QA 卡引用的是冻结的候选 head 或 integration head。
2. 从验收标准、风险面和历史缺陷推导测试矩阵。
3. 明确环境、数据、权限、可重复性和清理方式。
4. 测试代码只能写入 QA 卡授权的路径。

### 7.2 执行与报告

1. 记录环境指纹、候选 commit、命令、结果和证据位置。
2. 区分产品缺陷、环境失败、测试自身缺陷和 flaky。
3. 失败应最小化复现，但不得擅自修改产品代码掩盖失败。
4. 输出“建议通过 / 建议返工 / 无法判定”及残余风险。

### 7.3 权限边界

- QA 可以阻止质量门变绿，也可以提出返工建议。
- QA 不得把自己的“建议通过”写成 `Accepted`。
- QA 不得替 PM 接受风险、批准生产权限或决定产品取舍。
- 最终验收始终由持有有效 lease 的 PM 写入。

## 8. 整合角色 Runbook

1. 只接收 PM 明确列出的 `accepted_commit` 和目标 integration baseline。
2. 创建专属 integration branch/worktree，不在开发角色 worktree 中拼接。
3. 验证每个来源 head、验收记录和契约声明均可见。
4. 按任务指定顺序合并或重放，保留来源 commit 可追溯性。
5. 只解决整合卡授权的冲突；产品语义冲突返回 PM 决策。
6. 运行跨模块、build、lint、迁移检查和必要 E2E。
7. 输出 integration report：来源 heads、冲突决策、测试、最终 commit。
8. PM 验收 integration commit 后，才能写 `Integrated` 并更新下游 base。

整合角色不得因为“都是 Accepted”而自行选择版本，也不得顺手修改未授权业务逻辑。

## 9. 风险分层与审批

| 风险层 | 典型任务 | 执行前审批 | 验收要求 |
|---|---|---|---|
| L0 低 | 文档、小范围测试、无行为变化重构 | PM | diff + 定向检查 |
| L1 常规 | 普通前后端功能、局部契约内改动 | PM | 定向测试 + typecheck/build |
| L2 较高 | 公共 API、数据迁移、认证、依赖升级、跨模块整合 | PM + 对应专项角色 | 消费方回归 + 回滚验证 + 独立复核 |
| L3 高 | 加密、权限模型、支付、生产配置、不可逆外部动作 | Owner 事前批准 + PM + 安全/基础设施复核 | 隔离演练 + 双人复核 + 明确回滚门 |
| L4 关键 | 生产发布、批量删除、密钥轮换、不可逆迁移 | 专门发布流程；默认不由本协议自动执行 | 人工逐步确认、审计和应急值守 |

审批记录至少包含：`approval_id`、范围、批准人、时间、有效期、允许的 capability、目标环境和撤销条件。

批准“开发任务”不等于批准生产操作；批准一次操作也不得被其他 attempt 复用。

模型降级批准是更窄的 attempt-scoped 证据：`require_pm_approval` 时必须使用 [事件、Outbox 与校验](events-outbox-and-validation.md) 的 `MODEL_DEGRADATION_APPROVED` schema，并与 selector 的 selected/preferred tier 对账；仅把 `APR-*` 写入账本或命令行不构成批准。

批准记录不可修改；PM 撤销时追加携带有效 `lease_epoch` 的 `MODEL_DEGRADATION_REVOKED`，同一 approval 最多一个。撤销 commit 是批准的严格后代，且撤销时间/epoch 不早于批准。派发前/时撤销使批准无效；执行中撤销须停止并进入恢复；交付完成后撤销只影响未来 attempt，不反向改写历史证据。

## 10. 验收记录

PM 每次结论都写不可变验收记录，最低字段如下：

```yaml
schema_version: agentdesk.acceptance/v2
task_id: TC-XXX
revision: 3
attempt: 2
type: implementation
role_id: BE
reviewed_dispatch_id: <dispatch-id>
base_commit: <sha>
implementation_commit: <sha>
report_commit: <sha>
accepted_commit: <sha-or-null>
reviewer_role_id: PM
reviewer_id: <pm-id>
lease_epoch: 12
decision: accepted | returned | blocked
owner_approval:
  gate: none
  approval_ids: []
evidence_refs: []
residual_risks: []
created_at: <timestamp>
```

验收正文必须说明：

- 每条验收标准的证据；
- 精确 diff 与路径边界结论；
- 实际运行的检查及结果；
- 未运行检查及理由；
- 契约、迁移、安全和外部副作用结论；
- 返工必修项或接受后的整合动作；
- 谁可以据此解锁哪一张下游卡。

## 11. Heartbeat：只做 Reconcile

每轮 heartbeat 执行以下固定顺序：

1. 获取短期 reconcile lease；已有有效执行者时立即退出。reconcile lease 只防止重复扫描，不授予控制面写权。
2. 读取 canonical state、outbox、inbox、`routes/v2`、`transport-receipts/v1` 和活动 lease。
3. 对照真实 task/thread、精确标题、dispatch/callback receipt、Git refs、reports 和 worktrees。
4. 计算“声明状态”与“可验证事实”的差异。
5. 仅执行幂等修复：补 receipt、去重事件、发告警。若要纠正权威状态，必须另外持有有效 PM lease 与 fencing token，并走普通状态迁移。
6. 需要实现、测试、重新派卡、合并或外部动作时，只创建待办或唤醒 PM。
7. 记录本轮 reconcile ID、观察结果和修复动作；无变化时保持安静。

Heartbeat **禁止**：

- 因未见回调而重新执行任务；
- 因测试结果不可见而自动重跑测试；
- 因分支暂时不可见而新建 attempt；
- 因任务已 Accepted 而自行合并或派发依赖卡；
- 在没有 PM lease/CAS 的情况下直接改多个状态文件。

## 12. 故障场景与恢复表

任何恢复动作只要进入 Blocked，都必须一次性写完整 blocker envelope：`blocked_reason`、`blocked_kind`、`blocked_owner`、`unblock_condition`、`review_after`、`blocked_attempt_valid`、`resume_state`、`blocked_at`。下表中的简写不能省略这些字段。

| ID | 场景 | 检测 | 恢复 | 禁止动作 |
|---|---|---|---|---|
| F01 | 消息可见，但控制面没有对应派发状态 | 有 provider receipt、线程消息或角色 ack；账本仍为 Ready，或缺少匹配的 outbox/event | 立即冻结该角色开工；验证消息中的 revision、attempt、dispatch 与任务卡。如可证明为合法派发，用原 `dispatch_id` 写恢复 event 并迁移到 Dispatched；否则撤销消息并重派 | 不重新生成第二个 dispatch，不让角色边做边等，不靠聊天覆盖账本 |
| F02 | 状态已更新但发送失败 | 状态为 Dispatched，但无 receipt/ack；runtime 记录发送失败或超时 | 保持 Dispatched；在任务快照未变时重放原 outbox 消息与 `dispatch_id` | 不把角色视为 InProgress，不为了传输重试创建新 attempt |
| F03 | 重复回调 | callback ID、dispatch、attempt、revision、commit 与已处理回调相同 | 去重为 no-op，记录重复次数；仅补缺失的 inbox ack | 不重复验收，不重复派下一卡，不再次更新终态 |
| F04 | 陈旧 task revision | 回调/report revision 小于 canonical revision，或 task card commit 不一致 | 将旧交付隔离为 stale/superseded artifact，不改变当前任务状态；评估可复用内容后，由 PM 为当前 revision 明确创建新 attempt | 不把旧结果静默套到新标准，不直接 cherry-pick 后标 Accepted |
| F05 | 丢回调或线程失效 | 有 durable report/head 但无 `status: received` callback receipt；route 返回 not found/archived/无权限 | 把它明确标为主动通知链路失败；PM 可依据 immutable artifact 进入验收，同时修复 route/receipt。未完成时关闭旧线程 lease，再决定是否新 attempt | 不因“没消息”判定工作丢失，不伪造 callback receipt，不把 heartbeat 扫描写成 Worker 主动回调，不继续向失效 threadId 盲发 |
| F06 | 角色超时 | lease 过期、`last_seen_at` 超阈值，且无新 commit/report/ack | 先查线程、worktree 和日志；有活动证据则续租，否则进入 Blocked：kind=`role_timeout`、owner=PM、condition=关闭旧角色 lease 并完成现场盘点、attempt_valid=false、resume=`ready`，设置复查时间 | 不在旧 lease 有效时并行派同卡，不删除超时分支/worktree |
| F07 | PM 双活 | 同时存在不同 owner/epoch 写入，或短时间出现冲突状态迁移 | 立即冻结派卡；选定唯一有效 lease/最高合法 epoch；从事件与 Git 事实 reconcile，另一 PM 转只读 | 不让两个 PM 各自“把自己的流程走完”，不手工覆盖对方记录 |
| F08 | worktree 不可见 | 路由路径不存在、位于另一主机/容器，或权限拒绝 | 通过已发布 Git ref/artifact 交换；修复 location binding；必要时在可见环境重建只读 worktree | 不把不可见等同于不存在，不依赖跨主机本地绝对路径作为唯一交付 |
| F09 | report commit 不可见 | 回调给出 SHA，但 PM 仓库 `git cat-file`/fetch 找不到 | 进入 Blocked：kind=`report_unreachable`、owner=当前角色、condition=PM 可解析精确 report ref/artifact、attempt_valid=true、resume=`in_progress`，设置复查时间；满足后再转入 ReviewReady | 不只凭聊天摘要接受，不猜测相近分支或 commit |
| F10 | Accepted 未 Integrated | 依赖卡已 Accepted，但其 `accepted_commit` 不是下游 `base_commit` 的祖先 | 保持 `integration_state: pending`，创建/执行整合卡并暂停下游；已误启动的下游需明确 rebase 或新 attempt | 不以 Accepted 直接解锁依赖，不由开发角色临时合主线 |
| F11 | 分支漂移 | 分支名解析 SHA 与冻结 head 不同，出现 force-push、额外 commit 或 base 变化 | 冻结评审；保存旧 ref；由交付者明确提交 revised head，重新做祖先、diff 和测试检查 | 不针对移动分支继续验收，不 reset/delete 尚未核清的证据 |
| F12 | 测试 flaky | 相同 head 和环境出现不一致结果，或命中已知 quarantine | 保存所有运行；在干净环境按政策复现，区分产品失败与测试缺陷；另建修复卡并记录残余风险 | 不“重试到绿”后隐藏失败，不擅自降低质量门或删除测试 |
| F13 | 凭据/生产权限阻塞 | 命令请求未声明 capability，approval 缺失/过期，secret 或环境不可用 | 在动作前进入 Blocked：kind=`credentials` 或 `external_approval`、owner=Owner、condition=取得最小范围短期授权并验证环境、attempt_valid 由角色 lease 检查决定、resume=`in_progress` 或 `ready`，设置复查时间；暴露时立即吊销和轮换 | 不把密钥写入任务卡、report、日志或聊天，不借用个人凭据，不绕过审批 |
| F14 | 真实任务缺失或标题错误 | route 是占位 ID；PM/Worker thread 相同；read/list 找不到；`actual_title != role_no . role_name` | 保持/退回 Ready，route 标 `stale`；若需新建可见任务先取得明确用户授权，再按 Codex adapter 创建、改名并双重验证 | 不用 worktree、subagent 或同任务切岗冒充岗位任务，不在标题未验证时发送 |
| F15 | Dispatcher/runtime adapter 不可用 | 缺 create/read/list/title/send 工具，或不能获得可信 transport result | 明确降级为 Lite/manual 或 Blocked，列出缺失能力；保留 V2 仓库账本但停止闭环声明 | 不因 validator 通过、outbox 存在或用户可手动转发而宣称 Standard / Automated |

### 12.1 重试判定

- **同一出站消息的传输重试**：复用 `message_id`、`source_event_id`、`dispatch_id` 和 dedupe key，`task_card_commit` 与 payload digest 不变。
- **同一回调的传输重试**：复用 `callback_id`，PM 处理结果必须幂等。
- **执行尚未开始的重新路由**：可保留 attempt，但必须先关闭旧角色 lease 并记录 route revision。
- **执行已开始后的重启**：递增 `attempt` 并创建新 `dispatch_id`，记录被替代的旧 attempt。
- **任务说明变化**：提高 `revision`，旧 attempt 全部失效；不得伪装成普通重发。
- **外部副作用不确定**：先查幂等键和目标系统事实，禁止盲目重试。

## 13. 实施成熟度

### Lite

适合单机、小团队、低并发项目：

- 单一 PM，以手工接力棒实现逻辑 lease；每次接管递增 `lease_epoch` 并留下状态 event；
- 每卡独立 branch/worktree；
- 任务卡先提交，base 使用 SHA；
- 每个 attempt 独立 report；
- 手工派发和验收，不开启自动 heartbeat；
- 明确区分 Accepted 与 Integrated。

Lite 仍不得共享 checkout、覆盖旧 report 或把 QA 当最终验收人。

### Standard

适合稳定的多角色并行开发：

- PM 与每个 Active role 都是真实、独立、可读取的任务，并严格使用 `<role_no> . <role_name>` 标题；
- 创建新可见任务有明确用户授权；每卡独立 worktree，PM/Worker route、host 和 callback target 经验证；
- schema validator 和 CI 路径门；
- PM/角色 lease、单线程 single-flight；
- outbox/inbox、dispatch/event 去重，以及真实 dispatch/callback transport receipts；
- 依赖 DAG、冲突面锁和 integration queue；
- Worker 主动跨任务回调；定时 reconcile heartbeat 只兜底；
- 风险分层审批与不可变验收记录；
- 受控测试命令和隔离执行环境。

### Automated

适合高并发和半无人值守：

- 完整保留 Standard 的真实任务、精确命名、主动 callback 和 receipt 门槛；
- 控制面使用 Git ref compare-and-swap 或事务写入器，原子更新权威 `tasks.yaml`；不得再引入第二个可变权威源；
- 消息 provider receipt、ack、重试和 dead-letter 完整接入；
- 自动 lease 续期、超时、孤儿任务检测和审计事件流；
- 强制 capability sandbox、短期凭据和审批校验；
- 自动 ancestry/path/contract/secret 检查；
- 指标、告警、恢复演练和灾难恢复备份；
- 主线保护、集成队列和发布流程与开发协议分离。

## 14. 从旧机制迁移

- [ ] 冻结自动派卡，盘点 Ready、活动、待验收和孤儿任务。
- [ ] 为任务、report、角色和状态增加对应的 `schema_version`。
- [ ] 给每张活动卡补 `revision`、`base_commit` 和依赖关系。
- [ ] 给每个 Active role 补稳定唯一的 `role_no`、`role_id`、`role_name` 和派生 `expected_thread_title`，计算规范标题并处理重复/漂移。
- [ ] 新建 JSON-compatible `ROLE-POLICIES.yaml`，由 Leader 确认 tier/risk floor、deliberation、能力与降级策略；本机新建空的 gitignored `model-bindings.yaml` 后再配置可用 binding。
- [ ] 未派发的旧卡通过新 revision 增加 `min_model_tier: inherit` 与 `required_model_capabilities: []`，并让 `task_card_commit` 同时包含岗位策略；不得原地改已冻结卡。
- [ ] 活动 dispatch/outbox 不得回写。若现用 provider/model 可证明，可写不可变 migration event 供 `--allow-legacy-model-evidence` 审计，但这不能成为 strict 证据；要恢复 strict 或自动验收，关闭旧 attempt 并在新 attempt 运行 selector、建立原子 event/outbox 后重派。
- [ ] 旧 report 不得伪造 executor 信息；未知值明确记为 unknown 并阻止自动验收，直至 PM 能从 provider receipt/运行记录证明或决定重派。
- [ ] 给每次活动执行分配递增的 `attempt`，旧 report 转为 attempt 记录。
- [ ] 将共享 checkout 拆为 PM worktree 与任务 worktrees。
- [ ] 明确 canonical state；把 BOARD/STATUS 的重复字段改为生成视图。
- [ ] 建立 PM lease、角色 lease、outbox 和 inbox。
- [ ] 建立 `routes/v2` 与 `transport-receipts/v1`；逐一用真实 provider read/list 核对 PM/Worker task、精确标题、host/worktree 和 callback route。不能验证的旧 ID 标 `stale`，不得伪造成 verified。
- [ ] 把 Accepted 与 Integrated 拆开，补 integration baseline。
- [ ] 把任意测试命令迁移为受控命令 ID。
- [ ] 给高风险任务补 capability、approval 和 rollback 信息。
- [ ] 启用 validator，但先只告警；清完存量错误后再阻断 CI。
- [ ] `--allow-legacy-model-evidence` 只用于旧终态迁移盘点；其结果不是 strict pass，不得驱动自动验收、自动派发或 CI 放行。
- [ ] 用 F01、F02、F03、F07、F10 做桌面恢复演练。
- [ ] 先启用 heartbeat 的只读审计，再开放幂等修复。
- [ ] 最后才逐级开放自动派发、自动验收准备和整合队列。

## 15. Validator / CI 检查列表

- [ ] frontmatter 可解析，枚举和 `schema_version` 合法。
- [ ] `task_id + revision + attempt`、dispatch ID、state event ID、outbox message ID、callback ID 分别唯一。
- [ ] role 存在、Active，且 card type 与角色能力相容。
- [ ] Active role 的 `role_no` / `role_id` / `role_name` 唯一完整；真实 PM/Worker tasks 独立可读，`actual_title == expected_title == role_no . role_name`。
- [ ] role policy 在 `task_card_commit` 可读；selector snapshot 恰好九字段，满足 tier、deliberation 与能力规则，并与账本/outbox/report 深度完全一致。
- [ ] 每个活动或 report-backed terminal dispatch 的 first-state commit 同时新增唯一匹配的 `TASK_DISPATCHED` event 与 `task.dispatch` outbox；outbox 外键、四元组和 raw-byte SHA-256 匹配，工作树证据未偏离 Git blob。
- [ ] `require_pm_approval` 降级有全仓唯一且位于 `granted_approval_ids` 的 ID、同 commit 或更早的范围/tier 匹配批准；approval epoch 不大于 dispatch epoch，每个 approval 最多一个严格后代 revocation，且撤销时间/epoch 不早于批准。
- [ ] 状态迁移来自允许边，写入者持有有效 PM epoch。
- [ ] task hash 与派发快照一致，旧 revision 不可进入评审。
- [ ] `base_commit`、`implementation_commit`、`report_commit` 在正确仓库中可解析。
- [ ] base 是 delivery head 的祖先，评审期间 head 未漂移。
- [ ] 依赖图无环，所有依赖均包含在下游 base 中。
- [ ] worktree、branch、thread 与 active attempt 一一绑定。
- [ ] Standard / Automated 的 route 为 verified；新可见任务有明确用户授权；每个发送的 dispatch 有以 `dispatch_id` 为键的有效 receipt。
- [ ] allowed/blocked paths 无冲突，实际 diff 未越界。
- [ ] 控制面文件只能由授权 PM/自动化身份修改。
- [ ] report 的 revision、attempt、role、base、head 与任务一致。
- [ ] 必跑检查均有证据，失败或跳过未被写成 passed。
- [ ] 公共契约、schema、migration 和 lockfile 变化已被识别。
- [ ] L2 以上任务存在有效 approval 与 rollback 记录。
- [ ] report、日志和 diff 未发现凭据或高敏信息。
- [ ] Accepted 但未 Integrated 的任务不会解锁依赖卡。
- [ ] 终态事件不会被重复回调再次迁移。
- [ ] Worker 完成通知有以 `callback_id` 为键且 `status: received` 的 callback receipt，目标等于 delegation source 与 verified PM route；heartbeat 没有冒充主动 callback。
- [ ] 派发前 `validate_runtime.py --project <repo> --role-id <role-id>` 通过；活动任务 `--check-active` 通过。
- [ ] 最终 `validate_project.py --project <repo> --require-runtime` 通过；repo strict 与 runtime strict 均通过。

bundled validator 会对账 event/outbox 的结构、Git 原子性、摘要、九字段 parity 和 approval linkage；它仍不证明真实 task/thread、精确标题、provider execution receipt、消息实际送达、callback receipt、全部 guards 的语义完整性、`CHECKS.yaml` 命令是否安全，或真实实现 diff 是否遵守路径/契约。上述完整 checklist 与 runtime adapter 检查仍需 PM 执行；缺 dispatcher 时只能说明仓库控制面有效，不能说明自动闭环成立。

## 16. 反模式

- 把 Markdown 多文件当作支持并发写的数据库。
- 多个角色共享同一 checkout，只靠“注意不要切分支”。
- 任务卡尚未提交就靠聊天全文派发。
- 用同一 PM 任务切换岗位人格、subagent 或只有 worktree 来冒充独立岗位任务。
- 没有用户明确授权就创建侧边栏可见 Codex 任务，或在未获授权时静默同任务执行。
- 让 Codex 自动标题保留为 `Await ...` / 任务描述，而不设置并验证 `<role_no> . <role_name>`。
- 用移动分支名代替 `base_commit` 或冻结的 `implementation_commit`。
- 把 callback 当完成，把 report 自述当证据。
- 把 `Accepted` 当成已合并、已部署或依赖可用。
- Heartbeat 发现缺消息就重跑任务。
- Worker 只在自己的任务输出“完成”，不调用跨任务发送；或用 heartbeat 扫到 report 冒充主动 callback。
- 只有 outbox/report/validator pass 就宣称已派发、已回调或闭环成立。
- 每次消息重试都创建新 dispatch 或新 attempt。
- 返工覆盖旧 report，导致历史证据消失。
- 测试失败后不断重跑，直到得到一次绿色结果。
- QA 自己写最终 Accepted，或 PM 跳过 QA 证据直接拍板。
- 开发角色修改任务卡、路由表或验收标准以适配自己的实现。
- 手算、改写 selector 输出，或在同一 dispatch 下暗中换模型/委派给更弱 subagent。
- 把高模型等级当作 Owner approval、生产权限、验收权或发布权。
- 自动执行 task 中的任意 shell 字符串。
- 在 report、日志、聊天或仓库中传生产密钥。
- 超时后立即删除 worktree，再尝试判断发生过什么。
- PM 双活时用“最后写入覆盖”代替 reconcile。
- 为了赶进度绕过 integration card 和 ancestry 校验。
- 自动合并、生产发布与普通开发派卡共用同一批准动作。

## 17. 最小恢复口令

当现场混乱、事实不一致或不知道是否应继续时，PM 统一执行：

> 冻结派发 → 获取唯一 lease → 保全分支与 artifact → 读取 canonical state/outbox/inbox → 对照消息、Git、report、worktree → 按 revision/attempt 去重 → 修复可证明状态 → 将不可证明项转人工决策 → 重新开放队列。

恢复的目标不是“尽快让所有状态变绿”，而是重新建立一条可验证、可追溯、可安全继续的事实链。
