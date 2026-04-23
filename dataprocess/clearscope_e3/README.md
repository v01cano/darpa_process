# ClearScope E3 数据提取方案设计文档

## 一、数据集概况

ClearScope E3 是 DARPA TC Engagement 3 中由 ClearScope 团队在 **Android** 平台上采集的数据集。CDM 18 JSON 格式，共 51 个文件。

**与 CADETS/THEIA 的根本性差异：Android 不使用 fork+exec 进程模型。**

### 1.1 记录类型分布（ta1-clearscope-e3-official.json 样本，5,000,000 行）

| 记录类型 | 数量 | 占比 |
|----------|------|------|
| Event | 4,967,906 | 99.4% |
| ProvenanceTagNode | 27,383 | 0.5% |
| FileObject | 3,903 | 0.08% |
| SrcSinkObject | 513 | 0.01% |
| NetFlowObject | 228 | <0.01% |
| **Subject** | **37** | **<0.01%** |

**只有 37 个进程！** Android 应用进程由系统框架管理，不通过 fork+exec 创建。

---

## 二、与 CADETS/THEIA 的三方对比

| 维度 | CADETS E3 (FreeBSD) | THEIA E3 (Linux) | **ClearScope E3 (Android)** |
|------|---------------------|------------------|---------------------------|
| Subject数 | ~224,000 | ~279,000 | **37** |
| EVENT_FORK/CLONE | 26,194 FORK | 239,820 CLONE | **0（无）** |
| EVENT_EXECUTE | 24,408 | 116,887 | **0（无）** |
| Subject.path | 0% | 99.3% | **0%** |
| Subject.cmdLine | 0% | 99.3% (命令行) | **100% (Android进程名)** |
| FileObject.path | 0% | 0% | **100%** |
| FileObject.filename | 0% | 97.4% | 0% |
| Event.exec | 99.9% | 0% | **0%** |
| Event.cmdLine | 0.5% | 0.1% | **0%** |
| Event.predicateObjectPath | 44.2% | 0% | **0.9%** |
| SrcSinkObject | 0引用→丢弃 | 不存在 | **4,700,000+引用** |
| MemoryObject | 不存在 | 5,649,856 | **不存在** |
| 进程名来源 | Event.exec→EXECUTE dst | Subject.path→EXECUTE dst | **Subject.cmdLine（直接）** |
| 进程名更新 | 需要（2遍） | 需要（2遍） | **不需要（1遍）** |

---

## 三、实体分析

### 3.1 Subject（37个，全为SUBJECT_PROCESS）

| 字段 | 填充率 | 说明 |
|------|--------|------|
| cmdLine | **100%** | Android 进程名/包名（dict格式） |
| cid (PID) | 100% | 进程ID |
| parentSubject | 100% | 父进程UUID |
| properties.map.path | 0% | 无路径 |
| properties.map.name | 0% | 无名字 |

cmdLine 样例（本质是 Android 进程名，不是命令行）：
```
system_server
com.android.providers.calendar
android.process.acore
com.android.managedprovisioning
android.process.media
```

### 3.2 FileObject（3,903个）

| 子类型 | 数量 | path填充率 |
|--------|------|-----------|
| FILE_OBJECT_FILE | 3,355 | 100% |
| FILE_OBJECT_DIR | 512 | 100% |
| FILE_OBJECT_UNIX_SOCKET | 36 | 100% |

path 来自 `baseObject.properties.map.path`（100% 填充）：
```
/dev/null
/system/framework/services.jar
/data/data/org.mozilla.fennec_firefox_dev/cache/...
```

### 3.3 SrcSinkObject（513个，44种子类型）— 丢弃

| 主要子类型 | 数量 | Event引用数 |
|-----------|------|------------|
| SRCSINK_BINDER | 37 | **4,524,546**（91%的所有Event） |
| SRCSINK_BROADCAST_RECEIVER | 31 | 102,729 |
| SRCSINK_DATABASE | 23 | 27,312 |
| SRCSINK_NOTIFICATION | 7 | 20,650 |
| SRCSINK_SERVICE_MANAGEMENT | 37 | 8,133 |
| ... | ... | ... |

**丢弃理由（与 Orthrus 一致）：**
1. SrcSinkObject 不属于三类标准节点（process/file/netflow）
2. Orthrus/PIDSMaker 都不保留 SrcSinkObject
3. 91% 的 Binder READ 是 Android IPC 的背景噪声
4. 攻击在文件层面（Firefox 缓存），不在 Binder IPC 层面
5. 丢弃 SrcSinkObject 后，引用它们的 Event 在边提取时自动跳过

---

## 四、事件类型分析

| 事件类型 | 数量 | 有predObj | **决策** | 理由 |
|----------|------|----------|---------|------|
| EVENT_READ | 4,677,198 | 93.4% | **保留** | 核心读操作（91%指向Binder会被自动跳过） |
| EVENT_WRITE | 183,986 | 94.0% | **保留** | 核心写操作 |
| EVENT_CLOSE | 58,176 | 5.0% | **过滤** | 信息量低 |
| EVENT_DUP | 33,138 | 0.08% | **过滤** | 文件描述符复制，几乎无predObj |
| EVENT_CHECK_FILE_ATTRIBUTES | 6,236 | 100% | **过滤** | 文件属性检查 |
| EVENT_OTHER | 3,001 | 51% | **过滤** | 未分类事件 |
| EVENT_OPEN | 2,815 | 98.6% | **保留** | 文件打开 |
| EVENT_MODIFY_PROCESS | 1,758 | 100% | **过滤** | 进程修改 |
| EVENT_RENAME | 203 | 100% | **保留** | 文件重命名（用predicateObject2） |
| EVENT_UNLINK | 157 | 100% | **保留** | 文件删除 |
| EVENT_CREATE_OBJECT | 126 | 100% | **保留** | 文件创建 |
| EVENT_CONNECT | 67 | 49% | **保留** | 网络连接 |
| EVENT_SENDTO | 21 | 100% | **保留** | 网络发送 |
| EVENT_RECVFROM | 2 | 100% | **保留** | 网络接收 |

**注意：** EVENT_READ 保留但 91% 指向 SrcSinkObject（已丢弃），所以这些边会在提取时自动跳过。实际保留的 READ 边只有约 150,000 条（指向 FileObject 的）。

---

## 五、边方向反转

4种反转（比 CADETS/THEIA 少了 EVENT_EXECUTE）：

| 事件 | 反转后 | 说明 |
|------|--------|------|
| EVENT_READ | file→process | ClearScope 无 EXECUTE |
| EVENT_RECVFROM | netflow→process | |
| EVENT_RECVMSG | netflow→process | |
| EVENT_OPEN | file→process | |

---

## 六、与其他方法的对比

### 6.1 实体提取

| | 我们 | Orthrus | PIDSMaker | KAIROS | CAPTAIN |
|--|------|---------|-----------|--------|---------|
| 进程名 | Subject.cmdLine | Subject.cmdLine | Subject.cmdLine | Event.exec(**0%,失效**) | **不支持** |
| 文件路径 | FileObject.path (100%) | FileObject.path (正则) | 多级fallback | Event.predicateObjectPath(**0.9%**) | **不支持** |
| SrcSinkObject | 丢弃 | 丢弃 | 丢弃 | 丢弃 | — |

### 6.2 边提取

| | 我们 (11种) | Orthrus (10种rel2id) |
|--|---------|---------|
| EVENT_READ | ✅ | ✅ |
| EVENT_WRITE | ✅ | ✅ |
| EVENT_OPEN | ✅ | ✅ |
| EVENT_CONNECT | ✅ | ✅ |
| EVENT_SENDTO | ✅ | ✅ |
| EVENT_RECVFROM | ✅ | ✅ |
| EVENT_SENDMSG | ✅ | ✅ |
| EVENT_RECVMSG | ✅ | ✅ |
| EVENT_UNLINK | ✅ | — |
| EVENT_RENAME | ✅ (predicateObject2) | — |
| EVENT_CREATE_OBJECT | ✅ | — |
| EVENT_EXECUTE | —（不存在） | ✅（不存在） |
| EVENT_CLONE | —（不存在） | ✅（不存在） |

### 6.3 ClearScope 特有：无cmdLine

| 数据集 | EXECUTE边cmdLine | CLONE/FORK边cmdLine | 节点cmdLine |
|--------|-----------------|-------------------|------------|
| CADETS | ✅ Event.cmdLine | ✅ 回填子进程cmdLine | 无 |
| THEIA | ✅ Event.cmdLine | ✅ 回填(fallback Subject.cmdLine) | 无 |
| **ClearScope** | **无EXECUTE** | **无CLONE/FORK** | **无** |

ClearScope 的 event_table.cmdline 列**永远为 NULL**。

---

## 七、用法

### 本地磁盘版

```bash
python extract_clearscope_e3.py \
    --input_dir /mnt/disk/darpa/clearscope_e3 \
    --output_dir /mnt/disk/darpa/clearscope_e3_output
```

### PostgreSQL版

```bash
psql -U postgres -f init_database.sql
python extract_clearscope_e3_postgres.py \
    --input_dir /mnt/disk/darpa/clearscope_e3 \
    --db_name clearscope_e3
```

---

## 八、文件清单

```
cch_repeat/dataprocess/clearscope_e3/
├── README.md                              ← 本文件
├── readme_postgre.md                      ← PostgreSQL方案说明
├── init_database.sql                      ← 数据库创建脚本
├── extract_clearscope_e3.py               ← 提取脚本（本地磁盘版）
├── extract_clearscope_e3_postgres.py      ← 提取脚本（PostgreSQL版）
├── analyze_cdm_structure.py               ← CDM字段分析脚本
├── analyze_all_entity_types.py            ← 实体类型与事件关系分析
└── orthrus_groundtruth/                   ← ground truth
```
