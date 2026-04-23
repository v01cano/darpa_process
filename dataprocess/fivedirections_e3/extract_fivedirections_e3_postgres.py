"""
FiveDirections E3 数据提取脚本（PostgreSQL 版）

与本地磁盘版逻辑一致，但存入 PostgreSQL 数据库。

用法：
  psql -U postgres -f init_database.sql
  python extract_fivedirections_e3_postgres.py \
      --input_dir /mnt/disk/darpa/fivedirections_e3
"""

import json
import os
import time
import hashlib
import argparse
from collections import Counter

import psycopg2
from psycopg2 import extras as ex


def build_file_list():
    files = []
    files.append('ta1-fivedirections-e3-official.json')
    files.append('ta1-fivedirections-e3-official-2.json')
    for i in range(1, 53):
        files.append(f'ta1-fivedirections-e3-official-2.json.{i}')
    files.append('ta1-fivedirections-e3-official-3.json')
    return files

FILE_LIST = build_file_list()

INCLUDE_EDGE_TYPE = {
    'EVENT_READ', 'EVENT_WRITE', 'EVENT_OPEN',
    'EVENT_UNLINK', 'EVENT_RENAME', 'EVENT_CREATE_OBJECT',
    'EVENT_MODIFY_FILE_ATTRIBUTES',
    'EVENT_FORK', 'EVENT_EXECUTE', 'EVENT_LOADLIBRARY',
    'EVENT_CONNECT', 'EVENT_SENDTO', 'EVENT_RECVFROM',
    'EVENT_SENDMSG', 'EVENT_RECVMSG', 'EVENT_ACCEPT', 'EVENT_BIND',
}

EDGE_REVERSED = {
    'EVENT_READ', 'EVENT_RECVFROM', 'EVENT_RECVMSG', 'EVENT_OPEN',
    'EVENT_EXECUTE', 'EVENT_LOADLIBRARY', 'EVENT_ACCEPT',
}


def stringtomd5(s):
    """函数名沿用 Orthrus 命名习惯，实际是 SHA256"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def init_db(args):
    c = psycopg2.connect(database=args.db_name, user=args.db_user,
                         password=args.db_password, host=args.db_host, port=args.db_port)
    return c.cursor(), c


def extract_exe_name(cmdline):
    """从 cmdLine 提取可执行文件名"""
    if not cmdline:
        return None
    cmd = cmdline.strip().strip('"').strip("'")
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        first_token = cmd[1:end] if end > 0 else cmd[1:]
    else:
        first_token = cmd.split(' ', 1)[0]
    if '\\' in first_token:
        name = first_token.rsplit('\\', 1)[-1]
    elif '/' in first_token:
        name = first_token.rsplit('/', 1)[-1]
    else:
        name = first_token
    return name.lower() if name else None


# ============================================================================
# Pass 1
# ============================================================================

def pass1_collect_and_store(input_dir, cur, connect):
    uuid2name = {}
    uuid2index = {}
    thread_to_process = {}
    subject_cmdline = {}
    uuid_execute_path = {}
    netflow_info = {}

    loaded = 0
    begin = time.time()

    for vname in FILE_LIST:
        vpath = os.path.join(input_dir, vname)
        if not os.path.exists(vpath):
            continue
        print(f"  处理: {vname}")
        with open(vpath, 'r') as fin:
            for line in fin:
                loaded += 1
                if loaded % 2000000 == 0:
                    print(f"    {loaded:,} 行...")

                record = json.loads(line)['datum']
                rf = list(record.keys())[0]
                datum = record[rf]
                rtype = rf.split('.')[-1]

                if rtype == 'Subject':
                    uid = datum['uuid']
                    stype = datum.get('type', '')
                    if stype == 'SUBJECT_PROCESS':
                        uuid2name[uid] = ['process', None]
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        if cmdline:
                            subject_cmdline[uid] = cmdline
                    elif stype == 'SUBJECT_THREAD':
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
                    netflow_info[uid] = (la, lp, ra, rp)

                elif rtype == 'RegistryKeyObject':
                    uid = datum['uuid']
                    key = datum.get('key')
                    uuid2name[uid] = ['file', key]

                elif rtype == 'Event':
                    etype = datum.get('type', '')
                    if etype == 'EVENT_EXECUTE':
                        src = None
                        if isinstance(datum.get('subject'), dict):
                            src = list(datum['subject'].values())[0]
                        path = None
                        if isinstance(datum.get('predicateObjectPath'), dict):
                            path = datum['predicateObjectPath'].get('string')
                        if src and path and src not in uuid_execute_path:
                            uuid_execute_path[src] = path.lower()

                    # 更新 FileObject 的 path
                    if isinstance(datum.get('predicateObjectPath'), dict):
                        path = datum['predicateObjectPath'].get('string')
                        dst = None
                        if isinstance(datum.get('predicateObject'), dict):
                            dst = list(datum['predicateObject'].values())[0]
                        if path and dst and dst in uuid2name:
                            ntype, nname = uuid2name[dst]
                            if ntype == 'file' and nname is None:
                                uuid2name[dst][1] = path

                    if isinstance(datum.get('predicateObject2Path'), dict):
                        path2 = datum['predicateObject2Path'].get('string')
                        dst2 = None
                        if isinstance(datum.get('predicateObject2'), dict):
                            dst2 = list(datum['predicateObject2'].values())[0]
                        if path2 and dst2 and dst2 in uuid2name:
                            ntype, nname = uuid2name[dst2]
                            if ntype == 'file' and nname is None:
                                uuid2name[dst2][1] = path2

    print(f"\n  Pass 1 扫描完成: {loaded:,} 行, {time.time()-begin:.1f}s")

    # 设置 PROCESS name
    for uid, entry in uuid2name.items():
        if entry[0] != 'process':
            continue
        if uid in uuid_execute_path:
            uuid2name[uid][1] = uuid_execute_path[uid]
        else:
            cmdline = subject_cmdline.get(uid)
            name = extract_exe_name(cmdline)
            if name:
                uuid2name[uid][1] = name

    # 写入节点表
    print("  写入数据库...")
    idx = 0

    sd = []
    for uid, (t, n) in uuid2name.items():
        if t == 'process':
            uuid2index[uid] = idx
            sd.append((uid, stringtomd5(uid), n, idx))
            idx += 1
    ex.execute_values(cur, "INSERT INTO subject_node_table VALUES %s",
                       sd, page_size=10000)
    connect.commit()
    print(f"    subject_node_table: {len(sd):,}")

    fd = []
    for uid, (t, n) in uuid2name.items():
        if t == 'file':
            uuid2index[uid] = idx
            fd.append((uid, stringtomd5(uid), n, idx))
            idx += 1
    ex.execute_values(cur, "INSERT INTO file_node_table VALUES %s",
                       fd, page_size=10000)
    connect.commit()
    print(f"    file_node_table: {len(fd):,}")

    nd = []
    for uid, (t, n) in uuid2name.items():
        if t == 'netflow':
            uuid2index[uid] = idx
            la, lp, ra, rp = netflow_info.get(uid, ('', '', '', ''))
            nd.append((uid, stringtomd5(uid), la, lp, ra, rp, idx))
            idx += 1
    ex.execute_values(cur, "INSERT INTO netflow_node_table VALUES %s",
                       nd, page_size=10000)
    connect.commit()
    print(f"    netflow_node_table: {len(nd):,}")
    print(f"    总节点: {idx:,}")

    return uuid2name, uuid2index, thread_to_process, subject_cmdline


# ============================================================================
# Pass 2
# ============================================================================

def pass2_extract_and_store(input_dir, uuid2name, uuid2index, thread_to_process,
                             subject_cmdline, cur, connect):
    datalist = []
    etc = Counter()
    skipped = 0
    bs = 50000

    loaded = 0
    begin = time.time()

    for vname in FILE_LIST:
        vpath = os.path.join(input_dir, vname)
        if not os.path.exists(vpath):
            continue
        print(f"  处理: {vname}")
        with open(vpath, 'r') as fin:
            for line in fin:
                loaded += 1
                if loaded % 2000000 == 0:
                    print(f"    {loaded:,} 行...")

                record = json.loads(line)['datum']
                rf = list(record.keys())[0]
                datum = record[rf]
                rtype = rf.split('.')[-1]

                if rtype != 'Event':
                    continue

                etype = datum.get('type', '')
                if etype not in INCLUDE_EDGE_TYPE:
                    continue

                etime = datum.get('timestampNanos', 0)
                euuid = datum.get('uuid', '')

                src = ''
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = ''
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]
                dst2 = ''
                if isinstance(datum.get('predicateObject2'), dict):
                    dst2 = list(datum['predicateObject2'].values())[0]

                actual_dst = dst2 if etype == 'EVENT_RENAME' else dst

                # UUID 替换：THREAD → PROCESS
                if src in thread_to_process:
                    src = thread_to_process[src]
                if actual_dst in thread_to_process:
                    actual_dst = thread_to_process[actual_dst]

                # 跳过自环
                if src == actual_dst:
                    skipped += 1
                    continue

                if not src or not actual_dst:
                    skipped += 1
                    continue
                if src not in uuid2index or actual_dst not in uuid2index:
                    skipped += 1
                    continue

                # cmdLine
                cmdline = None
                if etype == 'EVENT_FORK':
                    cmdline = subject_cmdline.get(actual_dst)
                elif etype == 'EVENT_EXECUTE':
                    cmdline = subject_cmdline.get(src)

                # 反转
                if etype in EDGE_REVERSED:
                    es, ed = actual_dst, src
                else:
                    es, ed = src, actual_dst

                datalist.append((es, uuid2index[es], etype,
                                 ed, uuid2index[ed], euuid, etime, cmdline))
                etc[etype] += 1

                if len(datalist) >= bs:
                    sql = ("INSERT INTO event_table (src_uuid, src_index_id, operation, "
                           "dst_uuid, dst_index_id, event_uuid, timestamp_rec, cmdline) "
                           "VALUES %s")
                    ex.execute_values(cur, sql, datalist, page_size=bs)
                    connect.commit()
                    datalist = []

    if datalist:
        sql = ("INSERT INTO event_table (src_uuid, src_index_id, operation, "
               "dst_uuid, dst_index_id, event_uuid, timestamp_rec, cmdline) "
               "VALUES %s")
        ex.execute_values(cur, sql, datalist, page_size=bs)
        connect.commit()

    total = sum(etc.values())
    print(f"\n  Pass 2 完成: {loaded:,} 行, {time.time()-begin:.1f}s")
    print(f"  边数: {total:,}, 跳过: {skipped:,}")
    for e, c in etc.most_common():
        print(f"    {e:30s} {c:>12,}")


def main(args):
    print("=" * 60)
    print("FiveDirections E3 数据提取 (PostgreSQL)")
    print("=" * 60)
    cur, connect = init_db(args)

    print(f"\n{'='*60}")
    print("Pass 1")
    print(f"{'='*60}")
    uuid2name, uuid2index, thread_map, cmdlines = pass1_collect_and_store(
        args.input_dir, cur, connect)

    print(f"\n{'='*60}")
    print("Pass 2")
    print(f"{'='*60}")
    pass2_extract_and_store(args.input_dir, uuid2name, uuid2index,
                             thread_map, cmdlines, cur, connect)

    cur.close()
    connect.close()
    print(f"\n完成")


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument("--input_dir", default="/mnt/disk/darpa/fivedirections_e3")
    p.add_argument("--db_name", default="fivedirections_e3")
    p.add_argument("--db_user", default="postgres")
    p.add_argument("--db_password", default="postgres")
    p.add_argument("--db_host", default="localhost")
    p.add_argument("--db_port", default="5432")
    main(p.parse_args())
