"""
TRACE E3 FORK与EXECUTE关系分析

核心问题：
1. EXECUTE前是否一定有FORK？（即fork+exec是配对的吗？）
2. FORK后是否一定有EXECUTE？（即fork是否独立存在？）
3. FORK的子进程UUID == EXECUTE的src UUID吗？
4. EXECUTE的dst Subject的cmdLine是什么？
5. 完整的进程创建链条是什么？
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    subject_info = {}
    loaded = 0

    fork_events = []   # [{src, dst, ts}]
    execute_events = [] # [{src, dst, ts}]
    clone_events = []

    # 收集每个UUID作为subject的第一个和最后一个事件
    uuid_first_event = {}  # uuid → (ts, etype)
    uuid_all_events = defaultdict(list)  # uuid → [(ts, etype, dst)]

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
                    if src not in uuid_first_event:
                        uuid_first_event[src] = (ts, etype)

                if etype == 'EVENT_FORK':
                    fork_events.append({'src': src, 'dst': dst, 'ts': ts})
                elif etype == 'EVENT_EXECUTE':
                    execute_events.append({'src': src, 'dst': dst, 'ts': ts})
                elif etype == 'EVENT_CLONE':
                    clone_events.append({'src': src, 'dst': dst, 'ts': ts})

    print(f"  Subject(PROCESS): {sum(1 for v in subject_info.values() if v['type']=='SUBJECT_PROCESS'):,}")
    print(f"  FORK: {len(fork_events):,}  EXECUTE: {len(execute_events):,}  CLONE: {len(clone_events):,}")

    # ============ 1. FORK与EXECUTE的配对关系 ============
    print(f"\n{'='*80}")
    print("一、FORK与EXECUTE的配对关系")
    print(f"{'='*80}")

    fork_children = {e['dst']: e for e in fork_events}  # child_uuid → fork_event
    execute_src_set = {e['src'] for e in execute_events}  # 做过EXECUTE的uuid

    # FORK的子进程是否做了EXECUTE？
    fork_then_execute = 0
    fork_no_execute = 0
    for child_uuid in fork_children:
        if child_uuid in execute_src_set:
            fork_then_execute += 1
        else:
            fork_no_execute += 1

    print(f"  FORK子进程总数: {len(fork_children):,}")
    print(f"    随后做了EXECUTE: {fork_then_execute:,} ({fork_then_execute/max(len(fork_children),1)*100:.1f}%)")
    print(f"    未做EXECUTE:     {fork_no_execute:,} ({fork_no_execute/max(len(fork_children),1)*100:.1f}%)")

    # EXECUTE的src是否来自FORK？
    execute_from_fork = 0
    execute_not_from_fork = 0
    for e in execute_events:
        if e['src'] in fork_children:
            execute_from_fork += 1
        else:
            execute_not_from_fork += 1

    print(f"\n  EXECUTE事件总数: {len(execute_events):,}")
    print(f"    src来自FORK子进程: {execute_from_fork:,} ({execute_from_fork/max(len(execute_events),1)*100:.1f}%)")
    print(f"    src不来自FORK:     {execute_not_from_fork:,} ({execute_not_from_fork/max(len(execute_events),1)*100:.1f}%)")

    # ============ 2. 完整的 FORK→EXECUTE 链条 ============
    print(f"\n{'='*80}")
    print("二、FORK→EXECUTE 完整链条（前20个）")
    print(f"{'='*80}")

    shown = 0
    for e in execute_events:
        if e['src'] in fork_children:
            fork_ev = fork_children[e['src']]
            parent_info = subject_info.get(fork_ev['src'], {})
            child_info = subject_info.get(e['src'], {})  # FORK的子进程 = EXECUTE的src
            new_info = subject_info.get(e['dst'], {})      # EXECUTE的dst = 新身份

            print(f"\n  链条 #{shown+1}:")
            print(f"    FORK:    父={parent_info.get('name')}  →  子={child_info.get('name')} (UUID={str(e['src'])[:16]}...)")
            print(f"    EXECUTE: 子={child_info.get('name')}  →  新={new_info.get('name')} (UUID={str(e['dst'])[:16]}...)")
            print(f"    父cmdLine: {str(parent_info.get('cmdLine'))[:60]}")
            print(f"    子cmdLine: {str(child_info.get('cmdLine'))[:60]}")
            print(f"    新cmdLine: {str(new_info.get('cmdLine'))[:60]}")
            shown += 1
            if shown >= 20:
                break

    # ============ 3. 不来自FORK的EXECUTE ============
    print(f"\n{'='*80}")
    print("三、不来自FORK的EXECUTE（前15个）")
    print(f"{'='*80}")

    shown = 0
    for e in execute_events:
        if e['src'] not in fork_children:
            src_info = subject_info.get(e['src'], {})
            dst_info = subject_info.get(e['dst'], {})
            # 看这个src之前有什么事件
            prev_events = uuid_all_events.get(e['src'], [])
            prev_types = [et for _, et, _ in prev_events if et != 'EVENT_EXECUTE'][:5]

            print(f"\n  EXECUTE #{shown+1}:")
            print(f"    src={src_info.get('name')}({src_info.get('type','')}) cmdLine={str(src_info.get('cmdLine'))[:40]}")
            print(f"    dst={dst_info.get('name')}({dst_info.get('type','')}) cmdLine={str(dst_info.get('cmdLine'))[:40]}")
            print(f"    src的其他事件: {prev_types}")
            shown += 1
            if shown >= 15:
                break

    # ============ 4. FORK后不EXECUTE的子进程 ============
    print(f"\n{'='*80}")
    print("四、FORK后未EXECUTE的子进程（前15个）")
    print(f"{'='*80}")

    shown = 0
    for child_uuid, fork_ev in fork_children.items():
        if child_uuid not in execute_src_set:
            parent_info = subject_info.get(fork_ev['src'], {})
            child_info = subject_info.get(child_uuid, {})
            child_events = [(t, et) for t, et, _ in uuid_all_events.get(child_uuid, [])[:10]]

            print(f"\n  子进程: {child_info.get('name')}({child_info.get('type','')})")
            print(f"    cmdLine: {str(child_info.get('cmdLine'))[:60]}")
            print(f"    父进程: {parent_info.get('name')}")
            print(f"    子进程事件(前10): {[et for _, et in child_events]}")
            shown += 1
            if shown >= 15:
                break

    # ============ 5. CLONE与EXECUTE的关系 ============
    print(f"\n{'='*80}")
    print("五、CLONE与EXECUTE的关系")
    print(f"{'='*80}")

    clone_children_set = {e['dst'] for e in clone_events}
    clone_then_execute = sum(1 for c in clone_children_set if c in execute_src_set)
    print(f"  CLONE子进程总数: {len(clone_children_set):,}")
    print(f"    随后做了EXECUTE: {clone_then_execute:,}")
    print(f"    未做EXECUTE:     {len(clone_children_set) - clone_then_execute:,}")

    # ============ 6. EXECUTE链（A exec→B, B exec→C） ============
    print(f"\n{'='*80}")
    print("六、EXECUTE链（连续exec）")
    print(f"{'='*80}")

    # 建立 EXECUTE 图: src → dst
    exec_graph = {}
    for e in execute_events:
        exec_graph[e['src']] = e['dst']

    # 找链
    chains = []
    visited = set()
    for start in exec_graph:
        if start in visited:
            continue
        chain = [start]
        current = start
        while current in exec_graph:
            next_node = exec_graph[current]
            chain.append(next_node)
            visited.add(current)
            current = next_node
            if current in visited:
                break
        if len(chain) > 2:
            chains.append(chain)

    print(f"  EXECUTE链（长度>2）: {len(chains):,}")
    for chain in chains[:10]:
        names = [subject_info.get(u, {}).get('name', '?') for u in chain]
        cmds = [str(subject_info.get(u, {}).get('cmdLine', ''))[:30] for u in chain]
        print(f"    链: {' → '.join(names)}")
        print(f"    cmd: {' → '.join(cmds)}")

    # ============ 7. 总结：cmdLine应该放在哪里？ ============
    print(f"\n{'='*80}")
    print("七、cmdLine放置位置分析")
    print(f"{'='*80}")

    # FORK边：父→子（继承身份），子进程有cmdLine吗？
    fork_child_has_cmd = sum(1 for c in fork_children if subject_info.get(c, {}).get('cmdLine'))
    print(f"  FORK子进程有cmdLine: {fork_child_has_cmd:,} / {len(fork_children):,}")

    # EXECUTE dst（新身份）有cmdLine吗？
    exe_dst_has_cmd = sum(1 for e in execute_events if subject_info.get(e['dst'], {}).get('cmdLine'))
    print(f"  EXECUTE dst(新身份)有cmdLine: {exe_dst_has_cmd:,} / {len(execute_events):,}")

    # FORK的子进程cmdLine vs EXECUTE dst的cmdLine
    print(f"\n  FORK→EXECUTE 链中cmdLine变化:")
    shown = 0
    for e in execute_events:
        if e['src'] in fork_children and shown < 10:
            child_cmd = subject_info.get(e['src'], {}).get('cmdLine')
            new_cmd = subject_info.get(e['dst'], {}).get('cmdLine')
            child_name = subject_info.get(e['src'], {}).get('name')
            new_name = subject_info.get(e['dst'], {}).get('name')
            print(f"    FORK子={child_name} cmd={str(child_cmd)[:30]}  →  EXECUTE新={new_name} cmd={str(new_cmd)[:30]}")
            shown += 1


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/trace_e3/ta1-trace-e3-official-1.json.3"
    analyze(filepath)
