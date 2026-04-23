"""
TRACE E3 UUID生命周期分析

分析（参考CADETS/THEIA的完整分析流程）：
1. UUID生成机制：FORK/CLONE时生成？Subject记录何时出现？
2. Subject.name vs EXECUTE dst filename — 哪个是真实进程名？
3. Subject.cmdLine vs Event.cmdLine — 关系是什么？
4. CLONE/FORK后子进程的Subject.name是继承的还是exec后的？
5. 同一UUID是否多次EXECUTE？
6. SUBJECT_UNIT的UUID与SUBJECT_PROCESS的关系
7. EXECUTE的dst到底指向什么？（File? Memory? 跨文件引用？）
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    subject_info = {}  # uuid → {name, cmdLine, type, parent}
    file_info = {}
    memory_info = {}
    all_entity = {}

    uuid_executes = defaultdict(list)  # uuid → [(ts, event_cmdline, dst)]
    clone_events = []
    fork_events = []

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
                props_map = props.get('map', {}) if isinstance(props, dict) else {}
                if not isinstance(props_map, dict): props_map = {}
                cmdline = datum.get('cmdLine')
                if isinstance(cmdline, dict): cmdline = cmdline.get('string')
                parent = None
                if isinstance(datum.get('parentSubject'), dict):
                    parent = list(datum['parentSubject'].values())[0]
                subject_info[uid] = {
                    'name': props_map.get('name'),
                    'cmdLine': cmdline,
                    'type': datum.get('type', ''),
                    'parent': parent,
                }
                all_entity[uid] = ('Subject', datum.get('type', ''))

            elif rtype == 'FileObject':
                uid = datum['uuid']
                bp = datum.get('baseObject', {}).get('properties', {})
                bpm = bp.get('map', {}) if isinstance(bp, dict) else {}
                file_info[uid] = {'path': bpm.get('path') if isinstance(bpm, dict) else None}
                all_entity[uid] = ('FileObject', datum.get('type', ''))

            elif rtype == 'MemoryObject':
                memory_info[datum['uuid']] = True
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

                if etype == 'EVENT_EXECUTE' and src:
                    uuid_executes[src].append((ts, props.get('cmdLine'), dst))
                if etype == 'EVENT_CLONE' and src and dst:
                    clone_events.append({'src': src, 'dst': dst, 'ts': ts})
                if etype == 'EVENT_FORK' and src and dst:
                    fork_events.append({'src': src, 'dst': dst, 'ts': ts})

    # 排序
    for uid in uuid_executes:
        uuid_executes[uid].sort()

    print(f"\n  Subject(PROCESS): {sum(1 for v in subject_info.values() if v['type']=='SUBJECT_PROCESS'):,}")
    print(f"  Subject(UNIT):    {sum(1 for v in subject_info.values() if v['type']=='SUBJECT_UNIT'):,}")
    print(f"  FileObject:       {len(file_info):,}")
    print(f"  CLONE: {len(clone_events):,}  FORK: {len(fork_events):,}")
    print(f"  做过EXECUTE的UUID: {len(uuid_executes):,}")

    # ============ 1. UUID生成机制 ============
    print(f"\n{'='*80}")
    print("一、UUID生成机制")
    print(f"{'='*80}")

    process_uuids = {u for u, v in subject_info.items() if v['type'] == 'SUBJECT_PROCESS'}
    unit_uuids = {u for u, v in subject_info.items() if v['type'] == 'SUBJECT_UNIT'}

    clone_children = {e['dst'] for e in clone_events}
    fork_children = {e['dst'] for e in fork_events}
    all_children = clone_children | fork_children

    proc_from_clone = process_uuids & clone_children
    proc_from_fork = process_uuids & fork_children
    proc_no_event = process_uuids - all_children

    print(f"  SUBJECT_PROCESS总数: {len(process_uuids):,}")
    print(f"    来自CLONE: {len(proc_from_clone):,}")
    print(f"    来自FORK:  {len(proc_from_fork):,}")
    print(f"    无创建事件: {len(proc_no_event):,}")

    unit_from_clone = unit_uuids & clone_children
    unit_from_fork = unit_uuids & fork_children
    unit_no_event = unit_uuids - all_children
    print(f"\n  SUBJECT_UNIT总数: {len(unit_uuids):,}")
    print(f"    来自CLONE: {len(unit_from_clone):,}")
    print(f"    来自FORK:  {len(unit_from_fork):,}")
    print(f"    无创建事件: {len(unit_no_event):,}")

    # ============ 2. EXECUTE dst类型 ============
    print(f"\n{'='*80}")
    print("二、EXECUTE事件dst类型（全文件）")
    print(f"{'='*80}")

    exe_dst_file = 0
    exe_dst_mem = 0
    exe_dst_subj = 0
    exe_dst_not_found = 0

    for uid, execs in uuid_executes.items():
        for ts, cmd, dst in execs:
            if dst in file_info:
                exe_dst_file += 1
            elif dst in memory_info:
                exe_dst_mem += 1
            elif dst in subject_info:
                exe_dst_subj += 1
            elif dst:
                exe_dst_not_found += 1

    total_exe = sum(len(v) for v in uuid_executes.values())
    print(f"  EXECUTE总数: {total_exe:,}")
    print(f"    dst=FileObject:   {exe_dst_file:,}")
    print(f"    dst=MemoryObject: {exe_dst_mem:,}")
    print(f"    dst=Subject:      {exe_dst_subj:,}")
    print(f"    dst=NOT_FOUND:    {exe_dst_not_found:,}")

    # 详情
    print(f"\n  前15个EXECUTE详情:")
    shown = 0
    for uid, execs in uuid_executes.items():
        for ts, cmd, dst in execs:
            si = subject_info.get(uid, {})
            dst_desc = '?'
            if dst in file_info:
                dst_desc = f"FILE:{file_info[dst]['path']}"
            elif dst in memory_info:
                dst_desc = "MemoryObject"
            elif dst in subject_info:
                dst_desc = f"Subject:{subject_info[dst]['name']}"
            print(f"    进程={si.get('name')}({si.get('type','')}) cmd={str(si.get('cmdLine'))[:30]}")
            print(f"      Event.cmdLine={cmd}")
            print(f"      dst={dst_desc}")
            shown += 1
            if shown >= 15:
                break
        if shown >= 15:
            break

    # ============ 3. Subject.name vs EXECUTE dst ============
    print(f"\n{'='*80}")
    print("三、Subject.name vs EXECUTE dst filename 对比")
    print(f"{'='*80}")

    path_eq_first = 0
    path_eq_last = 0
    path_ne_all = 0
    examples = []

    for uid, execs in uuid_executes.items():
        info = subject_info.get(uid, {})
        if info.get('type') != 'SUBJECT_PROCESS':
            continue
        subj_name = info.get('name')
        first_dst = execs[0][2]
        last_dst = execs[-1][2]
        first_name = file_info.get(first_dst, {}).get('path')
        last_name = file_info.get(last_dst, {}).get('path')

        if subj_name and first_name and subj_name == first_name:
            path_eq_first += 1
        elif subj_name and last_name and subj_name == last_name:
            path_eq_last += 1
        else:
            path_ne_all += 1
            if len(examples) < 15:
                examples.append((uid, subj_name, info.get('cmdLine'), first_name, last_name,
                                execs[0][1], execs[-1][1], len(execs)))

    total_proc_exe = path_eq_first + path_eq_last + path_ne_all
    if total_proc_exe > 0:
        print(f"  做过EXECUTE的PROCESS数: {total_proc_exe:,}")
        print(f"    name == 第1次dst: {path_eq_first:,} ({path_eq_first/total_proc_exe*100:.1f}%)")
        print(f"    name == 最后dst:  {path_eq_last:,} ({path_eq_last/total_proc_exe*100:.1f}%)")
        print(f"    name != 任何dst:  {path_ne_all:,} ({path_ne_all/total_proc_exe*100:.1f}%)")

    if examples:
        print(f"\n  Subject.name != 任何dst的例子:")
        for uid, sn, sc, fd, ld, fc, lc, n in examples[:10]:
            print(f"    Subject.name={sn}  cmdLine={str(sc)[:40]}")
            print(f"      第1次dst={fd}  Event.cmdLine={fc}")
            if n > 1:
                print(f"      最后dst={ld}  Event.cmdLine={lc}")
            print()

    # ============ 4. CLONE/FORK后子进程分析 ============
    print(f"\n{'='*80}")
    print("四、CLONE/FORK后子进程的Subject.name分析")
    print(f"{'='*80}")

    for event_name, events in [("CLONE", clone_events), ("FORK", fork_events)]:
        if not events:
            continue
        same_name = 0
        diff_name = 0
        child_is_process = 0
        child_is_unit = 0
        child_did_execute = 0

        for e in events:
            parent = subject_info.get(e['src'], {})
            child = subject_info.get(e['dst'], {})
            if child.get('type') == 'SUBJECT_PROCESS':
                child_is_process += 1
            elif child.get('type') == 'SUBJECT_UNIT':
                child_is_unit += 1
            if parent.get('name') == child.get('name'):
                same_name += 1
            else:
                diff_name += 1
            if e['dst'] in uuid_executes:
                child_did_execute += 1

        print(f"\n  {event_name}事件: {len(events):,}")
        print(f"    子进程是PROCESS: {child_is_process:,}")
        print(f"    子进程是UNIT:    {child_is_unit:,}")
        print(f"    父子name相同:    {same_name:,} ({same_name/len(events)*100:.1f}%)")
        print(f"    父子name不同:    {diff_name:,} ({diff_name/len(events)*100:.1f}%)")
        print(f"    子进程做过EXECUTE: {child_did_execute:,}")

        print(f"\n    前10个{event_name}详情:")
        for e in events[:10]:
            pi = subject_info.get(e['src'], {})
            ci = subject_info.get(e['dst'], {})
            print(f"      父={pi.get('name')}({pi.get('type','')}) → 子={ci.get('name')}({ci.get('type','')})")
            if pi.get('cmdLine') != ci.get('cmdLine'):
                print(f"        父cmd={str(pi.get('cmdLine'))[:50]}")
                print(f"        子cmd={str(ci.get('cmdLine'))[:50]}")

    # ============ 5. 多次EXECUTE ============
    print(f"\n{'='*80}")
    print("五、多次EXECUTE的UUID")
    print(f"{'='*80}")

    exe_dist = Counter(len(v) for v in uuid_executes.values())
    print(f"  EXECUTE次数分布:")
    for n, cnt in sorted(exe_dist.items()):
        print(f"    {n}次: {cnt:,}")
        if n > 5: break

    multi = [(u, e) for u, e in uuid_executes.items() if len(e) > 1]
    if multi:
        print(f"\n  多次EXECUTE的进程(前10个):")
        for uid, execs in multi[:10]:
            info = subject_info.get(uid, {})
            print(f"    {info.get('name')}({info.get('type','')}) cmdLine={str(info.get('cmdLine'))[:40]}")
            for ts, cmd, dst in execs[:5]:
                dn = file_info.get(dst, {}).get('path', '?') if dst in file_info else '?'
                print(f"      EXECUTE → {dn}  Event.cmdLine={cmd}")

    # ============ 6. Subject.cmdLine分析 ============
    print(f"\n{'='*80}")
    print("六、Subject.cmdLine vs Event.cmdLine")
    print(f"{'='*80}")

    same = diff = only_subj = only_evt = both_none = 0
    for uid, execs in uuid_executes.items():
        info = subject_info.get(uid, {})
        s_cmd = info.get('cmdLine')
        for ts, e_cmd, dst in execs:
            if s_cmd and e_cmd:
                if s_cmd == e_cmd: same += 1
                else: diff += 1
            elif s_cmd and not e_cmd: only_subj += 1
            elif e_cmd and not s_cmd: only_evt += 1
            else: both_none += 1

    print(f"  Subject.cmdLine == Event.cmdLine: {same:,}")
    print(f"  Subject.cmdLine != Event.cmdLine: {diff:,}")
    print(f"  仅Subject有: {only_subj:,}")
    print(f"  仅Event有: {only_evt:,}")
    print(f"  两者都None: {both_none:,}")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/trace_e3/ta1-trace-e3-official-1.json.3"
    analyze(filepath)
