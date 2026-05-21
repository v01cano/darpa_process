# CADETS E5 PostgreSQL 存储方案说明

## 一、概述

表结构与 CADETS_E3 / THEIA_E3 / CLEARSCOPE_E3 / TRACE_E3 / FIVEDIRECTIONS_E3 /
CLEARSCOPE_E5 **完全一致**（七个数据集统一）。

CADETS E5 特点：
- CDM20 schema（与 E3 的 CDM18 不同的命名空间）
- FreeBSD 平台，3 种节点：subject / file / netflow
- `subject_node_table.exec_name` = Event.properties.exec（持续覆盖最后值）
- `file_node_table.path` = Event.predicateObjectPath（OPEN/EXECUTE/RENAME 等事件携带）
- `netflow_node_table` 字段从 dict 解包
- `event_table.cmdline` 仅在 EXECUTE / FORK 边有值（FORK 上回填子进程 cmdLine）
- SrcSinkObject / IpcObject / EVENT_FLOWS_TO / EVENT_MODIFY_PROCESS 全部丢弃

## 二、数据库设置

```bash
psql -U postgres -f init_database.sql
python extract_cadets_e5_postgres.py --input_dir /mnt/disk/darpa/cch_refine/cadets_e5_json
# 重建
psql -U postgres -c "DROP DATABASE IF EXISTS cadets_e5;"
psql -U postgres -f init_database.sql
```

## 三、表结构特殊说明

### subject_node_table

| 列 | 说明 | CADETS E5 特点 |
|----|------|---------------|
| node_uuid | UUID（小写） | SUBJECT_PROCESS 唯一 |
| exec_name | 进程名 | Event.exec 累积覆盖（如 `bash`, `sshd`, `cron`） |

### file_node_table

| 列 | 说明 | CADETS E5 特点 |
|----|------|---------------|
| path | 文件路径 | Event.predicateObjectPath（很多文件 = NULL，因仅 17.6% Event 有 path） |

### netflow_node_table

| 列 | 说明 | CADETS E5 特点 |
|----|------|---------------|
| src/dst addr/port | 网络四元组 | 从 dict 解包（与 E3 不同） |

### event_table

| 列 | 说明 |
|----|------|
| operation | 13 种保留事件 |
| cmdline | EXECUTE / FORK 边有值，其余 NULL |

## 四、查询示例

### EXECUTE 链：谁执行了什么

```sql
SELECT s.exec_name AS executor, f.path AS exec_target, e.cmdline,
       e.timestamp_rec
FROM event_table e
JOIN file_node_table f    ON e.src_uuid = f.node_uuid
JOIN subject_node_table s ON e.dst_uuid = s.node_uuid
WHERE e.operation = 'EVENT_EXECUTE'
ORDER BY e.timestamp_rec
LIMIT 50;
```

### FORK 树：父→子带 cmdLine

```sql
SELECT sp.exec_name AS parent, sc.exec_name AS child, e.cmdline
FROM event_table e
JOIN subject_node_table sp ON e.src_uuid = sp.node_uuid
JOIN subject_node_table sc ON e.dst_uuid = sc.node_uuid
WHERE e.operation = 'EVENT_FORK'
ORDER BY e.timestamp_rec;
```

### 进程网络连接

```sql
SELECT s.exec_name, n.dst_addr, n.dst_port, COUNT(*) AS cnt
FROM event_table e
JOIN subject_node_table s ON e.src_uuid = s.node_uuid
JOIN netflow_node_table n ON e.dst_uuid = n.node_uuid
WHERE e.operation = 'EVENT_CONNECT'
  AND n.dst_addr <> ''
GROUP BY s.exec_name, n.dst_addr, n.dst_port
ORDER BY cnt DESC;
```

### 可疑文件创建（持久化）

```sql
SELECT s.exec_name, f.path, e.timestamp_rec
FROM event_table e
JOIN subject_node_table s ON e.src_uuid = s.node_uuid
JOIN file_node_table f    ON e.dst_uuid = f.node_uuid
WHERE e.operation IN ('EVENT_CREATE_OBJECT', 'EVENT_WRITE', 'EVENT_RENAME')
  AND (f.path LIKE '/tmp/%' OR f.path LIKE '/var/tmp/%'
       OR f.path LIKE '/dev/shm/%')
ORDER BY e.timestamp_rec;
```

## 五、与其他数据集互操作

七个数据集表结构完全相同，可统一加载：

```python
def load(db):
    """cadets_e3/e5, theia_e3, clearscope_e3/e5, trace_e3, fivedirections_e3"""
    conn = psycopg2.connect(database=db, user='postgres', password='postgres',
                            host='localhost')
    cur = conn.cursor()
    cur.execute("SELECT * FROM event_table ORDER BY timestamp_rec")
    return cur.fetchall()
```

## 六、预期数据量

基于 1000 万行抽样推算：

| 表 | 预估行数 |
|----|---------|
| subject_node_table | ~30,000 |
| file_node_table | ~300,000 |
| netflow_node_table | ~15,000 |
| event_table | ~数千万（过滤后） |

## 七、注意事项

1. **UUID 小写存储**：外部 GT / 查询 UUID 时一律 `.lower()`。
2. **NetFlow 字段**：dict 已解包成纯 string；空值（监听 socket 等）是空字符串 `''`，不是 NULL。
3. **event_table.cmdline**：只在 EXECUTE / FORK 上非空。其余 JOIN `subject_node_table.exec_name` 看 exec。
4. **file 表 path 可能 NULL**：仅 OPEN/EXECUTE/RENAME/UNLINK/LINK/MODIFY_FILE_ATTRIBUTES 等事件携带 path，其他事件涉及的文件可能从未被命名（这是 CADETS 系列共有问题）。
