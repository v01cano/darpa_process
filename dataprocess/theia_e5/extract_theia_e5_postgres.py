"""
THEIA E5 数据提取脚本（PostgreSQL 版）

与 extract_theia_e5.py 提取逻辑完全一致，结果直接写入 PostgreSQL。
表结构与其他 7 个数据集统一。

用法：
  psql -U postgres -f init_database.sql
  python extract_theia_e5_postgres.py --input_dir /mnt/disk/darpa/cch_refine/theia_e5_json
"""

import json
import os
import time
import hashlib
import argparse
from collections import Counter

import psycopg2
from psycopg2 import extras as ex

from extract_theia_e5 import (
    INCLUDE_EDGE_TYPE, EDGE_REVERSED,
    norm_uuid, unpack_dict_str, unpack_dict_int, get_uuid,
    list_input_files,
)


def stringtomd5(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def init_db(args):
    conn = psycopg2.connect(
        database=args.db_name, user=args.db_user,
        password=args.db_password, host=args.db_host, port=args.db_port,
    )
    return conn.cursor(), conn


def extract_and_store(input_files, cur, conn):
    """两遍：1) 实体收集；2) 边提取 + 批量写入。
    (与本地版不同：PG 必须先写完节点表拿到 index_id 才能写边。)"""
    uuid2name = {}
    uuid_cmdline = {}
    netflow_info = {}
    uuid2index = {}

    # ============ Pass 1 ============
    print("  Pass 1: 实体收集 ...")
    begin = time.time()
    loaded_line = 0
    for vidx, vp in enumerate(input_files):
        print(f"    [{vidx+1}/{len(input_files)}] {os.path.basename(vp)}")
        with open(vp, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"      已扫描 {loaded_line:,} 行... uuid2name={len(uuid2name):,}")
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
                        if not uid:
                            continue
                        props = datum.get('properties') or {}
                        pmap = props.get('map') if isinstance(props, dict) else None
                        path = pmap.get('path') if isinstance(pmap, dict) else None
                        uuid2name[uid] = ['process', path]
                        cmd = datum.get('cmdLine')
                        if isinstance(cmd, dict):
                            cmd_val = cmd.get('string')
                        elif isinstance(cmd, str):
                            cmd_val = cmd
                        else:
                            cmd_val = None
                        if cmd_val:
                            uuid_cmdline[uid] = cmd_val

                elif rtype == 'FileObject':
                    ftype = datum.get('type', '')
                    if ftype in ('FILE_OBJECT_FILE', 'FILE_OBJECT_DIR',
                                 'FILE_OBJECT_BLOCK'):
                        uid = norm_uuid(datum.get('uuid'))
                        if not uid:
                            continue
                        base = datum.get('baseObject') or {}
                        base_props = base.get('properties') if isinstance(base, dict) else None
                        base_map = base_props.get('map') if isinstance(base_props, dict) else None
                        filename = base_map.get('filename') if isinstance(base_map, dict) else None
                        uuid2name[uid] = ['file', filename]

                elif rtype == 'NetFlowObject':
                    uid = norm_uuid(datum.get('uuid'))
                    if not uid:
                        continue
                    la = unpack_dict_str(datum.get('localAddress'))
                    lp = unpack_dict_int(datum.get('localPort'))
                    ra = unpack_dict_str(datum.get('remoteAddress'))
                    rp = unpack_dict_int(datum.get('remotePort'))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (str(la), str(lp), str(ra), str(rp))

    print(f"  Pass1 完成: {loaded_line:,} 行, {time.time()-begin:.1f}s")

    # ============ 写节点表 ============
    print("  写节点表 ...")
    index_id = 0
    subject_data, file_data, netflow_data = [], [], []

    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'process':
            uuid2index[uid] = index_id
            subject_data.append((uid, stringtomd5(uid), name, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO subject_node_table VALUES %s",
                      subject_data, page_size=10000)
    conn.commit()
    print(f"    subject_node_table: {len(subject_data):,}")

    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'file':
            uuid2index[uid] = index_id
            file_data.append((uid, stringtomd5(uid), name, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO file_node_table VALUES %s",
                      file_data, page_size=10000)
    conn.commit()
    print(f"    file_node_table: {len(file_data):,}")

    for uid, (ntype, name) in uuid2name.items():
        if ntype == 'netflow':
            uuid2index[uid] = index_id
            la, lp, ra, rp = netflow_info.get(uid, ('', '', '', ''))
            netflow_data.append((uid, stringtomd5(uid), la, lp, ra, rp, index_id))
            index_id += 1
    ex.execute_values(cur, "INSERT INTO netflow_node_table VALUES %s",
                      netflow_data, page_size=10000)
    conn.commit()
    print(f"    netflow_node_table: {len(netflow_data):,}")
    print(f"    总节点数: {index_id:,}")

    # ============ Pass 2 ============
    print("\n  Pass 2: 边提取 + 写入 ...")
    begin = time.time()
    loaded_line = 0
    datalist = []
    edge_type_count = Counter()
    skipped = 0
    batch_size = 50000
    insert_sql = (
        "INSERT INTO event_table "
        "(src_uuid, src_index_id, operation, dst_uuid, dst_index_id, "
        "event_uuid, timestamp_rec, cmdline) VALUES %s"
    )

    for vidx, vp in enumerate(input_files):
        print(f"    [{vidx+1}/{len(input_files)}] {os.path.basename(vp)}")
        with open(vp, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"      已扫描 {loaded_line:,} 行... 累计边 {sum(edge_type_count.values()):,}")
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
                    continue

                eventtime = datum.get('timestampNanos', 0)
                event_uuid = norm_uuid(datum.get('uuid', ''))
                raw_props = datum.get('properties')
                pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                if not isinstance(pmap, dict):
                    pmap = {}

                src = get_uuid(datum.get('subject'))
                dst = get_uuid(datum.get('predicateObject'))

                cmdline = None
                if eventtype == 'EVENT_EXECUTE':
                    cmdline = pmap.get('cmdLine', None)
                elif eventtype == 'EVENT_CLONE':
                    cmdline = uuid_cmdline.get(dst, None)

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
                    ex.execute_values(cur, insert_sql, datalist, page_size=batch_size)
                    conn.commit()
                    datalist = []

    if datalist:
        ex.execute_values(cur, insert_sql, datalist, page_size=batch_size)
        conn.commit()

    total_edges = sum(edge_type_count.values())
    print(f"  Pass2 完成: {loaded_line:,} 行, {time.time()-begin:.1f}s")
    print(f"  写入边数: {total_edges:,}  跳过: {skipped:,}")
    for et, cnt in edge_type_count.most_common():
        print(f"    {et:30s} {cnt:>12,}")


def main(args):
    print("=" * 60)
    print("THEIA E5 数据提取（PostgreSQL）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"数据库:   {args.db_name}@{args.db_host}:{args.db_port}")

    input_files = list_input_files(args.input_dir)
    print(f"\n输入文件 ({len(input_files)} 个):")
    for f in input_files:
        print(f"  {os.path.basename(f)}")

    cur, conn = init_db(args)
    extract_and_store(input_files, cur, conn)
    cur.close()
    conn.close()

    print(f"\n{'='*60}\n完成\n{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="THEIA E5 Data Extractor (PostgreSQL)")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/cch_refine/theia_e5_json")
    parser.add_argument("--db_name", type=str, default="theia_e5")
    parser.add_argument("--db_user", type=str, default="postgres")
    parser.add_argument("--db_password", type=str, default="postgres")
    parser.add_argument("--db_host", type=str, default="localhost")
    parser.add_argument("--db_port", type=str, default="5432")
    args = parser.parse_args()
    main(args)
