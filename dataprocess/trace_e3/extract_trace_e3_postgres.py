"""
TRACE E3 数据提取脚本（PostgreSQL 版）
与 extract_trace_e3.py 逻辑完全一致，结果存入 PostgreSQL。

用法：
  psql -U postgres -f init_database.sql
  python extract_trace_e3_postgres.py --input_dir /mnt/disk/darpa/trace_e3
"""

import json
import os
import time
import hashlib
import argparse
from collections import Counter, defaultdict

import psycopg2
from psycopg2 import extras as ex

def build_file_list():
    files = []
    files.append('ta1-trace-e3-official.json')
    for i in range(1, 204):
        files.append(f'ta1-trace-e3-official.json.{i}')
    files.append('ta1-trace-e3-official-1.json')
    for i in range(1, 7):
        files.append(f'ta1-trace-e3-official-1.json.{i}')
    return files

FILE_LIST = build_file_list()

INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_EXECUTE',
    'EVENT_FORK', 'EVENT_CLONE',
    'EVENT_OPEN', 'EVENT_CONNECT',
    'EVENT_SENDTO', 'EVENT_RECVFROM', 'EVENT_SENDMSG', 'EVENT_RECVMSG',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_CREATE_OBJECT',
    'EVENT_LOADLIBRARY', 'EVENT_ACCEPT',
}

EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG',
    'EVENT_OPEN', 'EVENT_LOADLIBRARY', 'EVENT_ACCEPT',
}


def stringtomd5(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def init_db(args):
    c = psycopg2.connect(database=args.db_name, user=args.db_user,
                         password=args.db_password, host=args.db_host, port=args.db_port)
    return c.cursor(), c


def pass1_collect_and_store(input_dir, cur, connect):
    uuid2name = {}
    uuid2index = {}
    uuid_execute_dst_cmdline = {}
    uuid_subject_cmdline = {}
    netflow_info = {}

    loaded = 0
    begin = time.time()

    for vname in FILE_LIST:
        vpath = os.path.join(input_dir, vname)
        if not os.path.exists(vpath): continue
        print(f"  处理: {vname}")
        with open(vpath, 'r') as fin:
            for line in fin:
                loaded += 1
                if loaded % 1000000 == 0:
                    print(f"    {loaded:,} 行...")

                record = json.loads(line)['datum']
                rf = list(record.keys())[0]
                datum = record[rf]
                rtype = rf.split('.')[-1]

                if rtype == 'Subject':
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = datum['uuid']
                        props = datum.get('properties', {})
                        pm = props.get('map', {}) if isinstance(props, dict) else {}
                        if not isinstance(pm, dict): pm = {}
                        uuid2name[uid] = ['process', pm.get('name')]
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict): cmdline = cmdline.get('string')
                        if cmdline: uuid_subject_cmdline[uid] = cmdline

                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    base = datum.get('baseObject', {})
                    bp = base.get('properties', {}).get('map', {}) if isinstance(base.get('properties'), dict) else {}
                    if not isinstance(bp, dict): bp = {}
                    uuid2name[uid] = ['file', bp.get('path')]

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    la = str(datum.get('localAddress', ''))
                    lp = str(datum.get('localPort', ''))
                    ra = str(datum.get('remoteAddress', ''))
                    rp = str(datum.get('remotePort', ''))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (la, lp, ra, rp)

                elif rtype == 'Event':
                    if datum.get('type') == 'EVENT_EXECUTE':
                        src = list(datum['subject'].values())[0] if isinstance(datum.get('subject'), dict) else None
                        dst = list(datum['predicateObject'].values())[0] if isinstance(datum.get('predicateObject'), dict) else None
                        if src and dst and src not in uuid_execute_dst_cmdline:
                            dc = uuid_subject_cmdline.get(dst)
                            if dc: uuid_execute_dst_cmdline[src] = dc

    print(f"\n  Pass 1 扫描完成: {loaded:,} 行, {time.time()-begin:.1f}s")

    # 写入节点表
    print("  写入数据库...")
    idx = 0

    sd = []
    for uid, (t, n) in uuid2name.items():
        if t == 'process':
            uuid2index[uid] = idx
            sd.append((uid, stringtomd5(uid), n, idx))
            idx += 1
    ex.execute_values(cur, "INSERT INTO subject_node_table VALUES %s", sd, page_size=10000)
    connect.commit()
    print(f"    subject: {len(sd):,}")

    fd = []
    for uid, (t, n) in uuid2name.items():
        if t == 'file':
            uuid2index[uid] = idx
            fd.append((uid, stringtomd5(uid), n, idx))
            idx += 1
    ex.execute_values(cur, "INSERT INTO file_node_table VALUES %s", fd, page_size=10000)
    connect.commit()
    print(f"    file: {len(fd):,}")

    nd = []
    for uid, (t, n) in uuid2name.items():
        if t == 'netflow':
            uuid2index[uid] = idx
            la, lp, ra, rp = netflow_info.get(uid, ('','','',''))
            nd.append((uid, stringtomd5(uid), la, lp, ra, rp, idx))
            idx += 1
    ex.execute_values(cur, "INSERT INTO netflow_node_table VALUES %s", nd, page_size=10000)
    connect.commit()
    print(f"    netflow: {len(nd):,}")
    print(f"    总节点: {idx:,}")

    return uuid2name, uuid2index, uuid_execute_dst_cmdline, uuid_subject_cmdline


def pass2_extract_and_store(input_dir, uuid2name, uuid2index, uuid_execute_dst_cmdline, uuid_subject_cmdline, cur, connect):
    datalist = []
    etc = Counter()
    skipped = 0
    bs = 50000

    loaded = 0
    begin = time.time()

    for vname in FILE_LIST:
        vpath = os.path.join(input_dir, vname)
        if not os.path.exists(vpath): continue
        print(f"  处理: {vname}")
        with open(vpath, 'r') as fin:
            for line in fin:
                loaded += 1
                if loaded % 1000000 == 0:
                    print(f"    {loaded:,} 行...")

                record = json.loads(line)['datum']
                rf = list(record.keys())[0]
                datum = record[rf]
                rtype = rf.split('.')[-1]

                if rtype != 'Event': continue
                etype = datum.get('type', '')
                if etype not in INCLUDE_EDGE_TYPE: continue

                etime = datum.get('timestampNanos', 0)
                euuid = datum.get('uuid', '')

                src = list(datum['subject'].values())[0] if isinstance(datum.get('subject'), dict) else ''
                dst = list(datum['predicateObject'].values())[0] if isinstance(datum.get('predicateObject'), dict) else ''
                dst2 = list(datum['predicateObject2'].values())[0] if isinstance(datum.get('predicateObject2'), dict) else ''

                cmdline = None
                if etype == 'EVENT_EXECUTE':
                    cmdline = uuid_subject_cmdline.get(dst, None)
                elif etype == 'EVENT_FORK':
                    cmdline = uuid_execute_dst_cmdline.get(dst, None)
                    if cmdline is None:
                        cmdline = uuid_subject_cmdline.get(dst, None)
                elif etype == 'EVENT_CLONE':
                    cmdline = uuid_subject_cmdline.get(dst, None)

                actual_dst = dst2 if etype == 'EVENT_RENAME' else dst

                if not src or not actual_dst or src not in uuid2index or actual_dst not in uuid2index:
                    skipped += 1
                    continue

                if etype in EDGE_REVERSED:
                    es, ed = actual_dst, src
                else:
                    es, ed = src, actual_dst

                datalist.append((es, uuid2index[es], etype, ed, uuid2index[ed], euuid, etime, cmdline))
                etc[etype] += 1

                if len(datalist) >= bs:
                    sql = "INSERT INTO event_table (src_uuid,src_index_id,operation,dst_uuid,dst_index_id,event_uuid,timestamp_rec,cmdline) VALUES %s"
                    ex.execute_values(cur, sql, datalist, page_size=bs)
                    connect.commit()
                    datalist = []

    if datalist:
        sql = "INSERT INTO event_table (src_uuid,src_index_id,operation,dst_uuid,dst_index_id,event_uuid,timestamp_rec,cmdline) VALUES %s"
        ex.execute_values(cur, sql, datalist, page_size=bs)
        connect.commit()

    total = sum(etc.values())
    print(f"\n  Pass 2 完成: {loaded:,} 行, {time.time()-begin:.1f}s")
    print(f"  边数: {total:,}, 跳过: {skipped:,}")
    for e, c in etc.most_common():
        print(f"    {e:30s} {c:>12,}")


def main(args):
    print("=" * 60)
    print("TRACE E3 数据提取 (PostgreSQL)")
    print("=" * 60)
    cur, connect = init_db(args)

    print(f"\n{'='*60}")
    print("Pass 1")
    print(f"{'='*60}")
    uuid2name, uuid2index, uedc, usc = pass1_collect_and_store(args.input_dir, cur, connect)

    print(f"\n{'='*60}")
    print("Pass 2")
    print(f"{'='*60}")
    pass2_extract_and_store(args.input_dir, uuid2name, uuid2index, uedc, usc, cur, connect)

    cur.close()
    connect.close()
    print(f"\n完成")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument("--input_dir", default="/mnt/disk/darpa/trace_e3")
    p.add_argument("--db_name", default="trace_e3")
    p.add_argument("--db_user", default="postgres")
    p.add_argument("--db_password", default="postgres")
    p.add_argument("--db_host", default="localhost")
    p.add_argument("--db_port", default="5432")
    main(p.parse_args())
