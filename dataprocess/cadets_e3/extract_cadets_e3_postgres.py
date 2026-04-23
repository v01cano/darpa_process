"""
CADETS E3 数据提取脚本（PostgreSQL 版）

与 extract_cadets_e3.py 提取逻辑完全一致，区别在于将结果存入 PostgreSQL 数据库。
表结构参考 Orthrus 设计，但做了以下改动：
  1. subject_node_table: 去掉 cmd 列（cmdLine 不是实体属性，是边属性）
  2. event_table: 增加 cmdline 列（EXECUTE 和 FORK 边上的 cmdLine）
  3. 边方向已反转存储（READ/RECV/EXECUTE/OPEN 类）

数据库名: cadets_e3
需要先执行 init_database.sql 创建库和表。

用法：
  # 1. 创建数据库和表
  psql -U postgres -f init_database.sql

  # 2. 执行数据提取
  python extract_cadets_e3_postgres.py \
      --input_dir /mnt/disk/darpa/cadets_e3 \
      --db_name cadets_e3 \
      --db_user postgres \
      --db_password postgres \
      --db_host localhost \
      --db_port 5432
"""

import json
import os
import sys
import time
import hashlib
import argparse
from collections import Counter

import psycopg2
from psycopg2 import extras as ex


# ============================================================================
# 配置（与 extract_cadets_e3.py 完全一致）
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

INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_EXECUTE', 'EVENT_FORK',
    'EVENT_OPEN', 'EVENT_CONNECT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    'EVENT_RENAME', 'EVENT_UNLINK', 'EVENT_CREATE_OBJECT',
}

EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG',
    'EVENT_EXECUTE', 'EVENT_OPEN',
}


# ============================================================================
# 工具函数
# ============================================================================

def stringtomd5(originstr):
    """SHA256 hash（函数名沿用 Orthrus 命名习惯，实际是 SHA256）"""
    return hashlib.sha256(originstr.encode("utf-8")).hexdigest()


def init_database_connection(args):
    """建立数据库连接"""
    connect = psycopg2.connect(
        database=args.db_name,
        user=args.db_user,
        password=args.db_password,
        host=args.db_host,
        port=args.db_port,
    )
    cur = connect.cursor()
    return cur, connect


# ============================================================================
# Pass 1: 实体收集 + 名称更新 → 写入节点表
# ============================================================================

def pass1_collect_and_store(input_dir, cur, connect):
    """
    单遍扫描所有文件：
    1. 收集实体 UUID 并建立内存映射
    2. 从 Event 更新进程名（exec）、文件路径（predicateObjectPath）
    3. 收集 cmdLine（来自 EVENT_EXECUTE）

    扫描完成后批量写入 3 张节点表。
    """
    uuid2name = {}       # uuid → [type, name]
    uuid_cmdline = {}    # uuid → cmdLine
    uuid2index = {}      # uuid → index_id（用于 event_table 的 index_id 列）

    # 额外为数据库存储记录网络四元组
    netflow_info = {}    # uuid → (local_addr, local_port, remote_addr, remote_port)

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

                if rtype == 'Subject':
                    if datum['type'] == 'SUBJECT_PROCESS':
                        uuid2name[datum['uuid']] = ['process', None]

                elif rtype == 'FileObject':
                    ftype = datum.get('type', '')
                    if ftype in ('FILE_OBJECT_FILE', 'FILE_OBJECT_DIR'):
                        uuid2name[datum['uuid']] = ['file', None]

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (la, lp, ra, rp)

                elif rtype == 'Event':
                    props = datum.get('properties', {}).get('map', {})
                    src = None
                    if isinstance(datum.get('subject'), dict):
                        src = list(datum['subject'].values())[0]
                    dst = None
                    if isinstance(datum.get('predicateObject'), dict):
                        dst = list(datum['predicateObject'].values())[0]

                    # 更新进程名
                    if src and 'exec' in props:
                        if src in uuid2name and uuid2name[src][0] == 'process':
                            uuid2name[src][1] = props['exec']

                    # 收集 cmdLine
                    if datum.get('type') == 'EVENT_EXECUTE' and src:
                        if src not in uuid_cmdline:
                            uuid_cmdline[src] = props.get('cmdLine', None)

                    # 更新文件路径
                    if isinstance(datum.get('predicateObjectPath'), dict):
                        path = datum['predicateObjectPath'].get('string', '')
                        if path and dst and dst in uuid2name and uuid2name[dst][0] == 'file':
                            uuid2name[dst][1] = path

                    if isinstance(datum.get('predicateObject2Path'), dict):
                        dst2 = None
                        if isinstance(datum.get('predicateObject2'), dict):
                            dst2 = list(datum['predicateObject2'].values())[0]
                        path2 = datum['predicateObject2Path'].get('string', '')
                        if path2 and dst2 and dst2 in uuid2name and uuid2name[dst2][0] == 'file':
                            uuid2name[dst2][1] = path2

    elapsed = time.time() - begin
    print(f"\n  Pass 1 扫描完成: {loaded_line:,} 行, {elapsed:.1f}s")

    # ---- 写入数据库 ----
    print(f"\n  写入数据库...")

    # 分配 index_id 并写入节点表
    index_id = 0

    # 1) subject_node_table
    subject_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'process':
            hid = stringtomd5(uid)
            uuid2index[uid] = index_id
            subject_data.append((uid, hid, name, index_id))
            index_id += 1

    sql = "INSERT INTO subject_node_table VALUES %s"
    ex.execute_values(cur, sql, subject_data, page_size=10000)
    connect.commit()
    print(f"    subject_node_table: {len(subject_data):,} 条")

    # 2) file_node_table
    file_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'file':
            hid = stringtomd5(uid)
            uuid2index[uid] = index_id
            file_data.append((uid, hid, name, index_id))
            index_id += 1

    sql = "INSERT INTO file_node_table VALUES %s"
    ex.execute_values(cur, sql, file_data, page_size=10000)
    connect.commit()
    print(f"    file_node_table: {len(file_data):,} 条")

    # 3) netflow_node_table
    netflow_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'netflow':
            hid = stringtomd5(uid)
            uuid2index[uid] = index_id
            la, lp, ra, rp = netflow_info.get(uid, ('', '', '', ''))
            netflow_data.append((uid, hid, la, lp, ra, rp, index_id))
            index_id += 1

    sql = "INSERT INTO netflow_node_table VALUES %s"
    ex.execute_values(cur, sql, netflow_data, page_size=10000)
    connect.commit()
    print(f"    netflow_node_table: {len(netflow_data):,} 条")

    print(f"    总节点数: {index_id:,}")

    return uuid2name, uuid2index, uuid_cmdline


# ============================================================================
# Pass 2: 边提取 → 写入 event_table
# ============================================================================

def pass2_extract_and_store(input_dir, uuid2name, uuid2index, uuid_cmdline, cur, connect):
    """
    第二遍扫描，提取边并写入 event_table。

    event_table 列:
      src_uuid, src_index_id, operation, dst_uuid,
      dst_index_id, event_uuid, timestamp_rec, cmdline
    """
    datalist = []
    edge_type_count = Counter()
    skipped = 0
    batch_size = 50000

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
                    continue

                eventtime = datum['timestampNanos']
                event_uuid = datum.get('uuid', '')
                props = datum.get('properties', {}).get('map', {})

                src = ''
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = ''
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]
                dst2 = ''
                if isinstance(datum.get('predicateObject2'), dict):
                    dst2 = list(datum['predicateObject2'].values())[0]

                # cmdLine
                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    cmdline = props.get('cmdLine', None)
                elif eventtype == 'EVENT_FORK':
                    cmdline = uuid_cmdline.get(dst, None)

                # 确定目标
                actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst

                if not src or not actual_dst:
                    skipped += 1
                    continue
                if src not in uuid2index or actual_dst not in uuid2index:
                    skipped += 1
                    continue

                # 边方向反转
                if eventtype in EDGE_REVERSED:
                    edge_src_uuid, edge_dst_uuid = actual_dst, src
                else:
                    edge_src_uuid, edge_dst_uuid = src, actual_dst

                src_idx = uuid2index[edge_src_uuid]
                dst_idx = uuid2index[edge_dst_uuid]

                datalist.append((
                    edge_src_uuid, src_idx, eventtype,
                    edge_dst_uuid, dst_idx, event_uuid,
                    eventtime, cmdline,
                ))
                edge_type_count[eventtype] += 1

                # 批量写入
                if len(datalist) >= batch_size:
                    sql = "INSERT INTO event_table (src_uuid, src_index_id, operation, dst_uuid, dst_index_id, event_uuid, timestamp_rec, cmdline) VALUES %s"
                    ex.execute_values(cur, sql, datalist, page_size=batch_size)
                    connect.commit()
                    datalist = []

    # 写入剩余
    if datalist:
        sql = "INSERT INTO event_table (src_uuid, src_index_id, operation, dst_uuid, dst_index_id, event_uuid, timestamp_rec, cmdline) VALUES %s"
        ex.execute_values(cur, sql, datalist, page_size=batch_size)
        connect.commit()

    elapsed = time.time() - begin
    total_edges = sum(edge_type_count.values())
    print(f"\n  Pass 2 完成: {loaded_line:,} 行, {elapsed:.1f}s")
    print(f"  写入边数: {total_edges:,}")
    print(f"  跳过: {skipped:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return total_edges


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("CADETS E3 数据提取 (PostgreSQL)")
    print("=" * 60)

    print(f"\n输入目录: {args.input_dir}")
    print(f"数据库:   {args.db_name}@{args.db_host}:{args.db_port}")

    cur, connect = init_database_connection(args)

    # Pass 1
    print(f"\n{'='*60}")
    print("Pass 1: 实体收集 + 名称更新 → 写入节点表")
    print(f"{'='*60}")
    uuid2name, uuid2index, uuid_cmdline = pass1_collect_and_store(
        args.input_dir, cur, connect)

    # Pass 2
    print(f"\n{'='*60}")
    print("Pass 2: 边提取 → 写入 event_table")
    print(f"{'='*60}")
    total_edges = pass2_extract_and_store(
        args.input_dir, uuid2name, uuid2index, uuid_cmdline, cur, connect)

    cur.close()
    connect.close()

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="CADETS E3 Data Extractor (PostgreSQL)")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/cadets_e3")
    parser.add_argument("--db_name", type=str, default="cadets_e3")
    parser.add_argument("--db_user", type=str, default="postgres")
    parser.add_argument("--db_password", type=str, default="postgres")
    parser.add_argument("--db_host", type=str, default="localhost")
    parser.add_argument("--db_port", type=str, default="5432")
    args = parser.parse_args()
    main(args)
