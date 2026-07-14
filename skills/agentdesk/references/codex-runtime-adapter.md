# Codex 任务运行时适配器

本参考是 Codex 上执行 Standard / Automated 多岗位闭环的低自由度适配器。它规定真实任务的创建、命名、核验、派发、主动回调和 receipt 记录顺序。仓库 event/outbox 证明“意图已提交”；本适配器证明“真实任务存在且消息已发送”。两者缺一不可。

## 目录

- [强制边界](#1-强制边界)
- [运行时文件](#2-运行时文件)
- [派发前创建或绑定岗位任务](#3-派发前创建或绑定岗位任务)
- [真实派发](#4-真实派发)
- [Worker 主动回调](#5-worker-主动回调)
- [消息模板](#6-消息模板)
- [重试与恢复](#7-重试与恢复)
- [适配器合规检查](#8-适配器合规检查)

## 1. 强制边界

1. Codex UI 中用户看到的是“任务”；工具和运行时字段使用 `thread` / `thread_id`。二者在本参考中指同一对象。
2. Standard / Automated 的 PM/Leader 与 Worker 必须是两个真实、可读取、彼此不同的 Codex 任务。子 Agent、逻辑角色切换、单独 Git worktree 或占位字符串都不等于岗位任务。
3. 每个 Active 岗位必须从 `ROLES.md` 取得唯一的 `role_no`、`role_id`、`role_name` 和派生 `expected_thread_title`。任务标题必须逐字等于：

   ```text
   <role_no> . <role_name>
   ```

   `expected_thread_title` 也必须等于该值；点号两侧各一个 ASCII 空格。自动生成标题、任务描述、`Await ... instructions`、英文职能缩写或 task-card 标题都不合格。
4. `create_thread` 会创建侧边栏可见、由用户拥有的任务。只有用户明确授权创建一个或一组指定岗位任务后才能调用。普通的“开始开发”“使用 Skill”“初始化项目”不构成授权。缺少授权时保持任务 `Ready`，列出拟创建的规范标题并询问；禁止静默同任务切岗。
5. 复用已经存在且重新验证成功的岗位任务不需要再次创建授权。创建授权只覆盖用户明确说出的岗位/批次，不可无限外推。
6. 岗位任务创建、改名或消息发送工具缺失时，只能运行 Lite 手工流程或停止。不得写占位 `thread_id`、伪造 receipt，或宣称 Standard / Automated 闭环成立。
7. 本适配器不改变 V2 的控制面权威：`tasks.yaml`、event/outbox、双提交交付、acceptance 和 integration 规则仍全部适用。

## 2. 运行时文件

所有 task/thread ID、绝对 worktree 路径和 transport receipt 都写在 gitignored `.agentdesk/runtime/`，不得进入仓库证据。

### 2.1 `routes.yaml`

使用 `agentdesk.routes/v2`。每个 PM 或 Active 岗位条目都必须包含以下字段；只有 `status: verified` 可用于派发或回调：

```json
{
  "schema_version": "agentdesk.routes/v2",
  "updated_at": "2026-07-13T08:00:00Z",
  "routes": {
    "PM": {
      "role_no": "PM",
      "role_name": "项目经理",
      "expected_title": "PM . 项目经理",
      "transport": "codex",
      "host_id": "local-mac-01",
      "thread_id": "<real-pm-thread-id>",
      "actual_title": "PM . 项目经理",
      "worktree": "/absolute/path/to/pm-worktree",
      "status": "verified",
      "verified_at": "2026-07-13T08:00:00Z"
    },
    "FE": {
      "role_no": "R2",
      "role_name": "前端工程师",
      "expected_title": "R2 . 前端工程师",
      "transport": "codex",
      "host_id": "local-mac-01",
      "thread_id": "<real-worker-thread-id>",
      "actual_title": "R2 . 前端工程师",
      "worktree": "/absolute/path/to/worker-worktree",
      "status": "verified",
      "verified_at": "2026-07-13T08:00:00Z"
    }
  }
}
```

`status` 只能是 `unbound`、`verified`、`stale`。任何读取失败、标题漂移、host/worktree 不可达、任务已归档/删除、PM 与 Worker 使用同一 `thread_id`，都立即把相关 route 标为 `stale` 并阻断新派发。

### 2.2 `transport-receipts.yaml`

使用 `agentdesk.transport-receipts/v1`。`dispatch_receipts` 以 `dispatch_id` 为键，`callback_receipts` 以 `callback_id` 为键：

```json
{
  "schema_version": "agentdesk.transport-receipts/v1",
  "updated_at": "2026-07-13T08:10:00Z",
  "dispatch_receipts": {
    "DSP-TC002-R2-A1-7F3C": {
      "message_id": "MSG-20260713-0001",
      "dispatch_id": "DSP-TC002-R2-A1-7F3C",
      "role_id": "FE",
      "source_thread_id": "<real-pm-thread-id>",
      "destination_thread_id": "<real-worker-thread-id>",
      "status": "sent",
      "provider_receipt": "<provider-result-or-null>",
      "sent_at": "2026-07-13T08:05:00Z",
      "acknowledged_at": null
    }
  },
  "callback_receipts": {
    "CB-TC002-R2-A1-2D91": {
      "callback_id": "CB-TC002-R2-A1-2D91",
      "dispatch_id": "DSP-TC002-R2-A1-7F3C",
      "source_role_id": "FE",
      "destination_role_id": "PM",
      "source_thread_id": "<real-worker-thread-id>",
      "destination_thread_id": "<real-pm-thread-id>",
      "status": "received",
      "provider_receipt": "<provider-result-or-null>",
      "received_at": "2026-07-13T08:10:00Z"
    }
  }
}
```

`dispatch_receipts[*].status` 只允许 `sent` 或 `acknowledged`；`callback_receipts[*].status` 固定为 `received`。发送失败不是 receipt：不得写成成功条目，保留 transport 错误用于重试即可。Dispatch 重试复用同一 `dispatch_id`/`message_id`；callback 重试复用同一 `callback_id`。`acknowledged_at` 在 `sent` 时为 `null`，Worker ack 后才填写。callback 的 `destination_thread_id` 必须等于原始委派的 `source_thread_id` 与 verified PM route。

## 3. 派发前创建或绑定岗位任务

严格按下列顺序执行，不跳步。

### 3.1 验证 PM/Leader 来源任务

1. 从当前 Codex 运行上下文取得真实 PM/Leader `source_thread_id`；不得自行编造。
2. 用 `read_thread(source_thread_id)` 读取任务，再用 `list_threads` 交叉确认该 ID 仍存在、可达且属于当前 PM/Leader。
3. 从 `ROLES.md` 读取 PM 的 `role_no`、`role_id`、`role_name`，计算 `expected_title`。标题不一致时先调用 `set_thread_title` 修正，再次 `read_thread` / `list_threads` 验证。
4. 核对当前 PM worktree 与 host，写入或刷新 PM 的 `routes.yaml` 条目。任一项不能验证时停止；没有 verified PM route 就没有可靠 callback 目标。

### 3.2 准备独立 worktree

1. 为当前 attempt 准备独立 branch/worktree，使 HEAD 等于任务卡 `base_commit`。
2. 验证绝对路径存在、可写、属于当前 `host_id`，且没有绑定另一个活动 attempt。
3. 不清理、不 reset、不删除未知或用户已有 worktree。发生冲突时保持 `Ready` 并交给 PM 恢复。

### 3.3 优先复验现有岗位任务

若 route 声称已绑定：

1. 用 `read_thread(thread_id)` 和 `list_threads` 验证真实任务存在且未失效。
2. 验证 `thread_id != PM source_thread_id`。
3. 验证 `actual_title == expected_title == "<role_no> . <role_name>"`。
4. 验证 host/worktree 与本 attempt 一致且岗位任务没有其他活动 attempt。
5. 全部通过后刷新 `verified_at`；否则标为 `stale`，不得把旧 ID 当作可用 route。

### 3.4 创建新岗位任务

仅在没有可复用 verified route 时执行：

1. 检查本轮对规范标题 `<role_no> . <role_name>` 的显式用户授权。没有授权就暂停并询问，不能继续调用工具。
2. 对项目任务先调用 `list_projects`，取得当前仓库对应的真实 `projectId`；不得猜测项目 ID，也不得用 projectless 任务代替项目岗位任务。
3. 调用 `create_thread`，按其实际 schema 把新任务绑定到该 `projectId` 和已准备的 worktree。初始消息只声明岗位身份、工作树和“等待不可变派卡”，不得让它凭聊天猜任务。只有 Leader 已明确指定具体模型/思考档位时才覆盖运行时默认值；否则遵守宿主工具规则，不擅自传模型参数。
4. 捕获真实 `thread_id`。若只返回排队用 `clientThreadId`，等待任务完成创建，再用 `list_threads` 找到并核对真实 `thread_id`；不能把 client ID 写成 verified route。
5. 立即调用 `set_thread_title(thread_id, "<role_no> . <role_name>")`。
6. 依次调用 `read_thread(thread_id)` 和 `list_threads`，验证 ID、实际标题、可达状态以及它与 PM 任务不同。
7. 再次验证该任务实际绑定的 worktree/host。无法证明时把 route 记为 `stale`，保持卡片 `Ready`。
8. 只有全部通过后，才把 `routes.yaml` 条目写成 `status: verified`。
9. 运行以下 guard；失败时把 route 保持为 `stale`/`unbound` 并停止派发：

   ```bash
   python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id>
   ```

因此 Codex 新任务的强制工具链是：

```text
explicit user authorization
→ list_projects
→ create_thread
→ set_thread_title
→ read_thread + list_threads verify
→ route status=verified
→ commit dispatch intent
→ send_message_to_thread
```

## 4. 真实派发

1. 在写 `TASK_DISPATCHED` 前，重新确认 PM route、岗位 route、精确标题、worktree、host、single-flight 和模型 binding 均通过。
2. 按 V2 规则原子提交 `current_dispatch + TASK_DISPATCHED event + outbox + views`，再运行 strict validator。仓库事务失败时不得发送。
3. 从 verified route 读取目标 `thread_id`，把不可变 outbox payload 与本次运行时 PM `source_thread_id` 组装为自包含派卡消息。`source_thread_id` 只进运行时消息，不写 Git。
4. 调用 `send_message_to_thread(destination_thread_id, dispatch_message)`。禁止只在 PM 当前任务里打印消息或让用户手动复制后声称已派发。
5. 将工具返回的 provider result、源/目标 task ID 和发送时间写入 `dispatch_receipts[dispatch_id]`，初始状态为 `sent`。调用失败时不写成功 receipt，也不得迁移到 `InProgress`。
6. 用 `read_thread(destination_thread_id)` 验证消息可见，或等待 Worker 返回匹配四元组的 ack；随后才由 PM 以普通状态迁移记录 `DISPATCH_ACKNOWLEDGED`。receipt 证明 transport，ack 证明 Worker 接单；二者都不等于完成。
7. 对活动任务运行 runtime strict；失败则停止推进并进入恢复：

   ```bash
   python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id> --check-active
   ```

## 5. Worker 主动回调

Worker 完成或受阻时必须在结束自身任务前执行：

1. 完成 implementation/report 双提交并把两者发布到 PM 可读位置。
2. 从原始委派上下文读取 `delegation source_thread_id`。它是 callback 的首选目标；不得根据标题搜索出一个“看起来像 PM”的任务。
3. 读取 `.agentdesk/runtime/routes.yaml` 的 verified PM route，核对其 `thread_id` 与 delegation `source_thread_id` 相同。再用 `read_thread` / `list_threads` 验证目标仍存在。若不相同或不可达，停止并报告 `callback_route_mismatch`；不得静默换目标。
4. 读取当前 Worker route，核对本任务的 `thread_id`、`role_no`、`role_id`、`role_name` 和精确标题。
5. 用固定 `callback_id` 组装回调，调用 `send_message_to_thread(delegation_source_thread_id, callback_message)`。在 Worker 自己任务中输出最终总结不算回调。
6. transport/PM 收到消息后，把 provider result 写入 `callback_receipts[callback_id]`，状态固定为 `received`；其中 destination 必须等于 delegation source 与 verified PM route。
7. 只有存在 `status: received` 的匹配 callback receipt 才可宣称“已主动回调 PM”。失败或无法确认接收时复用相同 `callback_id`、payload 和目标重试；仍失败则保留 durable report，明确标记 callback 未送达，并让 heartbeat/PM 恢复，不能声称闭环已完成。

PM 收到 callback 后按 `callback_id + dispatch_id + attempt + revision` 去重、入 inbox、读取 Git 证据并验收。callback 只负责唤醒 PM，不是验收结论。

入账 callback receipt 后再次运行 `validate_runtime.py --project <repo> --role-id <role-id> --check-active`。本地闭环最终校验必须让仓库 strict 与 runtime strict 同时通过：

```bash
python3 <skill-dir>/scripts/validate_runtime.py --project <repo> --check-active
python3 <skill-dir>/scripts/validate_project.py --project <repo> --require-runtime
```

第一个命令检查刚由 `read_thread` / `list_threads` 核验并落盘的 route attestation 与 receipts；它不能替代当次真实工具读取。第二个命令在仓库协议验证后强制运行时门槛。任一失败都不能宣称 Standard / Automated 闭环成立。

## 6. 消息模板

### 6.1 Bootstrap（创建任务时）

```text
你是 <role_no> . <role_name>（role_id=<role_id>）。
这是独立岗位任务，不是 PM/Leader 任务。
绑定 worktree：<absolute-runtime-worktree>
现在只验证身份、标题和工作树，然后等待包含 task_id/revision/attempt/dispatch_id 的不可变派卡；不要自行猜测或开始业务实现。
```

### 6.2 Dispatch（`send_message_to_thread`）

```text
【派卡 · <task_id> · r<revision> · attempt <attempt>】
岗位：<role_no> . <role_name>（role_id=<role_id>）
dispatch_id：<dispatch_id>
message_id：<message_id>
source_event_id：<event_id>
PM callback source_thread_id：<runtime-only-pm-thread-id>
任务卡：<task_path>@<task_card_commit>
base_commit：<base_commit>
branch/worktree：<branch> / <runtime-worktree>
report：<report_path>
model_selection：<exact-nine-field-snapshot>

请先核对当前真实任务标题、worktree HEAD、四元组和模型快照；不一致立即回报。
完成时先发布 implementation/report 两个提交，再主动向上述 source_thread_id 发送【回报】；仅在本任务输出最终答复不算回调。
```

### 6.3 Callback（Worker → PM）

```text
【回报 · <task_id> · r<revision> · attempt <attempt>】
岗位：<role_no> . <role_name>（role_id=<role_id>）
状态：completed | partial | blocked
dispatch_id：<dispatch_id>
callback_id：<callback_id>
implementation_commit：<sha-or-null>
report_commit：<sha>
report：<report_path>
executor_model：<exact-nine-field-snapshot>
blocked_reason：<none-or-reason>
需要 PM：验收 | 澄清 | 决策
```

## 7. 重试与恢复

- Dispatch transport 重试：复用同一 `message_id`、event、dispatch、payload digest、目标 `thread_id` 和模型快照。
- Callback transport 重试：复用同一 `callback_id`、dispatch、payload 和 delegation source target。
- 标题漂移：停止发送，调用 `set_thread_title` 修正，并经 `read_thread` + `list_threads` 重新验证后再继续；不修改逻辑 role identity。
- 任务不可达/已归档：route 标 `stale`，保留 report、branch、worktree 和 receipts；由 PM 决定在未开工时重新路由，或开工后新建 attempt。
- report 已存在但 callback receipt 缺失：按“漏回调”恢复，PM 可依据 durable Git 证据进入审查；同时修复 route。不得回写伪造 receipt。
- Heartbeat 只扫描 `stale` route、发送失败/缺失 receipt、未 ack dispatch 和 report-without-callback；它唤醒 PM 或重放同一 transport，不执行实现、不验收、不合并、不创建第二个 attempt。

## 8. 适配器合规检查

宣称 Standard / Automated 闭环成立前，逐项回答“是”：

- [ ] PM 与每个 Active Worker 都有不同且真实可读取的 `thread_id`。
- [ ] 每个 Active role 有唯一 `role_no`、`role_id`、`role_name`。
- [ ] 每个真实任务 `actual_title` 与 `<role_no> . <role_name>` 完全相等。
- [ ] 新可见任务均有明确用户授权；没有用“开始开发”推定授权。
- [ ] PM callback route、Worker route、host 和独立 worktree 均为 `verified`。
- [ ] 派发通过 `send_message_to_thread` 真实发送，并有匹配 `dispatch_receipt`。
- [ ] Worker 通过 `send_message_to_thread` 主动回调 delegation source，并有匹配 `callback_receipt`。
- [ ] 回调目标与 verified PM route 一致，用户没有充当消息中转站。
- [ ] heartbeat 仅处理丢失/失败通知，没有替代主动 callback。
- [ ] `validate_runtime.py --project <repo> --check-active` 通过。
- [ ] `validate_project.py --project <repo> --require-runtime` 通过；仓库 strict 与 runtime strict 双通过。

任一项为“否”，应明确报告具体缺口并保持 Lite/Blocked/Recover 状态；不得用 `55 pass / 0 error`、outbox 文件存在或 report 已提交来替代真实闭环证明。
