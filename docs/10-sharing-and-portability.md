# 公开分享与平台兼容

## GitHub 分享结构

AgentDesk 的公开仓库应保持以下边界：

```text
README.md                 # 对外入口
docs/                     # 给人阅读的用户文档
skills/agentdesk/         # 可安装 Skill 运行包
tests/                    # 发布和隐私校验
LICENSE
```

用户文档放在仓库 `docs/`，不塞入 `skills/agentdesk/`。Skill 包只保留 Agent 执行所需的 `SKILL.md`、references、scripts、templates 和 UI metadata，避免安装后占用不必要上下文。

## 安装固定版本

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo LeviXDD/AgentDeskSkill \
  --path skills/agentdesk \
  --ref v0.1.0-beta
```

这条命令安装的是旧版公开基线；`v0.1.0-beta` 早于项目版本锁、不可变发布包和圆桌
咨询。公开分享时推荐使用真实存在的 tag，而不是要求用户始终安装移动的 `main`。
开发分支文档描述的下一版本能力不会自动出现在旧 tag 中。

下一版本的源码包故意保留 `development` 身份。即使通过 Skill installer 从 GitHub
下载，它也只能用于代码审阅、测试和只读影子校验；没有经过发布构建器写入 commit
与摘要时，不得初始化生产项目或执行 Writer 操作。

## 构建并激活下一版本

维护者发布 tag 后，用户可以从该固定 tag 本地构建可验证发布包。`<release-tag>`
必须替换为真实存在的 tag，且 clone 后工作树保持干净：

```bash
git clone --depth 1 --branch <release-tag> \
  https://github.com/LeviXDD/AgentDeskSkill.git
cd AgentDeskSkill

python3 -m unittest discover -s tests -p 'test_*.py' -v

python3 tools/build_skill_release.py \
  --source skills/agentdesk \
  --destination ~/.local/share/agentdesk/releases/<release-tag> \
  --release-id <release-tag>

python3 tools/activate_skill_release.py \
  --release-directory ~/.local/share/agentdesk/releases/<release-tag> \
  --expected-release <release-tag> \
  --dry-run

python3 tools/activate_skill_release.py \
  --release-directory ~/.local/share/agentdesk/releases/<release-tag> \
  --expected-release <release-tag>
```

构建器只接受 Git 已跟踪、已提交且内容干净的 Skill 文件，拒绝符号链接、缓存和被
Git 忽略但实际存在的额外文件；它把规范化发布清单也纳入 bundle 摘要。激活器会
重新计算摘要、检查只读属性，并原子切换 `~/.codex/skills/agentdesk` 软链接。

如果该目标目前是 Skill installer 创建的普通目录，激活器会拒绝覆盖。先用受支持的
Skill 卸载流程移除旧安装，或在隔离的 Codex home 中验证新发布；不要让脚本静默
删除旧目录。仍有项目固定在旧发布时，必须保留对应不可变 bundle。

上述命令只有在真实 tag 发布后才是正式安装路径。对尚未发布的 PR/branch，可以用
同样流程构建本机 canary，但 release ID 应明确标记为候选，且不得宣称它是仓库的
正式公开版本。beta 阶段 schema 可能变化，升级前应阅读 release notes，并先在非
关键仓库执行 dry-run、compatibility check 和 validation。

## 安装后验证

1. 重新加载 Codex；
2. 确认 `$agentdesk` 出现在 Skill 列表；
3. 在测试仓库执行只读检查或初始化 dry-run；
4. 检查生成 diff，不直接在重要仓库接受未知修改；
5. 验证 `.agentdesk/runtime/` 被 Git 忽略。
6. 对已有项目运行 `check_compatibility.py --operation read`；写入前再运行 `--operation write`。

## 旧名称迁移

早期本地版本可能仍叫 `$task-card-agent-dev`，公开包使用 `$agentdesk`。迁移时：

1. 盘点本机两个 Skill 是否同时存在；
2. 备份旧版本中仅存在于本机的改动；
3. 安装固定版本的 `$agentdesk`；
4. 在测试仓库比较初始化和校验结果；
5. 停止让旧、新两个 Skill 同时操作一个项目；
6. 确认迁移完成后再禁用或移除旧版本。

不要通过直接重命名已初始化项目中的 schema、任务状态或历史证据来完成 Skill 改名。

`v0.1.0-beta` 项目没有版本锁，升级后的安全身份是 `legacy-unpinned`，而不是“自动等于当前版本”。它们可以只读审计，但必须经过显式迁移才能由新发布写入。完整规则见[版本隔离与安全升级](12-version-isolation-and-upgrades.md)。

## 哪些部分是跨平台的

AgentDesk 的核心协议本身不绑定 Agent 厂商：

- 任务卡、revision 和 attempt；
- Git 不可变基线；
- 独立岗位会话和 worktree；
- event / outbox / receipt；
- 主动回调；
- PM 验收和 integration gate；
- 厂商无关的模型等级和能力要求；
- 恢复与幂等规则。

这些概念可以在提供 CLI、API、MCP 或其他可寻址会话接口的平台上实现。

## 哪些部分当前依赖 Codex

公开 beta 内置的是 Codex 运行时适配协议。Standard 依赖 Codex Desktop 提供：

- 项目与任务枚举；
- 任务创建；
- 精确改名；
- 任务读取和存在性复验；
- 跨任务消息；
- 可验证的 provider 结果。

脚本本身不会创建 Codex 任务，也不会启动选中的模型；执行这些动作的是加载 Skill 的 Codex 运行时。

## 外部 Agent 平台

Claude Code、Gemini CLI、Cursor Agent、OpenCode 等平台理论上可以通过 adapter 接入，但当前仓库尚未内置这些 adapter。

一个兼容 adapter 至少需要实现：

```text
launch / bind
rename or identify
read / verify
send
status
collect_result
cancel
callback
receipt
```

推荐保留一个 Codex 可见的岗位包装任务，再由它启动外部执行器。这样可以继续保留岗位标题、Leader 回调和 Codex 侧边栏可见性，同时将外部平台 session ID 作为运行时身份保存。

## Automated 的真实边界

`Automated` 是成熟度目标，不是安装后自动获得的一套常驻服务。当前仓库没有完整提供：

- 自动 heartbeat daemon；
- lease renewal 服务；
- retry/dead-letter worker；
- orphan detector；
- 指标和告警后端；
- 多平台 dispatcher；
- 实时网页看板。

项目只有在这些组件真实存在、经过验证，并仍保留 Standard 的独立会话、主动回调和 receipt 门禁时，才能宣称 Automated。

## Beta 已知一致性问题

当前公开 beta 仍有几处规范与实现没有完全统一：

| 问题 | 当前安全解释 |
| --- | --- |
| `SKILL.md` 允许 Lite 经授权同任务切岗，而项目模板禁止任何同任务切岗 | 不在一个会话中切换 PM 与 Worker 权限；缺少独立任务时使用明确人工交接并标记 Lite |
| DoR 要求 route/授权齐备才进入 Ready，但 `SKILL.md` 要求缺授权时保持 Ready | 可以把 Ready 理解为规格已就绪；所有 runtime 与授权事实必须在 `Ready → Dispatched` 前补齐 |
| 初始化器被描述为 Initialize / Upgrade | 它是保守脚手架，不比较普通既有文件；升级必须人工对照协议、模板和 release notes |
| `tasks.yaml` 参考示例使用普通 YAML，而脚本调用 JSON 解析器 | 只从初始化器生成的 JSON 格式文件修改，不复制该示例 |
| `--require-runtime` 被描述为本地闭环判定 | 它只附加当前实现的活动态检查；终态历史、零活动任务和真实 provider 现场状态仍需人工复验 |
| receipt 示例允许 `provider_receipt: null`，校验器拒绝 null | 当前使用非空、非占位的 provider 结果 |
| 固定 `list_projects → create_thread` 链与部分 Codex 宿主版本的工具契约不同 | 运行前做 capability discovery；异步创建必须等待完成，再改名和读取复验 |
| callback `received` 和 callback ID 生命周期的定义不够完整 | `received` 不等于 PM 已处理；一个 attempt 只发送一个不可变回调事实，传输重试复用 ID/payload |
| strict validator 未普遍验证每一种状态迁移的完整 event 链 | 全绿只表示已实现规则通过；高风险项目仍人工核对迁移历史 |

这些差异修复前，不应从多个文件中挑选最宽松的解释，也不应把校验器尚未检查的事实写成“已被证明”。

## 隐私与发布检查

公开发布前至少检查：

- 没有真实 thread/session ID；
- 没有本机绝对路径；
- 没有 transport receipts；
- 没有凭据、token 或生产配置；
- 示例 provider/model 使用占位符或公开稳定名称；
- runtime 目录始终 gitignored；
- 安装、初始化、校验脚本在干净环境通过；
- README 中明确 beta 状态和权限边界。

本仓库以 MIT License 发布。公开许可证允许使用和修改，但不替代使用者对生成改动、模型费用、外部平台条款和生产风险的审查责任。
