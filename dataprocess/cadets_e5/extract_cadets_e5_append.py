"""
CADETS E5 增量补提取脚本

场景：之前 `extract_cadets_e5.py` 漏了最后一个文件
      `ta1-cadets-1-e5-official-2.bin.121.json.1`，
      不想全量重跑，只补这一个文件。

做法：
  1. 加载现有 uuid2name.pkl / uuid_cmdline.pkl / netflow_info.pkl
  2. Pass1：扫漏掉的文件，更新实体 + 名称（沿用 extract_cadets_e5 的逻辑）
  3. Pass2：扫漏掉的文件，提取边，写到下一个可用编号的 edges_part_NNN.pkl
  4. 覆盖回写 uuid2name.pkl / uuid_cmdline.pkl / netflow_info.pkl

注意事项：
  - 新文件中 Event.exec 会覆盖旧文件中已有的进程名（沿用"取最后一个 exec"策略）。
    但已经写出的旧 edges_part_*.pkl 中的 src_name/dst_name 是当时的快照，不会回填
    更新。这是有意的妥协；若需要严格一致，请改跑全量。
  - 新文件中第一次 EVENT_EXECUTE 的 cmdLine 只在该进程 UUID 第一次出现时记录。
    若旧文件已记录过该 UUID 的 cmdLine，将不会覆盖。

用法：
  python extract_cadets_e5_append.py \\
      --input_dir /mnt/disk/darpa/cch_refine/cadets_e5_json \\
      --output_dir /mnt/disk/darpa/cadets_e5_output \\
      --file ta1-cadets-1-e5-official-2.bin.121.json.1
"""

import os
import json
import time
import glob
import pickle
import argparse
from collections import Counter

from extract_cadets_e5 import (
    INCLUDE_EDGE_TYPE, EDGE_REVERSED,
    norm_uuid, unpack_dict_str, unpack_dict_int, get_uuid,
)


def next_part_index(output_dir):
    """根据已有 edges_part_*.pkl 推断下一个 part 编号。"""
    parts = sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl')))
    if not parts:
        return 0
    last = parts[-1]
    name = os.path.basename(last)
    # edges_part_NNN.pkl
    num = int(name.replace('edges_part_', '').replace('.pkl', ''))
    return num + 1


def load_state(output_dir):
    u2n_path = os.path.join(output_dir, 'uuid2name.pkl')
    uc_path = os.path.join(output_dir, 'uuid_cmdline.pkl')
    nf_path = os.path.join(output_dir, 'netflow_info.pkl')

    with open(u2n_path, 'rb') as f:
        uuid2name = pickle.load(f)
    print(f"  载入 uuid2name.pkl: {len(uuid2name):,} 个实体")

    uuid_cmdline = {}
    if os.path.exists(uc_path):
        with open(uc_path, 'rb') as f:
            uuid_cmdline = pickle.load(f)
        print(f"  载入 uuid_cmdline.pkl: {len(uuid_cmdline):,} 个进程")

    netflow_info = {}
    if os.path.exists(nf_path):
        with open(nf_path, 'rb') as f:
            netflow_info = pickle.load(f)
        print(f"  载入 netflow_info.pkl: {len(netflow_info):,} 条")

    return uuid2name, uuid_cmdline, netflow_info


def save_state(output_dir, uuid2name, uuid_cmdline, netflow_info):
    with open(os.path.join(output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name.pkl 已覆盖写 ({len(uuid2name):,})")
    with open(os.path.join(output_dir, 'uuid_cmdline.pkl'), 'wb') as f:
        pickle.dump(uuid_cmdline, f)
    print(f"  uuid_cmdline.pkl 已覆盖写 ({len(uuid_cmdline):,})")
    with open(os.path.join(output_dir, 'netflow_info.pkl'), 'wb') as f:
        pickle.dump(netflow_info, f)
    print(f"  netflow_info.pkl 已覆盖写 ({len(netflow_info):,})")


def pass1_one_file(filepath, uuid2name, uuid_cmdline, netflow_info):
    """对单个文件做 Pass1：更新实体 + 进程名 + 文件路径 + cmdLine。"""
    print(f"  Pass1: 扫描 {os.path.basename(filepath)} ...")
    n_lines = 0
    new_subject = 0
    new_file = 0
    new_netflow = 0
    name_updates = 0
    path_updates = 0
    cmd_adds = 0
    begin = time.time()

    with open(filepath, 'r') as fin:
        for line in fin:
            n_lines += 1
            if n_lines % 1000000 == 0:
                print(f"    已扫描 {n_lines:,} 行...")
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
                    if uid and uid not in uuid2name:
                        uuid2name[uid] = ['process', None]
                        new_subject += 1

            elif rtype == 'FileObject':
                ftype = datum.get('type', '')
                if ftype in ('FILE_OBJECT_FILE', 'FILE_OBJECT_DIR'):
                    uid = norm_uuid(datum.get('uuid'))
                    if uid and uid not in uuid2name:
                        uuid2name[uid] = ['file', None]
                        new_file += 1

            elif rtype == 'NetFlowObject':
                uid = norm_uuid(datum.get('uuid'))
                if uid and uid not in uuid2name:
                    la = unpack_dict_str(datum.get('localAddress'))
                    lp = unpack_dict_int(datum.get('localPort'))
                    ra = unpack_dict_str(datum.get('remoteAddress'))
                    rp = unpack_dict_int(datum.get('remotePort'))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (str(la), str(lp), str(ra), str(rp))
                    new_netflow += 1

            elif rtype == 'Event':
                raw_props = datum.get('properties')
                pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                if not isinstance(pmap, dict):
                    pmap = {}

                src = get_uuid(datum.get('subject'))
                dst = get_uuid(datum.get('predicateObject'))
                dst2 = get_uuid(datum.get('predicateObject2'))

                if src and 'exec' in pmap:
                    if src in uuid2name and uuid2name[src][0] == 'process':
                        uuid2name[src][1] = pmap['exec']
                        name_updates += 1

                if datum.get('type') == 'EVENT_EXECUTE' and src:
                    if src not in uuid_cmdline:
                        uuid_cmdline[src] = pmap.get('cmdLine', None)
                        cmd_adds += 1

                pop = datum.get('predicateObjectPath')
                if isinstance(pop, dict):
                    path = pop.get('string', '')
                    if path and dst and dst in uuid2name and uuid2name[dst][0] == 'file':
                        uuid2name[dst][1] = path
                        path_updates += 1

                po2p = datum.get('predicateObject2Path')
                if isinstance(po2p, dict):
                    path2 = po2p.get('string', '')
                    if path2 and dst2 and dst2 in uuid2name and uuid2name[dst2][0] == 'file':
                        uuid2name[dst2][1] = path2
                        path_updates += 1

    elapsed = time.time() - begin
    print(f"  Pass1 完成: {n_lines:,} 行, {elapsed:.1f}s")
    print(f"    新增 process: {new_subject:,}")
    print(f"    新增 file:    {new_file:,}")
    print(f"    新增 netflow: {new_netflow:,}")
    print(f"    进程名更新次数(exec):     {name_updates:,}")
    print(f"    文件路径更新次数(path):   {path_updates:,}")
    print(f"    新增 cmdLine:             {cmd_adds:,}")
    return n_lines


def pass2_one_file(filepath, uuid2name, uuid_cmdline):
    """对单个文件做 Pass2：提取边。"""
    print(f"  Pass2: 扫描 {os.path.basename(filepath)} ...")
    edges = []
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0
    n_lines = 0
    begin = time.time()

    with open(filepath, 'r') as fin:
        for line in fin:
            n_lines += 1
            if n_lines % 1000000 == 0:
                print(f"    已扫描 {n_lines:,} 行... edges_buf={len(edges):,}")
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
            raw_props = datum.get('properties')
            pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
            if not isinstance(pmap, dict):
                pmap = {}

            src = get_uuid(datum.get('subject'))
            dst = get_uuid(datum.get('predicateObject'))
            dst2 = get_uuid(datum.get('predicateObject2'))

            cmdline = None
            if eventtype == 'EVENT_EXECUTE':
                cmdline = pmap.get('cmdLine', None)
            elif eventtype == 'EVENT_FORK':
                cmdline = uuid_cmdline.get(dst, None)

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

            edges.append((
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

    elapsed = time.time() - begin
    print(f"  Pass2 完成: {n_lines:,} 行, {elapsed:.1f}s")
    print(f"    提取边: {len(edges):,}")
    print(f"    过滤(类型不在列表): {skipped_filtered:,}")
    print(f"    跳过(节点不存在):   {skipped_no_node:,}")
    print(f"    边类型分布:")
    for et, cnt in edge_type_count.most_common():
        print(f"      {et:30s} {cnt:>10,}")
    return edges


def main(args):
    print("=" * 60)
    print("CADETS E5 增量补提取")
    print("=" * 60)

    filepath = os.path.join(args.input_dir, args.file)
    if not os.path.exists(filepath):
        print(f"[ERROR] 文件不存在: {filepath}")
        return
    print(f"\n补提取文件: {filepath}")
    print(f"输出目录:   {args.output_dir}")

    # 1. 载入现有状态
    print(f"\n{'='*60}\n载入现有 pkl 状态\n{'='*60}")
    uuid2name, uuid_cmdline, netflow_info = load_state(args.output_dir)

    # 2. Pass1
    print(f"\n{'='*60}\nPass 1: 实体收集 + 名称更新\n{'='*60}")
    pass1_one_file(filepath, uuid2name, uuid_cmdline, netflow_info)

    # 3. Pass2
    print(f"\n{'='*60}\nPass 2: 边提取\n{'='*60}")
    edges = pass2_one_file(filepath, uuid2name, uuid_cmdline)

    # 4. 写新 part
    if edges:
        idx = next_part_index(args.output_dir)
        part_path = os.path.join(args.output_dir, f'edges_part_{idx:03d}.pkl')
        with open(part_path, 'wb') as f:
            pickle.dump(edges, f)
        print(f"\n  新增分片: {os.path.basename(part_path)} ({len(edges):,} 条边)")
    else:
        print("\n  [WARN] 该文件没有提取出任何边")

    # 5. 覆盖回写元数据
    print(f"\n{'='*60}\n覆盖回写元数据\n{'='*60}")
    save_state(args.output_dir, uuid2name, uuid_cmdline, netflow_info)

    print(f"\n{'='*60}\n完成\n{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="CADETS E5 incremental append")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/cch_refine/cadets_e5_json")
    parser.add_argument("--output_dir", type=str,
                        default="/mnt/disk/darpa/cadets_e5_output")
    parser.add_argument("--file", type=str,
                        default="ta1-cadets-1-e5-official-2.bin.121.json.1",
                        help="要补提取的单个文件名（位于 input_dir 下）")
    args = parser.parse_args()
    main(args)
