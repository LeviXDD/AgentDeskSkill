# 初始化与显式迁移项目

仅在初始化或已批准的接入/迁移时阅读本流程，日常 PM 或 Worker 执行无需加载。现有协议和版本门禁保持不变。

## 目录

- [1. 写入前检查](#1-写入前检查)
- [2. 运行幂等初始化器](#2-运行幂等初始化器)
- [3. 配置项目权威信息](#3-配置项目权威信息)
- [4. 渲染、预检、提交与校验](#4-渲染预检提交与校验)

新项目初始化与旧项目接入使用相同的生命周期：
`plan` 表示零写入审查；`prepared` 表示尚未提交的限定范围快照；
`committed` 表示 Owner 授权的快照已进入 Git，但尚未启用；
`durable` 表示同一不可变包已在可验证的情况下检查提交范围，并通过干净的精确 Writer 锁、生成视图和严格项目校验。
初始化器或接入工具返回零退出码，不能跳过后续阶段。

## 1. 写入前检查

读取仓库根目录、`AGENTS.md`、构建/测试配置、现有 `docs/pm/`、当前 Git 分支/状态，以及已有的同类项目管理文件。

复用等价文件，不在现有流程旁再建一套平行流程。保留无关或用户所有的改动。

选择接入级别：

- `lite`：一个 PM 和一到两个岗位，手工接力、手工派发，不声称具备自动多任务闭环。
- `standard`：真实独立的可见 PM/岗位任务、精确岗位标题、独立 worktree、Schema 校验、显式依赖和集成门禁、outbox/inbox、带回执的主动跨任务回调，以及 heartbeat 对账。
- `automated`：完整保留 Standard 保证，并增加定时租约续期、自动回执/接单重试和死信处理、孤儿检测、更强沙箱、指标及恢复自动化。

新项目默认从 `standard` 开始，除非规模明确适合 `lite`。Standard 尚未成功运行前，不选择 `automated`。

## 2. 运行幂等初始化器

`<skill-dir>` 为本 `references/` 目录的父目录，即包含 `SKILL.md` 的目录。运行：

```bash
python3 -I <skill-dir>/scripts/init_project.py --project <repo> --mode standard --dry-run
python3 -I <skill-dir>/scripts/init_project.py --project <repo> --mode standard
```

初始化器不是升级器。已有 AgentDesk 标记但缺少 `docs/pm/AGENTDESK.lock.json` 时，dry-run 和实际初始化都会以非零状态停止，且不修改文件；应使用[版本管理与升级](versioning-and-upgrades.md)中的显式迁移流程。已有锁时，只有完全相同的包可以恢复中断或幂等重复执行的初始化。

未经验证的 `development` manifest 只能通过显式 `--allow-development-release` 参数，在可丢弃的测试项目中初始化。该参数不授权 PM/Worker 写入、派发、迁移或生产接入。

对于真实的 `legacy-unpinned` 仓库，先冻结派发，再使用精确的不可变 Stable 包运行专门的接入规划器：

```bash
python3 -I <skill-dir>/scripts/adopt_legacy_project.py --project <repo> --dry-run
```

规划器不写入任何内容。先审查其稳定版本身份、干净的 Git 基线、项目 ID、标记清单和无活动 attempt 的检查结果，再考虑应用。
确认计划按顺序包含：独占创建锁、初始化缺失模板、渲染生成视图、视图 `--check` 和提交前校验。只有 Owner 明确批准后才能应用：

```bash
python3 -I <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --apply \
  --acknowledge-legacy-unpinned \
  --json
```

该命令独占创建第一个锁，调用同包初始化器补齐缺失模板而不覆盖现有文件，再重新生成派生视图、检查视图并执行提交前校验。
保留用户文件和已有协议记录，只能补充缺失模板；`BOARD.md`、`STATUS.md` 和 `CONSULTATIONS.md` 是确定性生成视图，可从权威来源重建。
接入工具持续核对并约束 HEAD、权威状态、Git 暂存区和显式路径白名单；随包 Python 子进程以隔离模式启动，调用方控制的 `PYTHONPATH` 代码不能参与迁移。

应用成功只表示 `prepared`，不是完成。审查限定范围的 diff，由用户授权后创建一个包含锁、缺失模板和重新生成视图的迁移提交。
提交前，锁尚未进入 `HEAD`，普通 Writer 必须继续被阻止；只有工具输出的、带一致性保护的 `resume-after-lock` 命令可继续未完成的准备。
提交后，运行同一不可变包输出的精确 `post_commit_verification` 命令：

```bash
python3 -I <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --verify-prepared \
  --expected-head <payload.project_head> \
  --expected-state-sha256 <payload.state_sha256> \
  --json
```

该验证器要求记录基线上恰好有一个非合并的接入提交；拒绝原白名单之外的改动或必需路径缺失，要求工作树干净，再执行精确 Writer、生成视图和严格校验门禁。
只有返回 `status: durable` 才能称接入已持久完成。在此之前保持派发和业务写入冻结。
若当前会话无权提交，停在 `prepared`，报告待完成的审查、提交及提交后验证。

创建锁后任何步骤失败，都应保留锁并将结果视为未完成。只能用同一个包执行输出 payload 中唯一的、带一致性保护的 `recovery.command`。
该命令重新进入 `adopt_legacy_project.py --resume-after-lock`，在运行子工具前复验原 HEAD、状态摘要、锁、暂存区和路径白名单。
不得直接运行这些子工具，也不得删除、重建或手工编辑锁。这是受控的首次版本认领，不证明历史 Skill 身份，也不是通用的跨版本 Schema 迁移器。

默认值存在歧义时，提供 `--project-id` 和 `--pm-holder-id`。

用户要求在空目录创建新项目时，为两条初始化命令都添加 `--init-git`。不要在现有 worktree 内创建嵌套仓库，应使用实际仓库根目录。已有非 Git 目录时，先停止并取得引入版本控制的确认。

版本化控制面路径必须可被 Git 跟踪。有效忽略规则覆盖 `AGENTS.md`、`docs/pm/**` 或未来任务/报告/验收/事件/outbox/咨询证据时，应先停止并修正规则，再初始化或接入；只有 `.agentdesk/runtime/` 应被有意忽略。

创建第一张 Ready 任务卡前，确保仓库有真实基线提交。不得为消除校验器警告而伪造作者身份或提交无关改动。

初始化器不得覆盖现有文件。若输出 `MANUAL_MERGE`，先检查，再用 `apply_patch` 只合并必需的协议章节。

## 3. 配置项目权威信息

创建项目骨架后：

1. 在 `AGENTS.md` 中填写稳定的项目摘要、仓库地图、命令、安全规则和报告规则。
2. 仅在 `docs/pm/ROLES.md` 配置项目需要的岗位。每个 Active 岗位必须有稳定且唯一的 `role_no`、`role_id` 和非空 `role_name`。记录派生的 `expected_thread_title`，必须精确等于 `<role_no> . <role_name>`。
3. 由 Project Leader 在 `docs/pm/ROLE-POLICIES.yaml` 配置各 Active 岗位的厂商无关模型等级、推理深度、能力和降级策略。
4. 在被 Git 忽略的 `.agentdesk/runtime/model-bindings.yaml` 中绑定本地可用 provider 模型，将等级/能力标签视为受信运行时声明。本地操作者不在 Leader 信任边界内时，强制路由前必须有 Leader 批准的模型注册表或决策引用。凭据、token 和会话 ID 不得进入仓库证据。
5. 在 `docs/pm/CHECKS.yaml` 中登记可信检查 ID 和 argv 数组。
6. 在 `DECISIONS.md` 记录持久决策，不将其用作状态日志。
7. 需求和首张任务卡尚未明确前，保持 `tasks.yaml` 为空。
8. 启用咨询时，审查 `docs/pm/CONSULTATION-POLICY.yaml`：V1 上限为三个席位、两轮，席位只读，不递归创建任务，派生前必须由 Owner 确认。

不得向未确认岗位派发，也不得编造生产凭据、外部批准或发布权限。

首次 Standard/Automated 派发前，创建或重建被 Git 忽略的 `.agentdesk/runtime/routes.yaml`（`agentdesk.routes/v2`）和 `.agentdesk/runtime/transport-receipts.yaml`（`agentdesk.transport-receipts/v1`）。
运行时丢失不改变仓库事实，但会阻止派发，直到重新发现 PM/Worker 路由、精确实际标题、主机和 worktree，并标记为 `verified`。

绑定或刷新岗位路由后，运行实时派发前门禁：

```bash
python3 -I <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id>
```

仓库严格校验与运行时校验是不同的门禁；Standard / Automated 派发前二者都必须通过。

每次派发前运行确定性 selector，原样使用其 JSON 对象，不改名、不改字段：

```bash
python3 -I <skill-dir>/scripts/select_model.py --project <repo> --task-card-commit <40-char-sha> --role-id <role-id> --risk <L0-L4> --task-min-tier <inherit|basic|standard|advanced|expert> [--required-capability <capability> ...] [--degradation-approval-id <approval-id>]
```

将精确的九字段快照复制到 `current_dispatch.model_selection`、outbox 的 `model_selection`，以及后续报告的 `executor_model`。
若 `require_pm_approval` 允许降级选择，先分配范围明确的审批，并最晚在派发提交中持久化结构化批准事件。选择或审批失败时保持 Ready 或 Blocked，不得手算替代结果。

派发流程中必须提供 `--task-card-commit`，让 selector 从与任务卡相同的提交读取冻结岗位策略。仅在创建任务卡前、针对当前工作树策略做配置检查时才可省略。

随包 selector 和 validator 不启动 provider 模型，也不创建或向 Codex 任务发送消息。dispatcher/运行时适配器必须在发送 outbox 前证明能够满足所选 provider、稳定模型版本、推理深度、能力、真实任务身份及传输回执要求。
`executor_model` 是声明，不是独立 provider 证明。运行时无法选择指定模型或给出可信执行/消息回执时，此功能只能作为审计策略，不能声称强制路由或闭环成立。

## 4. 渲染、预检、提交与校验

```bash
python3 -I <skill-dir>/scripts/check_compatibility.py --project <repo> --operation write
python3 -I <skill-dir>/scripts/render_views.py --project <repo>
python3 -I <skill-dir>/scripts/validate_project.py --project <repo> --pre-commit
python3 -I <skill-dir>/scripts/render_views.py --project <repo> --check
# 审查并提交限定范围的工作流快照
python3 -I <skill-dir>/scripts/check_compatibility.py --project <repo> --operation write --require-clean
python3 -I <skill-dir>/scripts/validate_project.py --project <repo>
python3 -I <skill-dir>/scripts/render_views.py --project <repo> --check
```

新初始化或已批准接入的精确锁尚未提交时，前三条准备命令改用显式引导/迁移路径：

```bash
python3 -I <skill-dir>/scripts/render_views.py --project <repo> --migration-prepare
python3 -I <skill-dir>/scripts/render_views.py --project <repo> --check
python3 -I <skill-dir>/scripts/validate_project.py \
  --project <repo> \
  --pre-commit \
  --migration-prepare
# 审查并仅提交限定范围的初始化或迁移快照
python3 -I <skill-dir>/scripts/check_compatibility.py --project <repo> --operation write --require-clean
python3 -I <skill-dir>/scripts/render_views.py --project <repo> --check
python3 -I <skill-dir>/scripts/validate_project.py --project <repo>
```

如果 `init_project.py` 针对已有基线 HEAD 的仓库输出 `POST_COMMIT_COMMAND`，必须执行该精确命令，不能认为三条独立的提交后检查已足够。
它复用 `--verify-prepared`，证明初始化是基线上的单次限定范围提交，再运行这些检查。没有基线的新仓库无法证明父提交；应将根提交作为整个仓库审查，仍须通过干净的 Writer、视图和严格校验门禁。

存在 `CONSULTATION-POLICY.yaml` 或咨询记录时，还需运行 `python3 -I <skill-dir>/scripts/validate_consultations.py --project <repo>`。它校验咨询记录和工件，不代替真实任务、标题和传输核验。

普通 `--pre-commit` 要求 Writer 兼容。附加 `--migration-prepare` 仅将明确批准的初始化或迁移快照切换到 `migrate` 门禁，不适用于日常任务写入。
两种模式都会发出警告，因为控制面文件与 `HEAD` 不同时，无法证明提交原子性。准备通过后，审查 diff 并只提交限定范围的工作流文件，不得混入无关已有改动。
最终严格校验不得带 `--pre-commit`、`--migration-prepare` 或 `--allow-legacy-model-evidence`，并须证明权威状态、生成视图、验收记录及派发事件/outbox 证据与已提交 Git 历史一致。
最终校验的退出码不能代替独立的精确 Writer 门禁。会话未获授权提交已有仓库时，应停止并报告最终严格校验待完成，不能宣称初始化完成。

`render_views.py` 内部也执行门禁：写模式必须使用正在执行的不可变 renderer 旁的 manifest；`--release-manifest` 只能与只读 `--check` 一起使用。不得用外部 manifest 给其他工作目录的脚本借用 Writer 权限。

`--allow-legacy-model-evidence` 仅供旧终态记录的迁移审计使用。其警告不能视为严格通过，不能据此自动验收或派发。

最终严格校验仍报错时，不得宣称初始化完成。

随包 validator 对照已提交事件/outbox 证据核验账本状态，包括 ID 唯一性、首次派发提交原子性、outbox 原始字节摘要、模型快照一致性和降级审批关联。
它不证明 provider 执行回执、派发门禁的完整性、`CHECKS.yaml` 的语义安全或实际实现 diff 的路径范围；派发或验收前仍需执行[运行手册](runbooks-and-recovery.md)中的完整 Validator / CI 检查列表。
