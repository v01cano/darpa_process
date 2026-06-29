"""
TRACE E5 数据提取脚本（本地磁盘版）

基于对 1000 万行 CDM20 数据的分析，与 TRACE E3 / THEIA E5 / CADETS E5 对比设计。

TRACE E5 与 TRACE E3 的关键差异：
  1. CDM 命名空间: cdm20（E3 是 cdm18）
  2. Subject.path 从高填充变为 0%
     → 改用 Subject.properties.map.name (100% 填充, Linux 进程短名, 15字符上限)
     → fallback Subject.cmdLine.string (99.5%, 提取第一个 token basename)
     → 实测 name 120 distinct vs cmdLine 419 distinct，name 更适合聚类
     → 完整 cmdLine 保留在 uuid_cmdline.pkl，用于 FORK/EXECUTE 边的 cmdline 属性
  3. FileObject.filename 从高填充变为 0%
     → 改用 baseObject.properties.map.path (100%)
  4. NetFlow 地址变为 dict {"string": "..."} / {"int": n}
  5. UUID 全部大写 → 强制 .lower()
  6. **EXECUTE 模型变了**：
     - TRACE E3: exec 后造新 UUID（14753/14754 未定义）
     - TRACE E5: fork+exec 分离（100% subject 已定义为 PROCESS）
     - → 与 THEIA E5 / CADETS E5 处理一致，不再需要 EXECUTE 身份切换边
  7. SUBJECT_UNIT 仍占 83.7%（175k / 209k），丢弃
  8. EVENT_UNIT (175k) / UnitDependency (138k) 丢弃
  9. MemoryObject 1.2M / SrcSinkObject 591k / IpcObject 38k 全部丢弃
 10. 数据量可能极大（TRACE E3 是 300GB+211 文件，E5 可能更大）→ 分批输出

节点类型（3 种，统一）：
  - subject  : SUBJECT_PROCESS (丢弃 UNIT)
               name = Subject.cmdLine.string → fallback name
  - file     : FileObject (FILE/DIR/LINK), name = baseObject.props.map.path
  - netflow  : NetFlowObject (dict 解包)

边过滤 + 反转（14 种保留，6 种反转）：
  保留: READ, WRITE, OPEN, CREATE_OBJECT, UNLINK, RENAME,
        CONNECT, ACCEPT, SENDMSG, RECVMSG,
        FORK, CLONE, EXECUTE, LOADLIBRARY
  反转: READ, RECVMSG, OPEN, ACCEPT, EXECUTE, LOADLIBRARY
  丢弃: CLOSE/MMAP/MPROTECT/EXIT/SIGNAL/UNIT/TRUNCATE/MODIFY_FILE_ATTRIBUTES/
        UPDATE/LINK/CHANGE_PRINCIPAL

用法：
  python extract_trace_e5.py \\
      --input_dir /mnt/disk1/darpa_e5/trace \\
      --output_dir /mnt/disk1/darpa_e5/trace_output \\
      --batch_size 10
"""

import json
import os
import time
import argparse
import pickle
import glob
from collections import Counter

# ============================================================================
# 配置
# ============================================================================

# TRACE E5 完整文件列表（1661 个 json 分片，按 series 顺序）
# 3 个 series (1/2/3)，每个 series:
#   - bin.json / bin.json.1 / bin.json.2  (主文件 + 2 个续段)
#   - bin.{i}.json / bin.{i}.json.1 / bin.{i}.json.2  for i in 1..N
# Series 1: bin.N 1~184  -> 554 files
# Series 2: bin.N 1~190  -> 572 files
# Series 3: bin.N 1~178  -> 535 files
# 实际产生 1665 个，其中 4 个不存在（list_input_files 会过滤）
_SERIES_MAX = {1: 184, 2: 190, 3: 178}


def _gen_file_list():
    files = []
    for s in (1, 2, 3):
        base = f'ta1-trace-{s}-e5-official-1.bin'
        # 主 bin.json / .1 / .2（时间最早）
        files.append(f'{base}.json')
        files.append(f'{base}.json.1')
        files.append(f'{base}.json.2')
        # bin.1.json ~ bin.N.json，每个 + .1 / .2
        for i in range(1, _SERIES_MAX[s] + 1):
            files.append(f'{base}.{i}.json')
            files.append(f'{base}.{i}.json.1')
            files.append(f'{base}.{i}.json.2')
    return files


DEFAULT_FILE_LIST = _gen_file_list()

INCLUDE_EDGE_TYPE = {
    # 文件 I/O
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN',
    'EVENT_CREATE_OBJECT', 'EVENT_UNLINK', 'EVENT_RENAME',
    # 网络 / IPC
    'EVENT_CONNECT', 'EVENT_ACCEPT',
    'EVENT_SENDMSG', 'EVENT_RECVMSG',
    # 进程 lineage（标准 fork+exec 分离）
    'EVENT_FORK', 'EVENT_CLONE', 'EVENT_EXECUTE',
    # DLL
    'EVENT_LOADLIBRARY',
}

EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVMSG',
    'EVENT_OPEN',
    'EVENT_ACCEPT',
    'EVENT_EXECUTE',
    'EVENT_LOADLIBRARY',
}

FILE_OBJECT_KEEP = {
    'FILE_OBJECT_FILE', 'FILE_OBJECT_DIR', 'FILE_OBJECT_LINK',
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


def list_input_files(input_dir, override=None):
    """按 DEFAULT_FILE_LIST 顺序生成路径，过滤掉不存在的（少数缺失的分片）。"""
    if override:
        files = [os.path.join(input_dir, f) for f in override]
    else:
        files = [os.path.join(input_dir, f) for f in DEFAULT_FILE_LIST]
    existing = [f for f in files if os.path.exists(f)]
    missing_cnt = len(files) - len(existing)
    if missing_cnt:
        print(f"  [INFO] {missing_cnt} 个文件不存在，已跳过")
    return existing


# ============================================================================
# Pass 1: 实体收集 + 进程名 + cmdLine
# ============================================================================

def pass1_collect(input_files):
    """单遍扫描，收集：
       1. SUBJECT_PROCESS → uuid2name['process']
          name = Subject.cmdLine.string → fallback Subject.props.name
       2. FileObject (FILE/DIR/LINK) → uuid2name['file']
          name = baseObject.props.map.path
       3. NetFlowObject → uuid2name['netflow']
       4. uuid_cmdline = Subject.cmdLine.string（用于 FORK 边回填）
       5. SUBJECT_UNIT 丢弃
       6. EVENT_EXECUTE 的 predicateObjectPath 可能空，但 subject 都是已定义 PROCESS
          → 不需要 EXECUTE 更新进程名
    """
    uuid2name = {}            # uuid (lower) → [type, name]
    uuid_cmdline = {}         # process uuid → cmdLine string
    netflow_info = {}         # uuid → (la, lp, ra, rp)

    loaded_line = 0
    begin = time.time()

    for vidx, vp in enumerate(input_files):
        print(f"  Pass1 [{vidx+1}/{len(input_files)}]: {os.path.basename(vp)}")
        with open(vp, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    p_cnt = sum(1 for v in uuid2name.values() if v[0] == 'process')
                    print(f"    已扫描 {loaded_line:,} 行... "
                          f"process={p_cnt:,} files={sum(1 for v in uuid2name.values() if v[0] == 'file'):,} "
                          f"net={sum(1 for v in uuid2name.values() if v[0] == 'netflow'):,}")

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

                if rtype == 'Subject':
                    stype = datum.get('type')
                    if stype != 'SUBJECT_PROCESS':
                        continue   # 丢弃 UNIT / THREAD
                    uid = norm_uuid(datum.get('uuid'))
                    if not uid:
                        continue
                    # 进程名: Subject.props.map.name 优先（100% 填充，15字符 Linux 短名，
                    #         聚类粒度合适：120 distinct 名 vs cmdLine 419 distinct）
                    name_val = None
                    props = datum.get('properties') or {}
                    pmap = props.get('map') if isinstance(props, dict) else None
                    if isinstance(pmap, dict):
                        name_val = pmap.get('name')

                    # cmdLine 仅用于 FORK / EXECUTE 边的 cmdline 属性（详情保留）
                    cmd = datum.get('cmdLine')
                    cmd_val = None
                    if isinstance(cmd, dict):
                        cmd_val = cmd.get('string')
                    elif isinstance(cmd, str):
                        cmd_val = cmd

                    # 节点 name：name 主 + cmdLine 提取 basename fallback
                    final_name = name_val
                    if not final_name and cmd_val:
                        # 从 cmdLine 第一个 token 提取 basename
                        first_token = cmd_val.split()[0] if cmd_val.split() else cmd_val
                        final_name = os.path.basename(first_token.strip('"'))

                    uuid2name[uid] = ['process', final_name]
                    if cmd_val:
                        uuid_cmdline[uid] = cmd_val

                elif rtype == 'FileObject':
                    ftype = datum.get('type', '')
                    if ftype not in FILE_OBJECT_KEEP:
                        continue
                    uid = norm_uuid(datum.get('uuid'))
                    if not uid:
                        continue
                    base = datum.get('baseObject') or {}
                    base_props = base.get('properties') if isinstance(base, dict) else None
                    base_map = base_props.get('map') if isinstance(base_props, dict) else None
                    path = base_map.get('path') if isinstance(base_map, dict) else None
                    uuid2name[uid] = ['file', path]

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

                # 其他 (Memory/Ipc/SrcSink/UnitDependency) → 丢弃

    elapsed = time.time() - begin
    print(f"\n  Pass1 完成: {loaded_line:,} 行, {elapsed:.1f}s")

    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")
    print(f"  有 cmdLine 的进程: {len(uuid_cmdline):,}")
    return uuid2name, uuid_cmdline, netflow_info


# ============================================================================
# Pass 2: 边提取（分批输出）
# ============================================================================

def pass2_extract_edges(input_files, uuid2name, uuid_cmdline,
                        output_dir, batch_size_files):
    """边提取，按文件数 flush edges_part_*.pkl。
    cmdLine 处理：
      - EXECUTE: 用 uuid_cmdline[subject]（标准 fork+exec 分离，subject 是 exec 后的进程）
      - FORK/CLONE: 用 uuid_cmdline[predObj=child]
    """
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0

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

                # RENAME 用 predicateObject2 为目标
                actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst

                if not src or not actual_dst:
                    skipped_no_node += 1
                    continue
                if src not in uuid2name or actual_dst not in uuid2name:
                    skipped_no_node += 1
                    continue

                # cmdLine: EXECUTE 用 subject 的，FORK/CLONE 用 child 的
                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    cmdline = uuid_cmdline.get(src)
                elif eventtype in ('EVENT_FORK', 'EVENT_CLONE'):
                    cmdline = uuid_cmdline.get(dst)

                # 反转
                if eventtype in EDGE_REVERSED:
                    edge_src, edge_dst = actual_dst, src
                else:
                    edge_src, edge_dst = src, actual_dst

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

def save_pass1_state(output_dir, uuid2name, uuid_cmdline, netflow_info):
    """Pass1 完成后立即落盘，避免 Pass2 崩溃后丢失成果。"""
    print(f"\n{'='*60}\n保存 Pass1 中间结果\n{'='*60}")
    paths = [
        ('uuid2name.pkl', uuid2name),
        ('uuid_cmdline.pkl', uuid_cmdline),
        ('netflow_info.pkl', netflow_info),
    ]
    for fname, obj in paths:
        full = os.path.join(output_dir, fname)
        # 先写 .tmp 再 rename，防止写一半中断导致 pkl 损坏
        tmp = full + '.tmp'
        with open(tmp, 'wb') as f:
            pickle.dump(obj, f)
        os.replace(tmp, full)
        print(f"  {fname} ({len(obj):,})")
    print(f"  → Pass2 若崩溃，可用 --skip_pass1 重启续跑")


def load_pass1_state(output_dir):
    """从已有 pkl 加载 Pass1 状态（用于 --skip_pass1）。"""
    print(f"\n{'='*60}\n载入已有 Pass1 中间结果\n{'='*60}")
    paths = ['uuid2name.pkl', 'uuid_cmdline.pkl', 'netflow_info.pkl']
    out = []
    for fname in paths:
        full = os.path.join(output_dir, fname)
        if not os.path.exists(full):
            raise FileNotFoundError(f"缺少 {full}，请先跑完一次 Pass1")
        with open(full, 'rb') as f:
            obj = pickle.load(f)
        print(f"  载入 {fname} ({len(obj):,})")
        out.append(obj)
    return out[0], out[1], out[2]


def main(args):
    print("=" * 60)
    print("TRACE E5 数据提取（本地版）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    print(f"批大小:   {args.batch_size}")
    print(f"跳过Pass1: {args.skip_pass1}")
    os.makedirs(args.output_dir, exist_ok=True)

    input_files = list_input_files(args.input_dir)
    print(f"\n输入文件 ({len(input_files)} 个):")
    if len(input_files) <= 10:
        for f in input_files:
            print(f"  {os.path.basename(f)}")
    else:
        for f in input_files[:3]:
            print(f"  {os.path.basename(f)}")
        print(f"  ... ({len(input_files)-6} 个省略)")
        for f in input_files[-3:]:
            print(f"  {os.path.basename(f)}")

    if args.skip_pass1:
        # 直接载入之前 Pass1 的成果
        uuid2name, uuid_cmdline, netflow_info = load_pass1_state(args.output_dir)
    else:
        print(f"\n{'='*60}\nPass 1: 实体 + 进程名 + cmdLine\n{'='*60}")
        uuid2name, uuid_cmdline, netflow_info = pass1_collect(input_files)
        # 立即落盘，防止 Pass2 崩溃后丢失
        save_pass1_state(args.output_dir, uuid2name, uuid_cmdline, netflow_info)

    print(f"\n{'='*60}\nPass 2: 边提取\n{'='*60}")
    pass2_extract_edges(input_files, uuid2name, uuid_cmdline,
                        args.output_dir, args.batch_size)

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
    parser = argparse.ArgumentParser(description="TRACE E5 Data Extractor")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk1/darpa_e5/trace")
    parser.add_argument("--output_dir", type=str,
                        default="/mnt/disk1/darpa_e5/trace_output")
    parser.add_argument("--batch_size", type=int, default=10,
                        help="每多少个输入文件 flush 一次 edges_part_*.pkl")
    parser.add_argument("--skip_pass1", action='store_true',
                        help="跳过 Pass1，直接从 output_dir 载入已有 uuid2name.pkl 等"
                             "做 Pass2（用于 Pass2 中途崩溃后续跑）")
    args = parser.parse_args()
    main(args)
