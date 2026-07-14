# AgentDesk

[English](#english) · [中文](#中文)

## English

A Codex Skill for turning medium or large Git repositories into controlled, task-card-driven multi-agent development workflows. It provides project scaffolding, role protocols, Git-backed evidence, delivery and acceptance gates, runtime-route checks, and recovery validation. It does not replace the Codex runtime or grant additional permissions.

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

### Install

Install the pinned beta with the bundled Skill installer:

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo LeviXDD/AgentDeskSkill \
  --path skills/agentdesk \
  --ref v0.1.0-beta
```

### Quick start

Open the target Git repository in Codex Desktop and ask:

```text
Use $agentdesk to initialize this repository in
Standard mode. Start with a dry run, and ask before creating visible role tasks.
```

For a small project or a runtime without independent task tooling, request `Lite mode` explicitly. Inspect the dry-run output and repository diff before accepting generated workflow files.

### Privacy boundary

Versioned task cards, logical role IDs, decisions, events, and acceptance evidence belong in Git. Runtime-only data must stay in the initialized project's gitignored `.agentdesk/runtime/`: task or session IDs, host and absolute worktree paths, cursors, tokens, model bindings, transport receipts, and timed lease details. Never place credentials, production secrets, or runtime identifiers in cards, reports, events, or this public repository.

### Beta status

`v0.1.0-beta` is an early public release. Protocols and schemas may change before `v1.0.0`; pin the tag, review generated diffs, and test on a non-critical repository first.

## 中文

这是一个用于中大型 Git 仓库的 Codex Skill：它用任务卡、岗位协议、Git 证据、交付/验收门禁、运行时路由检查和恢复校验，把多 Agent 开发变成可追溯、可验收的流程。它不会替代 Codex 运行时，也不会自动获得额外权限。

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

### 安装

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo LeviXDD/AgentDeskSkill \
  --path skills/agentdesk \
  --ref v0.1.0-beta
```

### 快速使用

在 Codex Desktop 中打开目标 Git 仓库，然后输入：

```text
使用 $agentdesk 以 Standard 模式初始化当前仓库。
先执行 dry-run，创建用户可见的岗位任务前先征得我同意。
```

小型项目或缺少独立任务工具时，请明确要求 `Lite 模式`。接受生成内容前，先检查 dry-run 输出和仓库 diff。

### 隐私边界

版本化的任务卡、逻辑岗位 ID、决策、事件和验收证据可进入 Git。任务/会话 ID、主机与绝对 worktree 路径、游标、token、模型绑定、传输回执和定时租约等运行时数据，必须只保留在已被 Git 忽略的 `.agentdesk/runtime/` 中。不要把凭据、生产密钥或运行时标识写入任务卡、报告、事件或本公开仓库。

### Beta 说明

`v0.1.0-beta` 是早期公开版。`v1.0.0` 前协议和 schema 可能变更；请固定 tag、审查生成 diff，并先在非关键仓库中试用。

## License / 许可证

Released under the [MIT License](LICENSE). 本项目使用 [MIT 许可证](LICENSE)。
