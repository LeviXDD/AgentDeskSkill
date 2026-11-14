# 项目岗位

> 理解需求后再配置岗位，创建岗位会话前由用户确认拟启用的岗位。`role_no`、`role_id` 和 `role_name` 是稳定身份字段，派发后不得静默重新编号、改名或复用。

## 当前岗位

表头和状态值保留原接口名称，供校验器读取。

| role_no | role_id | role_name | expected_thread_title | Responsibilities | Forbidden | Common paths | Acceptance focus | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PM | PM | 项目经理 | `PM . 项目经理` | 需求、任务拆分、状态、审查与验收 | 承担 Worker 实现或未经审查的业务改动 | `docs/pm/**` | 范围、证据、依赖与风险 | Active |
| R1 | DEV | 开发工程师 | `R1 . 开发工程师` | 项目实现与测试 | PM 验收、未经批准的契约变更 | `<configure>` | 行为、错误处理与测试 | Proposed |
| R2 | QA | QA 工程师 | `R2 . QA 工程师` | 独立测试设计与证据 | PM 最终验收 | `<configure>` | E2E、回归与残余风险 | Proposed |

`expected_thread_title` 必须严格按 `<role_no> . <role_name>` 派生，句点两侧各一个 ASCII 空格。这是必需的可见标题，不是建议。

只启用项目需要的岗位。岗位描述职责，不代表模型或工具厂商。每个 Active Worker 岗位都必须有不同于 PM 及其他 Worker 的真实会话、核验后的精确标题，以及被 Git 忽略的 `.agentdesk/runtime/routes.yaml` 本地绑定。只有 worktree 不能满足要求。Project Leader 负责 `ROLE-POLICIES.yaml` 中与厂商无关的执行要求；本地 provider/模型可用性放在被 Git 忽略的 `.agentdesk/runtime/model-bindings.yaml` 中。

## 咨询席不是岗位

不要将临时圆桌或顾问席加入上方岗位表。咨询席仅限一个 `Cnnn` 议题，保持只读，没有任务卡、实现、QA、验收、集成、合并、发布、部署、凭据或控制面权限，也不得创建更多会话。

咨询席的可见标题使用独立格式：

```text
<consultation_id>.<seat_no> . <seat_name>｜<short_topic>
```

例如 `C001.01 . 产品顾问｜会员权益`。咨询身份和厂商无关模型要求放在 `docs/pm/consultations/<consultation_id>/consultation.yaml`；真实线程 ID、观察到的标题、提示/回调 ID、模型路由及传输回执只放在被 Git 忽略的 `.agentdesk/runtime/consultations.yaml` 中。

咨询会话不能满足独立 Worker 会话要求。已确认咨询结论转为可执行工作时，应创建普通任务卡，另行通过岗位启用和可见 Worker 会话授权门禁。
