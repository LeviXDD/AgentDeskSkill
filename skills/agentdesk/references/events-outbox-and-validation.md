# 事件、Outbox、消息与校验

本参考给出控制面事件、outbox 消息、本机运行时镜像、角色消息、状态提交顺序与校验不变量。任务账本、任务卡、交付报告和验收记录的字段与模板见 [数据模型、Schema 与模板](schemas-and-templates.md)。

## 目录

- [Event 与 outbox 模板](#7-event-与-outbox-模板)
- [本机 routes、transport receipts 与 timed PM lease 镜像](#8-本机-routestransport-receipts-与-timed-pm-lease-镜像)
- [消息模板](#9-消息模板)
- [状态与提交写入顺序](#10-状态与提交写入顺序)
- [校验不变量](#11-校验不变量)

## 7. Event 与 outbox 模板

两者不能混为一谈：event 记录**已经发生的状态迁移或批准/撤销事实**；outbox 记录 PM **准备发送、允许安全重放的消息意图**。它们都由持有有效 lease 的 PM 控制面写入。

### 7.1 状态迁移 event

文件：`docs/pm/events/EVT-YYYYMMDD-NNNN.yaml`

```yaml
schema_version: agentdesk.state-event/v2
event_id: EVT-20260712-0001
event_type: TASK_DISPATCHED
source_message_id: null
task_id: TC-031
revision: 2
attempt: 1
dispatch_id: DSP-TC031-R2-A1-7F3C
from_state: ready
to_state: dispatched
lease_epoch: 12
actor_role_id: PM
occurred_at: "2026-07-12T06:30:00Z"
evidence_refs:
  - docs/pm/tasks/TC-031-r2-runtime-api.md
guard_results:
  - guard: dependency_commit_ancestry
    inputs:
      required_commit: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      base_commit: "0123456789abcdef0123456789abcdef01234567"
    result: passed
    checked_at: "2026-07-12T06:29:58Z"
    evidence_ref: "git-merge-base-is-ancestor:exit-0"
payload_digest: "sha256:<64-lowercase-hex>"
```

每次状态迁移的 event、更新后的 `state/tasks.yaml`、生成视图以及必要的验收记录必须进入同一个 Git commit。event 只新增，不修改。

对派发，`payload_digest` 不是格式占位符：先完成不可变 outbox 文件，再对其**仓库中的 UTF-8 原始内容（LF bytes，包含文件实际末尾换行）**计算 SHA-256，并写成 `sha256:<hex>`。strict validator 会读取 Git blob 的真实字节重新计算并比较。

`current_dispatch` 首次出现在 `state/tasks.yaml` 的 Git commit 是该 dispatch 的 first-state commit。这个 commit 必须同时新增且只匹配一份 `TASK_DISPATCHED` event 和一份 `task.dispatch` outbox；二者与账本的 `task_id + revision + attempt + dispatch_id` 完全相同，且 outbox `event_id` 外键指向该 event。strict 模式从 Git 历史证明“同 commit 新增”，不是只检查当前文件看起来匹配。

角色回调携带自己的 `callback_id` 作为消息幂等键。PM 因该回调产生状态迁移时，创建新的控制面 `event_id`，并把回调 ID 写入 `source_message_id`；二者不能混用。PM 出站 outbox 则使用独立的 `message_id`。每个 `event_id` 与 `message_id` 在全仓库各自唯一；文件名重复、不同文件复用同一 ID，或同一 ID 指向不同 payload 都是错误。

`guard_results` 用于机器重建调度依据。进入 Ready 或 Dispatched 的 event 至少记录实际执行过的 dependency、WIP、conflict surface、base commit 与 approval guards；每项都要有结构化输入、结果、时间和证据引用。

核心事件采用 [核心协议与状态机](protocol.md) 的事件名，例如：`TASK_SPECIFIED`、`TASK_DISPATCHED`、`DISPATCH_ACKNOWLEDGED`、`DELIVERY_SUBMITTED`、`DELIVERY_ACCEPTED`、`DELIVERY_RETURNED`、`TASK_BLOCKED`、`BLOCKER_RESOLVED`、`CHANGE_INTEGRATED`。

#### `require_pm_approval` 的模型降级批准

`model_degradation_approval_id` 非空不等于已批准。它必须引用如下 append-only event：

```yaml
schema_version: agentdesk.state-event/v2
event_id: EVT-20260712-0000
event_type: MODEL_DEGRADATION_APPROVED
purpose: model_degradation
approval_id: APR-TC031-R2-A1-0001
task_id: TC-031
revision: 2
attempt: 1
approver_role_id: PM
lease_epoch: 12
approved_selected_tier: advanced
preferred_model_tier: expert
granted_at: "2026-07-12T06:28:00Z"
expires_at: null
revoked_at: null
reason: "Preferred expert binding unavailable; approved advanced tier for this attempt only."
```

批准 event 必须在 dispatch first-state commit 中新增，或已存在于其祖先 commit；`event_id` 使用唯一 `EVT-*`，`approval_id` 使用全仓唯一的非空 `APR-*`，`reason` 非空。selection 中非空 approval ID 必须同时出现在该 task 的 `granted_approval_ids`，且 approval ID、task/revision/attempt、`approved_selected_tier` 和 `preferred_model_tier` 与账本、冻结策略及 selector snapshot 匹配；`approver_role_id` 必须为 `PM`。`granted_at` 不晚于 dispatch；`expires_at` 只能为 `null` 或严格晚于 dispatch；批准原记录的 `revoked_at` 永远为 `null`。`lease_epoch` 必须是整数且不小于 1；approval 与 dispatch 同 commit 时二者 epoch 相同，更早授权 commit 必须是 dispatch commit 的祖先且 approval epoch 不大于 dispatch epoch。该批准只允许本 attempt 在仍满足硬下限时从 preferred tier 降到所列 tier，不授权其他 capability 或外部动作。

批准 event 不可修改。撤销时另增独立 event：

```yaml
schema_version: agentdesk.state-event/v2
event_id: EVT-20260712-0002
event_type: MODEL_DEGRADATION_REVOKED
approval_id: APR-TC031-R2-A1-0001
task_id: TC-031
revision: 2
attempt: 1
actor_role_id: PM
lease_epoch: 13
revoked_at: "2026-07-12T07:00:00Z"
reason: "Preferred binding restored; degraded execution is no longer authorized."
```

revocation 的 `event_id` 唯一，approval/task/revision/attempt 必须指向原批准，`actor_role_id` 必须为 `PM`，`lease_epoch` 为整数且不小于 1，`revoked_at` 与 `reason` 必填。同一 approval 最多有一个 revocation event；传输重试复用原 event ID。revocation commit 必须是 approval commit 的严格后代，`revoked_at >= granted_at`，且 revocation epoch 不小于 approval epoch。派发前或派发时已存在有效撤销，则批准无效；执行中撤销时停止该执行并进入普通恢复流程，禁止继续或重放；交付完成后才撤销只影响未来使用，不反向改写批准、派发或已完成证据。

当 `integrated_commit` 不保留 `accepted_commit` 的祖先关系时（例如 cherry-pick），`CHANGE_INTEGRATED` event 还必须加入以下顶层机器字段；内置 validator 只接受已与账本同 commit 提交、且字段完全匹配的证据：

```yaml
accepted_commit: "<reviewed-source-sha>"
integrated_commit: "<target-baseline-sha>"
equivalence_method: patch_id # patch_id | tree | approved_mapping
equivalence_result: passed
equivalence_evidence_ref: "<immutable-check-output-or-approved-decision-ref>"
```

`equivalence_evidence_ref` 不是一句主观结论；它必须定位到可复核的 patch-id、tree comparison、检查输出或 Owner 批准的映射记录。

### 7.2 待发送 outbox 消息

文件：`docs/pm/outbox/MSG-YYYYMMDD-NNNN.yaml`

```yaml
schema_version: agentdesk.outbox-message/v2
message_id: MSG-20260712-0001
event_id: EVT-20260712-0001
message_type: task.dispatch
dedupe_key: TC-031/r2/a1/DSP-TC031-R2-A1-7F3C/task.dispatch
task_id: TC-031
revision: 2
attempt: 1
dispatch_id: DSP-TC031-R2-A1-7F3C
destination_role_id: R3
created_at: "2026-07-12T06:30:00Z"
model_selection:
  required_model_tier: advanced
  required_model_capabilities: [coding, testing]
  model_binding_id: local-coder-advanced
  selected_model_provider: "<provider>"
  selected_model_id: "<stable-model-revision>"
  selected_model_tier: advanced
  selected_deliberation_tier: balanced
  selected_model_capabilities: [coding, testing]
  model_degradation_approval_id: APR-TC031-R2-A1-0001
payload:
  task_path: docs/pm/tasks/TC-031-r2-runtime-api.md
  task_card_commit: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  base_commit: "0123456789abcdef0123456789abcdef01234567"
  branch: feat/tc-031-runtime-api
  report_path: docs/pm/reports/TC-031-r2-a1.md
```

规则：

- outbox 文件不可变；重试沿用同一 `message_id`、`event_id`、`dispatch_id` 和 `dedupe_key`。
- `destination_role_id` 是逻辑角色，发送时才从本机 routes 解析 thread ID。
- Standard / Automated 发送前要求 destination role 的真实 task/thread、精确标题、host/worktree 与 PM callback route 均为 `verified`；占位 ID、仅有 worktree 或同任务切岗不满足。
- 传输 receipt、重试计时器和“已发送”游标属于 `.agentdesk/runtime/transport-receipts.yaml`，不回写 outbox。outbox 存在只证明发送意图，不证明真实送达。
- 角色回调通过消息模板发送；PM 校验后将其作为证据写入新的状态 event。角色不直接写控制面 event/outbox。
- 旧 revision、旧 dispatch 或旧 attempt 的消息只记审计，不改变当前状态。
- `event_id` 必须指向 first-state commit 中匹配的 `TASK_DISPATCHED` event；task/revision/attempt/dispatch 四元组也必须一致。
- `model_selection` 的键集合必须恰好是 selector 的九个固定字段，并与 `current_dispatch.model_selection` 深度完全相等（包括数组值和顺序）；不得缺键、加键、手算、重命名或静默改写。同一 dispatch 的重放完全复用该对象。
- provider 使用移动 alias 时，在 provider 支持的范围内把它解析为稳定 model revision 后再写入 outbox 与 report。
- `TASK_DISPATCHED.payload_digest` 必须等于该 outbox Git blob 的实际 UTF-8/LF 字节 SHA-256；只写合法格式但摘要不匹配仍失败。

任务进入 `returned`、`accepted`、`integrated`，或进入已有 report 的 `cancelled` / `superseded` 后会清空 `current_dispatch`。此时 strict validator 从 report 的 `dispatch_id` 与九字段 `executor_model` 反向定位原 event/outbox 并继续对账；event/outbox 的工作树副本必须存在，且与原始 Git blob 字节不变。

## 8. 本机 routes、transport receipts 与 timed PM lease 镜像

`.agentdesk/runtime/routes.yaml`：

```json
{
  "schema_version": "agentdesk.routes/v2",
  "updated_at": "2026-07-12T06:00:00Z",
  "routes": {
    "PM": {
      "role_no": "PM",
      "role_name": "项目经理",
      "expected_title": "PM . 项目经理",
      "transport": "codex",
      "host_id": "local-host-01",
      "thread_id": "<real-pm-thread-id>",
      "actual_title": "PM . 项目经理",
      "worktree": "<absolute-local-path>",
      "status": "verified",
      "verified_at": "2026-07-12T06:00:00Z"
    },
    "R3": {
      "role_no": "R3",
      "role_name": "QA 工程师",
      "expected_title": "R3 . QA 工程师",
      "transport": "codex",
      "host_id": "local-host-01",
      "thread_id": "<real-worker-thread-id>",
      "actual_title": "R3 . QA 工程师",
      "worktree": "<absolute-local-path>",
      "status": "verified",
      "verified_at": "2026-07-12T06:00:00Z"
    }
  }
}
```

route key 使用 `role_id`。每个条目必须逐字段存在；`status` 只允许 `unbound`、`verified`、`stale`。只有真实 task/thread 可读取、`actual_title == expected_title == "<role_no> . <role_name>"`、host/worktree 可达且 PM/Worker thread 不同，才可写 `verified`。任何漂移立即标 `stale`。`role_no` 与 `role_name` 来自 `ROLES.md`，不能由 thread 自动标题反向推导。

`.agentdesk/runtime/transport-receipts.yaml`：

```json
{
  "schema_version": "agentdesk.transport-receipts/v1",
  "updated_at": "2026-07-12T06:10:00Z",
  "dispatch_receipts": {
    "DSP-TC031-R2-A1-7F3C": {
      "message_id": "MSG-20260712-0001",
      "dispatch_id": "DSP-TC031-R2-A1-7F3C",
      "role_id": "R3",
      "source_thread_id": "<real-pm-thread-id>",
      "destination_thread_id": "<real-worker-thread-id>",
      "status": "sent",
      "provider_receipt": "<provider-result-or-null>",
      "sent_at": "2026-07-12T06:05:00Z",
      "acknowledged_at": null
    }
  },
  "callback_receipts": {
    "CB-TC031-R2-A1-2D91": {
      "callback_id": "CB-TC031-R2-A1-2D91",
      "dispatch_id": "DSP-TC031-R2-A1-7F3C",
      "source_role_id": "R3",
      "destination_role_id": "PM",
      "source_thread_id": "<real-worker-thread-id>",
      "destination_thread_id": "<real-pm-thread-id>",
      "status": "received",
      "provider_receipt": "<provider-result-or-null>",
      "received_at": "2026-07-12T06:10:00Z"
    }
  }
}
```

两个映射分别以 `dispatch_id` 和 `callback_id` 为键。Dispatch receipt 必须恰有 `message_id`、`dispatch_id`、`role_id`、`source_thread_id`、`destination_thread_id`、`status`、`provider_receipt`、`sent_at`、`acknowledged_at`；状态仅为 `sent` 或 `acknowledged`。Callback receipt 必须恰有 `callback_id`、`dispatch_id`、`source_role_id`、`destination_role_id`、`source_thread_id`、`destination_thread_id`、`status: received`、`provider_receipt`、`received_at`。失败发送不是 receipt，不能伪装成成功条目。transport 重试复用原键和业务 payload。callback 目标优先使用 Worker 收到派卡时的 delegation `source_thread_id`，并且必须与 verified PM route 相同。完整 Codex 操作顺序见 [Codex 任务运行时适配器](codex-runtime-adapter.md)。

`.agentdesk/runtime/pm-lease.yaml`（Standard / Automated 的本机租约镜像）：

```yaml
schema_version: agentdesk.pm-lease/v1
lease_id: "<random-unique-id>"
holder_id: pm-session-20260712-01
holder_instance_id: "<local-pm-instance-id>"
lease_epoch: 12
last_snapshot_commit: "<control-plane-git-sha>"
acquired_at: "2026-07-12T06:00:00Z"
heartbeat_at: "2026-07-12T06:00:20Z"
expires_at: "2026-07-12T06:01:20Z"
```

`.agentdesk/runtime/model-bindings.yaml`（本机可用模型目录，JSON-compatible YAML）：

```json
{
  "schema_version": "agentdesk.model-bindings/v1",
  "updated_at": "2026-07-12T06:00:00Z",
  "bindings": {
    "local-coder-advanced": {
      "provider": "<provider>", "model_id": "<model-id-or-stable-revision>",
      "tier": "advanced", "deliberation_tier": "balanced",
      "capabilities": ["coding", "testing"], "enabled": true
    }
  }
}
```

binding 只保存非秘密的本机选择元数据；provider credential、token、session ID 和连接信息不得写入其中。runtime adapter 把厂商无关的 `deliberation_tier` 映射为 provider 专属推理参数。

bundled selector/validator 不会启动模型；dispatcher 必须能把 binding 映射为真实 provider 调用并核对实际 executor。若编排平台不能固定指定 provider/model/deliberation，或不能提供受信的 adapter/provider execution receipt，则 selection 与 report executor 只是审计声明，不得作为已执行路由的独立证明。

`.gitignore` 必须包含：

```gitignore
.agentdesk/runtime/
```

thread ID、会话 ID、本机绝对 worktree、transport receipt、传输 token、lease ID、holder instance ID 和 heartbeat 都是运行时标识，禁止写入任务卡、账本、报告、验收记录和 Git 历史。逻辑 `holder_id` 与单调递增的 `lease_epoch` 例外：它们属于控制面审计事实，必须进入权威快照和状态 event。

## 9. 消息模板

### 9.1 派卡

```text
【派卡 · TC-XXX · rN · attempt N】
岗位：<role_no> . <role_name>（role_id=<role_id>）
dispatch_id：<dispatch-id>
message_id：<message-id>
source_event_id：<TASK_DISPATCHED-event-id>
PM callback source_thread_id：<runtime-only-source-thread-id>
任务卡：docs/pm/tasks/TC-XXX-rN-<slug>.md
任务卡提交：<task_card_commit>
基线：<base_commit>
分支：<branch>
报告路径：docs/pm/reports/TC-XXX-rN-aN.md
模型要求：tier=<required-tier>；deliberation=<required-deliberation-tier>；capabilities=<required-capabilities>
执行模型：binding=<binding-id>；provider=<provider>；model=<stable-model-revision>；tier=<selected-tier>；deliberation=<selected-deliberation-tier>；capabilities=<selected-capabilities>；degradation_approval=<id-or-null>
请只读取任务卡的必需上下文并遵守 allowed/blocked paths。
先提交实现，再提交 report；完成或受阻后必须用跨任务发送工具按“交付回调”模板通知上述 PM source task，并记录 callback receipt；只在岗位任务内输出最终答复不算通知。
回调必须原样携带 task_id、revision、dispatch_id 和 attempt。
```

消息中的 `required-deliberation-tier` 只是从 `task_card_commit` 的 frozen role policy 派生的 display-only 提示，不是 selection snapshot 的第十个字段；权威 snapshot 仍只有 outbox/账本中的九字段。

### 9.2 交付回调

```text
【回报 · TC-XXX · rN · attempt N】
岗位：<role_no> . <role_name>（role_id=<role_id>）
状态：completed | partial | blocked
dispatch_id：<dispatch-id>
callback_id：<callback-id>
implementation_commit：<code-sha-or-null>
report_commit：<report-sha>
report：docs/pm/reports/TC-XXX-rN-aN.md
executor_model：<必须与派发快照一致；详见 report>
blocked_reason：<none-or-reason>
需要 PM：验收 | 澄清 | 决策
```

### 9.3 返工

```text
【返工 · TC-XXX · rN · next attempt N】
前次 dispatch_id：<old-dispatch-id>
验收记录：docs/pm/acceptances/TC-XXX-rN-aN-reviewN.md
必须修复：
1. <可验证的问题与期望结果>
2. <可验证的问题与期望结果>
规格未变化，revision 保持不变；PM 重新派发时会提供新的 dispatch_id。
若目标、范围、依赖或基线需要变化，请停止并请求 PM 创建 rN+1。
```

## 10. 状态与提交写入顺序

控制面修改采用两阶段校验：先生成 `BOARD.md` / `STATUS.md` 并运行 `validate_project.py --pre-commit`。该模式只检查拟提交对象的结构与当前可见交叉引用，并明确 warning：在文件尚未提交时，它不能证明 first-state commit 原子性、Git 新增关系或 immutable blob。随后把 event、账本、视图、必要的 outbox/approval/acceptance 作为同一个控制面 commit 提交。提交后再运行不带 `--pre-commit` 且不带 `--allow-legacy-model-evidence` 的 strict validator 与 `render_views.py --check`；strict 才从 Git 历史证明 dispatch 首次出现与 event/outbox 同 commit 新增、摘要/模型/批准匹配，并逐文件比较证据的工作树副本与 immutable Git blob。不能用忽略规则或 `skip-worktree` 绕过。未形成并通过这个 commit 时不得宣称迁移完成；strict 通过也不证明真实 task、标题、route 或 transport receipt，Standard / Automated 还必须执行 runtime adapter 检查。

### 派发事务

PM 必须持有逻辑 lease；`mode: timed` 时还要验证本机/协调器 lease 未过期。随后校验依赖、冲突面、owner approval，并在状态迁移前验证 PM/岗位真实 task、精确标题、host、独立 worktree、single-flight 和 callback route；新建 Codex 可见任务还必须有显式用户授权。分配 attempt/dispatch ID，并使用完整 `task_card_commit` 运行确定性 `scripts/select_model.py`。若 `require_pm_approval` 发生降级，先生成匹配 approval event（可与派发同 commit）；再将 selector 的九字段 JSON 原样复制进账本和 outbox。finalize outbox 后计算其 raw-byte digest，写 `TASK_DISPATCHED` event，运行 pre-commit 结构检查，并把首次 `current_dispatch`、event、outbox、账本和视图一并提交。strict 通过后才向 verified thread 投递并写 dispatch receipt；投递失败只重放同一 outbox，不生成新 attempt，也不进入 `InProgress`。

一次 dispatch 只冻结一个 primary execution binding。未经声明不得把实质工作委派给更弱或未记录的子 Agent；承担实质性目标、改动或验收证据的 subagent 必须获得自己的 dispatch/execution evidence。仅做非实质机械辅助且不影响结论的工具调用不产生第二权威执行者。

### 交付入账

PM 校验 `revision + dispatch_id + attempt`、callback receipt 与来源/目标 route，从 `report_commit` 读取报告，核对 Git 对象、`implementation_commit` 及 executor model 与派发快照；通过后写入两个 SHA 和 `delivered_at`，状态改为 `review_ready`。回调本身不等于验收。若 durable report 存在但 receipt 缺失，按漏回调恢复，不得伪造 receipt；PM 可以基于 Git 证据审查，但必须报告主动通知链路失效。

### 验收与集成

PM 按 base commit 审查 diff、源码、检查结果和范围并写 acceptance record。通过时写 `accepted_commit`；共享基线真正包含成果后才写 `integrated_commit`。后继任务仅在依赖要求达到后可派发。

## 11. 校验不变量

实现校验器时至少检查以下规则：

1. `schema_version` 必须匹配文件类型，未知主版本立即失败。
2. `task_id` 在账本中唯一；revision 为大于等于 1 的整数。
3. `role_id` 必须已启用；每个 Active role 有唯一非空 `role_no`、`role_id`、`role_name`，且派生 `expected_thread_title` 精确等于 `<role_no> . <role_name>`。QA 是合法角色，不得用“你不是 QA”覆盖岗位职责。`type` 描述成果，`role_id` 描述责任主体，二者不做僵硬绑定。
4. 依赖必须引用存在的任务、不得自依赖、不得形成环。
5. `ready` / `dispatched` 必须满足全部依赖；默认要求 `integrated`。使用 `accepted` 例外时必须有 `required_commit`，且它等于前置任务当前 accepted commit，并且是本卡 `base_commit` 的祖先；校验证据必须进入状态 event 的 `guard_results`。
6. `unlocks` 是 `depends_on` 的反向派生值，禁止存入任务记录。
7. `task_card_commit`、`base_commit` 和四类结果 commit 必须是完整 Git SHA；同一 revision 的任务卡与基线不可变。
8. `blocked_paths` 优先于 `allowed_paths`；除当前派发的精确 `report_path` 例外外，实际 diff 不得命中 blocked paths。
9. `conflict_surfaces` 相交的活动任务默认不得并行，除非 PM 在决策记录中批准。
10. `required_checks` 只能引用 `CHECKS.yaml` 中受控的 `check_id`，不得自动执行任务文本中的任意 shell 字符串。
11. 每个 Active role 在 `task_card_commit` 的 `ROLE-POLICIES.yaml` 中存在；模型等级、deliberation 和能力硬要求计算正确，selector 的恰好九字段在 current dispatch/outbox 完全相等。
12. 每个活动 attempt 只有一个按 `task_id + revision + attempt` 推导的 `report_path`；它是角色对控制面目录的唯一写入例外，且不得覆盖旧报告。
13. `event_id`、`message_id`、`dispatch_id` 在各自命名空间唯一，attempt 只能递增；消息重放不得递增。新 revision 的 draft/首次 ready 从 attempt 0 开始；返工回到 ready 时保留已使用的最大 attempt，但活动 `dispatch_id` 必须清空，下一次派发再递增。
14. `current_dispatch` 在 dispatched / in_progress / review_ready 时必填；draft / ready / returned / accepted / integrated / cancelled / superseded 时不得保留活动派发。blocked 仅在 `blocked_attempt_valid: true` 时可保留。
15. `review_ready` 必须来自 `delivery_status: completed`，并具有 implementation_commit、report_commit、delivered_at；两个提交不得自引用。
16. `accepted` 必须有 accepted_commit、acceptance_path、不可变 acceptance record、accepted_at；代码变化后必须重新交付。
17. `integrated` 必须有 accepted_commit、integrated_commit、integrated_at；accepted commit 不是 integrated commit 祖先时还必须有等价性证据。
18. `blocked` 必须有 blocked_reason、blocked_kind、blocked_owner、unblock_condition、review_after、blocked_attempt_valid、resume_state 和 blocked_at；解除阻塞只能由 PM 执行，并在重新校验前置条件后清空活动阻塞字段。
19. 时间戳使用 UTC RFC 3339 且单调不减；任务卡只定义 owner approval 的 gate、capability 和 approver，当前有效授权只写入账本 `granted_approval_ids`。无证据时不得越过指定节点。
20. 状态迁移符合 [核心协议与状态机](protocol.md)，不得跳过 `review_ready` 直接 accepted。
21. runtime 标识不得进入已跟踪文件；每次写账本都须持有效逻辑 PM lease、携带当前 `lease_epoch` 并使用 compare-and-swap / 原子替换。
22. 每个 active 或 report-backed terminal dispatch 都能追溯到 first-state commit 同时新增的唯一 `TASK_DISPATCHED` event/outbox；outbox 外键、四元组、九字段 snapshot 与 raw-byte digest 全部匹配，工作树证据未偏离 Git blob。
23. `require_pm_approval` 降级有同 commit 或更早、范围匹配、未过期且派发前/时未撤销的唯一 approval；selection ID 同时在 `granted_approval_ids`。approval epoch 不大于 dispatch epoch；每个 approval 最多一个严格后代 revocation，且撤销时间/epoch 不早于批准。
24. report executor model 与派发快照完全一致；一个 dispatch 只有一个 primary binding，实质 subagent 有独立 dispatch/evidence；模型等级不授予或替代 approval/capability 权限。
25. Standard / Automated 的每个活动 dispatch 有 distinct verified PM/Worker route，实际标题精确匹配，host/worktree 可达，并有真实 `dispatch_receipt`；placeholder thread ID 或只有 outbox 不算投递。
26. Worker 的完成通知有匹配 `callback_id + dispatch_id` 的 receipt；delegation source、destination 与 verified PM route 一致。只有 final answer/report 不算 callback，heartbeat 不能替代它。
27. 任一校验失败时保留原状态，新增错误 event 并按 [运行手册与故障恢复](runbooks-and-recovery.md) 处理。

`--allow-legacy-model-evidence` 仅用于盘点 legacy terminal record 的迁移审计；即使退出码为零也不是 strict pass，不能用于自动验收、自动派发或 CI 放行。正常 strict 必须不带此参数。

bundled validator 会实际对账上述 event/outbox Git 证据，但不证明真实 Codex task/thread、精确标题、provider execution receipt、消息送达或 callback receipt，不判断所有 `guard_results` 是否语义完整，也不替代对 `CHECKS.yaml` 安全性与真实实现 diff/路径范围的 PM/CI 审查。缺少 runtime dispatcher/adapter 或独立运行时检查时，不得因 bundled validator 通过而宣称闭环成立。

### 完成标准

任意新 PM 只读 `tasks.yaml`、当前 revision 任务卡和对应交付证据，就能判断任务现在在哪里、依据哪个提交验收、为何可或不可继续推进；无需依赖旧聊天记录。
