# THEIA E3 数据提取方案设计文档

## 一、数据集概况

THEIA E3 是 DARPA TC Engagement 3 中由 THEIA 团队在 Linux (Ubuntu) 系统上采集的数据集。数据格式为 CDM 18 JSON，共 25 个文件，约 113,293,343 行。

### 1.1 文件列表（按时间顺序）

```
ta1-theia-e3-official-1r.json ~ .9     (10个文件)
ta1-theia-e3-official-3.json           (1个文件)
ta1-theia-e3-official-5m.json          (1个文件)
ta1-theia-e3-official-6r.json ~ .12    (13个文件)
```

### 1.2 记录类型分布（单文件 6r.json.8 样本，5,000,000 行）

| 记录类型 | 数量 | 占比 |
|----------|------|------|
| Event | 4,713,418 | 94.3% |
| MemoryObject | 235,905 | 4.7% |
| FileObject | 24,562 | 0.5% |
| Subject | 13,124 | 0.3% |
| NetFlowObject | 12,990 | 0.3% |

全量数据统计：

| 实体类型 | 数量 |
|----------|------|
| Subject (SUBJECT_PROCESS) | 279,369 |
| FileObject (FILE_OBJECT_BLOCK) | 1,021,955 |
| NetFlowObject | 186,100 |
| MemoryObject | 5,649,856 |

---

## 二、与 CADETS E3 的核心差异

THEIA E3 与 CADETS E3 在 CDM 数据结构上存在根本性差异：

| 维度 | CADETS E3 | THEIA E3 |
|------|-----------|----------|
| **Subject.properties.map.path** | 0% | **99.3%**（进程路径） |
| **Subject.properties.map.name** | 0% | 0% |
| **Subject.cmdLine** | 0% | **99.3%**（dict格式） |
| **FileObject.baseObject.props.map.filename** | 0% | **97.4%** |
| **FileObject.baseObject.props.map.path** | 0% | 0% |
| **Event.properties.map.exec** | 99.9% | **0%** |
| **Event.predicateObjectPath** | 44.2% | **0%** |
| **FileObject 类型** | FILE_OBJECT_FILE | **FILE_OBJECT_BLOCK** |
| **MemoryObject** | 不存在 | **5,649,856 个** |
| **EVENT_MPROTECT** | 0.03% | **49.5%** |
| **进程创建事件** | EVENT_FORK | **EVENT_CLONE** |

**核心含义：** CADETS 实体记录是"空壳"（信息全在 Event 中），THEIA 实体记录自带丰富信息（但 Event 中没有 exec 等字段）。

---

## 三、UUID 生命周期分析

### 3.1 UUID 生成机制

与 CADETS 本质相同：UUID 在 fork/clone 时生成。

| 指标 | 全量统计 |
|------|---------|
| Subject 记录数 | 279,369 |
| CLONE 事件数 | 239,820 |
| 无 CLONE 的 Subject（已有进程） | ~39,549 |

### 3.2 Subject.path 的真正含义

通过全量验证确认：

```
做过EXECUTE的子进程 (84,400个):
  Subject.path == 父进程path:      40.2%  ← fork继承！
  Subject.path == 第1次EXECUTE dst:  2.3%
  Subject.path == 最后EXECUTE dst:   0.0%
  Subject.path != 以上所有:         57.5%
```

**Subject.path 是 fork 继承的父进程路径（创建时身份），不是 exec 后的真实身份。** 这与 CADETS 完全一样——只是 CADETS 的 Subject 记录连继承值都没有（0%填充），而 THEIA 有（99.3%填充）。

### 3.3 Subject.cmdLine vs Event.cmdLine

```
全量验证 (116,887 个 EXECUTE 事件):
  Subject.cmdLine == Event.cmdLine:  19.5%
  Subject.cmdLine != Event.cmdLine:  80.3%
```

| | Subject.cmdLine | Event.cmdLine (EXECUTE) |
|--|----------------|------------------------|
| 含义 | "我是谁"（创建时身份） | "我要exec成什么" |
| 示例 | `-bash` | `ls --color=auto -alh /data` |
| 位置 | 回填到 CLONE 边 | 存在 EXECUTE 边 |

### 3.4 进程名更新机制

**THEIA 没有 Event.exec 字段（0%），但 EXECUTE 事件 100% 有 dst FileObject：**

```
bash(进程) ──EXECUTE──> /bin/ls (FileObject)
```

通过 EXECUTE 的 dst FileObject 的 filename 更新进程名，效果与 CADETS 用 Event.exec 相同：

```
进程初始名: Subject.path = /bin/bash (fork继承)
EXECUTE dst: /bin/ls → 进程名更新为 /bin/ls
```

### 3.5 CLONE/EXECUTE 模式

```
CLONE创建的子进程: 239,820
  做过EXECUTE: 84,400 (35.2%)
  从未EXECUTE: 155,420 (64.8%)

EXECUTE次数分布:
  1次: 102,770 (93.6%)
  2次:   6,879 (6.3%)
  3次:     109 (0.1%)
```

**64.8%的子进程从未exec**（如 postgres worker、firefox content process），它们的 Subject.path 就是最终身份。

---

## 四、实体过滤决策

### 4.1 保留的实体

| 实体类型 | 来源 | 归类 | 数量 | 命名来源 |
|----------|------|------|------|---------|
| SUBJECT_PROCESS | Subject记录 | `process` | 279,369 | Subject.path → EXECUTE dst更新 |
| FILE_OBJECT_BLOCK | FileObject记录 | `file` | 1,021,955 | baseObject.props.map.filename |
| NetFlowObject | NetFlowObject记录 | `netflow` | 186,100 | 记录本身(四元组) |

### 4.2 丢弃的实体

| 实体类型 | 数量 | 丢弃理由 |
|----------|------|---------|
| MemoryObject | 5,649,856 | 仅被 MPROTECT(2,327,086)/MMAP(247,643)/SHM(598) 引用，这些事件全部过滤 |

---

## 五、事件类型过滤决策

### 5.1 保留的 11 种事件

| 事件类型 | 数量 | 保留理由 |
|----------|------|---------|
| EVENT_READ | 385,069 | 核心读操作 |
| EVENT_WRITE | 152,896 | 核心写操作 |
| EVENT_EXECUTE | 5,645 (样本) / 116,887 (全量) | 核心：进程加载文件，带cmdLine |
| EVENT_CLONE | 12,600 (样本) / 239,820 (全量) | 进程创建 |
| EVENT_OPEN | 312,688 | 文件打开 |
| EVENT_CONNECT | 90,579 | 网络连接 |
| EVENT_SENDTO | 30,577 | 网络发送 |
| EVENT_RECVFROM | 910,321 | 网络接收 |
| EVENT_SENDMSG | 78,871 | 消息发送 |
| EVENT_RECVMSG | 97,960 | 消息接收 |
| EVENT_UNLINK | 17,612 | 文件删除 |

### 5.2 过滤的事件

| 事件类型 | 数量 | 过滤理由 |
|----------|------|---------|
| EVENT_MPROTECT | **2,334,215 (49.5%)** | 占半数事件量，全指向 MemoryObject，纯噪声 |
| EVENT_MMAP | 260,215 | 内存映射，指向 MemoryObject |
| EVENT_WRITE_SOCKET_PARAMS | 13,018 | 套接字参数操作 |
| EVENT_READ_SOCKET_PARAMS | 10,281 | 套接字参数操作 |
| EVENT_SHM | 610 | 共享内存操作 |
| EVENT_MODIFY_FILE_ATTRIBUTES | 261 | 量极少 |

### 5.3 与 CADETS E3 的事件差异

| | CADETS E3 (13种) | THEIA E3 (11种) | 差异 |
|--|-------------------|-----------------|------|
| EVENT_FORK | ✅ | — | THEIA 用 CLONE |
| EVENT_CLONE | — | ✅ | CADETS 用 FORK |
| EVENT_RENAME | ✅ | — | THEIA 中不存在 |
| EVENT_CREATE_OBJECT | ✅ | — | THEIA 中不存在 |

---

## 六、边方向反转

与 CADETS E3 完全一致的 5 种反转：

| 事件 | 原始 | 反转后 |
|------|------|--------|
| EVENT_READ | 进程→文件 | 文件→进程 |
| EVENT_RECVFROM | 进程→网络 | 网络→进程 |
| EVENT_RECVMSG | 进程→网络 | 网络→进程 |
| EVENT_EXECUTE | 进程→文件 | 文件→进程 |
| EVENT_OPEN | 进程→文件 | 文件→进程 |

---

## 七、cmdLine 处理

### 7.1 与 CADETS E3 的统一设计

| | CADETS E3 | THEIA E3 |
|--|-----------|----------|
| CLONE/FORK cmdLine来源 | 子进程第1个EXECUTE的Event.cmdLine | **同左** |
| CLONE/FORK cmdLine fallback | — | 子进程的Subject.cmdLine |
| EXECUTE cmdLine | Event.properties.map.cmdLine | **同左** |
| 节点cmdLine属性 | 无（全null） | 无（回填到CLONE边） |

### 7.2 CLONE边cmdLine的fallback策略

```
64.8%的子进程从未EXECUTE → 无Event.cmdLine
  → fallback到Subject.cmdLine（99.3%有值）
  → 例: sshd clone → bash, bash的Subject.cmdLine="-bash"

35.2%的子进程做过EXECUTE → 使用第1个EXECUTE的Event.cmdLine
  → 例: bash clone → child, child EXECUTE ls
  → CLONE边cmdLine = "ls --color=auto -alh /data"
```

---

## 八、进程命名策略

### 8.1 命名逻辑（与 CADETS 统一）

| 步骤 | CADETS | THEIA |
|------|--------|-------|
| 初始名 | None（0%） | Subject.path（99.3%） |
| 更新来源 | Event.exec（99.9%Event有） | **EXECUTE dst FileObject filename** |
| 更新时机 | 每条Event覆盖 | 每次EXECUTE覆盖 |
| 最终名 | 最后一个exec值 | 最后一次EXECUTE dst文件名 |
| 未exec进程 | 继承的exec值 | Subject.path（不变） |

### 8.2 数据验证

对做过EXECUTE的109,766个进程：
- Subject.path 在 79.6% 的情况下与EXECUTE dst不同（是fork继承的旧名）
- 使用EXECUTE dst更新后能获得进程的真实身份

---

## 九、与其他方法的对比

### 9.1 实体提取

| | 我们 | CAPTAIN | Orthrus | PIDSMaker | KAIROS |
|--|------|---------|---------|-----------|--------|
| Subject命名 | Subject.path → EXECUTE dst更新 | Subject.path (不更新) | Subject.path+cmdLine (正则) | Subject.path+cmdLine (json) | **Event.exec (0%，失效)** |
| File命名 | FileObject.filename (97.4%) | FileObject.filename | FileObject.filename (正则) | 多级fallback | **Event.predicateObjectPath (0%，失效)** |
| 进程名准确性 | **最高**（通过EXECUTE更新） | 偏低（只有初始名） | 中等（初始名+cmdLine） | 中等 | **完全失效** |

### 9.2 边提取

| | 我们(11种) | Orthrus(10种) | PIDSMaker(10种) | KAIROS(7种) |
|--|---------|---------|-----------|--------|
| EVENT_CLONE | ✅ | ✅ | ✅ | — |
| EVENT_UNLINK | ✅ | — | — | — |
| 反转种数 | 5 | 10 | 11 | 3 |
| CLONE边cmdLine | ✅(回填) | — | — | — |
| EXECUTE边cmdLine | ✅ | — | — | — |

---

## 十、用法

### 本地磁盘版

```bash
python extract_theia_e3.py \
    --input_dir /mnt/disk/darpa/theia_e3 \
    --output_dir /mnt/disk/darpa/theia_e3_output
```

输出：`uuid2name.pkl`, `datalist.pkl`, `edges.csv`

### PostgreSQL版

```bash
psql -U postgres -f init_database.sql
python extract_theia_e3_postgres.py \
    --input_dir /mnt/disk/darpa/theia_e3 \
    --db_name theia_e3
```

---

## 十一、文件清单

```
cch_repeat/dataprocess/theia_e3/
├── README.md                          ← 本文件（总体说明）
├── readme_postgre.md                  ← PostgreSQL方案说明
├── init_database.sql                  ← 数据库创建脚本
├── extract_theia_e3.py                ← 提取脚本（本地磁盘版）
├── extract_theia_e3_postgres.py       ← 提取脚本（PostgreSQL版）
├── analyze_cdm_structure.py           ← CDM字段分析脚本
├── analyze_all_entity_types.py        ← 实体类型与事件关系分析
├── analyze_uuid_lifecycle.py          ← UUID生命周期分析
├── analyze_full_dataset.py            ← 全量EXECUTE dst验证
├── analyze_naming_strategy.py         ← 命名策略对比分析
└── orthrus_groundtruth/               ← ground truth（如有）
```
