# AgentDesk 用户文档

这套文档面向准备安装、采用或维护 AgentDesk 的项目负责人和开发团队。它解释“为什么这样设计、怎样开始、如何运行、出现问题时怎样恢复”。

Skill 自身的 `SKILL.md` 与 `references/` 是给 Agent 执行的低层协议；这里的文档是给人阅读的使用说明。`SKILL.md` 与其引用的协议文件共同构成规范来源。公开 beta 内部若出现不一致，不应挑选较宽松的解释；应采用更保守的门禁，并参考[已知一致性问题](10-sharing-and-portability.md#beta-已知一致性问题)。

> 当前公开版本是 `v0.2.0-beta`，`v0.1.0-beta` 保留为旧版基线。tag 中的
> development 源码不能直接充当可写 Stable 发布；必须先按发布文档构建并激活
> 可验证的只读 bundle。

## 推荐阅读路径

### 第一次接触 AgentDesk

1. [产品概览](01-overview.md)
2. [系统架构](02-architecture.md)
3. [新项目快速开始](03-getting-started.md)

### 已有项目准备增加新功能

1. [既有项目接入](04-existing-project-adoption.md)
2. 可选：[咨询与圆桌研讨](11-consultation-and-roundtables.md)
3. [项目运行手册](05-operating-guide.md)
4. [任务、状态与证据](06-task-state-and-evidence.md)

### Leader、维护者与高级使用者

1. [岗位、模型与运行时](07-roles-models-and-runtime.md)
2. [校验与故障恢复](08-validation-and-recovery.md)
3. [看板与可观测性规划](09-dashboard-and-roadmap.md)
4. [公开分享与平台兼容](10-sharing-and-portability.md)
5. [咨询与圆桌研讨](11-consultation-and-roundtables.md)
6. [版本隔离与安全升级](12-version-isolation-and-upgrades.md)
7. [术语表](GLOSSARY.md)

## 文档地图

| 文档 | 回答的问题 |
| --- | --- |
| [产品概览](01-overview.md) | AgentDesk 是什么、解决什么问题、有什么边界？ |
| [系统架构](02-architecture.md) | 仓库控制面、真实岗位任务、Git 证据怎样协作？ |
| [新项目快速开始](03-getting-started.md) | 怎样安装并初始化一个新项目？ |
| [既有项目接入](04-existing-project-adoption.md) | 已开发完成或需求已确定的项目如何接入？ |
| [项目运行手册](05-operating-guide.md) | Leader、Worker、QA、Integration 每天怎样工作？ |
| [任务、状态与证据](06-task-state-and-evidence.md) | revision、attempt、dispatch、Accepted、Integrated 分别是什么？ |
| [岗位、模型与运行时](07-roles-models-and-runtime.md) | 如何命名岗位、选择模型、验证会话和回调？ |
| [校验与故障恢复](08-validation-and-recovery.md) | 怎样验证闭环，漏派发或漏回调时怎样处理？ |
| [看板与可观测性规划](09-dashboard-and-roadmap.md) | 当前看板能做什么，实时趣味看板准备怎样演进？ |
| [公开分享与平台兼容](10-sharing-and-portability.md) | 怎样通过 GitHub 分享，哪些能力依赖 Codex？ |
| [咨询与圆桌研讨](11-consultation-and-roundtables.md) | 执行前怎样组织 0–3 席的只读咨询，怎样汇总分歧并等待 Owner 确认？ |
| [版本隔离与安全升级](12-version-isolation-and-upgrades.md) | 怎样隔离稳定版与候选版，给项目锁版本，并在不中断活动任务的前提下升级？ |

## 权威资料入口

- [Skill 主入口](../skills/agentdesk/SKILL.md)
- [核心协议与状态机](../skills/agentdesk/references/protocol.md)
- [数据模型、Schema 与模板](../skills/agentdesk/references/schemas-and-templates.md)
- [事件、Outbox、消息与校验](../skills/agentdesk/references/events-outbox-and-validation.md)
- [Codex 运行时适配器](../skills/agentdesk/references/codex-runtime-adapter.md)
- [运行手册与故障恢复](../skills/agentdesk/references/runbooks-and-recovery.md)
