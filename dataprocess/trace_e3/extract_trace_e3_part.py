"""
TRACE E3 数据提取脚本（分片版）

解决原版的内存问题：
  - 原版将所有边存在内存 datalist 中 → 300G数据集会OOM
  - 本版按文件分片输出，边不在内存中累积

方案：
  Pass 1: 扫描所有文件，收集实体 + cmdLine（只存节点信息，内存可控）
  Pass 2: 逐文件扫描，边直接追加写入磁盘（不在内存中累积）

输出：
  - uuid2name.pkl          节点映射
  - edges_part_XXX.pkl     分片边文件（每个原始文件对应一个分片）
  - edges.csv              CSV格式（前10万条，方便查看）

用法：
  python extract_trace_e3_part.py --input_dir /mnt/disk/darpa/trace_e3 --output_dir ./output
"""

import json
import os
import time
import argparse
import pickle
from collections import Counter

# ============================================================================
# 配置（与 extract_trace_e3.py 完全一致）
# ============================================================================

def build_file_list():
    files = []
    files.append('ta1-trace-e3-official.json')
    for i in range(1, 204):
        files.append(f'ta1-trace-e3-official.json.{i}')
    files.append('ta1-trace-e3-official-1.json')
    for i in range(1, 7):
        files.append(f'ta1-trace-e3-official-1.json.{i}')
    return files

FILE_LIST = build_file_list()

INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_EXECUTE',
    'EVENT_FORK', 'EVENT_CLONE',
    'EVENT_OPEN', 'EVENT_CONNECT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_CREATE_OBJECT',
    'EVENT_LOADLIBRARY', 'EVENT_ACCEPT',
}

EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG',
    'EVENT_OPEN', 'EVENT_LOADLIBRARY', 'EVENT_ACCEPT',
}


# ============================================================================
# Pass 1: 实体收集 + cmdLine收集（只存节点，内存可控）
# ============================================================================

def pass1_collect(input_dir):
    uuid2name = {}
    uuid_execute_dst_cmdline = {}
    uuid_subject_cmdline = {}

    loaded_line = 0
    begin = time.time()

    for volume_name in FILE_LIST:
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            continue
        print(f"  处理: {volume_name}")

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 2000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行...")

                record = json.loads(line)['datum']
                rtype_full = list(record.keys())[0]
                datum = record[rtype_full]
                rtype = rtype_full.split('.')[-1]

                if rtype == 'Subject':
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = datum['uuid']
                        props = datum.get('properties', {})
                        pm = props.get('map', {}) if isinstance(props, dict) else {}
                        if not isinstance(pm, dict): pm = {}
                        uuid2name[uid] = ['process', pm.get('name')]
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        if cmdline:
                            uuid_subject_cmdline[uid] = cmdline

                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    base = datum.get('baseObject', {})
                    bp = base.get('properties', {})
                    bpm = bp.get('map', {}) if isinstance(bp, dict) else {}
                    if not isinstance(bpm, dict): bpm = {}
                    uuid2name[uid] = ['file', bpm.get('path')]

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]

                elif rtype == 'Event':
                    if datum.get('type') == 'EVENT_EXECUTE':
                        src = None
                        if isinstance(datum.get('subject'), dict):
                            src = list(datum['subject'].values())[0]
                        dst = None
                        if isinstance(datum.get('predicateObject'), dict):
                            dst = list(datum['predicateObject'].values())[0]
                        if src and dst and src not in uuid_execute_dst_cmdline:
                            dc = uuid_subject_cmdline.get(dst)
                            if dc:
                                uuid_execute_dst_cmdline[src] = dc

    elapsed = time.time() - begin
    print(f"\n  Pass 1 完成: {loaded_line:,} 行, {elapsed:.1f}s")

    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")
    print(f"  有EXECUTE dst cmdLine: {len(uuid_execute_dst_cmdline):,}")
    print(f"  有Subject cmdLine: {len(uuid_subject_cmdline):,}")

    return uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline


# ============================================================================
# Pass 2: 逐文件提取边，直接写入磁盘（不在内存中累积）
# ============================================================================

def pass2_extract_edges_by_part(input_dir, output_dir, uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline):
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0
    total_edges = 0
    part_index = 0

    # CSV样本（前10万条，与其他数据集一致）
    csv_path = os.path.join(output_dir, 'edges.csv')
    csv_file = open(csv_path, 'w')
    csv_file.write('timestamp,event_type,src_uuid,src_type,src_name,dst_uuid,dst_type,dst_name,cmdline\n')
    csv_written = 0
    csv_limit = 100000

    loaded_line = 0
    begin = time.time()

    for volume_name in FILE_LIST:
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            continue
        print(f"  处理: {volume_name}")

        part_edges = []  # 当前文件的边（单文件大小可控）

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 2000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... (当前分片边数: {len(part_edges):,})")

                record = json.loads(line)['datum']
                rtype_full = list(record.keys())[0]
                datum = record[rtype_full]
                rtype = rtype_full.split('.')[-1]

                if rtype != 'Event':
                    continue

                eventtype = datum.get('type', '')
                if eventtype not in INCLUDE_EDGE_TYPE:
                    skipped_filtered += 1
                    continue

                eventtime = datum.get('timestampNanos', 0)

                src = ''
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = ''
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]
                dst2 = ''
                if isinstance(datum.get('predicateObject2'), dict):
                    dst2 = list(datum['predicateObject2'].values())[0]

                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    cmdline = uuid_subject_cmdline.get(dst, None)
                elif eventtype == 'EVENT_FORK':
                    cmdline = uuid_execute_dst_cmdline.get(dst, None)
                    if cmdline is None:
                        cmdline = uuid_subject_cmdline.get(dst, None)
                elif eventtype == 'EVENT_CLONE':
                    cmdline = uuid_subject_cmdline.get(dst, None)

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

                edge = (
                    eventtime, eventtype,
                    edge_src, uuid2name[edge_src][0], uuid2name[edge_src][1],
                    edge_dst, uuid2name[edge_dst][0], uuid2name[edge_dst][1],
                    cmdline,
                )
                part_edges.append(edge)
                edge_type_count[eventtype] += 1

                # CSV样本写入（前10万条）
                if csv_written < csv_limit:
                    fields = [str(x) if x is not None else '' for x in edge]
                    fields = [field.replace(',', ';') for field in fields]
                    csv_file.write(','.join(fields) + '\n')
                    csv_written += 1

        # 当前文件处理完毕，写入分片pkl
        if part_edges:
            part_path = os.path.join(output_dir, f'edges_part_{part_index:03d}.pkl')
            with open(part_path, 'wb') as f:
                pickle.dump(part_edges, f)
            total_edges += len(part_edges)
            print(f"    → 分片 {part_index:03d}: {len(part_edges):,} 条边")
            part_index += 1
            del part_edges  # 释放内存

    csv_file.close()

    elapsed = time.time() - begin
    print(f"\n  Pass 2 完成: {loaded_line:,} 行, {elapsed:.1f}s")
    print(f"  总边数: {total_edges:,}")
    print(f"  分片数: {part_index}")
    print(f"  过滤(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过(节点不存在):   {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return total_edges


# ============================================================================
# 加载工具（供下游使用）
# ============================================================================

def load_all_edges(output_dir):
    """从分片文件加载全部边（按需使用，需要足够内存）"""
    import glob
    all_edges = []
    for part_file in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(part_file, 'rb') as f:
            edges = pickle.load(f)
            all_edges.extend(edges)
        print(f"  加载 {part_file}: {len(edges):,} 条")
    print(f"  总计: {len(all_edges):,} 条边")
    return all_edges


def iter_edges(output_dir):
    """迭代器方式逐分片读取边（不占用额外内存）"""
    import glob
    for part_file in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(part_file, 'rb') as f:
            edges = pickle.load(f)
        for edge in edges:
            yield edge


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("TRACE E3 数据提取（分片版）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print("Pass 1: 实体收集 + cmdLine收集")
    print(f"{'='*60}")
    uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline = pass1_collect(args.input_dir)

    # 保存节点信息
    uuid2name_path = os.path.join(args.output_dir, 'uuid2name.pkl')
    with open(uuid2name_path, 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name.pkl 已保存 ({len(uuid2name):,} 个实体)")

    print(f"\n{'='*60}")
    print("Pass 2: 逐文件提取边 → 分片写入磁盘")
    print(f"{'='*60}")
    total = pass2_extract_edges_by_part(
        args.input_dir, args.output_dir,
        uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline)

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")
    print(f"  输出目录: {args.output_dir}")
    print(f"  uuid2name.pkl — 节点映射")
    print(f"  edges_part_XXX.pkl — 分片边文件")
    print(f"  edges.csv — CSV样本（前10万条）")
    print(f"\n  下游加载方式:")
    print(f"    全量: edges = load_all_edges('{args.output_dir}')")
    print(f"    流式: for edge in iter_edges('{args.output_dir}'): ...")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="TRACE E3 Data Extractor (Partitioned)")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/trace_e3")
    parser.add_argument("--output_dir", type=str, default="./output_trace_e3")
    args = parser.parse_args()
    main(args)
