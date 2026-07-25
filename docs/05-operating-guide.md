# 项目运行手册

AgentDesk 中有两类容易混淆的概念：

- **采用级别**：Lite、Standard、Automated；
- **当前职责**：Initialize、PM、Worker、QA、Integration、Validate、Recover。

本页描述项目进入日常运行后各职责的工作顺序。

## 执行前：可选咨询

咨询不属于 PM 任务状态机。Leader 可以在需求冻结和生成任务卡之前判断是否需要 0–3 席 advisory roundtable：

- 0 席：跳过咨询，不创建会话；
- 1 席：一个边界清晰的专业核查；
- 2 席：需要两个互补或相互挑战的视角；
- 3 席：跨产品、技术、风险等多领域且不确定性较高。

真实咨询运行创建 1–3 个独立咨询会话，第一轮相互隔离，全部经 Leader 中心辐射，最多两轮。每席必须主动回调 Leader。Leader 将结果区分为共识、条件共识、分歧和少数意见，不按票数裁决。

在 Owner 明确确认结论之前，Leader 不得把咨询意见写成正式决策，也不得生成任务卡。Owner 的这次确认只覆盖“写决策/生成任务卡”，不覆盖执行岗位会话、实施、合并、部署或发布；后续仍走各自的独立授权门禁。操作提示词、命名和恢复方法见[咨询与圆桌研讨](11-consultation-and-roundtables.md)。

每次真实咨询使用 `docs/pm/consultations/CNNN/consultation.yaml` 保存该次 advisory 生命周期；真实咨询 task/thread、标题核验和传输回执只进入 gitignored `.agentdesk/runtime/consultations.yaml`。两者都不能覆盖 `tasks.yaml` 的执行状态。

## PM / Leader 工作流

### 1. 接管现场

1. 确认当前 PM lease holder 和 `lease_epoch`；
2. 读取 `tasks.yaml`、当前任务卡、未处理 outbox/inbox、交付报告和阻塞；
3. 对照 Git refs、worktrees、routes 和 receipts；
4. 发现双活、漂移或孤儿 attempt 时先冻结新派发。

### 2. 准备任务卡

Leader 将需求拆成最小可验收任务，并冻结：

- `task_id` 与 `revision`；
- 目标岗位；
- 风险和模型硬要求；
- 依赖、路径边界与冲突面；
- `base_commit`；
- 必跑检查；
- Owner approval gate；
- attempt 专属报告路径。

任务卡和当时的 `ROLE-POLICIES.yaml` 必须同时存在于 `task_card_commit`。该提交必须在当前 HEAD 的祖先链；派发提交产生后，还必须是首次 dispatch commit 的祖先，不能用 detached/orphan 提交充当冻结证据。

### 3. 验证岗位与运行时

派发前必须验证：

- PM 来源任务真实存在且 callback route 可用；
- Worker 是不同的真实岗位任务；
- 标题精确等于 `<role_no> . <role_name>`；
- worktree 独立、可达，HEAD 等于 `base_commit`；
- 该岗位没有另一个活动 attempt；
- 新建岗位任务已获得明确用户授权。

### 4. 选择执行模型

使用完整 `task_card_commit` 运行 `select_model.py`。选择结果是九字段不可变快照，原样进入账本、outbox 和 Worker report。

选择器是 guard，不是模型启动器。运行时如果无法实际执行选中的 provider、模型和推理档位，应保持 Ready/Blocked，不能只凭字段声称已经路由成功。

### 5. 原子提交派发意图

同一个 Git commit 中首次加入：

- `current_dispatch`；
- `TASK_DISPATCHED` event；
- `task.dispatch` outbox；
- 更新后的 `tasks.yaml`；
- 重新生成的 `BOARD.md` / `STATUS.md`；
- 必要的模型降级批准 event。

strict validation 通过后，再向 verified Worker 任务真实发送消息并记录 receipt。

### 6. 接收交付

Worker 回调只负责唤醒 PM。PM 仍需：

1. 校验 callback ID、dispatch、attempt 和 revision；
2. 确认 callback receipt 与来源/目标 route；
3. 从 `report_commit` 读取报告；
4. 验证实现与报告两个提交；
5. 将完整交付迁移到 `ReviewReady`。

### 7. 独立验收

PM 根据冻结 `base_commit` 审查精确 diff、关键源码、路径边界、检查结果、审批和风险，写入不可变 acceptance record。

- 通过：进入 `Accepted`；
- 需要返工：进入 `Returned`，关闭旧 attempt；
- 事实不充分或外部条件缺失：进入 `Blocked`。

### 8. 集成并解锁

`Accepted` 不代表共享基线已经包含成果。合并、cherry-pick、rebase 或等价应用完成并验证后，记录 `integrated_commit`，任务才进入 `Integrated`，后继任务才默认解锁。

## Worker 工作流

### 接卡

Worker 先核对：

- 当前真实任务身份和规范标题；
- `task_id + revision + attempt + dispatch_id`；
- `task_card_commit` 与 `base_commit`；
- worktree HEAD；
- allowed/blocked paths；
- 九字段模型快照；
- delegation `source_thread_id` 与 verified PM route。

任何字段陈旧或冲突都应停止并回报，不能猜“最新版”。

### 执行

- 只修改授权业务路径；
- 控制面默认只读；
- 使用 `CHECKS.yaml` 中的受控检查；
- 契约、迁移或范围需要变化时先请求 Leader；
- 不把实质工作暗中交给未记录的更弱子 Agent；
- 不使用模型强度绕过审批或外部权限。

### 双提交交付

1. 提交实现和测试，得到 `implementation_commit`；
2. 创建 attempt 专属 report，引用实现提交；
3. 提交报告，得到 `report_commit`；
4. 将两个提交发布到 PM 可读取的位置；
5. 使用固定 `callback_id` 主动向 delegation source 回调；
6. 保存符合当前 beta schema 的 `status: received` callback receipt；它只证明运行时适配器记录了接收结果，不证明 PM 已经阅读或处理。

只在 Worker 任务中输出“完成”不算回调。只有报告、没有 receipt 属于漏回调恢复场景。

需要更强的端到端证据时，PM 还应读取该消息或返回匹配 ACK，再开始验收；发送工具返回成功不能被解释为 PM 已完成处理。

## QA 工作流

QA 使用独立任务卡、worktree 和报告：

1. 接收冻结的候选提交；
2. 根据验收标准和风险设计测试矩阵；
3. 记录环境、命令、结果、失败分类和证据；
4. 输出建议通过、建议返工或无法判定；
5. 主动回调 PM。

QA 可以阻止质量门变绿，但不能代替 PM 写最终 `Accepted`。

## Integration 工作流

1. 只接收 PM 明确列出的 accepted commits 和目标基线；
2. 使用专属 integration branch/worktree；
3. 按指定顺序合并或重放；
4. 只解决任务卡授权的冲突；
5. 运行跨模块检查和必要 E2E；
6. 输出来源提交、冲突决定、测试和最终提交；
7. 由 PM 写入 `Integrated`。

## Heartbeat 的位置

Heartbeat 是漏通知后的兜底：检查 route、receipt、Git ref、report 和 worktree，重放同一传输或提醒 PM。它不能自动实现、验收、合并、发布或创建第二个 attempt。

## 必须由用户或 Owner 决定的事项

- 是否创建具体的独立咨询席会话；
- 是否确认咨询结论并允许记录决策或生成任务卡；
- 创建新的用户可见岗位任务；
- 产品范围和重要取舍；
- L3/L4 高风险动作；
- 凭据、生产环境和不可逆外部操作；
- 合并、部署和发布；
- 无法从证据恢复的冲突事实。

这些授权彼此独立。例如，授权创建咨询席不等于确认咨询结论；确认咨询结论不等于授权创建执行岗位任务；允许创建岗位任务不等于允许实施或合并；允许合并也不等于允许部署或发布。Leader 不得从前一项授权推定后一项授权。

状态和 ID 的完整含义见[任务、状态与证据](06-task-state-and-evidence.md)。
