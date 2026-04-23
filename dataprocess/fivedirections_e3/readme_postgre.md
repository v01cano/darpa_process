# FiveDirections E3 PostgreSQL 存储方案说明

## 一、概述

表结构与 CADETS/THEIA/ClearScope/TRACE E3 完全一致（五个数据集统一设计）。

FiveDirections 特点：
- `subject_node_table.exec_name` = EXECUTE path（简洁程序名，如 `svchost.exe`）
- `event_table.cmdline`：
  - FORK 边：dst 进程的 Subject.cmdLine
  - EXECUTE 边：src 进程的 Subject.cmdLine
  - 其他边：NULL
- SUBJECT_THREAD UUID 合并到父 PROCESS，不单独作为节点
- RegistryKeyObject 归入 file 节点表（path 列存储 key）

## 二、数据库设置

```bash
# 创建数据库和表
psql -U postgres -f init_database.sql

# 执行提取
python extract_fivedirections_e3_postgres.py \
    --input_dir /mnt/disk/darpa/fivedirections_e3 \
    --db_name fivedirections_e3

# 重建
psql -U postgres -c "DROP DATABASE IF EXISTS fivedirections_e3;"
psql -U postgres -f init_database.sql
```

## 三、表结构特殊说明

### subject_node_table

| 列 | 说明 | FiveDirections 特点 |
|----|------|-------------------|
| node_uuid | UUID | 仅 PROCESS UUID，不含 THREAD |
| exec_name | 程序名 | 如 `svchost.exe`（来自 EXECUTE path 或 cmdLine 提取） |

**注意：** THREAD 不在 subject_node_table 中，它们的事件通过 UUID 合并归属到父 PROCESS。

### file_node_table

| 列 | 说明 | FiveDirections 特点 |
|----|------|-------------------|
| path | 文件路径 | 包含三类：1) 普通文件路径；2) PE 文件名；3) Registry 的 key |

**RegistryKey 示例：**
```
path = "\REGISTRY\MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
```

## 四、查询示例

### 查看所有 svchost 实例

```sql
-- 同名进程（不同参数）
SELECT node_uuid, exec_name FROM subject_node_table
WHERE exec_name = 'svchost.exe';
-- 返回多个 UUID，全部叫 svchost.exe
```

### 查看某个 svchost 的完整 cmdLine（通过 FORK 边）

```sql
SELECT s.exec_name, e.cmdline
FROM event_table e
JOIN subject_node_table s ON e.dst_uuid = s.node_uuid
WHERE e.operation = 'EVENT_FORK'
  AND s.exec_name = 'svchost.exe'
ORDER BY e.timestamp_rec;
-- cmdLine 列显示各实例的完整参数
```

### 查找注册表持久化行为

```sql
-- 写入 Run 键（经典持久化）
SELECT s.exec_name, f.path AS registry_key, e.timestamp_rec
FROM event_table e
JOIN subject_node_table s ON e.src_uuid = s.node_uuid
JOIN file_node_table f ON e.dst_uuid = f.node_uuid
WHERE e.operation = 'EVENT_WRITE'
  AND f.path LIKE '%\Run%'
ORDER BY e.timestamp_rec;
```

### 查找 PE 文件加载链

```sql
-- 每个进程加载了哪个 PE 文件（反转后是 file → process）
SELECT f.path AS pe_file, s.exec_name AS process, e.cmdline
FROM event_table e
JOIN file_node_table f ON e.src_uuid = f.node_uuid
JOIN subject_node_table s ON e.dst_uuid = s.node_uuid
WHERE e.operation = 'EVENT_EXECUTE'
ORDER BY e.timestamp_rec;
```

### 查找 DLL 注入（LOADLIBRARY）

```sql
-- 进程加载了哪些 DLL（反转后是 dll → process）
SELECT f.path AS dll, s.exec_name AS process, COUNT(*) AS load_count
FROM event_table e
JOIN file_node_table f ON e.src_uuid = f.node_uuid
JOIN subject_node_table s ON e.dst_uuid = s.node_uuid
WHERE e.operation = 'EVENT_LOADLIBRARY'
GROUP BY f.path, s.exec_name
ORDER BY load_count DESC;
```

### 完整攻击链追踪

```sql
-- 从某进程出发的所有 FORK 子进程
WITH RECURSIVE process_tree AS (
    SELECT node_uuid, exec_name, 0 AS depth
    FROM subject_node_table WHERE exec_name = 'cmd.exe'
    UNION ALL
    SELECT s.node_uuid, s.exec_name, pt.depth + 1
    FROM process_tree pt
    JOIN event_table e ON e.src_uuid = pt.node_uuid AND e.operation = 'EVENT_FORK'
    JOIN subject_node_table s ON e.dst_uuid = s.node_uuid
    WHERE pt.depth < 5
)
SELECT DISTINCT * FROM process_tree;
```

## 五、与其他数据集数据库互操作

五个数据集表结构完全相同：

```python
def load_from_db(db_name):
    """统一加载，适用于 cadets_e3, theia_e3, clearscope_e3, trace_e3, fivedirections_e3"""
    connect = psycopg2.connect(database=db_name, ...)
    cur = connect.cursor()
    cur.execute("SELECT * FROM event_table ORDER BY timestamp_rec")
    ...
```

## 六、预期数据量

基于 21.5M 行样本（ta1-fivedirections-e3-official_fixed.json）推算：

| 表 | 预估行数 |
|----|---------|
| subject_node_table | ~5,000 |
| file_node_table | ~500,000（含RegistryKey） |
| netflow_node_table | ~10,000 |
| event_table | ~数千万（过滤后） |

## 七、注意事项

1. **THREAD 不在 subject_node_table 中** — 如果下游代码需要线程级分析，需要从原始 CDM 数据重新提取。

2. **RegistryKeyObject 归入 file_node_table** — 通过 `path` 字段的值（以 `\REGISTRY\` 开头）可以区分注册表项和普通文件。

3. **EXECUTE 边 src 是 file，dst 是 process**（因为反转）— 这与 CADETS/THEIA 一致。

4. **同名进程（如 svchost.exe）** — 用 UUID 或 FORK 边上的 cmdLine 区分实例。
