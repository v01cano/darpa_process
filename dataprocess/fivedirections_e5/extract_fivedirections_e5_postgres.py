"""
FiveDirections E5 数据提取脚本（PostgreSQL 版）

与 extract_fivedirections_e5.py 提取逻辑完全一致，结果直接写入 PostgreSQL。
"""

import json
import os
import time
import hashlib
import argparse
from collections import Counter

import psycopg2
from psycopg2 import extras as ex

from extract_fivedirections_e5 import (
    INCLUDE_EDGE_TYPE, EDGE_REVERSED, FILE_OBJECT_KEEP,
    norm_uuid, unpack_dict_str, unpack_dict_int, get_uuid,
    extract_exe_basename, list_input_files,
    pass1_collect,
)


def stringtomd5(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def init_db(args):
    conn = psycopg2.connect(
        database=args.db_name, user=args.db_user,
        password=args.db_password, host=args.db_host, port=args.db_port,
    )
    return conn.cursor(), conn


def write_nodes(uuid2name, netflow_info, cur, conn):
    print("  写节点表 ...")
    index_id = 0
    uuid2index = {}
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
    return uuid2index


def pass2_to_db(input_files, uuid2name, uuid2index, thread_to_process, cur, conn):
    print("\n  Pass 2: 边提取 + 写入 ...")
    begin = time.time()
    loaded_line = 0
    datalist = []
    edge_type_count = Counter()
    skipped = 0
    self_loop = 0
    batch_size = 50000
    insert_sql = (
        "INSERT INTO event_table "
        "(src_uuid, src_index_id, operation, dst_uuid, dst_index_id, "
        "event_uuid, timestamp_rec, cmdline) VALUES %s"
    )

    def resolve(uid):
        if uid in thread_to_process:
            return thread_to_process[uid]
        return uid

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
                src = resolve(get_uuid(datum.get('subject')))
                dst = resolve(get_uuid(datum.get('predicateObject')))
                dst2 = resolve(get_uuid(datum.get('predicateObject2')))

                actual_dst = dst2 if eventtype == 'EVENT_RENAME' else dst
                if not src or not actual_dst:
                    skipped += 1
                    continue
                if src not in uuid2index or actual_dst not in uuid2index:
                    skipped += 1
                    continue
                if src == actual_dst:
                    self_loop += 1
                    continue

                if eventtype in EDGE_REVERSED:
                    edge_src, edge_dst = actual_dst, src
                else:
                    edge_src, edge_dst = src, actual_dst

                cmdline = None
                pop = datum.get('predicateObjectPath')
                if isinstance(pop, dict):
                    pop_str = pop.get('string', '')
                    if eventtype in ('EVENT_FORK', 'EVENT_EXECUTE') and pop_str:
                        cmdline = pop_str

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
    print(f"  写入边数: {total_edges:,}  跳过: {skipped:,}  自环: {self_loop:,}")
    for et, cnt in edge_type_count.most_common():
        print(f"    {et:30s} {cnt:>12,}")


def main(args):
    print("=" * 60)
    print("FiveDirections E5 数据提取（PostgreSQL）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"数据库:   {args.db_name}@{args.db_host}:{args.db_port}")

    input_files = list_input_files(args.input_dir)
    print(f"\n输入文件 ({len(input_files)} 个):")
    for f in input_files:
        print(f"  {os.path.basename(f)}")

    print(f"\n  Pass 1 ...")
    uuid2name, thread_to_process, netflow_info, _registry = pass1_collect(input_files)

    cur, conn = init_db(args)
    uuid2index = write_nodes(uuid2name, netflow_info, cur, conn)
    pass2_to_db(input_files, uuid2name, uuid2index, thread_to_process, cur, conn)
    cur.close()
    conn.close()

    print(f"\n{'='*60}\n完成\n{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="FiveDirections E5 Data Extractor (PostgreSQL)")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/cch_refine/fivedirections_e5_json")
    parser.add_argument("--db_name", type=str, default="fivedirections_e5")
    parser.add_argument("--db_user", type=str, default="postgres")
    parser.add_argument("--db_password", type=str, default="postgres")
    parser.add_argument("--db_host", type=str, default="localhost")
    parser.add_argument("--db_port", type=str, default="5432")
    args = parser.parse_args()
    main(args)
