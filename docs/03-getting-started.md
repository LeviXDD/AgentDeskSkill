# 新项目快速开始

本页以 Codex Desktop 和 Standard 模式为例。第一次使用时，先在非关键仓库跑通完整闭环。

## 环境要求

- Python 3.9 或更高版本；
- Git；
- 能加载本地 Skill 的 Codex 环境；
- Standard 还要求运行时能够创建、命名、读取并跨任务发送消息；
- 项目需要一个真实仓库根目录，而不是另一个仓库里的嵌套目录。

## 安装

当前公开 beta 是 `v0.2.1-beta`。从固定 tag 构建并激活可验证只读发布包：

```bash
(
  set -eu

  release_tag="v0.2.1-beta"
  repo_dir="AgentDeskSkill-$release_tag"
  release_dir="$HOME/.local/share/agentdesk/releases/$release_tag"
  codex_home="${CODEX_HOME:-$HOME/.codex}"
  install_link="$codex_home/skills/agentdesk"

  test ! -e "$repo_dir"
  test ! -L "$repo_dir"
  test ! -e "$release_dir"
  test ! -L "$release_dir"

  if { [ -e "$install_link" ] && [ ! -L "$install_link" ]; } ||
     { [ -L "$install_link" ] && [ ! -e "$install_link" ]; }; then
    echo "STOP: back up or repair $install_link before activation" >&2
    exit 1
  fi

  git clone --depth 1 --branch "$release_tag" \
    https://github.com/LeviXDD/AgentDeskSkill.git \
    "$repo_dir"
  cd "$repo_dir"
  test "$(git describe --tags --exact-match HEAD)" = "$release_tag"

  env PYTHONDONTWRITEBYTECODE=1 \
    python3 -m unittest discover -s tests -p 'test_*.py'
  test -z "$(git status --porcelain=v1 --untracked-files=all)"

  python3 -I tools/build_skill_release.py \
    --source skills/agentdesk \
    --destination "$release_dir" \
    --release-id "$release_tag"

  python3 -I tools/activate_skill_release.py \
    --release-directory "$release_dir" \
    --install-link "$install_link" \
    --expected-release "$release_tag" \
    --dry-run

  python3 -I tools/activate_skill_release.py \
    --release-directory "$release_dir" \
    --install-link "$install_link" \
    --expected-release "$release_tag"
)
```

不要把通用 Skill installer 当成 `v0.2.1-beta` 的可写安装器：tag 中的源码清单
刻意保持 development 身份，复制后目录也仍可写，因此只能用于审阅、测试和只读
影子校验。如果 `~/.codex/skills/agentdesk` 已经是 `v0.1.0-beta` 留下的普通目录，
激活器会拒绝覆盖；先按[公开分享与平台兼容](10-sharing-and-portability.md#从-v010-beta-升级安装)
备份并移出 Skill discovery，再执行激活。

安装后重新打开或刷新 Codex，确认 Skill 列表中出现 `$agentdesk`。如果本机仍显示旧名称 `$task-card-agent-dev`，不要同时让两个版本操作同一个项目；先完成旧名称迁移，见[公开分享与平台兼容](10-sharing-and-portability.md)。

不要把全局 Skill 路径直接链接到一个正在编辑的源码仓库。稳定版应指向只读发布目录，开发工作在独立 candidate 分支或 worktree 中进行。详见[版本隔离与安全升级](12-version-isolation-and-upgrades.md)。

## 第一次初始化：推荐提示词

对于空目录：

```text
使用 $agentdesk 以 Standard 模式初始化当前空目录。
我明确授权为该目录初始化 Git；先执行 dry-run，
不要创建任何用户可见岗位任务。

完成仓库分析后，请提出最小岗位集合、精确任务名称和创建数量，
等我确认后继续。
```

对于已经存在的 Git 仓库，去掉“授权初始化 Git”，并明确要求保留现有内容：

```text
使用 $agentdesk 以 Standard 模式初始化当前 Git 仓库。
先执行 dry-run；复用现有 AGENTS.md 和 docs/pm，
不覆盖文件，不修改业务代码，不创建用户可见岗位任务。
```

## 初始化命令

新项目与旧项目认领统一使用四态：`plan` 是零写入计划；`prepared` 是尚未提交的受限
快照；`committed` 表示用户已授权把该快照提交进 Git，但还不能开始普通写入；
`durable` 表示同一不可变 bundle 的 exact Writer（提交后使用
`--require-clean`）、视图检查和 strict validation 均已通过。初始化器返回 0 只说明
本阶段成功，不得越级报告完成。

Skill 实际执行的核心命令为：

```bash
python3 -I <skill-dir>/scripts/init_project.py \
  --project <repo> \
  --mode standard \
  --dry-run

python3 -I <skill-dir>/scripts/init_project.py \
  --project <repo> \
  --mode standard
```

空目录且已明确授权创建 Git 仓库时，两条命令都加入 `--init-git`。已有 Git worktree 不使用该参数。

正式初始化前，`AGENTS.md`、`docs/pm/**` 以及未来的任务卡、报告、验收、事件、outbox、讨论记录都必须能被 Git 跟踪；若现有 ignore 规则覆盖这些路径，应先修正规则。只有 `.agentdesk/runtime/` 按设计保持 gitignored。

初始化器具有保守的幂等行为：它不会覆盖已有文件。出现 `MANUAL_MERGE` 时，应检查差异并把必要规则合并到现有文件，而不是建立第二套并行控制面。

版本隔离发布会同时生成 `docs/pm/AGENTDESK.lock.json`。初始化器不是版本升级器：旧 AgentDesk 项目缺少 lock 时，dry-run 只做诊断，正式执行会在任何写入前停止。升级必须走显式迁移，不得通过重跑初始化器自动补锁。

新项目刚生成的 lock 在进入 `HEAD` 之前只是准备中的版本证据，不能授权普通
Writer。配置完项目事实后，先运行：

```bash
python3 -I <skill-dir>/scripts/render_views.py \
  --project <repo> \
  --migration-prepare
python3 -I <skill-dir>/scripts/render_views.py \
  --project <repo> \
  --check
python3 -I <skill-dir>/scripts/validate_project.py \
  --project <repo> \
  --pre-commit \
  --migration-prepare
```

审查并提交 scoped initialization snapshot 后，再运行 exact Writer gate、视图检查
和 strict validation。只有三者都通过，才能开始建卡或派发：

```bash
python3 -I <skill-dir>/scripts/check_compatibility.py \
  --project <repo> \
  --operation write \
  --require-clean
python3 -I <skill-dir>/scripts/render_views.py --project <repo> --check
python3 -I <skill-dir>/scripts/validate_project.py --project <repo>
```

如果初始化器输出了 `POST_COMMIT_COMMAND`（说明初始化前已有 baseline `HEAD`），提交
后应执行那条原样命令，而不是只把上面三条独立检查当成充分条件。它会证明初始化是
从记录 baseline 出发的一次 scoped 非 merge 提交，并同时执行 clean exact Writer、
视图和 strict 门禁。全新无 baseline 的仓库无法证明父提交范围，应把首次 root
commit 作为整个仓库审查，但仍必须通过上述三门禁。

## 初始化后必须补齐的项目事实

初始化不等于已经可以派卡。至少完成以下配置：

1. 在 `AGENTS.md` 填写项目目标、目录地图、构建和测试方式；
2. 在 `docs/pm/ROLES.md` 只启用真正需要的岗位；
3. 确保每个岗位拥有唯一 `role_no`、`role_id`、`role_name`；
4. 在 `ROLE-POLICIES.yaml` 配置岗位模型等级、推理深度和能力要求；
5. 在 `CHECKS.yaml` 登记可安全自动运行的检查 ID；
6. 确保仓库已有一个真实 baseline commit；
7. 保持 `tasks.yaml` 为空，直到第一张需求和验收标准已经具体；
8. 建立 gitignored `.agentdesk/runtime/`，但不向 Git 提交任何会话 ID、绝对路径或回执。
9. 确认 `AGENTDESK.lock.json` 与当前不可变 Skill 发布包兼容；开发态或无法验证 digest 的候选版不得开始项目写入。

## 创建岗位任务需要第二次明确授权

“初始化项目”“开始开发”或“使用 Skill”不代表允许创建 Codex 侧边栏中的新任务。Leader 应先列出拟创建的规范标题，然后等待确认。

示例：

```text
我确认启用以下岗位，并明确授权创建这些独立可见任务：

R1 . 开发工程师
R2 . QA 工程师

请完成任务创建、精确命名、读取复验、独立 worktree 绑定和运行时校验，
然后再派发任务卡。
```

获得授权后，Standard 必须完成以下能力链：

```text
确认目标项目或 worktree 上下文
→ 按当前宿主 schema 调用 create_thread
→ 等待异步创建结果
→ set_thread_title
→ read/list 复验真实任务与标题
→ route 标记 verified
→ 提交派发意图
→ send_message_to_thread
```

公开 beta 的低层适配器参考仍写有固定的 `list_projects → create_thread → ...` 工具链，但 Codex 宿主工具契约可能随版本变化。执行时必须先发现当前可用能力和参数；异步创建还必须等待结果，不能把“已排队”当成“任务已创建”。缺少等价的创建、等待、改名、读取或发送能力时，应降为 Lite 或停在门禁处。

独立 branch/worktree 的建立与绑定由持有 lease 的 PM 通过当前运行时适配器完成，并以冻结的 `base_commit` 为起点。用户无需自行猜测平台命令；如果 worktree 无法安全创建、不是独立目录或 HEAD 不匹配，任务保持 Ready/Blocked，不得先派发再补证据。

## 第一张任务卡

第一张卡应尽量小，避免同时验证太多复杂能力。建议选择：

- 一个边界清楚的局部功能；
- 一个开发岗位和一个可选 QA 岗位；
- 无生产凭据、无不可逆迁移；
- 有明确的构建或测试命令；
- 可以在独立 worktree 完成；
- 能走完实现提交、报告提交、主动回调、验收和集成。

## 第一次派发前检查

- [ ] 任务卡及岗位策略已进入不可变 `task_card_commit`；
- [ ] `base_commit` 是完整 SHA；
- [ ] 依赖、路径、冲突面和风险已明确；
- [ ] PM 与 Worker 是不同的真实任务；
- [ ] 实际标题严格等于 `<role_no> . <role_name>`；
- [ ] Worker 有独立 worktree；
- [ ] PM callback route 与 Worker route 都是 `verified`；
- [ ] 模型选择器能找到满足硬要求的 binding；
- [ ] repository validation 与 runtime validation 均通过。

初始化达到 `durable` 后，日常使用不需要反复初始化。请切换到 [项目运行手册](05-operating-guide.md) 的 PM 工作流。
