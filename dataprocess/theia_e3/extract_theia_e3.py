"""
THEIA E3 数据提取脚本（本地磁盘版）

基于对 113,293,343 行原始CDM数据的统计分析，设计的数据提取方案。

与 CADETS E3 的核心差异：
  1. 进程初始名来自 Subject.properties.map.path（99.3%填充，CADETS=0%）
  2. 进程名通过 EXECUTE 事件的 dst FileObject filename 更新（CADETS 用 Event.exec）
  3. 文件路径来自 FileObject.baseObject.properties.map.filename（97.4%，CADETS=0%）
  4. Event.exec 字段不存在（0%，CADETS=99.9%）
  5. Event.predicateObjectPath 不存在（0%，CADETS=44.2%）
  6. FileObject 类型为 FILE_OBJECT_BLOCK（非 CADETS 的 FILE_OBJECT_FILE）
  7. 有大量 MemoryObject（5,649,856个）→ 丢弃
  8. EVENT_MPROTECT 占 49.5% → 过滤
  9. 使用 EVENT_CLONE 而非 EVENT_FORK

THEIA E3 的 UUID 生成机制（与 CADETS 本质相同）：
  - UUID 在 fork/clone 时生成
  - Subject.path 是 fork 继承的父进程路径（79.6% 与 EXECUTE dst 不同）
  - exec 后进程身份改变，但 Subject 记录不更新
  - 需要通过 EXECUTE 的 dst FileObject filename 来获取进程的真实身份
  - 最终进程名 = 最后一次 EXECUTE 的 dst 文件名

用法：
  python extract_theia_e3.py --input_dir /mnt/disk/darpa/theia_e3 --output_dir ./output
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
    'ta1-theia-e3-official-1r.json',
    'ta1-theia-e3-official-1r.json.1',
    'ta1-theia-e3-official-1r.json.2',
    'ta1-theia-e3-official-1r.json.3',
    'ta1-theia-e3-official-1r.json.4',
    'ta1-theia-e3-official-1r.json.5',
    'ta1-theia-e3-official-1r.json.6',
    'ta1-theia-e3-official-1r.json.7',
    'ta1-theia-e3-official-1r.json.8',
    'ta1-theia-e3-official-1r.json.9',
    'ta1-theia-e3-official-3.json',
    'ta1-theia-e3-official-5m.json',
    'ta1-theia-e3-official-6r.json',
    'ta1-theia-e3-official-6r.json.1',
    'ta1-theia-e3-official-6r.json.2',
    'ta1-theia-e3-official-6r.json.3',
    'ta1-theia-e3-official-6r.json.4',
    'ta1-theia-e3-official-6r.json.5',
    'ta1-theia-e3-official-6r.json.6',
    'ta1-theia-e3-official-6r.json.7',
    'ta1-theia-e3-official-6r.json.8',
    'ta1-theia-e3-official-6r.json.9',
    'ta1-theia-e3-official-6r.json.10',
    'ta1-theia-e3-official-6r.json.11',
    'ta1-theia-e3-official-6r.json.12',
]

# 保留的边类型（11种）
INCLUDE_EDGE_TYPE = {
    'EVENT_READ',
    'EVENT_WRITE',
    'EVENT_EXECUTE',
    'EVENT_CLONE',          # THEIA 用 CLONE（CADETS 用 FORK）
    'EVENT_OPEN',
    'EVENT_CONNECT',
    'EVENT_SENDTO',
    'EVENT_RECVFROM',
    'EVENT_SENDMSG',
    'EVENT_RECVMSG',
    'EVENT_UNLINK',
}

# 反转的边类型（转为数据流方向，与 CADETS 一致）
EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_EXECUTE',
    'EVENT_OPEN',
}


# ============================================================================
# Pass 1: 实体收集 + 进程名更新 + cmdLine收集
# ============================================================================

def pass1_collect(input_dir):
    """
    单遍扫描所有文件：
    1. 从 Subject 记录创建进程节点（初始名 = Subject.properties.map.path）
    2. 从 FileObject 记录创建文件节点（名 = baseObject.properties.map.filename）
    3. 从 NetFlowObject 记录创建网络节点
    4. 遇到 EXECUTE 事件时，用 dst FileObject 的 filename 更新进程名
    5. 收集每个进程第一个 EXECUTE 的 Event.cmdLine（用于回填 CLONE 边）
    6. 收集 Subject.cmdLine 作为 fallback（未做 EXECUTE 的进程）
    """
    uuid2name = {}              # uuid → [type, name]
    uuid_exec_cmdline = {}      # uuid → 第一个 EXECUTE 的 Event.cmdLine
    uuid_subject_cmdline = {}   # uuid → Subject.cmdLine（fallback）

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
                    if datum['type'] == 'SUBJECT_PROCESS':
                        uid = datum['uuid']
                        props = datum.get('properties', {}).get('map', {})
                        path = props.get('path')
                        uuid2name[uid] = ['process', path]
                        # 收集 Subject.cmdLine
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        if cmdline:
                            uuid_subject_cmdline[uid] = cmdline

                # ---------- FileObject ----------
                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    base_props = datum.get('baseObject', {}).get('properties', {}).get('map', {})
                    filename = base_props.get('filename')
                    uuid2name[uid] = ['file', filename]

                # ---------- NetFlowObject ----------
                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]

                # ---------- Event (EXECUTE) ----------
                elif rtype == 'Event':
                    if datum.get('type') != 'EVENT_EXECUTE':
                        continue

                    props = datum.get('properties', {}).get('map', {})
                    src = None
                    if isinstance(datum.get('subject'), dict):
                        src = list(datum['subject'].values())[0]
                    dst = None
                    if isinstance(datum.get('predicateObject'), dict):
                        dst = list(datum['predicateObject'].values())[0]

                    if src and dst:
                        # 用 dst FileObject 的 filename 更新进程名
                        if dst in uuid2name and uuid2name[dst][0] == 'file':
                            dst_filename = uuid2name[dst][1]
                            if dst_filename and src in uuid2name and uuid2name[src][0] == 'process':
                                uuid2name[src][1] = dst_filename

                        # 收集第一个 EXECUTE 的 Event.cmdLine
                        if src not in uuid_exec_cmdline:
                            event_cmdline = props.get('cmdLine')
                            if event_cmdline:
                                uuid_exec_cmdline[src] = event_cmdline

                # MemoryObject / UnnamedPipeObject / SrcSinkObject → 丢弃

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
    print(f"  有EXECUTE cmdLine的进程: {len(uuid_exec_cmdline):,}")
    print(f"  有Subject cmdLine的进程: {len(uuid_subject_cmdline):,}")

    return uuid2name, uuid_exec_cmdline, uuid_subject_cmdline


# ============================================================================
# Pass 2: 边提取
# ============================================================================

def pass2_extract_edges(input_dir, uuid2name, uuid_exec_cmdline, uuid_subject_cmdline):
    """
    第二遍扫描，提取所有边。

    cmdLine 处理：
    - EVENT_EXECUTE: Event.properties.map.cmdLine
    - EVENT_CLONE: 子进程第一个 EXECUTE 的 Event.cmdLine
                   fallback: 子进程的 Subject.cmdLine
    """
    datalist = []
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0

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
                if loaded_line % 1000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行...")

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

                eventtime = datum['timestampNanos']
                props = datum.get('properties', {}).get('map', {})

                src = ''
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = ''
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]

                # cmdLine 处理
                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    cmdline = props.get('cmdLine', None)
                elif eventtype == 'EVENT_CLONE':
                    # 子进程第一个 EXECUTE 的 Event.cmdLine
                    cmdline = uuid_exec_cmdline.get(dst, None)
                    # fallback: 子进程的 Subject.cmdLine
                    if cmdline is None:
                        cmdline = uuid_subject_cmdline.get(dst, None)

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
                    cmdline,
                ))
                edge_type_count[eventtype] += 1

    elapsed = time.time() - begin
    print(f"\n  Pass 2 完成: {loaded_line:,} 行, {elapsed:.1f}s")
    print(f"  提取的边: {len(datalist):,}")
    print(f"  过滤的边(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过的边(节点不存在):   {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return datalist


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("THEIA E3 数据提取")
    print("=" * 60)

    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print("Pass 1: 实体收集 + 进程名更新")
    print(f"{'='*60}")
    uuid2name, uuid_exec_cmdline, uuid_subject_cmdline = pass1_collect(args.input_dir)

    print(f"\n{'='*60}")
    print("Pass 2: 边提取")
    print(f"{'='*60}")
    datalist = pass2_extract_edges(args.input_dir, uuid2name, uuid_exec_cmdline, uuid_subject_cmdline)

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
    parser = argparse.ArgumentParser(description="THEIA E3 Data Extractor")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/theia_e3")
    parser.add_argument("--output_dir", type=str, default="./output_theia_e3")
    args = parser.parse_args()
    main(args)
