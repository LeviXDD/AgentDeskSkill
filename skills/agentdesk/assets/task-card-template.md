---
schema_version: agentdesk.task-card/v2
task_id: TC-XXX
revision: 1
type: implementation
role_id: DEV
priority: P1
risk: L1
min_model_tier: inherit
required_model_capabilities: []
depends_on: []
base_commit: "<40-char-git-sha>"
allowed_paths: []
blocked_paths: []
conflict_surfaces:
  paths: []
  symbols: []
  contracts: []
  migrations: []
required_checks: []
source_consultation_id: null
source_confirmation_id: null
owner_approval:
  gate: none
  required_capabilities: []
  approver_role_id: Owner
callback_destination_role_id: PM
callback_transport: runtime_route
callback_required: true
created_at: "<RFC3339 UTC>"
---

# TC-XXX · <可观察的结果>

## 目标

<一个具体结果。>

## 上下文预算

必读：
- `AGENTS.md`
- 当前任务卡
- `<必要的源码或契约>`

按需读取：
- `<用于澄清不确定性的相关材料>`

默认不读取：
- 完整历史报告
- 无关模块
- 全部项目归档

## 范围

允许：
- `<行为或模块>`

禁止：
- `<契约、行为或目录>`

## 验收标准

- [ ] `<可观察的行为及证据>`
- [ ] `<失败或边界情形>`
- [ ] 所有必需检查均有可审查证据

## 交付

使用派发指定的分支、attempt、ID、基线提交和精确报告路径。先提交实现，再提交报告。完成报告仅请求 PM 审查，不等于回调或验收。

报告提交存在后，通过已核验的运行时路由，主动向逻辑岗位 `callback_destination_role_id` 发送一次完成/阻塞回调。每次传输重试复用同一 `callback_id`；取得成功运行时回执后才能宣称交接完成。回调失败时，保留报告和回调 ID，明确报告传输失败，并使原回调保持可重试。

不得在任务卡或报告中写入主机 ID、会话/线程 ID、worktree 绝对路径、provider 回执、重试计时器、凭据或其他运行时路由值。这些信息只放在被 Git 忽略的 `.agentdesk/runtime/` 中。
