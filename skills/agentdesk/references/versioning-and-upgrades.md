# 版本锁定与安全升级

本参考定义 AgentDesk Skill、项目协议和数据 Schema 的版本隔离规则。目标是让正在运行的项目继续使用其已固定版本，同时允许新版本在隔离环境中开发、只读验证、迁移和回滚。

## 目录

- [1. 两份版本事实](#1-两份版本事实)
- [2. Stable 与 Candidate](#2-stable-与-candidate)
- [3. 项目锁格式](#3-项目锁格式)
- [4. Reader 与 Writer 兼容性](#4-reader-与-writer-兼容性)
- [5. 初始化和恢复入口](#5-初始化和恢复入口)
- [6. Attempt 不跨版本](#6-attempt-不跨版本)
- [7. Dry-run 与影子验证](#7-dry-run-与影子验证)
- [8. 显式迁移](#8-显式迁移)
- [9. 回滚](#9-回滚)
- [10. Legacy-unpinned 项目](#10-legacy-unpinned-项目)
- [11. 发布门禁](#11-发布门禁)

## 1. 两份版本事实

版本身份由两份机器可读记录共同证明：

- Skill bundle 内的 `release-manifest.json` 描述该不可变 bundle 的 release、来源 commit、bundle SHA-256、协议、Schema 集合、runtime adapter 和读写能力。
- 项目内提交到 Git 的 `docs/pm/AGENTDESK.lock.json` 固定该项目允许使用的上述身份。

运行时 task/thread ID、host、绝对路径、cursor、token、transport receipt 和凭据不得进入项目锁；它们仍只属于 gitignored `.agentdesk/runtime/`。项目锁不是新的任务状态权威，`docs/pm/state/tasks.yaml` 仍是当前执行编排状态的唯一权威来源。

普通 PM、Worker、渲染器和看板不得重写项目锁。只有经过显式批准的初始化、版本认领或迁移流程可以创建或更新它，并把变更作为独立可审查的 Git 事实提交。

## 2. Stable 与 Candidate

同时维护两条互不覆盖的轨道：

| 轨道 | 用途 | 约束 |
| --- | --- | --- |
| Stable | 已有项目的生产性读写 | 安装目标必须是只读、内容可校验的不可变 bundle；按项目锁解析版本 |
| Candidate | 新版本开发、影子读取和 canary | 使用独立 branch/worktree 和隔离的 Skill 发现环境；不得替换 Stable 安装目标 |

全局 `$agentdesk` 可以指向一个不可变 Stable 默认版本，但恢复具体项目时仍必须先读项目锁，并解析到对应的不可变 bundle。找不到锁定版本、摘要不一致或只有 Candidate 工作目录时，停止写入；不得静默使用“最新版”。

`release_status: development` 的 manifest 可以使用类似 `0.2.0-dev` 的 release，并让 `source_commit` 或 `bundle_sha256` 为 `null`。这种身份只适用于可丢弃测试、影子读取和候选开发，不构成可验证的 Stable pin，也不得用于已有项目的正常 Writer 操作。

## 3. 项目锁格式

项目锁使用 `agentdesk.project-lock/v1`，并保持严格 JSON。模板见 [AGENTDESK.lock.json](../assets/project-template/docs/pm/AGENTDESK.lock.json)。

| 字段 | 规则 |
| --- | --- |
| `schema_version` | 固定为 `agentdesk.project-lock/v1`；未知主版本立即停止 |
| `project_id` | 必须与 `tasks.yaml` 的稳定项目 ID 相同 |
| `skill.release` | 非空 release ID；Stable 项目不得使用会移动的别名 |
| `skill.source_commit` | Stable 时为完整 Git commit；仅 development manifest 可为 `null` |
| `skill.bundle_sha256` | Stable 时为 release manifest 定义范围的 64 位 SHA-256；仅 development manifest 可为 `null` |
| `protocol.version` | 项目控制协议的精确版本 |
| `schemas` | 组件名到精确 Schema 版本的完整映射 |
| `runtime_adapter` | adapter ID、contract version 和非秘密 runtime Schema |
| `operation_compatibility.reader` | 此锁定版本可安全读取的协议、Schema 和 adapter contract |
| `operation_compatibility.writer` | 此锁定版本可安全写入的协议、Schema 和 adapter contract，以及精确版本/摘要要求 |

锁内 `schemas`、`runtime_adapter` 和 `operation_compatibility` 必须由同一 release manifest 确定性复制，不能手工删减来绕过不兼容。项目实际出现未知 Schema 时，即使它未列入锁也必须失败，不能把“未声明”解释为“可忽略”。

## 4. Reader 与 Writer 兼容性

兼容判断按操作分开，不使用单一的“兼容/不兼容”布尔值：

### Reader

只有以下条件全部成立，Candidate 或 Stable 才可只读检查项目：

1. 工具支持 `agentdesk.project-lock/v1`。
2. 锁定的协议版本存在于当前 manifest 的 `compatibility.reader.protocol_versions`。
3. 锁中每个 Schema 版本都存在于 `compatibility.reader.schema_versions`。
4. runtime adapter contract 存在于 `compatibility.reader.runtime_adapter_contracts`。

Reader 通过只授权读取、诊断、生成内存视图或写入项目外临时目录；它不授权修改任务账本、任务卡、事件、outbox、runtime receipt、生成视图或项目锁。

运行只读判定：

```bash
python3 <skill-dir>/scripts/check_compatibility.py --project <repo> --operation read
python3 <skill-dir>/scripts/check_compatibility.py --project <repo> --operation validate
```

`read` 和 `validate` 都使用 manifest 的 Reader 集合。development manifest 的空 source/digest 或缺失项目锁会产生 warning 并以成功状态退出，以便继续只读盘点；这不表示项目已锁定或 Writer 兼容。自动化需要结构化结果时增加 `--json`，并检查 verdict/warning，不能只看退出码。

### Writer

Writer 必须先满足 Reader 条件，再同时满足：

1. 协议、全部 Schema 和 adapter contract 均在 manifest 的 `compatibility.writer` 集合中。
2. `requires_exact_release: true` 时，manifest 与项目锁的 release、source commit 和 bundle SHA-256 完全相等。
3. `requires_verified_bundle: true` 时，按 manifest 的 digest scope 重算 bundle 摘要并验证成功；`null`、缺失或摘要不符均失败。
4. 当前项目不存在会被本次版本切换跨越的活动 attempt。

Reader 通过而 Writer 失败时，必须明确报告“只读兼容”，不得继续渲染到仓库、派发、回调入账、验收或迁移状态。

运行写入或迁移判定：

```bash
python3 <skill-dir>/scripts/check_compatibility.py --project <repo> --operation write
python3 <skill-dir>/scripts/check_compatibility.py --project <repo> --operation migrate
```

`write` 和 `migrate` 都使用 Writer 集合。development manifest 的空 source/digest、缺失项目锁、版本/摘要不匹配或能力集合不足必须 fail closed 并返回非零状态。外部 `--release-manifest <path>` 只允许用于 `read` / `validate` 的候选影子检查；写入与迁移必须使用执行中 checker 同一 bundle 内的 manifest，不能让开发脚本借另一个自报 Stable 的目录取得 Writer 权限。

## 5. 初始化和恢复入口

### 全新接入

初始化器先读取自身 `release-manifest.json`，验证 bundle，再执行 dry-run。只有仓库中不存在 AgentDesk markers 时，才可从同一 manifest 渲染并独占创建项目锁；锁、基础控制面和首次视图应作为同一初始化范围审查。

项目模板中的三个版本 token 必须由初始化器进行 JSON-safe 替换：

- `{{AGENTDESK_SKILL_RELEASE}}`
- `{{AGENTDESK_SOURCE_COMMIT_JSON}}`
- `{{AGENTDESK_BUNDLE_SHA256_JSON}}`

替换后不得残留模板 token，且结果必须能以严格 JSON 解析。

### 已接入或部分接入

- 已有项目锁：任何项目写入前先运行 Writer 兼容检查。
- 已有任一 AgentDesk marker 但没有项目锁：定义为 `legacy-unpinned`，普通初始化必须停止，不能自动补锁。
- 只有项目锁且其他文件尚未创建：视为初始化中断；只有同一已验证 bundle 可以幂等续跑。身份不同则停止并人工恢复。
- 锁与 `tasks.yaml.project_id` 不同：视为错误初始化或串库，禁止自动修正。

`init_project.py` 是保守初始化器，不是升级器。它不得借幂等补文件的行为给旧项目认领版本。

## 6. Attempt 不跨版本

一个执行 attempt 从首次派发到其关闭只能使用同一锁定 Skill 身份。暂停会话、停止 Worker 或等待回调都不会改变这一约束。

以下任一事实存在时，禁止更新项目锁：

- task 仍有 `current_dispatch`；
- task 处于 `dispatched`、`in_progress` 或 `review_ready`；
- blocked task 的当前 attempt 仍有效、未知或可能恢复；
- 已提交交付尚未由旧版本 PM 完成审查和关闭。

正常做法是让旧 attempt 在旧 Stable bundle 下完成报告、主动回调、审查和关闭，再迁移项目。若因安全或不可恢复故障必须中止，应先按旧协议保存证据并明确关闭旧 attempt；迁移后只能创建新的 attempt/dispatch，不能让原 dispatch 在新版本下继续。

Ready/Draft 卡没有活动 attempt，可以在迁移后重新执行 DoR。任务规格或兼容要求发生变化时新建 revision，不原地修改已冻结任务卡。

## 7. Dry-run 与影子验证

升级前按以下顺序执行，不对项目写入：

1. 冻结新派发、自动 heartbeat 修复和 Candidate 写入。
2. 保存当前 Stable bundle、manifest、项目锁和 Git 基线。
3. 用 Stable 执行 `--operation write`，确认项目当前仍可恢复。
4. 用 Candidate 执行 `--operation read --release-manifest <candidate-manifest>`；失败则停止并报告缺失协议、Schema 或 adapter contract。warning 只能继续盘点，不能进入迁移。
5. 盘点 Draft、Ready、活动、待审、Accepted 未 Integrated、Blocked 和孤儿证据。
6. 在项目副本、临时 worktree 或只读 shadow 环境运行迁移 dry-run。
7. 比较计划中的 lock、Schema、生成视图和 validator 差异；不得把 unrelated worktree 修改带入计划。

影子验证只产生诊断报告。Candidate 不得因为 Reader 通过就更新 `BOARD.md`、`STATUS.md`、runtime 文件或任何 Git 跟踪文件。

## 8. 显式迁移

所有版本升级逐项目进行：

当前通用脚本只提供第 10 节的 `legacy-unpinned` 首次认领，不包含任意
pinned-release 到新 release 的 Schema 转换器。`check_compatibility.py
--operation migrate` 是同 bundle 的迁移写入门禁，不会自行改 lock 或转换数据。
正式发布某个不兼容升级时，必须随该 release 提供并测试专用、确定性的 migrator；
缺少它时只能做 Reader 影子评估，不能宣称升级链已经自动化。

1. 确认没有活动 attempt，并取得范围明确的迁移批准。
2. 从干净基线创建迁移 branch/worktree；保留旧 Stable bundle 可用。
3. 运行 Candidate Reader 检查和迁移 dry-run。
4. 对每个 Schema 执行显式、可重复的转换；不猜测未知字段，不伪造旧 receipt 或模型证据。
5. 从 Candidate manifest 确定性生成新项目锁。
6. 在一个受控迁移 commit 中提交 Schema 转换、项目锁和由新状态生成的视图；不要混入业务改动。
7. 使用 Candidate 的 Writer 检查、严格 validator、视图 `--check` 和适用的 runtime 检查验证迁移结果。
8. 先在非关键项目 canary；确认恢复和回滚演练后，再逐项目批准迁移。

项目迁移成功不等于全局安装可以覆盖所有旧版本。仍有旧锁项目时必须保留其不可变 bundle，并按项目锁解析。

## 9. 回滚

按已产生的事实选择回滚方式：

- 迁移尚未写入：停止 Candidate，项目保持不变，继续使用旧 Stable。
- 迁移 commit 已写入但新版本尚未创建 attempt：使用新的 Git revert commit 撤销迁移，再用旧 Stable 严格验证；不得用 hard reset 删除证据。
- 新版本已有活动 attempt：先在新版本协议下保存并关闭这些 attempt，之后再执行兼容的反向迁移；不得直接切回旧 Writer。
- Schema 不支持无损反向转换：从迁移前基线建立恢复分支，保留迁移后的分支、reports、commits 和 runtime 取证，不伪称原地回滚成功。

回滚 lock 时必须同时回滚与其绑定的 Schema 变化。只改 release 字符串、只切换软链接或只恢复旧脚本都不是有效回滚。

## 10. Legacy-unpinned 项目

已有 AgentDesk 文件但缺少 `AGENTDESK.lock.json` 的项目统一标记为 `legacy-unpinned`。这表示“历史 Skill 身份不可证明”，不是一个可伪造填入锁文件的 release。

处理规则：

1. 冻结新派发和控制面写入，保全分支、worktree、cards、reports、events、receipts 和 Git 历史。
2. 不从文件修改时间、当前软链接、聊天记忆或最新 tag 反推旧 Skill 版本。
3. Candidate 只能在 Reader 能覆盖所有实际 Schema 时做只读盘点；Writer 始终阻断。
4. 盘点并关闭活动旧 attempt。若原 bundle 可证明，使用其恢复副本；不能证明时按人工恢复流程保留不确定性，不能伪造闭环证据。
5. 由 Owner/PM 明确批准版本认领和 Schema 迁移，在一个 migration commit 中首次加入真实、可验证的新锁。
6. 认领后重新运行 DoR；后续执行使用新的 attempt/dispatch。

不得通过普通 initializer、复制模板锁或把 `legacy-unpinned` 写成伪 SHA 来完成认领。

候选版本提供一个受限的首次认领入口：

```bash
python3 <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --dry-run
```

`--dry-run` 不写文件，可以在 development bundle 下输出诊断，但只有满足下列全部条件
的不可变 Stable bundle 才会显示可 apply：

- 当前目录是真实 Git worktree 根，存在 `HEAD` 且工作树干净；
- 项目存在明确 AgentDesk marker、没有 lock，且 lock 路径不是符号链接；
- `tasks.yaml` 可解析，`project_id` 稳定且没有活动 attempt；
- bundle 的 `release_status` 为 `stable`，source commit 和规范化 bundle digest
  都已验证，且整个 bundle 已设为只读；
- 将要调用的 initializer 与 manifest 来自同一个 bundle。

Owner 审查计划并明确批准后，才可执行：

```bash
python3 <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --apply \
  --acknowledge-legacy-unpinned
```

apply 会在临写入前重新检查上述条件，使用独占创建语义写入首个 lock，然后调用同一
bundle 的 initializer，仅补齐缺失模板，不覆盖现有文件。若 initializer 失败，lock
会故意保留为恢复锚点；必须按命令输出使用同一 bundle 续跑，不能删除或手改 lock。

该命令只解决“已有 V2 控制面但从未固定发布身份”的首次认领。它不会转换未知旧
Schema，不会证明旧历史使用过当前 release，也不是任意 pinned release 之间的升级
工具。需要 Schema 转换或已有 lock 的跨版本升级时，仍按第 8 节建立专用 migration
实现和迁移 commit。

## 11. 发布门禁

候选版本成为 Stable 前至少满足：

- release manifest 和 bundle digest 可确定性复算；
- 项目锁模板与 manifest 的协议、Schema、adapter 和 reader/writer 集合完全一致；
- 初始化 dry-run 不写入，正式初始化不覆盖既有文件；
- 旧项目缺锁时保持 `legacy-unpinned`，不会被自动补锁；
- Reader-only 检查不会产生项目内修改；
- 活动 attempt 会阻止 lock 更新；
- migration dry-run、canary、strict validation 和 view check 通过；
- 回滚演练能恢复旧锁、旧 Schema 与旧 Stable bundle；
- Stable 与 Candidate 的安装/发现路径互不覆盖。

任一门禁失败时保持 Candidate，不修改全局 Stable 指向，也不迁移仍在运行的项目。
