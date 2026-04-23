"""
FiveDirections E3 数据提取脚本（本地磁盘版，分片输出）

Windows 平台数据集，CDM18 格式。

核心设计：
  1. SUBJECT_THREAD UUID 合并到父 SUBJECT_PROCESS UUID（方案B）
     - THREAD 不作为独立节点
     - 所有线程发起的事件通过 UUID 替换归属到父进程
  2. 节点 name：
     - PROCESS: EVENT_EXECUTE.predicateObjectPath（主，如"svchost.exe"）
               或从 Subject.cmdLine 提取可执行文件名（辅）
     - RegistryKeyObject: datum['key']，归入 'file' 类型
     - FileObject: Event.predicateObjectPath
  3. cmdLine 放在 FORK/EXECUTE 边上：
     - FORK 边 cmdLine = dst 进程的 Subject.cmdLine
     - EXECUTE 边 cmdLine = src 进程的 Subject.cmdLine
  4. EVENT_CREATE_THREAD 丢弃（UUID 合并后变自环）
  5. EXECUTE/LOADLIBRARY 反转（file → process）

输出：
  - uuid2name.pkl          节点映射
  - cmdlines.pkl           完整cmdLine（供续跑使用）
  - edges_part_XXX.pkl     分片边文件（每 batch_size 个文件一片）
  - edges.csv              样本CSV（前10万条）

用法：
  python extract_fivedirections_e3.py \
      --input_dir /mnt/disk/darpa/fivedirections_e3 \
      --output_dir /mnt/disk/darpa/fivedirections_e3_output \
      --batch_size 10
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

def build_file_list():
    """按顺序构建文件列表"""
    files = []
    files.append('ta1-fivedirections-e3-official.json')
    files.append('ta1-fivedirections-e3-official-2.json')
    for i in range(1, 53):
        files.append(f'ta1-fivedirections-e3-official-2.json.{i}')
    files.append('ta1-fivedirections-e3-official-3.json')
    return files

FILE_LIST = build_file_list()

# 保留的 15 种边类型
INCLUDE_EDGE_TYPE = {
    # 文件操作
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_CREATE_OBJECT',
    'EVENT_MODIFY_FILE_ATTRIBUTES',
    # 进程创建 + 加载
    'EVENT_FORK',
    'EVENT_EXECUTE',
    'EVENT_LOADLIBRARY',
    # 网络
    'EVENT_CONNECT', 'EVENT_SENDTO', 'EVENT_RECVFROM',
    'EVENT_SENDMSG', 'EVENT_RECVMSG', 'EVENT_ACCEPT', 'EVENT_BIND',
}

# 反转的 7 种边类型（转为数据流方向）
EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_OPEN',
    'EVENT_EXECUTE',     # PE 文件 → 进程
    'EVENT_LOADLIBRARY', # DLL → 进程
    'EVENT_ACCEPT',
}


# ============================================================================
# 工具函数
# ============================================================================

def extract_exe_name(cmdline):
    """从完整 cmdLine 提取可执行文件名（无路径无参数）"""
    if not cmdline:
        return None
    cmd = cmdline.strip().strip('"').strip("'")
    # 处理引号包围的路径
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        first_token = cmd[1:end] if end > 0 else cmd[1:]
    else:
        first_token = cmd.split(' ', 1)[0]
    # 提取文件名（Windows反斜杠 或 Unix正斜杠）
    if '\\' in first_token:
        name = first_token.rsplit('\\', 1)[-1]
    elif '/' in first_token:
        name = first_token.rsplit('/', 1)[-1]
    else:
        name = first_token
    return name.lower() if name else None


# ============================================================================
# Pass 1: 实体收集 + cmdLine收集 + THREAD映射
# ============================================================================

def pass1_collect(input_dir):
    """
    单遍扫描所有文件：
    1. 收集 PROCESS → uuid2name[pid] = ['process', None]（name待补）
    2. 收集 THREAD → thread_to_process 映射（不加入uuid2name）
    3. 收集 FileObject → uuid2name[fid] = ['file', None]
    4. 收集 NetFlowObject → uuid2name[nid] = ['netflow', "la:lp->ra:rp"]
    5. 收集 RegistryKeyObject → uuid2name[rid] = ['file', key]
    6. 收集每个 PROCESS 的 Subject.cmdLine
    7. 从 EVENT_EXECUTE.predicateObjectPath 获取程序名（给PROCESS作为name）
    8. 从 Event.predicateObjectPath 更新 FileObject 的 path
    """
    uuid2name = {}
    thread_to_process = {}           # THREAD UUID → 父 PROCESS UUID
    subject_cmdline = {}              # PROCESS UUID → Subject.cmdLine（完整命令行）
    uuid_execute_path = {}            # PROCESS UUID → EXECUTE path（简洁程序名）

    loaded_line = 0
    begin = time.time()

    for volume_name in FILE_LIST:
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            print(f"  WARNING: {volume_path} 不存在, 跳过")
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
                    uid = datum['uuid']
                    stype = datum.get('type', '')
                    if stype == 'SUBJECT_PROCESS':
                        uuid2name[uid] = ['process', None]  # name 后续补
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        if cmdline:
                            subject_cmdline[uid] = cmdline
                    elif stype == 'SUBJECT_THREAD':
                        # 建立 THREAD → 父 PROCESS 映射
                        if isinstance(datum.get('parentSubject'), dict):
                            parent = list(datum['parentSubject'].values())[0]
                            thread_to_process[uid] = parent

                elif rtype == 'FileObject':
                    uuid2name[datum['uuid']] = ['file', None]

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]

                elif rtype == 'RegistryKeyObject':
                    # Registry 归入 file 类型
                    uid = datum['uuid']
                    key = datum.get('key')
                    uuid2name[uid] = ['file', key]

                # MemoryObject / SrcSinkObject / PacketSocketObject → 丢弃

                elif rtype == 'Event':
                    etype = datum.get('type', '')

                    # 收集 EVENT_EXECUTE 的程序名
                    if etype == 'EVENT_EXECUTE':
                        src = None
                        if isinstance(datum.get('subject'), dict):
                            src = list(datum['subject'].values())[0]
                        path = None
                        if isinstance(datum.get('predicateObjectPath'), dict):
                            path = datum['predicateObjectPath'].get('string')
                        if src and path and src not in uuid_execute_path:
                            uuid_execute_path[src] = path.lower()

                    # 更新 FileObject 的 path（从 Event.predicateObjectPath）
                    if isinstance(datum.get('predicateObjectPath'), dict):
                        path = datum['predicateObjectPath'].get('string')
                        dst = None
                        if isinstance(datum.get('predicateObject'), dict):
                            dst = list(datum['predicateObject'].values())[0]
                        if path and dst and dst in uuid2name:
                            ntype, nname = uuid2name[dst]
                            # 只更新 file 类型且未设置 name 的节点
                            # Registry 节点的 name 已经是 key，不覆盖
                            if ntype == 'file' and nname is None:
                                uuid2name[dst][1] = path

                    # predicateObject2Path 更新（用于 RENAME 等）
                    if isinstance(datum.get('predicateObject2Path'), dict):
                        path2 = datum['predicateObject2Path'].get('string')
                        dst2 = None
                        if isinstance(datum.get('predicateObject2'), dict):
                            dst2 = list(datum['predicateObject2'].values())[0]
                        if path2 and dst2 and dst2 in uuid2name:
                            ntype, nname = uuid2name[dst2]
                            if ntype == 'file' and nname is None:
                                uuid2name[dst2][1] = path2

    elapsed = time.time() - begin
    print(f"\n  Pass 1 扫描完成: {loaded_line:,} 行, {elapsed:.1f}s")

    # ============ Pass 1 结束：为每个 PROCESS 确定 name ============
    process_named_by_execute = 0
    process_named_by_cmdline = 0
    process_no_name = 0

    for uid, entry in uuid2name.items():
        if entry[0] != 'process':
            continue
        # 优先：EXECUTE path
        if uid in uuid_execute_path:
            uuid2name[uid][1] = uuid_execute_path[uid]
            process_named_by_execute += 1
        else:
            # Fallback：从 Subject.cmdLine 提取
            cmdline = subject_cmdline.get(uid)
            name = extract_exe_name(cmdline)
            if name:
                uuid2name[uid][1] = name
                process_named_by_cmdline += 1
            else:
                process_no_name += 1

    # 统计
    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"\n  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")
    print(f"\n  THREAD 映射: {len(thread_to_process):,} 个")
    print(f"  有 Subject.cmdLine 的 PROCESS: {len(subject_cmdline):,}")
    print(f"  PROCESS 命名方式:")
    print(f"    EXECUTE path: {process_named_by_execute:,}")
    print(f"    cmdLine 提取:  {process_named_by_cmdline:,}")
    print(f"    无名字:        {process_no_name:,}")

    return uuid2name, thread_to_process, subject_cmdline


# ============================================================================
# Pass 2: 边提取（UUID 替换 + 反转 + cmdLine）
# ============================================================================

def pass2_extract_edges(input_dir, output_dir, uuid2name, thread_to_process,
                        subject_cmdline, batch_size):
    """
    第二遍扫描：
    - UUID 替换：THREAD → 父 PROCESS
    - 边类型过滤
    - 方向反转
    - cmdLine 处理：FORK/EXECUTE 边上
    - 每 batch_size 个文件保存一个分片
    """
    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0
    total_edges = 0

    # CSV 样本（前10万）
    csv_path = os.path.join(output_dir, 'edges.csv')
    csv_file = open(csv_path, 'w')
    csv_file.write('timestamp,event_type,src_uuid,src_type,src_name,'
                   'dst_uuid,dst_type,dst_name,cmdline\n')
    csv_written = 0
    csv_limit = 100000

    # 批处理状态
    batch_edges = []
    batch_file_count = 0
    batch_start_index = 0

    loaded_line = 0
    begin = time.time()

    existing_files = [f for f in FILE_LIST if os.path.exists(os.path.join(input_dir, f))]
    print(f"  共处理 {len(existing_files)} 个文件，每 {batch_size} 个保存一片")

    for file_idx, volume_name in enumerate(existing_files):
        volume_path = os.path.join(input_dir, volume_name)
        print(f"  [{file_idx}] 处理: {volume_name}")

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 2000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... "
                          f"(当前批次边数: {len(batch_edges):,})")

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

                # EVENT_RENAME 使用 predicateObject2
                actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst

                # ============ 关键步骤：UUID 替换（THREAD → PROCESS）============
                if src in thread_to_process:
                    src = thread_to_process[src]
                if actual_dst in thread_to_process:
                    actual_dst = thread_to_process[actual_dst]

                # 跳过自环（如 CREATE_THREAD 替换后自己→自己）
                if src == actual_dst:
                    skipped_no_node += 1
                    continue

                if not src or not actual_dst:
                    skipped_no_node += 1
                    continue
                if src not in uuid2name or actual_dst not in uuid2name:
                    skipped_no_node += 1
                    continue

                # ============ cmdLine 处理 ============
                cmdline = None
                if eventtype == 'EVENT_FORK':
                    # FORK 边：dst 进程的 Subject.cmdLine
                    cmdline = subject_cmdline.get(actual_dst)
                elif eventtype == 'EVENT_EXECUTE':
                    # EXECUTE 边：src 进程的 Subject.cmdLine
                    cmdline = subject_cmdline.get(src)

                # ============ 边方向反转 ============
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

                # CSV 样本写入
                if csv_written < csv_limit:
                    fields = [str(x) if x is not None else '' for x in edge]
                    fields = [f.replace(',', ';') for f in fields]
                    csv_file.write(','.join(fields) + '\n')
                    csv_written += 1

        batch_file_count += 1

        # 达到 batch_size 或最后一个文件 → 写入分片
        is_last = (file_idx == len(existing_files) - 1)
        if batch_file_count >= batch_size or is_last:
            if batch_edges:
                part_path = os.path.join(output_dir,
                                         f'edges_part_{batch_start_index:03d}.pkl')
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

    csv_file.close()

    elapsed = time.time() - begin
    print(f"\n  Pass 2 完成: {loaded_line:,} 行, {elapsed:.1f}s")
    print(f"  总边数: {total_edges:,}")
    print(f"  过滤(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过(节点不存在/自环): {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return total_edges


# ============================================================================
# 加载工具（供下游使用）
# ============================================================================

def load_all_edges(output_dir):
    """从分片文件加载全部边（需要足够内存）"""
    import glob
    all_edges = []
    for part_file in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(part_file, 'rb') as f:
            edges = pickle.load(f)
            all_edges.extend(edges)
    return all_edges


def iter_edges(output_dir):
    """迭代器方式流式读取边"""
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
    print("FiveDirections E3 数据提取")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    print(f"分片大小: {args.batch_size}")
    os.makedirs(args.output_dir, exist_ok=True)

    # Pass 1
    print(f"\n{'='*60}")
    print("Pass 1: 实体收集 + THREAD 映射 + 命名")
    print(f"{'='*60}")
    uuid2name, thread_to_process, subject_cmdline = pass1_collect(args.input_dir)

    # 保存 uuid2name 和 cmdlines（供续跑使用）
    with open(os.path.join(args.output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    with open(os.path.join(args.output_dir, 'cmdlines.pkl'), 'wb') as f:
        pickle.dump({
            'thread_to_process': thread_to_process,
            'subject_cmdline': subject_cmdline,
        }, f)
    print(f"\n  uuid2name.pkl 已保存 ({len(uuid2name):,} 个实体)")
    print(f"  cmdlines.pkl 已保存 (thread映射 {len(thread_to_process):,}, cmdLine {len(subject_cmdline):,})")

    # Pass 2
    print(f"\n{'='*60}")
    print("Pass 2: 边提取（UUID 替换 + 反转 + cmdLine）")
    print(f"{'='*60}")
    pass2_extract_edges(args.input_dir, args.output_dir, uuid2name,
                         thread_to_process, subject_cmdline, args.batch_size)

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")
    print(f"  输出目录: {args.output_dir}")
    print(f"  uuid2name.pkl — 节点映射")
    print(f"  cmdlines.pkl — 线程映射与cmdLine")
    print(f"  edges_part_XXX.pkl — 分片边文件")
    print(f"  edges.csv — CSV样本（前10万条）")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FiveDirections E3 Data Extractor")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/fivedirections_e3")
    parser.add_argument("--output_dir", type=str,
                        default="./output_fivedirections_e3")
    parser.add_argument("--batch_size", type=int, default=10,
                        help="每多少个文件保存一个分片（默认10）")
    args = parser.parse_args()
    main(args)
