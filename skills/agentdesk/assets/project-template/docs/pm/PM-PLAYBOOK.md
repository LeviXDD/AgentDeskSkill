# PM 操作手册

## 不可省略的会话约定

- PM/Leader 与 Worker 必须是真实、可独立寻址的会话。逻辑岗位、分支、worktree、排队中的任务或同会话角色提示词，都不等于 Worker 会话。
- 不得通过把 PM 会话切换成某岗位来执行 Worker 任务卡。同一 Worker 会话不得复用为两个岗位身份。
- 每个会话的可见标题必须精确等于 `ROLES.md` 中的 `role_no . role_name`。重命名自动生成的标题，从平台读取标题复核，只在 `.agentdesk/runtime/routes.yaml` 中记录已核验绑定。
- 任务卡和报告只包含逻辑岗位 ID 和回调意图。主机 ID、线程 ID、worktree 绝对路径、provider 回执、重试计时器和核验时间戳只放在被 Git 忽略的 `.agentdesk/runtime/` 中。
- 平台无法创建、改名、寻址独立会话或向其回调时，派发前停止。说明缺少哪项能力，不得静默降级为同会话执行。

## 用户确认门禁

为新项目或已有项目创建首个 Worker 会话前：

1. 理解需求，提出最少必要岗位，列出 `role_no`、`role_id`、`role_name`、精确 `expected_thread_title`、职责和启用理由。
2. 告知用户每个 Active Worker 都是可见独立会话，说明将创建或重新绑定的会话数量。
3. 宿主要求用户授权时，就岗位分配及独立会话的创建/复用取得明确确认。
4. 将已确认岗位写入 `ROLES.md`，不启用未确认岗位。

初始化控制面、创建任务卡或收到“开始开发”等一般指令，都不能证明岗位会话已存在，也不能豁免此门禁。已开发仓库接入 Skill 后，首次新功能派发前同样需要通过此门禁。

## 咨询门禁

咨询是独立的只读协作路径，不使用 `tasks.yaml`、执行派发、Worker 路由、交付报告、验收或集成状态。咨询席不是 Active 岗位，不得加入 `ROLES.md`。

创建任何咨询会话前：

1. 判断额外视角是否会实质改善结果。采用最小有效组合：一个专业缺口用一席，真实取舍用两席，高风险或跨领域问题才用三席，最多三席。
2. 冻结一个精确议题、简短议题标签、讨论边界、各席不同的关注点，以及一到两轮的上限。
3. 用 `<consultation_id>.<seat_no> . <seat_name>｜<short_topic>` 提出每个精确可见标题，例如 `C001.01 . 产品顾问｜会员权益`。
4. 只读探测宿主能否创建、改名、读取/列出、发送消息和等待独立可见会话，以及这些会话能否主动向发起咨询的 Leader 任务回调。能力探测不是创建授权。
5. 取得针对精确标题的 `explicit_batch` 授权，或明确授予的 `scoped_auto` 授权；后者必须限定当前精确议题、可见会话数量上限、最多两轮和只读操作。仅当 User / Owner 明确委托在这些边界内选席时，`scoped_auto.authorized_titles` 才可为空；非空时必须与创建的标题完全一致。一般性的分析、初始化、开发或 Skill 升级批准不足以授权。
6. 先将 `docs/pm/consultations/<consultation_id>/consultation.yaml` 持久化为 `awaiting_authorization`，授权为 null，仅包含计划席位且没有轮次。获授权后记录稳定的 `CA-*` ID 和精确边界。不得包含主机 ID、线程 ID、本地绝对路径、实际回调 ID、回执、provider 路由、凭据或 token。公开 response 的 `callback_id` 保持 `null`；稳定 `response_id` 使用 `<seat_id>.R<round>`。
7. 以只读引导消息创建所有已批准席位，设置精确标题并读取/列出复核。真实路由和传输事实只保存在被 Git 忽略的 `.agentdesk/runtime/consultations.yaml`。
8. 向所有席位发送相同的不可变第一轮简报，只附加各席已声明的关注点。第一轮结束前，不泄露其他席位的回答。
9. 要求每席向委派来源主动发送结构化回调。席位不得互相发消息、创建会话、修改文件、提交、改变控制状态、实现、验收、合并、发布、部署或产生外部副作用。
10. 只有可能改变建议的实质分歧，且原授权允许时，才使用第二轮，并复用原席位。不存在第三轮。
11. 综合共识、条件性共识、分歧、少数意见、假设、残余风险和 Leader 建议。不以多数票作为决策规则，披露缺席和回调降级。
12. 将综合结果标为 `proposed`，就精确范围取得 User / Owner 明确确认。只有已确认范围才能产生决策日志记录或任务卡。决策记录引用咨询/确认 ID；派生任务卡填写 `source_consultation_id` 和 `source_confirmation_id`，并在 `derived_refs` 列出路径。之后仍需分别通过普通执行岗位、可见会话、派发、验收、集成、合并、发布和部署门禁。

主动回调不可用，但有界等待/读取可用时，只能将后者作为已披露的轮询兜底来恢复回答，不能证明主动回调闭环。独立会话创建、精确命名、核验或安全只读执行不可用时，停止或取得明确同意，采用清楚标注的仅当前对话/Lite 替代方式，不得声称完整咨询闭环成立。

咨询席可以有厂商无关的 `minimum_tier`、`deliberation_tier` 和 `required_capabilities`，但不能假定 `create_thread` 能强制执行。User / Owner 未选定具体模型时，不得仅因咨询记录含等级要求就覆盖宿主模型。宿主未提供实际模型证据时，本地 `model_binding` 应标为默认/覆盖配置未验证；模型证据只放在运行时。

## 开始工作

1. 确认逻辑 PM 持有人和 `lease_epoch`。
2. 读取 `state/tasks.yaml`、`ROLES.md`、相关任务卡、决策、报告和验收记录。
3. 在 `.agentdesk/runtime/routes.yaml` 绑定当前 PM/Leader 会话：填写真实本地路由，将可见标题设为 `PM . 项目经理`，`actual_title` 填写观察到的标题，只有平台查询成功后才标记 verified。
4. 对照平台核验 Active Worker 路由。缓存的线程 ID 或已存在的 worktree 都不够；派发前重新核验可达性、身份、标题和 worktree 归属。
5. 运行仓库严格校验。岗位派发前还需运行 `python3 -I <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id>`；仓库校验不能代替运行时门禁。

## 绑定岗位会话

用户确认后、该岗位首次派发前执行：

1. 新建独立岗位会话，或选择此前已确认、专用于该精确岗位的会话。它必须不同于 PM 和其他岗位会话。
2. 分配任务卡的独立 worktree，并只将该 worktree 绑定到本岗位 attempt。
3. 将可见会话重命名为 `ROLES.md` 中精确的 `expected_thread_title`。
4. 通过宿主重新读取会话，核实其存在、可寻址、未归档、标题正确且关联预期 worktree。
5. 使用 `agentdesk.routes/v2` 在被 Git 忽略的 `.agentdesk/runtime/routes.yaml` 中添加或更新岗位条目，记录 `role_no`、`role_name`、`expected_title`、transport、主机/线程绑定、实际观察到的 `actual_title`、worktree、`status: verified` 和 `verified_at`。
6. 断言所有非 null 的 `thread_id` 唯一，且 Worker 路由不同于 PM。占位 ID、推测 ID、`status: unbound`、标题不匹配或只有 worktree 而无真实线程，均不能通过。
7. 运行 `python3 -I <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id>`。实时平台查询后的校验未通过前，不派卡。

任何本地路由字段都不得写入 `ROLES.md`、任务卡、任务账本、事件、outbox、报告、验收或 Git 历史。

## 派发任务卡

1. 冻结一个可观察结果、岗位、revision、基线提交、路径、依赖、检查、风险、模型下限/能力、审批门禁和上下文预算。
2. 通过 Definition of Ready，包括用户确认门禁，以及身份、标题、worktree 全部匹配的当前已核验岗位路由。未通过时保持卡片 `Ready`，不得在 PM 会话中冒充该岗位。
3. 分配下一 attempt、唯一 `dispatch_id`、worktree、分支和岗位租约；使用完整 `task_card_commit` 运行 selector。
4. `require_pm_approval` 选择低于首选等级时，分配全仓唯一的 `APR-*`，加入任务的 `granted_approval_ids`，并在派发提交或其祖先提交中创建范围明确的 `MODEL_DEGRADATION_APPROVED` 事件。它必须匹配 task/revision/attempt 和所选/首选等级，由 PM 授予，在派发时未过期、未被独立撤销，且保留 `revoked_at: null`。最多通过一个严格后代提交中的 `MODEL_DEGRADATION_REVOKED` 撤销，撤销时间/epoch 不早于批准。
5. 将 selector 的精确九字段对象复制到账本 `current_dispatch.model_selection` 和 outbox `model_selection`；不得缺少、增加或改名键，数组保留 selector 输出顺序。
6. 完成不可变 `task.dispatch` outbox，再对其仓库 UTF-8/LF 原始字节计算摘要，写入匹配的 `TASK_DISPATCHED.payload_digest`。
7. 渲染视图并运行 `validate_project.py --pre-commit`，仅将其视为结构预检，阅读警告并审查限定范围的 diff。
8. 在同一个提交中首次加入 `current_dispatch`，同时新增唯一匹配的派发事件/outbox、更新的状态/视图，以及同提交的审批事件（如有）。事件的 `lease_epoch` 必须为 >= 1 的整数；同提交的审批和派发 epoch 相同。
9. 不带 legacy 参数运行严格校验，并检查视图。
10. 通过已核验运行时路由解析 `destination_role_id`，把自包含 outbox 消息投递到该精确独立会话。消息必须说明预期岗位身份/标题、完整冻结任务卡、worktree/分支/提交事实、逻辑回调目标 `PM`，以及禁止修改控制面状态。
11. 在 `.agentdesk/runtime/transport-receipts.yaml.dispatch_receipts` 中按原 `message_id` 记录 provider 结果。成功条目必须证明已投递的派发和逻辑岗位、实际运行时目标、provider 状态/回执及投递时间。不得将回执复制到 Git。
12. 要求已绑定 Worker 会话回传包含 `task_id + revision + attempt + dispatch_id` 的接单确认，之后才能转为 `InProgress`。传输回执证明投递，接单确认才证明岗位接收了正确任务卡。
13. 运行 `python3 -I <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id> --check-active`。失败时冻结后续生命周期推进，直到完成对账。

投递失败或没有回执/接单确认时，持久状态保持 `Dispatched`，使用相同 ID 重放同一不可变 outbox，并明确报告失败。不得新建 attempt、标为 `InProgress` 或在 PM 会话执行任务卡。

## 回调门禁

1. Worker 先提交实现，生成一个稳定 `callback_id`，将其写入不可变报告，再提交报告。
2. 报告提交存在后，Worker 通过已核验 PM 路由解析逻辑目标 `PM`，主动发送完成/阻塞回调。必须原样携带 `task_id + revision + attempt + dispatch_id`、两个提交 SHA、报告路径、结果及请求 PM 执行的动作。
3. 传输重试复用同一 `callback_id`，不创建新的交付事实或 attempt。
4. 在 `.agentdesk/runtime/transport-receipts.yaml.callback_receipts` 中按该 `callback_id` 记录 provider 结果。回执只在运行时保存，必须标识回调、派发、逻辑源/目标、真实本地路由、provider 状态/回执和投递时间。
5. 回调有成功回执前，Worker 不得宣称编排完成。无法发送时，在可见最终回复中报告传输失败，并使原回调 ID 保持可重试。
6. PM 对照当前派发和回执核验回调身份，再将任务转入审查。Git 中找到的报告是持久恢复证据，不证明回调已送达。
7. 记录已接收回调后，运行 `python3 -I <skill-dir>/scripts/validate_runtime.py --project <repo> --role-id <role-id> --check-active`。宣称本地闭环完成前，还必须通过 `python3 -I <skill-dir>/scripts/validate_project.py --project <repo> --require-runtime`。

## Heartbeat 门禁

每个活动派发都必须有 heartbeat 兜底监控；它不能代替 Worker 主动回调。

1. 工作进行中，定期核验 PM/Worker 路由、派发/回调回执、当前任务状态、worktree head 和预期报告路径。
2. 报告/head 存在但没有回调回执时，归类为回调丢失：唤醒 PM 依据工件审查，已知原 `callback_id` 时予以保留，另行修复或重试路由。
3. 回调回执已存在但 PM 状态未推进时，唤醒 PM 并幂等处理。
4. 路由缺失、已归档、不可达、重复或标题不匹配时，冻结该岗位新派发并请求修复；不得降级为同会话角色切换。
5. 无变化时保持安静。heartbeat 观察记录、重试计时器和游标只放在 `.agentdesk/runtime/` 中。

宿主无法提供周期唤醒时，明确告知用户无法实现完整自动闭环，并保持人工 PM 轮询。不得宣称项目已有自动回调闭环。

## 审查

1. 对照原派发事件/outbox 核验 ID、派发和回调传输回执、两个交付提交，以及报告中的执行模型。`current_dispatch` 清除后仍须可追溯。
2. 审查精确 diff、源码、检查、偏差、契约和风险。
3. 记录不可变验收。
4. 区分 Accepted 与 Integrated。
5. 集成后解锁下一张依赖满足的卡，并重新执行路由、标题和派发门禁；之前一次成功核验不永久证明路由可达。

## 恢复

冻结派发，确立唯一 PM 租约，保全证据，对照 Git 与消息核对状态，只修复可证明的事实，校验后再开放队列。

`--allow-legacy-model-evidence` 仅用于迁移审计，不是严格通过，也不能授权自动验收、派发或 CI 发布。
