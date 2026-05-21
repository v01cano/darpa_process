# THEIA E5 数据提取方案

## 一、数据集概况

- **平台**: Linux
- **CDM 版本**: 20（E3 是 CDM 18）
- **抽样规模**（前 2367 万行，3 个文件 ~23.6M 行）：
  - Subject: 747 个 SUBJECT_PROCESS（无 THREAD/UNIT）
  - FileObject: 3,787（**只有 FILE_OBJECT_BLOCK**，无 FILE/DIR 区分）
  - NetFlowObject: 120（**dict 地址**）
  - MemoryObject: 11,741（**丢弃**）
  - IpcObject: 51（**丢弃**）
  - Principal: 11 / Host: 1（元数据，丢弃）
  - Event: 23,662,499（其中 **EVENT_WRITE 占 99.6%** = 23.5M）

## 二、关键差异（vs THEIA E3）

| 维度 | THEIA E3 (CDM18) | **THEIA E5 (CDM20)** |
|---|---|---|
| Schema | `...cdm18.*` | `...cdm20.*` |
| **Subject.props.path** | 99.3% | **100%** ← 更完整 |
| **Subject.cmdLine** | str 99.3% | **dict 99.7%**（{"string": "..."}） |
| Subject.parentSubject | 高 | **100%**（完整进程链） |
| **FileObject 类型** | FILE/DIR | **FILE_OBJECT_BLOCK 唯一** |
| FileObject filename | 97.4% | **88.6%** |
| **NetFlow 地址** | str/int | **dict** `{"string":...}` / `{"int":...}` |
| **UUID 大小写** | 小写 | **全大写** |
| Event.exec | 0% | **0%** ← 同（不能用 exec 命名） |
| Event.predicateObjectPath | 0% | **0%** ← 同 |
| **predicateObject2** | 偶尔 | **100% 总是 subject 自身**（冗余 marker） |
| EVENT_EXECUTE cmdLine | properties.cmdLine | **同 100%** |
| **FORK/EXEC 模型** | 分离 | **分离**（416/416 EXECUTE subject 100% 已定义 ✓） |
| **是否需要 EXECUTE.dst 更新** | **需要**（path 99.3%） | **不需要**（path 100% 已完整） |
| 新增 MemoryObject | 无 | **11,741**（MMAP/MPROTECT 用） |

## 三、提取设计

### 节点类型（3 种，与其他 6 数据集统一）

| 节点 | 来源 | name 字段 |
|------|------|-----------|
| `subject` | `SUBJECT_PROCESS` | `properties.map.path`（100% 填充） |
| `file` | `FileObject` (BLOCK/FILE/DIR) | `baseObject.properties.map.filename`（88.6%） |
| `netflow` | `NetFlowObject` | `"<la>:<lp>-><ra>:<rp>"`，从 dict 解包 |

**丢弃：** MemoryObject / IpcObject / Principal / Host / SrcSinkObject（不出现）

### 边过滤 + 反转

```python
INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN',
    'EVENT_EXECUTE', 'EVENT_CLONE',
    'EVENT_CONNECT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    'EVENT_UNLINK',
}
EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG',
    'EVENT_OPEN', 'EVENT_EXECUTE',
}
```

**丢弃：** MMAP / MPROTECT / OTHER / EXIT / CORRELATION / SHM / BOOT / CHANGE_PRINCIPAL / MODIFY_FILE_ATTRIBUTES

### 关键解析

```python
# Subject 自带名字，无需 Event.exec 回填
path = datum['properties']['map']['path']         # 100%
cmd  = datum['cmdLine']['string']                  # dict 解包

# FileObject
filename = datum['baseObject']['properties']['map']['filename']  # 88.6%

# NetFlow dict 解包
la = datum['localAddress'].get('string')
lp = datum['localPort'].get('int')

# UUID 强制 lower
uid = datum['uuid'].lower()

# Event：直接用，predicateObject2 忽略（与 subject 冗余）
src = list(datum['subject'].values())[0].lower()
dst = list(datum['predicateObject'].values())[0].lower()

# cmdLine on EXECUTE
cmdline = properties.map.get('cmdLine')  # EVENT_EXECUTE 100%
# cmdLine on CLONE
cmdline = uuid_cmdline[child_uuid]  # 从 Subject.cmdLine 回填
```

## 四、与其他方法对比

| 维度 | KAIROS E5 THEIA | Orthrus | CAPTAIN | PIDSMaker | **我们** |
|------|---|---|---|---|---|
| 覆盖 THEIA E5 | ✓ | ✓ | 部分 | 有 GT | ✓ |
| 进程命名源 | Subject.path | Subject.path | Subject.path | Subject.path | **Subject.path**（一致） |
| cmdLine dict 解包 | regex | json | — | json | **json** |
| NetFlow dict 解包 | regex | json | — | json | **json** |
| UUID lower 强制 | 不强制 | 不强制 | — | 不一定 | **强制** |
| 提取轮数 | 单遍 | 两遍 | 两遍 | 两遍 | **单遍**（Subject 100% 自带名字） |
| MemoryObject | 丢弃 | 丢弃 | 丢弃 | 丢弃 | **丢弃** |
| MMAP/MPROTECT | 丢弃 | 丢弃 | 部分 | 丢弃 | **丢弃** |
| predicateObject2 | 忽略 | 忽略 | 处理 | 部分 | **忽略**（冗余 marker） |
| EVENT_CLONE 处理 | 反向加边 | 反向加边 | — | — | **保留 + 不反转** + cmdLine 回填 |

### THEIA E5 vs 7 数据集设计统一性

| 数据集 | 进程命名 | 文件命名 | NetFlow | UUID | FORK/EXEC | 提取轮数 |
|---|---|---|---|---|---|---|
| CADETS E3 | Event.exec | predObjPath | 直接 | 小写 | 分离 | 两遍 |
| **THEIA E3** | Subject.path + EXECUTE 更新 | filename | 直接 | 小写 | 分离 | 两遍 |
| ClearScope E3 | Subject.cmdLine | path | 直接 | 小写 | 无 | 单遍 |
| TRACE E3 | Subject.path | filename | 直接 | 小写 | exec 造新 UUID | 单遍分批 |
| FiveDirections E3 | EXECUTE.path → cmdLine | predObjPath | — | 混合大写 | 原子 | 两遍 |
| ClearScope E5 | Subject.cmdLine | path | **dict** | **大写** | 几乎无 | 单遍 |
| CADETS E5 | Event.exec | predObjPath | **dict** | **大写** | 分离 | 两遍 |
| **THEIA E5** | **Subject.path** | **filename** | **dict** | **大写** | **分离** | **单遍** |

## 五、目录结构

```
theia_e5/
├── README.md
├── analyze_cdm_structure.py
├── analyze_uuid_lifecycle.py
├── analyze_all_entity_types.py
├── extract_theia_e5.py
├── extract_theia_e5_postgres.py
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

# 2. 本地版提取
python extract_theia_e5.py \
    --input_dir /mnt/disk/darpa/cch_refine/theia_e5_json \
    --output_dir /mnt/disk/darpa/theia_e5_output \
    --batch_size 3

# 3. PostgreSQL 版提取
psql -U postgres -f init_database.sql
python extract_theia_e5_postgres.py --db_name theia_e5

# 4. GT 验证
python verify_ground_truth_simple.py   # 秒级
python verify_ground_truth.py          # 含完整攻击图
```

## 七、已知坑 / 注意事项

1. **UUID 大写**：原始数据全大写，提取时已 `.lower()`。下游对接外部 GT 务必小写。
2. **Subject.cmdLine 是 dict**：必须 `cmdLine.get('string')`，不能 `str(cmdLine)`。
3. **NetFlow dict 解包**：`localAddress.get('string')` / `localPort.get('int')`。
4. **EVENT_WRITE 占比异常高**（样本中 99.6%）：可能是磁盘 IO 基准测试场景；全量数据可能不同。
5. **predicateObject2 = subject** 是 THEIA E5 的冗余 marker，**忽略**即可（不像 RENAME 等需要它）。
6. **FileObject 全是 BLOCK 类型**：与 THEIA E3 的 FILE/DIR 不同；提取脚本兼容三种类型。
7. **MemoryObject 量大但与图无关**：MMAP/MPROTECT/CORRELATION/SHM 使用，**丢弃**。
8. **本数据集是 fork+exec 分离模型**（416/416 EXECUTE subject 100% 已定义为 PROCESS），不像 TRACE E3 的 exec 造新 UUID。
9. **Subject 信息完整 → 单遍提取**：与 THEIA E3 不同（E3 需要两遍因为 path 99.3% 不够，需 EXECUTE.dst 更新）。
