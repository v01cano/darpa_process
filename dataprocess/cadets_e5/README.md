# CADETS E5 数据提取方案

## 一、数据集概况

- **平台**: FreeBSD
- **CDM 版本**: 20（E3 是 CDM 18）
- **原始数据**: `/mnt/disk/darpa/cch_refine/cadets_e5_json/`
- **抽样规模**（前 1000 万行）：
  - Subject: 13,655 个 SUBJECT_PROCESS（无 THREAD/UNIT）
  - FileObject: 111,802（FILE 104k / UNIX_SOCKET 6.9k / DIR 141）
  - NetFlowObject: 5,608（**dict 地址**）
  - SrcSinkObject: 17,369（**丢弃**）
  - IpcObject: 9,864（**丢弃**）
  - Principal: 34 / Host: 3（元数据，丢弃）
  - Event: 9,841,665（其中 97.5% 携带 properties.exec）

## 二、关键差异（vs CADETS E3）

| 维度 | CADETS E3 (CDM18) | **CADETS E5 (CDM20)** |
|------|---|---|
| Schema 命名空间 | `...cdm18.*` | `...cdm20.*` |
| Subject.cmdLine | 0% | **0%** ← 同 |
| Subject.props.path | 0% | **0%** ← 同 |
| **parentSubject** | — | **60.4%**（可用进程链） |
| FileObject path | 0% | **0%** ← 同 |
| FileObject filename | 0% | **0%** ← 同 |
| **Event.exec** | 99.9% | **97.5%** ← 仍是命名主源 |
| **Event.cmdLine** | 0.5%（仅 EXECUTE） | **0.1%**（仍仅 EXECUTE，9218/9218 = 100%） |
| **Event.predicateObjectPath** | 44.2% | **17.6%** |
| **NetFlow 地址** | 直接 string | **dict** `{"string":...}` / `{"int":...}` |
| **UUID 大小写** | 小写 | **全大写** |
| **FORK/EXEC 模型** | fork+exec 分离 | **fork+exec 分离**（EXECUTE 不造新 UUID，8898/8898 已定义 ✓） |
| **新事件 EVENT_FLOWS_TO** | 无 | **246k**（ProvenanceTagNode 退化，丢弃） |
| **新事件 EVENT_MODIFY_PROCESS** | 无 | **1.5M**（process→process+File，含义模糊，丢弃） |

## 三、提取设计（与 CADETS E3 几乎完全一致）

### 节点类型（3 种）

| 节点 | 来源 | name 字段 |
|------|------|-----------|
| `subject` | `SUBJECT_PROCESS` | `Event.properties.map.exec`（持续覆盖到最后看到的值） |
| `file` | `FileObject` (FILE/DIR) | `Event.predicateObjectPath` / `predicateObject2Path` |
| `netflow` | `NetFlowObject` | `"<la>:<lp>-><ra>:<rp>"`，从 dict 解包 |

**丢弃的实体类型：**
- `FILE_OBJECT_UNIX_SOCKET` — 与 CADETS E3 一致，匿名节点
- `SrcSinkObject` / `IpcObject` — CDM20 新增，与 IPC 中间态，无独立价值
- `Principal` / `Host` — 元数据

### 边过滤 + 反转（与 CADETS E3 完全一致 13 种）

```python
INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_EXECUTE', 'EVENT_FORK',
    'EVENT_OPEN', 'EVENT_CONNECT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    'EVENT_RENAME', 'EVENT_UNLINK', 'EVENT_CREATE_OBJECT',
}
EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG',
    'EVENT_EXECUTE',   # file → process
    'EVENT_OPEN',      # file → process
}
```

**丢弃的高频事件：**
- `EVENT_CLOSE` (1.7M), `EVENT_LSEEK` (205k), `EVENT_FCNTL` (68k) — 句柄操作噪声
- `EVENT_MMAP` (204k), `EVENT_MPROTECT` (98k) — 内存映射
- `EVENT_MODIFY_PROCESS` (1.5M) — 含义模糊
- `EVENT_FLOWS_TO` (246k) — CDM20 内部 taint 标记
- `EVENT_CHANGE_PRINCIPAL` / `EVENT_LOGIN` / `EVENT_EXIT` / `EVENT_SIGNAL` / `EVENT_OTHER` / `EVENT_ADD_OBJECT_ATTRIBUTE`
- `EVENT_ACCEPT` / `EVENT_BIND` / `EVENT_LINK` / `EVENT_MODIFY_FILE_ATTRIBUTES` / `EVENT_TRUNCATE` — 量小语义边缘

### 关键解析细节

```python
# CDM20 namespace（与 ClearScope E5 一致）
rtype = rtype_full.rsplit('.', 1)[-1]      # 'Subject' / 'Event' ...
src = list(datum['subject'].values())[0]   # 取 UUID（dict 唯一 value）

# NetFlow 解包
la = datum['localAddress'].get('string', '')
lp = datum['localPort'].get('int', '')

# UUID 强制 lower
uid = datum['uuid'].lower()

# 进程名 = Event.exec
if src and 'exec' in pmap:
    uuid2name[src][1] = pmap['exec']

# 文件路径 = predicateObjectPath
pop = datum.get('predicateObjectPath')
if isinstance(pop, dict):
    path = pop['string']

# EXECUTE 边 cmdLine = properties.map.cmdLine
# FORK 边 cmdLine = uuid_cmdline[child]（子进程后续 EXECUTE 回填）
```

## 四、与其他方法对比

| 维度 | KAIROS E5 CADETS | Orthrus | CAPTAIN | PIDSMaker | **我们** |
|------|---|---|---|---|---|
| 覆盖 CADETS E5 | ✓ | ✓ | 部分 | 有 GT | ✓ |
| 进程命名源 | Event.exec | Event.exec | Event.exec | Event.exec | **Event.exec**（一致） |
| 文件路径源 | predObjPath | predObjPath | predObjPath | 部分 | **predObjPath** |
| 节点类型数 | 3 | 3 | 3 | 3 | **3** |
| 边类型数 | ~10 | 11 | 10+ | 13 | **13** |
| Reverse READ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reverse OPEN | 视情况 | ✗ | ✓ | ✓ | **✓**（与 CADETS E3 一致） |
| Reverse EXECUTE | ✓ | ✓ | ✓ | ✓ | **✓** |
| NetFlow dict 解包 | regex | json | json | json | **json** |
| UUID lower 强制 | 不强制 | 不强制 | 不强制 | 不一定 | **强制** |
| ProvenanceTagNode/FLOWS_TO | 丢弃 | 丢弃 | 丢弃 | 丢弃 | **丢弃** |
| EVENT_MODIFY_PROCESS | 多数丢 | 丢 | 丢 | 部分保留 | **丢** |
| EVENT_FORK 处理 | 反向加边 | 反向加边 | — | — | **保留 + 不反转**（subject=parent→child 已是数据流） |
| EVENT_FORK 上的 cmdLine | 部分保留 | 部分保留 | — | — | **回填子进程 EXECUTE 的 cmdLine** |

### CADETS E5 vs 我们 6 个数据集设计统一性

| 数据集 | 进程命名 | 文件命名 | NetFlow | UUID 处理 | FORK/EXEC |
|---|---|---|---|---|---|
| CADETS E3 | Event.exec | predObjPath | 直接 string | 小写 | 分离 |
| THEIA E3 | Subject.path + EXECUTE 更新 | filename | 直接 string | 小写 | 分离 |
| ClearScope E3 | Subject.cmdLine | path | 直接 string | 小写 | 无 |
| TRACE E3 | Subject.path | filename | 直接 string | 小写 | exec 造新 UUID |
| FiveDirections E3 | EXECUTE.path → cmdLine | predObjPath | — | **混合大写** | 原子 |
| ClearScope E5 | Subject.cmdLine | path | **dict** | **大写→lower** | 几乎无 |
| **CADETS E5** | **Event.exec** | **predObjPath** | **dict** | **大写→lower** | **分离**（同 E3） |

## 五、目录结构

```
cadets_e5/
├── README.md
├── readme_postgre.md
├── analyze_cdm_structure.py
├── analyze_uuid_lifecycle.py
├── analyze_all_entity_types.py
├── extract_cadets_e5.py
├── extract_cadets_e5_postgres.py
├── init_database.sql
├── verify_ground_truth.py
├── verify_ground_truth_simple.py
└── pidsmaker_groundtruth/        ← 放 GT CSV
```

## 六、用法

```bash
# 1. 分析（已完成，可重跑验证）
python analyze_cdm_structure.py
python analyze_uuid_lifecycle.py
python analyze_all_entity_types.py

# 2. 本地版提取
python extract_cadets_e5.py \
    --input_dir /mnt/disk/darpa/cch_refine/cadets_e5_json \
    --output_dir /mnt/disk/darpa/cadets_e5_output \
    --batch_size 1

# 3. PostgreSQL 版提取
psql -U postgres -f init_database.sql
python extract_cadets_e5_postgres.py --db_name cadets_e5

# 4. GT 验证
python verify_ground_truth_simple.py   # 秒级
python verify_ground_truth.py          # 含完整攻击图
```

## 七、下游使用

```python
from extract_cadets_e5 import iter_edges, load_all_edges

for edge in iter_edges('/mnt/disk/darpa/cadets_e5_output'):
    ts, etype, src_uuid, src_type, src_name, dst_uuid, dst_type, dst_name, cmdline = edge
```

## 八、已知坑 / 注意事项

1. **UUID 大写**：原始 UUID 全大写，提取时已 `.lower()`。下游对接外部 GT 时务必也用小写匹配。
2. **NetFlow dict 解包**：`localAddress` 是 `{"string": "..."}`，**不能** `str(datum['localAddress'])`，要用 `.get('string', '')`。
3. **Event.exec 是命名唯一来源**：Subject/FileObject 字段全空，必须靠 Event.exec 给进程命名，否则 name=None。
4. **EVENT_FLOWS_TO 是 taint 标记**：CDM20 把 ProvenanceTagNode 合并到 Event 里，subject=process predObj=file predObj2=file 表示数据流，**不属于真正系统调用**，丢弃。
5. **EVENT_MODIFY_PROCESS 含义模糊**：process→process+File 的复合事件，量大但语义不清，丢弃。
6. **FORK 不造新 UUID**：与 CADETS E3 一致，fork+exec 分离，EXECUTE 后 UUID 不变。
7. **新增 IpcObject / SrcSinkObject 不要被混淆**：与图无关，全部丢弃。
