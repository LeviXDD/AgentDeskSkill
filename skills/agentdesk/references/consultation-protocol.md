# 圆桌研讨协议

本参考定义 AgentDesk V1 的多会话咨询模式：Leader 为一个有界议题创建 1–3 个独立、只读的咨询席，收集第一轮独立意见，必要时进行一次交叉质询，再由 Leader 综合并请 User / Owner 确认。

这是**咨询控制面**，不是第二套执行任务系统。它的目的是扩大视角、暴露假设和收敛分歧，不是用多数票代替 Leader 判断，也不是让咨询席变成 Worker、QA 或验收者。

## 目录

- [核心边界](#1-核心边界)
- [何时启动研讨](#2-何时启动研讨)
- [数据与权威分层](#3-数据与权威分层)
- [ID 与会话命名](#4-id-与会话命名)
- [授权门禁](#5-授权门禁)
- [选席与模型要求](#6-选席与模型要求)
- [生命周期](#7-生命周期)
- [创建与核验真实会话](#8-创建与核验真实会话)
- [第一轮独立分析](#9-第一轮独立分析)
- [第二轮交叉质询](#10-第二轮交叉质询)
- [中心辐射回调与 receipt](#11-中心辐射回调与-receipt)
- [综合与 Owner 确认](#12-综合与-owner-确认)
- [从咨询转入执行](#13-从咨询转入执行)
- [降级与恢复](#14-降级与恢复)
- [最小合规检查](#15-最小合规检查)

## 1. 核心边界

### 1.1 咨询不是执行

咨询席只能读取简报和被允许的上下文，并向 Leader 提供结构化建议。咨询席无权：

- 修改仓库、工作树或项目控制面；
- 创建 Git commit、分支、任务卡、event、outbox、acceptance 或 decision；
- 迁移 `docs/pm/state/tasks.yaml` 中的任务状态；
- 实施、测试、验收、集成、合并、发布或部署；
- 使用生产凭据或执行有外部副作用的操作；
- 创建更多会话、委派子 Agent 或把实质分析再转包；
- 直接向其他咨询席发送消息。

咨询席可以建议开发、调查、QA 或决策动作，但只能把它们写成建议并回调 Leader。这些建议不是实现或验收证据。

### 1.2 咨询席不是岗位

咨询席是一次性的议题视角，不写入 `ROLES.md`，不占用执行 WIP，不绑定执行 `routes/v2`，不使用 Worker 的 task/revision/attempt/dispatch 状态机，也不能替代 Standard / Automated 中真实的执行岗位会话。

一个咨询席可以提供产品、技术、安全或风险视角，但它不因名称而获得相应岗位的实际权限。

### 1.3 Leader 是唯一中心

所有提示和回调都遵循：

```text
Leader source task
→ consultation seat
→ Leader source task
```

咨询席之间不建立任何路由。需要交叉质询时，由 Leader 先整理分歧，再把有界、必要时匿名化的问题发回原席位。

## 2. 何时启动研讨

Leader 只在额外视角会实质改善决策质量时建议研讨，例如：

- 需求存在多种合理解释；
- 架构、产品、安全或成本之间存在真实取舍；
- 决策不可逆、失败成本高或风险较高；
- 主会话缺少一个明确的专业视角；
- Leader 的结论依赖未验证假设，或置信度不足；
- User / Owner 明确要求多视角讨论。

对于事实明确、风险低、可直接验证的单步问题，Leader 直接处理，不为了形式感创建咨询会话。

默认数量决策：

| 席位数 | 适用情况 |
| --- | --- |
| 1 | 一个明确的专业知识缺口 |
| 2 | 两个合理方向、对立假设或主要取舍 |
| 3 | 跨三个领域或高风险决策 |

V1 绝不得为同一研讨创建超过 3 个席位。

## 3. 数据与权威分层

### 3.1 执行状态保持隔离

`docs/pm/state/tasks.yaml` 仍然是**执行任务编排状态**的唯一权威。不得把 consultation 写成任务记录，不得为它创造 `TC-*`、`dispatch_id`、delivery state、acceptance 或 integration state，也不得用 `type: spike` 伪装咨询。

项目型研讨使用独立的每议题记录：

```text
docs/pm/consultations/C001/
  consultation.yaml
  brief.md
  responses/
    r1-C001.01.md
    r1-C001.02.md
    r2-C001.01.md             # 仅需要第二轮时
  synthesis.md
```

`consultation.yaml` 是该次研讨的唯一当前记录，使用 `agentdesk.consultation/v1`，并保持 JSON-compatible YAML。它不是执行状态的第二权威，不得反向更改 `tasks.yaml`。

只有发起并持有当前咨询协调权的 Leader 可以写入上述咨询记录和持久化 response。咨询协调权不等于 PM lease，也不授权 Leader 修改执行任务控制面；咨询席永远只读。

### 3.2 公开证据与运行时证据

可进入 Git 的稳定信息包括：

- `consultation_id`、`seat_id`、`response_id`、轮次与规范标题；
- 用户批准的有界范围和精确标题列表；
- 厂商无关的席位模型要求；
- 不含运行时 ID 的 brief、response、synthesis 和 Owner 确认事实。

上述“不含”适用于整个公开控制面，不只是 JSON key。`topic`、`focus`、`trigger_reason`、`cancellation_reason`、`confirmed_scope` 和 artifact 正文也不得夹带 runtime ID/凭据键值、provider receipt、session 信息或本机绝对路径。需要说明运行时偏差时，只写脱敏结论，详细值留在 gitignored runtime 中。

以下信息只能保存在 gitignored `.agentdesk/runtime/consultations.yaml`：

- source 与席位 thread/task ID；
- host ID、本机路径、消息游标和重试计时器；
- prompt ID、live callback ID、provider receipt 和传输错误；
- 实际模型路由、token、凭据、session ID 或连接数据。

公开 `rounds[].responses[].callback_id` 是一个保留字段，**始终写 `null`**。公开记录通过稳定 `response_id` 与本机 `response_receipts[*].response_id` 交叉核对；不得把 live callback ID 或 receipt 复制回 Git。

可使用 [consultation record 模板](../assets/consultation-record-template.yaml)建立记录。

### 3.3 非项目话题

如果当前没有已初始化的 AgentDesk 项目，Leader 可以在取得相同会话授权后运行一次 conversation-only 咨询。这种模式只在主会话中交付综合结论，不宣称有 Git 审计或可恢复的项目证据，也不能自动转为项目决策或任务卡。

## 4. ID 与会话命名

### 4.1 逻辑 ID

- `consultation_id`：`C001`、`C002` 递增，项目内永不复用。
- `seat_no`：两位数字 `01`、`02`、`03`。
- `seat_id`：`<consultation_id>.<seat_no>`，例如 `C001.02`。
- `response_id`：稳定的公开关联键，固定为 `<seat_id>.R<round>`，例如 `C001.02.R1`。
- `authorization_id`：独立命名空间，例如 `CA-C001-7F3C`。
- `confirmation_id`：只在 Owner 确认、拒绝或延后时分配，例如 `CC-C001-2D91`。

V1 中授权 ID 必须逐字匹配 `CA-<consultation_id>-<4–32 位大写字母或数字>`，确认 ID 必须匹配 `CC-<consultation_id>-<4–32 位大写字母或数字>`；`XXXX`、`TODO`、`TBD` 等占位符不得进入实际记录。`granted_by` / `confirmed_by` 使用 `User`、`Owner`，或带稳定标识的 `User:<id>` / `Owner:<id>`；可选 suffix 必须包含字母或数字，且不得使用 `TODO`、`TBD`、`XXXX`、`pending`、`unbound` 或 `placeholder` 等伪标识。这只能防止 Leader 自签和模糊身份，不能证明平台外的现实授权。

prompt ID、live callback ID 和 provider receipt 是运行时传输键，不写入公开 record 或 response。

### 4.2 真实会话标题

每个咨询会话的可见标题必须逐字等于：

```text
<consultation_id>.<seat_no> . <seat_name>｜<short_topic>
```

例如：

```text
C001.01 . 产品顾问｜会员权益
C001.02 . 技术顾问｜会员权益
```

第一个点是 `consultation_id` 与两位 `seat_no` 的紧密分隔符；第二个点两侧各有一个 ASCII 空格。岗位名与短议题之间使用全角 `｜`。`short_topic` 必须稳定、简短、单行，不包含额外的 `.` 或 `｜`。

咨询会话不使用执行岗位的 `<role_no> . <role_name>` 公式，也不写入 `ROLES.md`。

## 5. 授权门禁

### 5.1 创建可见会话需要独立授权

创建 Codex 任务/会话是用户可见的外部状态变化。以下说法均不构成咨询会话授权：

- “使用 AgentDesk”；
- “开始分析/开发”；
- “深入想一想”；
- 同意升级 Skill 的能力；
- 曾经允许为另一个议题创建会话。

Leader 在调用创建工具前，必须先展示：

1. 完整议题与讨论边界；
2. 席位数、每席视角与选择理由；
3. 将创建的每个精确标题；
4. 最多轮次（不超过 2）；
5. 只读、不执行、不验收、不递归开会的权限边界。

之后才能记录 `creation_authorization`。授权未获得时，state 保持 `awaiting_authorization`，不调用任何创建工具。

### 5.2 授权种类

`creation_authorization` 在 `draft` / `awaiting_authorization` 可为 `null`；授权后必须是：

```yaml
authorization_id: CA-C001-7F3C
kind: explicit_batch              # explicit_batch | scoped_auto
granted_by: User
granted_at: "<RFC3339 UTC>"
topic_scope: "<must exactly equal consultation.topic>"
max_visible_sessions: 2
max_rounds: 2
read_only_required: true
authorized_titles:
  - "C001.01 . 产品顾问｜会员权益"
  - "C001.02 . 技术顾问｜会员权益"
```

- `explicit_batch`：User / Owner 直接确认当前精确标题批次。
- `scoped_auto`：User / Owner 明确授权 Leader 在当前精确议题范围内自行选择有界席位。它仍是用户授予的限定权限，不是默认自治。

`topic_scope` 必须逐字等于当前 record 的 `topic`，不得用更宽泛的表述扩大授权。`explicit_batch` 必须在创建前写出实际 `authorized_titles`，且该集合与 `seats[].expected_thread_title` 完全一致。`scoped_auto` 的 `authorized_titles` 可为空数组，表示 User / Owner 已在当前 topic 和数量上限内明确委托 Leader 选席；如果非空，也必须与实际 titles 完全一致。`seat_count` 不得超过 `max_visible_sessions`，`round_limit` 不得超过 `max_rounds`。

授权在研讨关闭、取消、议题实质改变、标题/席位变更或需创建替代会话时失效。不得将它外推到后续议题、执行会话或更多轮次。

## 6. 选席与模型要求

### 6.1 选最小有效席位集

每个席位必须有不重复的 `focus`。Leader 不得为了制造“多数意见”而设置三个同质席位，也不得让某席位在第一轮充当 Leader 或评审其他席位。

建议的互补组合例子：

- 产品价值 / 技术可行性；
- 主方案 / 反对者或 pre-mortem；
- 产品 / 技术 / 安全与风险。

### 6.2 厂商无关的模型要求

每席使用：

```yaml
model_requirement:
  minimum_tier: standard          # basic | standard | advanced | expert
  deliberation_tier: balanced     # efficient | balanced | deep
  required_capabilities: [analysis]
```

它描述需求，不绑定 provider 或具体 model，也不授予任何权限。议题风险与推理链可以抬高要求，但不能超过本机运行时可证明的能力。

V1 不得把 `create_thread` 假定为可以强制选择这个抽象等级。当 User / Owner 没有明确点名具体模型时，不得仅因 record 中存在 `model_requirement` 就擅自覆盖宿主默认 model 参数。只有 User / Owner 明确点名具体模型、宿主工具当前 schema 明确支持该 override，且能取得可信执行证据时，才能声称路由已强制。否则它只是选席和审计要求，Leader 只能披露实际模型未验证。

## 7. 生命周期

### 7.1 state 枚举

`consultation.yaml.state` 只允许：

```text
draft
awaiting_authorization
authorized
round_1_in_progress
round_1_complete
round_2_in_progress
round_2_complete
awaiting_confirmation
closed
cancelled
```

正常路径：

```text
draft
→ awaiting_authorization
→ authorized
→ round_1_in_progress
→ round_1_complete
→ [round_2_in_progress → round_2_complete]
→ awaiting_confirmation
→ closed
```

`cancelled` 可从任何未关闭状态进入，但必须保留已有证据并说明原因。已关闭或取消的议题不得静默恢复；实质继续时建立新 `consultation_id`。

取消时必须填写非空 `cancellation_reason`，保留已经发生的 Round 1 / Round 2、公开回答和本机 receipt，不得为了让状态通过而删除证据。`cancelled` 只允许 `outcome_status: none | proposed`、`confirmation.status: pending | not_required`，且 `derived_refs` 必须为空；已经确认、拒绝或延后的结果应以 `closed` 保存。

### 7.2 outcome_status 枚举

```text
none
proposed
confirmed
rejected
deferred
```

- 尚未综合时为 `none`。
- Leader 完成 synthesis 后为 `proposed`。
- User / Owner 对明确范围做出选择后，才可改为 `confirmed`、`rejected` 或 `deferred`。

`confirmation.status` 只允许 `pending | confirmed | rejected | deferred | not_required`。`not_required` 只用于已取消、纯信息性且不产生任何 decision/task 的研讨。

### 7.3 记录不变量

- `seat_count` 是 1–3 的整数，且等于 `seats` 长度。
- `round_limit` 只能是 1 或 2。
- 每个 `seat_no`、`seat_id`、`expected_thread_title` 都唯一。
- 席位 `status` 只允许 `planned | created | prompted | responded | missing | blocked | cancelled`。
- `rounds[].round` 只允许 1 或 2，不重复，不超过 `round_limit`。
- Round 1 必须 `independent: true`；Round 2 必须 `independent: false`。
- Round 2 只能在 Round 1 已结束且存在会改变结论的实质分歧时新增，`trigger_reason` 必须说明该分歧。
- response `status` 只允许 `received | missing | blocked`；每个席位在每轮最多一个稳定 `response_id`。
- 公开 response 引用的 `callback_id` 必须为 `null`。
- `derived_refs` 任一数组非空时，`confirmation.status` 和 `outcome_status` 必须都为 `confirmed`。
- `draft` / `awaiting_authorization` 的席位只能是 `planned | cancelled`，授权对象必须为 `null`，且不得存在 round；`authorized` 才能出现 `created`；进入 round 后席位状态必须与该阶段一致。
- brief、response、synthesis 必须使用对应 V1 模板 frontmatter、稳定 ID 和必需章节；公开 artifact 不得含 runtime route、provider evidence 或本机绝对路径。

## 8. 创建与核验真实会话

### 8.1 先发现宿主能力

在向用户承诺多会话研讨之前，Leader 先检查当前宿主是否提供等价能力：

1. 取得当前 source task 的真实 ID；
2. 列出并读取 task/thread；
3. 创建新的用户可见 task/thread；
4. 设置并回读可见标题；
5. 向已知 task/thread 发送消息；
6. 有界等待一个或多个席位；
7. 让咨询席主动回调 source task，并能保存 receipt。

只有在创建授权获得后才能调用创建工具；能力发现本身不等于授权。

任一能力缺失时，应在创建前说明精确缺口。不得使用逻辑 subagent、同会话人格切换或一个工作树来冒充用户要求的真实可见咨询会话。

### 8.2 创建顺序

通用顺序是：

```text
discover capabilities
→ propose exact topic / seats / titles / round limit
→ obtain explicit_batch or scoped_auto authorization
→ persist authorized consultation record
→ verify source task
→ create every seat with read-only bootstrap
→ set exact title
→ read/list every seat back
→ persist runtime-only route attestations
→ send Round 1 prompts
```

对 Codex，使用宿主当前可用的 `create_thread`、`set_thread_title`、`read_thread`、`list_threads`、`send_message_to_thread` 和 `wait_threads` 等价工具，但必须以工具的实际 schema 为准，不猜测参数。

如果创建只返回排队用 client ID，使用有界等待和 list/read 查找真实 thread ID；不得把 client ID 标为 verified。会话存在、可读、未归档，实际标题与 `expected_thread_title` 逐字相等，且与 source/其他席位 ID 都不同时，才可记为 verified。

创建时的 bootstrap 只说明席位、只读边界、禁止创建子会话与“等待 Round 1 简报”。不让席位根据标题猜测议题或开始实质分析。

咨询会话不要求执行岗位的专用可写 worktree。如果宿主强制绑定项目工作树，席位的权限仍为只读，Leader 必须在接受回答前检查没有因咨询产生仓库变更。一旦出现写入，将该席位标记 `blocked`，保留证据并交给 User / Owner 处理；不得自动接纳或清理它。

## 9. 第一轮独立分析

### 9.1 共享简报与席位视角

Leader 使用 [consultation brief 模板](../assets/consultation-brief-template.md)创建一份不可变的公共简报，其中包含问题、范围、事实、非目标、决策标准和可读上下文。对其仓库 UTF-8/LF 字节计算 SHA-256，把结果记入 Round 1 `brief_digest`。

每席可额外获得自己的 `focus`，但所有席位的事实底稿和公共问题必须一致。

### 9.2 独立性规则

- `rounds[round=1].independent` 必须为 `true`。
- Leader 必须向全部席位发出 Round 1，才开始向任一席位转发新信息。
- 在 Round 1 关闭前，Leader 不向席位显示其他席位的回答、摘要、置信度、投票或评价。
- 席位不得搜索其他席位会话，也不得通过共享文件寻找已经回调的内容。
- 一席咨询时，仍须保持独立简报与回调证据；独立性规则在形式上自动满足。

### 9.3 回答结构

Round 1 席位必须使用 [Round 1 response 模板](../assets/consultation-response-template.md)，至少提供：

- 推荐结论与成立条件；
- 核心依据；
- 关键假设；
- 风险与反例；
- 不确定事项；
- 会改变结论的信息；
- 建议下一步与置信度。

咨询席发送的运行时消息带有 live prompt/callback ID。Leader 持久化回答时必须用预分配的 `response_id` 关联，删除运行时路由字段，并把公开 `callback_id` 保持为 `null`。

brief、response 和 synthesis 的每个必需章节都必须含有非空、非模板占位的实质内容。`<TBD>`、`TODO`、`XXXX`、未替换的 `<...>` 模板 token、只剩标题或只剩表头的产物都不构成咨询证据。

## 10. 第二轮交叉质询

Round 2 可选，且是 V1 的绝对上限。它只在以下条件全部满足时开启：

1. `round_limit == 2` 且创建授权覆盖最多两轮；
2. Round 1 已关闭，所有收到的回答已经持久化，且 Round 2 最早的 prompt 不早于所有已收到的 Round 1 callback；
3. Leader 能明确指出会改变最终建议的实质分歧；
4. Round 2 仅重新联系原席位，不新增会话。

Leader 将分歧整理成有界问题，必要时匿名化观点，再通过中心辐射路由发回原席位。Leader 不建立席位间群聊，不把一席的整段原文直接交给另一席，也不开启 Round 3。

Round 2 使用单独的 [Round 2 response 模板](../assets/consultation-response-r2-template.md)，必须声明立场是 `unchanged`、`narrowed` 还是 `changed`，并给出理由。不要把 Round 2 占位块留在 Round 1 产物中。

## 11. 中心辐射回调与 receipt

### 11.1 主动回调是主路径

每席在完成或受阻后，必须在结束自身任务前：

1. 从委派上下文读取运行时 `source_thread_id`，不按标题猜测 Leader；
2. 原样携带 `consultation_id + seat_id + round + response_id + prompt_id + callback_id`；
3. 调用真实跨任务发送工具，目标为 delegation source；
4. 运行时以 live `callback_id` 为 key 写入 `response_receipt`，并在对象内用 `response_id` 交叉引用公开回答；
5. 传输失败时复用相同 prompt/callback/response ID 和 payload 重试，不创造第二个咨询事实。

仅在席位会话输出最终答复、Leader 看到任务状态完成，或 wait 工具返回结果，都不等于席位主动回调。

### 11.2 runtime schema

`.agentdesk/runtime/consultations.yaml` 使用 `agentdesk.consultation-runtime/v1`；V1 采用严格字段集合，结构如下：

```yaml
schema_version: agentdesk.consultation-runtime/v1
updated_at: "<RFC3339 UTC>"
consultations:
  C001:
    source:
      thread_id: "<runtime-only-real-source-id>"
      host_id: "<runtime-only-host-id>"
      status: verified
      verified_at: "<RFC3339 UTC>"
    seats:
      C001.01:
        expected_title: "C001.01 . 产品顾问｜会员权益"
        actual_title: "C001.01 . 产品顾问｜会员权益"
        thread_id: "<runtime-only-real-seat-id>"
        host_id: "<runtime-only-host-id>"
        created_at: "<RFC3339 UTC>"
        creation_evidence: "<terminal-provider-creation-evidence>"
        status: verified
        verified_at: "<RFC3339 UTC>"
        model_binding:
          requested_model: null
          request_source: null
          actual_model: null
          status: default_unverified
          provider_evidence: null
    prompt_receipts:
      C001.01.R1:
        prompt_id: "<runtime-only-prompt-id>"
        round: 1
        seat_id: C001.01
        source_thread_id: "<runtime-only-real-source-id>"
        destination_thread_id: "<runtime-only-real-seat-id>"
        brief_digest: "sha256:<64-lowercase-hex>"
        status: sent
        provider_receipt: "<non-empty-provider-evidence>"
        sent_at: "<RFC3339 UTC>"
    response_receipts:
      "<runtime-only-live-callback-id>":
        callback_id: "<runtime-only-live-callback-id>"
        prompt_id: "<runtime-only-prompt-id>"
        response_id: C001.01.R1
        round: 1
        seat_id: C001.01
        source_thread_id: "<runtime-only-real-seat-id>"
        destination_thread_id: "<runtime-only-real-source-id>"
        response_digest: "sha256:<64-lowercase-hex>"
        status: received
        provider_receipt: "<non-empty-provider-evidence>"
        received_at: "<RFC3339 UTC>"
```

实际项目使用 JSON-compatible YAML 序列化这个本机文件。`source` 和席位 route 必须为真实、可读且互不重复的 task/thread；咨询席 thread 还不得复用任何 Active Worker route，只有 Leader source 可以与 PM route 相同。每个席位保存创建时间与终态创建证据，不能只靠后来读取到一个 route 反推“曾按授权创建”。`prompt_receipts` 以预分配的 `response_id` 为 key，对象内保存 live `prompt_id`，并用 `brief_digest` 绑定当轮公开简报字节；`response_receipts` 以 live `callback_id` 为 key，对象内的 `prompt_id + response_id` 必须与同席同轮 prompt/公开回答精确对应，并用 `response_digest` 绑定 Leader 持久化后的公开回答字节。response receipt 的 destination 必须等于委派 source。成功 receipt 的 provider evidence 必须为非空字符串或对象，明示 `failed`、`denied`、`error`或含非空错误字段的对象不是成功证据；若对象任意嵌套层提供 `status/state/outcome/result`，它必须是与创建、prompt、response 或 model 证据类型相符的非空终态成功字符串；`success/ok/accepted` 控制字段如存在必须是真正的布尔值 `true`。`pending`、`queued`、`timeout`、`not_delivered`、`unavailable`、`unknown` 等非终态/不确定状态不得证明 `created`、`sent`、`received` 或 `verified`。失败发送不得伪装成成功 receipt。runtime 中多出的 consultation、seat、prompt 或 response receipt 都视为孤儿事实并失败，不能被静默忽略。

公开记录与 runtime 必须形成同一个有向因果图，而不是强行规定互不依赖动作的先后：`record.created_at ≤ authorization.granted_at ≤ seat.created_at ≤ seat.verified_at ≤ prompt.sent_at`，且 `record.created_at ≤ brief.created_at ≤ prompt.sent_at`；Leader source 的本次读取验证也必须位于授权之后、prompt 之前。brief 可以在授权或建席之前准备，但未经创建授权不得建立可见席位，未经 prompt receipt 不得接受该轮回答。每个已收到回答再满足 `prompt.sent_at ≤ callback.received_at ≤ response artifact.created_at ≤ synthesis.created_at ≤ confirmation.recorded_at ≤ record.updated_at`。所有 source/seat/prompt/callback 运行时事件也不得晚于公开 record 的 `updated_at`。没有发生的可选节点可省略，但不能倒置已存在的依赖。Round 2 的 brief/prompt 必须等 Round 1 所有 callback 与公开回答持久化收口，Round 2 的回答时间也不得早于 Round 1。这条因果图防止“未授权已建会话”、“未发题已收到回调”和“Owner 先确认、后收到建议”的不可能证据链。

创建 evidence 与两个 digest 只能绑定本机记录、provider 回执和公开文件之间的内部证据链，不能凭空证明 provider 真正创建了什么、实际发送了哪些 payload，或回调当时携带了哪些原始字节。完整闭环仍需使用宿主的 live read/list/message 能力复验；宿主不提供这些可观察性时必须披露未验证，不能提升证据强度。

`model_binding` 只保存运行时可观察性，不扩大权限。没有精确模型请求时使用 `default_unverified`；User / Owner 明确点名精确模型但宿主无法证明结果时，记录 `requested_model + request_source: user_explicit + override_unverified`；只有观察到的 `actual_model` 与精确请求逐字相等并取得非空 provider evidence 时才可写 `verified`。请求与实际不一致时保持未验证并阻断完整闭环，不得把该对象复制进公开 record。

### 11.3 Leader 持久化回答

Leader 收到回调后：

1. 核对 consultation、seat、round、response 与本机 receipt；
2. 去除 thread ID、live callback ID、provider receipt 和其他运行时数据；
3. 把回答按 `response_id` 持久化到公开 response 路径；
4. 在 `consultation.yaml` 中将对应 response 改为 `received`、写入 path，但 `callback_id` 仍为 `null`；
5. 仅在所有席位已回答、受阻或被明确标记缺失后关闭当轮。

response artifact frontmatter 的 `created_at` 表示 Leader 完成脱敏并持久化该公开文件的时间，不是席位开始作答或 provider 生成原始消息的时间，因此必须不早于 callback `received_at`。

## 12. 综合与 Owner 确认

### 12.1 Leader 综合，不做机械投票

Leader 使用 [consultation synthesis 模板](../assets/consultation-synthesis-template.md)，必须分开表达：

1. 一致共识；
2. 条件性共识；
3. 关键分歧；
4. 少数意见；
5. 关键假设、不确定事项和残余风险；
6. Leader 的有条件综合建议；
7. 需要 User / Owner 裁决的精确问题。

“2 比 1”、席位数量或模型等级不能自动成为结论。Leader 必须根据证据质量、适用条件、风险和决策标准做出独立判断，并保留重要少数意见。

任何缺失席位、丢失主动回调、wait/read 恢复或其他降级都必须出现在 synthesis，不得把残缺集合表述为“全部共识”。

### 12.2 综合报告只是 proposed

初始 synthesis 必须 `outcome_status: proposed`，record 进入 `awaiting_confirmation`。Leader 把综合结论与待裁决项交给 User / Owner，不得从沉默、超时、“继续”、之前的会话创建授权或 Leader 自己的推荐中推定确认。

确认必须记录：

```yaml
confirmation_id: CC-C001-2D91
status: confirmed                 # confirmed | rejected | deferred
confirmed_by: Owner
confirmed_scope:
  - "<exact accepted conclusion>"
recorded_at: "<RFC3339 UTC>"
```

如果 User / Owner 只确认一部分结论，`confirmed_scope` 只写那一部分；未被列出的内容仍是 proposed。拒绝或延后不得产生执行派生物。

## 13. 从咨询转入执行

只有当 `confirmation.status == confirmed` 且 `outcome_status == confirmed` 时，Leader 才可对 `confirmed_scope` 做以下事情：

- 向 `DECISIONS.md` 追加一条指向 `consultation_id + confirmation_id` 的决策；
- 起草一张明确引用 `consultation_id + confirmation_id` 的任务卡。

对应引用写入 `derived_refs.decision_ids` 或 `derived_refs.task_card_paths`。不得把未确认的 synthesis 链接写成生效决策。

咨询派生任务卡还必须在 frontmatter 同时写入：

```yaml
source_consultation_id: C001
source_confirmation_id: CC-C001-2D91
```

`consultation.yaml.derived_refs.task_card_paths` 必须反向列出该卡；决策日志行也必须同时出现 decision ID、consultation ID 和 confirmation ID。任何一侧缺失都不构成可验证血缘。

该 Owner 确认**仅**表示可以将指定结论转换为决策记录或工作规格。它不推定：

- 创建任何执行岗位会话；
- 派发任务或开始实现；
- 变更业务、使用凭据或对外发送消息；
- 验收、合并、发布、部署或不可逆操作。

之后仍必须进入原 AgentDesk 的 Definition of Ready、执行岗位可见会话授权、派发、交付、验收和集成门禁。

## 14. 降级与恢复

### 14.1 创建、改名或读取失败

- 会话未真实创建：席位保持 `planned` 或标记 `blocked`，不发 Round 1。
- 标题不符：先修正并通过 list/read 重新核验；核验前不得发送简报。
- 会话不可达或已归档：保留记录，不自动创建替代会话。替代会话是新的用户可见状态，需要重新检查授权和数量上限。

### 14.2 发送或回调失败

- prompt 发送失败：复用同一运行时 prompt ID、response ID、目标和 payload 重试，不新增轮次。
- 席位完成但主动 callback receipt 缺失：标记为 lost callback，Leader 可使用有界 `wait_threads` / `read_thread` 恢复回答，但必须将传输模式记为 `polled_recovery`，公开 callback ID 仍为 `null`，且不宣称主动回调闭环成功。
- wait/read 也无法取得回答：席位与 response 标记 `missing` 或 `blocked`，向 User / Owner 暴露缺口。

`wait_threads` 是有界监控和恢复手段，不是席位主动回调的同义词。在持续等待中，Leader 不做无间隔轮询，不因超时自动补写席位意见。

### 14.3 不完整研讨

默认等待所有已授权席位返回。存在 `missing` / `blocked` 席位时：

1. Leader 不得伪造其回答或把剩余席位称为全体共识；
2. 优先修复原路由和复用原 ID；
3. 如果需要在不完整集合上综合，先向 User / Owner 说明缺失与影响；
4. synthesis 必须单独列出不完整性、恢复方式和置信度下调。

### 14.4 运行时丢失

运行时文件丢失不改变公开议题记录，但会阻断新 prompt 和完整回调宣称。Leader 使用 list/read 从真实宿主重新发现 source/席位、回读标题并重建 runtime route；无法证明的 receipt 不得伪造。

## 15. 最小合规检查

宣称一次 V1 圆桌研讨完成前，Leader 逐项回答“是”：

- [ ] 议题范围精确，席位数为 1–3，轮次上限为 1–2。
- [ ] `creation_authorization` 来自 User / Owner，范围、上限和全部精确 titles 匹配实际创建。
- [ ] 每个席位都是真实、独立、可读的会话，标题逐字符合规则。
- [ ] 咨询席未写入 `ROLES.md`，未伪装成 task/spike/Worker，未写入仓库或执行其他副作用。
- [ ] Round 1 使用同一 public brief digest，且在关闭前没有传播同席回答。
- [ ] 没有 Round 3；Round 2 如存在，有可改变结论的明确分歧原因。
- [ ] 每份已收回答都有稳定 `response_id`；完整模式还有匹配的运行时主动 callback receipt。
- [ ] 仓库记录没有 thread ID、live callback ID、provider receipt、本机路径、token 或凭据；公开 callback ID 均为 `null`。
- [ ] Leader 列出了共识、条件性共识、分歧、少数意见、风险和自己的综合建议，没有以多数票代替判断。
- [ ] 任何缺失席位或轮询恢复都被明确披露。
- [ ] synthesis 在 User / Owner 确认前只是 `proposed`。
- [ ] 任何 decision/task 派生物都有匹配的 `confirmed_scope + confirmation_id`。
- [ ] 没有把咨询确认解释为执行岗位创建、实现、验收、合并、发布或部署授权。

任一项为“否”，Leader 必须报告具体缺口，保留现有证据，并不得宣称完整的多会话研讨闭环成立。
