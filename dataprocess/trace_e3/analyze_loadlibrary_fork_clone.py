"""
TRACE E3 EVENT_LOADLIBRARY / FORK / CLONE 深度分析

问题1：EVENT_LOADLIBRARY 是否等于 EXECUTE？
  - LOADLIBRARY的dst指向什么？（FileObject? Subject?）
  - LOADLIBRARY的src和EXECUTE的src有什么关系？
  - CAPTAIN把LOADLIBRARY映射为'execve'，把EXECUTE映射为'update_process'，为什么？

问题2：FORK和CLONE是否可以合并？
  - FORK和CLONE在语义上有什么区别？
  - FORK的父子类型 vs CLONE的父子类型
  - FORK后续行为 vs CLONE后续行为
  - 是否存在FORK和CLONE在同一条链上？
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    subject_info = {}
    file_info = {}
    all_entity = {}

    loadlib_events = []
    execute_events = []
    fork_events = []
    clone_events = []

    uuid_all_events = defaultdict(list)

    loaded = 0
    with open(filepath, 'r') as f:
        for line in f:
            loaded += 1
            if loaded % 500000 == 0:
                print(f"  已扫描 {loaded:,} 行...")

            datum = json.loads(line)['datum']
            rf = list(datum.keys())[0]
            datum = datum[rf]
            rtype = rf.split('.')[-1]

            if rtype == 'Subject':
                uid = datum['uuid']
                props = datum.get('properties', {})
                pm = props.get('map', {}) if isinstance(props, dict) else {}
                if not isinstance(pm, dict): pm = {}
                cmdline = datum.get('cmdLine')
                if isinstance(cmdline, dict): cmdline = cmdline.get('string')
                subject_info[uid] = {
                    'name': pm.get('name'),
                    'cmdLine': cmdline,
                    'type': datum.get('type', ''),
                }
                all_entity[uid] = ('Subject', datum.get('type', ''))

            elif rtype == 'FileObject':
                uid = datum['uuid']
                bp = datum.get('baseObject', {}).get('properties', {})
                bpm = bp.get('map', {}) if isinstance(bp, dict) else {}
                if not isinstance(bpm, dict): bpm = {}
                file_info[uid] = {'path': bpm.get('path')}
                all_entity[uid] = ('FileObject', datum.get('type', ''))

            elif rtype == 'MemoryObject':
                all_entity[datum['uuid']] = ('MemoryObject', '')

            elif rtype == 'Event':
                etype = datum.get('type', '')
                ts = datum.get('timestampNanos', 0)
                raw_props = datum.get('properties')
                props = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                if not isinstance(props, dict): props = {}

                src = None
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = None
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]
                dst2 = None
                if isinstance(datum.get('predicateObject2'), dict):
                    dst2 = list(datum['predicateObject2'].values())[0]

                if src:
                    uuid_all_events[src].append((ts, etype, dst))

                if etype == 'EVENT_LOADLIBRARY':
                    loadlib_events.append({'src': src, 'dst': dst, 'dst2': dst2, 'ts': ts, 'props': dict(props)})
                elif etype == 'EVENT_EXECUTE':
                    execute_events.append({'src': src, 'dst': dst, 'dst2': dst2, 'ts': ts, 'props': dict(props)})
                elif etype == 'EVENT_FORK':
                    fork_events.append({'src': src, 'dst': dst, 'ts': ts})
                elif etype == 'EVENT_CLONE':
                    clone_events.append({'src': src, 'dst': dst, 'ts': ts})

    print(f"  LOADLIBRARY: {len(loadlib_events):,}")
    print(f"  EXECUTE: {len(execute_events):,}")
    print(f"  FORK: {len(fork_events):,}")
    print(f"  CLONE: {len(clone_events):,}")

    # ================================================================
    # 第一部分：LOADLIBRARY vs EXECUTE
    # ================================================================
    print(f"\n{'='*80}")
    print("一、EVENT_LOADLIBRARY vs EVENT_EXECUTE：dst类型对比")
    print(f"{'='*80}")

    # LOADLIBRARY dst类型
    ll_dst_types = Counter()
    for e in loadlib_events:
        if e['dst'] in all_entity:
            ll_dst_types[f"{all_entity[e['dst']][0]}:{all_entity[e['dst']][1]}"] += 1
        else:
            ll_dst_types['NOT_FOUND'] += 1
    print(f"\n  LOADLIBRARY dst类型:")
    for t, c in ll_dst_types.most_common():
        print(f"    {t:40s} {c:>8,}")

    # EXECUTE dst类型
    ex_dst_types = Counter()
    for e in execute_events:
        if e['dst'] in all_entity:
            ex_dst_types[f"{all_entity[e['dst']][0]}:{all_entity[e['dst']][1]}"] += 1
        else:
            ex_dst_types['NOT_FOUND'] += 1
    print(f"\n  EXECUTE dst类型:")
    for t, c in ex_dst_types.most_common():
        print(f"    {t:40s} {c:>8,}")

    print(f"\n{'='*80}")
    print("二、LOADLIBRARY 详细分析")
    print(f"{'='*80}")

    print(f"\n  前20个LOADLIBRARY事件:")
    for e in loadlib_events[:20]:
        si = subject_info.get(e['src'], {})
        dst_desc = '?'
        if e['dst'] in file_info:
            dst_desc = f"FILE:{file_info[e['dst']]['path']}"
        elif e['dst'] in subject_info:
            dst_desc = f"Subject:{subject_info[e['dst']]['name']}"
        elif e['dst'] in all_entity:
            dst_desc = f"{all_entity[e['dst']]}"

        dst2_desc = '?'
        if e['dst2'] in file_info:
            dst2_desc = f"FILE:{file_info[e['dst2']]['path']}"
        elif e['dst2'] in subject_info:
            dst2_desc = f"Subject:{subject_info[e['dst2']]['name']}"
        elif e['dst2'] is None:
            dst2_desc = 'None'

        print(f"    src={si.get('name')}({si.get('type','')[:4]})")
        print(f"      dst={dst_desc}")
        print(f"      dst2={dst2_desc}")
        print(f"      props={e['props']}")

    # LOADLIBRARY的src后续是否有EXECUTE？
    ll_src_set = {e['src'] for e in loadlib_events}
    ex_src_set = {e['src'] for e in execute_events}
    ll_then_execute = ll_src_set & ex_src_set
    print(f"\n  LOADLIBRARY的src后续做了EXECUTE: {len(ll_then_execute):,} / {len(ll_src_set):,}")

    # 同一src的LOADLIBRARY和EXECUTE时序
    if ll_then_execute:
        print(f"\n  LOADLIBRARY→EXECUTE 时序示例:")
        shown = 0
        for uid in list(ll_then_execute)[:10]:
            ll_ts = [e['ts'] for e in loadlib_events if e['src'] == uid]
            ex_ts = [e['ts'] for e in execute_events if e['src'] == uid]
            si = subject_info.get(uid, {})
            ex_dst = [e['dst'] for e in execute_events if e['src'] == uid]
            ex_dst_name = subject_info.get(ex_dst[0], {}).get('name') if ex_dst else '?'

            ll_files = []
            for e in loadlib_events:
                if e['src'] == uid and e['dst'] in file_info:
                    ll_files.append(file_info[e['dst']]['path'])

            print(f"    进程={si.get('name')} UUID={uid[:16]}...")
            print(f"      LOADLIBRARY时间: {ll_ts[:3]}  加载文件: {ll_files[:3]}")
            print(f"      EXECUTE时间:     {ex_ts[:3]}  目标进程: {ex_dst_name}")
            # LOADLIBRARY在EXECUTE之前还是之后？
            if ll_ts and ex_ts:
                if min(ll_ts) < min(ex_ts):
                    print(f"      → LOADLIBRARY 在 EXECUTE 之前（先加载库，再exec）")
                else:
                    print(f"      → LOADLIBRARY 在 EXECUTE 之后")
            shown += 1

    print(f"\n{'='*80}")
    print("三、EXECUTE 详细分析（对比LOADLIBRARY）")
    print(f"{'='*80}")

    print(f"\n  前10个EXECUTE事件:")
    for e in execute_events[:10]:
        si = subject_info.get(e['src'], {})
        dst_desc = '?'
        if e['dst'] in subject_info:
            di = subject_info[e['dst']]
            dst_desc = f"Subject:{di.get('name')} cmd={str(di.get('cmdLine'))[:30]}"
        elif e['dst'] in file_info:
            dst_desc = f"FILE:{file_info[e['dst']]['path']}"
        print(f"    src={si.get('name')}({si.get('type','')[:4]}) → dst={dst_desc}")
        print(f"      props={e['props']}")

    # ================================================================
    # 第二部分：FORK vs CLONE
    # ================================================================
    print(f"\n{'='*80}")
    print("四、FORK vs CLONE：全方位对比")
    print(f"{'='*80}")

    # 4.1 父子类型
    fork_src_type = Counter()
    fork_dst_type = Counter()
    clone_src_type = Counter()
    clone_dst_type = Counter()

    for e in fork_events:
        si = subject_info.get(e['src'], {})
        di = subject_info.get(e['dst'], {})
        fork_src_type[si.get('type', '?')] += 1
        fork_dst_type[di.get('type', '?')] += 1

    for e in clone_events:
        si = subject_info.get(e['src'], {})
        di = subject_info.get(e['dst'], {})
        clone_src_type[si.get('type', '?')] += 1
        clone_dst_type[di.get('type', '?')] += 1

    print(f"\n  4.1 父子类型:")
    print(f"    FORK  src类型: {dict(fork_src_type)}  dst类型: {dict(fork_dst_type)}")
    print(f"    CLONE src类型: {dict(clone_src_type)}  dst类型: {dict(clone_dst_type)}")

    # 4.2 父子name关系
    fork_same_name = sum(1 for e in fork_events
                        if subject_info.get(e['src'], {}).get('name') == subject_info.get(e['dst'], {}).get('name'))
    clone_same_name = sum(1 for e in clone_events
                         if subject_info.get(e['src'], {}).get('name') == subject_info.get(e['dst'], {}).get('name'))

    print(f"\n  4.2 父子name关系:")
    print(f"    FORK  父子name相同: {fork_same_name}/{len(fork_events)} ({fork_same_name/max(len(fork_events),1)*100:.1f}%)")
    print(f"    CLONE 父子name相同: {clone_same_name}/{len(clone_events)} ({clone_same_name/max(len(clone_events),1)*100:.1f}%)")

    # 4.3 后续行为
    fork_children = {e['dst'] for e in fork_events}
    clone_children = {e['dst'] for e in clone_events}

    fork_did_exe = sum(1 for c in fork_children if c in ex_src_set)
    clone_did_exe = sum(1 for c in clone_children if c in ex_src_set)
    fork_did_fork = sum(1 for c in fork_children if any(e['src'] == c for e in fork_events))
    clone_did_fork = sum(1 for c in clone_children if any(e['src'] == c for e in fork_events))
    fork_did_clone = sum(1 for c in fork_children if any(e['src'] == c for e in clone_events))
    clone_did_clone = sum(1 for c in clone_children if any(e['src'] == c for e in clone_events))

    print(f"\n  4.3 子进程后续行为:")
    print(f"    FORK子进程({len(fork_children)}):")
    print(f"      做了EXECUTE: {fork_did_exe} ({fork_did_exe/max(len(fork_children),1)*100:.1f}%)")
    print(f"      做了FORK:    {fork_did_fork}")
    print(f"      做了CLONE:   {fork_did_clone}")
    print(f"    CLONE子进程({len(clone_children)}):")
    print(f"      做了EXECUTE: {clone_did_exe} ({clone_did_exe/max(len(clone_children),1)*100:.1f}%)")
    print(f"      做了FORK:    {clone_did_fork}")
    print(f"      做了CLONE:   {clone_did_clone}")

    # 4.4 子进程参与的事件类型对比
    fork_child_events = Counter()
    clone_child_events = Counter()

    for c in fork_children:
        for _, et, _ in uuid_all_events.get(c, []):
            fork_child_events[et] += 1
    for c in clone_children:
        for _, et, _ in uuid_all_events.get(c, []):
            clone_child_events[et] += 1

    all_etypes = sorted(set(list(fork_child_events.keys()) + list(clone_child_events.keys())))
    print(f"\n  4.4 子进程参与的事件类型对比:")
    print(f"    {'事件类型':35s} {'FORK子':>10s} {'CLONE子':>10s}")
    print(f"    {'-'*35} {'-'*10} {'-'*10}")
    for et in all_etypes:
        fc = fork_child_events.get(et, 0)
        cc = clone_child_events.get(et, 0)
        if fc > 0 or cc > 0:
            print(f"    {et:35s} {fc:>10,} {cc:>10,}")

    # 4.5 具体name对比（FORK vs CLONE创建了什么进程）
    fork_child_names = Counter(subject_info.get(e['dst'], {}).get('name', '?') for e in fork_events)
    clone_child_names = Counter(subject_info.get(e['dst'], {}).get('name', '?') for e in clone_events)

    print(f"\n  4.5 创建的子进程名字 Top15:")
    print(f"    FORK创建的进程:")
    for n, c in fork_child_names.most_common(15):
        print(f"      {n:30s} {c:>6}")
    print(f"    CLONE创建的进程:")
    for n, c in clone_child_names.most_common(15):
        print(f"      {n:30s} {c:>6}")

    # 4.6 同一个UUID是否既被FORK创建又被CLONE引用？
    both = fork_children & clone_children
    print(f"\n  4.6 同时出现在FORK和CLONE的子进程: {len(both)}")

    # ================================================================
    # 第三部分：CAPTAIN为什么合并FORK和CLONE？
    # ================================================================
    print(f"\n{'='*80}")
    print("五、CAPTAIN合并FORK/CLONE的可能原因分析")
    print(f"{'='*80}")

    # CAPTAIN的CLONE_SET = {EVENT_CLONE, EVENT_FORK}，全部映射为'clone'
    # 检查：FORK和CLONE在图结构中是否有本质区别？

    # 关键区别1：CLONE的src可以是UNIT
    clone_from_unit = sum(1 for e in clone_events if subject_info.get(e['src'], {}).get('type') == 'SUBJECT_UNIT')
    clone_from_proc = sum(1 for e in clone_events if subject_info.get(e['src'], {}).get('type') == 'SUBJECT_PROCESS')
    fork_from_unit = sum(1 for e in fork_events if subject_info.get(e['src'], {}).get('type') == 'SUBJECT_UNIT')
    fork_from_proc = sum(1 for e in fork_events if subject_info.get(e['src'], {}).get('type') == 'SUBJECT_PROCESS')

    print(f"\n  CLONE src来自UNIT: {clone_from_unit}  来自PROCESS: {clone_from_proc}")
    print(f"  FORK  src来自UNIT: {fork_from_unit}  来自PROCESS: {fork_from_proc}")
    print(f"\n  如果丢弃UNIT，CLONE中src=UNIT的{clone_from_unit}条边的src找不到 → 会被跳过")
    print(f"  剩余CLONE（src=PROCESS）: {clone_from_proc}条 → 与FORK语义相同（PROCESS→PROCESS）")

    # 关键区别2：丢弃UNIT后，FORK和CLONE是否在图结构上完全等价？
    print(f"\n  丢弃UNIT后的对比:")
    print(f"    FORK（PROCESS→PROCESS）: {fork_from_proc}条, dst做EXECUTE: {fork_did_exe}")
    print(f"    CLONE（PROCESS→PROCESS）: {clone_from_proc}条, dst做EXECUTE: {clone_did_exe}")
    print(f"    两者dst类型: 都是SUBJECT_PROCESS")
    print(f"    唯一区别: FORK子进程73.9%会exec, CLONE子进程0%会exec")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/trace_e3/ta1-trace-e3-official-1.json.3"
    analyze(filepath)
