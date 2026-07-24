# 版本隔离与安全升级

AgentDesk 的 Skill 文件、项目控制面和正在执行的任务不是同一类状态。升级时必须把三者分开，否则“改了 Skill 源码”可能在下一轮对话中悄悄改变一个进行中项目的规则。

## 三层版本身份

### 1. Skill 发布包

每个可投入使用的发布包都应是只读、不可变的目录，并带有 `release-manifest.json`。发布身份由以下字段共同确定：

- `skill_release`；
- `source_commit`；
- `bundle_sha256`；
- `protocol_version`；
- `schema_set`；
- `runtime_adapter` contract。

开发目录中的 `0.2.0-dev`、空 `source_commit` 或空 `bundle_sha256` 代表候选源码，不是可执行生产写入的正式发布包。

### 2. 项目版本锁

新项目在 `docs/pm/AGENTDESK.lock.json` 中记录创建该控制面的 Skill、协议、schema 集和运行时适配器。该文件属于 Git 控制面，不保存会话 ID、绝对路径、token 或其他运行时隐私。

Skill 读取项目时先比较发布清单与项目锁：

- `read` / `validate`：兼容时继续；旧项目无锁时只警告并保持只读；
- `write`：必须精确匹配项目锁，并且当前发布包有可验证的 commit 和 digest；
- `migrate`：必须显式执行，不由初始化器或普通 PM 工作流顺手完成；
- 未知 lock schema、协议、schema 或 adapter：失败关闭。

### 3. Attempt 执行版本

一个已经派发的 attempt 必须用它开始时的发布身份完成、退回或终止。不能在同一个 attempt 中途切换 Skill、schema 或 adapter，再把前后证据拼成一个闭环。

## 稳定版与候选版必须物理分离

推荐目录关系：

```text
可修改源码仓库
└── candidate branch / worktree

~/.local/share/agentdesk/releases/
└── <immutable-release>/skills/agentdesk

~/.codex/skills/agentdesk
└── 只指向某个 immutable release
```

不要让 `~/.codex/skills/agentdesk` 直接指向正在编辑的源码目录。这样做时，一次文档或脚本保存就等于全局热更新，已经打开的项目也无法判断自己正在使用哪一版。

## 旧项目的安全状态

没有 `AGENTDESK.lock.json` 的历史项目称为 `legacy-unpinned`。它不等于损坏，也不应被自动补锁：

- 可以用新 Skill 做只读盘点和兼容性报告；
- 可以继续使用原来冻结的 Skill 完成原 attempt；
- 不允许新 Skill 直接修改控制面、派发、验收或恢复写入；
- `init_project.py --dry-run` 可以审计；
- 不带 `--dry-run` 的初始化器必须在写入前停止，要求显式迁移。

自动给历史项目补一个“当前版本”的 lock 会制造虚假事实：它只能证明补锁时使用了哪版工具，不能证明旧控制面由该版本创建。

### 首次版本认领

对于控制面已经存在、Schema 可由当前 Reader 完整理解、但从未生成版本锁的项目，
使用专用入口而不是初始化器：

```bash
python3 <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --dry-run
```

dry-run 始终零写入。只有真实 Git 根、干净 HEAD、无活动 attempt、无现有 lock，
并且执行脚本来自 commit/digest 均已验证的 Stable bundle 时，计划才可 apply。
Owner 明确接受历史身份不可证明这一事实后执行：

```bash
python3 <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --apply \
  --acknowledge-legacy-unpinned
```

apply 会在写入前再次检查环境，以独占方式创建首个 lock，再调用同一 bundle 的
initializer 只补缺失模板。若 initializer 失败，lock 会保留为恢复锚点；不要删除或
手改它，应按输出使用同一 bundle 续跑。

该命令不是通用升级器：它不转换未知 Schema，不证明旧历史由当前 release 创建，也
不处理已经固定到另一 release 的项目。后一类项目仍需要专用、可重复的 Schema
migration 和独立迁移 commit。

## 日常兼容检查

```bash
python3 <skill-dir>/scripts/check_compatibility.py \
  --project <repo> \
  --operation read

python3 <skill-dir>/scripts/check_compatibility.py \
  --project <repo> \
  --operation validate

python3 <skill-dir>/scripts/check_compatibility.py \
  --project <repo> \
  --operation write
```

`write` 失败时，不要通过手工删锁、改版本号或复制模板绕过门禁。应继续使用项目锁定的旧发布，或进入单独的迁移流程。

## 推荐升级流程

当前候选只内置 `legacy-unpinned` 的首次版本认领；它没有承诺一个能处理任意
pinned release 与未知 Schema 的通用升级器。`check_compatibility.py --operation
migrate` 只是迁移写入前的门禁，不会自行转换数据。未来某次发布若改变 Schema，
必须同时提供该版本专用、可重复且有回滚测试的 migrator，否则只能影子评估，不能
直接升级项目。

1. 暂停新派发，并让活动 attempt 在旧版下完成、退回或明确终止；
2. 记录当前全局 Skill 指向的不可变目录；
3. 在独立候选分支开发，不改稳定发布目录；
4. 从干净 commit 构建带 commit 与 digest 的只读候选包；
5. 在临时项目和历史项目副本上执行单测、Skill 校验、初始化 smoke、只读兼容检查；
6. 为目标项目建立单独迁移分支，输出迁移计划、前后 schema、回滚点和受影响任务；
7. 用户确认后才更新项目 lock 和控制面；先做一个 canary 项目；
8. 验证通过后原子切换全局 Skill 链接；
9. 保留上一发布目录；回滚时恢复旧链接，并回到迁移前项目 commit。

升级 Skill 与迁移项目是两个授权动作。切换全局安装不代表允许迁移所有项目；迁移一个项目也不代表允许派发、合并、部署或发布。

维护者从干净、已提交的 Skill tree 构建发布：

```bash
python3 tools/build_skill_release.py \
  --source skills/agentdesk \
  --destination ~/.local/share/agentdesk/releases/<release-id> \
  --release-id <release-id>
```

构建器拒绝 Skill 目录内的 tracked/untracked 改动、符号链接和易变缓存文件；它将 commit、稳定 release ID 和按文件字节复算的 bundle digest 写进复制后的 manifest，再把整个发布目录设为只读。

先验证原子切换，再执行：

```bash
python3 tools/activate_skill_release.py \
  --release-directory ~/.local/share/agentdesk/releases/<release-id> \
  --expected-release <release-id> \
  --dry-run

python3 tools/activate_skill_release.py \
  --release-directory ~/.local/share/agentdesk/releases/<release-id> \
  --expected-release <release-id>
```

激活器会重新计算摘要、拒绝可写或含符号链接的发布包，并只替换 `~/.codex/skills/agentdesk` 这个链接；它不会迁移任何项目。已经打开的会话可能仍缓存旧 Skill，因此切换后应新开验证会话，活动 attempt 仍按原锁完成。

## 当前仓库的版本状态

- `v0.1.0-beta` 属于没有项目锁的历史发布，现有项目按 `legacy-unpinned` 处理；
- `0.2.0-dev` 是版本隔离候选，不应作为已验证发布包投入项目写入；
- 只有发布清单中的 `source_commit` 与 `bundle_sha256` 均由正式打包流程写入后，候选包才具备通过写门禁的基础。
