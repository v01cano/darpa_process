# ClearScope E5 数据提取方案

## 一、数据集概况

- **平台**: Android (HOST_MOBILE)
- **CDM 版本**: 20（E3 是 CDM 18）
- **原始数据**: `/mnt/disk/darpa/clearscope_e5/`
  - `ta1-clearscope-1-e5-official-1.bin.json` (~11 GB)
  - `ta1-clearscope-1-e5-official-1.bin.json.1` (~12 GB)
- **抽样规模**（前 1000 万行）：
  - Subject: 829 个 SUBJECT_PROCESS（无 THREAD）
  - FileObject: 110,377（FILE / DIR）
  - NetFlowObject: 2,265
  - IpcObject: 8,524（**丢弃**）
  - SrcSinkObject: 2,579（**丢弃**）
  - ProvenanceTagNode: 218,578（**丢弃**）
  - Event: 9,656,792
  - 估计全量边数：1 亿+（双 bin.json 共 ~24 GB）

## 二、关键差异（vs ClearScope E3）

| 维度 | E3 (CDM18) | **E5 (CDM20)** |
|------|---|---|
| Schema 命名空间 | `com.bbn.tc.schema.avro.cdm18.*` | `...cdm20.*` |
| Subject.cmdLine | dict (string) | dict (string) ← 同 |
| FileObject 路径 | `properties.map.path` 100% | `properties.map.path` 100% ← 同 |
| **NetFlow 地址** | 直接 string，端口 int | **dict** `{"string": "0.0.0.0"}` / `{"int": 0}` |
| **UUID 大小写** | 小写 | **全大写**（含 `00000000-...` 全零占位） |
| **EXECUTE/FORK/CLONE** | 无 | 极少（EXECUTE=1, FORK=14, CLONE=128 / 1000 万行） |
| **EVENT_LOADLIBRARY** | 无 | 有（21 / 1000 万行） |
| 新增独立 datum | — | `Host`、`PacketSocketObject`、`PrincipalObject` |
| `ProvenanceTagNode` | 少量 | 大量（taint 标签，与图无关） |
| `IpcObject` 顶层 datum | 无 | 有（含 uuid1, uuid2, fd1, fd2） |

## 三、提取设计

### 节点类型（3 种，与 E3 完全一致）

| 节点 | 来源 | name 字段 |
|------|------|-----------|
| `subject` | `SUBJECT_PROCESS` | `cmdLine.string`（Android 包名 / 二进制路径） |
| `file` | `FileObject` (FILE/DIR) | `baseObject.properties.map.path` |
| `netflow` | `NetFlowObject` | `"<la>:<lp>-><ra>:<rp>"`，从 dict 解包 |

**丢弃的实体类型：**
- `ProvenanceTagNode` — TA1 taint 标签，不属于图
- `IpcObject` / `SrcSinkObject` — Binder/IPC 中间对象，与 E3 一致丢弃
- `PacketSocketObject` / `Host` / `PrincipalObject` — 元数据

### 节点命名样例

```
process : "system_server" / "com.android.bluetooth" / "/system/bin/dex2oat" / "kernel"
file    : "/sys/fs/selinux" / "/dev/binder" / "/data/system/users/0/settings_global.xml"
netflow : "0.0.0.0:0->:" (本地监听) / "10.0.0.1:443->8.8.8.8:53"
```

### 边过滤 + 反转

**保留的边类型（15 种）：**

```python
INCLUDE_EDGE_TYPE = {
    # 文件 I/O
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_CREATE_OBJECT',
    # 网络 / IPC
    'EVENT_CONNECT', 'EVENT_SENDTO', 'EVENT_RECVFROM',
    'EVENT_SENDMSG', 'EVENT_RECVMSG',
    # 进程 lineage（量极少但保留）
    'EVENT_EXECUTE', 'EVENT_FORK', 'EVENT_CLONE', 'EVENT_LOADLIBRARY',
}
```

**反转的边类型（让边方向 = 数据流向）：**

```python
EDGE_REVERSED = {
    'EVENT_READ',         # file/binder → process
    'EVENT_RECVFROM',     # netflow/ipc → process
    'EVENT_RECVMSG',
    'EVENT_OPEN',         # file → process
    'EVENT_EXECUTE',      # file → process
    'EVENT_LOADLIBRARY',  # so → process
}
```

**丢弃的高频事件（信息冗余或语义模糊）：**
`EVENT_CLOSE`, `EVENT_OTHER`, `EVENT_MMAP`, `EVENT_CHECK_FILE_ATTRIBUTES`,
`EVENT_LSEEK`, `EVENT_FCNTL`, `EVENT_DUP`, `EVENT_MODIFY_PROCESS`,
`EVENT_SIGNAL`, `EVENT_TRUNCATE`, `EVENT_WRITE_SOCKET_PARAMS`,
`EVENT_READ_SOCKET_PARAMS`, `EVENT_BIND`, `EVENT_MODIFY_FILE_ATTRIBUTES`

### 关键解析细节

```python
# CDM20 namespace
rtype = rtype_full.rsplit('.', 1)[-1]   # 'Subject'
# E3 是 record[full].keys() 取 'com.bbn.tc.schema.avro.cdm18.UUID'
# E5 是                       '...cdm20.UUID'
src = list(datum['subject'].values())[0]  # 通用，与 namespace 无关

# NetFlow 解包
la = datum['localAddress'].get('string', '')   # E3: 直接是 str
lp = datum['localPort'].get('int', '')

# UUID lower（避免 FiveDirections 那种坑）
uid = datum['uuid'].lower()

# Subject.cmdLine 解包（同 E3）
cmd = datum.get('cmdLine')
if isinstance(cmd, dict):
    cmd = cmd.get('string')
```

## 四、与其他方法对比

| 维度 | KAIROS (E5) | Orthrus | CAPTAIN | PIDSMaker | **我们** |
|------|---|---|---|---|---|
| 是否覆盖 E5 ClearScope | ✓ ipynb | 部分 | ✗ | 有 GT | ✓ |
| 节点类型 | 3 (subject/file/netflow) | 3 | — | 3+ipc | **3（同 E3）** |
| 边类型数 | 10 | 11 | — | 13 | **15** |
| Reverse READ | ✓ | ✓ | — | ✓ | ✓ |
| Reverse OPEN | ✗ | ✗ | — | 视情况 | **✓**（沿用 E3） |
| Reverse EXECUTE | ✓ | ✓ | — | ✓ | **✓** |
| EVENT_LOADLIBRARY | 通常忽略 | 忽略 | — | 部分保留 | **✓ 反转** |
| EVENT_CLOSE | 一般丢 | 丢 | — | 丢 | **丢** |
| NetFlow dict 解包 | 用 regex | json | — | json | **json** |
| UUID lower 强制 | 不强制 | 不强制 | — | 不一定 | **强制** |
| ProvenanceTagNode | 丢弃 | 丢弃 | — | 丢弃 | **丢弃** |
| 输出形式 | PostgreSQL | PostgreSQL | pkl | pkl | **本地分片 pkl + PG** |

### KAIROS 实现细节对比

KAIROS 用正则提取（速度快但易丢字段），我们用 `json.loads`（更稳健）：

```python
# KAIROS（regex，丢字段时静默 except pass）
res = re.findall('NetFlowObject":{"uuid":"(.*?)"...', line)
# 我们（json，解 dict 解构清晰）
datum = json.loads(line)['datum'][rtype_full]
la = datum['localAddress'].get('string')
```

KAIROS 边类型选取：`READ/WRITE/OPEN/CLOSE/CONNECT/SENDTO/RECVFROM/SENDMSG/RECVMSG/EXECUTE`
反转：`READ/RECVFROM/RECVMSG`（不反转 OPEN）

我们多保留：`UNLINK/RENAME/CREATE_OBJECT/FORK/CLONE/LOADLIBRARY` —
更利于 lineage 与持久化检测。

## 五、目录结构

```
clearscope_e5/
├── README.md                          ← 本文件
├── readme_postgre.md                  ← PostgreSQL 使用说明
├── analyze_cdm_structure.py           ← 字段填充率分析
├── analyze_uuid_lifecycle.py          ← UUID / FORK / EXECUTE 分析
├── analyze_all_entity_types.py        ← Event 引用实体类型分析
├── extract_clearscope_e5.py           ← 本地分片 pkl 提取
├── extract_clearscope_e5_postgres.py  ← PostgreSQL 提取
├── init_database.sql                  ← PG 表结构
├── verify_ground_truth.py             ← GT 验证（含攻击图）
├── verify_ground_truth_simple.py      ← 简化 GT 验证（不扫边）
└── pidsmaker_groundtruth/             ← PIDSMaker GT CSV（按需放置）
```

## 六、用法

```bash
# 1. 字段分析（确认假设）
python analyze_cdm_structure.py
python analyze_uuid_lifecycle.py
python analyze_all_entity_types.py

# 2. 本地版提取（推荐）
python extract_clearscope_e5.py \
    --input_dir /mnt/disk/darpa/clearscope_e5 \
    --output_dir /mnt/disk/darpa/clearscope_e5_output \
    --batch_size 1   # 双文件，每个 flush 一次 part

# 3. PostgreSQL 版提取
psql -U postgres -f init_database.sql
python extract_clearscope_e5_postgres.py \
    --input_dir /mnt/disk/darpa/clearscope_e5 \
    --db_name clearscope_e5

# 4. GT 验证
python verify_ground_truth_simple.py    # 秒级
python verify_ground_truth.py           # 含完整攻击图（需要扫所有边分片）
```

## 七、下游使用

```python
# 流式迭代（内存友好）
from extract_clearscope_e5 import iter_edges
for edge in iter_edges('/mnt/disk/darpa/clearscope_e5_output'):
    ts, etype, src_uuid, src_type, src_name, dst_uuid, dst_type, dst_name, _ = edge

# 全量加载
from extract_clearscope_e5 import load_all_edges
edges = load_all_edges('/mnt/disk/darpa/clearscope_e5_output')
```

## 八、已知坑 / 注意事项

1. **UUID 大小写**：原始数据全大写（含 `00000000-...` 全零占位），提取时已 `.lower()`。
   下游对接外部 GT 时，**也要 `.lower()` 后再查**（参考 `verify_ground_truth.py`）。

2. **NetFlow 端口/地址解包**：`localAddress` 是 `{"string": "..."}`，`localPort` 是 `{"int": 0}`。
   直接 `str(datum['localAddress'])` 会得到 `{'string': '0.0.0.0'}` 而不是 `'0.0.0.0'` —
   必须用 `.get('string')`。

3. **零 UUID parent**：所有 `parentSubject` 都是全零（kernel 占位），不可用于 fork 链推断。
   只能靠 cmdLine 区分进程。

4. **Subject 极少但 Event 极多**：829 个 Subject 对应 9.6M Events。
   很多 Event 的 subject UUID 在前 1000 万行尚未定义（在后续文件里），
   `skipped_no_node` 计数正常会有几十万条。

5. **ProvenanceTagNode 数量大**：~22 万条 / 1000 万行，必须丢弃，否则会成为图主体。
