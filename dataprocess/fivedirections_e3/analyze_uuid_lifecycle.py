"""
FiveDirections E3 UUID生命周期深度分析

核心问题：
1. UUID 的含义：每个 UUID 代表什么？PROCESS/THREAD/FILE 的 UUID 何时生成？
2. SUBJECT_PROCESS 与 SUBJECT_THREAD 的关系（parent 指向什么？）
3. SUBJECT_PROCESS 的 UUID 是否在 FORK 时创建？
4. SUBJECT_THREAD 的 UUID 是否在 CREATE_THREAD 时创建？
5. FORK/EXECUTE 的配对关系
6. EXECUTE 是否改变 UUID？（与 TRACE 对比）
7. Subject.cmdLine 是什么时候确定的？能否通过 EXECUTE 更新？
"""

import json
import sys
from collections import defaultdict, Counter


def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    subject_info = {}        # uuid → 完整Subject信息
    file_info = {}
    parent_to_children = defaultdict(list)  # parent_uuid → [child_uuids]

    fork_events = []
    execute_events = []
    create_thread_events = []
    loadlib_events = []

    # 每个UUID作为subject的事件序列
    uuid_as_src_events = defaultdict(list)  # uuid → [(ts, etype, dst, path)]

    loaded = 0
    with open(filepath, 'r') as f:
        for line in f:
            loaded += 1
            if loaded % 500000 == 0:
                print(f"  已扫描 {loaded:,} 行...")

            datum = json.loads(line)['datum']
            rtype_full = list(datum.keys())[0]
            datum = datum[rtype_full]
            rtype = rtype_full.split('.')[-1]

            if rtype == 'Subject':
                uid = datum['uuid']
                props = datum.get('properties', {})
                pm = props.get('map', {}) if isinstance(props, dict) else {}
                if not isinstance(pm, dict): pm = {}
                cmdline = datum.get('cmdLine')
                if isinstance(cmdline, dict): cmdline = cmdline.get('string')
                parent = None
                if isinstance(datum.get('parentSubject'), dict):
                    parent = list(datum['parentSubject'].values())[0]
                subject_info[uid] = {
                    'type': datum.get('type', ''),
                    'cid': datum.get('cid'),
                    'cmdLine': cmdline,
                    'parent': parent,
                    'ts': datum.get('startTimestampNanos'),
                }
                if parent:
                    parent_to_children[parent].append(uid)

            elif rtype == 'FileObject':
                uid = datum['uuid']
                file_info[uid] = {'type': datum.get('type')}

            elif rtype == 'Event':
                etype = datum.get('type', '')
                ts = datum.get('timestampNanos', 0)

                src = None
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = None
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]
                path = ''
                if isinstance(datum.get('predicateObjectPath'), dict):
                    path = datum['predicateObjectPath'].get('string', '')

                if src:
                    uuid_as_src_events[src].append((ts, etype, dst, path))

                if etype == 'EVENT_FORK':
                    fork_events.append({'src': src, 'dst': dst, 'ts': ts, 'path': path})
                elif etype == 'EVENT_EXECUTE':
                    execute_events.append({'src': src, 'dst': dst, 'ts': ts, 'path': path})
                elif etype == 'EVENT_CREATE_THREAD':
                    create_thread_events.append({'src': src, 'dst': dst, 'ts': ts})
                elif etype == 'EVENT_LOADLIBRARY':
                    loadlib_events.append({'src': src, 'dst': dst, 'ts': ts, 'path': path})

    # ============ 1. UUID 基本统计 ============
    print(f"{'='*80}")
    print("一、UUID 基本统计")
    print(f"{'='*80}")

    process_uuids = {u for u, v in subject_info.items() if v['type'] == 'SUBJECT_PROCESS'}
    thread_uuids = {u for u, v in subject_info.items() if v['type'] == 'SUBJECT_THREAD'}
    print(f"  SUBJECT_PROCESS: {len(process_uuids):,}")
    print(f"  SUBJECT_THREAD:  {len(thread_uuids):,}")
    print(f"  FORK事件:         {len(fork_events):,}")
    print(f"  EXECUTE事件:      {len(execute_events):,}")
    print(f"  CREATE_THREAD:    {len(create_thread_events):,}")
    print(f"  LOADLIBRARY:      {len(loadlib_events):,}")

    # ============ 2. UUID 生成机制 ============
    print(f"\n{'='*80}")
    print("二、UUID 生成机制分析")
    print(f"{'='*80}")

    fork_created_uuids = {e['dst'] for e in fork_events if e['dst']}
    create_thread_uuids = {e['dst'] for e in create_thread_events if e['dst']}

    proc_from_fork = process_uuids & fork_created_uuids
    proc_no_fork = process_uuids - fork_created_uuids
    thread_from_create = thread_uuids & create_thread_uuids
    thread_no_create = thread_uuids - create_thread_uuids

    print(f"\n  SUBJECT_PROCESS UUID来源:")
    print(f"    来自EVENT_FORK: {len(proc_from_fork):,} ({len(proc_from_fork)/max(len(process_uuids),1)*100:.1f}%)")
    print(f"    无FORK事件:     {len(proc_no_fork):,} ({len(proc_no_fork)/max(len(process_uuids),1)*100:.1f}%)")

    print(f"\n  SUBJECT_THREAD UUID来源:")
    print(f"    来自EVENT_CREATE_THREAD: {len(thread_from_create):,}")
    print(f"    无CREATE_THREAD事件:     {len(thread_no_create):,}")

    # ============ 3. SUBJECT_THREAD 与 SUBJECT_PROCESS 的父子关系 ============
    print(f"\n{'='*80}")
    print("三、SUBJECT_THREAD 与 SUBJECT_PROCESS 的父子关系")
    print(f"{'='*80}")

    # THREAD的parent
    thread_parent_types = Counter()
    thread_same_pid_as_parent = 0
    thread_diff_pid_from_parent = 0

    for uid, info in subject_info.items():
        if info['type'] != 'SUBJECT_THREAD':
            continue
        parent = info.get('parent')
        if parent and parent in subject_info:
            p_info = subject_info[parent]
            thread_parent_types[p_info['type']] += 1
            if info['cid'] == p_info['cid']:
                thread_same_pid_as_parent += 1
            else:
                thread_diff_pid_from_parent += 1

    print(f"\n  THREAD 的 parent 类型:")
    for t, c in thread_parent_types.most_common():
        print(f"    {t}: {c:,}")

    print(f"\n  THREAD 与 parent 的 PID 关系:")
    print(f"    PID相同（线程属于同一进程）: {thread_same_pid_as_parent:,}")
    print(f"    PID不同: {thread_diff_pid_from_parent:,}")

    # 一个PROCESS有多少个THREAD？
    proc_thread_count = Counter()
    for t_uid, t_info in subject_info.items():
        if t_info['type'] == 'SUBJECT_THREAD':
            proc_thread_count[t_info['parent']] += 1

    thread_count_dist = Counter(proc_thread_count.values())
    print(f"\n  每个 PROCESS 拥有的 THREAD 数量分布:")
    for n, c in sorted(thread_count_dist.items())[:15]:
        print(f"    {n}个THREAD: {c:,}个PROCESS")

    # 展示几个PROCESS及其THREAD示例
    print(f"\n  前5个 PROCESS 及其 THREAD:")
    shown = 0
    for p_uid, p_info in subject_info.items():
        if p_info['type'] != 'SUBJECT_PROCESS':
            continue
        threads = [t for t in parent_to_children.get(p_uid, [])
                   if t in subject_info and subject_info[t]['type'] == 'SUBJECT_THREAD']
        if len(threads) >= 2:
            print(f"\n    PROCESS: {str(p_info.get('cmdLine', '?'))[:50]} (PID={p_info['cid']}, UUID={p_uid[:16]}...)")
            print(f"      拥有 {len(threads)} 个THREAD (PID相同):")
            for t_uid in threads[:3]:
                t = subject_info[t_uid]
                print(f"        THREAD UUID={t_uid[:16]}... PID={t['cid']} cmdLine={t.get('cmdLine')}")
            shown += 1
            if shown >= 5:
                break

    # ============ 4. FORK 事件分析 ============
    print(f"\n{'='*80}")
    print("四、EVENT_FORK 深度分析")
    print(f"{'='*80}")

    fork_src_types = Counter()
    fork_dst_types = Counter()
    for e in fork_events:
        si = subject_info.get(e['src'], {})
        di = subject_info.get(e['dst'], {})
        fork_src_types[si.get('type', 'NOT_FOUND')] += 1
        fork_dst_types[di.get('type', 'NOT_FOUND')] += 1

    print(f"\n  FORK src类型: {dict(fork_src_types)}")
    print(f"  FORK dst类型: {dict(fork_dst_types)}")

    # FORK的含义：线程创建新进程？
    print(f"\n  前10个FORK详情:")
    for e in fork_events[:10]:
        si = subject_info.get(e['src'], {})
        di = subject_info.get(e['dst'], {})
        # 找src线程的父进程
        src_parent = si.get('parent')
        src_parent_cmd = subject_info.get(src_parent, {}).get('cmdLine') if src_parent else '?'
        print(f"    src(线程): PID={si.get('cid','?')} UUID={str(e['src'])[:16]}...")
        print(f"      其所属进程: {str(src_parent_cmd)[:50]}")
        print(f"    dst(新进程): PID={di.get('cid','?')} cmd={str(di.get('cmdLine','?'))[:60]}")
        print()

    # ============ 5. EXECUTE 事件分析 ============
    print(f"\n{'='*80}")
    print("五、EVENT_EXECUTE 深度分析")
    print(f"{'='*80}")

    exe_src_types = Counter()
    for e in execute_events:
        si = subject_info.get(e['src'], {})
        exe_src_types[si.get('type', 'NOT_FOUND')] += 1
    print(f"\n  EXECUTE src类型: {dict(exe_src_types)}")

    # EXECUTE的src是不是FORK的dst？
    execute_src_set = {e['src'] for e in execute_events}
    fork_dst_set = {e['dst'] for e in fork_events}
    overlap = execute_src_set & fork_dst_set
    print(f"\n  EXECUTE的src 与 FORK的dst 重叠: {len(overlap):,} / {len(execute_src_set):,}")
    print(f"  (即: {len(overlap):,} 个EXECUTE是由FORK出的新进程发起的)")

    # 分析进程是否exec自己？看进程的cmdLine和EXECUTE的path
    print(f"\n  前15个EXECUTE详情（对比 src cmdLine 和 EXECUTE dst path）:")
    same_name = 0
    diff_name = 0
    for e in execute_events[:15]:
        si = subject_info.get(e['src'], {})
        src_cmd = si.get('cmdLine', '?')
        dst_path = e['path']
        match = src_cmd and dst_path and dst_path in str(src_cmd)
        if match:
            same_name += 1
        else:
            diff_name += 1
        print(f"    src cmdLine={str(src_cmd)[:40]:40s}  →  EXECUTE path={dst_path}")

    # 全量统计
    all_same = 0
    all_diff = 0
    for e in execute_events:
        si = subject_info.get(e['src'], {})
        src_cmd = si.get('cmdLine')
        dst_path = e['path']
        if src_cmd and dst_path and dst_path in str(src_cmd):
            all_same += 1
        else:
            all_diff += 1

    print(f"\n  全量统计（{len(execute_events)}条EXECUTE）:")
    print(f"    path 在 src.cmdLine 中: {all_same} ({all_same/max(len(execute_events),1)*100:.1f}%)")
    print(f"    path 不在 src.cmdLine 中: {all_diff}")

    # ============ 6. FORK → EXECUTE 链条 ============
    print(f"\n{'='*80}")
    print("六、FORK → EXECUTE 时序链条")
    print(f"{'='*80}")

    # FORK创建的新进程随后是否EXECUTE？
    fork_child_did_exe = 0
    fork_child_no_exe = 0
    for e in fork_events:
        if e['dst'] in execute_src_set:
            fork_child_did_exe += 1
        else:
            fork_child_no_exe += 1

    print(f"\n  FORK创建的新进程 (共{len(fork_events)}个):")
    print(f"    随后做了EXECUTE: {fork_child_did_exe}")
    print(f"    没做EXECUTE:    {fork_child_no_exe}")

    # 找出FORK → EXECUTE配对的例子
    print(f"\n  前10个 FORK→EXECUTE 完整链:")
    shown = 0
    for e in fork_events:
        if e['dst'] in execute_src_set and shown < 10:
            fork_dst = e['dst']
            # 找这个进程的EXECUTE
            exe = None
            for ee in execute_events:
                if ee['src'] == fork_dst:
                    exe = ee
                    break
            if exe:
                di = subject_info.get(fork_dst, {})
                print(f"    FORK(t={e['ts']}): → 新进程 cmd={str(di.get('cmdLine'))[:50]}")
                print(f"    EXECUTE(t={exe['ts']}): → dst path={exe['path']}")
                print(f"      时序: EXECUTE {'在FORK之后' if exe['ts'] > e['ts'] else '在FORK之前或同时'}")
                shown += 1

    # ============ 7. EXECUTE dst 的PE文件如何与 src.cmdLine 关联 ============
    print(f"\n{'='*80}")
    print("七、EXECUTE 如何确定新进程身份？")
    print(f"{'='*80}")
    print(f"""
  Windows的CreateProcess分两步：
  1. FORK（创建新进程UUID，此时新进程的cmdLine已经在Subject记录中）
  2. EXECUTE（加载PE文件，src是新进程，dst是PE文件）

  所以：
  - FORK的dst cmdLine 已经反映新进程身份
  - EXECUTE的path 是PE文件路径（通常与cmdLine一致）
  - 这与CADETS/THEIA完全不同——那里EXECUTE改变同一UUID的身份
  - 与TRACE也不同——TRACE的EXECUTE创建新UUID

  结论: FiveDirections中 UUID 身份在 Subject 记录（FORK时）就已经确定，
       EXECUTE 只是显式的"加载PE文件"动作。
""")

    # ============ 8. LOADLIBRARY vs EXECUTE ============
    print(f"\n{'='*80}")
    print("八、LOADLIBRARY vs EXECUTE")
    print(f"{'='*80}")

    ll_src_types = Counter()
    for e in loadlib_events:
        si = subject_info.get(e['src'], {})
        ll_src_types[si.get('type', 'NOT_FOUND')] += 1
    print(f"\n  LOADLIBRARY src类型: {dict(ll_src_types)}")

    print(f"\n  对比:")
    print(f"    EXECUTE src=PROCESS, 共{len(execute_events)}条, dst=PE文件（主程序）")
    print(f"    LOADLIBRARY src=THREAD, 共{len(loadlib_events)}条, dst=DLL（库）")

    # 进程的EXECUTE和其线程的LOADLIBRARY的关系
    print(f"\n  前5个进程的 EXECUTE 和其线程的 LOADLIBRARY:")
    shown = 0
    for e in execute_events[:20]:
        proc_uid = e['src']
        if proc_uid not in subject_info or shown >= 5:
            continue
        # 找这个进程的所有THREAD
        threads = [t for t in parent_to_children.get(proc_uid, [])
                   if t in subject_info and subject_info[t]['type'] == 'SUBJECT_THREAD']
        thread_ll = []
        for t in threads:
            for le in loadlib_events:
                if le['src'] == t:
                    thread_ll.append(le['path'])

        if thread_ll:
            si = subject_info[proc_uid]
            print(f"\n    进程: {str(si.get('cmdLine'))[:50]}")
            print(f"      EXECUTE path: {e['path']}")
            print(f"      其线程LOADLIBRARY的DLL（前5个）:")
            for p in thread_ll[:5]:
                print(f"        {p}")
            shown += 1


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/fivedirections_e3/ta1-fivedirections-e3-official-2.json"
    analyze(filepath)
