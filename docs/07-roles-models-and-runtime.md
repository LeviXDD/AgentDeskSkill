# 岗位、模型与运行时

## 岗位是稳定身份，不是临时提示词

每个 Active 岗位必须具有：

| 字段 | 示例 | 用途 |
| --- | --- | --- |
| `role_no` | `R2` | 人类排序和任务标题 |
| `role_id` | `FE` | 任务卡、账本、outbox 的逻辑外键 |
| `role_name` | `前端工程师` | 岗位名称和规范标题 |
| `expected_thread_title` | `R2 . 前端工程师` | 运行时验证 |

真实岗位任务标题必须逐字等于：

```text
<role_no> . <role_name>
```

点号两侧各有一个 ASCII 空格。自动生成标题、任务摘要、英文缩写、worktree 名称或“你现在扮演前端”都不能代替规范标题。

## 独立岗位任务的门禁

Standard / Automated 要求：

- PM 与 Worker 使用不同的真实 task/thread ID；
- 每个 Worker 使用独立 worktree；
- 真实任务可以被读取和跨任务寻址；
- 实际标题与预期标题一致；
- host、worktree 和 callback route 可达；
- route 状态为 `verified`；
- 创建新的用户可见任务已有明确授权。

一个 worktree、一个 subagent 或同会话人格切换都不能被记录为独立岗位任务。

## 咨询席不是 Active 执行岗位

V1 咨询席是执行前的临时只读 advisory session，与 `ROLES.md` 中的 Active roles、`tasks.yaml` 和执行看板正交：

| 对比项 | 咨询席 | Active 执行岗位 |
| --- | --- | --- |
| 标识 | `C001.01` 等临时席位编号 | 稳定 `role_no + role_id + role_name` |
| 任务标题 | `C001.01 . 产品顾问｜短议题` | `R2 . 前端工程师` |
| 输入 | 议题、共同事实和席位问题 | 冻结任务卡与 `base_commit` |
| 输出 | 建议及主动回调 | 实现、报告、回调和 Git 证据 |
| 权限 | 只读建议，无控制面写权 | 仅任务卡授权的业务路径 |
| 状态 | 不进入 `tasks.yaml` | 进入权威任务状态机 |

咨询会话标题格式必须逐字采用：

```text
<consultation_id>.<两位席号> . <顾问岗位>｜<短议题>
```

例如：`C001.01 . 产品顾问｜短议题`。第一轮席位彼此独立，所有派发和回传都经过 Leader；每席必须主动回调 Leader，不能只在自己的会话里输出结论。

每次咨询的公开生命周期记录位于 `docs/pm/consultations/CNNN/consultation.yaml`；真实咨询 task/thread ID、expected/actual title、prompt/response receipt 和 `model_binding` 只保存在 gitignored `.agentdesk/runtime/consultations.yaml`。`model_binding` 必须区分默认未验证、精确 override 未验证和已有 provider evidence 的 verified；咨询席 thread 不得复用 Worker route，也不复用执行 receipt 或 worktree 证明。

## 岗位模型策略

Leader 在版本化 `ROLE-POLICIES.yaml` 中定义厂商无关要求：

- `default_tier`：正常首选等级；
- `minimum_tier`：不可突破的硬下限；
- `deliberation_tier`：最低推理深度；
- `required_capabilities`：硬能力集合；
- `degradation_policy`：降级处理方式。

模型等级：

```text
basic < standard < advanced < expert
```

推理深度：

```text
efficient < balanced < deep
```

它们是项目内部校准，不是跨厂商的绝对排行榜。

## 任务怎样抬高模型要求

```text
required_tier = max(
  role.minimum_tier,
  task.min_model_tier,
  risk_floor
)

preferred_tier = max(
  role.default_tier,
  required_tier
)
```

岗位与任务的 capabilities 做并集。任务和风险可以抬高要求，但不能降低岗位硬下限。

## 本机模型绑定

具体 provider 和 model ID 不进入仓库策略，而保存在：

```text
.agentdesk/runtime/model-bindings.yaml
```

每个 binding 记录 provider、稳定模型标识、tier、deliberation、capabilities 和 enabled 状态。凭据、token 和 session ID 不得写入其中。

派发前运行：

```bash
python3 <skill-dir>/scripts/select_model.py \
  --project <repo> \
  --task-card-commit <40-char-sha> \
  --role-id <role-id> \
  --risk <L0-L4> \
  --task-min-tier <inherit|basic|standard|advanced|expert>
```

根据需要重复加入 `--required-capability`。选择结果的九个字段必须原样复制到：

1. `current_dispatch.model_selection`；
2. outbox 的 `model_selection`；
3. report 的 `executor_model`。

不能缺键、加键、重命名或手算替代。

## 降级策略

| 策略 | 含义 |
| --- | --- |
| `block` | 达不到首选等级时停止 |
| `allow_to_minimum` | 可以降到仍满足全部硬要求的等级 |
| `require_pm_approval` | 需要 attempt 级结构化批准后才能降级 |

任何策略都不能降到硬下限以下，也不能缺少所需能力或推理深度。模型降级批准只授权这一次模型选择，不授权生产、凭据、验收、合并或发布。

## 选择器不负责启动模型

`select_model.py` 只负责确定性选择和校验。dispatcher / runtime adapter 仍需证明实际执行环境能够使用该 provider、模型和推理档位。

如果运行时不能固定模型，模型策略只能作为审计要求，不能声称已经强制路由。

### 咨询模型建议与披露

咨询模型只做能力建议，不进入执行任务的九字段 model-selection 证据，也不会因为建议了某个档位就自动启动或强制该模型：

| 场景 | 建议能力 | 建议档位 |
| --- | --- | --- |
| 单一、边界清晰的专业核查 | 对口领域知识、批判性检查 | `advanced / balanced` |
| 两席互补或对抗审查 | 各自领域能力、反例分析 | `advanced / deep` |
| 三席跨领域或高风险议题 | 产品、技术、风险综合判断 | 关键席与 Leader 可建议 `expert / deep` |
| Leader 汇总 | 比较前提、识别冲突、保留少数意见 | 高不确定性时建议 `expert / deep` |

席位数量不能代替模型质量，模型等级也不能授予决策权。宿主如果不能读取真实会话、核对精确标题、证明第一轮隔离、确认回调送达或返回可信的实际模型证据，应逐项写明“未验证”，不得把提示词、自述或配置偏好写成平台强制结果。

只有用户明确要求某个精确模型且宿主支持精确 override 时，Leader 才应传入该 override；其他情况应让宿主使用默认绑定并如实披露。无论哪种方式，都不能把请求参数当作实际执行证明。

同样，平台通常只能记录用户发出的显式确认，不能验证该用户在现实组织中确实具有 Owner 身份、法定授权或相应权限。高风险决策仍需项目约定的外部身份与审批渠道。

## 运行时路由

真实会话绑定保存在：

```text
.agentdesk/runtime/routes.yaml
```

每个 PM 或 Worker route 包含逻辑岗位、预期/实际标题、transport、host、真实 thread ID、worktree、状态和验证时间。

route 状态只有：

- `unbound`：尚未绑定；
- `verified`：真实读取和核对通过；
- `stale`：任务、标题、host 或 worktree 已漂移。

只有 `verified` 可用于派发和回调。

## Transport receipts

`.agentdesk/runtime/transport-receipts.yaml` 保存两类事实：

- dispatch receipt：PM 的消息真实送达哪个 Worker；
- callback receipt：Worker 的回调真实到达哪个 PM。

outbox 只证明“准备发送”，report 只证明“产生交付”。没有对应 receipt，不能声称传输闭环已完成。

当前 beta 校验器要求 `provider_receipt` 是非空、非占位字符串，尽管部分低层示例仍写成可为 `null`；实际项目应遵循校验器，不要写 `null`。此外，callback 的 `status: received` 表示运行时适配器记录了传输接收结果，不等于 PM 已阅读、ACK 或验收。需要强证明时还应由 PM 读取消息或返回匹配 ACK。

## 隐私边界

以下内容不得进入 Git、任务卡、报告或公开看板：

- task/thread/session ID；
- 本机绝对 worktree 路径；
- provider receipt 原文；
- token、凭据和连接信息；
- lease ID、进程 ID、游标和重试计时器；
- 未脱敏日志、提示词和内部推理。

运行时目录丢失不会改变仓库事实，但在 route 和 receipt 被重新发现并验证前，会阻止 Standard / Automated 的新派发。
