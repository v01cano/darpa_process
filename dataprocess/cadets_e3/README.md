# CADETS E3 数据提取方案设计文档

## 一、数据集概况

CADETS E3 是 DARPA Transparent Computing (TC) 项目 Engagement 3 中的一个数据集，由 CADETS 团队在 FreeBSD 系统上采集。数据格式为 CDM 18 (Common Data Model) JSON 日志，共 10 个文件，约 44,404,339 行记录。

### 1.1 文件列表（按时间顺序）

```
ta1-cadets-e3-official.json         ← 第1卷
ta1-cadets-e3-official.json.1
ta1-cadets-e3-official.json.2
ta1-cadets-e3-official-1.json       ← 第2卷
ta1-cadets-e3-official-1.json.1
ta1-cadets-e3-official-1.json.2
ta1-cadets-e3-official-1.json.3
ta1-cadets-e3-official-1.json.4
ta1-cadets-e3-official-2.json       ← 第3卷
ta1-cadets-e3-official-2.json.1
```

注意：文件必须按上述顺序处理，不能简单使用字典序排列（`sorted()` 会把 `-1.json` 排在 `.json` 前面）。

### 1.2 记录类型分布（以 ta1-cadets-e3-official-2.json 为样本，5,000,000 行）

| 记录类型 | 数量 | 占比 |
|----------|------|------|
| Event | 4,624,270 | 92.5% |
| FileObject | 317,325 | 6.3% |
| Subject | 26,297 | 0.5% |
| SrcSinkObject | 12,952 | 0.3% |
| NetFlowObject | 12,659 | 0.3% |
| UnnamedPipeObject | 6,476 | 0.1% |
| Principal | 20 | <0.01% |
| Host | 1 | <0.01% |

---

## 二、CDM 数据结构分析

### 2.1 Subject 记录（进程）

**子类型分布：**

| 子类型 | 数量 |
|--------|------|
| SUBJECT_PROCESS | 26,297 (100%) |

CADETS E3 中所有 Subject 都是 SUBJECT_PROCESS，不存在 SUBJECT_THREAD / SUBJECT_UNIT 等其他类型。

**字段填充率：**

| 字段 | 填充率 | 说明 |
|------|--------|------|
| `properties.name` | **0.0%** (0/26,297) | CAPTAIN 使用此字段提取进程名 |
| `properties.map.path` | **0.0%** (0/26,297) | PIDSMaker 使用此字段提取进程名 |
| `cmdLine` | **0.0%** (0/26,297) | 全部为 null |
| `cid` (PID) | 100% | 进程 ID |
| `parentSubject` | 99.6% | 父进程 UUID |
| `properties.map.host` | 100% | 主机标识 |

**关键发现：CADETS E3 的 Subject 记录不携带任何名称信息。** `properties.name`、`properties.map.path`、`cmdLine` 填充率均为 0%。唯一有价值的字段是 `uuid`、`cid`（PID）和 `parentSubject`。

### 2.2 FileObject 记录（文件）

**子类型分布：**

| 子类型 | 数量 | 占比 |
|--------|------|------|
| FILE_OBJECT_UNIX_SOCKET | 227,869 | 71.8% |
| FILE_OBJECT_FILE | 89,376 | 28.2% |
| FILE_OBJECT_DIR | 80 | <0.1% |

**字段填充率（所有子类型）：**

| 字段 | 填充率 | 说明 |
|------|--------|------|
| `baseObject.properties.map.path` | **0.0%** | 我们和 CAPTAIN 尝试从此提取文件路径 |
| `baseObject.properties.map.filename` | **0.0%** | PIDSMaker fallback 字段 |
| `baseObject.properties.map.name` | **0.0%** | |
| `type` | 100% | 子类型标识 |
| `baseObject.hostId` | 100% | 主机标识 |

**关键发现：CADETS E3 的 FileObject 记录同样不携带任何路径信息。** `baseObject.properties.map` 为空 dict。

### 2.3 NetFlowObject 记录（网络连接）

| 字段 | 填充率 | 格式 | 样例 |
|------|--------|------|------|
| `localAddress` | 100% | string | `10.0.6.23` |
| `localPort` | 100% | int | `123` |
| `remoteAddress` | 100% | string | `10.0.4.1` |
| `remotePort` | 100% | int | `123` |

NetFlowObject 记录本身携带完整的地址信息，不需要从 Event 中获取。

### 2.4 Event 记录（事件/边）

**关联字段填充率：**

| 字段 | 填充率 | 说明 |
|------|--------|------|
| `subject` | 99.9% (4,619,353/4,624,270) | 事件的发起者（进程 UUID） |
| `predicateObject` | 88.4% (4,088,602) | 事件的目标对象 |
| `predicateObject2` | 5.1% (237,721) | 第二目标（RENAME 等） |
| `predicateObjectPath` | 44.2% (2,046,190) | 目标对象的路径 |
| `predicateObject2Path` | 0.7% (32,524) | 第二目标的路径 |

**properties.map 中的关键字段：**

| 字段 | 出现次数 | 占 Event 比例 | 说明 |
|------|---------|-------------|------|
| `exec` | 4,619,232 | **99.9%** | 发起进程的当前程序名 |
| `cmdLine` | 24,408 | **0.5%** | 仅在 EVENT_EXECUTE 中出现 |
| `host` | 4,619,232 | 99.9% | 主机标识 |
| `ppid` | 4,619,232 | 99.9% | 父进程 PID |
| `fd` | 4,123,527 | 89.2% | 文件描述符 |

**关键发现：**
1. **`exec` 字段出现在 99.9% 的 Event 中**，记录的是发起此事件的进程的"当前程序名"。这是 CADETS E3 中获取进程名的**唯一有效途径**。
2. **`cmdLine` 仅在 EVENT_EXECUTE 中出现**（24,408 条，0.5%），记录的是该次 exec 系统调用的完整命令行。

---

## 三、UUID 生命周期分析

### 3.1 UUID 生成机制

通过对全部 44,404,339 行数据的分析，验证了以下结论：

**UUID 在 fork 时生成，exec 后不变。**

| 指标 | 全数据集统计 |
|------|-------------|
| Subject 记录数 | 224,629 |
| EVENT_FORK 数 | 223,781 |
| 差值（无 fork 记录的 Subject） | 848（系统启动时已存在的进程） |
| PID 被多个 UUID 使用 | 75,688 (75.8%) |

- **每次 fork 创建一条新的 Subject 记录**，Subject 数与 fork 数几乎 1:1
- **PID 大量复用**（75.8%），因此 PID 不能作为节点标识，必须用 UUID
- UUID 在 fork 时分配后**终身不变**，即使进程 exec 了新程序

### 3.2 exec 身份变化

| exec 身份变化次数 | UUID 数量 | 占比 |
|------------------|----------|------|
| 1 次（从未 exec） | 19,981 | 8.9% |
| 2 次（fork 继承 → exec 后） | 200,590 | **89.5%** |
| 3 次（多次 exec） | 3,566 | 1.6% |
| 4 次 | 9 | <0.01% |

**89.5% 的进程经历了 2 次身份变化**：fork 时继承父进程名，随后 exec 变成真实程序。例如：
- `bash` → `date`（fork 继承 bash，exec 成 date）
- `sshd` → `bash`（sshd fork 子进程，子进程 exec 成 bash）

### 3.3 exec 字段的归属

通过逐条分析 26,147 个 fork 事件验证：

```
exec 字段 == 父进程 exec: 100.0%
exec 字段 == 子进程首次 exec: 100.0%
父进程 exec == 子进程首次 exec: 100.0%
```

**exec 字段属于 Event 的 subject（源节点），即发起操作的进程。** fork 时父子相同（因为子进程继承了父进程的内存映像），EXECUTE 之后子进程的 exec 才变成新程序名。

**身份变化的精确时序：**

```
EVENT_CLOSE     exec='bash'     ← fork 后继承父进程身份
EVENT_EXECUTE   exec='bash'     ← EXECUTE 事件本身的 exec 仍是旧名
EVENT_OPEN      exec='date'     ← EXECUTE 之后的第一条事件，exec 已变成新名
```

### 3.4 攻击场景中的 exec 变化

全数据集中发现的攻击相关 exec 转换：

| 转换 | 含义 | 涉及进程 |
|------|------|---------|
| `master` → `vUgefal` | postfix 被注入运行恶意程序 | PID=76833 |
| `sshd` → `pEja72mA` | sshd 被利用执行恶意程序 | PID=36112 |
| `pEja72mA` → `procstat` | 恶意程序收集系统信息 | PID=36170 |
| `sshd` → `main` | sshd 执行 /tmp/main | PID=20521 |
| `sshd` → `test` | sshd 执行 /tmp/test | 多个 PID |
| `inetd` → `XIM` | inetd 被利用执行恶意程序 | PID=20367 |
| `sshd` → `XIM` | sshd 执行 /tmp/XIM | PID=20408 |
| `XIM` → `test` | 恶意程序链式执行 | PID=20708 |

**如果只存最后一个 exec 值（如 Orthrus/PIDSMaker/KAIROS 的做法），攻击来源信息被覆盖。** 但由于 fork 边天然记录了父子关系，来源仍可追溯。

---

## 四、实体过滤决策

### 4.1 FILE_OBJECT_UNIX_SOCKET — 丢弃

| 分析指标 | 结果 |
|----------|------|
| 数量 | 227,869 |
| 被 Event 引用 | 227,587 (99.9%)，涉及 420,830 条 Event |
| 名字获取 | 9,078 个通过 predicateObjectPath 获得名字，**全部为 `<unknown>`** |
| 有真实名字（非`<unknown>`） | **2 / 227,869 (0.001%)** |
| 多个进程参与（真正 IPC） | **7 / 227,869 (0.003%)** |
| 攻击进程使用 | **0**（vUgefal/pEja72mA/test/XIM 均未使用） |
| 事件类型分布 | 92.1% 文件类事件，7.9% 网络类事件 |
| 主要事件 | CLOSE(225,372) + CREATE(107,863) 占 79%，大量噪声 |

**丢弃理由：**
1. 227,869 个匿名节点会让图膨胀 3 倍（file 节点仅 89,376）
2. 99.997% 不是真正的 IPC（仅 7 个 socket 有多个进程参与）
3. 与攻击完全无关
4. 没有可用的名字信息
5. CAPTAIN 同样丢弃此类型

### 4.2 UnnamedPipeObject — 丢弃

| 分析指标 | 结果 |
|----------|------|
| 数量 | 6,476 |
| 被 Event 引用 | 6,476 (100%)，涉及 21,943 条 Event |
| 名字获取 | 4,516 个获得名字，**全部为 `<unknown>`** |
| 真正有读+写的管道 | 3,862 (59.7%) |
| 攻击相关 | `test`(恶意程序) 使用 6 条管道读取 `uname` 输出 |

**丢弃理由：**
1. 四个项目（CAPTAIN/Orthrus/PIDSMaker/KAIROS）**全部丢弃**
2. 名字全为 `<unknown>`
3. 攻击信息（test 运行 uname）通过 fork/exec 链已经可以追溯
4. 管道两端的典型通信对是 `top|head`、`imapd↔mlock` 等正常系统行为

### 4.3 SrcSinkObject — 丢弃

| 分析指标 | 结果 |
|----------|------|
| 数量 | 12,952 |
| 被 Event 引用为 subject | **0** |
| 被 Event 引用为 predicateObject | **0** |
| 被 Event 引用为 predicateObject2 | **0** |

**完全孤立，无任何 Event 引用。** 所有项目均丢弃。

### 4.4 FILE_OBJECT_DIR — 保留（归入 file）

| 分析指标 | 结果 |
|----------|------|
| 数量 | 80 |
| 被 Event 引用 | 79 (98.8%)，涉及 2,656 条 Event |
| 名字获取 | 78 (97.5%)，**真实目录名**如 `/home/henry`、`/var/at/jobs/.` |
| 参与事件 | OPEN(1,306)、CLOSE(1,227)、CREATE(62)、UNLINK(61) |

**保留理由：** 数量极少（80 个），97.5% 有真实名字，参与的事件有意义。归入 `file` 类型。

### 4.5 保留的实体类型（最终）

| 实体类型 | 来源 | 归类 | 数量 |
|----------|------|------|------|
| SUBJECT_PROCESS | Subject 记录 | `process` | 26,297 |
| FILE_OBJECT_FILE | FileObject 记录 | `file` | 89,376 |
| FILE_OBJECT_DIR | FileObject 记录 | `file` | 80 |
| NetFlowObject | NetFlowObject 记录 | `netflow` | 12,659 |
| **总计** | | **3 类** | **128,412** |

---

## 五、事件类型过滤决策

### 5.1 CADETS E3 事件类型完整分布

| 事件类型 | 数量 | 有predObj | 有path | 有exec | 有cmdLine | **决策** |
|----------|------|----------|--------|--------|----------|---------|
| EVENT_READ | 1,418,575 | 100% | 100% | ✓ | | **保留** |
| EVENT_CLOSE | 702,588 | 100% | 0% | ✓ | | **过滤** |
| EVENT_FCNTL | 534,835 | **0.4%** | 0% | ✓ | | **过滤** |
| EVENT_MMAP | 526,854 | 100% | 0% | ✓ | | **过滤** |
| EVENT_LSEEK | 438,421 | 100% | 0% | ✓ | | **过滤** |
| EVENT_OPEN | 427,616 | 100% | 100% | ✓ | | **保留** |
| EVENT_WRITE | 158,997 | 100% | 100% | ✓ | | **保留** |
| EVENT_CREATE_OBJECT | 114,401 | 100% | 0.05% | ✓ | | **保留** |
| EVENT_MODIFY_PROCESS | 106,153 | 100% | 0% | ✓ | | **过滤** |
| EVENT_CHANGE_PRINCIPAL | 41,297 | 100% | 0% | ✓ | | **过滤** |
| EVENT_FORK | 26,194 | 100% | 0% | ✓ | | **保留** |
| EVENT_EXIT | 25,248 | 100% | 0% | ✓ | | **过滤** |
| EVENT_EXECUTE | 24,408 | 100% | 100% | ✓ | ✓ | **保留** |
| EVENT_SENDTO | 16,775 | 100% | 0% | ✓ | | **保留** |
| EVENT_RECVFROM | 15,913 | 100% | 0% | ✓ | | **保留** |
| EVENT_CONNECT | 12,463 | 100% | 0% | ✓ | | **保留** |
| EVENT_UNLINK | 6,629 | 100% | 100% | ✓ | | **保留** |
| EVENT_MODIFY_FILE_ATTRIBUTES | 6,324 | 100% | 96% | ✓ | | **过滤** |
| EVENT_ACCEPT | 6,297 | 100% | 0% | ✓ | | **过滤** |
| EVENT_ADD_OBJECT_ATTRIBUTE | 4,917 | 100% | 0% | | | **过滤** |
| EVENT_LINK | 2,025 | 100% | 100% | ✓ | | **过滤** |
| EVENT_RENAME | 1,807 | 100% | 100% | ✓ | | **保留** |
| EVENT_LOGIN | 1,448 | **0%** | 0% | ✓ | | **过滤** |
| EVENT_MPROTECT | 1,253 | **0%** | 0% | ✓ | | **过滤** |
| EVENT_TRUNCATE | 1,085 | 100% | 0% | ✓ | | **过滤** |
| EVENT_RECVMSG | 785 | 100% | 0% | ✓ | | **保留** |
| EVENT_SENDMSG | 398 | 100% | 0% | ✓ | | **保留** |
| EVENT_SIGNAL | 212 | 89% | 0% | ✓ | | **过滤** |
| EVENT_OTHER | 196 | **1%** | 0% | ✓ | | **过滤** |
| EVENT_FLOWS_TO | 121 | 100% | 0% | | | **过滤** |
| EVENT_BIND | 35 | 100% | 0% | ✓ | | **过滤** |

### 5.2 各事件过滤/保留的详细理由

#### 保留的 13 种事件

| 事件类型 | 保留理由 |
|----------|---------|
| **EVENT_READ** | 核心事件。进程读取文件或网络数据。1,418,575 条，100% 有 predicateObject 和 path。 |
| **EVENT_WRITE** | 核心事件。进程写入文件或网络。158,997 条。 |
| **EVENT_EXECUTE** | 核心事件。进程加载可执行文件，**唯一携带 cmdLine 的事件类型**（24,408 条）。 |
| **EVENT_FORK** | 核心事件。进程创建子进程。26,194 条。我们额外将子进程的 cmdLine 回填到此边上。 |
| **EVENT_OPEN** | 进程打开文件。427,616 条，100% 有 path。 |
| **EVENT_CONNECT** | 进程发起网络连接。12,463 条。 |
| **EVENT_SENDTO** | 进程通过网络发送数据。16,775 条。 |
| **EVENT_RECVFROM** | 进程从网络接收数据。15,913 条。 |
| **EVENT_SENDMSG** | 消息发送。398 条，量少但语义重要。 |
| **EVENT_RECVMSG** | 消息接收。785 条。 |
| **EVENT_RENAME** | 文件重命名。1,807 条。**使用 predicateObject2 作为目标**（其他项目忽略此事件）。 |
| **EVENT_UNLINK** | 文件删除。6,629 条。攻击者可能删除痕迹文件。 |
| **EVENT_CREATE_OBJECT** | 文件创建。114,401 条。攻击者创建恶意文件的记录。 |

#### 过滤的事件及详细理由

| 事件类型 | 数量 | 过滤理由 |
|----------|------|---------|
| **EVENT_CLOSE** | 702,588 | 关闭文件句柄。信息量极低——只说明"不再使用此文件"，不包含数据流信息。KAIROS 保留了此事件，但 Orthrus/PIDSMaker 的 `rel2id` 中未包含。量大（占 Event 的 15.2%）但对检测无贡献。 |
| **EVENT_FCNTL** | 534,835 | 文件控制操作。**99.6% 没有有效的 predicateObject**（仅 2,104 条有），即绝大多数 FCNTL 事件无法构成有效的边。Orthrus/PIDSMaker 明确将其列入 `exclude_edge_type`。 |
| **EVENT_MMAP** | 526,854 | 内存映射。量极大（占 11.4%）但语义模糊——进程将文件映射到内存，通常是程序加载共享库（如 libc.so）的正常行为。所有 mmap 事件的 predicateObjectPath 填充率为 0%，无法获知映射的是什么文件。Orthrus/PIDSMaker 在 `rel2id` 中未包含此事件。 |
| **EVENT_LSEEK** | 438,421 | 文件指针移动。纯粹的文件指针定位操作，不涉及数据读写，对理解信息流无帮助。量大但全是噪声。 |
| **EVENT_MODIFY_PROCESS** | 106,153 | 进程属性修改。predicateObject 是进程自身（subject→subject），含义模糊。不在任何项目的保留列表中。 |
| **EVENT_CHANGE_PRINCIPAL** | 41,297 | setuid/setgid 操作。改变进程的权限身份。理论上与权限提升攻击相关，但本数据集中未发现攻击关联。可作为后续扩展考虑。 |
| **EVENT_EXIT** | 25,248 | 进程退出。predicateObject 是进程自身，仅表示"进程结束"。对信息流分析无贡献。 |
| **EVENT_ACCEPT** | 6,297 | 接受网络连接。Orthrus/PIDSMaker 在反转列表中包含此事件但未放入 `rel2id`。KAIROS 仅反转但未明确包含。语义上可被 RECVFROM 覆盖。 |
| **EVENT_ADD_OBJECT_ATTRIBUTE** | 4,917 | CDM 元数据操作，用于补充发布时不完整的对象属性。不对应真实系统调用。Orthrus/PIDSMaker 明确排除。 |
| **EVENT_LINK** | 2,025 | 硬链接创建。量少，可选保留。当前过滤以减少边类型复杂度。 |
| **EVENT_LOGIN** | 1,448 | 用户登录事件。**predicateObject 填充率为 0%**，无法构成有效边。 |
| **EVENT_MPROTECT** | 1,253 | 内存保护修改。**predicateObject 填充率为 0%**，无法构成有效边。 |
| **EVENT_TRUNCATE** | 1,085 | 文件截断。量少，语义价值有限。 |
| **EVENT_SIGNAL** | 212 | 进程间信号。量极少，predicateObject 填充率仅 89%。 |
| **EVENT_OTHER** | 196 | 未分类事件。**predicateObject 填充率仅 1%**。Orthrus/PIDSMaker 明确排除。 |
| **EVENT_FLOWS_TO** | 121 | CDM 内部标记，不对应真实系统调用。所有项目均排除。 |
| **EVENT_BIND** | 35 | 绑定端口。量极少。 |

---

## 六、边方向反转

### 6.1 反转的目的

CDM 原始方向是**操作发起方向**：`subject(进程) → predicateObject(对象)`。

反转的目的是将边方向统一为**数据流/信息流方向**，使图中的路径自然表达"数据从哪里流向哪里"。

### 6.2 反转规则

| 事件类型 | 原始方向 | 反转后方向 | 数据流语义 |
|----------|---------|-----------|-----------|
| EVENT_READ | 进程→文件 | **文件→进程** | 数据从文件流入进程 |
| EVENT_RECVFROM | 进程→网络 | **网络→进程** | 数据从网络流入进程 |
| EVENT_RECVMSG | 进程→网络 | **网络→进程** | 消息从网络流入进程 |
| EVENT_EXECUTE | 进程→文件 | **文件→进程** | 代码从文件加载进进程 |
| EVENT_OPEN | 进程→文件 | **文件→进程** | 文件句柄流入进程 |
| EVENT_WRITE | 进程→文件 | 不反转 | 数据从进程流向文件 |
| EVENT_SENDTO | 进程→网络 | 不反转 | 数据从进程流向网络 |
| EVENT_SENDMSG | 进程→网络 | 不反转 | 消息从进程流向网络 |
| EVENT_CONNECT | 进程→网络 | 不反转 | 进程发起连接 |
| EVENT_FORK | 父→子 | 不反转 | 父进程创建子进程 |
| EVENT_RENAME | 进程→文件 | 不反转 | 进程重命名文件 |
| EVENT_UNLINK | 进程→文件 | 不反转 | 进程删除文件 |
| EVENT_CREATE_OBJECT | 进程→文件 | 不反转 | 进程创建文件 |

### 6.3 反转后的攻击链示例

```
C2_server ──RECVFROM──> nginx              攻击者发送 exploit
nginx ──WRITE──> /tmp/vUgefal              nginx 写入恶意文件
/tmp/vUgefal ──EXECUTE──> vUgefal(进程B)    恶意文件被加载
sshd(进程A) ──FORK──> vUgefal(进程B)       进程B的来源（fork边）
/etc/passwd ──READ──> vUgefal(进程B)       恶意进程读取密码文件
vUgefal(进程B) ──SENDTO──> C2_server       恶意进程回传数据
```

信息沿箭头方向流动，攻击链清晰可追踪。

### 6.4 与其他项目反转策略的对比

| 事件类型 | 我们(5种) | Orthrus(10种) | PIDSMaker(11种) | KAIROS(3种) |
|----------|---------|---------|-----------|--------|
| EVENT_READ | ✅ | ✅ | ✅ | — |
| EVENT_RECVFROM | ✅ | ✅ | ✅ | ✅ |
| EVENT_RECVMSG | ✅ | ✅ | ✅ | ✅ |
| EVENT_EXECUTE | ✅ | ✅ | ✅ | — |
| EVENT_OPEN | ✅ | ✅ | ✅ | — |
| EVENT_ACCEPT | — | — | ✅ | ✅ |
| EVENT_LSEEK | — | ✅ | ✅ | — |
| EVENT_MMAP | — | ✅ | ✅ | — |
| EVENT_READ_SOCKET_PARAMS | — | ✅ | ✅ | — |
| EVENT_CHECK_FILE_ATTRIBUTES | — | ✅ | ✅ | — |
| READ (OpTC) | — | — | ✅ | — |

我们只反转 5 种核心数据流事件。Orthrus/PIDSMaker 额外反转了 LSEEK/MMAP 等，但这些事件已被我们过滤，故不涉及。

---

## 七、cmdLine 处理

### 7.1 为什么 cmdLine 应该是边属性而非节点属性

| 论据 | 说明 |
|------|------|
| 数据占比 | cmdLine 仅在 EVENT_EXECUTE 中出现（24,408 条，占 0.5%），不是节点的通用属性 |
| 语义 | cmdLine 描述的是"一次执行动作的参数"，不是"进程是什么" |
| 时变性 | 同一个 UUID 可能多次 exec（1.6%），每次 cmdLine 不同 |
| 信息丢失 | 作为节点属性只能保存一个值，覆盖历史（Orthrus/PIDSMaker 的 cmdLine 实际全为 null） |

### 7.2 我们的处理方式

| 边类型 | cmdLine 来源 | 说明 |
|--------|-------------|------|
| EVENT_EXECUTE | `Event.properties.map.cmdLine` | 直接从事件中获取 |
| EVENT_FORK | `uuid_cmdline[子进程UUID]` | 从子进程的第一个 EXECUTE 事件回填 |
| 其他事件 | `None` | 不携带 cmdLine |

**FORK 边回填 cmdLine 的意义：**

```
sshd(A) ──FORK(cmdLine='/tmp/pEja72mA BBBB')──> pEja72mA(B)
```

直接在 fork 边上看到子进程将要执行什么，不需要再查找后续的 EXECUTE 事件。

---

## 八、节点命名策略

### 8.1 进程节点

**使用最终 exec 值命名。**

由于 89.5% 的进程经历 `fork继承名 → exec真实名` 的变化，最终 exec 值就是进程的"真实身份"。例如：
- UUID=0295A18C: exec 变化 `['bash', 'date']` → 节点名为 `date`
- UUID=D3822AFC: exec 变化 `['master', 'vUgefal']` → 节点名为 `vUgefal`

对于 8.9% 从未 exec 的进程（如 python2.7 fork 的 worker），使用继承的名字。

**为什么不用第一个 exec：** 第一个 exec 是 fork 继承的父进程名，不代表该进程的真实身份。

**来源信息不会丢失：** 虽然节点只有最终名字，但 fork 边记录了父子关系，EXECUTE 边记录了身份变换过程。完整的攻击链 `sshd → fork → EXECUTE /tmp/pEja72mA → 恶意行为` 在图结构中完整保留。

### 8.2 文件节点

**使用最后一次 Event.predicateObjectPath 更新的路径。**

由于 FileObject 记录本身路径填充率为 0%，文件名只能从 Event 获取。44.2% 的 Event 携带 predicateObjectPath。76.3% 的 FILE_OBJECT_FILE 最终能获得路径名。

### 8.3 网络节点

**使用 NetFlowObject 记录本身的地址信息。**

格式：`localAddress:localPort->remoteAddress:remotePort`

---

## 九、与其他方法的详细对比

### 9.1 实体提取对比

| 维度 | 我们 | CAPTAIN | Orthrus | PIDSMaker | KAIROS |
|------|------|---------|---------|-----------|--------|
| **Subject 来源** | Subject 记录 | Subject 记录 | **Event 记录** | Subject 记录 | **Event 记录** |
| **Subject 命名字段** | Event.exec 最终值 | Event.exec 动态更新 | Event.exec | Subject.properties.map.path | Event.exec |
| **Subject 命名效果** | **99.9% 有名字** | 99.9% | 99.9% | **0%（全为null）** | 99.9% |
| **File 来源** | FileObject 记录 | FileObject 记录 | **Event 记录** | FileObject 记录 | **Event 记录** |
| **File 命名字段** | Event.predicateObjectPath | Event.predicateObjectPath 动态更新 | Event.predicateObjectPath | FileObject.baseObject 多级 fallback | Event.predicateObjectPath |
| **File 命名效果** | **76.3% 有路径** | 76.3% | 76.3% | **0%（全为null）** | 76.3% |
| **NetFlow 字段** | 完整四元组 | 仅 remote | 完整四元组 | 完整四元组 | 完整四元组 |
| **Hash/ID 方式** | 直接 UUID | 直接 UUID | SHA256(UUID) | SHA256(UUID) | **SHA256(属性值)** |
| **UNIX_SOCKET** | 丢弃 | 丢弃 | →netflow | →file | →file |
| **UnnamedPipe** | 丢弃 | 丢弃 | 丢弃 | 丢弃 | 丢弃 |
| **FILE_OBJECT_DIR** | →file | 丢弃 | — | →file | →file |
| **类型数** | 3 类 | 3 类 | 3 类 | 3 类 | 3 类 |

**关键差异分析：**

1. **Orthrus 和 KAIROS 从 Event 记录提取 Subject/File**：
   - Orthrus `store_subject()` 扫描 Event 行，用正则提取 `"exec":"(.*?)"` 作为 Subject 名
   - Orthrus `store_file()` 扫描 Event 行，用正则提取 `predicateObjectPath` 作为 File 路径
   - KAIROS 同样从 Event 提取，但 **hash 对象是属性值而非 UUID**（`stringtomd5(exec值)` 而非 `stringtomd5(uuid)`），导致同名进程/同路径文件会 hash 冲突
   - 在 CADETS E3 上这种方式碰巧有效（因为实体记录本身没有名称信息）
   - **问题**：没有出现在含 exec 的 Event 中的进程会被完全遗漏

2. **PIDSMaker 从 Subject 记录提取但取错了字段**：
   - 使用 `properties.map.path` 作为进程名，但 CADETS E3 中此字段填充率为 0%
   - 使用 `Subject.cmdLine` 作为命令行，填充率同样为 0%
   - **结果：PIDSMaker 在 CADETS E3 上所有进程名和命令行均为 "null"**
   - 后续的 Word2Vec 嵌入实际上是在对 "null" 字符串做嵌入

3. **我们和 CAPTAIN 的方法最完整**：
   - 从实体记录创建节点（保证 UUID 完整性）
   - 从 Event 更新名称（利用 99.9% 的 exec 和 44.2% 的 predicateObjectPath）
   - CAPTAIN 是单遍流式处理（边解析边更新 node_buffer），我们是两遍处理

### 9.2 边提取对比

| 维度 | 我们(13种) | CAPTAIN(~13种) | Orthrus(10种) | PIDSMaker(10种) | KAIROS(7种) |
|------|-----------|---------|---------|-----------|--------|
| EVENT_READ | ✅ | ✅ | ✅ | ✅ | ✅ |
| EVENT_WRITE | ✅ | ✅ | ✅ | ✅ | ✅ |
| EVENT_EXECUTE | ✅ | ✅ | ✅ | ✅ | ✅ |
| EVENT_FORK/CLONE | ✅(FORK) | ✅(CLONE) | ✅(CLONE) | ✅(CLONE) | — |
| EVENT_OPEN | ✅ | — | ✅ | ✅ | ✅ |
| EVENT_CONNECT | ✅ | — | ✅ | ✅ | — |
| EVENT_SENDTO | ✅ | ✅ | ✅ | ✅ | ✅ |
| EVENT_RECVFROM | ✅ | ✅ | ✅ | ✅ | ✅ |
| EVENT_SENDMSG | ✅ | ✅ | ✅ | ✅ | — |
| EVENT_RECVMSG | ✅ | ✅ | ✅ | ✅ | — |
| EVENT_RENAME | ✅(predObj2) | ✅(predObj2) | ❌ | ❌ | — |
| EVENT_UNLINK | ✅ | ✅ | — | — | — |
| EVENT_CREATE_OBJECT | ✅ | ✅ | — | — | — |
| EVENT_CLOSE | — | — | — | — | ✅ |
| 边方向反转 | 5种 | 无 | 10种 | 11种 | 3种 |
| Subject→Subject 边 | ✅ | ✅ | ✅ | ✅ | ❌ |
| EVENT_RENAME 处理 | predObj2 | predObj2 | 忽略 | 忽略 | 忽略 |

### 9.3 cmdLine 处理对比

| 维度 | 我们 | CAPTAIN | Orthrus | PIDSMaker | KAIROS |
|------|------|---------|---------|-----------|--------|
| **位置** | **边属性** | 边属性 | 节点属性 | 节点属性 | 节点属性 |
| **EXECUTE 边** | ✅ cmdLine | ✅ event.parameters | — | — | — |
| **FORK 边** | **✅ 回填子进程 cmdLine** | — | — | — | — |
| **实际值** | 24,408 条有值 | 同左 | 全 null | 全 null | 从 exec 混淆 |

### 9.4 我们独有的特性总结

1. **FORK 边携带 cmdLine**：预收集子进程的 cmdLine 回填到 fork 边上，直接在 fork 边看到子进程将执行什么
2. **EVENT_RENAME 正确使用 predicateObject2**：仅 CAPTAIN 和我们处理了这个语义
3. **基于数据统计的设计决策**：每个过滤/保留决策都有 5,000,000 行数据的统计支撑
4. **两遍高效处理**：比 Orthrus(4-5遍)/PIDSMaker(4遍)/KAIROS(5遍) 更少的磁盘 I/O
5. **节点命名使用最终 exec**：基于 UUID 生命周期分析（89.5% fork→exec 模式），选择 exec 后的真实身份

---

## 十、提取方案源文件

| 文件 | 说明 |
|------|------|
| `extract_cadets_e3.py` | 最终数据提取脚本 |
| `analyze_cdm_structure.py` | CDM 字段填充率分析脚本 |
| `analyze_uuid_lifecycle.py` | UUID 生命周期分析（单文件） |
| `analyze_uuid_lifecycle_all.py` | UUID 生命周期分析（全文件，含攻击分析） |
| `analyze_fork_exec_ownership.py` | fork/exec 中 exec 字段归属验证 |
| `analyze_all_entity_types.py` | 所有实体类型的 Event 引用分析 |
| `analyze_entity_event_relation.py` | 实体类型与事件类型的对应关系 |
| `analyze_unnamed_pipe.py` | UnnamedPipeObject 使用场景分析 |
| `analyze_unix_socket.py` | UNIX_SOCKET 使用场景分析 |

---

## 十一、用法

```bash
python extract_cadets_e3.py \
    --input_dir /mnt/disk/darpa/cadets_e3 \
    --output_dir ./output_cadets_e3
```

输出文件：
- `uuid2name.pkl`：节点映射 `{uuid → [type, name]}`
- `datalist.pkl`：边列表 `[(timestamp, event_type, src_uuid, src_type, src_name, dst_uuid, dst_type, dst_name, cmdline), ...]`
- `edges.csv`：前 100,000 条边的 CSV 格式（方便查看）
