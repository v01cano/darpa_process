# TRACE E3 数据提取方案设计文档

## 一、数据集概况

TRACE E3 是 DARPA TC Engagement 3 中由 TRACE 团队在 Linux 系统上采集的数据集。CDM 18 JSON 格式，共 211 个文件（official 系列 204 个 + official-1 系列 7 个）。

**TRACE 具有独特的进程模型，是四个数据集中最复杂的。**

## 二、TRACE 独特的三层进程模型

```
PROCESS ──EVENT_UNIT──> UNIT ──EVENT_CLONE──> PROCESS ──EVENT_FORK──> PROCESS ──EVENT_EXECUTE──> PROCESS(新UUID)
```

| 层 | 实体类型 | 创建方式 | 后续行为 |
|----|---------|---------|---------|
| PROCESS | SUBJECT_PROCESS | CLONE/FORK | 做FORK、做EVENT_UNIT |
| UNIT | SUBJECT_UNIT | EVENT_UNIT | 做CLONE、做READ/WRITE等 |
| 新PROCESS | SUBJECT_PROCESS | EXECUTE | 新UUID、新name、新cmdLine |

### 关键统计（ta1-trace-e3-official-1.json.3 样本）

```
SUBJECT_PROCESS: 3,207
SUBJECT_UNIT:    411,355  ← TRACE独有，丢弃
EVENT_CLONE:     2,250    ← 创建并行工作进程，子进程不exec
EVENT_FORK:      483      ← 传统fork+exec
EVENT_EXECUTE:   393      ← 旧进程→新进程（新UUID）
EVENT_UNIT:      411,355  ← PROCESS→UNIT，丢弃
```

### FORK 与 EXECUTE 的配对关系

```
FORK子进程做了EXECUTE: 73.9% (357/483)  ← fork+exec 配对
EXECUTE的src来自FORK:  90.8% (357/393)
```

### CLONE 的角色

```
CLONE子进程做了EXECUTE: 0% (0/2,250)     ← CLONE子进程从不exec！
CLONE子进程做了FORK:    38/2,250 (1.7%)  ← 少量CLONE→FORK→EXECUTE链
```

### EXECUTE 的独特语义

```
CADETS/THEIA: 进程(UUID_A) ──EXECUTE──> 文件(/bin/ls)     ← 进程加载文件
TRACE:        进程(UUID_A) ──EXECUTE──> 进程(UUID_B)       ← 旧身份→新身份（新UUID）
```

**TRACE 中每次 exec 创建新 UUID。** Subject.name 就是该UUID的唯一且正确的身份，无需更新。

## 三、与其他数据集的完整对比

| 维度 | CADETS | THEIA | ClearScope | **TRACE** |
|------|--------|-------|-----------|---------|
| 平台 | FreeBSD | Linux | Android | **Linux** |
| Subject.name | 0% | 0% | 0% | **100%** |
| Subject.path | 0% | 99.3% | 0% | **0%** |
| Subject.cmdLine | 0% | 99.3% | 100% | **27~100%** |
| FileObject.path | 0% | 0% | 100% | **100%** |
| FileObject.filename | 0% | 97.4% | 0% | **0%** |
| Event.exec | 99.9% | 0% | 0% | **0%** |
| Event.cmdLine | 0.5% | 0.1% | 0% | **0%** |
| EXECUTE dst | FileObject | FileObject | 无 | **Subject（新UUID）** |
| EXECUTE反转 | 是 | 是 | — | **否** |
| exec后UUID | 不变 | 不变 | — | **创建新UUID** |
| FORK | 有 | 无 | 无 | **有** |
| CLONE | 无 | 有 | 无 | **有** |
| SUBJECT_UNIT | 无 | 无 | 无 | **有（丢弃）** |
| 进程名更新 | 需要 | 需要 | 不需要 | **不需要** |
| 扫描遍数 | 2 | 2 | 1 | **2**（收集EXECUTE dst cmdLine） |

## 四、实体提取

| 实体 | 来源 | 归类 | 命名来源 |
|------|------|------|---------|
| SUBJECT_PROCESS | Subject记录 | process | Subject.properties.map.name (100%) |
| FILE_OBJECT_FILE/DIR/LINK/CHAR | FileObject记录 | file | baseObject.props.map.path (100%) |
| NetFlowObject | NetFlowObject记录 | netflow | 记录本身(四元组) |

**丢弃：**
- SUBJECT_UNIT (411,355) — 进程内执行单元
- SrcSinkObject (644,921, 全为SRCSINK_UNKNOWN)
- MemoryObject (118,471)
- UnnamedPipeObject (533)

## 五、边类型

### 保留的 16 种

| 事件 | 说明 | 反转? |
|------|------|------|
| EVENT_READ | 读操作 | **反转** |
| EVENT_WRITE | 写操作 | 不反转 |
| EVENT_EXECUTE | **旧进程→新进程** | **不反转** |
| EVENT_FORK | 传统fork | 不反转 |
| EVENT_CLONE | 创建并行进程 | 不反转 |
| EVENT_OPEN | 打开文件 | **反转** |
| EVENT_CONNECT | 网络连接 | 不反转 |
| EVENT_SENDTO | 发送 | 不反转 |
| EVENT_RECVFROM | 接收 | **反转** |
| EVENT_SENDMSG | 消息发送 | 不反转 |
| EVENT_RECVMSG | 消息接收 | **反转** |
| EVENT_UNLINK | 删除 | 不反转 |
| EVENT_RENAME | 重命名(predicateObject2) | 不反转 |
| EVENT_CREATE_OBJECT | 创建 | 不反转 |
| EVENT_LOADLIBRARY | 加载库 | **反转** |
| EVENT_ACCEPT | 接受连接 | **反转** |

### 过滤的事件

| 事件 | 数量 | 理由 |
|------|------|------|
| EVENT_MPROTECT | 925,700 | 内存保护，指向MemoryObject |
| EVENT_UNIT | 411,355 | PROCESS→UNIT，UNIT已丢弃 |
| EVENT_MMAP | 63,702 | 内存映射 |
| EVENT_CLOSE | 82,213 | 信息量低 |
| EVENT_EXIT | 2,058 | 进程退出 |
| EVENT_TRUNCATE | 4,179 | 文件截断 |

## 六、cmdLine 处理

| 边类型 | cmdLine来源 | 说明 |
|--------|------------|------|
| **FORK** | EXECUTE dst的Subject.cmdLine; fallback子进程Subject.cmdLine | "这个子进程将变成什么" |
| **CLONE** | 子进程Subject.cmdLine | "这个并行进程是什么"（CLONE子进程不exec） |
| **EXECUTE** | dst Subject.cmdLine | "新进程的命令行" |

**Event.cmdLine 在 TRACE 中永远为 None（0%），所有 cmdLine 来自 Subject 记录。**

## 七、用法

```bash
# 本地磁盘版
python extract_trace_e3.py --input_dir /mnt/disk/darpa/trace_e3 --output_dir /mnt/disk/darpa/trace_e3_output

# PostgreSQL版
psql -U postgres -f init_database.sql
python extract_trace_e3_postgres.py --input_dir /mnt/disk/darpa/trace_e3
```

## 八、文件清单

```
cch_repeat/dataprocess/trace_e3/
├── README.md
├── readme_postgre.md
├── init_database.sql
├── extract_trace_e3.py
├── extract_trace_e3_postgres.py
├── analyze_cdm_structure.py
├── analyze_all_entity_types.py
├── analyze_uuid_lifecycle.py
├── analyze_fork_execute_relation.py
├── analyze_clone_fork_execute.py
└── orthrus_groundtruth/
```
