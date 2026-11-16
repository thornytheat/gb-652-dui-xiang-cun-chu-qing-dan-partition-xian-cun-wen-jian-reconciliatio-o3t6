# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `contract_version` | string | 否 | 清单接收时绑定的摘要契约版本（`v1-etag` / `v2-digest`） |
| `evidence` | string | 否 | 核验实际采用的证据组合（如 `size+etag`、`size+digest`） |
| `migration_state` | string | 否 | 历史记录迁移裁决（`pending` / `carried` / `refused` / `current`） |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。
