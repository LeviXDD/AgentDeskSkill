# AgentDesk 术语表

| 术语 | 含义 |
| --- | --- |
| AgentDesk | 任务卡驱动的多 Agent 开发协议及其 Codex Skill 实现 |
| Consultation / 咨询 | 执行前可选、只读、与任务状态机正交的 advisory workflow |
| Consultation seat / 咨询席 | 某次咨询中临时创建的独立建议会话；不属于 Active 执行岗位 |
| Consultation record / 咨询记录 | `docs/pm/consultations/CNNN/consultation.yaml`；该次 advisory 生命周期的公开权威记录，但不能驱动执行状态 |
| Round / 轮次 | Leader 向所有有效咨询席发出问题并收集主动回调的一轮；V1 最多两轮 |
| Hub-and-spoke / 中心辐射 | 咨询席只与 Leader 通信，席位之间不直接交换意见的结构 |
| `CONSULTATIONS.md` | 从公开 consultation records 生成的动态只读投影视图；不是决策、授权、`tasks.yaml` 或执行状态的权威来源 |
| PM / Leader | 控制面负责人；唯一可以裁决任务状态和验收的人或自动化任期 |
| Worker | 接收一张任务卡、在独立 worktree 执行并主动回调的岗位任务 |
| Role | 稳定岗位身份，由 `role_no + role_id + role_name` 组成 |
| Task / Thread | Codex UI 称“任务”，运行时工具使用 thread；在运行时适配器中指同一对象 |
| Task card | 某一 revision 的不可变工作规格 |
| `task_id` | 一项工作稳定且不复用的 ID |
| `revision` | 任务规格版本；语义变化时递增 |
| `attempt` | 同一规格的一次执行；返工或执行重启时递增 |
| `dispatch_id` | 一次逻辑派发的幂等 ID |
| `event_id` | 控制面状态迁移、批准或撤销事实的 ID |
| `message_id` | PM outbox 消息的 ID |
| `callback_id` | Worker 回调的幂等 ID |
| Outbox | PM 已提交、允许安全重放的发送意图；不证明消息已送达 |
| Receipt | 运行时记录的真实传输结果 |
| Callback | Worker 完成或受阻后主动通知来源 PM 任务 |
| PM lease | 控制面写权限任期；用 holder 和递增 epoch 防止双活 |
| Fencing token | 单调递增 `lease_epoch`，使旧 PM 失去写权限 |
| `base_commit` | 本次执行不可变 Git 基线 |
| `implementation_commit` | Worker 交付并供 PM 审查的实现提交 |
| `report_commit` | 在实现提交之后新增交付报告的提交 |
| `accepted_commit` | PM 实际审查并接受的实现提交 |
| `integrated_commit` | 目标共享基线已经包含验收成果的提交 |
| Accepted | PM 已验收，但成果未必进入共享基线 |
| Integrated | 成果已经进入目标基线，可默认解锁后继任务 |
| Worktree | 与某个 attempt 绑定的独立 Git 工作目录 |
| Route | 逻辑岗位到真实 task/thread、host 和 worktree 的本机绑定 |
| `verified` | route 已通过真实任务、标题、host 和 worktree 核验 |
| Heartbeat | 只负责发现差异、重试传输和提醒 PM 的兜底对账机制 |
| Repository validation | 对账账本、event、outbox、Git 历史和版本化证据 |
| Runtime validation | 对账真实岗位 route、精确标题和 transport receipts |
| Lite | 不宣称自动多任务闭环的人工/受限运行方式 |
| Standard | 具备真实独立岗位任务、worktree、派发/回调和双重校验的运行方式 |
| Automated | 在 Standard 上增加常驻调度、续租、重试、监控和恢复的目标成熟度 |

Owner 对咨询结果的确认，仅允许 Leader 记录对应决策或生成任务卡。它与咨询席创建授权、执行岗位任务创建、实施、合并、部署和发布授权相互独立。
