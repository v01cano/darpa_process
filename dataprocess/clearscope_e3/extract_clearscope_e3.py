"""
ClearScope E3 数据提取脚本（本地磁盘版）

基于对 5,000,000 行原始CDM数据的统计分析，设计的数据提取方案。

ClearScope E3 是 Android 平台数据集，与 CADETS/THEIA（Linux/FreeBSD）有根本性差异：
  1. 只有 37 个进程（Android应用进程），无 FORK/CLONE/EXECUTE 事件
  2. 进程名来自 Subject.cmdLine（Android包名/进程名，如 system_server）
  3. 文件路径来自 FileObject.baseObject.properties.map.path（100%填充）
  4. SrcSinkObject 丢弃（与 Orthrus 一致，Android Binder IPC 不纳入图）
  5. Event 无 exec/cmdLine/predicateObjectPath 字段
  6. 无需进程名更新、无需 cmdLine 回填 → 单遍扫描即可

用法：
  python extract_clearscope_e3.py --input_dir /mnt/disk/darpa/clearscope_e3 --output_dir ./output
"""

import json
import os
import time
import argparse
import pickle
from collections import Counter

# ============================================================================
# 配置
# ============================================================================

FILE_LIST = [
    'ta1-clearscope-e3-official.json',
    'ta1-clearscope-e3-official.json.1',
    'ta1-clearscope-e3-official-1.json',
    'ta1-clearscope-e3-official-1.json.1',
    'ta1-clearscope-e3-official-1.json.2',
    'ta1-clearscope-e3-official-1.json.3',
    'ta1-clearscope-e3-official-1.json.4',
    'ta1-clearscope-e3-official-1.json.5',
    'ta1-clearscope-e3-official-1.json.6',
    'ta1-clearscope-e3-official-1.json.7',
    'ta1-clearscope-e3-official-1.json.8',
    'ta1-clearscope-e3-official-1.json.9',
    'ta1-clearscope-e3-official-1.json.10',
    'ta1-clearscope-e3-official-1.json.11',
    'ta1-clearscope-e3-official-1.json.12',
    'ta1-clearscope-e3-official-1.json.13',
    'ta1-clearscope-e3-official-1.json.14',
    'ta1-clearscope-e3-official-1.json.15',
    'ta1-clearscope-e3-official-1.json.16',
    'ta1-clearscope-e3-official-1.json.17',
    'ta1-clearscope-e3-official-1.json.18',
    'ta1-clearscope-e3-official-1.json.19',
    'ta1-clearscope-e3-official-2.json',
    'ta1-clearscope-e3-official-2.json.1',
    'ta1-clearscope-e3-official-2.json.2',
    'ta1-clearscope-e3-official-2.json.3',
    'ta1-clearscope-e3-official-2.json.4',
    'ta1-clearscope-e3-official-2.json.5',
    'ta1-clearscope-e3-official-2.json.6',
    'ta1-clearscope-e3-official-2.json.7',
    'ta1-clearscope-e3-official-2.json.8',
    'ta1-clearscope-e3-official-2.json.9',
    'ta1-clearscope-e3-official-2.json.10',
    'ta1-clearscope-e3-official-2.json.11',
    'ta1-clearscope-e3-official-2.json.12',
    'ta1-clearscope-e3-official-2.json.13',
    'ta1-clearscope-e3-official-2.json.14',
    'ta1-clearscope-e3-official-2.json.15',
    'ta1-clearscope-e3-official-2.json.16',
    'ta1-clearscope-e3-official-2.json.17',
    'ta1-clearscope-e3-official-2.json.18',
    'ta1-clearscope-e3-official-2.json.19',
    'ta1-clearscope-e3-official-2.json.20',
    'ta1-clearscope-e3-official-2.json.21',
    'ta1-clearscope-e3-official-2.json.22',
    'ta1-clearscope-e3-official-2.json.23',
    'ta1-clearscope-e3-official-2.json.24',
    'ta1-clearscope-e3-official-2.json.25',
    'ta1-clearscope-e3-official-2.json.26',
    'ta1-clearscope-e3-official-2.json.27',
    'ta1-clearscope-e3-official-2.json.28',
]

# 保留的边类型（与 Orthrus rel2id 一致）
INCLUDE_EDGE_TYPE = {
    'EVENT_READ',
    'EVENT_WRITE',
    'EVENT_OPEN',
    'EVENT_CONNECT',
    'EVENT_SENDTO',
    'EVENT_RECVFROM',
    'EVENT_SENDMSG',
    'EVENT_RECVMSG',
    'EVENT_UNLINK',
    'EVENT_RENAME',
    'EVENT_CREATE_OBJECT',
}

# 反转的边类型（与 CADETS/THEIA 一致）
EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_OPEN',
}


# ============================================================================
# 单遍提取（ClearScope 不需要多遍——无 EXECUTE 更新进程名）
# ============================================================================

def extract_all(input_dir):
    """
    单遍扫描所有文件，同时完成实体收集和边提取。

    ClearScope 的简化之处：
    - 无 EXECUTE → 不需要更新进程名
    - 无 CLONE/FORK → 不需要 cmdLine 回填
    - 实体记录 100% 自带名称 → 不需要从 Event 更新
    """
    uuid2name = {}      # uuid → [type, name]
    datalist = []
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0

    loaded_line = 0
    begin = time.time()

    for volume_name in FILE_LIST:
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            print(f"  WARNING: {volume_path} not found, skipping.")
            continue
        print(f"  处理: {volume_name}")

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行...")

                record = json.loads(line)['datum']
                rtype_full = list(record.keys())[0]
                datum = record[rtype_full]
                rtype = rtype_full.split('.')[-1]

                # ---------- Subject ----------
                if rtype == 'Subject':
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = datum['uuid']
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        uuid2name[uid] = ['process', cmdline]

                # ---------- FileObject ----------
                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    base_props = datum.get('baseObject', {})
                    if isinstance(base_props, dict):
                        props_map = base_props.get('properties', {})
                        if isinstance(props_map, dict):
                            props_map = props_map.get('map', {})
                        else:
                            props_map = {}
                    else:
                        props_map = {}
                    path = props_map.get('path') if isinstance(props_map, dict) else None
                    uuid2name[uid] = ['file', path]

                # ---------- NetFlowObject ----------
                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]

                # ---------- Event ----------
                elif rtype == 'Event':
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

                    # RENAME 使用 predicateObject2
                    if eventtype == 'EVENT_RENAME':
                        actual_dst = dst2
                    else:
                        actual_dst = dst

                    if not src or not actual_dst:
                        skipped_no_node += 1
                        continue
                    if src not in uuid2name or actual_dst not in uuid2name:
                        skipped_no_node += 1
                        continue

                    # 边方向反转
                    if eventtype in EDGE_REVERSED:
                        edge_src, edge_dst = actual_dst, src
                    else:
                        edge_src, edge_dst = src, actual_dst

                    datalist.append((
                        eventtime,
                        eventtype,
                        edge_src,
                        uuid2name[edge_src][0],
                        uuid2name[edge_src][1],
                        edge_dst,
                        uuid2name[edge_dst][0],
                        uuid2name[edge_dst][1],
                        None,  # cmdLine: ClearScope 无 EXECUTE/CLONE，永远为 None
                    ))
                    edge_type_count[eventtype] += 1

                # SrcSinkObject / MemoryObject / ProvenanceTagNode → 丢弃

    elapsed = time.time() - begin
    print(f"\n  完成: {loaded_line:,} 行, {elapsed:.1f}s")

    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")

    print(f"  提取的边: {len(datalist):,}")
    print(f"  过滤的边(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过的边(节点不存在):   {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return uuid2name, datalist


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("ClearScope E3 数据提取")
    print("=" * 60)

    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    uuid2name, datalist = extract_all(args.input_dir)

    print(f"\n{'='*60}")
    print("保存结果")
    print(f"{'='*60}")

    with open(os.path.join(args.output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name.pkl 已保存")

    with open(os.path.join(args.output_dir, 'datalist.pkl'), 'wb') as f:
        pickle.dump(datalist, f)
    print(f"  datalist.pkl 已保存")

    csv_path = os.path.join(args.output_dir, 'edges.csv')
    with open(csv_path, 'w') as f:
        f.write('timestamp,event_type,src_uuid,src_type,src_name,dst_uuid,dst_type,dst_name,cmdline\n')
        for row in datalist[:100000]:
            fields = [str(x) if x is not None else '' for x in row]
            fields = [field.replace(',', ';') for field in fields]
            f.write(','.join(fields) + '\n')
    print(f"  edges.csv (前10万条) 已保存")

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="ClearScope E3 Data Extractor")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/clearscope_e3")
    parser.add_argument("--output_dir", type=str, default="./output_clearscope_e3")
    args = parser.parse_args()
    main(args)
