"""
CADETS E3 数据提取脚本（最终版）

基于对 44,404,339 行原始CDM数据的统计分析，设计的数据提取方案。

核心设计决策及数据依据：
  1. 实体来源：从实体记录本身创建（Subject/FileObject/NetFlowObject）
  2. 实体命名：从Event动态获取（CADETS E3实体记录的名称字段填充率为0%）
     - 进程名：Event.properties.map.exec（99.9%的Event有此字段）
     - 文件路径：Event.predicateObjectPath（44.2%的Event有此字段）
     - 网络地址：NetFlowObject记录本身（100%填充）
  3. 进程使用最终exec名（89.5%的进程经历 fork继承名→exec真实名 的变化）
  4. cmdLine作为边属性（仅0.5%的Event有cmdLine，全部在EVENT_EXECUTE上）
     - 同时放在FORK边上（子进程的cmdLine回填到fork边）
  5. EVENT_RENAME使用predicateObject2作为目标
  6. 边方向反转为数据流方向（READ/RECV类反转）

实体过滤依据：
  - FILE_OBJECT_UNIX_SOCKET 丢弃：227,869个匿名节点，0.003%为真正IPC，0攻击关联
  - UnnamedPipeObject 丢弃：名字全为<unknown>，四个项目均丢弃
  - SrcSinkObject 丢弃：0条Event引用，完全孤立

边过滤依据：
  - EVENT_FCNTL 丢弃：534,835条中99.6%无有效predicateObject
  - EVENT_CLOSE 丢弃：702,588条，信息量低（关闭文件句柄）
  - EVENT_LSEEK 丢弃：438,421条，文件指针移动，噪声
  - EVENT_MMAP 丢弃：526,854条，内存映射，量大噪声高
  - EVENT_MODIFY_PROCESS 丢弃：106,153条，含义模糊
  - EVENT_LOGIN/MPROTECT 丢弃：无predicateObject
  - EVENT_OTHER/FLOWS_TO/ADD_OBJECT_ATTRIBUTE 丢弃：无效或CDM内部标记

用法：
  python extract_cadets_e3.py --input_dir /mnt/disk/darpa/cadets_e3 --output_dir ./output
"""

import json
import os
import sys
import time
import argparse
from collections import Counter

# ============================================================================
# 配置
# ============================================================================

FILE_LIST = [
    'ta1-cadets-e3-official.json',
    'ta1-cadets-e3-official.json.1',
    'ta1-cadets-e3-official.json.2',
    'ta1-cadets-e3-official-1.json',
    'ta1-cadets-e3-official-1.json.1',
    'ta1-cadets-e3-official-1.json.2',
    'ta1-cadets-e3-official-1.json.3',
    'ta1-cadets-e3-official-1.json.4',
    'ta1-cadets-e3-official-2.json',
    'ta1-cadets-e3-official-2.json.1',
]

# 保留的边类型（13种）
INCLUDE_EDGE_TYPE = {
    'EVENT_READ',           # 进程读取文件/网络
    'EVENT_WRITE',          # 进程写入文件/网络
    'EVENT_EXECUTE',        # 进程加载可执行文件（带cmdLine）
    'EVENT_FORK',           # 进程创建子进程（带cmdLine）
    'EVENT_OPEN',           # 进程打开文件
    'EVENT_CONNECT',        # 进程发起网络连接
    'EVENT_SENDTO',         # 网络发送
    'EVENT_RECVFROM',       # 网络接收
    'EVENT_SENDMSG',        # 消息发送
    'EVENT_RECVMSG',        # 消息接收
    'EVENT_RENAME',         # 文件重命名（使用predicateObject2）
    'EVENT_UNLINK',         # 文件删除
    'EVENT_CREATE_OBJECT',  # 文件创建
}

# 反转的边类型（转为数据流方向）
# 原始: subject(进程) → predicateObject(对象)
# 反转后: 数据从对象流向进程
EDGE_REVERSED = {
    'EVENT_READ',       # 文件/网络 → 进程（数据流入进程）
    'EVENT_RECVFROM',   # 网络 → 进程
    'EVENT_RECVMSG',    # 网络 → 进程
    'EVENT_EXECUTE',    # 文件 → 进程（代码加载进进程）
    'EVENT_OPEN',       # 文件 → 进程（文件句柄流入进程）
}


# ============================================================================
# Pass 1: 实体收集 + exec/cmdLine/路径收集
# ============================================================================

def pass1_collect(input_dir):
    """
    单遍扫描所有文件，同时完成：
    1. 从实体记录创建uuid2name（name初始为None）
    2. 从Event收集每个进程的最终exec名
    3. 从Event收集每个进程的cmdLine（来自EVENT_EXECUTE）
    4. 从Event.predicateObjectPath更新文件路径

    由于CDM日志按时间顺序排列，实体记录出现在其Event之前，
    所以单遍即可完成实体创建和名称更新。
    """
    uuid2name = {}          # uuid → [type, name]
    uuid_cmdline = {}       # uuid → cmdLine (来自该uuid的第一个EVENT_EXECUTE)

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
                        uuid2name[datum['uuid']] = ['process', None]

                # ---------- FileObject ----------
                elif rtype == 'FileObject':
                    ftype = datum.get('type', '')
                    if ftype in ('FILE_OBJECT_FILE', 'FILE_OBJECT_DIR'):
                        uuid2name[datum['uuid']] = ['file', None]

                # ---------- NetFlowObject ----------
                elif rtype == 'NetFlowObject':
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[datum['uuid']] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]

                # ---------- Event ----------
                elif rtype == 'Event':
                    props = datum.get('properties', {}).get('map', {})

                    # 获取 subject uuid
                    src = None
                    if isinstance(datum.get('subject'), dict):
                        src = list(datum['subject'].values())[0]

                    # 获取 predicateObject uuid
                    dst = None
                    if isinstance(datum.get('predicateObject'), dict):
                        dst = list(datum['predicateObject'].values())[0]

                    # 1) 更新进程名（持续覆盖，最终为最后一个exec值）
                    if src and 'exec' in props:
                        if src in uuid2name and uuid2name[src][0] == 'process':
                            uuid2name[src][1] = props['exec']

                    # 2) 收集cmdLine（仅保留第一个EVENT_EXECUTE的）
                    if datum.get('type') == 'EVENT_EXECUTE' and src:
                        if src not in uuid_cmdline:
                            uuid_cmdline[src] = props.get('cmdLine', None)

                    # 3) 更新文件路径（predicateObjectPath）
                    if isinstance(datum.get('predicateObjectPath'), dict):
                        path = datum['predicateObjectPath'].get('string', '')
                        if path and dst and dst in uuid2name and uuid2name[dst][0] == 'file':
                            uuid2name[dst][1] = path

                    # 4) 更新文件路径（predicateObject2Path，用于RENAME等）
                    if isinstance(datum.get('predicateObject2Path'), dict):
                        dst2 = None
                        if isinstance(datum.get('predicateObject2'), dict):
                            dst2 = list(datum['predicateObject2'].values())[0]
                        path2 = datum['predicateObject2Path'].get('string', '')
                        if path2 and dst2 and dst2 in uuid2name and uuid2name[dst2][0] == 'file':
                            uuid2name[dst2][1] = path2

    elapsed = time.time() - begin
    print(f"\n  Pass 1 完成: {loaded_line:,} 行, {elapsed:.1f}s")

    # 统计
    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")
    print(f"  有cmdLine的进程: {len(uuid_cmdline):,}")

    return uuid2name, uuid_cmdline


# ============================================================================
# Pass 2: 边提取
# ============================================================================

def pass2_extract_edges(input_dir, uuid2name, uuid_cmdline):
    """
    第二遍扫描，提取所有边。

    每条边包含：
    (timestamp, event_type,
     src_uuid, src_type, src_name,
     dst_uuid, dst_type, dst_name,
     cmdline)

    特殊处理：
    - EVENT_EXECUTE: cmdLine从Event.properties.map.cmdLine获取
    - EVENT_FORK: cmdLine从子进程的uuid_cmdline获取（回填）
    - EVENT_RENAME: 目标使用predicateObject2
    - 边方向反转: READ/RECV/EXECUTE/OPEN类反转为数据流方向
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

                # 边类型过滤
                if eventtype not in INCLUDE_EDGE_TYPE:
                    skipped_filtered += 1
                    continue

                eventtime = datum['timestampNanos']
                props = datum.get('properties', {}).get('map', {})

                # 获取源/目标 uuid
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
                    cmdline = props.get('cmdLine', None)
                elif eventtype == 'EVENT_FORK':
                    cmdline = uuid_cmdline.get(dst, None)

                # 确定实际目标节点
                if eventtype == 'EVENT_RENAME':
                    actual_dst = dst2
                else:
                    actual_dst = dst

                # 验证两端节点存在
                if not src or not actual_dst:
                    skipped_no_node += 1
                    continue
                if src not in uuid2name or actual_dst not in uuid2name:
                    skipped_no_node += 1
                    continue

                # 边方向反转（转为数据流方向）
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
    print("CADETS E3 数据提取")
    print("=" * 60)

    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    # Pass 1
    print(f"\n{'='*60}")
    print("Pass 1: 实体收集 + 名称更新")
    print(f"{'='*60}")
    uuid2name, uuid_cmdline = pass1_collect(args.input_dir)

    # Pass 2
    print(f"\n{'='*60}")
    print("Pass 2: 边提取")
    print(f"{'='*60}")
    datalist = pass2_extract_edges(args.input_dir, uuid2name, uuid_cmdline)

    # 保存结果
    print(f"\n{'='*60}")
    print("保存结果")
    print(f"{'='*60}")

    import pickle
    uuid2name_path = os.path.join(args.output_dir, 'uuid2name.pkl')
    datalist_path = os.path.join(args.output_dir, 'datalist.pkl')

    with open(uuid2name_path, 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name 保存到: {uuid2name_path}")

    with open(datalist_path, 'wb') as f:
        pickle.dump(datalist, f)
    print(f"  datalist 保存到:  {datalist_path}")

    # 同时保存CSV方便查看
    csv_path = os.path.join(args.output_dir, 'edges.csv')
    with open(csv_path, 'w') as f:
        f.write('timestamp,event_type,src_uuid,src_type,src_name,dst_uuid,dst_type,dst_name,cmdline\n')
        for row in datalist[:100000]:  # 前10万条
            fields = [str(x) if x is not None else '' for x in row]
            # 简单转义逗号
            fields = [field.replace(',', ';') for field in fields]
            f.write(','.join(fields) + '\n')
    print(f"  CSV样本(前10万条): {csv_path}")

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")




if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="CADETS E3 Data Extractor")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/cadets_e3",
                        help="CADETS E3原始JSON文件目录")
    parser.add_argument("--output_dir", type=str,
                        default="./output_cadets_e3",
                        help="输出目录")
    args = parser.parse_args()
    main(args)
