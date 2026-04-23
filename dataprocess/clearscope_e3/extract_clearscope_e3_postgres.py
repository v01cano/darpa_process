"""
ClearScope E3 数据提取脚本（PostgreSQL 版）

与 extract_clearscope_e3.py 提取逻辑完全一致，结果存入 PostgreSQL。
表结构与 CADETS/THEIA E3 一致（统一设计）。

用法：
  psql -U postgres -f init_database.sql
  python extract_clearscope_e3_postgres.py --input_dir /mnt/disk/darpa/clearscope_e3
"""

import json
import os
import time
import hashlib
import argparse
from collections import Counter

import psycopg2
from psycopg2 import extras as ex

# ============================================================================
# 配置（与本地磁盘版一致）
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

INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN', 'EVENT_CONNECT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_CREATE_OBJECT',
}

EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG', 'EVENT_OPEN',
}


def stringtomd5(originstr):
    return hashlib.sha256(originstr.encode("utf-8")).hexdigest()


def init_database_connection(args):
    connect = psycopg2.connect(
        database=args.db_name, user=args.db_user,
        password=args.db_password, host=args.db_host, port=args.db_port,
    )
    return connect.cursor(), connect


def extract_and_store(input_dir, cur, connect):
    """单遍提取：收集实体 → 写入节点表 → 提取边 → 写入边表"""

    uuid2name = {}
    uuid2index = {}
    netflow_info = {}

    # ============ 第1遍：实体收集 ============
    loaded_line = 0
    begin = time.time()
    print("  第1遍：实体收集...")

    for volume_name in FILE_LIST:
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            continue
        print(f"    处理: {volume_name}")

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"      已扫描 {loaded_line:,} 行...")

                record = json.loads(line)['datum']
                rtype_full = list(record.keys())[0]
                datum = record[rtype_full]
                rtype = rtype_full.split('.')[-1]

                if rtype == 'Subject':
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = datum['uuid']
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        uuid2name[uid] = ['process', cmdline]

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

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (la, lp, ra, rp)

    print(f"  第1遍完成: {loaded_line:,} 行, {time.time()-begin:.1f}s")

    # 写入节点表
    print("  写入节点表...")
    index_id = 0

    subject_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'process':
            uuid2index[uid] = index_id
            subject_data.append((uid, stringtomd5(uid), name, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO subject_node_table VALUES %s", subject_data, page_size=10000)
    connect.commit()
    print(f"    subject_node_table: {len(subject_data):,}")

    file_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'file':
            uuid2index[uid] = index_id
            file_data.append((uid, stringtomd5(uid), name, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO file_node_table VALUES %s", file_data, page_size=10000)
    connect.commit()
    print(f"    file_node_table: {len(file_data):,}")

    netflow_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'netflow':
            uuid2index[uid] = index_id
            la, lp, ra, rp = netflow_info.get(uid, ('', '', '', ''))
            netflow_data.append((uid, stringtomd5(uid), la, lp, ra, rp, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO netflow_node_table VALUES %s", netflow_data, page_size=10000)
    connect.commit()
    print(f"    netflow_node_table: {len(netflow_data):,}")
    print(f"    总节点数: {index_id:,}")

    # ============ 第2遍：边提取 ============
    datalist = []
    edge_type_count = Counter()
    skipped = 0
    batch_size = 50000

    loaded_line = 0
    begin = time.time()
    print(f"\n  第2遍：边提取...")

    for volume_name in FILE_LIST:
        volume_path = os.path.join(input_dir, volume_name)
        if not os.path.exists(volume_path):
            continue
        print(f"    处理: {volume_name}")

        with open(volume_path, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"      已扫描 {loaded_line:,} 行...")

                record = json.loads(line)['datum']
                rtype_full = list(record.keys())[0]
                datum = record[rtype_full]
                rtype = rtype_full.split('.')[-1]

                if rtype != 'Event':
                    continue

                eventtype = datum.get('type', '')
                if eventtype not in INCLUDE_EDGE_TYPE:
                    continue

                eventtime = datum.get('timestampNanos', 0)
                event_uuid = datum.get('uuid', '')

                src = ''
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = ''
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]
                dst2 = ''
                if isinstance(datum.get('predicateObject2'), dict):
                    dst2 = list(datum['predicateObject2'].values())[0]

                actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst

                if not src or not actual_dst:
                    skipped += 1
                    continue
                if src not in uuid2index or actual_dst not in uuid2index:
                    skipped += 1
                    continue

                if eventtype in EDGE_REVERSED:
                    edge_src, edge_dst = actual_dst, src
                else:
                    edge_src, edge_dst = src, actual_dst

                datalist.append((
                    edge_src, uuid2index[edge_src], eventtype,
                    edge_dst, uuid2index[edge_dst], event_uuid,
                    eventtime, None,  # cmdLine 永远为 None
                ))
                edge_type_count[eventtype] += 1

                if len(datalist) >= batch_size:
                    sql = "INSERT INTO event_table (src_uuid, src_index_id, operation, dst_uuid, dst_index_id, event_uuid, timestamp_rec, cmdline) VALUES %s"
                    ex.execute_values(cur, sql, datalist, page_size=batch_size)
                    connect.commit()
                    datalist = []

    if datalist:
        sql = "INSERT INTO event_table (src_uuid, src_index_id, operation, dst_uuid, dst_index_id, event_uuid, timestamp_rec, cmdline) VALUES %s"
        ex.execute_values(cur, sql, datalist, page_size=batch_size)
        connect.commit()

    total_edges = sum(edge_type_count.values())
    print(f"  第2遍完成: {loaded_line:,} 行, {time.time()-begin:.1f}s")
    print(f"  写入边数: {total_edges:,}")
    print(f"  跳过: {skipped:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")


def main(args):
    print("=" * 60)
    print("ClearScope E3 数据提取 (PostgreSQL)")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"数据库:   {args.db_name}@{args.db_host}:{args.db_port}")

    cur, connect = init_database_connection(args)
    extract_and_store(args.input_dir, cur, connect)
    cur.close()
    connect.close()

    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="ClearScope E3 Data Extractor (PostgreSQL)")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/clearscope_e3")
    parser.add_argument("--db_name", type=str, default="clearscope_e3")
    parser.add_argument("--db_user", type=str, default="postgres")
    parser.add_argument("--db_password", type=str, default="postgres")
    parser.add_argument("--db_host", type=str, default="localhost")
    parser.add_argument("--db_port", type=str, default="5432")
    args = parser.parse_args()
    main(args)
