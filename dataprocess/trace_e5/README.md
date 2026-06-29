# TRACE E5 数据提取方案

## 一、数据集概况

- **平台**: Linux
- **CDM 版本**: 20（E3 是 CDM 18）
- **抽样规模**（前 1000 万行）：
  - Subject: 209,722（**SUBJECT_UNIT 175k = 83.7%** + PROCESS 34k）
  - FileObject: 29,258（DIR 17k / FILE 12k / LINK 62 / CHAR 1）
  - NetFlowObject: 27,815（**dict 地址**）
  - **UnitDependency: 138,205**（unit→unit 依赖，丢弃）
  - MemoryObject: 1,209,251（MMAP/MPROTECT 用，丢弃）
  - SrcSinkObject: 591,203（丢弃）
  - IpcObject: 37,866（丢弃）
  - Event: 7,756,680（其中 EVENT_UNIT 175k 占 2.3%）

## 二、关键差异（vs TRACE E3）— **最重要：EXECUTE 模型改变！**

| 维度 | TRACE E3 (CDM18) | **TRACE E5 (CDM20)** |
|---|---|---|
| Schema | cdm18 | **cdm20** |
| **SUBJECT_UNIT** | 大量 | **175k = 83.7%**，仍需丢弃 |
| **Subject.path** | 高填充（命名来源） | **0%** ← 变化！ |
| **Subject.cmdLine** | str | **dict 99.9%**（`{"string": "..."}`） |
| **Subject.props.name** | 偶尔 | **100%**（Linux 进程短名） |
| **FileObject.filename** | 高填充 | **0%** ← 变化！ |
| **FileObject.path** | 低 | **100%** ← 变化！ |
| **NetFlow 地址** | str | **dict** |
| **UUID 大小写** | 小写 | **全大写** |
| **EVENT_EXECUTE 模型** ⭐ | **exec 造新 UUID** | **fork+exec 分离**（14753/14754 = 100% subject 已定义）|
| **是否需要 EXECUTE 身份切换** | **是**（TRACE E3 特色） | **否！**（与 CADETS/THEIA E5 一致） |
| EVENT_LOADLIBRARY | 少 | **31,187**（大量） |
| EVENT_UNIT | — | **175k**（subject=PROCESS, predObj=UNIT，丢弃） |
| UnitDependency | 独立 datum | **138k**（丢弃） |
| Event.predicateObjectPath | 低 | **57.7%**（提升） |

## 三、提取设计

### 节点类型（3 种，与 9 数据集统一）

| 节点 | 来源 | name 字段 |
|------|------|-----------|
| `subject` | `SUBJECT_PROCESS`（UNIT 丢弃） | 1) `Subject.properties.map.name` (100%，Linux 短名)；2) fallback `cmdLine` 第一 token basename。完整 cmdLine 留在边的 cmdline 字段 |
| `file` | `FileObject` (FILE/DIR/LINK) | `baseObject.properties.map.path` (100%) |
| `netflow` | `NetFlowObject` | `"<la>:<lp>-><ra>:<rp>"` dict 解包 |

**丢弃：** SUBJECT_UNIT / MemoryObject / SrcSinkObject / IpcObject / UnitDependency / Principal / Host

### 边过滤 + 反转（14 种保留，6 种反转）

```python
INCLUDE_EDGE_TYPE = {
    # 文件 I/O
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN',
    'EVENT_CREATE_OBJECT', 'EVENT_UNLINK', 'EVENT_RENAME',
    # 网络
    'EVENT_CONNECT', 'EVENT_ACCEPT',
    'EVENT_SENDMSG', 'EVENT_RECVMSG',
    # 进程 lineage（标准 fork+exec 分离）
    'EVENT_FORK', 'EVENT_CLONE', 'EVENT_EXECUTE',
    # DLL
    'EVENT_LOADLIBRARY',
}
EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVMSG', 'EVENT_OPEN', 'EVENT_ACCEPT',
    'EVENT_EXECUTE', 'EVENT_LOADLIBRARY',
}
```

**丢弃**：
- 噪声：CLOSE / MMAP / MPROTECT / EXIT / SIGNAL / TRUNCATE / MODIFY_FILE_ATTRIBUTES / UPDATE / LINK
- TRACE 特色：**EVENT_UNIT (175k)** / EVENT_CHANGE_PRINCIPAL

### 关键解析

```python
# UUID 强制 lower
uid = datum['uuid'].lower()

# 进程命名: props.map.name 优先（100% 填充，聚类粒度合适）
name_val = datum['properties']['map'].get('name')
cmd = datum.get('cmdLine')
cmd_val = cmd.get('string') if isinstance(cmd, dict) else cmd
final_name = name_val
if not final_name and cmd_val:
    # fallback: 提取第一个 token 的 basename
    first = cmd_val.split()[0] if cmd_val.split() else cmd_val
    final_name = os.path.basename(first.strip('"'))
# cmdLine 完整保存到 uuid_cmdline.pkl，FORK/EXECUTE 边使用

# 文件路径
path = datum['baseObject']['properties']['map']['path']  # 100%

# NetFlow 解包
la = datum['localAddress'].get('string')
lp = datum['localPort'].get('int')

# cmdLine on edges
# EXECUTE: subject 是 exec 后的进程（标准模型）→ uuid_cmdline[src]
# FORK/CLONE: predObj 是子进程 → uuid_cmdline[dst]
```

## 四、与其他方法对比

| 维度 | KAIROS E5 TRACE | Orthrus | CAPTAIN | PIDSMaker | **我们** |
|---|---|---|---|---|---|
| 覆盖 TRACE E5 | ✓ | ✓ | 部分 | 有 GT | ✓ |
| 进程命名 | cmdLine | Subject.path → cmdLine | path | cmdLine | **cmdLine + name fallback** |
| 文件命名 | path | filename → path | path | path | **path** |
| **EXECUTE 模型** | 仍保留特殊处理 | 仍保留 | 仍保留 | 标准分离 | **标准 fork+exec 分离**（数据已变） |
| SUBJECT_UNIT | 丢弃 | 丢弃 | 部分 | 丢弃 | **丢弃** |
| EVENT_UNIT | 多保留 | 丢弃 | 部分 | 丢弃 | **丢弃** |
| UnitDependency | 丢弃 | 丢弃 | 丢弃 | 丢弃 | **丢弃** |
| NetFlow dict | regex | json | — | json | **json** |
| UUID lower | 不强制 | 不强制 | — | 不一定 | **强制** |
| EVENT_LOADLIBRARY | 部分 | 部分 | 忽略 | 部分 | **保留 + 反转** |

### 10 数据集设计统一性表

| 数据集 | 进程命名 | 文件命名 | NetFlow | UUID | FORK/EXEC | UNIT/THREAD |
|---|---|---|---|---|---|---|
| CADETS E3 | Event.exec | predObjPath | str | 小写 | 分离 | — |
| THEIA E3 | Subj.path + EXEC | filename | str | 小写 | 分离 | — |
| ClearScope E3 | Subj.cmdLine | path | str | 小写 | 无 | — |
| TRACE E3 | Subj.path | filename | str | 小写 | **exec 新 UUID** | UNIT 丢弃 |
| FD E3 | EXEC.path | predObjPath | — | 混大写 | 原子 | THREAD 合并 |
| CADETS E5 | Event.exec | predObjPath | dict | 大写 | 分离 | — |
| THEIA E5 | Subj.path | filename | dict | 大写 | 分离 | — |
| ClearScope E5 | Subj.cmdLine | path | dict | 大写 | 几乎无 | — |
| FD E5 | EXEC.path + cmdLine | predObjPath + Reg.key | dict | 大写 | 原子 | THREAD 合并 |
| **TRACE E5** | **cmdLine + name fallback** | **path** | **dict** | **大写** | **分离（变化！）** | **UNIT 丢弃** |

## 五、目录结构

```
trace_e5/
├── README.md
├── analyze_cdm_structure.py
├── analyze_uuid_lifecycle.py
├── analyze_all_entity_types.py
├── extract_trace_e5.py
├── extract_trace_e5_postgres.py
├── init_database.sql
├── verify_ground_truth.py
├── verify_ground_truth_simple.py
└── pidsmaker_groundtruth/        ← 放 GT CSV
```

## 六、用法

```bash
# 1. 分析（已完成）
python analyze_cdm_structure.py
python analyze_uuid_lifecycle.py
python analyze_all_entity_types.py

# 2. 本地版提取（数据量大，建议 batch=10）
python extract_trace_e5.py \
    --input_dir /mnt/disk1/darpa_e5/trace \
    --output_dir /mnt/disk1/darpa_e5/trace_output \
    --batch_size 10

# 3. PostgreSQL 版
psql -U postgres -f init_database.sql
python extract_trace_e5_postgres.py --db_name trace_e5

# 4. GT 验证
python verify_ground_truth_simple.py
python verify_ground_truth.py
```

## 七、已知坑 / 注意事项

1. **EXECUTE 模型已变（最关键）**：TRACE E3 是 exec 造新 UUID，TRACE E5 是标准 fork+exec 分离。
   旧的 TRACE E3 处理逻辑（保留 EXECUTE 边作"身份切换"）**不再适用**。
2. **UUID 全大写**：原始数据全大写，提取时 `.lower()`。
3. **Subject.path 已为 0%**：不能再用 TRACE E3 的进程命名策略，改用 `cmdLine.string` + `props.name` fallback。
4. **FileObject.filename 已为 0%**：改用 `baseObject.props.map.path`（100%）。
5. **NetFlow dict 解包**：`localAddress.get('string')` / `localPort.get('int')`。
6. **SUBJECT_UNIT 占 83.7%**：与 TRACE E3 一致，**全部丢弃**。
7. **EVENT_UNIT (175k)** subject=PROCESS, predObj=UNIT 不属于真正数据流，丢弃。
8. **UnitDependency datum** 是 unit→unit 依赖关系，与图无关，丢弃。
9. **MemoryObject (1.2M) / SrcSinkObject (591k) / IpcObject (38k)** 量大但与图无关，全部丢弃。
10. **数据规模可能极大**：TRACE E3 是 300GB+211 文件，E5 可能更大 → 必须分批输出，调小 `batch_size` 防止 OOM。
11. **EVENT_LOADLIBRARY 量大**（31k），subject 全是 PROCESS，保留 + 反转（so → process）。
