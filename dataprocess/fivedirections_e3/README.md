# FiveDirections E3 数据提取方案设计文档

## 一、数据集概况

- **平台：** Windows
- **CDM 版本：** CDM 18
- **采集方：** FiveDirections
- **文件数：** 55 个（ta1-fivedirections-e3-official.json, -2.json, -2.json.1~.52, -3.json）
- **项目支持情况：**
  - ✅ CAPTAIN
  - ❌ Orthrus
  - ❌ PIDSMaker（dataset_preprocessing 无 fivedirections）
  - ❌ KAIROS（DARPA 目录无 FIVEDIRECTIONS_E3）

## 二、Windows CreateProcess 模型（核心差异）

FiveDirections 是**第五种独特的 UUID 生成模型**，与前四个数据集完全不同：

```
Unix/FreeBSD (CADETS/TRACE): fork() + exec() 两步
Linux (THEIA):              clone() + exec() 两步
Android (ClearScope):        Zygote 启动，无 fork+exec
Windows (FiveDirections):    CreateProcess() 原子操作
```

### Windows CreateProcess 的流程

```
某线程（如 svchost 内的线程）调用 CreateProcess("wmiprvse.exe", ...)
     ↓（同一纳秒，原子操作）
1. 创建新进程 UUID (cmdLine 已完整)
2. 触发 EVENT_FORK: src=调用线程, dst=新进程
3. 触发 EVENT_EXECUTE: src=新进程, dst=PE文件
4. 触发 EVENT_CREATE_THREAD: 创建新进程的第一个线程
```

**重要数据验证（5M 行样本 + 21M 行样本）：**
```
FORK事件 100% 紧跟 EXECUTE（同一纳秒）
EXECUTE path 94.0% 在 src.cmdLine 中
FORK 的 src 是 SUBJECT_THREAD（不是PROCESS）
EXECUTE 的 dst 是 FILE_OBJECT_PEFILE
```

## 三、实体统计（单文件样本）

| 实体类型 | 数量 | 占比 | 决策 |
|---------|------|------|------|
| MemoryObject | 461,689 | 61.7% | 丢弃（仅参与MPROTECT/MMAP类） |
| FileObject (FILE) | 135,838 | 18.2% | 保留 |
| RegistryKeyObject | 104,695 | 14.0% | 保留（归入 file 类） |
| SUBJECT_THREAD | 37,381 | 5.0% | **UUID 合并到父 PROCESS** |
| NetFlowObject | 3,445 | 0.5% | 保留 |
| FileObject (PEFILE) | 2,303 | 0.3% | 保留 |
| SUBJECT_PROCESS | 1,412 | 0.2% | 保留 |
| FileObject (CHAR/BLOCK/等) | 1,339 | 0.2% | 保留 |
| SrcSinkObject | 39 | <0.01% | 丢弃 |

## 四、SUBJECT_THREAD 处理（方案B：UUID 合并）

### 核心问题

Windows 中 3.8M+ 条事件的 subject 是 THREAD（不是 PROCESS）。如果丢弃 THREAD，会失去几乎所有行为信息。

### 方案：UUID 替换

```python
# Pass 1：建立映射
thread_to_process[thread_uuid] = parent_process_uuid

# Pass 2 提边时：
src = thread_to_process.get(event.subject, event.subject)
dst = thread_to_process.get(event.predicateObject, event.predicateObject)
# 此时 src/dst 都是 PROCESS UUID
```

### 效果

```
原始: THREAD_UUID_Y ──READ──> FILE_UUID_F
替换: PROCESS_UUID_B ──READ──> FILE_UUID_F

攻击查询: 查 PROCESS_UUID_B 的边 → 看到所有线程的行为 ✓
```

### 副作用

- **EVENT_CREATE_THREAD 被丢弃：** THREAD→THREAD 替换后变成 PROCESS→PROCESS 自环
- **节点数大幅减少：** 1,412 PROCESS 节点（原 1,412 + 37,381 ≈ 39K）
- **边数不变：** 3.8M+ 条 THREAD 事件全部归属到 PROCESS

## 五、节点命名策略

### PROCESS 命名

**主来源：** EVENT_EXECUTE.predicateObjectPath（简洁程序名）

```
EVENT_EXECUTE path 样例：
  "svchost.exe"
  "wmiprvse.exe"
  "wininit.exe"
  "services.exe"
```

**覆盖率：** 94.1%（1,329 / 1,412 PROCESS 有 EXECUTE 事件）

**Fallback：** 从 Subject.cmdLine 提取可执行文件名

```python
"C:\WINDOWS\system32\svchost.exe -k DcomLaunch" → "svchost.exe"
"\SystemRoot\System32\smss.exe 000000d0"        → "smss.exe"
'"fontdrvhost.exe"'                              → "fontdrvhost.exe"
```

### 为什么不用完整 cmdLine？

多个 svchost 实例 cmdLine 带不同参数，会被识别为不同进程：
```
svchost.exe -k DcomLaunch
svchost.exe -k NetworkService
svchost.exe -k LocalSystem
```

用 `svchost.exe` 作为 name 可以按进程类型聚类，完整 cmdLine 放在边上保留参数信息。

### FileObject 命名

- `Event.predicateObjectPath` 更新（类似 CADETS 的策略）

### RegistryKeyObject 命名

- `datum['key']` 直接作为 name，归入 `file` 类型

```
RegistryKeyObject key 样例：
  "\REGISTRY\MACHINE\SYSTEM\ControlSet001\Services\Tcpip\Parameters"
  "\REGISTRY\MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
```

### NetFlowObject 命名

- `"{localAddr}:{localPort}->{remoteAddr}:{remotePort}"` 四元组

## 六、边类型方案

### 保留的 17 种边

| 事件 | 作用 | 是否反转 |
|------|------|---------|
| EVENT_READ | 文件/网络读取 | **反转** (→ process) |
| EVENT_WRITE | 文件/网络写入 | 不反转 |
| EVENT_OPEN | 文件打开 | **反转** |
| EVENT_UNLINK | 文件删除 | 不反转 |
| EVENT_RENAME | 文件重命名（用 predicateObject2） | 不反转 |
| EVENT_CREATE_OBJECT | 文件创建 | 不反转 |
| EVENT_MODIFY_FILE_ATTRIBUTES | 修改文件属性 | 不反转 |
| EVENT_FORK | 父进程→新进程（UUID替换后） | 不反转 |
| EVENT_EXECUTE | PE 文件加载 | **反转** (PE→process) |
| EVENT_LOADLIBRARY | DLL 加载 | **反转** (DLL→process) |
| EVENT_CONNECT | 网络连接 | 不反转 |
| EVENT_SENDTO | 网络发送 | 不反转 |
| EVENT_RECVFROM | 网络接收 | **反转** |
| EVENT_SENDMSG | 消息发送 | 不反转 |
| EVENT_RECVMSG | 消息接收 | **反转** |
| EVENT_ACCEPT | 接受连接 | **反转** |
| EVENT_BIND | 端口绑定 | 不反转 |

### 丢弃的边

| 事件 | 数量 | 丢弃理由 |
|------|------|---------|
| EVENT_CHECK_FILE_ATTRIBUTES | 3.9M | 纯噪声 |
| EVENT_CLOSE | 1.0M | 信息量低 |
| EVENT_FCNTL | 748K | Windows 下无意义 |
| EVENT_OTHER | 1.4M | 大部分指向 Memory |
| EVENT_CREATE_THREAD | 36K | UUID 替换后变自环 |
| EVENT_EXIT/SIGNAL/LOGIN/LOGOUT | 少量 | 非核心 |

## 七、cmdLine 在边上

**Windows 下 Event.cmdLine 永远为 0%**，所有 cmdLine 来自 Subject 记录。

| 边类型 | cmdLine 来源 | 语义 |
|--------|------------|------|
| **EVENT_FORK** | **dst 进程的 Subject.cmdLine** | 新进程的完整命令行 |
| **EVENT_EXECUTE** | **src 进程的 Subject.cmdLine** | 加载 PE 文件的进程的完整命令行 |
| 其他边 | None | 无 cmdLine 语义 |

### 示例

```
图中:
  svchost(UUID_A) ──FORK(cmdLine="C:\WINDOWS\system32\svchost.exe -k DcomLaunch")──> svchost(UUID_B)
                   ^^^^^                                                              ^^^^^
                   同名                                                               同名但cmdLine不同

  WmiPrvSE.exe(file) ──EXECUTE(cmdLine="...wmiprvse.exe -Embedding")──> wmiprvse(process)
```

同名的多个 svchost 在图中有相同的 name，但 FORK 边上的 cmdLine 保留了完整参数，可以用于精确分析。

## 八、完整数据流示例

### 原始 CDM 数据

```
Subject:
  UUID_A (PROCESS, cmdLine="C:\WINDOWS\system32\svchost.exe -k DcomLaunch")
  UUID_X (THREAD,  parent=UUID_A)
  UUID_B (PROCESS, cmdLine="C:\WINDOWS\system32\wbem\wmiprvse.exe -Embedding")
  UUID_Y (THREAD,  parent=UUID_B)
  UUID_P (FileObject PEFILE)  # WmiPrvSE.exe
  UUID_R (RegistryKeyObject, key="\REGISTRY\...\Run")

Event:
  FORK(T1):    subject=UUID_X, predObj=UUID_B
  EXECUTE(T1): subject=UUID_B, predObj=UUID_P, path="WmiPrvSE.exe"
  READ(T2):    subject=UUID_Y, predObj=UUID_F
  WRITE(T3):   subject=UUID_Y, predObj=UUID_R  (写注册表持久化)
```

### Pass 1 结果

```python
uuid2name = {
    UUID_A: ['process', 'svchost.exe'],     # 来自EXECUTE path
    UUID_B: ['process', 'wmiprvse.exe'],    # 来自EXECUTE path
    UUID_P: ['file', 'WmiPrvSE.exe'],       # 来自Event predicateObjectPath
    UUID_F: ['file', '...'],
    UUID_R: ['file', '\\REGISTRY\\...\\Run'],
}
thread_to_process = {UUID_X: UUID_A, UUID_Y: UUID_B}
subject_cmdline = {
    UUID_A: '...svchost.exe -k DcomLaunch',
    UUID_B: '...wmiprvse.exe -Embedding',
}
```

### Pass 2 输出（UUID替换+反转+cmdLine）

```
FORK事件:
  原: src=UUID_X, dst=UUID_B
  替换: src=UUID_A, dst=UUID_B
  不反转
  cmdLine = subject_cmdline[UUID_B] = "...wmiprvse.exe -Embedding"
  → (T1, 'EVENT_FORK', UUID_A, 'process', 'svchost.exe',
                      UUID_B, 'process', 'wmiprvse.exe',
                      '...wmiprvse.exe -Embedding')

EXECUTE事件:
  原: src=UUID_B, dst=UUID_P
  替换后: 不变
  反转: src=UUID_P, dst=UUID_B
  cmdLine = subject_cmdline[UUID_B]（替换前的src）
  → (T1, 'EVENT_EXECUTE', UUID_P, 'file', 'WmiPrvSE.exe',
                          UUID_B, 'process', 'wmiprvse.exe',
                          '...wmiprvse.exe -Embedding')

READ事件:
  原: src=UUID_Y, dst=UUID_F
  替换: src=UUID_B, dst=UUID_F
  反转: src=UUID_F, dst=UUID_B
  → (T2, 'EVENT_READ', UUID_F, 'file', '...',
                       UUID_B, 'process', 'wmiprvse.exe', None)

WRITE事件（写注册表）:
  原: src=UUID_Y, dst=UUID_R
  替换: src=UUID_B, dst=UUID_R
  不反转
  → (T3, 'EVENT_WRITE', UUID_B, 'process', 'wmiprvse.exe',
                        UUID_R, 'file', '\\REGISTRY\\...\\Run', None)
```

## 九、与其他数据集对比

| 维度 | CADETS | THEIA | ClearScope | TRACE | **FiveDirections** |
|------|--------|-------|-----------|-------|-------------------|
| 平台 | FreeBSD | Linux | Android | Linux | **Windows** |
| UUID 生成 | fork时(空) | fork时(延迟写入) | Android启动 | fork+exec各创建 | **CreateProcess原子** |
| THREAD/UNIT处理 | - | - | - | UNIT丢弃 | **UUID合并到PROCESS** |
| 进程名来源 | 最后Event.exec | 最后EXECUTE dst | Subject.cmdLine | Subject.name | **EXECUTE path(简洁程序名)** |
| FORK cmdLine | Event.cmdLine | 同左 | - | dst Subject.cmdLine | **dst Subject.cmdLine** |
| EXECUTE cmdLine | Event.cmdLine | 同左 | - | dst Subject.cmdLine | **src Subject.cmdLine** |
| EXECUTE dst | FileObject | FileObject | - | 新PROCESS UUID | **FILE_OBJECT_PEFILE** |
| EXECUTE反转 | ✓ | ✓ | - | ✗ | **✓** |
| 特殊实体 | - | MemoryObj丢 | SrcSink丢 | UNIT丢 | **THREAD合并,Registry归入file** |
| 保留边数 | 13 | 11 | 11 | 16 | **17** |

## 十、扫描遍数与性能

### 两遍扫描

**Pass 1：** 实体收集 + THREAD 映射 + PROCESS 命名 + FileObject path 更新
**Pass 2：** 边提取 + UUID 替换 + cmdLine 附加 + 方向反转

### 分片输出

与 TRACE 一样，每 10 个文件保存一个 `edges_part_XXX.pkl`，避免单个 pkl 文件过大或内存溢出。

## 十一、文件清单

```
cch_repeat/dataprocess/fivedirections_e3/
├── README.md                              ← 本文件
├── readme_postgre.md                      ← PostgreSQL 方案说明
├── init_database.sql                      ← 数据库建表脚本
├── extract_fivedirections_e3.py           ← 本地磁盘版（分片输出）
├── extract_fivedirections_e3_postgres.py  ← PostgreSQL 版
├── analyze_cdm_structure.py               ← 字段分析
├── analyze_all_entity_types.py            ← 实体与事件关系分析
├── analyze_uuid_lifecycle.py              ← UUID生命周期分析
└── pidsmaker_groundtruth/                 ← ground truth（如有）
```

## 十二、用法

```bash
# 本地版
python extract_fivedirections_e3.py \
    --input_dir /mnt/disk/darpa/fivedirections_e3 \
    --output_dir /mnt/disk/darpa/fivedirections_e3_output \
    --batch_size 10

# PostgreSQL 版
psql -U postgres -f init_database.sql
python extract_fivedirections_e3_postgres.py \
    --input_dir /mnt/disk/darpa/fivedirections_e3 \
    --db_name fivedirections_e3
```

## 十三、已知坑 / 注意事项

### 1. UUID 大小写不统一（重要）

FiveDirections E3 原始 CDM 数据中 UUID **大小写混合**（大部分为大写，例如 `E20F7C55-319D-4AF5-BC62-11317B9DEC3C`，也有小写），提取脚本 `extract_fivedirections_e3.py` 按原样存入 `uuid2name.pkl`、`cmdlines.pkl` (thread_to_process)、`edges_part_*.pkl`，**没有做大小写归一化**。

**影响：**
- 下游代码在用外部 UUID（例如 pidsmaker ground truth CSV）去查 `uuid2name` / `thread_to_process` / 边里的 `src_uuid` / `dst_uuid` 时，如果两边大小写不一致，Python `dict` 查找是大小写敏感的，会导致 0% 命中
- 曾经在 `verify_ground_truth.py` 上踩过这个坑：GT CSV UUID 被 `.lower()`，但 pkl 里存的是原始大小写，结果 120 个 GT UUID 全部匹配失败

**规避方式（下游侧）：**
- 加载 pkl 后立刻把 `uuid2name` / `thread_to_process` 的 key（和 value，如 thread_map 的父进程 UUID）统一 `.lower()`
- 比较 edge 里的 uuid 字段时也先 `.lower()` 再查
- 外部输入的 UUID（GT CSV、查询参数等）也一律 `.lower()`
- 参见 `verify_ground_truth.py` 中 `load_uuid2name()` / `load_thread_map()` / `collect_attack_edges()` 的写法

其他四个数据集（CADETS/THEIA/ClearScope/TRACE）的原始 CDM UUID 大体是小写一致的，这是 FiveDirections 独有的问题。

## 十四、下游使用

```python
from extract_fivedirections_e3 import load_all_edges, iter_edges

# 全量加载（需要足够内存）
edges = load_all_edges('/mnt/disk/darpa/fivedirections_e3_output')

# 流式迭代（内存友好）
for edge in iter_edges('/mnt/disk/darpa/fivedirections_e3_output'):
    ts, etype, src_uuid, src_type, src_name, dst_uuid, dst_type, dst_name, cmdline = edge
    # 处理
```
