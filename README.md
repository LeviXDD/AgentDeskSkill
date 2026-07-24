# AgentDesk

[English](#english) · [中文](#中文)

## English

A Codex Skill for turning medium or large Git repositories into controlled, task-card-driven multi-agent development workflows. It provides project scaffolding, role protocols, Git-backed evidence, delivery and acceptance gates, runtime-route checks, and recovery validation. It does not replace the Codex runtime or grant additional permissions.

Full Chinese documentation: [AgentDesk 用户文档](docs/README.md).

### Modes and boundaries

| Mode | What it means |
| --- | --- |
| **Standard** | Every active role uses a real, independently visible Codex task with the exact configured title, a dedicated Git worktree, verified runtime routes, proactive callbacks, and transport receipts. |
| **Lite** | Uses a manual baton or an explicitly authorized same-task role switch. It must not claim an independent multi-task closed loop, enforced model routing, or automated transport guarantees. |
| **Automated** | Adds lease renewal, retry/dead-letter handling, orphan detection, metrics, and recovery automation after Standard has been proven in the target environment. |

Standard requires Codex Desktop to expose task creation, naming, listing/reading, and cross-task messaging, and the runtime must be able to honor the selected model binding. If those capabilities are unavailable, use Lite or stop at the relevant gate. Creating a new user-visible Codex task always requires explicit user authorization.

### Requirements

- Python 3.9 or newer; bundled scripts use only the standard library.
- Git and a real repository root for durable workflow evidence.
- A Codex environment that can load local Skills; Standard additionally requires Codex Desktop and the task operations described above.

### Install `v0.2.0-beta`

Build the current public beta from its fixed tag, then atomically activate the
verified read-only bundle:

```bash
(
  set -eu

  release_tag="v0.2.0-beta"
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

  python3 tools/build_skill_release.py \
    --source skills/agentdesk \
    --destination "$release_dir" \
    --release-id "$release_tag"

  python3 tools/activate_skill_release.py \
    --release-directory "$release_dir" \
    --install-link "$install_link" \
    --expected-release "$release_tag" \
    --dry-run

  python3 tools/activate_skill_release.py \
    --release-directory "$release_dir" \
    --install-link "$install_link" \
    --expected-release "$release_tag"
)
```

The tagged source intentionally retains a development manifest. Installing
`skills/agentdesk` directly with the generic Skill installer creates a mutable,
review-only identity; it is not a writer-eligible release. Existing
`v0.1.0-beta` installations and projects are legacy and must be preserved or
adopted explicitly. See [sharing and portability](docs/10-sharing-and-portability.md)
for the exact installation, legacy-backup, and rollback procedure.

### Quick start

Open the target Git repository in Codex Desktop and ask:

```text
Use $agentdesk to initialize this repository in
Standard mode. Start with a dry run, and ask before creating visible role tasks.
```

For a small project or a runtime without independent task tooling, request `Lite mode` explicitly. Inspect the dry-run output and repository diff before accepting generated workflow files.

### Version isolation

Do not point the global Skill install at a mutable development checkout. Version-isolated releases use an immutable `release-manifest.json` plus a committed project `docs/pm/AGENTDESK.lock.json`; read-only checks may inspect an older unpinned project, but writes require an exact release/commit/bundle match. `v0.1.0-beta` predates project locks and is treated as `legacy-unpinned` by the `v0.2.0-beta` protocol—adopt it explicitly, never by rerunning the initializer. See the [version isolation and upgrade guide](docs/12-version-isolation-and-upgrades.md).

### Optional advisory roundtables

Before any task card is created, the Leader may optionally run a read-only V1 consultation with zero to three advisory seats; the same bounded pattern can discuss a complex topic outside a repository, without claiming Git-backed audit. Consultation sessions are separate from Active execution roles and never authorize execution-role sessions, implementation, merge, deployment, or release. A real roundtable creates one to three independently named sessions, runs at most two rounds, and requires every seat to proactively return its advice to the Leader. Only explicit Owner confirmation allows the Leader to record a decision or generate task cards. See the [consultation and roundtable guide](docs/11-consultation-and-roundtables.md).

### Privacy boundary

Versioned task cards, logical role IDs, decisions, events, and acceptance evidence belong in Git. Runtime-only data must stay in the initialized project's gitignored `.agentdesk/runtime/`: task or session IDs, host and absolute worktree paths, cursors, tokens, model bindings, transport receipts, and timed lease details. Never place credentials, production secrets, or runtime identifiers in cards, reports, events, or this public repository.

### Beta status

`v0.2.0-beta` is the current public beta; `v0.1.0-beta` remains the legacy
baseline. “Stable” in a built manifest means sealed and writer-eligible, not
post-beta API stability. Protocols and schemas may change before `v1.0.0`;
pin the tag, review generated diffs, and test on a non-critical repository
first.

## 中文

这是一个用于中大型 Git 仓库的 Codex Skill：它用任务卡、岗位协议、Git 证据、交付/验收门禁、运行时路由检查和恢复校验，把多 Agent 开发变成可追溯、可验收的流程。它不会替代 Codex 运行时，也不会自动获得额外权限。

完整中文文档：[AgentDesk 用户文档](docs/README.md)。

### 模式与边界

| 模式 | 含义 |
| --- | --- |
| **Standard** | 每个活跃岗位都使用真实、独立可见且精确命名的 Codex 任务，配合独立 Git worktree、已验证路由、主动回调和传输回执。 |
| **Lite** | 使用人工接力或经明确授权的同任务切换。不得声称实现了独立多任务闭环、强制模型路由或自动传输保障。 |
| **Automated** | 在 Standard 已经跑通后，再增加租约续期、重试/死信、孤儿检测、指标和恢复自动化。 |

Standard 需要 Codex Desktop 能够创建、命名、列出/读取并跨任务发送消息，且运行时能实际执行所选模型绑定。能力不足时应明确使用 Lite，或停在对应门禁。创建新的用户可见 Codex 任务始终需要用户明确授权。

### 环境要求

- Python 3.9 或更高版本；内置脚本仅使用标准库。
- Git，且目标路径应是真实的仓库根目录。
- 能够加载本地 Skill 的 Codex 环境；Standard 还需要 Codex Desktop 及上述任务操作能力。

### 安装 `v0.2.0-beta`

从固定 tag 构建当前公开 beta，再原子激活经过校验的只读发布包：

```bash
(
  set -eu

  release_tag="v0.2.0-beta"
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

  python3 tools/build_skill_release.py \
    --source skills/agentdesk \
    --destination "$release_dir" \
    --release-id "$release_tag"

  python3 tools/activate_skill_release.py \
    --release-directory "$release_dir" \
    --install-link "$install_link" \
    --expected-release "$release_tag" \
    --dry-run

  python3 tools/activate_skill_release.py \
    --release-directory "$release_dir" \
    --install-link "$install_link" \
    --expected-release "$release_tag"
)
```

tag 内的源码清单会刻意保持 development 身份。使用通用 Skill installer 直接复制
`skills/agentdesk`，得到的是可修改、仅供审阅和影子校验的身份，不是具备写权限的
正式发布包。现有 `v0.1.0-beta` 安装和项目属于 legacy，必须保留或显式认领。完整的
安装、旧目录备份和回滚流程见[公开分享与平台兼容](docs/10-sharing-and-portability.md)。

### 快速使用

在 Codex Desktop 中打开目标 Git 仓库，然后输入：

```text
使用 $agentdesk 以 Standard 模式初始化当前仓库。
先执行 dry-run，创建用户可见的岗位任务前先征得我同意。
```

小型项目或缺少独立任务工具时，请明确要求 `Lite 模式`。接受生成内容前，先检查 dry-run 输出和仓库 diff。

### 版本隔离

不要让全局 Skill 安装指向持续变化的开发目录。版本隔离发布通过不可变 `release-manifest.json` 和项目内提交的 `docs/pm/AGENTDESK.lock.json` 共同确定身份；旧项目可以只读检查，写入必须精确匹配 release、commit 和 bundle 摘要。`v0.1.0-beta` 早于项目锁机制，`v0.2.0-beta` 协议会把它识别为 `legacy-unpinned`，只能显式认领，不能靠重跑初始化器升级。详见[版本隔离与安全升级](docs/12-version-isolation-and-upgrades.md)。

### 可选咨询与圆桌研讨

创建任务卡之前，Leader 可以选择运行 V1 只读咨询：席位数可选 0–3，选择 0 表示跳过圆桌，真实创建时为 1–3 个独立咨询会话。同一套有界流程也可讨论仓库之外的复杂话题，但不会宣称具备 Git 审计和恢复能力。咨询席与 Active 执行岗位相互独立，最多进行两轮，每席都必须主动向 Leader 回传意见。只有 Owner 明确确认后，Leader 才能写入决策或生成任务卡；该确认不授权创建执行岗位会话、实施、合并、部署或发布。详见[咨询与圆桌研讨](docs/11-consultation-and-roundtables.md)。

### 隐私边界

版本化的任务卡、逻辑岗位 ID、决策、事件和验收证据可进入 Git。任务/会话 ID、主机与绝对 worktree 路径、游标、token、模型绑定、传输回执和定时租约等运行时数据，必须只保留在已被 Git 忽略的 `.agentdesk/runtime/` 中。不要把凭据、生产密钥或运行时标识写入任务卡、报告、事件或本公开仓库。

### Beta 说明

`v0.2.0-beta` 是当前公开 beta，`v0.1.0-beta` 保留为旧版基线。构建清单中的
Stable 表示制品已封装并具备通过写门禁的资格，不表示 API 已脱离 beta。
`v1.0.0` 前协议和 schema 仍可能变化；请固定 tag、审查生成 diff，并先在非关键
仓库中试用。

## License / 许可证

Released under the [MIT License](LICENSE). 本项目使用 [MIT 许可证](LICENSE)。
