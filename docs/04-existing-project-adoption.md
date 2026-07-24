# 既有项目接入

AgentDesk 不要求项目从第一天就使用它。一个已经开发完成、现在准备增加新功能的项目，可以从“当前稳定状态”开始建立新的协作事实链，而不需要伪造过去的任务卡、会话和验收记录。

## 先判断项目处于哪种情况

### 尚未初始化 AgentDesk

运行 Initialize 的 dry-run，审计现有仓库并建立控制面；如果仓库含有旧版 AgentDesk 文件，再单独进行人工升级对照。

### 已经初始化 AgentDesk

先检查 `docs/pm/AGENTDESK.lock.json`。有 lock 时，使用对应不可变发布包验证现有账本、岗位路由、回执、worktree 和活动 attempt；没有 lock 时项目属于 `legacy-unpinned`，新版本只能只读盘点。不要把初始化器当作自动升级器反复运行，出现不一致时先停止写入并进入 Recover 或显式版本迁移。

### 需求已经确定

可以跳过产品方向探索，但不能跳过 Definition of Ready、任务卡冻结、岗位授权、模型 guard、真实派发和运行时验证。

## 推荐接入流程

### 1. 只读盘点

检查：

- 仓库根目录和当前分支；
- 工作树是否干净；
- 现有 `AGENTS.md`、`docs/pm/` 或同类项目管理文件；
- 构建、测试、lint、typecheck 配置；
- 当前稳定基线和未合并分支；
- 现有角色、审批和发布流程。

不要在盘点阶段改业务代码，也不要根据旧聊天猜测历史状态。

同时记录当前 Skill 的 `release-manifest.json` 和项目的 `AGENTDESK.lock.json`。如果后者不存在，不要根据当前安装版本反推或补写历史版本。

### 2. 运行 dry-run

```bash
python3 <skill-dir>/scripts/init_project.py \
  --project <repo> \
  --mode standard \
  --dry-run
```

如果输出 `MANUAL_MERGE`，优先复用现有文件。不要在已经存在的控制面旁边新建另一套 `docs/pm/`。

需要特别注意：dry-run 对普通既有文件通常只输出 `EXISTS`。对于已有 AgentDesk marker、但没有版本锁的项目，它会报告 `legacy-unpinned` 并返回非零；不带 `--dry-run` 的执行也会在写入前失败，避免把新旧控制面混在一起。

### 2.1 对 legacy-unpinned 做显式认领

先暂停新派发，让活动 attempt 在旧流程下关闭或明确终止。然后从准备采用的不可变
Stable bundle 运行零写入计划：

```bash
python3 <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --dry-run
```

计划会检查 Git 根、HEAD、工作树、AgentDesk markers、项目 ID、活动 attempt 和
当前 bundle 的 commit/digest。development 源码可以输出诊断，但不能 apply。

Owner 审查并明确接受“历史 Skill 身份无法证明”后，才执行：

```bash
python3 <skill-dir>/scripts/adopt_legacy_project.py \
  --project <repo> \
  --apply \
  --acknowledge-legacy-unpinned
```

命令会独占创建首个 lock，并调用同一 bundle 的初始化器只补缺失文件。它不会覆盖
现有文件，也不会把旧聊天或时间戳伪造成历史证据。initializer 后续失败时不要删除
lock；按输出的恢复命令用同一 bundle 续跑。

该入口只适用于当前 Schema 已被候选 Reader 完整理解的首次版本认领。若需转换旧
Schema，或项目已经存在另一版本 lock，应单独实现并审查跨版本 migration，不能使用
此命令强行升级。

完全未接入 AgentDesk 的业务仓库在审查 dry-run 后，可以运行不带 `--dry-run` 的正式初始化：

```bash
python3 <skill-dir>/scripts/init_project.py \
  --project <repo> \
  --mode standard
```

已有 Git 仓库不要加入 `--init-git`。如果这是 `legacy-unpinned` 项目，不要执行该正式初始化命令，而应先制定版本迁移。正式初始化只适用于全新接入或同一锁定发布的幂等续跑，也不等于授权创建用户可见岗位任务、派发任务或修改业务代码。

### 3. 固定增量开发基线

选择本次新功能真正依赖的稳定 commit 作为 baseline。AgentDesk 不会把“当前分支名”当成冻结基线，也不会自动把过去未记录的开发过程改造成虚假证据。

### 4. 把新需求映射为任务卡

任务卡应包含：

- 可观察结果；
- 允许和禁止范围；
- 可验证验收标准；
- 依赖关系；
- 不可变 `base_commit`；
- 风险等级与审批门；
- 冲突面；
- 受控检查 ID；
- 所需岗位与模型能力。

### 5. 提出最小岗位集合

只启用本次增量需要的岗位。已有项目并不意味着必须一次创建产品、前端、后端、QA、运维等全部岗位任务。

### 6. 单独取得任务创建授权

初始化和开发批准不等于允许创建新的用户可见 Codex 任务。Leader 应列出精确标题与数量，用户确认后才创建。

这项授权也不自动包含后续合并、部署或发布；各外部状态变更仍需按风险门分别确认。

### 7. 从第一张新卡开始闭环

旧历史保留原样。新功能从第一张 AgentDesk 任务卡开始使用派发、双提交、主动回调、PM 验收和集成证据。

## 推荐提示词：已有项目增加功能

```text
当前是已经开发完成的 Git 项目，现在要增加新功能。
使用 $agentdesk 以 Standard 模式接入本次增量开发。

先只读审计并执行初始化 dry-run，同时人工对照当前协议：
- 复用已有 AGENTS.md 和 docs/pm
- 不修改业务代码
- 不导入或伪造旧开发历史
- 不创建用户可见岗位任务

请输出需要合并的控制面文件、本次功能所需的最小岗位集合，
以及精确岗位任务名称，等我确认后继续。
```

## 推荐提示词：需求已经确定

```text
本项目需求已经确认，需求基准位于 <文件或链接>。
使用 $agentdesk 处理当前项目：

- 不重新改变产品范围
- 将现有需求映射为可验收任务卡、依赖关系、风险等级和受控检查
- 只针对缺失的执行事实提出问题
- 提议最小岗位集合和模型等级
- 创建用户可见岗位任务前停下来征得我确认
- 未满足 DoR 的任务保持 Draft 或 Blocked
```

## 推荐提示词：继续已初始化项目

```text
使用 $agentdesk 的 PM 模式继续当前项目。
先运行版本兼容检查，再验证 tasks.yaml、当前任务卡、岗位路由、transport receipts、
worktrees 和活动 attempt。

不要重建已有任务卡，不要因为回调缺失创建新 attempt；
发现不一致时先进入 Recover。
```

如果兼容检查显示 `legacy-unpinned` 或 release 不匹配，保持只读，不要通过删除 lock、重跑初始化器或修改版本字符串绕过。

## 常见误区

### 直接在对话里说“使用 Skill 开始开发”

这不足以建立 Standard 闭环。项目仍可能缺少控制面、用户授权、真实岗位任务、规范标题、派发回执和回调目标。

### 只创建 worktree，不创建岗位任务

worktree 提供代码隔离，但不是可寻址的岗位会话。Standard 要求两者同时存在并绑定。

### 为旧历史补写“看起来完整”的证据

无法证明的历史保持未知。需要严格证据时，从新的 revision / attempt 重派，而不是伪造旧模型、会话、回调或验收字段。

### 需求确定后直接进入 InProgress

需求确定只解决了规格问题。依赖、基线、岗位、模型、风险、路径和运行时 route 仍需通过派发前门禁。

## 分阶段采用建议

1. 先建立任务卡、Git SHA 和 PM 验收；
2. 再建立真实岗位任务和独立 worktree；
3. 跑通派发与主动回调；
4. 加入 repository/runtime 双重校验；
5. 最后才考虑 heartbeat、动态看板或 Automated 基础设施。
