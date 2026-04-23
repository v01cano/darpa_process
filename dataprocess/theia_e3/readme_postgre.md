# THEIA E3 PostgreSQL 存储方案说明

## 一、概述

表结构与 CADETS E3 完全一致（统一设计），两个数据集的下游代码无需区分。

| 脚本 | 存储方式 | 输出 |
|------|---------|------|
| `extract_theia_e3.py` | 文件 | uuid2name.pkl, datalist.pkl, edges.csv |
| `extract_theia_e3_postgres.py` | PostgreSQL | 4 张表 |

## 二、数据库设置

```bash
# 创建数据库和表
psql -U postgres -f init_database.sql

# 执行提取
python extract_theia_e3_postgres.py \
    --input_dir /mnt/disk/darpa/theia_e3 \
    --db_name theia_e3 \
    --db_user postgres \
    --db_password postgres \
    --db_host localhost \
    --db_port 5432

# 重建
psql -U postgres -c "DROP DATABASE IF EXISTS theia_e3;"
psql -U postgres -f init_database.sql
```

## 三、表结构（与 CADETS E3 完全一致）

### subject_node_table

| 列 | 类型 | 说明 |
|----|------|------|
| node_uuid | VARCHAR PK | 原始UUID |
| hash_id | VARCHAR | SHA256(uuid) |
| exec_name | VARCHAR | 进程的最终exec名（通过EXECUTE dst文件名更新） |
| index_id | BIGINT | 顺序编号 |

**与 CADETS 的区别：** CADETS 的 exec_name 来自 Event.exec 最终值，THEIA 的 exec_name 来自最后一次 EXECUTE 的 dst FileObject filename。两者语义一致，数据来源不同。

### file_node_table

| 列 | 类型 | 说明 |
|----|------|------|
| node_uuid | VARCHAR PK | 原始UUID |
| hash_id | VARCHAR | SHA256(uuid) |
| path | VARCHAR | 文件路径（来自 FileObject.baseObject.props.map.filename） |
| index_id | BIGINT | 顺序编号 |

**与 CADETS 的区别：** CADETS 的 path 来自 Event.predicateObjectPath，THEIA 的 path 来自 FileObject 记录本身。

### netflow_node_table — 与 CADETS 完全一致

### event_table

| 列 | 类型 | 说明 |
|----|------|------|
| src_uuid | VARCHAR | 源节点UUID（反转后方向） |
| src_index_id | BIGINT | 源节点index_id |
| operation | VARCHAR | 事件类型 |
| dst_uuid | VARCHAR | 目标节点UUID（反转后方向） |
| dst_index_id | BIGINT | 目标节点index_id |
| event_uuid | VARCHAR | 事件UUID |
| timestamp_rec | BIGINT | 纳秒时间戳 |
| cmdline | VARCHAR | EXECUTE: Event.cmdLine; CLONE: 子进程cmdLine |
| _id | SERIAL PK | 自增主键 |

**CLONE边的cmdLine：** 优先使用子进程第一个EXECUTE的Event.cmdLine，fallback到子进程的Subject.cmdLine。与CADETS的FORK边设计一致。

## 四、查询示例

```sql
-- 查找攻击相关进程（如gtcache恶意程序）
SELECT node_uuid, exec_name FROM subject_node_table
WHERE exec_name LIKE '%gtcache%' OR exec_name LIKE '%drakon%';

-- 查找进程的所有操作
SELECT e.operation, e.timestamp_rec, e.cmdline,
       f.path AS dst_file, n.dst_addr
FROM event_table e
JOIN subject_node_table s ON e.src_uuid = s.node_uuid
LEFT JOIN file_node_table f ON e.dst_uuid = f.node_uuid
LEFT JOIN netflow_node_table n ON e.dst_uuid = n.node_uuid
WHERE s.exec_name = 'gtcache'
ORDER BY e.timestamp_rec;

-- 查找可疑的EXECUTE
SELECT s.exec_name, f.path AS executed_file, e.cmdline, e.timestamp_rec
FROM event_table e
JOIN file_node_table f ON e.src_uuid = f.node_uuid
JOIN subject_node_table s ON e.dst_uuid = s.node_uuid
WHERE e.operation = 'EVENT_EXECUTE'
ORDER BY e.timestamp_rec;

-- CLONE关系（谁创建了谁）
SELECT p.exec_name AS parent, c.exec_name AS child, e.cmdline
FROM event_table e
JOIN subject_node_table p ON e.src_uuid = p.node_uuid
JOIN subject_node_table c ON e.dst_uuid = c.node_uuid
WHERE e.operation = 'EVENT_CLONE' AND e.cmdline IS NOT NULL
ORDER BY e.timestamp_rec;
```

## 五、预期数据量

| 表 | 预估行数 |
|----|---------|
| subject_node_table | ~279,000 |
| file_node_table | ~1,022,000 |
| netflow_node_table | ~186,000 |
| event_table | ~5,000,000 (过滤后) |

## 六、与 CADETS E3 数据库的互操作

两个数据库表结构完全一致，下游代码可以用相同逻辑处理：

```python
def load_from_db(db_name):
    """统一的数据加载函数，适用于cadets_e3和theia_e3"""
    connect = psycopg2.connect(database=db_name, ...)
    cur = connect.cursor()
    cur.execute("SELECT * FROM event_table ORDER BY timestamp_rec")
    ...
```
