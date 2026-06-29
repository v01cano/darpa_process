# FiveDirections E5 数据提取方案

## 一、数据集概况

- **平台**: Windows
- **CDM 版本**: 20（E3 是 CDM 18）
- **抽样规模**（前 1000 万行）：
  - Subject: 51,581（**THREAD 50,344 占 97.6%** + PROCESS 1,237）
  - FileObject: 19,257（FILE 96% / PEFILE 3.8% / CHAR 0.1%）
  - NetFlowObject: 13,653（**dict 地址**）
  - **RegistryKeyObject: 12,177**（key 100% 填充，归 file 节点）
  - MemoryObject: 327,073（CREATE_OBJECT 用，丢弃）
  - IpcObject: 2,867 / SrcSinkObject: 602（丢弃）
  - TimeMarker: 256 / Principal: 5 / Host: 1（丢弃）
  - Event: 9,572,528

## 二、关键差异（vs FD E3）

| 维度 | FD E3 (CDM18) | **FD E5 (CDM20)** |
|---|---|---|
| Schema | cdm18 | **cdm20** |
| **SUBJECT_THREAD 比例** | ~5% | **~97.6%**（必须合并） |
| **THREAD parent ∈ PROCESS** | 大多 | **97.2%**（48945/50344） |
| Subject.path | 0% | **0%** ← 同 |
| **Subject.cmdLine** | dict + 完整 | **dict 但仅 2.1%**（THREAD 全空） |
| **进程命名源** | EXECUTE.path → cmdLine fallback | **EVENT_EXECUTE.predObjPath（100%）** + cmdLine fallback |
| **文件命名源** | Event.predObjPath | **Event.predObjPath（34.5%）** |
| RegistryKeyObject | 独立 datum，归 file | **独立 datum，归 file** ← 同 |
| **NetFlow 地址** | dict | **dict** ← 同 |
| **UUID 大小写** | 混合大写 | **全大写** |
| **FORK/EXEC 模型** | 原子（1:1） | **原子（695/695 完全 1:1）** |
| EVENT_LOADLIBRARY | 少量 | **28,056**（大量，subject=THREAD） |
| EVENT_CREATE_THREAD | 36k 自环 | **49,521 自环（丢弃）** |
| 新增 MemoryObject | 无 | **327k**（CREATE_OBJECT 用） |
| 新增 TimeMarker | 无 | **256**（时间戳元数据） |

## 三、提取设计

### 节点类型（3 种，与 7 数据集统一）

| 节点 | 来源 | name 字段 |
|------|------|-----------|
| `subject` | `SUBJECT_PROCESS`（**THREAD 已合并到父 PROCESS**） | 1) `EVENT_EXECUTE.predObjPath` 提取 `.exe` basename；2) fallback `Subject.cmdLine.string` |
| `file` | `FileObject` (FILE/DIR/PEFILE/CHAR) + **`RegistryKeyObject`** | `Event.predicateObjectPath`（文件）/ `RegistryKey.key`（注册表） |
| `netflow` | `NetFlowObject` | `"<la>:<lp>-><ra>:<rp>"` dict 解包 |

**THREAD 合并策略：**
- Pass1 收集 `SUBJECT_THREAD.parentSubject` 建 `thread_to_process` map
- Pass2 边提取时所有 src/dst 中 thread UUID 替换为父 PROCESS UUID
- 替换后产生自环的边（如 EVENT_CREATE_THREAD）直接丢弃
- 97.2% 的 thread 可成功合并

**丢弃：** SUBJECT_THREAD（合并）/ MemoryObject / IpcObject / SrcSinkObject / TimeMarker / Principal / Host / FileObject(UNIX_SOCKET/BLOCK)

### 边过滤 + 反转（18 种保留，6 种反转）

```python
INCLUDE_EDGE_TYPE = {
    # 文件 I/O
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN', 'EVENT_CREATE_OBJECT',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_MODIFY_FILE_ATTRIBUTES', 'EVENT_LINK',
    # 进程 lineage (原子模型)
    'EVENT_FORK', 'EVENT_EXECUTE',
    # 网络
    'EVENT_CONNECT', 'EVENT_BIND', 'EVENT_ACCEPT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    # Windows DLL
    'EVENT_LOADLIBRARY',
}
EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG',
    'EVENT_OPEN', 'EVENT_EXECUTE',
    'EVENT_LOADLIBRARY',     # PEFILE → process
}
```

**丢弃：** CLOSE / CHECK_FILE_ATTRIBUTES / OTHER / FCNTL / EXIT / **CREATE_THREAD (合并后自环)** / SIGNAL / LOGIN / LOGOUT / UPDATE

### 关键解析

```python
# UUID 强制 lower
uid = datum['uuid'].lower()

# NetFlow dict
la = datum['localAddress'].get('string', '')
lp = datum['localPort'].get('int', '')

# 进程命名 = EXECUTE.predObjPath 的 .exe basename
# 例如 \\Device\\HarddiskVolume2\\WINDOWS\\system32\\svchost.exe → svchost.exe
import re
EXE_RE = re.compile(r'([^\\/]+\.exe)$', re.IGNORECASE)
exe_name = EXE_RE.search(path).group(1).lower()

# RegistryKey 归 file
if rtype == 'RegistryKeyObject':
    key = datum.get('key')  # 如 \\REGISTRY\\MACHINE\\SOFTWARE\\...
    uuid2name[uid] = ['file', key]

# THREAD 合并
def resolve(uid):
    return thread_to_process.get(uid, uid)
edge_src = resolve(src)
edge_dst = resolve(dst)
```

## 四、与其他方法对比

| 维度 | KAIROS E5 FD | Orthrus | CAPTAIN | PIDSMaker | **我们** |
|---|---|---|---|---|---|
| 覆盖 FD E5 | 部分 | 部分 | ✗ | 有 GT | ✓ |
| **THREAD 处理** | 多丢弃 → 数据丢失 | 丢弃 | 丢弃 | 部分合并 | **合并到父 PROCESS（97.2% 可达）** |
| RegistryKey | 独立节点或丢 | 部分独立 | 丢弃 | 独立 | **归 file 节点**（同 FD E3） |
| 进程命名 | cmdLine | cmdLine | cmdLine | EXECUTE.path | **EXECUTE.path + cmdLine fallback** |
| NetFlow dict | regex | json | — | json | **json** |
| UUID lower | 不强制 | 不强制 | — | 不一定 | **强制** |
| EVENT_LOADLIBRARY | 多忽略 | 忽略 | 忽略 | 部分 | **保留 + 反转**（PEFILE→process） |
| EVENT_FORK + EXEC | 都保留 | 都保留 | 都保留 | 都保留 | **都保留 + path 作 cmdLine** |
| EVENT_CREATE_THREAD | 保留 | 保留 | 保留 | 部分 | **丢弃**（合并后自环） |
| MemoryObject | 丢弃 | 丢弃 | 丢弃 | 丢弃 | **丢弃** |

### 8 数据集设计统一性总结

| 数据集 | 进程命名 | 文件命名 | NetFlow | UUID | FORK/EXEC | THREAD | 提取轮数 |
|---|---|---|---|---|---|---|---|
| CADETS E3 | Event.exec | predObjPath | str | 小写 | 分离 | — | 2 |
| THEIA E3 | Subj.path + EXEC 更新 | filename | str | 小写 | 分离 | — | 2 |
| ClearScope E3 | Subj.cmdLine | path | str | 小写 | 无 | — | 1 |
| TRACE E3 | Subj.path | filename | str | 小写 | exec 新 UUID | — | 1 |
| FiveDirections E3 | EXEC.path → cmdLine | predObjPath | — | 混大写 | 原子 | 合并 | 2 |
| ClearScope E5 | Subj.cmdLine | path | dict | 大写 | 极少 | — | 1 |
| CADETS E5 | Event.exec | predObjPath | dict | 大写 | 分离 | — | 2 |
| THEIA E5 | Subj.path | filename | dict | 大写 | 分离 | — | 1 |
| **FD E5** | **EXEC.path + cmdLine fallback** | **predObjPath + Reg.key** | **dict** | **大写** | **原子** | **合并 97.2%** | **2** |

## 五、目录结构

```
fivedirections_e5/
├── README.md
├── analyze_cdm_structure.py
├── analyze_uuid_lifecycle.py
├── analyze_all_entity_types.py
├── extract_fivedirections_e5.py
├── extract_fivedirections_e5_postgres.py
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
python extract_fivedirections_e5.py \
    --input_dir /mnt/disk/darpa/cch_refine/fivedirections_e5_json \
    --output_dir /mnt/disk/darpa/fivedirections_e5_output \
    --batch_size 3

# 3. PostgreSQL 版
psql -U postgres -f init_database.sql
python extract_fivedirections_e5_postgres.py --db_name fivedirections_e5

# 4. GT 验证
python verify_ground_truth_simple.py
python verify_ground_truth.py
```

## 七、已知坑 / 注意事项

1. **UUID 大写**：原始全大写，提取时已 `.lower()`，外部 GT 也要小写。
2. **THREAD 占绝大多数（97.6%）**：与 FD E3 完全相反；**必须**合并到父 PROCESS。
3. **THREAD 合并丢失率 2.8%**：48945/50344，剩余 1399 个 THREAD 的 parent 不是 SUBJECT_PROCESS（可能是别的 THREAD 或缺失），事件会被 `skipped_no_node` 丢掉。
4. **EVENT_CREATE_THREAD 必须丢弃**：合并后 subject 和 predObj 都变成同一个 PROCESS，是自环。
5. **EVENT_LOADLIBRARY 量大（28k）**：subject 全是 THREAD，必须合并后再连边到 PEFILE。
6. **RegistryKey 归 file 节点**：路径以 `\\REGISTRY\\` 开头，下游可用 `path LIKE '\\REGISTRY\\%'` 区分。
7. **Subject.cmdLine 仅 2.1%**：仅 PROCESS 有，THREAD 全空 → 不要用 cmdLine 作为主要命名源。
8. **进程命名优先级**：EXECUTE.predObjPath > FORK.predObjPath > Subject.cmdLine。
9. **NetFlow dict 解包**：`localAddress.get('string')` / `localPort.get('int')`。
10. **MemoryObject 量极大（327k）**：CREATE_OBJECT 主体，**必须丢弃**，否则图被内存对象主导。
