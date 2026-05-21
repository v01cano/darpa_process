# ClearScope E5 PostgreSQL 存储方案说明

## 一、概述

表结构与 CADETS / THEIA / ClearScope_E3 / TRACE / FiveDirections E3 **完全一致**（六个数据集统一设计）。

ClearScope E5 特点：
- CDM20 schema（与 E3 的 CDM18 不同的命名空间）
- Android 平台（HOST_MOBILE）
- 无 SUBJECT_THREAD（不需要 thread→process 合并）
- `subject_node_table.exec_name` = Subject.cmdLine.string（包名或二进制路径）
- `event_table.cmdline` 永远为 NULL（ClearScope 无 properties.cmdLine）
- ProvenanceTagNode / IpcObject / SrcSinkObject 全部丢弃

## 二、数据库设置

```bash
# 创建数据库和表
psql -U postgres -f init_database.sql

# 执行提取
python extract_clearscope_e5_postgres.py \
    --input_dir /mnt/disk/darpa/clearscope_e5 \
    --db_name clearscope_e5

# 重建
psql -U postgres -c "DROP DATABASE IF EXISTS clearscope_e5;"
psql -U postgres -f init_database.sql
```

## 三、表结构特殊说明

### subject_node_table

| 列 | 说明 | ClearScope E5 特点 |
|----|------|-------------------|
| node_uuid | UUID（小写） | 仅 SUBJECT_PROCESS，无 THREAD |
| exec_name | 进程名 | Android 包名（`com.android.bluetooth`）或路径（`/system/bin/dex2oat`） |

### file_node_table

| 列 | 说明 | ClearScope E5 特点 |
|----|------|-------------------|
| path | 文件路径 | 来自 `baseObject.properties.map.path`（100% 填充） |

### netflow_node_table

| 列 | 说明 | ClearScope E5 特点 |
|----|------|-------------------|
| src_addr / src_port | 本地 | 从 `{"string":"..."}` / `{"int":0}` dict 解包 |
| dst_addr / dst_port | 远端 | 同上，可能为空（监听 socket） |

### event_table

| 列 | 说明 |
|----|------|
| operation | 边类型，已过滤到 15 种 |
| cmdline | 永远为 NULL |

## 四、查询示例

### 查看活跃进程及其行为类型

```sql
SELECT s.exec_name, COUNT(*) AS event_cnt,
       COUNT(DISTINCT e.operation) AS op_kinds
FROM event_table e
JOIN subject_node_table s
  ON e.src_uuid = s.node_uuid OR e.dst_uuid = s.node_uuid
GROUP BY s.exec_name
ORDER BY event_cnt DESC
LIMIT 20;
```

### 查找进程访问的敏感文件

```sql
SELECT s.exec_name, f.path, e.operation, COUNT(*) AS cnt
FROM event_table e
JOIN subject_node_table s ON e.dst_uuid = s.node_uuid
JOIN file_node_table f    ON e.src_uuid = f.node_uuid
WHERE e.operation = 'EVENT_READ'
  AND f.path LIKE '/data/data/%'
GROUP BY s.exec_name, f.path, e.operation
ORDER BY cnt DESC
LIMIT 50;
```

### 查找进程网络连接

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

### 查找 EXECUTE / FORK / CLONE / LOADLIBRARY（量极少但语义重要）

```sql
SELECT e.operation, e.timestamp_rec,
       sf.path AS source, ts.exec_name AS target_proc
FROM event_table e
LEFT JOIN file_node_table sf    ON e.src_uuid = sf.node_uuid
LEFT JOIN subject_node_table ts ON e.dst_uuid = ts.node_uuid
WHERE e.operation IN ('EVENT_EXECUTE', 'EVENT_LOADLIBRARY',
                      'EVENT_FORK', 'EVENT_CLONE')
ORDER BY e.timestamp_rec;
```

### 持久化文件写入检测

```sql
SELECT s.exec_name, f.path, COUNT(*) AS write_cnt
FROM event_table e
JOIN subject_node_table s ON e.src_uuid = s.node_uuid
JOIN file_node_table f    ON e.dst_uuid = f.node_uuid
WHERE e.operation IN ('EVENT_WRITE', 'EVENT_CREATE_OBJECT', 'EVENT_RENAME')
  AND (f.path LIKE '/data/local/%' OR f.path LIKE '/data/system/%')
GROUP BY s.exec_name, f.path
HAVING COUNT(*) > 5
ORDER BY write_cnt DESC;
```

## 五、与其他数据集互操作

六个数据集表结构完全相同，可统一加载：

```python
import psycopg2

def load_from_db(db_name):
    """适用于 cadets_e3 / theia_e3 / clearscope_e3 / clearscope_e5 /
       trace_e3 / fivedirections_e3"""
    conn = psycopg2.connect(database=db_name, user='postgres',
                            password='postgres', host='localhost')
    cur = conn.cursor()
    cur.execute("SELECT * FROM event_table ORDER BY timestamp_rec")
    return cur.fetchall()
```

## 六、预期数据量

基于 1000 万行抽样推算（双 bin.json 共 ~24 GB）：

| 表 | 预估行数 |
|----|---------|
| subject_node_table | ~3,000（Android 进程少） |
| file_node_table | ~500,000 |
| netflow_node_table | ~10,000 |
| event_table | ~1 亿（过滤后保留 15 种边） |

## 七、注意事项

1. **UUID 全部小写存储**：提取时已 `.lower()`，外部查询时务必也用小写。

2. **NetFlow 字段**：来自 dict 解包后的 plain string；空值（监听 socket 等）会是空字符串 ``''``，不是 NULL。

3. **event_table.cmdline 永远 NULL**：ClearScope CDM20 没有 `properties.cmdLine` 字段。
   要用 cmdLine 信息请 JOIN `subject_node_table.exec_name`。

4. **ProvenanceTagNode 不入库**：此数据集 TA1 标签量极大（~22 万 / 1000 万行），
   语义上属于 taint 跟踪而非系统行为，全部丢弃。

5. **零 UUID**：原始数据中存在 `00000000-0000-0000-0000-000000000000`（kernel 占位）
   作为 Subject UUID 出现，但 `parentSubject` 也是同样的零 UUID。
   不可用于推断进程父子关系，只能依赖 cmdLine。
