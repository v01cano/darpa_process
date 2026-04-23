# DARPA TC E3 数据集提取方案总览

## 目录

- [一、项目概述](#一项目概述)
- [二、数据集概览](#二数据集概览)
- [三、统一设计原则](#三统一设计原则)
- [四、五个数据集的 UUID 生命周期](#四五个数据集的-uuid-生命周期)
- [五、实体提取策略](#五实体提取策略)
- [六、节点命名策略](#六节点命名策略)
- [七、边类型与方向](#七边类型与方向)
- [八、cmdLine 处理策略](#八cmdline-处理策略)
- [九、与其他方法的对比](#九与其他方法的对比)
- [十、数据库设计](#十数据库设计)
- [十一、使用方式](#十一使用方式)
- [十二、Ground Truth 验证](#十二ground-truth-验证)

---

## 一、项目概述

本目录包含对 **DARPA Transparent Computing Engagement 3 (E3)** 五个数据集的数据提取方案。每个数据集代表一个独特的操作系统平台和 CDM 数据模型：

| 数据集 | 平台 | 采集方 | CDM 版本 | 文件数 |
|--------|------|--------|---------|--------|
| **CADETS E3** | FreeBSD | CADETS | CDM18 | 10 |
| **THEIA E3** | Linux | THEIA | CDM18 | 25 |
| **ClearScope E3** | Android | ClearScope | CDM18 | 51 |
| **TRACE E3** | Linux | TRACE | CDM18 | 211 |
| **FiveDirections E3** | Windows | FiveDirections | CDM18 | 55 |

### 目录结构

```
cch_repeat/dataprocess/
├── README.md                        ← 本文件（总览）
├── cadets_e3/
│   ├── README.md                    ← CADETS 详细说明
│   ├── readme_postgre.md            ← PostgreSQL 方案
│   ├── init_database.sql
│   ├── extract_cadets_e3.py         ← 本地磁盘版
│   ├── extract_cadets_e3_postgres.py
│   ├── analyze_*.py                 ← 分析脚本
│   └── verify_ground_truth.py       ← GT 验证
├── theia_e3/
│   ├── ... (同上结构)
├── clearscope_e3/
│   ├── ...
├── trace_e3/
│   ├── ...
│   └── extract_trace_e3_part.py     ← 分片版（大数据集）
│   └── extract_trace_e3_break.py    ← 续跑版
└── fivedirections_e3/
    ├── ...
```

---

## 二、数据集概览

### 2.1 实体规模对比

| 数据集 | SUBJECT_PROCESS | SUBJECT_THREAD/UNIT | FileObject | NetFlowObject | 特殊实体 |
|--------|----------------|---------------------|-----------|---------------|---------|
| CADETS | 26,297 | — | 317K（含UNIX_SOCKET） | 12,659 | — |
| THEIA | 279,369 | — | 1,022K（BLOCK类型） | 186,100 | MemoryObject(5.6M) |
| ClearScope | 37 | — | 3,903 | 228 | SrcSinkObject(513) |
| TRACE | 3,207 | UNIT: 411,355 | 8,715 | 25,881 | SrcSinkObject(644K) |
| FiveDirections | 1,412 | **THREAD: 37,381** | 139,480 | 3,445 | **RegistryKeyObject(104K)** |

### 2.2 事件规模对比

| 数据集 | 总 Event | FORK/CLONE | EXECUTE | 其他特殊 |
|--------|---------|-----------|---------|---------|
| CADETS | 4.6M | FORK: 26,194 | 24,408 | — |
| THEIA | 4.7M | CLONE: 12,600 | 5,645 | — |
| ClearScope | 4.97M | **0（无）** | **0（无）** | EVENT_READ 占91%(Binder) |
| TRACE | 3.3M | FORK: 483, CLONE: 2,250 | 393 | EVENT_UNIT: 411K |
| FiveDirections | 4.8M | FORK: 419 | 556 | EVENT_LOADLIBRARY: 35K, CREATE_THREAD: 13K |

### 2.3 关键字段填充率对比

| 字段 | CADETS | THEIA | ClearScope | TRACE | FiveDirections |
|------|--------|-------|-----------|-------|---------------|
| **Subject.properties.map.name** | 0% | 0% | 0% | **100%** | 0% |
| **Subject.properties.map.path** | 0% | **99.3%** | 0% | 0% | 0% |
| **Subject.cmdLine** | 0% | **99.3%** | **100%** | 27-100% | 94.9%(PROCESS) |
| **FileObject.baseObject.map.path** | 0% | 0% | **100%** | **100%** | 0% |
| **FileObject.baseObject.map.filename** | 0% | **97.4%** | 0% | 0% | 0% |
| **Event.properties.map.exec** | **99.9%** | 0% | 0% | 0% | 0% |
| **Event.properties.map.cmdLine** | 0.5% | 0.1% | 0% | 0% | 0% |
| **Event.predicateObjectPath** | 44.2% | 0% | 0.9% | 0.1-12.2% | **54.3%** |

**核心发现：** 每个数据集的字段填充模式都独特，**不存在通用的提取方案**，必须针对性设计。

---

## 三、统一设计原则

尽管各数据集差异巨大，我们遵循统一的设计原则：

### 3.1 三类标准节点

所有数据集输出统一的节点类型：
- **`process`** — 进程节点
- **`file`** — 文件/目录/注册表节点（Windows 的 RegistryKey 归入此类）
- **`netflow`** — 网络连接节点

### 3.2 边方向：数据流方向

统一将"发起方向"（subject → predicateObject）反转为"数据流方向"：
- **READ 类**（READ, RECVFROM, RECVMSG, OPEN, EXECUTE, LOADLIBRARY, ACCEPT）：反转
- **WRITE 类**（WRITE, SENDTO, SENDMSG, CONNECT, FORK, CLONE）：不反转

### 3.3 cmdLine 作为边属性

- **CLONE/FORK 边**：携带子进程的 cmdLine（完整命令行）
- **EXECUTE 边**：携带命令行或目标程序信息
- **其他边**：cmdLine = None

### 3.4 节点 name 简洁化

节点 name 只存程序名（`svchost.exe`）而非完整 cmdLine，便于同名进程聚类分析。

### 3.5 统一输出格式

所有数据集的提取结果：
- **本地磁盘版**：`uuid2name.pkl` + `datalist.pkl`（或分片版 `edges_part_*.pkl`）
- **PostgreSQL版**：相同的 4 张表结构（subject/file/netflow/event），下游代码通用

---

## 四、五个数据集的 UUID 生命周期

**这是理解数据模型的核心。** 每个数据集的 UUID 生成机制完全不同，直接决定了提取策略。

### 4.1 CADETS E3：UUID 空壳 + Event 逐步命名

```
fork → 创建新 UUID（Subject 记录完全为空）
      ↓
exec → Event 中 exec 字段逐步设置进程名（99.9% 填充）
      ↓
进程生命周期中，Event.exec 反映当前程序名
```

**关键决策：** 用 Event.exec 作为进程名来源（最后一次 exec 的值）。

### 4.2 THEIA E3：延迟写入 Subject 记录

```
fork → 创建新 UUID（初始身份继承自父进程）
      ↓
exec → Subject 记录延迟写入（path 字段已是完整路径）
      ↓
再次 exec → 通过 EVENT_EXECUTE 的 dst FileObject 更新进程名
```

**关键决策：** Subject.path 作为初始名，通过后续 EXECUTE dst 文件名更新。

### 4.3 ClearScope E3（Android）：Zygote 模型

```
Android 系统启动 → 所有应用通过 Zygote fork
     ↓
无 fork/exec 事件记录 → 只有 37 个进程
     ↓
Subject.cmdLine 就是 Android 包名（system_server、com.android.media 等）
```

**关键决策：** 直接用 Subject.cmdLine 作为进程名，单遍扫描。

### 4.4 TRACE E3：exec 创建新 UUID

```
fork → 父线程（UNIT）触发 → 创建新进程 UUID
      ↓
exec → 创建新的进程 UUID（身份转换）
      ↓
EXECUTE 的 dst 是新进程（Subject 类型，非 FileObject）
```

**关键决策：**
- EVENT_EXECUTE 不反转（进程→新进程，非文件→进程）
- Subject.name 直接作为进程名
- SUBJECT_UNIT 丢弃（411K 个执行单元）

### 4.5 FiveDirections E3（Windows）：CreateProcess 原子操作

```
某线程调用 CreateProcess("wmiprvse.exe")
     ↓（同一纳秒，原子操作）
- 创建新进程 UUID（Subject 记录中 cmdLine 已完整）
- 创建新进程的第一个线程 UUID
- EVENT_FORK: src=调用线程, dst=新进程
- EVENT_EXECUTE: src=新进程, dst=PE文件
```

**关键决策：**
- **THREAD UUID 合并到父 PROCESS UUID**（处理 Windows 特有的线程级追踪）
- 进程名 = EXECUTE 的 predicateObjectPath（简洁程序名）
- RegistryKeyObject 归入 file 类型
- CREATE_THREAD 事件丢弃（合并后变自环）

### 4.6 UUID 模型对比总表

| 维度 | CADETS | THEIA | ClearScope | TRACE | FiveDirections |
|------|--------|-------|-----------|-------|---------------|
| UUID 创建时机 | fork 时（空壳） | fork 时（延迟写入） | Android 启动时 | fork+exec 各创建 | CreateProcess 原子 |
| 进程身份变化 | 通过 Event.exec | 通过 EXECUTE dst | 不变 | 每次 exec 新 UUID | 不变（cmdLine 一次定） |
| EXECUTE 作用 | 加载+更新身份 | 加载+更新身份 | 不存在 | 身份转换（新UUID） | 加载声明（不变身份） |
| FORK src 类型 | PROCESS | PROCESS | — | PROCESS/UNIT | **THREAD** |
| 特殊实体 | UNIX_SOCKET | MemoryObject, FILE_OBJECT_BLOCK | SrcSinkObject(Binder) | SUBJECT_UNIT | SUBJECT_THREAD, RegistryKey |

---

## 五、实体提取策略

### 5.1 统一的实体过滤规则

| 实体类型 | CADETS | THEIA | ClearScope | TRACE | FiveDirections |
|---------|--------|-------|-----------|-------|---------------|
| SUBJECT_PROCESS | ✅ | ✅ | ✅ | ✅ | ✅ |
| SUBJECT_THREAD | — | — | — | — | **UUID合并** |
| SUBJECT_UNIT | — | — | — | 丢弃 | — |
| FileObject(FILE) | ✅ | ✅(BLOCK) | ✅ | ✅ | ✅ |
| FileObject(DIR) | ✅ | — | ✅ | ✅ | ✅ |
| FileObject(UNIX_SOCKET) | **丢弃** | — | ✅ | ✅ | — |
| FileObject(PEFILE) | — | — | — | — | ✅ |
| NetFlowObject | ✅ | ✅ | ✅ | ✅ | ✅ |
| RegistryKeyObject | — | — | — | — | ✅(归入file) |
| MemoryObject | — | 丢弃 | — | — | 丢弃 |
| UnnamedPipeObject | 丢弃 | 丢弃 | — | 丢弃 | — |
| SrcSinkObject | 丢弃 | — | 丢弃 | 丢弃 | 丢弃 |
| ProvenanceTagNode | 丢弃 | — | 丢弃 | — | — |

### 5.2 SUBJECT_THREAD 的 UUID 合并（FiveDirections 独有）

**核心机制：**

```python
# Pass 1：建立映射
thread_to_process[thread_uuid] = parent_process_uuid

# Pass 2 提边时替换
src = thread_to_process.get(event.subject, event.subject)
dst = thread_to_process.get(event.predicateObject, event.predicateObject)
```

**效果：**
- THREAD 发起的 3.8M+ 条事件全部归属到父 PROCESS
- 图节点只有 1,412 PROCESS（不是 1,412 + 37,381 = 39K）
- 攻击调查可以直接通过 PROCESS UUID 查询所有行为

---

## 六、节点命名策略

### 6.1 各数据集进程命名来源

| 数据集 | 主要来源 | Fallback |
|--------|---------|---------|
| **CADETS** | Event.properties.map.exec 最后一次值（99.9%） | — |
| **THEIA** | Subject.properties.map.path（初始）<br>EXECUTE dst FileObject.filename（更新） | — |
| **ClearScope** | Subject.cmdLine（Android 包名，100%） | — |
| **TRACE** | Subject.properties.map.name（100%） | — |
| **FiveDirections** | EVENT_EXECUTE.predicateObjectPath（94.1%） | 从 Subject.cmdLine 提取可执行文件名 |

### 6.2 为什么用简洁程序名？

**问题：** 如果用完整 cmdLine 作为 name，多个 svchost 实例会被认为是不同进程：
```
node1: "C:\Windows\system32\svchost.exe -k DcomLaunch"
node2: "C:\Windows\system32\svchost.exe -k NetworkService"
node3: "C:\Windows\system32\svchost.exe -k LocalSystem"
```

**解决方案：** 用简洁程序名作为 name，完整 cmdLine 放在边上：
```
所有 svchost 实例: name = "svchost.exe"
FORK/EXECUTE 边上: cmdLine = "C:\Windows\...\svchost.exe -k DcomLaunch"
```

**优势：** 
- 按 name 聚类：所有 svchost 行为归为一类，便于异常检测
- 保留参数信息：边上 cmdLine 仍可区分实例

### 6.3 文件命名来源

| 数据集 | 主要来源 |
|--------|---------|
| **CADETS** | Event.predicateObjectPath（44.2%） |
| **THEIA** | FileObject.baseObject.properties.map.filename（97.4%） |
| **ClearScope** | FileObject.baseObject.properties.map.path（100%） |
| **TRACE** | FileObject.baseObject.properties.map.path（100%） |
| **FiveDirections** | Event.predicateObjectPath（54.3%） |

### 6.4 网络连接命名

所有数据集统一：`{localAddr}:{localPort}->{remoteAddr}:{remotePort}`

### 6.5 Windows 特有：RegistryKey 命名

FiveDirections 的 RegistryKeyObject 用 `datum['key']` 作为 path，归入 file 类型：
```
path = "\REGISTRY\MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
```

---

## 七、边类型与方向

### 7.1 各数据集保留的边类型数量

| 数据集 | 保留事件数 | 反转事件数 |
|--------|----------|----------|
| CADETS | 13 | 5 |
| THEIA | 11 | 5 |
| ClearScope | 11 | 4（无 EXECUTE） |
| TRACE | 16 | 6 |
| FiveDirections | 17 | 7 |

### 7.2 边方向反转规则

统一的数据流方向规则：

| 事件 | 原始方向 | 反转后 | 所有数据集 |
|------|---------|-------|-----------|
| EVENT_READ | process→file | file→process | ✅ 反转 |
| EVENT_RECVFROM | process→netflow | netflow→process | ✅ 反转 |
| EVENT_RECVMSG | process→netflow | netflow→process | ✅ 反转 |
| EVENT_OPEN | process→file | file→process | ✅ 反转 |
| EVENT_ACCEPT | process→netflow | netflow→process | ✅ 反转（除CADETS）|
| EVENT_EXECUTE | process→file | file→process | ✅ 反转（除TRACE）|
| EVENT_LOADLIBRARY | process→file | file→process | ✅ 反转（TRACE/FiveDirections）|
| EVENT_WRITE | process→file | **不反转** | ✅ 保持 |
| EVENT_SENDTO | process→netflow | **不反转** | ✅ 保持 |
| EVENT_FORK/CLONE | parent→child | **不反转** | ✅ 保持 |

### 7.3 TRACE 的 EXECUTE 特殊：不反转

因为 TRACE 的 EXECUTE 连接**两个进程**（旧进程→新进程），而非进程→文件，不符合"文件→进程"的数据流语义，故不反转。

### 7.4 典型过滤的事件

- **EVENT_CHECK_FILE_ATTRIBUTES**：噪声
- **EVENT_CLOSE**：信息量低
- **EVENT_FCNTL**：大多无 predObj
- **EVENT_OTHER**：未分类
- **EVENT_MMAP/MPROTECT**：内存操作（除必要的 LOAD 场景）
- **EVENT_EXIT/SIGNAL/LOGIN/LOGOUT**：非核心
- **EVENT_CREATE_THREAD**（FiveDirections）：THREAD 合并后自环

---

## 八、cmdLine 处理策略

### 8.1 总览

| 数据集 | FORK/CLONE 边 | EXECUTE 边 |
|--------|-------------|-----------|
| CADETS | 子进程第1次EXECUTE的 Event.cmdLine | Event.cmdLine |
| THEIA | 子进程第1次EXECUTE的 Event.cmdLine（fallback Subject.cmdLine） | Event.cmdLine |
| ClearScope | 无（无 FORK/EXECUTE） | — |
| TRACE | dst 的 Subject.cmdLine | dst 的 Subject.cmdLine |
| FiveDirections | dst 的 Subject.cmdLine | src 的 Subject.cmdLine |

### 8.2 各数据集 cmdLine 特点

**CADETS（Event.cmdLine 0.5%）：**
- 仅 EVENT_EXECUTE 事件有 cmdLine
- 需要通过"回填"策略：为每个进程记录第一次 EXECUTE 的 cmdLine，在 FORK 边上回填

**THEIA（Event.cmdLine 0.1%）：**
- 类似 CADETS 但有 Subject.cmdLine 作为 fallback

**ClearScope（Event.cmdLine 0%）：**
- 无 FORK/EXECUTE 事件，不需要 cmdLine

**TRACE（Event.cmdLine 0%）：**
- 所有 cmdLine 来自 Subject 记录
- FORK/EXECUTE 边上的 cmdLine 都是 **dst 新进程**的 Subject.cmdLine

**FiveDirections（Event.cmdLine 0%）：**
- 所有 cmdLine 来自 Subject 记录
- FORK 边 cmdLine = dst 的 Subject.cmdLine（新进程完整命令行）
- EXECUTE 边 cmdLine = src 的 Subject.cmdLine（执行的进程完整命令行）

---

## 九、与其他方法的对比

### 9.1 项目支持范围对比

| 数据集 | Orthrus | KAIROS | PIDSMaker | CAPTAIN | **我们** |
|--------|---------|--------|-----------|---------|---------|
| CADETS E3 | ✅ | ✅ | ✅ | ✅ | ✅ |
| THEIA E3 | ✅ | ✅(notebook) | ✅ | ✅ | ✅ |
| ClearScope E3 | ✅ | ✅(notebook) | ✅(部分) | ❌ | ✅ |
| TRACE E3 | ❌ | ❌ | ❌ | ✅ | ✅ |
| FiveDirections E3 | ❌ | ❌ | ❌ | ✅ | ✅ |

**只有 CAPTAIN 和我们支持全部 5 个数据集。**

### 9.2 CADETS E3 的处理差异

| 维度 | Orthrus | KAIROS | PIDSMaker | CAPTAIN | **我们** |
|------|---------|--------|-----------|---------|---------|
| Subject 来源 | Event 记录 | Event 记录 | Subject 记录 | Subject 记录 | Subject 记录 |
| 进程名字段 | Event.exec | Event.exec | Subject.properties.map.path(**0%**) | properties.name → Event.exec更新 | properties.name → Event.exec更新 |
| 进程名最终正确率 | 有值 | 有值 | **0%（全null）** | 99.9% | **99.9%** |
| File 命名 | Event.predObjectPath | Event.predObjectPath | 多级 fallback（**0%**） | 记录+Event更新 | 记录+Event更新 |
| Hash 对象 | hash(uuid) | **hash(属性值)** ← bug | hash(uuid) | UUID直接 | UUID直接 |

**PIDSMaker 在 CADETS 上实际失效**：所有进程名均为 null。

### 9.3 THEIA E3 的处理差异

| 维度 | Orthrus | PIDSMaker | CAPTAIN | **我们** |
|------|---------|-----------|---------|---------|
| Subject 命名 | path+cmdLine（正则） | path+cmdLine（json） | properties.map.path→EXECUTE更新 | properties.map.path→EXECUTE更新 |
| FileObject 类型 | 不过滤 | 不过滤 | 不过滤 | 不过滤（FILE_OBJECT_BLOCK 是 THEIA 唯一类型） |
| MemoryObject | 不处理 | 不处理 | 丢弃 | 丢弃 |

### 9.4 ClearScope E3 的处理差异

| 维度 | Orthrus | PIDSMaker | KAIROS | **我们** |
|------|---------|-----------|--------|---------|
| Subject 命名 | Subject.cmdLine | Subject.cmdLine | **Event.exec（0%失效）** | Subject.cmdLine |
| File 命名 | Event.predObjPath | 多级fallback | **Event.predObjPath（0.9%）** | FileObject.path（100%） |
| SrcSinkObject | 丢弃 | 丢弃 | — | 丢弃（Binder 占 91% 事件） |

**KAIROS 在 ClearScope 上完全失效**。

### 9.5 TRACE E3 的处理差异

| 维度 | CAPTAIN | **我们** |
|------|---------|---------|
| SUBJECT_UNIT | 丢弃（返回None） | 丢弃 |
| EVENT_EXECUTE 映射 | 'update_process'（身份转换） | 保留原名 |
| EVENT_LOADLIBRARY 映射 | 'execve'（类似Linux） | 保留原名 |
| FORK+CLONE | 合并为 'clone' | **分开保留**（两者语义不同） |
| cmdLine 位置 | 节点属性 | 边属性 |
| 边方向反转 | 无（标签传播处理） | 显式反转 |
| chmod/setuid/mprotect | 保留 | 过滤 |

### 9.6 FiveDirections E3 的处理差异（最关键）

| 维度 | CAPTAIN | **我们** |
|------|---------|---------|
| **SUBJECT_THREAD** | **直接丢弃（大量数据丢失）** | **UUID 合并到父 PROCESS** |
| 节点 name | 完整 cmdLine | 简洁程序名（svchost.exe） |
| cmdLine 位置 | 节点属性 | 边属性（FORK/EXECUTE 边） |
| MemoryObject | 保留 | 丢弃 |
| RegistryKey | 独立 subtype | 归入 file 类型 |
| MPROTECT/UPDATE | 有代码（pdb.set_trace） | 过滤 |
| 数据完整性 | **丢失 ~95% 线程事件** | **保留 3.8M+ 线程事件** |

**CAPTAIN 在 FiveDirections 上的关键缺陷：** 由于 THREAD 被丢弃，Windows 下 95% 的事件（以 THREAD 为 src）会因 assert 失败被跳过。

### 9.7 综合对比总表

| 项目 | 核心思路 | 扫描遍数 | 节点类型 | cmdLine 位置 | 边反转 | 数据完整性 |
|------|---------|---------|---------|-------------|-------|-----------|
| **Orthrus** | PostgreSQL + GNN | 4-5 | 3类 | 节点属性 | 10种反转 | 不支持 TRACE/FD |
| **KAIROS** | PyG TemporalData | 5 | 3类 | 节点属性 | 3种反转 | CADETS/ClearScope 失效 |
| **PIDSMaker** | 多模块框架 | 4 | 3类 | 节点属性 | 11种反转 | CADETS 失效 |
| **CAPTAIN** | 标签传播（iTag/cTag） | 1 流式 | 3类 | 节点属性 | 无显式反转 | Windows 数据丢失 |
| **我们** | 统一图模型 | 2 | 3类 | **边属性** | **5-7种反转** | **支持全部 5 个数据集** |

---

## 十、数据库设计

### 10.1 统一的 4 张表

所有 5 个数据集使用相同的表结构，下游代码通用：

```sql
-- 进程节点
CREATE TABLE subject_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,    -- SHA256(uuid)
    exec_name   VARCHAR,              -- 简洁程序名
    index_id    BIGINT
);

-- 文件节点（含目录、Registry）
CREATE TABLE file_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    path        VARCHAR,
    index_id    BIGINT
);

-- 网络节点
CREATE TABLE netflow_node_table (
    node_uuid   VARCHAR NOT NULL PRIMARY KEY,
    hash_id     VARCHAR NOT NULL,
    src_addr    VARCHAR,
    src_port    VARCHAR,
    dst_addr    VARCHAR,
    dst_port    VARCHAR,
    index_id    BIGINT
);

-- 事件/边
CREATE TABLE event_table (
    src_uuid        VARCHAR,
    src_index_id    BIGINT,
    operation       VARCHAR,
    dst_uuid        VARCHAR,
    dst_index_id    BIGINT,
    event_uuid      VARCHAR NOT NULL,
    timestamp_rec   BIGINT,
    cmdline         VARCHAR,         -- FORK/EXECUTE 边才有值
    _id             SERIAL PRIMARY KEY
);
```

### 10.2 与 Orthrus 表结构的差异

| 维度 | Orthrus | 我们 |
|------|---------|------|
| Subject 列 | uuid, hash, **path, cmd, idx** | uuid, hash, **exec_name, idx** |
| Event 列 | src_node(hash), dst_node(hash) | **src_uuid, dst_uuid** |
| cmdline 列 | ❌ 无 | ✅ 有 |
| Registry 表 | ❌ | ✅ 归入 file |

**改进理由：**
1. 合并 path/cmd → exec_name：path 在 CADETS 中 0% 填充，冗余
2. 使用 UUID 替代 hash：可直接追溯原始 CDM，SHA256(uuid) 可随时计算
3. 新增 cmdline 列：保留完整命令行信息

### 10.3 跨数据集查询

由于表结构统一，可以用相同代码查询任意数据集：

```python
def load_attack_chain(db_name):
    """查询攻击链 - 适用于任意数据集"""
    conn = psycopg2.connect(database=db_name, ...)
    cur = conn.cursor()
    cur.execute("""
        SELECT s1.exec_name AS process,
               e.operation,
               COALESCE(s2.exec_name, f.path, n.dst_addr) AS target,
               e.cmdline
        FROM event_table e
        JOIN subject_node_table s1 ON e.src_uuid = s1.node_uuid
        LEFT JOIN subject_node_table s2 ON e.dst_uuid = s2.node_uuid
        LEFT JOIN file_node_table f ON e.dst_uuid = f.node_uuid
        LEFT JOIN netflow_node_table n ON e.dst_uuid = n.node_uuid
        WHERE e.timestamp_rec BETWEEN %s AND %s
        ORDER BY e.timestamp_rec
    """, (start_ts, end_ts))
    return cur.fetchall()
```

---

## 十一、使用方式

### 11.1 CADETS E3

```bash
cd /home/cch/CAPTAIN/CAPTAIN-main/cch_repeat/dataprocess/cadets_e3

# 本地版
python extract_cadets_e3.py \
    --input_dir /mnt/disk/darpa/cadets_e3 \
    --output_dir /mnt/disk/darpa/cadets_e3_output

# PostgreSQL 版
psql -U postgres -f init_database.sql
python extract_cadets_e3_postgres.py \
    --input_dir /mnt/disk/darpa/cadets_e3
```

### 11.2 THEIA E3

```bash
cd theia_e3
python extract_theia_e3.py \
    --input_dir /mnt/disk/darpa/theia_e3 \
    --output_dir /mnt/disk/darpa/theia_e3_output
```

### 11.3 ClearScope E3

```bash
cd clearscope_e3
python extract_clearscope_e3.py \
    --input_dir /mnt/disk/darpa/clearscope_e3 \
    --output_dir /mnt/disk/darpa/clearscope_e3_output
```

### 11.4 TRACE E3（211 文件，使用分片版）

```bash
cd trace_e3
python extract_trace_e3_part.py \
    --input_dir /mnt/disk/darpa/trace_e3 \
    --output_dir /mnt/disk/darpa/trace_e3_output

# 如中断，使用续跑版
python extract_trace_e3_break.py \
    --input_dir /mnt/disk/darpa/trace_e3 \
    --output_dir /mnt/disk/darpa/trace_e3_output \
    --resume_from ta1-trace-e3-official.json.16 \
    --batch_size 10
```

### 11.5 FiveDirections E3

```bash
cd fivedirections_e3
python extract_fivedirections_e3.py \
    --input_dir /mnt/disk/darpa/fivedirections_e3 \
    --output_dir /mnt/disk/darpa/fivedirections_e3_output \
    --batch_size 10
```

### 11.6 下游使用

```python
import pickle

# 加载节点
with open('uuid2name.pkl', 'rb') as f:
    uuid2name = pickle.load(f)

# 加载边（小数据集）
with open('datalist.pkl', 'rb') as f:
    datalist = pickle.load(f)

# 加载分片边（大数据集，如 TRACE/FiveDirections）
from extract_trace_e3_part import load_all_edges, iter_edges
edges = load_all_edges('./output_trace_e3')
# 或流式迭代
for edge in iter_edges('./output_trace_e3'):
    ts, etype, src_uuid, src_type, src_name, dst_uuid, dst_type, dst_name, cmdline = edge
```

---

## 十二、Ground Truth 验证

每个数据集都有 `verify_ground_truth.py` 脚本，验证攻击节点的提取正确性。

### 12.1 验证内容

1. **UUID 匹配率**：ground truth 中的每个 UUID 是否在提取数据中存在
2. **名字对应关系**：提取的 name 是否与 GT 描述一致
3. **攻击节点之间的边**：构建攻击子图
4. **攻击链时序**：按时间顺序展示攻击步骤

### 12.2 各数据集 Ground Truth 位置

```
cadets_e3/orthrus_groundtruth/
  ├── node_Nginx_Backdoor_06.csv
  ├── node_Nginx_Backdoor_12.csv
  └── node_Nginx_Backdoor_13.csv

theia_e3/orthrus_groundtruth/
  ├── node_Browser_Extension_Drakon_Dropper.csv
  └── node_Firefox_Backdoor_Drakon_In_Memory.csv

clearscope_e3/orthrus_groundtruth/
  └── node_clearscope_e3_firefox_0411.csv

trace_e3/pidsmaker_groundtruth/
  ├── node_trace_e3_firefox_0410.csv
  ├── node_trace_e3_phishing_executable_0413.csv
  └── node_trace_e3_pine_0413.csv

fivedirections_e3/pidsmaker_groundtruth/
  └── (具体文件)
```

### 12.3 验证方式

```bash
cd <dataset_directory>
python verify_ground_truth.py
```

---

## 十三、关键决策总结

### 13.1 为什么不直接用现有方法？

| 现有方法 | 局限性 |
|---------|-------|
| **Orthrus** | 不支持 TRACE、FiveDirections；在 CADETS 上 path/cmd 列全 null |
| **KAIROS** | 不支持 TRACE、FiveDirections；Hash 用属性值（非 UUID）导致冲突；ClearScope 失效 |
| **PIDSMaker** | 不支持 TRACE、FiveDirections；CADETS 上 path/cmdLine 全 null（方法论正确但字段不匹配） |
| **CAPTAIN** | 不支持 ClearScope；FiveDirections 丢失 95% 线程事件；节点 name 用完整 cmdLine 不利聚类 |

### 13.2 我们的核心创新

1. **UUID 生命周期深度分析**：针对每个数据集做 UUID 生成机制的完整分析，决策基于数据
2. **SUBJECT_THREAD UUID 合并**（FiveDirections）：保留 Windows 下 95% 的行为事件
3. **统一的简洁 name + 边上 cmdLine**：便于聚类分析，保留参数信息
4. **统一的 4 表数据库设计**：五个数据集下游代码通用
5. **分片输出支持大数据集**：TRACE 300GB 可正常处理

### 13.3 设计决策对照表

| 问题 | 传统做法 | 我们的做法 | 理由 |
|------|---------|-----------|------|
| FiveDirections 的 THREAD | 丢弃 | UUID 合并 | 保留 95% 行为 |
| CADETS 进程名 | PIDSMaker: Subject.path（失效） | Event.exec 动态更新 | 99.9% 填充率 |
| THEIA 进程名 | Subject.path（初始） | Subject.path + EXECUTE dst 更新 | 身份可能变化 |
| cmdLine | 节点属性 | 边属性 | 便于同名进程聚类 |
| RegistryKey | 独立类型 | 归入 file | 统一三类节点 |
| 边方向 | 操作方向 | 数据流方向 | 攻击追踪语义更清晰 |

---

## 十四、参考文献与数据源

- DARPA TC Engagement 3: https://github.com/darpa-i2o/Transparent-Computing
- CDM Schema: CDM18（所有五个数据集使用）
- Orthrus: 基于溯源图的入侵检测
- KAIROS: TGN 时序图网络
- PIDSMaker: 多模块流水线框架
- CAPTAIN: 基于标签传播的入侵检测

---

## 附录：快速索引

- [CADETS E3 详细文档](./cadets_e3/README.md)
- [THEIA E3 详细文档](./theia_e3/README.md)
- [ClearScope E3 详细文档](./clearscope_e3/README.md)
- [TRACE E3 详细文档](./trace_e3/README.md)
- [FiveDirections E3 详细文档](./fivedirections_e3/README.md)

PostgreSQL 方案：
- [CADETS E3 PostgreSQL](./cadets_e3/readme_postgre.md)
- [THEIA E3 PostgreSQL](./theia_e3/readme_postgre.md)
- [ClearScope E3 PostgreSQL](./clearscope_e3/readme_postgre.md)
- [TRACE E3 PostgreSQL](./trace_e3/readme_postgre.md)
- [FiveDirections E3 PostgreSQL](./fivedirections_e3/readme_postgre.md)
