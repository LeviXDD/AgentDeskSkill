# 决策日志

追加持久决策。不要重写历史；通过新增记录取代旧决策。

咨询综合报告不是决策。由咨询派生的记录必须取得 User / Owner 明确确认，并在 `Source / confirmation` 中同时引用 `consultation_id` 和 `confirmation_id`。未确认、已拒绝或暂缓的咨询结论留在咨询记录中，不得追加到本日志。咨询确认不授权执行、验收、合并、发布或部署。

表头保留原接口名称，供校验器读取。

| ID | Date | Decision | Rationale | Source / confirmation | Supersedes |
| --- | --- | --- | --- | --- | --- |
