# 校验与故障恢复

AgentDesk 有两类校验：仓库协议校验和运行时校验。两者都是 Standard 的必要门禁，但当前 beta 即使两者都通过，也不能单独证明完整历史闭环成立；真实工具调用、PM 审查和未被校验器覆盖的状态仍需人工核对。

## 命令速查

### 生成或检查人类视图

```bash
python3 -I <skill-dir>/scripts/render_views.py --project <repo>
python3 -I <skill-dir>/scripts/render_views.py --project <repo> --check
```

### 提交前结构校验

```bash
python3 -I <skill-dir>/scripts/validate_project.py \
  --project <repo> \
  --pre-commit
```

它检查拟提交快照的结构，但不能证明尚未形成的 Git 原子提交。

### 提交后 strict 校验

```bash
python3 -I <skill-dir>/scripts/validate_project.py --project <repo>
```

它从 Git 历史验证已实现范围内的 event/outbox、账本、摘要、模型快照和 acceptance 等仓库证据。当前实现并未普遍重放每一次状态迁移的完整 event 链。

### 派发前 runtime 校验

```bash
python3 -I <skill-dir>/scripts/validate_runtime.py \
  --project <repo> \
  --role-id <role-id>
```

### 活动任务 runtime 校验

```bash
python3 -I <skill-dir>/scripts/validate_runtime.py \
  --project <repo> \
  --check-active
```

### 仓库校验并附加活动态 runtime 门禁

```bash
python3 -I <skill-dir>/scripts/validate_project.py \
  --project <repo> \
  --require-runtime
```

## 各校验能证明什么

| 检查 | 可以证明 | 不能证明 |
| --- | --- | --- |
| `render_views --check` | BOARD/STATUS 与账本一致；可选 CONSULTATIONS 与已通过公开严格校验的 consultation records 一致 | 运行时会话真实存在、消息已送达 |
| `validate_project --pre-commit` | 当前拟提交结构和交叉引用合理 | first-state commit 原子性 |
| `validate_project` | Git 中已实现范围的 dispatch event/outbox、账本、摘要、模型和验收证据一致 | 每一种状态迁移都有完整 event；真实 Codex 任务存在或消息已传输 |
| `validate_runtime` | 指定岗位及部分活动态的本机 route、标题和 receipts 满足 schema/关系 | 终态历史闭环；provider read/list 在此刻真实成功，除非刚执行过对应工具核验 |
| PM 代码审查与 CI | 实现、路径、契约和检查是否符合任务 | 跨任务回调是否真实发生 |

因此“validator 全绿”不能代替真实的任务创建、标题复验、派发、回调和 PM 审查。

## 当前 beta 的校验覆盖边界

- `validate_runtime --check-active` 当前只覆盖 `dispatched`、`in_progress`、`review_ready`；
- `accepted`、`integrated` 和仍保留有效 attempt 的部分 `blocked` 历史，不由它完整复核；
- 仓库没有活动任务时，runtime 检查可以空跑通过，这不是完整闭环证明；
- callback receipt 主要证明本地记录的传输结果，不证明 PM 已阅读或处理；
- repository strict 对派发首次提交等关键事实校验较强，但没有为所有状态迁移普遍验证“账本 + event + 视图同 commit”。

因此，发布“Standard 已闭环”结论前还要现场复验真实任务、精确标题、派发可见性、Worker ACK、回调可见性、PM 审查对象及 integration 证据。

## `--allow-legacy-model-evidence`

该参数只用于旧终态数据的迁移盘点。它把部分缺失模型证据降为 warning，但结果不是 strict pass，不能用于自动派发、自动验收或 CI 放行。

## 常见故障与处理

| 症状 | 判断 | 安全处理 |
| --- | --- | --- |
| Leader 创建了任务卡，却没有岗位任务 | 缺少授权或 runtime adapter 创建链路 | 保持 Ready；列出岗位标题，取得明确授权后创建并验证 |
| 只有 worktree，没有独立会话 | 不满足 Standard | 创建/绑定真实岗位任务，或明确降为 Lite |
| 岗位任务名称不符合编号规则 | route 不可用 | 修正标题，read/list 复验后再标 `verified` |
| 状态为 Dispatched，但 Worker 没收到 | outbox 已提交、transport 失败 | 重放同一 message/dispatch，不创建新 attempt |
| Worker 有报告，但 Leader 没收到回调 | F05 丢回调 | 保留报告和提交；PM 可审查 artifact，同时修复 route；不得伪造 receipt |
| Worker 在自己的任务里说完成 | 不是回调 | 使用跨任务发送工具向 delegation source 主动回调 |
| route 指向归档或不存在的任务 | route stale | 冻结派发、保留 worktree/报告/回执，重新绑定或按规则重启 attempt |
| report SHA 无法读取 | report unreachable | 进入 Blocked，要求发布可达 ref/artifact；不凭聊天摘要验收 |
| Accepted 的任务没有解锁后继 | 可能尚未 Integrated | 先进入目标基线并记录 integration 证据 |
| PM 同时有两个写入者 | PM 双活 | 冻结派发，选择唯一合法 epoch，另一方转只读并 reconcile |
| 模型 binding 不满足要求 | 模型 guard 失败 | 保持 Ready/Blocked；配置合格 binding 或取得合法降级批准 |
| repository strict 通过、runtime strict 失败 | 仓库意图存在，真实闭环不完整 | 修复 route/receipt，不宣称 Standard 完成 |

## Blocked 时应记录什么

不要只写“环境问题”。至少记录：

- 具体原因和分类；
- 责任人；
- 可验证解除条件；
- 下次检查时间；
- 当前 attempt 是否仍有效；
- 解除后希望恢复到哪个状态。

## 统一恢复顺序

```text
冻结新派发
→ 建立唯一 PM lease 与 epoch
→ 保全分支、worktree、report、消息和 receipts
→ 读取 tasks.yaml
→ 对照任务卡、Git、events、outbox、reports、acceptances、routes、receipts
→ 按 revision / attempt / ID 命名空间去重
→ 只修复能够证明的事实
→ 把不能证明的事项交给 PM 或 Owner
→ 重新生成视图
→ repository strict + 活动态 runtime 校验 + 真实工具复验
→ 恢复派发
```

最重要的恢复原则不是“尽快变绿”，而是重新建立一条可以继续信任的事实链。

完整故障表见[运行手册与故障恢复](../skills/agentdesk/references/runbooks-and-recovery.md)。
