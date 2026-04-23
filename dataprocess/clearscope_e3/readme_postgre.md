# ClearScope E3 PostgreSQL 存储方案说明

## 一、概述

表结构与 CADETS/THEIA E3 完全一致（统一设计）。

ClearScope 特点：
- `subject_node_table.exec_name` = Android进程名/包名（来自 Subject.cmdLine）
- `event_table.cmdline` 永远为 NULL（无 EXECUTE/CLONE 事件）
- 只有 37 个进程节点

## 二、数据库设置

```bash
# 创建
psql -U postgres -f init_database.sql

# 提取
python extract_clearscope_e3_postgres.py \
    --input_dir /mnt/disk/darpa/clearscope_e3 \
    --db_name clearscope_e3

# 重建
psql -U postgres -c "DROP DATABASE IF EXISTS clearscope_e3;"
psql -U postgres -f init_database.sql
```

## 三、表结构（与 CADETS/THEIA 完全一致）

### subject_node_table

| 列 | 说明 | ClearScope特点 |
|----|------|-------------|
| node_uuid | UUID | |
| hash_id | SHA256(uuid) | |
| exec_name | 进程名 | Android包名（如 `system_server`），来自 Subject.cmdLine |
| index_id | 编号 | |

### file_node_table

| 列 | 说明 | ClearScope特点 |
|----|------|-------------|
| path | 文件路径 | 来自 FileObject.baseObject.props.map.path（100%填充） |

### event_table

| 列 | 说明 | ClearScope特点 |
|----|------|-------------|
| cmdline | 命令行 | **永远为 NULL**（无 EXECUTE/CLONE 事件） |

## 四、查询示例

```sql
-- 查看所有进程（只有37个）
SELECT exec_name, index_id FROM subject_node_table ORDER BY index_id;

-- 查找攻击相关文件（Firefox缓存）
SELECT node_uuid, path FROM file_node_table
WHERE path LIKE '%firefox%' OR path LIKE '%fennec%';

-- 查看某进程的所有文件操作
SELECT e.operation, f.path, e.timestamp_rec
FROM event_table e
JOIN subject_node_table s ON e.src_uuid = s.node_uuid OR e.dst_uuid = s.node_uuid
JOIN file_node_table f ON e.src_uuid = f.node_uuid OR e.dst_uuid = f.node_uuid
WHERE s.exec_name = 'org.mozilla.fennec_firefox_dev'
ORDER BY e.timestamp_rec;

-- 按时间窗口查询
SELECT * FROM event_table
WHERE timestamp_rec >= 1523462040000000000
  AND timestamp_rec <  1523465280000000000
ORDER BY timestamp_rec;
```

## 五、预期数据量

| 表 | 预估行数 |
|----|---------|
| subject_node_table | ~37 |
| file_node_table | ~30,000 |
| netflow_node_table | ~2,000 |
| event_table | ~200,000（过滤Binder后） |

注：原始约5,000,000条Event中，91%指向SrcSinkObject（被丢弃），实际写入边表的约200,000条。

## 六、与 CADETS/THEIA 数据库的互操作

三个数据库表结构完全一致，下游代码通用：

```python
def load_from_db(db_name):
    """统一加载，适用于 cadets_e3, theia_e3, clearscope_e3"""
    connect = psycopg2.connect(database=db_name, ...)
    cur = connect.cursor()
    cur.execute("SELECT * FROM event_table ORDER BY timestamp_rec")
    ...
```
