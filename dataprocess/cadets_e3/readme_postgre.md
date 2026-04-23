# CADETS E3 PostgreSQL 存储方案说明

## 一、概述

本方案将提取的溯源图数据存入 PostgreSQL 数据库，表结构参考 Orthrus 的设计，并根据我们的数据分析做了针对性改动。

提供两个版本的提取脚本，提取逻辑完全一致，仅存储方式不同：

| 脚本 | 存储方式 | 输出 |
|------|---------|------|
| `extract_cadets_e3.py` | 内存 + 文件 | uuid2name.pkl, datalist.pkl, edges.csv |
| `extract_cadets_e3_postgres.py` | PostgreSQL | 4 张数据库表 |

## 二、数据库设置

### 2.1 前置条件

```bash
# 确认 PostgreSQL 已安装并运行
sudo systemctl status postgresql

# 确认可以连接
psql -U postgres -c "SELECT version();"

# 安装 Python 依赖
pip install psycopg2-binary
```

### 2.2 创建数据库和表

```bash
# 方法一：使用 SQL 脚本
psql -U postgres -f init_database.sql

# 方法二：如果需要重建（会删除已有数据）
psql -U postgres -c "DROP DATABASE IF EXISTS cadets_e3;"
psql -U postgres -f init_database.sql
```

### 2.3 执行数据提取

```bash
python extract_cadets_e3_postgres.py \
    --input_dir /mnt/disk/darpa/cadets_e3 \
    --db_name cadets_e3 \
    --db_user postgres \
    --db_password postgres \
    --db_host localhost \
    --db_port 5432
```

## 三、表结构设计

### 3.1 与 Orthrus 的表结构对比

#### subject_node_table（进程节点）

| 列 | Orthrus | 我们 | 说明 |
|----|---------|------|------|
| node_uuid | ✅ VARCHAR | ✅ VARCHAR | 原始 UUID |
| hash_id | ✅ VARCHAR | ✅ VARCHAR | SHA256(uuid) |
| path | ✅ VARCHAR（全 null） | ❌ **去掉** | Orthrus 存了但填充率 0% |
| cmd | ✅ VARCHAR（全 null） | ❌ **去掉** | cmdLine 不是节点属性 |
| exec_name | ❌ | ✅ **新增** | 进程的最终 exec 名 |
| index_id | ✅ BIGINT | ✅ BIGINT | 顺序编号 |

**改动理由：**
- `path`: CADETS E3 中 Subject 记录的 `properties.name` 和 `properties.map.path` 填充率均为 **0%**。Orthrus 存了此列但全部为 null，浪费空间。
- `cmd`: cmdLine 仅在 0.5% 的 Event (EVENT_EXECUTE) 中出现，是**边属性**而非节点属性。一个进程可能多次 exec，每次 cmdLine 不同。Orthrus 将其存为节点属性，在 CADETS E3 上全部为 null（实际从 Event.exec 提取，与 cmdLine 混淆）。
- `exec_name`: 新增列，存储进程的最终 exec 名（来自 Event.properties.map.exec 的最后一个值），填充率 **99.9%**。

#### file_node_table（文件节点） — 与 Orthrus 一致

| 列 | 类型 | 说明 |
|----|------|------|
| node_uuid | VARCHAR | 原始 UUID |
| hash_id | VARCHAR | SHA256(uuid) |
| path | VARCHAR | 文件路径（从 Event.predicateObjectPath 获取） |
| index_id | BIGINT | 顺序编号 |

注意：包含 FILE_OBJECT_FILE 和 FILE_OBJECT_DIR（Orthrus 只有 FILE_OBJECT_FILE）。

#### netflow_node_table（网络节点） — 与 Orthrus 一致

| 列 | 类型 | 说明 |
|----|------|------|
| node_uuid | VARCHAR | 原始 UUID |
| hash_id | VARCHAR | SHA256(uuid) |
| src_addr | VARCHAR | 本地地址 |
| src_port | VARCHAR | 本地端口 |
| dst_addr | VARCHAR | 远程地址 |
| dst_port | VARCHAR | 远程端口 |
| index_id | BIGINT | 顺序编号 |

#### event_table（事件/边表）

| 列 | Orthrus | 我们 | 说明 |
|----|---------|------|------|
| src_node (hash) | ✅ VARCHAR | ❌ **改为 uuid** | Orthrus 用 hash_id，可读性差 |
| src_uuid | ❌ | ✅ **新增** | 源节点原始 UUID，直接可追溯到 CDM |
| src_index_id | ✅ VARCHAR | ✅ BIGINT | 源节点 index_id |
| operation | ✅ VARCHAR | ✅ VARCHAR | 事件类型 |
| dst_node (hash) | ✅ VARCHAR | ❌ **改为 uuid** | 同上 |
| dst_uuid | ❌ | ✅ **新增** | 目标节点原始 UUID |
| dst_index_id | ✅ VARCHAR | ✅ BIGINT | 目标节点 index_id |
| event_uuid | ✅ VARCHAR | ✅ VARCHAR | 事件 UUID |
| timestamp_rec | ✅ BIGINT | ✅ BIGINT | 纳秒时间戳 |
| cmdline | ❌ | ✅ **新增** | EVENT_EXECUTE/FORK 的命令行 |
| _id | ✅ SERIAL | ✅ SERIAL | 自增主键 |

**改动理由：**
- `src_uuid/dst_uuid 替代 src_node/dst_node (hash)`: UUID 是 CDM 原始标识，可直接追溯到原始数据，且 hash = SHA256(uuid) 是确定性计算随时可算出，无需作为外键。Orthrus 用 hash 是因为其 netflow 节点对属性值做 hash（为合并同地址连接），但我们统一用 SHA256(uuid)，hash 没有额外语义。
- `cmdline`: 新增列。EVENT_EXECUTE 时存储完整命令行，EVENT_FORK 时回填子进程命令行，其他事件为 NULL。

### 3.2 额外索引

相比 Orthrus，我们增加了 4 个索引以加速查询：

```sql
CREATE INDEX idx_event_timestamp ON event_table (timestamp_rec);
CREATE INDEX idx_event_src ON event_table (src_node);
CREATE INDEX idx_event_dst ON event_table (dst_node);
CREATE INDEX idx_event_operation ON event_table (operation);
```

## 四、边方向说明

**重要：event_table 中存储的边方向已经过反转。** READ/RECV/EXECUTE/OPEN 类事件的 src 和 dst 已经互换，表示数据流方向。

| operation | 存储方向 | 含义 |
|-----------|---------|------|
| EVENT_READ | file/netflow → process | 数据从文件/网络流入进程 |
| EVENT_RECVFROM | netflow → process | 数据从网络流入进程 |
| EVENT_RECVMSG | netflow → process | 消息从网络流入进程 |
| EVENT_EXECUTE | file → process | 代码从文件加载进进程 |
| EVENT_OPEN | file → process | 文件句柄流入进程 |
| EVENT_WRITE | process → file/netflow | 数据从进程流向文件/网络（未反转） |
| EVENT_SENDTO | process → netflow | 数据从进程流向网络（未反转） |
| EVENT_FORK | parent → child | 父进程创建子进程（未反转） |
| 其他 | process → object | 原始方向（未反转） |

## 五、常用查询示例

### 5.1 基本统计

```sql
-- 各节点表统计
SELECT 'subject' AS type, COUNT(*) FROM subject_node_table
UNION ALL
SELECT 'file', COUNT(*) FROM file_node_table
UNION ALL
SELECT 'netflow', COUNT(*) FROM netflow_node_table;

-- 边类型分布
SELECT operation, COUNT(*) AS cnt
FROM event_table
GROUP BY operation
ORDER BY cnt DESC;

-- 总边数
SELECT COUNT(*) FROM event_table;
```

### 5.2 查找攻击相关进程

```sql
-- 搜索恶意进程名
SELECT node_uuid, hash_id, exec_name, index_id
FROM subject_node_table
WHERE exec_name IN ('vUgefal', 'pEja72mA', 'XIM', 'test', 'main');

-- 查看恶意进程的所有操作（作为源）
SELECT e.operation, e.timestamp_rec, e.cmdline,
       s_dst.exec_name AS dst_process,
       f.path AS dst_file,
       n.dst_addr || ':' || n.dst_port AS dst_network
FROM event_table e
JOIN subject_node_table s ON e.src_uuid = s.node_uuid
LEFT JOIN subject_node_table s_dst ON e.dst_uuid = s_dst.node_uuid
LEFT JOIN file_node_table f ON e.dst_uuid = f.node_uuid
LEFT JOIN netflow_node_table n ON e.dst_uuid = n.node_uuid
WHERE s.exec_name = 'vUgefal'
ORDER BY e.timestamp_rec;
```

### 5.3 查找攻击链

```sql
-- 查找所有带 cmdLine 的 FORK 边（看谁创建了什么）
SELECT s_parent.exec_name AS parent,
       s_child.exec_name AS child,
       e.cmdline,
       e.timestamp_rec
FROM event_table e
JOIN subject_node_table s_parent ON e.src_uuid = s_parent.node_uuid
JOIN subject_node_table s_child ON e.dst_uuid = s_child.node_uuid
WHERE e.operation = 'EVENT_FORK'
  AND e.cmdline IS NOT NULL
ORDER BY e.timestamp_rec;

-- 查找可疑的 EXECUTE（执行 /tmp 下的文件）
SELECT s.exec_name AS process,
       f.path AS executed_file,
       e.cmdline,
       e.timestamp_rec
FROM event_table e
JOIN file_node_table f ON e.src_uuid = f.node_uuid      -- 反转后 file 是 src
JOIN subject_node_table s ON e.dst_uuid = s.node_uuid    -- 反转后 process 是 dst
WHERE e.operation = 'EVENT_EXECUTE'
  AND f.path LIKE '/tmp/%'
ORDER BY e.timestamp_rec;
```

### 5.4 按时间窗口查询（用于图构建）

```sql
-- 获取指定时间窗口内的所有边（类似 Orthrus 的 gen_edge_fused_tw）
SELECT src_uuid, src_index_id, operation,
       dst_uuid, dst_index_id, event_uuid,
       timestamp_rec, cmdline
FROM event_table
WHERE timestamp_rec >= 1523478000000000000
  AND timestamp_rec <  1523479000000000000
ORDER BY timestamp_rec;
```

## 六、与 Orthrus 数据库的兼容性

### 6.1 完全兼容的部分

- `file_node_table`: 表结构完全一致
- `netflow_node_table`: 表结构完全一致

### 6.2 需要适配的部分

- `subject_node_table`: 列名 `path`/`cmd` 替换为 `exec_name`
- `event_table`: src/dst 使用 `uuid` 而非 `hash_id`。Orthrus 下游代码中 `src_node`/`dst_node` 引用的是 hash，需要改为 uuid。
- 边方向: Orthrus 反转 10 种事件，我们反转 5 种

### 6.3 快速适配 Orthrus 下游代码

如果需要让下游代码无需修改即可使用，可以创建兼容视图：

```sql
-- 兼容 Orthrus 的 subject_node_table 格式
CREATE VIEW subject_node_table_compat AS
SELECT node_uuid, hash_id,
       exec_name AS path,     -- Orthrus 的 path 列
       NULL AS cmd,            -- Orthrus 的 cmd 列（我们不存）
       index_id
FROM subject_node_table;

-- 兼容 Orthrus 的 event_table 格式（src/dst 用 hash）
CREATE VIEW event_table_compat AS
SELECT s_src.hash_id AS src_node,
       e.src_index_id,
       e.operation,
       COALESCE(s_dst.hash_id, f_dst.hash_id, n_dst.hash_id) AS dst_node,
       e.dst_index_id,
       e.event_uuid,
       e.timestamp_rec,
       e._id
FROM event_table e
LEFT JOIN subject_node_table s_src ON e.src_uuid = s_src.node_uuid
LEFT JOIN subject_node_table s_dst ON e.dst_uuid = s_dst.node_uuid
LEFT JOIN file_node_table f_dst ON e.dst_uuid = f_dst.node_uuid
LEFT JOIN netflow_node_table n_dst ON e.dst_uuid = n_dst.node_uuid;
```

## 七、文件清单

```
cch_repeat/dataprocess/cadets_e3/
├── README.md                          ← 数据提取方案总体说明文档
├── readme_postgre.md                  ← PostgreSQL 存储方案说明（本文件）
├── init_database.sql                  ← 数据库和表的创建脚本
├── extract_cadets_e3.py               ← 提取脚本（文件存储版）
└── extract_cadets_e3_postgres.py      ← 提取脚本（PostgreSQL 版）
```

## 八、性能参考

以 CADETS E3 全数据集（44,404,339 行）为参考：

| 阶段 | 预估耗时 | 说明 |
|------|---------|------|
| Pass 1（实体收集） | ~5-10 分钟 | 遍历所有文件，写入 3 张节点表 |
| Pass 2（边提取） | ~5-10 分钟 | 遍历所有文件，写入 event_table |
| 总计 | ~10-20 分钟 | 取决于磁盘 I/O 速度 |

预期数据量（单文件 ta1-cadets-e3-official-2.json 推算全数据集）：

| 表 | 预估行数 |
|----|---------|
| subject_node_table | ~224,000 |
| file_node_table | ~800,000 |
| netflow_node_table | ~155,000 |
| event_table | ~15,000,000 |
