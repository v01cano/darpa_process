"""
TRACE E3 CLONE/FORK/EXECUTE 三者关系分析

核心问题：
1. CLONE创建的子进程后续是否会FORK或EXECUTE？
2. FORK和CLONE的子进程类型有什么区别？（PROCESS vs UNIT）
3. 三者之间是否存在链式关系？（如 CLONE→FORK→EXECUTE）
4. SUBJECT_UNIT 和 SUBJECT_PROCESS 在事件链中的角色
5. CLONE的父进程是什么类型？FORK的父进程是什么类型？
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    subject_info = {}
    loaded = 0

    clone_events = []
    fork_events = []
    execute_events = []
    unit_events = []

    # 每个UUID的所有事件
    uuid_all_events = defaultdict(list)

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
                    'name': pm.get('name'),
                    'cmdLine': cmdline,
                    'type': datum.get('type', ''),
                    'parent': parent,
                }

            elif rtype == 'Event':
                etype = datum.get('type', '')
                ts = datum.get('timestampNanos', 0)
                src = None
                if isinstance(datum.get('subject'), dict):
                    src = list(datum['subject'].values())[0]
                dst = None
                if isinstance(datum.get('predicateObject'), dict):
                    dst = list(datum['predicateObject'].values())[0]

                if src:
                    uuid_all_events[src].append((ts, etype, dst))

                if etype == 'EVENT_CLONE':
                    clone_events.append({'src': src, 'dst': dst, 'ts': ts})
                elif etype == 'EVENT_FORK':
                    fork_events.append({'src': src, 'dst': dst, 'ts': ts})
                elif etype == 'EVENT_EXECUTE':
                    execute_events.append({'src': src, 'dst': dst, 'ts': ts})
                elif etype == 'EVENT_UNIT':
                    unit_events.append({'src': src, 'dst': dst, 'ts': ts})

    print(f"  CLONE: {len(clone_events):,}  FORK: {len(fork_events):,}  EXECUTE: {len(execute_events):,}  UNIT: {len(unit_events):,}")

    # 构建映射
    clone_children = {e['dst']: e for e in clone_events}
    fork_children = {e['dst']: e for e in fork_events}
    execute_src_to_dst = {e['src']: e['dst'] for e in execute_events}

    # ============ 1. 三种事件的父子类型 ============
    print(f"\n{'='*80}")
    print("一、三种事件的父/子实体类型")
    print(f"{'='*80}")

    for event_name, events in [("CLONE", clone_events), ("FORK", fork_events), ("EXECUTE", execute_events)]:
        src_types = Counter()
        dst_types = Counter()
        for e in events:
            si = subject_info.get(e['src'], {})
            di = subject_info.get(e['dst'], {})
            src_types[si.get('type', 'NOT_FOUND')] += 1
            dst_types[di.get('type', 'NOT_FOUND')] += 1

        print(f"\n  {event_name} ({len(events)}条):")
        print(f"    subject(src)类型: {dict(src_types)}")
        print(f"    predObj(dst)类型: {dict(dst_types)}")

    # ============ 2. CLONE子进程后续做了什么 ============
    print(f"\n{'='*80}")
    print("二���CLONE子进程后续行为")
    print(f"{'='*80}")

    clone_child_did_fork = 0
    clone_child_did_execute = 0
    clone_child_did_clone = 0
    clone_child_events = Counter()

    for child_uid in clone_children:
        events = uuid_all_events.get(child_uid, [])
        event_types = set(et for _, et, _ in events)
        if 'EVENT_FORK' in event_types:
            clone_child_did_fork += 1
        if 'EVENT_EXECUTE' in event_types:
            clone_child_did_execute += 1
        if 'EVENT_CLONE' in event_types:
            clone_child_did_clone += 1
        for _, et, _ in events:
            clone_child_events[et] += 1

    print(f"  CLONE子进程总数: {len(clone_children):,}")
    print(f"    后续做了FORK:    {clone_child_did_fork:,}")
    print(f"    后续做了EXECUTE: {clone_child_did_execute:,}")
    print(f"    后续做了CLONE:   {clone_child_did_clone:,}")
    print(f"\n  CLONE子进程参与的所有事件类型:")
    for et, cnt in clone_child_events.most_common(15):
        print(f"    {et:30s} {cnt:>10,}")

    # ============ 3. FORK子进程后续做了什么 ============
    print(f"\n{'='*80}")
    print("三、FORK���进程后续行为")
    print(f"{'='*80}")

    fork_child_did_fork = 0
    fork_child_did_execute = 0
    fork_child_did_clone = 0
    fork_child_events = Counter()

    for child_uid in fork_children:
        events = uuid_all_events.get(child_uid, [])
        event_types = set(et for _, et, _ in events)
        if 'EVENT_FORK' in event_types:
            fork_child_did_fork += 1
        if 'EVENT_EXECUTE' in event_types:
            fork_child_did_execute += 1
        if 'EVENT_CLONE' in event_types:
            fork_child_did_clone += 1
        for _, et, _ in events:
            fork_child_events[et] += 1

    print(f"  FORK子进程总数: {len(fork_children):,}")
    print(f"    后续做了FORK:    {fork_child_did_fork:,}")
    print(f"    后续做��EXECUTE: {fork_child_did_execute:,}")
    print(f"    后续做了CLONE:   {fork_child_did_clone:,}")
    print(f"\n  FORK子进程参与的所有事件类型:")
    for et, cnt in fork_child_events.most_common(15):
        print(f"    {et:30s} {cnt:>10,}")

    # ============ 4. 链式关系分析 ============
    print(f"\n{'='*80}")
    print("四、链式关系（CLONE→FORK→EXECUTE 等）")
    print(f"{'='*80}")

    # CLONE→FORK链：CLONE创建的子进程后来做了FORK
    clone_then_fork = []
    for child_uid, clone_ev in clone_children.items():
        events = uuid_all_events.get(child_uid, [])
        for ts, et, dst in events:
            if et == 'EVENT_FORK':
                clone_then_fork.append({
                    'clone_src': clone_ev['src'],
                    'clone_dst': child_uid,
                    'fork_dst': dst,
                })
                break

    print(f"\n  CLONE→FORK 链: {len(clone_then_fork):,}")
    for chain in clone_then_fork[:10]:
        ci = subject_info.get(chain['clone_src'], {})
        cd = subject_info.get(chain['clone_dst'], {})
        fd = subject_info.get(chain['fork_dst'], {})
        print(f"    CLONE: {ci.get('name')}({ci.get('type','')}) → {cd.get('name')}({cd.get('type','')})")
        print(f"    FORK:  {cd.get('name')}({cd.get('type','')}) → {fd.get('name')}({fd.get('type','')})")

    # CLONE→FORK→EXECUTE 完整链
    clone_fork_execute = []
    for chain in clone_then_fork:
        fork_child = chain['fork_dst']
        if fork_child in execute_src_to_dst:
            exe_dst = execute_src_to_dst[fork_child]
            clone_fork_execute.append({
                **chain,
                'execute_dst': exe_dst,
            })

    print(f"\n  CLONE→FORK→EXECUTE ��整链: {len(clone_fork_execute):,}")
    for chain in clone_fork_execute[:10]:
        ci = subject_info.get(chain['clone_src'], {})
        cd = subject_info.get(chain['clone_dst'], {})
        fd = subject_info.get(chain['fork_dst'], {})
        ed = subject_info.get(chain['execute_dst'], {})
        print(f"    CLONE: {ci.get('name')}({ci.get('type','')[:4]}) → {cd.get('name')}({cd.get('type','')[:4]})")
        print(f"    FORK:  {cd.get('name')}({cd.get('type','')[:4]}) → {fd.get('name')}({fd.get('type','')[:4]})")
        print(f"    EXECUTE: {fd.get('name')}({fd.get('type','')[:4]}) → {ed.get('name')}({ed.get('type','')[:4]}) cmd={str(ed.get('cmdLine'))[:40]}")
        print()

    # ============ 5. EVENT_UNIT 的角色 ============
    print(f"\n{'='*80}")
    print("五、EVENT_UNIT 的角色")
    print(f"{'='*80}")

    unit_src_types = Counter()
    unit_dst_types = Counter()
    for e in unit_events:
        si = subject_info.get(e['src'], {})
        di = subject_info.get(e['dst'], {})
        unit_src_types[si.get('type', 'NOT_FOUND')] += 1
        unit_dst_types[di.get('type', 'NOT_FOUND')] += 1

    print(f"  EVENT_UNIT总数: {len(unit_events):,}")
    print(f"    subject(src)类型: {dict(unit_src_types)}")
    print(f"    predObj(dst)类型: {dict(unit_dst_types)}")

    # UNIT是由PROCESS创建的吗？
    unit_from_process = sum(1 for e in unit_events if subject_info.get(e['src'], {}).get('type') == 'SUBJECT_PROCESS')
    unit_from_unit = sum(1 for e in unit_events if subject_info.get(e['src'], {}).get('type') == 'SUBJECT_UNIT')
    print(f"\n  UNIT由PROCESS创建: {unit_from_process:,}")
    print(f"  UNIT由UNIT创建:    {unit_from_unit:,}")

    # UNIT创建后做了CLONE吗���
    unit_dsts = {e['dst'] for e in unit_events}
    unit_then_clone = sum(1 for uid in unit_dsts if uid in set(e['src'] for e in clone_events))
    print(f"\n  UNIT后续做了CLONE(作为src): {unit_then_clone:,}")

    # 完整链: PROCESS→UNIT→CLONE→PROCESS
    print(f"\n  完整链示例: PROCESS ──UNIT──> UNIT ──CLONE──> PROCESS")
    shown = 0
    for e in unit_events:
        unit_uid = e['dst']
        if unit_uid in set(ce['src'] for ce in clone_events):
            # 这个UNIT做了CLONE
            for ce in clone_events:
                if ce['src'] == unit_uid:
                    pi = subject_info.get(e['src'], {})
                    ui = subject_info.get(unit_uid, {})
                    ci = subject_info.get(ce['dst'], {})
                    # 这个CLONE的子进程做了EXECUTE吗？
                    exe_info = ""
                    if ce['dst'] in execute_src_to_dst:
                        ei = subject_info.get(execute_src_to_dst[ce['dst']], {})
                        exe_info = f" ──EXECUTE──> {ei.get('name')}(PROC) cmd={str(ei.get('cmdLine'))[:30]}"

                    print(f"    {pi.get('name')}(PROC) ──UNIT──> {ui.get('name')}(UNIT) ──CLONE──> {ci.get('name')}(PROC){exe_info}")
                    shown += 1
                    if shown >= 15:
                        break
            if shown >= 15:
                break

    # ============ 6. 独立FORK（不涉及CLONE/UNIT）============
    print(f"\n{'='*80}")
    print("六、FORK的父进程来源分析")
    print(f"{'='*80}")

    fork_parent_from_clone = 0  # FORK的父来自CLONE
    fork_parent_is_process = 0
    fork_parent_is_unit = 0

    for e in fork_events:
        parent = e['src']
        pi = subject_info.get(parent, {})
        if pi.get('type') == 'SUBJECT_PROCESS':
            fork_parent_is_process += 1
        elif pi.get('type') == 'SUBJECT_UNIT':
            fork_parent_is_unit += 1
        if parent in clone_children:
            fork_parent_from_clone += 1

    print(f"  FORK父进程是PROCESS: {fork_parent_is_process:,}")
    print(f"  FORK父进程是UNIT:    {fork_parent_is_unit:,}")
    print(f"  FORK父进程来自CLONE: {fork_parent_from_clone:,}")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/trace_e3/ta1-trace-e3-official-1.json.3"
    analyze(filepath)
