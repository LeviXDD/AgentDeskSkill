# 数据模型、Schema 与模板

本参考给出任务账本、任务卡、交付报告与验收记录的规范格式。事件、outbox、消息、本机运行时文件和校验规则见 [事件、Outbox 与校验](events-outbox-and-validation.md)。创建或校验项目文件时，应复用这里的字段与不变量。

## 目录

- [权威性与写入权](#1-权威性与写入权)
- [推荐目录](#2-推荐目录)
- [岗位身份字段](#21-岗位身份字段)
- [权威任务账本](#3-权威任务账本tasksyaml)
- [岗位模型策略与绑定](#34-岗位模型策略与绑定)
- [版本化任务卡模板](#4-版本化任务卡模板)
- [Delivery report 模板](#5-delivery-report-模板)
- [Acceptance record 模板](#6-acceptance-record-模板)
- [事件、outbox 与校验](events-outbox-and-validation.md)

本参考解决的问题：用一份机器可校验的任务账本承载当前状态，用版本化任务卡定义工作，用交付报告、验收记录和事件保存证据。聊天只传通知，不保存项目真相。

## 1. 权威性与写入权

### 1.1 唯一当前状态

`docs/pm/state/tasks.yaml` 是**编排状态的唯一权威来源**。只有当前逻辑 PM lease 的持有者可以修改它；STATUS、BOARD 和仪表盘只能由它与被引用的版本化任务卡生成，不能反向编辑。

- 任务卡定义某个 revision；report 声明交付；acceptance record 记录审查，三者都不保存当前状态。
- event/outbox 只负责通知与审计，不能覆盖账本。
- Git 对象保存实际代码真相；账本中的 SHA 必须能在 Git 中解析。

### 1.2 写入责任表

| 文件 | 写入者 | 更新方式 | 是否权威 |
| --- | --- | --- | --- |
| `state/tasks.yaml` | 当前 PM lease holder | 原子更新 | 当前编排状态唯一权威 |
| `tasks/TC-XXX-rN-*.md` | PM | 派发后不可变 | 该 revision 的规格权威 |
| `reports/TC-XXX-rN-aN.md` | 指派角色 | 每个 attempt 新增并提交 | 交付声明与自检证据 |
| `acceptances/TC-XXX-rN-aN-reviewN.md` | PM | 每次审查新增且不可变 | 审查证据 |
| `events/*.yaml` | 当前 PM lease holder | 每个迁移、批准或撤销事实新增且不可变 | 控制面审计与重建依据 |
| `outbox/*.yaml` | 当前 PM lease holder | 与派发迁移同时新增 | 待发送消息意图，非当前状态权威 |
| `ROLE-POLICIES.yaml` | Project Leader；PM 按授权提交 | 策略变更时版本化提交 | 岗位模型要求权威，不含厂商绑定 |
| `STATUS.md`、`BOARD.md` | 生成器 | 从账本全量重建 | 人类视图，非权威 |
| `.agentdesk/runtime/*.yaml` | 本机运行时 | 就地更新 | 本机真实任务路由、transport receipts 与 lease，禁止提交 |

### 冲突裁决

当前状态冲突时以 `tasks.yaml` 为准；工作范围冲突时以对应 revision 的任务卡为准；代码内容冲突时以账本记录的 Git SHA 为准。聊天消息只能触发复核，不能裁决冲突。

## 2. 推荐目录

```text
AGENTS.md
docs/
  pm/
    state/
      tasks.yaml                  # 唯一当前状态账本
    tasks/
      TC-031-r1-runtime-api.md    # revision 1，不可变
      TC-031-r2-runtime-api.md    # 规格变化后新建
    reports/
      TC-031-r2-a1.md             # revision 2 / attempt 1 的交付报告
    acceptances/
      TC-031-r2-a1-review1.md     # attempt 1 的第一次审查
    events/
      EVT-20260712-0001.yaml      # append-only 状态迁移事件
    outbox/
      MSG-20260712-0001.yaml      # PM 待投递消息，可安全重放
    STATUS.md                     # 从 tasks.yaml 生成
    BOARD.md                      # 从 tasks.yaml 生成
    ROLES.md                      # 项目启用的逻辑角色
    ROLE-POLICIES.yaml            # 厂商无关的岗位模型策略
    CHECKS.yaml                   # 允许自动运行的命令目录
    DECISIONS.md
    PM-PLAYBOOK.md
.agentdesk/
  runtime/
    routes.yaml                   # routes/v2：真实 task/thread、精确标题、host、worktree
    model-bindings.yaml           # 本机 provider/model 能力映射，禁止提交
    pm-lease.yaml                 # timed 模式的本机 PM lease 镜像
    inbox/                        # 尚未入账的回调与去重游标
    transport-receipts.yaml       # dispatch/callback 真实 transport receipts
.gitignore
```

命名规则：

- `task_id` 使用稳定的 `TC-NNN`，永不复用。
- 每个 Active role 同时有唯一 `role_no`、唯一 `role_id` 和非空 `role_name`；真实任务标题固定为 `<role_no> . <role_name>`。
- `revision` 从 `1` 开始；规格发生实质变化才递增。
- `attempt` 从 `1` 开始；同一 revision 的重新派发只递增 attempt。
- report 文件的 `aN` 对应 attempt；acceptance 文件再以 `reviewN` 区分同一 attempt 的审查记录。
- 仓库内路径一律使用相对路径和 `/`，不写本机绝对路径。

### 2.1 岗位身份字段

`docs/pm/ROLES.md` 是项目启用岗位的稳定身份来源。每个 Active row 至少包含：

| 字段 | 规则 | 用途 |
| --- | --- | --- |
| `role_no` | 全项目稳定唯一，例如 `R1`、`R3A`、`01` | 人类排序、沟通与真实任务命名 |
| `role_id` | 全项目稳定唯一，例如 `BE`、`FE`、`QA` | 任务卡、账本、outbox 的逻辑外键 |
| `role_name` | 非空稳定名称，例如 `后端工程师` | 规范任务标题与岗位说明 |
| `expected_thread_title` | 必须逐字等于 `<role_no> . <role_name>` | 可机器校验的真实任务标题 |
| `status` | `Proposed`、`Active`、`Paused`、`Retired` | 只有 Active 可接新派发 |

规范标题按 `role_no + " . " + role_name` 确定，`expected_thread_title` 只是该公式的显式派生列，不能自由填写，也不能从运行时自动标题反向推导。`role_id` 进入任务卡后不得静默改义；改名或拆岗要记录决策并重新验证 runtime route。Standard / Automated 的 route 使用 `agentdesk.routes/v2`，其 `role_no`、`role_name` 和 expected title 必须与这里相等；完整 runtime schema 见 [事件、Outbox 与校验](events-outbox-and-validation.md)。

## 3. 权威任务账本：`tasks.yaml`

本 Skill 的零依赖脚本要求 `tasks.yaml` 保持 **JSON-compatible YAML**：JSON 本身是合法 YAML，同时可由 Python 标准库确定性解析。初始化器已生成这种格式。若项目改用普通 YAML 语法，必须自行提供等价解析器，内置 `validate_project.py` 与 `render_views.py` 会明确报错而不会猜测。

```yaml
schema_version: agentdesk.tasks/v2
project_id: example-project
adoption_level: standard
updated_at: "2026-07-12T06:30:00Z"
pm_control:
  holder_id: pm-session-20260712-01
  lease_epoch: 12
  mode: timed
tasks:
  - task_id: TC-031
    revision: 2
    task_card_path: docs/pm/tasks/TC-031-r2-runtime-api.md
    task_card_commit: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    state: dispatched
    attempt: 1
    current_dispatch:
      dispatch_id: DSP-TC031-R2-A1-7F3C
      role_id: R3
      base_commit: "0123456789abcdef0123456789abcdef01234567"
      branch: feat/tc-031-runtime-api
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
    report_path: docs/pm/reports/TC-031-r2-a1.md
    granted_approval_ids: [APR-TC031-R2-A1-0001]
    delivery_state: none
    integration_state: pending
    implementation_commit: null
    report_commit: null
    accepted_commit: null
    acceptance_path: null
    integrated_commit: null
    blocked_reason: null
    blocked_kind: null
    blocked_owner: null
    unblock_condition: null
    review_after: null
    blocked_attempt_valid: null
    resume_state: null
    timestamps:
      created_at: "2026-07-10T04:00:00Z"
      ready_at: "2026-07-12T05:50:00Z"
      dispatched_at: "2026-07-12T06:30:00Z"
      started_at: null
      delivered_at: null
      blocked_at: null
      accepted_at: null
      integrated_at: null
      updated_at: "2026-07-12T06:30:00Z"
```

账本不重复保存目标、验收标准、依赖、路径边界、风险和检查目录；这些只存在于被 `task_card_commit` 固定的任务卡中。`current_dispatch` 仅保存当前 attempt 的逻辑路由、冻结基线与九字段模型快照，并且必须与任务卡及 outbox 一致。这样“当前状态”和“工作规格”各有唯一权威，不会形成两份可独立修改的真相。

每个 `current_dispatch` 都必须追溯到它首次出现在 `tasks.yaml` 的 commit；该 commit 同时新增匹配 task/revision/attempt/dispatch 的 `TASK_DISPATCHED` event 与 `task.dispatch` outbox。strict 校验要求两份证据的工作树副本仍存在、与各自 Git blob 完全相同，并且 outbox 的九字段 `model_selection` 与上例账本逐字段相等。

`returned`、`accepted`、`integrated`，以及已有 report 的 `cancelled` / `superseded` 虽然清空 `current_dispatch`，仍由 report frontmatter 的 `dispatch_id` 和 `executor_model` 追溯并核对原 dispatch event/outbox；终态不能只靠账本或 report 自述。

### 3.1 账本与任务卡共用的关键枚举

| 字段 | 允许值 / 规则 |
| --- | --- |
| `schema_version` | 固定为 `agentdesk.tasks/v2` |
| `pm_control.mode` | `manual` 或 `timed`；manual 用于 Lite 人工接力棒，timed 的到期时间只保存在运行时 lease 中，避免频繁改仓库 |
| `task_card_commit` | 包含当前 revision 任务卡的不可变完整 Git SHA |
| `type` | `implementation`、`qa`、`integration`、`review`、`architecture`、`docs`、`ops`、`spike` |
| `role_id` | `ROLES.md` 中状态为 Active 且同时具备唯一 `role_no` / `role_name` 的逻辑角色 ID；`PM` 只用于 PM 工作卡 |
| `state` | `draft`、`ready`、`dispatched`、`in_progress`、`review_ready`、`returned`、`blocked`、`accepted`、`integrated`、`cancelled`、`superseded` |
| `priority` | `P0`、`P1`、`P2`、`P3` |
| `risk` | `L0`、`L1`、`L2`、`L3`、`L4`，含义见 [运行手册与故障恢复](runbooks-and-recovery.md) |
| `min_model_tier` | `inherit`、`basic`、`standard`、`advanced`、`expert`；只能抬高岗位/风险硬下限 |
| `required_model_capabilities` | 字符串数组；与岗位能力做硬并集，不是提示性标签 |
| `required_state` | `accepted` 或 `integrated` |
| `depends_on[].required_commit` | 当 `required_state: accepted` 时必填，必须等于前置任务当前 `accepted_commit` |
| `owner_approval.gate` | `none`、`before_dispatch`、`before_acceptance`、`before_integration` |
| `resume_state` | 除 `blocked`、`integrated`、`cancelled`、`superseded` 外的合法状态；非阻塞时必须为 `null` |
| `delivery_state` | `none`、`working`、`submitted`、`invalid`、`accepted`、`rejected` |
| `integration_state` | `not_applicable`、`pending`、`integrated`、`failed` |
| `blocked_kind` | `external_approval`、`credentials`、`environment`、`dependency`、`role_timeout`、`report_unreachable`、`integration_conflict`、`decision_required`、`other` |

默认使用 `required_state: integrated`。只有后继 `base_commit` 已经包含前置 `accepted_commit` 时才允许写 `accepted`，并同时记录 `required_commit`；控制面必须执行 ancestry 校验，并把验证结果写入进入 Ready 的状态 event。无需代码集成的决策或文档任务应在验收后立即以 `integration_state: not_applicable` 进入 `integrated`，不需要放宽这一规则。

`accepted_commit` 是 PM 实际审过的提交，`integrated_commit` 是成果进入共享基线后的提交；cherry-pick 或 rebase 造成 SHA 不同时，在后续 integration event 中记录映射与等价性证据，不修改旧 acceptance record。

### 3.2 Revision 与 attempt

- 修改目标、验收标准、依赖、允许路径、冲突面或 `base_commit`：递增 `revision`，新建任务卡。
- 单纯的消息传输失败复用原 attempt 与 `dispatch_id`；角色在开工后失效或相同规格返工时，revision 不变，递增 `attempt` 并生成新 `dispatch_id`。
- revision 递增时，`attempt` 归零，交付与验收 SHA 清空，状态回到 `draft` 或 `ready`。
- 已派发的旧任务卡不得原地修改；旧回调保留为审计事件，但不能推动新 revision。

### 3.3 受控检查目录：`CHECKS.yaml`

自动化只能运行 PM 预先登记的检查 ID，不直接执行任务卡正文里的任意命令字符串。

```yaml
schema_version: agentdesk.check-catalog/v1
checks:
  daemon.typecheck:
    argv: [corepack, pnpm, --filter, "@agentdesk/daemon", typecheck]
    workdir: .
    network: false
    timeout_seconds: 600
  daemon.test:
    argv: [corepack, pnpm, --filter, "@agentdesk/daemon", test]
    workdir: .
    network: false
    timeout_seconds: 1200
```

`argv` 使用参数数组，避免通过 shell 解释任务文本。需要网络、生产环境、凭据或外部副作用的检查必须另走 capability 与审批流程。

### 3.4 岗位模型策略与绑定

`ROLE-POLICIES.yaml` 必须保持 JSON-compatible YAML，schema 为 `agentdesk.role-policies/v1`，并包含：

```json
{
  "tier_order": ["basic", "standard", "advanced", "expert"],
  "deliberation_tier_order": ["efficient", "balanced", "deep"],
  "risk_floors": {"L0":"basic", "L1":"standard", "L2":"advanced", "L3":"expert", "L4":"expert"},
  "roles": {
    "DEV": {
      "default_tier": "standard", "minimum_tier": "standard",
      "deliberation_tier": "balanced",
      "required_capabilities": ["coding", "testing"],
      "degradation_policy": "allow_to_minimum"
    }
  }
}
```

每个 Active role 都必须存在于 `roles`；`minimum_tier` 不得高于 `default_tier`。有效硬下限是岗位 minimum、任务非 inherit 值和风险 floor 的最大值；首选等级是岗位 default 与硬下限的最大值。能力要求是岗位与任务数组的并集，推理要求是岗位 `deliberation_tier` 的硬下限；运行时适配器负责把它映射为 provider 专属设置。`degradation_policy` 只可决定是否从首选等级降到仍满足全部硬要求的等级。

本机 `.agentdesk/runtime/model-bindings.yaml` 使用 `agentdesk.model-bindings/v1`，初始 `bindings` 为空对象；键就是稳定的 `binding_id`。每个值必须含 `provider`、`model_id`、`tier`、`deliberation_tier`、`capabilities`、`enabled`。该文件可随环境变化且禁止提交；凭据不得进入该文件或仓库。派发前必须运行 `scripts/select_model.py`，把其 JSON 输出原样复制到 `current_dispatch.model_selection` 和 outbox 顶层 `model_selection`；不得手算或静默编辑。实际选中项的同一非秘密快照进入 report `executor_model`，成为不可变证据。

```bash
python3 <skill-dir>/scripts/select_model.py --project <repo> --task-card-commit <40-char-sha> --role-id DEV --risk L2 --task-min-tier inherit [--required-capability long_context ...] [--degradation-approval-id APR-...]
```

真实派发必须提供完整 `--task-card-commit`，selector 会从该 commit 读取冻结策略；仅在任务卡尚未提交的配置预检中才可省略并读取当前工作树策略。

selector 固定输出且只允许下列九字段；整个对象就是不可改写的 selection snapshot：

```json
{
  "required_model_tier": "advanced",
  "required_model_capabilities": ["coding", "testing"],
  "model_binding_id": "local-coder-advanced",
  "selected_model_provider": "<provider>",
  "selected_model_id": "<stable-model-revision>",
  "selected_model_tier": "advanced",
  "selected_deliberation_tier": "balanced",
  "selected_model_capabilities": ["coding", "testing"],
  "model_degradation_approval_id": null
}
```

账本 `current_dispatch.model_selection`、对应 `task.dispatch` outbox 的顶层 `model_selection` 和 report 的 `executor_model` 必须按这九个键和值完全相等，不接受缺键、额外键、重命名或“等价”改写。`require_pm_approval` 且实际低于 preferred tier 时，非空 approval ID 必须全仓唯一、同时存在于 task 的 `granted_approval_ids`，并指向同 dispatch commit 或其祖先中的有效结构化批准 event；批准原记录不可改写，每个 approval 最多由一个严格后代的独立 `MODEL_DEGRADATION_REVOKED` 撤销。完整 YAML、时间与 lease epoch 规则见 [事件、Outbox 与校验](events-outbox-and-validation.md)。

`--allow-legacy-model-evidence` 只把部分旧终态缺失证据降为迁移审计 warning；这种运行不是 strict pass，也不得触发自动验收。要恢复 strict，必须建立可验证的新 attempt/dispatch 证据，而不是伪造历史字段。

## 4. 版本化任务卡模板

文件：`docs/pm/tasks/TC-XXX-rN-short-title.md`

```markdown
---
schema_version: agentdesk.task-card/v2
task_id: TC-XXX
revision: 1
type: implementation
role_id: R2
priority: P1
risk: L1
min_model_tier: inherit
required_model_capabilities: []
depends_on:
  - task_id: TC-000
    required_state: integrated
base_commit: "<40-char-git-sha>"
allowed_paths:
  - packages/service/**
blocked_paths:
  - packages/public-contracts/**
conflict_surfaces:
  paths:
    - packages/service/runtime
  symbols:
    - RuntimeService
  contracts:
    - service-runtime-contract
  migrations: []
required_checks:
  - check_id: service.typecheck
    required: true
owner_approval:
  gate: none
  required_capabilities: []
  approver_role_id: Owner
created_at: "<RFC3339 UTC>"
---

# TC-XXX · <可观察的结果>

## 目标
<一句话说明完成后系统具备什么能力。>

## 上下文预算
- `AGENTS.md`
- 本任务卡
- `<最小必要源码或契约>`
- 按需：`<用于消除不确定性的邻近资料>`
- 默认不读：完整历史报告、无关模块、全部产品文档

## 范围
- 允许：<具体行为或模块>
- 禁止：<不得改变的契约、行为或目录>

## 验收标准
- [ ] <可观察行为 + 证据形式>
- [ ] <失败路径或边界条件>
- [ ] 所有 required checks 有可复核结果

## 交付要求
1. 先提交实现，得到 `implementation_commit`。
2. 再创建并提交派卡消息指定的唯一 `report_path`。
3. 回调中提供实现 SHA 和报告 SHA；不要把报告所在提交写回报告自身。
```

任务卡不保存 `state`、`dispatch_id`、`attempt` 或当前交付 SHA。这些是易变编排数据，只存在于 `tasks.yaml` 和派卡消息中。

若确需从尚未 Integrated 的已验收分支继续开发，依赖必须写成：

```yaml
depends_on:
  - task_id: TC-000
    required_state: accepted
    required_commit: "<upstream-accepted-commit>"
base_commit: "<commit-that-contains-required-commit>"
```

PM 在任务进入 Ready 前验证祖先关系；只写 `required_state: accepted` 而没有 commit 证据属于 schema 错误。

角色对 `docs/pm/` 默认只读；当前派发在 `tasks.yaml` 与 outbox 中指定的**唯一 `report_path`**是协议级写入例外。控制面必须按 `task_id + revision + attempt` 推导该路径，禁止覆盖旧 attempt 的报告。即使任务用 `docs/pm/**` 做宽泛禁止区，也只有这个精确文件获得例外，其余控制面文件仍然禁止修改。

## 5. Delivery report 模板

文件：`docs/pm/reports/TC-XXX-rN-aN.md`

```markdown
---
schema_version: agentdesk.delivery-report/v2
task_id: TC-XXX
revision: 1
role_id: R2
delivery_status: completed
dispatch_id: DSP-TCXXX-R1-A1-XXXX
callback_id: CB-TCXXX-R1-A1-XXXX
attempt: 1
base_commit: "<task-card-base-sha>"
implementation_commit: "<reviewable-code-head-sha-or-null>"
report_commit: null
report_path: docs/pm/reports/TC-XXX-rN-aN.md
branch: feat/tc-xxx-short-title
executor_model:
  required_model_tier: advanced
  required_model_capabilities: [coding, testing]
  model_binding_id: "<binding-id>"
  selected_model_provider: "<provider>"
  selected_model_id: "<stable-model-revision>"
  selected_model_tier: advanced
  selected_deliberation_tier: balanced
  selected_model_capabilities: [coding, testing]
  model_degradation_approval_id: null
blocked_reason: null
suggested_resume_state: null
created_at: "<RFC3339 UTC>"
---

# TC-XXX · Delivery report · attempt N

## 摘要
<实现了什么；没有实现什么。>

## 改动与自检
- `<path>`：<改动原因>
- [x] <验收标准>：<测试、源码位置或可观察证据>

## 检查结果
| check | 结果 | 证据 |
| --- | --- | --- |
| `<required check name>` | passed / failed / not_run | `<摘要>` |

## 偏离、风险与后续
- None
```

`report_commit: null` 是有意设计：角色先提交实现，把 `implementation_commit` 写入报告后再提交报告，只在回调中发送新得到的 `report_commit`。PM 从该 SHA 读取报告并把两个 SHA 写入账本；不追加“填写 report_commit”的提交，因此没有自引用。

`delivery_status` 只允许 `completed`、`partial`、`blocked`。它是交付声明，不等于任务的权威 `state`。

`callback_id` 是必填的角色回调幂等键。同一交付事实的传输重试必须复用同一个 `callback_id`；只有新的 attempt 才生成新的回调 ID。

报告中出现 `callback_id` 只声明 Worker 准备发送哪一条回调，不证明真实发送或 PM 已收到。Standard / Automated 只有在 `.agentdesk/runtime/transport-receipts.yaml` 存在以该 `callback_id` 为键、`status: received` 且 source/destination 与 verified Worker/PM route 一致的 receipt 时，才可声称主动回调闭环完成。receipt 是本机运行事实，不写回 report 或 Git。

`executor_model` 必须逐字段复制 dispatch 的 `model_selection`，是实际执行者的不可变非秘密快照；binding ID、provider、稳定 model revision（provider 支持时）、tier、deliberation tier、capabilities 和降级批准 ID 均不得省略或用“更强模型”代替核对。即使任务进入清空 `current_dispatch` 的终态，它仍是追溯原始 dispatch event/outbox 的键和值证据。token、凭据、session/thread ID 不得写入 report。

`completed` 或包含代码变化的 `partial` 必须有 `implementation_commit`。若角色在产生实现前即受阻，可以把它设为 `null`，并让 `report_commit` 直接基于 `base_commit`；这种报告只能触发 Blocked，不满足 ReviewReady 的双提交条件。

只有 `delivery_status: completed` 且全部验收证据完整的交付可以进入 ReviewReady。`partial` 必须保持 InProgress、进入 Blocked，或由 PM 退回/修订任务；不能把“部分完成”自动解释成可验收完成。

## 6. Acceptance record 模板

文件：`docs/pm/acceptances/TC-XXX-rN-aN-reviewN.md`

```markdown
---
schema_version: agentdesk.acceptance/v2
task_id: TC-XXX
revision: 1
decision: accepted
reviewed_dispatch_id: DSP-TCXXX-R1-A1-XXXX
attempt: 1
type: implementation
role_id: R2
reviewer_role_id: PM
reviewer_id: "<pm-id>"
lease_epoch: 12
base_commit: "<task-card-base-sha>"
implementation_commit: "<sha-reviewed-by-pm>"
report_commit: "<sha-containing-reviewed-report>"
accepted_commit: "<same-reviewed-code-sha-or-null>"
owner_approval:
  gate: none
  approval_ids: []
evidence_refs: []
residual_risks: []
created_at: "<RFC3339 UTC>"
---

# TC-XXX · Acceptance · attempt N · review N

## 结论
accepted / returned / blocked

## 复核清单
- [ ] diff 相对 `base_commit` 且未越过 blocked paths
- [ ] conflict surfaces 已检查
- [ ] <标准>：<PM 复核证据>
- [ ] required checks 已复跑，或已说明为何采信报告结果

## 理由与后续整合要求
<通过理由或返工问题；若通过，说明应进入哪个 integration baseline。>
```

- `decision: accepted` 时 `accepted_commit` 必填，且必须是 PM 实际审查的代码提交。
- `returned` 或 `blocked` 时 `accepted_commit` 必须为 `null`。
- acceptance 中的 `owner_approval.approval_ids` 是本次审查实际使用的不可变证据，必须来自当时账本中的 `granted_approval_ids`。
- acceptance record、状态 event、更新后的账本和生成视图必须进入同一个状态 commit。
- acceptance record 一经提交不得修改；后续集成通过新的 integration event 与账本字段记录，不回写旧验收。

事件、outbox、运行时路由、消息格式、状态写入顺序与校验规则继续见 [事件、Outbox 与校验](events-outbox-and-validation.md)。
