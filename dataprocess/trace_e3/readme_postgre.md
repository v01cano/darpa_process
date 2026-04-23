# TRACE E3 PostgreSQL 存储方案说明

## 一、概述

表结构与 CADETS/THEIA/ClearScope E3 完全一致（统一设计）。

TRACE 特点：
- `subject_node_table.exec_name` = Subject.properties.map.name（100%填充，无需Event更新）
- `event_table.cmdline` 在 FORK/CLONE/EXECUTE 边上有值（来源均为 Subject 记录）
- EVENT_EXECUTE 连接两个 Subject（不反转）

## 二、数据库设置

```bash
psql -U postgres -f init_database.sql
python extract_trace_e3_postgres.py --input_dir /mnt/disk/darpa/trace_e3

# 重建
psql -U postgres -c "DROP DATABASE IF EXISTS trace_e3;"
psql -U postgres -f init_database.sql
```

## 三、TRACE 特有的边语义

| operation | src | dst | cmdline | 说明 |
|-----------|-----|-----|---------|------|
| EVENT_FORK | 父进程 | 子进程 | 子进程将变成的命令行 | 传统fork |
| EVENT_CLONE | 父(PROCESS/UNIT) | 子进程 | 子进程的cmdLine | 并行工作进程 |
| EVENT_EXECUTE | **旧进程** | **新进程(新UUID)** | 新进程的cmdLine | **不反转** |

EVENT_EXECUTE 的 dst 是另一个 Subject 节点（新UUID），不是 FileObject。

## 四、查询示例

```sql
-- 查看 FORK→EXECUTE 链
SELECT p.exec_name AS parent, c.exec_name AS child,
       n.exec_name AS new_identity, e2.cmdline
FROM event_table e1
JOIN subject_node_table p ON e1.src_uuid = p.node_uuid
JOIN subject_node_table c ON e1.dst_uuid = c.node_uuid
JOIN event_table e2 ON e2.src_uuid = c.node_uuid AND e2.operation = 'EVENT_EXECUTE'
JOIN subject_node_table n ON e2.dst_uuid = n.node_uuid
WHERE e1.operation = 'EVENT_FORK'
ORDER BY e1.timestamp_rec
LIMIT 20;

-- 查找攻击相关进程
SELECT node_uuid, exec_name FROM subject_node_table
WHERE exec_name IN ('firefox', 'cache', 'tcexec', 'pine');

-- EXECUTE链（连续exec）
SELECT s1.exec_name AS old, s2.exec_name AS new, e.cmdline
FROM event_table e
JOIN subject_node_table s1 ON e.src_uuid = s1.node_uuid
JOIN subject_node_table s2 ON e.dst_uuid = s2.node_uuid
WHERE e.operation = 'EVENT_EXECUTE'
ORDER BY e.timestamp_rec;
```

## 五、与其他数据集数据库的互操作

四个数据库表结构完全一致：

```python
def load_from_db(db_name):
    """统一加载，适用于 cadets_e3, theia_e3, clearscope_e3, trace_e3"""
    connect = psycopg2.connect(database=db_name, ...)
    cur = connect.cursor()
    cur.execute("SELECT * FROM event_table ORDER BY timestamp_rec")
    ...
```

**唯一需要注意：** TRACE 的 EVENT_EXECUTE 边连接的是两个 Subject（process→process），而 CADETS/THEIA 连接的是 file→process（反转后）。下游代码需要根据 operation 判断 dst 类型。
