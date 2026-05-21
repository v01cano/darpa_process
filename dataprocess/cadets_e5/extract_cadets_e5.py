"""
CADETS E5 数据提取脚本（本地磁盘版）

基于对 1000 万行原始 CDM20 数据的统计分析，与 CADETS E3 对比，设计的提取方案。

CADETS E5 与 E3 的关键差异：
  1. CDM 命名空间: cdm20（E3 是 cdm18）
  2. NetFlow 地址是 dict {"string":"..."} / {"int": n}（E3 直接 string/int）
  3. UUID 全部大写（含 00000000-... 全零占位）→ 提取时统一 .lower()
  4. ProvenanceTagNode 合并进 EVENT_FLOWS_TO（246k 条 / 1000 万行），建议丢弃
  5. 新增 EVENT_MODIFY_PROCESS（1.5M 条），含义模糊，丢弃
  6. EVENT_FORK 9.2k / EVENT_EXECUTE 9.2k —— 仍是标准 fork+exec 分离模型
     EXECUTE 中 8898 个独立 subject UUID 100% 已定义为 SUBJECT_PROCESS
     → 与 CADETS E3 完全一致，**不**是 TRACE 风格新 UUID
  7. Subject / FileObject 字段仍然全空 → 沿用 CADETS E3 策略：
     - 进程名 = Event.exec（97.5% 覆盖）
     - 文件路径 = Event.predicateObjectPath（17.6%，主要在 OPEN/EXECUTE/RENAME 等）
  8. EVENT_EXECUTE 的 cmdLine 在 properties.map.cmdLine（9218/9218 = 100%）

节点类型（与 E3 一致，3 种）：
  - subject  : SUBJECT_PROCESS, name = Event.exec
  - file     : FileObject (FILE/DIR), name = Event.predicateObjectPath
  - netflow  : NetFlowObject (dict 解包)

边过滤 + 反转（与 CADETS E3 完全一致 13 种）：
  保留: READ, WRITE, EXECUTE, FORK, OPEN, CONNECT, SENDTO, RECVFROM,
        SENDMSG, RECVMSG, RENAME, UNLINK, CREATE_OBJECT
  反转: READ, RECVFROM, RECVMSG, EXECUTE, OPEN

用法：
  python extract_cadets_e5.py \\
      --input_dir /mnt/disk/darpa/cch_refine/cadets_e5_json \\
      --output_dir /mnt/disk/darpa/cadets_e5_output \\
      --batch_size 5
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

# CADETS E5 完整文件列表（364 个 bin.json 分片，按时间顺序）
DEFAULT_FILE_LIST = (
    [
        'ta1-cadets-1-e5-official-2.bin.json',
        'ta1-cadets-1-e5-official-2.bin.json.1',
        'ta1-cadets-1-e5-official-2.bin.json.2',
    ]
    + [
        f'ta1-cadets-1-e5-official-2.bin.{i}.json{suffix}'
        for i in range(1, 121)
        for suffix in ('', '.1', '.2')
    ]
    + [
        'ta1-cadets-1-e5-official-2.bin.121.json',
        'ta1-cadets-1-e5-official-2.bin.121.json.1',
    ]
)

# 保留的边类型（与 CADETS E3 完全一致，13 种）
INCLUDE_EDGE_TYPE = {
    'EVENT_READ',
    'EVENT_WRITE',
    'EVENT_EXECUTE',
    'EVENT_FORK',
    'EVENT_OPEN',
    'EVENT_CONNECT',
    'EVENT_SENDTO',
    'EVENT_RECVFROM',
    'EVENT_SENDMSG',
    'EVENT_RECVMSG',
    'EVENT_RENAME',
    'EVENT_UNLINK',
    'EVENT_CREATE_OBJECT',
}

# 反转的边（转为数据流方向）
EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_EXECUTE',
    'EVENT_OPEN',
}


# ============================================================================
# 辅助函数
# ============================================================================

def norm_uuid(u):
    """CADETS E5 UUID 大部分大写，统一转小写。"""
    return u.lower() if isinstance(u, str) else u


def unpack_dict_str(v):
    """NetFlow.localAddress 等是 dict {"string": "..."}, 解包成 str。"""
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
    if override:
        files = [os.path.join(input_dir, f) for f in override]
    else:
        files = [os.path.join(input_dir, f) for f in DEFAULT_FILE_LIST]
    return [f for f in files if os.path.exists(f)]


# ============================================================================
# Pass 1: 实体收集 + 名称更新 + cmdLine 收集
# ============================================================================

def pass1_collect(input_files):
    """
    单遍扫描，同时完成：
      1. 从 Subject/FileObject/NetFlowObject 创建 uuid2name（name 初始 None）
      2. 从 Event.properties.exec 持续覆盖进程名
      3. 从 Event.predicateObjectPath / predicateObject2Path 更新文件路径
      4. 从 EVENT_EXECUTE 收集进程 cmdLine（仅第一次出现的）
    """
    uuid2name = {}          # uuid (lower) → [type, name]
    uuid_cmdline = {}       # uuid → cmdLine
    netflow_info = {}       # uuid → (la, lp, ra, rp)

    loaded_line = 0
    begin = time.time()

    for vidx, vp in enumerate(input_files):
        print(f"  Pass1 [{vidx+1}/{len(input_files)}]: {os.path.basename(vp)}")
        with open(vp, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... uuid2name={len(uuid2name):,}")

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
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = norm_uuid(datum.get('uuid'))
                        if uid:
                            uuid2name[uid] = ['process', None]

                elif rtype == 'FileObject':
                    ftype = datum.get('type', '')
                    # 仅保留 FILE / DIR（与 CADETS E3 一致，丢弃 UNIX_SOCKET）
                    if ftype in ('FILE_OBJECT_FILE', 'FILE_OBJECT_DIR'):
                        uid = norm_uuid(datum.get('uuid'))
                        if uid:
                            uuid2name[uid] = ['file', None]

                elif rtype == 'NetFlowObject':
                    uid = norm_uuid(datum.get('uuid'))
                    la = unpack_dict_str(datum.get('localAddress'))
                    lp = unpack_dict_int(datum.get('localPort'))
                    ra = unpack_dict_str(datum.get('remoteAddress'))
                    rp = unpack_dict_int(datum.get('remotePort'))
                    if uid:
                        uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                        netflow_info[uid] = (str(la), str(lp), str(ra), str(rp))

                elif rtype == 'Event':
                    raw_props = datum.get('properties')
                    pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                    if not isinstance(pmap, dict):
                        pmap = {}

                    src = get_uuid(datum.get('subject'))
                    dst = get_uuid(datum.get('predicateObject'))
                    dst2 = get_uuid(datum.get('predicateObject2'))

                    # 1) 持续更新进程名为最后看到的 exec
                    if src and 'exec' in pmap:
                        if src in uuid2name and uuid2name[src][0] == 'process':
                            uuid2name[src][1] = pmap['exec']

                    # 2) cmdLine 仅在 EVENT_EXECUTE，取第一次
                    if datum.get('type') == 'EVENT_EXECUTE' and src:
                        if src not in uuid_cmdline:
                            uuid_cmdline[src] = pmap.get('cmdLine', None)

                    # 3) predicateObjectPath 更新文件路径
                    pop = datum.get('predicateObjectPath')
                    if isinstance(pop, dict):
                        path = pop.get('string', '')
                        if path and dst and dst in uuid2name and uuid2name[dst][0] == 'file':
                            uuid2name[dst][1] = path

                    # 4) predicateObject2Path（RENAME 等）
                    po2p = datum.get('predicateObject2Path')
                    if isinstance(po2p, dict):
                        path2 = po2p.get('string', '')
                        if path2 and dst2 and dst2 in uuid2name and uuid2name[dst2][0] == 'file':
                            uuid2name[dst2][1] = path2

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
    """
    第二遍扫描提取边。

    cmdLine 处理：
      - EVENT_EXECUTE: cmdLine = Event.properties.map.cmdLine
      - EVENT_FORK:    cmdLine = uuid_cmdline[child]  （回填子进程后续 EXECUTE 的 cmdLine）

    内存友好：每 batch_size_files 个输入文件 flush 一次 edges_part_NNN.pkl
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
        print(f"  [flush] edges_part_{part_idx:03d}.pkl  ({len(cur_batch):,} 条)")
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
                rtype = rtype_full.rsplit('.', 1)[-1]
                if rtype != 'Event':
                    continue

                eventtype = datum.get('type', '')
                if eventtype not in INCLUDE_EDGE_TYPE:
                    skipped_filtered += 1
                    continue

                eventtime = datum.get('timestampNanos', 0)
                raw_props = datum.get('properties')
                pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                if not isinstance(pmap, dict):
                    pmap = {}

                src = get_uuid(datum.get('subject'))
                dst = get_uuid(datum.get('predicateObject'))
                dst2 = get_uuid(datum.get('predicateObject2'))

                # cmdLine 处理
                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    cmdline = pmap.get('cmdLine', None)
                elif eventtype == 'EVENT_FORK':
                    cmdline = uuid_cmdline.get(dst, None)

                # RENAME 用 predicateObject2 作为目标
                actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst

                if not src or not actual_dst:
                    skipped_no_node += 1
                    continue
                if src not in uuid2name or actual_dst not in uuid2name:
                    skipped_no_node += 1
                    continue

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
    print(f"  提取的边: {total_edges:,}（分 {len(edges_part_paths)} 个 part）")
    print(f"  过滤的边(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过的边(节点不存在):   {skipped_no_node:,}")
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
    print("CADETS E5 数据提取（本地版）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    print(f"批大小:   {args.batch_size}")
    os.makedirs(args.output_dir, exist_ok=True)

    input_files = list_input_files(args.input_dir)
    print(f"\n输入文件 ({len(input_files)} 个):")
    for f in input_files:
        print(f"  {os.path.basename(f)}")

    print(f"\n{'='*60}\nPass 1: 实体收集 + 名称更新\n{'='*60}")
    uuid2name, uuid_cmdline, netflow_info = pass1_collect(input_files)

    print(f"\n{'='*60}\nPass 2: 边提取\n{'='*60}")
    pass2_extract_edges(input_files, uuid2name, uuid_cmdline,
                        args.output_dir, args.batch_size)

    print(f"\n{'='*60}\n保存元数据\n{'='*60}")

    with open(os.path.join(args.output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name.pkl ({len(uuid2name):,})")

    with open(os.path.join(args.output_dir, 'uuid_cmdline.pkl'), 'wb') as f:
        pickle.dump(uuid_cmdline, f)
    print(f"  uuid_cmdline.pkl ({len(uuid_cmdline):,})")

    with open(os.path.join(args.output_dir, 'netflow_info.pkl'), 'wb') as f:
        pickle.dump(netflow_info, f)
    print(f"  netflow_info.pkl ({len(netflow_info):,})")

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
    parser = argparse.ArgumentParser(description="CADETS E5 Data Extractor")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/cch_refine/cadets_e5_json")
    parser.add_argument("--output_dir", type=str,
                        default="/mnt/disk/darpa/cadets_e5_output")
    parser.add_argument("--batch_size", type=int, default=5,
                        help="每多少个输入文件 flush 一次 edges_part_*.pkl")
    args = parser.parse_args()
    main(args)
