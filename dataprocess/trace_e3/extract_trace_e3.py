"""
TRACE E3 数据提取脚本（本地磁盘版）

TRACE E3 是 Linux 平台数据集，具有独特的进程模型：
  1. 每次 exec 创建新 UUID（不像 CADETS/THEIA 同一UUID改名）
  2. EXECUTE 连接 旧进程(Subject) → 新进程(Subject)，不是进程→文件
  3. 三层结构: PROCESS ──UNIT──> UNIT ──CLONE──> PROCESS ──FORK──> PROCESS ──EXECUTE──> PROCESS
  4. FORK + EXECUTE 是配对的 (73.9%的FORK子进程随后EXECUTE)
  5. CLONE 子进程从不 EXECUTE（创建并行工作进程）
  6. Subject.name 100%填充，无需从Event更新
  7. Event.exec 和 Event.cmdLine 均为 0%
  8. EXECUTE 不反转（进程→进程，不是文件→进程）

丢弃的实体: SUBJECT_UNIT, SrcSinkObject(SRCSINK_UNKNOWN), MemoryObject, UnnamedPipeObject
保留的实体: SUBJECT_PROCESS, FileObject(FILE/DIR), NetFlowObject

用法：
  python extract_trace_e3.py --input_dir /mnt/disk/darpa/trace_e3 --output_dir ./output
"""

import json
import os
import time
import argparse
import pickle
from collections import Counter, defaultdict

# ============================================================================
# 配置
# ============================================================================

def build_file_list():
    """按正确顺序构建文件列表"""
    files = []
    # official 系列: .json, .json.1 ~ .json.203
    files.append('ta1-trace-e3-official.json')
    for i in range(1, 204):
        files.append(f'ta1-trace-e3-official.json.{i}')
    # official-1 系列: .json, .json.1 ~ .json.6
    files.append('ta1-trace-e3-official-1.json')
    for i in range(1, 7):
        files.append(f'ta1-trace-e3-official-1.json.{i}')
    return files

FILE_LIST = build_file_list()

# 保留的边类型
INCLUDE_EDGE_TYPE = {
    'EVENT_READ',
    'EVENT_WRITE',
    'EVENT_EXECUTE',        # 旧进程→新进程（不反转）
    'EVENT_FORK',           # 传统fork+exec
    'EVENT_CLONE',          # 创建并行工作进程
    'EVENT_OPEN',
    'EVENT_CONNECT',
    'EVENT_SENDTO',
    'EVENT_RECVFROM',
    'EVENT_SENDMSG',
    'EVENT_RECVMSG',
    'EVENT_UNLINK',
    'EVENT_RENAME',
    'EVENT_CREATE_OBJECT',
    'EVENT_LOADLIBRARY',    # TRACE特有：映射为exec
    'EVENT_ACCEPT',
}

# 反转的边类型
# 注意：EVENT_EXECUTE 不反转（进程→进程，不是文件→进程）
EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_OPEN',
    'EVENT_LOADLIBRARY',    # 类似EXECUTE但加载库文件
    'EVENT_ACCEPT',
}


# ============================================================================
# Pass 1: 实体收集 + cmdLine收集
# ============================================================================

def pass1_collect(input_dir):
    """
    收集所有 SUBJECT_PROCESS 和 FileObject/NetFlowObject。
    同时收集每个进程的EXECUTE dst的Subject.cmdLine（用于FORK边回填）。

    TRACE特殊处理：
    - SUBJECT_UNIT 丢弃（411,355个执行单元，非真实进程）
    - EXECUTE的dst是Subject（新UUID），收集其cmdLine
    - Subject.name 作为进程名（100%填充，无需Event更新）
    """
    uuid2name = {}
    # uuid → 该进程EXECUTE dst的Subject.cmdLine（用于FORK边cmdLine回填）
    uuid_execute_dst_cmdline = {}
    # uuid → 该进程自己的Subject.cmdLine（用于CLONE边cmdLine / FORK fallback）
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
                if loaded_line % 1000000 == 0:
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
                        name = pm.get('name')
                        uuid2name[uid] = ['process', name]

                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        if cmdline:
                            uuid_subject_cmdline[uid] = cmdline
                    # SUBJECT_UNIT → 丢弃

                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    base = datum.get('baseObject', {})
                    bp = base.get('properties', {})
                    bpm = bp.get('map', {}) if isinstance(bp, dict) else {}
                    if not isinstance(bpm, dict): bpm = {}
                    path = bpm.get('path')
                    uuid2name[uid] = ['file', path]

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]

                elif rtype == 'Event':
                    etype = datum.get('type', '')
                    if etype == 'EVENT_EXECUTE':
                        src = None
                        if isinstance(datum.get('subject'), dict):
                            src = list(datum['subject'].values())[0]
                        dst = None
                        if isinstance(datum.get('predicateObject'), dict):
                            dst = list(datum['predicateObject'].values())[0]

                        # 收集EXECUTE dst的cmdLine（用于FORK边回填）
                        if src and dst and src not in uuid_execute_dst_cmdline:
                            dst_cmdline = uuid_subject_cmdline.get(dst)
                            if dst_cmdline:
                                uuid_execute_dst_cmdline[src] = dst_cmdline

                # SrcSinkObject / MemoryObject / UnnamedPipeObject → 丢弃

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
    print(f"  有EXECUTE dst cmdLine的进程: {len(uuid_execute_dst_cmdline):,}")
    print(f"  有Subject cmdLine的进程: {len(uuid_subject_cmdline):,}")

    return uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline


# ============================================================================
# Pass 2: 边提取
# ============================================================================

def pass2_extract_edges(input_dir, uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline):
    """
    TRACE特殊边处理：
    - FORK cmdLine: EXECUTE dst的Subject.cmdLine（73.9%配对）; fallback子进程Subject.cmdLine
    - CLONE cmdLine: 子进程Subject.cmdLine（CLONE子进程从不EXECUTE）
    - EXECUTE cmdLine: dst Subject.cmdLine（新身份的命令行）
    - EXECUTE 不反转（旧进程→新进程）
    - EVENT_RENAME 使用 predicateObject2
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

                # cmdLine处理
                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    # EXECUTE cmdLine = dst Subject.cmdLine（新身份的命令行）
                    cmdline = uuid_subject_cmdline.get(dst, None)
                elif eventtype == 'EVENT_FORK':
                    # FORK cmdLine = 子进程EXECUTE dst的cmdLine; fallback子进程自己的cmdLine
                    cmdline = uuid_execute_dst_cmdline.get(dst, None)
                    if cmdline is None:
                        cmdline = uuid_subject_cmdline.get(dst, None)
                elif eventtype == 'EVENT_CLONE':
                    # CLONE cmdLine = 子进程Subject.cmdLine（CLONE子进程不exec）
                    cmdline = uuid_subject_cmdline.get(dst, None)

                # 确定实际目标
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

                # 边方向反转（EXECUTE不反转）
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
    print(f"  过滤(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过(节点不存在):   {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return datalist


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("TRACE E3 数据提取")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    print(f"\n{'='*60}")
    print("Pass 1: 实体收集 + cmdLine收集")
    print(f"{'='*60}")
    uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline = pass1_collect(args.input_dir)

    print(f"\n{'='*60}")
    print("Pass 2: 边提取")
    print(f"{'='*60}")
    datalist = pass2_extract_edges(args.input_dir, uuid2name, uuid_execute_dst_cmdline, uuid_subject_cmdline)

    print(f"\n{'='*60}")
    print("保存结果")
    print(f"{'='*60}")

    with open(os.path.join(args.output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    with open(os.path.join(args.output_dir, 'datalist.pkl'), 'wb') as f:
        pickle.dump(datalist, f)

    csv_path = os.path.join(args.output_dir, 'edges.csv')
    with open(csv_path, 'w') as f:
        f.write('timestamp,event_type,src_uuid,src_type,src_name,dst_uuid,dst_type,dst_name,cmdline\n')
        for row in datalist[:100000]:
            fields = [str(x) if x is not None else '' for x in row]
            fields = [field.replace(',', ';') for field in fields]
            f.write(','.join(fields) + '\n')

    print(f"  已保存: uuid2name.pkl, datalist.pkl, edges.csv")
    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="TRACE E3 Data Extractor")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/trace_e3")
    parser.add_argument("--output_dir", type=str, default="./output_trace_e3")
    args = parser.parse_args()
    main(args)
