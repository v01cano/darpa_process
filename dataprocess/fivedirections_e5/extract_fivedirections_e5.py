"""
FiveDirections E5 数据提取脚本（本地磁盘版）

基于对 1000 万行 CDM20 数据的分析，与 FD E3 / CADETS-THEIA-ClearScope E5 对比，
设计的提取方案。

FD E5 与 FD E3 的关键差异：
  1. CDM 命名空间: cdm20（E3 是 cdm18）
  2. SUBJECT_THREAD 比例 97.6%（E3 ~5%）→ 必须合并 THREAD → 父 PROCESS
     - 97.2% 的 THREAD 可通过 parentSubject 找到 SUBJECT_PROCESS
  3. UUID 全部大写 → 强制 .lower()
  4. Subject.cmdLine 是 dict 格式但仅 2.1% 填充（仅 PROCESS 有）
  5. FORK + EXECUTE 仍是原子模型（695:695 完全 1:1 配对）
  6. EVENT_LOADLIBRARY 28k（大量 DLL 加载）
  7. EVENT_CREATE_THREAD 49k（合并后变自环 → 丢弃）
  8. RegistryKeyObject 12k（key 100% 填充，归 file 节点）
  9. NetFlow 地址是 dict {"string":...} / {"int":...}
 10. 数据量大（双 bin.json ~24GB+）→ 分批输出 edges_part_*.pkl

节点类型（3 种，RegistryKey 归 file）：
  - subject  : SUBJECT_PROCESS (THREAD 已合并)
               name = EVENT_EXECUTE.predicateObjectPath 提取 basename，
                      fallback Subject.cmdLine.string
  - file     : FileObject (FILE/DIR/PEFILE/CHAR) + RegistryKeyObject
               name = Event.predicateObjectPath（文件）/ RegistryKey.key
  - netflow  : NetFlowObject (dict 解包)

边过滤 + 反转（18 种保留，6 种反转）：
  保留: READ, WRITE, OPEN, CREATE_OBJECT, UNLINK, RENAME, MODIFY_FILE_ATTRIBUTES,
        LINK, FORK, EXECUTE, CONNECT, BIND, ACCEPT,
        SENDTO, RECVFROM, SENDMSG, RECVMSG, LOADLIBRARY
  反转: READ, RECVFROM, RECVMSG, OPEN, EXECUTE, LOADLIBRARY (file → process)
  丢弃: CLOSE, CHECK_FILE_ATTRIBUTES, OTHER, FCNTL, EXIT, CREATE_THREAD (合并后自环),
        SIGNAL, LOGIN, LOGOUT, UPDATE

用法：
  python extract_fivedirections_e5.py \\
      --input_dir /mnt/disk/darpa/cch_refine/fivedirections_e5_json \\
      --output_dir /mnt/disk/darpa/fivedirections_e5_output \\
      --batch_size 3
"""

import json
import os
import re
import time
import argparse
import pickle
import glob
from collections import Counter

# ============================================================================
# 配置
# ============================================================================

# FiveDirections E5 完整文件列表（1005 个分片，按 series 顺序）
DEFAULT_FILE_LIST = (
    # ---------- Series 1: 共 367 个 ----------
    [
        'ta1-fivedirections-1-e5-official-1.bin.json',
        'ta1-fivedirections-1-e5-official-1.bin.json.1',
        'ta1-fivedirections-1-e5-official-1.bin.json.2',
    ]
    + [
        f'ta1-fivedirections-1-e5-official-1.bin.{i}.json{suffix}'
        for i in range(1, 122)
        for suffix in ('', '.1', '.2')
    ]
    + ['ta1-fivedirections-1-e5-official-1.bin.122.json']
    # ---------- Series 2: 共 304 个 ----------
    + [
        'ta1-fivedirections-2-e5-official-1.bin.json',
        'ta1-fivedirections-2-e5-official-1.bin.json.1',
        'ta1-fivedirections-2-e5-official-1.bin.json.2',
    ]
    + [
        f'ta1-fivedirections-2-e5-official-1.bin.{i}.json{suffix}'
        for i in range(1, 101)
        for suffix in ('', '.1', '.2')
    ]
    + ['ta1-fivedirections-2-e5-official-1.bin.101.json']
    # ---------- Series 3: 共 334 个 ----------
    + [
        'ta1-fivedirections-3-e5-official-1.bin.json',
        'ta1-fivedirections-3-e5-official-1.bin.json.1',
        'ta1-fivedirections-3-e5-official-1.bin.json.2',
    ]
    + [
        f'ta1-fivedirections-3-e5-official-1.bin.{i}.json{suffix}'
        for i in range(1, 111)
        for suffix in ('', '.1', '.2')
    ]
    + ['ta1-fivedirections-3-e5-official-1.bin.111.json']
)

INCLUDE_EDGE_TYPE = {
    # 文件 I/O
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN', 'EVENT_CREATE_OBJECT',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_MODIFY_FILE_ATTRIBUTES', 'EVENT_LINK',
    # 进程 lineage（原子模型）
    'EVENT_FORK', 'EVENT_EXECUTE',
    # 网络
    'EVENT_CONNECT', 'EVENT_BIND', 'EVENT_ACCEPT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    # Windows DLL
    'EVENT_LOADLIBRARY',
}

EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_OPEN',
    'EVENT_EXECUTE',
    'EVENT_LOADLIBRARY',
}

# 文件节点保留的 FileObject 子类型
FILE_OBJECT_KEEP = {
    'FILE_OBJECT_FILE', 'FILE_OBJECT_DIR',
    'FILE_OBJECT_PEFILE', 'FILE_OBJECT_CHAR',
}


# ============================================================================
# 辅助
# ============================================================================

def norm_uuid(u):
    return u.lower() if isinstance(u, str) else u


def unpack_dict_str(v):
    if isinstance(v, dict):
        return v.get('string', '')
    return v if v is not None else ''


def unpack_dict_int(v):
    if isinstance(v, dict):
        return v.get('int', '')
    return v if v is not None else ''


def get_uuid(ref):
    """从 {"com.bbn.tc.schema.avro.cdm20.UUID": "..."} 取 UUID 并 lower。"""
    if isinstance(ref, dict):
        for v in ref.values():
            if isinstance(v, str):
                return norm_uuid(v)
    return ''


EXE_RE = re.compile(r'([^\\/]+\.exe)$', re.IGNORECASE)


def extract_exe_basename(path):
    """从 Windows 路径提取 .exe 名（如 \\Device\\HarddiskVolume2\\...\\sshd.exe）。"""
    if not path:
        return None
    m = EXE_RE.search(path)
    if m:
        return m.group(1).lower()
    # 退而求其次：取最后一段
    seg = path.replace('/', '\\').split('\\')[-1]
    return seg.lower() if seg else None


def list_input_files(input_dir, override=None):
    if override:
        files = [os.path.join(input_dir, f) for f in override]
    else:
        files = [os.path.join(input_dir, f) for f in DEFAULT_FILE_LIST]
    return [f for f in files if os.path.exists(f)]


# ============================================================================
# Pass 1: 实体收集 + thread→process map + 名称收集
# ============================================================================

def pass1_collect(input_files):
    """
    单遍扫描，同时完成：
      1. 收集 SUBJECT_PROCESS → uuid2name['process']
      2. 收集 SUBJECT_THREAD.parentSubject → thread_to_process map
      3. 收集 FileObject / RegistryKeyObject → uuid2name['file']
      4. 收集 NetFlowObject → uuid2name['netflow']
      5. 用 Subject.cmdLine.string 给进程初步命名
      6. 遇到 EVENT_EXECUTE：从 predicateObjectPath 提取 .exe basename
         覆盖该 subject 的进程名（更可靠）
      7. 用 Event.predicateObjectPath 给 file 节点命名（首次出现就记录）
    """
    uuid2name = {}            # uuid → [type, name]
    thread_to_process = {}    # thread uuid → parent process uuid
    netflow_info = {}         # uuid → (la, lp, ra, rp)
    registry_keys = {}        # uuid → key (RegistryKey 当 file 路径)

    loaded_line = 0
    begin = time.time()
    n_thread_seen = 0
    n_thread_with_process_parent = 0

    for vidx, vp in enumerate(input_files):
        print(f"  Pass1 [{vidx+1}/{len(input_files)}]: {os.path.basename(vp)}")
        with open(vp, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... "
                          f"process={sum(1 for v in uuid2name.values() if v[0]=='process'):,} "
                          f"thread_map={len(thread_to_process):,}")

                try:
                    record = json.loads(line)['datum']
                except Exception:
                    continue
                if not isinstance(record, dict) or not record:
                    continue
                rtype_full = next(iter(record.keys()))
                datum = record[rtype_full]
                if not isinstance(datum, dict):
                    continue
                rtype = rtype_full.rsplit('.', 1)[-1]

                # ---------- Subject ----------
                if rtype == 'Subject':
                    stype = datum.get('type')
                    uid = norm_uuid(datum.get('uuid'))
                    if not uid:
                        continue

                    if stype == 'SUBJECT_PROCESS':
                        # 命名初值：Subject.cmdLine.string
                        cmd = datum.get('cmdLine')
                        cmd_val = None
                        if isinstance(cmd, dict):
                            cmd_val = cmd.get('string')
                        elif isinstance(cmd, str):
                            cmd_val = cmd
                        # 用 cmdLine 提取 exe 名作为初步 name
                        name = extract_exe_basename(cmd_val) if cmd_val else None
                        if uid not in uuid2name:
                            uuid2name[uid] = ['process', name]
                        elif uuid2name[uid][1] is None and name:
                            uuid2name[uid][1] = name

                    elif stype == 'SUBJECT_THREAD':
                        n_thread_seen += 1
                        parent = datum.get('parentSubject')
                        if isinstance(parent, dict):
                            pu = norm_uuid(get_uuid(parent))
                            if pu:
                                thread_to_process[uid] = pu

                # ---------- FileObject ----------
                elif rtype == 'FileObject':
                    ftype = datum.get('type', '')
                    if ftype in FILE_OBJECT_KEEP:
                        uid = norm_uuid(datum.get('uuid'))
                        if uid and uid not in uuid2name:
                            uuid2name[uid] = ['file', None]

                # ---------- RegistryKeyObject → file ----------
                elif rtype == 'RegistryKeyObject':
                    uid = norm_uuid(datum.get('uuid'))
                    if not uid:
                        continue
                    key = datum.get('key')
                    if isinstance(key, dict):
                        key = key.get('string', '')
                    uuid2name[uid] = ['file', key if key else None]
                    registry_keys[uid] = key

                # ---------- NetFlowObject ----------
                elif rtype == 'NetFlowObject':
                    uid = norm_uuid(datum.get('uuid'))
                    if not uid:
                        continue
                    la = unpack_dict_str(datum.get('localAddress'))
                    lp = unpack_dict_int(datum.get('localPort'))
                    ra = unpack_dict_str(datum.get('remoteAddress'))
                    rp = unpack_dict_int(datum.get('remotePort'))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (str(la), str(lp), str(ra), str(rp))

                # ---------- Event ----------
                elif rtype == 'Event':
                    eventtype = datum.get('type', '')
                    src = get_uuid(datum.get('subject'))
                    dst = get_uuid(datum.get('predicateObject'))
                    pop = datum.get('predicateObjectPath')
                    pop_str = pop.get('string') if isinstance(pop, dict) else None

                    # 1) EVENT_EXECUTE 用 predObjPath 给进程命名（更可靠）
                    if eventtype == 'EVENT_EXECUTE' and src and pop_str:
                        # FD 中 EXECUTE 的 subject 是 PROCESS（实测 695/695）
                        # 取 path 的 .exe basename
                        exe = extract_exe_basename(pop_str)
                        if exe and src in uuid2name and uuid2name[src][0] == 'process':
                            uuid2name[src][1] = exe

                    # 2) EVENT_FORK 的 predObjPath 是子进程的 exe 路径
                    #    给 dst (子进程) 命名
                    if eventtype == 'EVENT_FORK' and dst and pop_str:
                        exe = extract_exe_basename(pop_str)
                        if exe and dst in uuid2name and uuid2name[dst][0] == 'process':
                            if uuid2name[dst][1] is None:
                                uuid2name[dst][1] = exe
                        elif exe and dst not in uuid2name:
                            uuid2name[dst] = ['process', exe]

                    # 3) predicateObjectPath 给 file/registry 节点命名
                    if pop_str and dst and dst in uuid2name and uuid2name[dst][0] == 'file':
                        if uuid2name[dst][1] is None:
                            uuid2name[dst][1] = pop_str

                    # 4) predicateObject2Path 同理（RENAME 等）
                    po2p = datum.get('predicateObject2Path')
                    po2p_str = po2p.get('string') if isinstance(po2p, dict) else None
                    dst2 = get_uuid(datum.get('predicateObject2'))
                    if po2p_str and dst2 and dst2 in uuid2name and uuid2name[dst2][0] == 'file':
                        if uuid2name[dst2][1] is None:
                            uuid2name[dst2][1] = po2p_str

    # 验证 thread → process 映射有效性
    process_uuids = {u for u, v in uuid2name.items() if v[0] == 'process'}
    for t, p in thread_to_process.items():
        if p in process_uuids:
            n_thread_with_process_parent += 1

    elapsed = time.time() - begin
    print(f"\n  Pass1 完成: {loaded_line:,} 行, {elapsed:.1f}s")
    print(f"  THREAD UUIDs:           {n_thread_seen:,}")
    print(f"  thread_to_process map:  {len(thread_to_process):,}")
    print(f"  其中 parent ∈ PROCESS:  {n_thread_with_process_parent:,} "
          f"({n_thread_with_process_parent/max(len(thread_to_process),1)*100:.1f}%)")

    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")

    return uuid2name, thread_to_process, netflow_info, registry_keys


# ============================================================================
# Pass 2: 边提取（thread UUID 替换为 process UUID）
# ============================================================================

def pass2_extract_edges(input_files, uuid2name, thread_to_process,
                        output_dir, batch_size_files):
    """
    Pass2 扫描 Event。THREAD UUID 在 src/dst 中替换为父 PROCESS UUID。
    EVENT_CREATE_THREAD 在替换后变自环，直接跳过。
    """
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0
    skipped_self_loop = 0

    cur_batch = []
    part_idx = 0
    files_in_batch = 0
    edges_part_paths = []

    def flush_batch():
        nonlocal cur_batch, part_idx, files_in_batch
        if not cur_batch:
            return
        path = os.path.join(output_dir, f'edges_part_{part_idx:03d}.pkl')
        with open(path, 'wb') as f:
            pickle.dump(cur_batch, f)
        edges_part_paths.append(path)
        print(f"  [flush] edges_part_{part_idx:03d}.pkl ({len(cur_batch):,} 条)")
        cur_batch = []
        part_idx += 1
        files_in_batch = 0

    def resolve(uid):
        """THREAD → parent PROCESS。"""
        if uid in thread_to_process:
            return thread_to_process[uid]
        return uid

    loaded_line = 0
    begin = time.time()

    for vidx, vp in enumerate(input_files):
        print(f"  Pass2 [{vidx+1}/{len(input_files)}]: {os.path.basename(vp)}")
        with open(vp, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... edges_buf={len(cur_batch):,}")
                try:
                    record = json.loads(line)['datum']
                except Exception:
                    continue
                if not isinstance(record, dict) or not record:
                    continue
                rtype_full = next(iter(record.keys()))
                datum = record[rtype_full]
                if not isinstance(datum, dict):
                    continue
                if rtype_full.rsplit('.', 1)[-1] != 'Event':
                    continue

                eventtype = datum.get('type', '')
                if eventtype not in INCLUDE_EDGE_TYPE:
                    skipped_filtered += 1
                    continue

                eventtime = datum.get('timestampNanos', 0)
                src = get_uuid(datum.get('subject'))
                dst = get_uuid(datum.get('predicateObject'))
                dst2 = get_uuid(datum.get('predicateObject2'))

                # THREAD → PROCESS 替换
                src = resolve(src)
                dst = resolve(dst)
                dst2 = resolve(dst2)

                # RENAME 用 dst2 为目标
                actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst

                if not src or not actual_dst:
                    skipped_no_node += 1
                    continue
                if src not in uuid2name or actual_dst not in uuid2name:
                    skipped_no_node += 1
                    continue

                # 自环（THREAD 合并后可能出现，如 CREATE_THREAD 已丢，但其他事件可能）
                if src == actual_dst:
                    skipped_self_loop += 1
                    continue

                # 反转
                if eventtype in EDGE_REVERSED:
                    edge_src, edge_dst = actual_dst, src
                else:
                    edge_src, edge_dst = src, actual_dst

                # cmdLine: FORK/EXECUTE 用 predObjPath 作为信息
                cmdline = None
                pop = datum.get('predicateObjectPath')
                if isinstance(pop, dict):
                    pop_str = pop.get('string', '')
                    if eventtype in ('EVENT_FORK', 'EVENT_EXECUTE') and pop_str:
                        cmdline = pop_str

                cur_batch.append((
                    eventtime,
                    eventtype,
                    edge_src,
                    uuid2name[edge_src][0],
                    uuid2name[edge_src][1],
                    edge_dst,
                    uuid2name[edge_dst][0],
                    uuid2name[edge_dst][1],
                    cmdline,
                ))
                edge_type_count[eventtype] += 1

        files_in_batch += 1
        if files_in_batch >= batch_size_files:
            flush_batch()

    flush_batch()

    elapsed = time.time() - begin
    print(f"\n  Pass2 完成: {loaded_line:,} 行, {elapsed:.1f}s")
    total_edges = sum(edge_type_count.values())
    print(f"  提取的边: {total_edges:,} （分 {len(edges_part_paths)} 个 part）")
    print(f"  过滤(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过(节点不存在):   {skipped_no_node:,}")
    print(f"  跳过(自环):         {skipped_self_loop:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return edges_part_paths


# ============================================================================
# 下游辅助
# ============================================================================

def iter_edges(output_dir):
    for path in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(path, 'rb') as f:
            edges = pickle.load(f)
        for e in edges:
            yield e


def load_all_edges(output_dir):
    edges = []
    for path in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(path, 'rb') as f:
            edges.extend(pickle.load(f))
    return edges


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("FiveDirections E5 数据提取（本地版）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    print(f"批大小:   {args.batch_size}")
    os.makedirs(args.output_dir, exist_ok=True)

    input_files = list_input_files(args.input_dir)
    print(f"\n输入文件 ({len(input_files)} 个):")
    for f in input_files:
        print(f"  {os.path.basename(f)}")

    print(f"\n{'='*60}\nPass 1: 实体 + thread map + 名称\n{'='*60}")
    uuid2name, thread_to_process, netflow_info, registry_keys = pass1_collect(input_files)

    print(f"\n{'='*60}\nPass 2: 边提取（THREAD → PROCESS 替换）\n{'='*60}")
    pass2_extract_edges(input_files, uuid2name, thread_to_process,
                        args.output_dir, args.batch_size)

    print(f"\n{'='*60}\n保存元数据\n{'='*60}")
    with open(os.path.join(args.output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name.pkl ({len(uuid2name):,})")
    with open(os.path.join(args.output_dir, 'thread_to_process.pkl'), 'wb') as f:
        pickle.dump(thread_to_process, f)
    print(f"  thread_to_process.pkl ({len(thread_to_process):,})")
    with open(os.path.join(args.output_dir, 'netflow_info.pkl'), 'wb') as f:
        pickle.dump(netflow_info, f)
    print(f"  netflow_info.pkl ({len(netflow_info):,})")
    with open(os.path.join(args.output_dir, 'registry_keys.pkl'), 'wb') as f:
        pickle.dump(registry_keys, f)
    print(f"  registry_keys.pkl ({len(registry_keys):,})")

    csv_path = os.path.join(args.output_dir, 'edges.csv')
    with open(csv_path, 'w') as f:
        f.write('timestamp,event_type,src_uuid,src_type,src_name,dst_uuid,dst_type,dst_name,cmdline\n')
        cnt = 0
        for row in iter_edges(args.output_dir):
            fields = [str(x) if x is not None else '' for x in row]
            fields = [field.replace(',', ';') for field in fields]
            f.write(','.join(fields) + '\n')
            cnt += 1
            if cnt >= 100000:
                break
    print(f"  edges.csv (前 10 万条)")

    print(f"\n{'='*60}\n完成\n{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FiveDirections E5 Data Extractor")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/cch_refine/fivedirections_e5_json")
    parser.add_argument("--output_dir", type=str,
                        default="/mnt/disk/darpa/fivedirections_e5_output")
    parser.add_argument("--batch_size", type=int, default=3,
                        help="每多少个输入文件 flush 一次 edges_part_*.pkl")
    args = parser.parse_args()
    main(args)
