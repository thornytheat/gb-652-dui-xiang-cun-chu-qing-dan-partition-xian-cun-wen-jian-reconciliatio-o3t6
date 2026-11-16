# 对象存储清单与分区现存文件对账

        面向一线复核人员的单服务数据工作台。样例数据随仓库提供，结论保留行号、原因码和原值。

        ## 业务模块

        - M1 清单采信：`/api/manifests/import`
- M2 路径契约：`/api/paths/normalize`
- M3 对象核验：`/api/objects/verify`
- M4 分区判定：`/api/partitions`
- M5 差异对账：`/api/reconcile`
- M6 批次导出：`/api/export/differences.csv`

        ## 核心规则

        路径规范化后按 key、size 与摘要逐项比对，目录标记、空分区和缺失对象不得混为一类。

        ## 摘要契约

        清单在接收时绑定契约版本：`v1-etag` 按 大小+分片标识 验收，`v2-digest` 按 大小+整文件摘要 验收。
        分片标识与内容摘要互不替代，大小相同也不混用证明；升级不影响在途清单，重试沿用上次证据且不新增核验记录。

        - 升级契约：`/api/objects/contract/upgrade`
- 重试核验：`/api/objects/retry`
- 历史迁移：`/api/objects/migrate`（游标推进，中断可续跑；缺旧契约证据的记录拒绝猜测补齐）

        ## 启动

        ```bash
        docker compose up --build
        ```

        浏览器访问 `http://localhost:18211`。运行测试：

        ```bash
        docker compose exec -T app pytest -q
        ```

        ## 样例流程

        依次运行六个模块；最后在批次页复核输入、有效、隔离与输出合计。同一输入摘要重复执行时复用原 `run_id`。

        ## 已知不覆盖

        不处理加密文件与需要外部密钥的对象；不连接真实生产存储。
