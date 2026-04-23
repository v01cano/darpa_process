"""
THEIA E3 数据提取脚本（PostgreSQL 版）

与 extract_theia_e3.py 提取逻辑完全一致，结果存入 PostgreSQL。
表结构与 CADETS E3 postgres 版一致。

用法：
  psql -U postgres -f init_database.sql
  python extract_theia_e3_postgres.py --input_dir /mnt/disk/darpa/theia_e3
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

INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_EXECUTE', 'EVENT_CLONE',
    'EVENT_OPEN', 'EVENT_CONNECT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    'EVENT_UNLINK',
}

EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG',
    'EVENT_EXECUTE', 'EVENT_OPEN',
}


def stringtomd5(originstr):
    return hashlib.sha256(originstr.encode("utf-8")).hexdigest()


def init_database_connection(args):
    connect = psycopg2.connect(
        database=args.db_name, user=args.db_user,
        password=args.db_password, host=args.db_host, port=args.db_port,
    )
    return connect.cursor(), connect


# ============================================================================
# Pass 1
# ============================================================================

def pass1_collect_and_store(input_dir, cur, connect):
    uuid2name = {}
    uuid2index = {}
    uuid_exec_cmdline = {}
    uuid_subject_cmdline = {}
    netflow_info = {}

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
                    if datum['type'] == 'SUBJECT_PROCESS':
                        uid = datum['uuid']
                        props = datum.get('properties', {}).get('map', {})
                        path = props.get('path')
                        uuid2name[uid] = ['process', path]
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        if cmdline:
                            uuid_subject_cmdline[uid] = cmdline

                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    base_props = datum.get('baseObject', {}).get('properties', {}).get('map', {})
                    filename = base_props.get('filename')
                    uuid2name[uid] = ['file', filename]

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (la, lp, ra, rp)

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
                        if dst in uuid2name and uuid2name[dst][0] == 'file':
                            dst_filename = uuid2name[dst][1]
                            if dst_filename and src in uuid2name and uuid2name[src][0] == 'process':
                                uuid2name[src][1] = dst_filename
                        if src not in uuid_exec_cmdline:
                            event_cmdline = props.get('cmdLine')
                            if event_cmdline:
                                uuid_exec_cmdline[src] = event_cmdline

    elapsed = time.time() - begin
    print(f"\n  Pass 1 扫描完成: {loaded_line:,} 行, {elapsed:.1f}s")

    # 写入数据库
    print(f"\n  写入数据库...")
    index_id = 0

    # subject_node_table
    subject_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'process':
            hid = stringtomd5(uid)
            uuid2index[uid] = index_id
            subject_data.append((uid, hid, name, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO subject_node_table VALUES %s", subject_data, page_size=10000)
    connect.commit()
    print(f"    subject_node_table: {len(subject_data):,}")

    # file_node_table
    file_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'file':
            hid = stringtomd5(uid)
            uuid2index[uid] = index_id
            file_data.append((uid, hid, name, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO file_node_table VALUES %s", file_data, page_size=10000)
    connect.commit()
    print(f"    file_node_table: {len(file_data):,}")

    # netflow_node_table
    netflow_data = []
    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'netflow':
            hid = stringtomd5(uid)
            uuid2index[uid] = index_id
            la, lp, ra, rp = netflow_info.get(uid, ('', '', '', ''))
            netflow_data.append((uid, hid, la, lp, ra, rp, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO netflow_node_table VALUES %s", netflow_data, page_size=10000)
    connect.commit()
    print(f"    netflow_node_table: {len(netflow_data):,}")
    print(f"    总节点数: {index_id:,}")

    return uuid2name, uuid2index, uuid_exec_cmdline, uuid_subject_cmdline


# ============================================================================
# Pass 2
# ============================================================================

def pass2_extract_and_store(input_dir, uuid2name, uuid2index, uuid_exec_cmdline, uuid_subject_cmdline, cur, connect):
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

                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    cmdline = props.get('cmdLine', None)
                elif eventtype == 'EVENT_CLONE':
                    cmdline = uuid_exec_cmdline.get(dst, None)
                    if cmdline is None:
                        cmdline = uuid_subject_cmdline.get(dst, None)

                if not src or not dst:
                    skipped += 1
                    continue
                if src not in uuid2index or dst not in uuid2index:
                    skipped += 1
                    continue

                if eventtype in EDGE_REVERSED:
                    edge_src, edge_dst = dst, src
                else:
                    edge_src, edge_dst = src, dst

                datalist.append((
                    edge_src, uuid2index[edge_src], eventtype,
                    edge_dst, uuid2index[edge_dst], event_uuid,
                    eventtime, cmdline,
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
    print("THEIA E3 数据提取 (PostgreSQL)")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"数据库:   {args.db_name}@{args.db_host}:{args.db_port}")

    cur, connect = init_database_connection(args)

    print(f"\n{'='*60}")
    print("Pass 1: 实体收集 + 进程名更新 → 写入节点表")
    print(f"{'='*60}")
    uuid2name, uuid2index, uuid_exec_cmdline, uuid_subject_cmdline = pass1_collect_and_store(
        args.input_dir, cur, connect)

    print(f"\n{'='*60}")
    print("Pass 2: 边提取 → 写入 event_table")
    print(f"{'='*60}")
    pass2_extract_and_store(args.input_dir, uuid2name, uuid2index,
                            uuid_exec_cmdline, uuid_subject_cmdline, cur, connect)

    cur.close()
    connect.close()
    print(f"\n{'='*60}")
    print("完成")
    print(f"{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="THEIA E3 Data Extractor (PostgreSQL)")
    parser.add_argument("--input_dir", type=str, default="/mnt/disk/darpa/theia_e3")
    parser.add_argument("--db_name", type=str, default="theia_e3")
    parser.add_argument("--db_user", type=str, default="postgres")
    parser.add_argument("--db_password", type=str, default="postgres")
    parser.add_argument("--db_host", type=str, default="localhost")
    parser.add_argument("--db_port", type=str, default="5432")
    args = parser.parse_args()
    main(args)
