"""
TRACE E3 数据提取脚本（续跑版）

用于在 extract_trace_e3_part.py 中断后续跑。

改进：
1. 从指定文件开始处理（--resume_from ta1-trace-e3-official.json.16）
2. 每 10 个文件保存一次（而非每个文件，减少 I/O）
3. Pass 1 的 cmdLine 映射会单独保存（cmdlines.pkl），下次中断无需重跑 Pass 1
4. 复用已保存的 uuid2name.pkl 和 cmdlines.pkl（如果存在）

用法：
  # 首次（或未保存cmdLine映射）运行 Pass 1 然后从 json.16 开始 Pass 2:
  python extract_trace_e3_break.py \
      --input_dir /mnt/disk/darpa/trace_e3 \
      --output_dir /mnt/disk/darpa/trace_e3_20260422 \
      --resume_from ta1-trace-e3-official.json.16

  # 如果 cmdlines.pkl 已存在则跳过 Pass 1：
  同上命令，脚本会自动检测并跳过
"""

import json
import os
import time
import argparse
import pickle
from collections import Counter


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
# Pass 1（仅在 cmdlines.pkl 不存在时执行）
# ============================================================================

def pass1_collect(input_dir):
    """重新扫描所有文件，收集 cmdLine 映射"""
    uuid2name = {}
    uuid_execute_dst_cmdline = {}
    uuid_subject_cmdline = {}

    loaded_line = 0
    begin = time.time()

    for volume_name in FILE_LIST:
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            continue
        print(f"  Pass1 处理: {volume_name}")

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

    print(f"\n  Pass 1 完成: {loaded_line:,} 行, {time.time()-begin:.1f}s")
    return uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline


# ============================================================================
# Pass 2：从指定文件开始，10个文件一个分片
# ============================================================================

def pass2_extract_edges_batched(input_dir, output_dir, start_index,
                                  uuid2name, uuid_execute_dst_cmdline,
                                  uuid_subject_cmdline, batch_size=10):
    """
    从 FILE_LIST[start_index] 开始处理，每 batch_size 个文件保存一个分片。
    """
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0
    total_edges = 0
    part_index = start_index  # 分片编号从起始文件索引开始

    # 缓冲区：累积 batch_size 个文件的边再写入
    batch_edges = []
    batch_file_count = 0
    batch_start_index = start_index

    loaded_line = 0
    begin = time.time()

    remaining_files = FILE_LIST[start_index:]
    print(f"  将处理 {len(remaining_files)} 个文件，从索引 {start_index} 开始")
    print(f"  每 {batch_size} 个文件保存一个分片")

    for file_idx, volume_name in enumerate(remaining_files, start=start_index):
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            print(f"  跳过（文件不存在）: {volume_name}")
            continue
        print(f"  处理 [{file_idx}]: {volume_name}")

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 2000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... (当前批次边数: {len(batch_edges):,})")

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
                batch_edges.append(edge)
                edge_type_count[eventtype] += 1

        batch_file_count += 1

        # 达到 batch_size 个文件或最后一个文件 → 写入分片
        is_last = (file_idx == start_index + len(remaining_files) - 1)
        if batch_file_count >= batch_size or is_last:
            if batch_edges:
                part_path = os.path.join(output_dir, f'edges_part_{batch_start_index:03d}.pkl')
                with open(part_path, 'wb') as f:
                    pickle.dump(batch_edges, f)
                total_edges += len(batch_edges)
                print(f"    → 分片 {batch_start_index:03d} "
                      f"(文件 {batch_start_index}~{batch_start_index+batch_file_count-1}): "
                      f"{len(batch_edges):,} 条边")
                del batch_edges
            batch_edges = []
            batch_start_index = file_idx + 1
            batch_file_count = 0

    elapsed = time.time() - begin
    print(f"\n  Pass 2 完成: {loaded_line:,} 行, {elapsed:.1f}s")
    print(f"  总边数: {total_edges:,}")
    print(f"  过滤(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过(节点不存在):   {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return total_edges


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("TRACE E3 数据提取（续跑版）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    print(f"从文件开始: {args.resume_from}")
    print(f"分片大小: {args.batch_size} 个文件")

    os.makedirs(args.output_dir, exist_ok=True)

    # 定位起始文件索引
    try:
        start_index = FILE_LIST.index(args.resume_from)
    except ValueError:
        print(f"  错误：找不到文件 {args.resume_from} 在 FILE_LIST 中")
        return
    print(f"  起始索引: {start_index}  (剩余 {len(FILE_LIST)-start_index} 个文件)")

    # ========== 加载或重建 cmdLine 映射 ==========
    uuid2name_path = os.path.join(args.output_dir, 'uuid2name.pkl')
    cmdlines_path = os.path.join(args.output_dir, 'cmdlines.pkl')

    if os.path.exists(uuid2name_path) and os.path.exists(cmdlines_path):
        print(f"\n{'='*60}")
        print("加载已有的 uuid2name.pkl 和 cmdlines.pkl")
        print(f"{'='*60}")
        t0 = time.time()
        with open(uuid2name_path, 'rb') as f:
            uuid2name = pickle.load(f)
        with open(cmdlines_path, 'rb') as f:
            cmdlines_data = pickle.load(f)
            uuid_execute_dst_cmdline = cmdlines_data['uuid_execute_dst_cmdline']
            uuid_subject_cmdline = cmdlines_data['uuid_subject_cmdline']
        print(f"  加载完成: {time.time()-t0:.1f}s")
        print(f"  uuid2name: {len(uuid2name):,}")
        print(f"  uuid_execute_dst_cmdline: {len(uuid_execute_dst_cmdline):,}")
        print(f"  uuid_subject_cmdline: {len(uuid_subject_cmdline):,}")

    else:
        print(f"\n{'='*60}")
        print("cmdlines.pkl 不存在，重新运行 Pass 1")
        print(f"{'='*60}")
        print("  注意：这需要重新扫描所有文件（约3小时）")
        uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline = pass1_collect(args.input_dir)

        # 保存
        print(f"\n  保存 uuid2name.pkl 和 cmdlines.pkl ...")
        with open(uuid2name_path, 'wb') as f:
            pickle.dump(uuid2name, f)
        with open(cmdlines_path, 'wb') as f:
            pickle.dump({
                'uuid_execute_dst_cmdline': uuid_execute_dst_cmdline,
                'uuid_subject_cmdline': uuid_subject_cmdline,
            }, f)
        print(f"  已保存")

    # ========== Pass 2：从指定文件开始，10个文件一片 ==========
    print(f"\n{'='*60}")
    print(f"Pass 2: 从 {args.resume_from} 开始提取边")
    print(f"{'='*60}")
    total = pass2_extract_edges_batched(
        args.input_dir, args.output_dir, start_index,
        uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline,
        batch_size=args.batch_size,
    )

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="TRACE E3 Resume Extractor")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/trace_e3")
    parser.add_argument("--output_dir", type=str, default="/mnt/disk/darpa/trace_e3_20260422")
    parser.add_argument("--resume_from", type=str, default="ta1-trace-e3-official.json.16",
                        help="从此文件开始处理")
    parser.add_argument("--batch_size", type=int, default=10,
                        help="每多少个文件保存一个分片（默认10）")
    args = parser.parse_args()
    main(args)
