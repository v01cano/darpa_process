"""
TRACE E3 实体类型与事件关系分析

分析：
1. 各实体类型（含TRACE特有的SUBJECT_UNIT/SrcSinkObject/UnitDependency）是否被Event引用
2. 各实体类型参与了哪些事件
3. EXECUTE事件的dst是什么类型
4. CLONE/FORK事件的结构
5. SUBJECT_UNIT vs SUBJECT_PROCESS 的区别
6. EVENT_UNIT 的结构
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    entity_type = {}
    subject_info = {}
    file_info = {}

    loaded = 0
    with open(filepath, 'r') as f:
        for line in f:
            loaded += 1
            datum = json.loads(line)['datum']
            rtype_full = list(datum.keys())[0]
            datum = datum[rtype_full]
            rtype = rtype_full.split('.')[-1]

            if rtype == 'Subject':
                entity_type[datum['uuid']] = ('Subject', datum.get('type', ''))
                props = datum.get('properties', {})
                props_map = props.get('map', {}) if isinstance(props, dict) else {}
                if not isinstance(props_map, dict): props_map = {}
                cmdline = datum.get('cmdLine')
                if isinstance(cmdline, dict): cmdline = cmdline.get('string')
                subject_info[datum['uuid']] = {
                    'name': props_map.get('name'),
                    'cmdLine': cmdline,
                    'type': datum.get('type', ''),
                    'ppid': props_map.get('ppid'),
                }
            elif rtype == 'FileObject':
                entity_type[datum['uuid']] = ('FileObject', datum.get('type', ''))
                base = datum.get('baseObject', {})
                bp = base.get('properties', {}).get('map', {}) if isinstance(base.get('properties'), dict) else {}
                file_info[datum['uuid']] = {'path': bp.get('path') if isinstance(bp, dict) else None}
            elif rtype == 'NetFlowObject':
                entity_type[datum['uuid']] = ('NetFlowObject', 'NetFlow')
            elif rtype == 'MemoryObject':
                entity_type[datum['uuid']] = ('MemoryObject', 'Memory')
            elif rtype == 'UnnamedPipeObject':
                entity_type[datum['uuid']] = ('UnnamedPipeObject', 'Pipe')
            elif rtype == 'SrcSinkObject':
                entity_type[datum['uuid']] = ('SrcSinkObject', datum.get('type', ''))

    print(f"  第1遍完成，共 {len(entity_type):,} 个实体")
    tc = Counter(f"{v[0]}:{v[1]}" for v in entity_type.values())
    for t, c in tc.most_common(20):
        print(f"    {t}: {c:,}")

    # 第2遍
    type_role_events = defaultdict(Counter)
    type_event_has_path = defaultdict(int)
    type_event_total = defaultdict(int)
    type_got_name = defaultdict(set)
    type_name_samples = defaultdict(list)

    execute_dst_types = Counter()
    execute_events = []
    clone_events = []
    fork_events = []
    unit_events = []

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
            if rtype != 'Event': continue

            etype = datum.get('type', '')
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

            if src and src in entity_type:
                ekey = f"{entity_type[src][0]}:{entity_type[src][1]}"
                type_role_events[(ekey, 'as_subject')][etype] += 1

            if dst and dst in entity_type:
                ekey = f"{entity_type[dst][0]}:{entity_type[dst][1]}"
                type_role_events[(ekey, 'as_predObj')][etype] += 1
                type_event_total[(ekey, etype)] += 1
                if isinstance(datum.get('predicateObjectPath'), dict):
                    path = datum['predicateObjectPath'].get('string', '')
                    if path:
                        type_event_has_path[(ekey, etype)] += 1
                        type_got_name[ekey].add(dst)
                        if len(type_name_samples[ekey]) < 5:
                            type_name_samples[ekey].append(path)

            if dst2 and dst2 in entity_type:
                ekey = f"{entity_type[dst2][0]}:{entity_type[dst2][1]}"
                type_role_events[(ekey, 'as_predObj2')][etype] += 1

            if etype == 'EVENT_EXECUTE' and dst:
                if dst in entity_type:
                    execute_dst_types[f"{entity_type[dst][0]}:{entity_type[dst][1]}"] += 1
                else:
                    execute_dst_types["NOT_FOUND"] += 1
                execute_events.append({'src': src, 'dst': dst, 'dst2': dst2,
                                        'cmdline': props.get('cmdLine'),
                                        'ts': datum.get('timestampNanos', 0)})

            if etype == 'EVENT_CLONE':
                clone_events.append({'src': src, 'dst': dst, 'props': dict(props)})
            if etype == 'EVENT_FORK':
                fork_events.append({'src': src, 'dst': dst, 'props': dict(props)})
            if etype == 'EVENT_UNIT':
                unit_events.append({'src': src, 'dst': dst})

    # 输出
    all_ekeys = sorted(set(k[0] for k in type_role_events.keys()))
    type_total = Counter(f"{v[0]}:{v[1]}" for v in entity_type.values())

    print(f"\n{'='*80}")
    print("一、各实体类型被Event引用情况")
    print(f"{'='*80}")
    for ekey in all_ekeys:
        total = type_total.get(ekey, 0)
        as_subj = sum(type_role_events.get((ekey, 'as_subject'), {}).values())
        as_pred = sum(type_role_events.get((ekey, 'as_predObj'), {}).values())
        as_pred2 = sum(type_role_events.get((ekey, 'as_predObj2'), {}).values())
        print(f"\n  {ekey} (共{total:,}个)")
        print(f"    作为subject: {as_subj:,}")
        print(f"    作为predObj: {as_pred:,}")
        print(f"    作为predObj2: {as_pred2:,}")
        pred_ev = type_role_events.get((ekey, 'as_predObj'), {})
        if pred_ev:
            print(f"    作为predObj的事件:")
            for et, cnt in pred_ev.most_common(8):
                t = type_event_total.get((ekey, et), 0)
                hp = type_event_has_path.get((ekey, et), 0)
                pp = hp/t*100 if t > 0 else 0
                print(f"      {et:35s} {cnt:>10,}  (有path={hp:,} {pp:.0f}%)")
        got = type_got_name.get(ekey, set())
        print(f"    通过path获得名字: {len(got):,}/{total:,}")
        for s in type_name_samples.get(ekey, [])[:3]:
            print(f"      {s}")

    print(f"\n{'='*80}")
    print("二、EXECUTE事件dst类型")
    print(f"{'='*80}")
    print(f"  EXECUTE事件数: {len(execute_events):,}")
    for t, c in execute_dst_types.most_common():
        print(f"    {t:40s} {c:>8,}")
    print(f"\n  前10个EXECUTE详情:")
    for e in execute_events[:10]:
        si = subject_info.get(e['src'], {})
        dst_desc = '?'
        if e['dst'] in file_info:
            dst_desc = f"FILE:{file_info[e['dst']]['path']}"
        elif e['dst'] in subject_info:
            dst_desc = f"Subject:{subject_info[e['dst']]['name']}"
        elif e['dst'] in entity_type:
            dst_desc = f"{entity_type[e['dst']]}"
        print(f"    进程={si.get('name')} cmdLine={str(si.get('cmdLine'))[:40]}")
        print(f"      dst={dst_desc}")

    print(f"\n{'='*80}")
    print("三、CLONE/FORK事件")
    print(f"{'='*80}")
    print(f"  CLONE: {len(clone_events):,}  FORK: {len(fork_events):,}")

    if clone_events:
        print(f"\n  前10个CLONE:")
        for e in clone_events[:10]:
            si = subject_info.get(e['src'], {})
            di = subject_info.get(e['dst'], {})
            print(f"    父={si.get('name')}({si.get('type','')})  →  子={di.get('name')}({di.get('type','')})")
            if si.get('cmdLine') != di.get('cmdLine'):
                print(f"      父cmd={str(si.get('cmdLine'))[:50]}")
                print(f"      子cmd={str(di.get('cmdLine'))[:50]}")
            print(f"      props keys: {list(e['props'].keys())}")

    if fork_events:
        print(f"\n  前10个FORK:")
        for e in fork_events[:10]:
            si = subject_info.get(e['src'], {})
            di = subject_info.get(e['dst'], {})
            print(f"    父={si.get('name')}({si.get('type','')})  →  子={di.get('name')}({di.get('type','')})")
            if si.get('cmdLine') != di.get('cmdLine'):
                print(f"      父cmd={str(si.get('cmdLine'))[:50]}")
                print(f"      子cmd={str(di.get('cmdLine'))[:50]}")
            print(f"      props keys: {list(e['props'].keys())}")

    print(f"\n{'='*80}")
    print("四、SUBJECT_UNIT vs SUBJECT_PROCESS")
    print(f"{'='*80}")
    unit_count = sum(1 for v in entity_type.values() if v == ('Subject', 'SUBJECT_UNIT'))
    proc_count = sum(1 for v in entity_type.values() if v == ('Subject', 'SUBJECT_PROCESS'))
    print(f"  SUBJECT_PROCESS: {proc_count:,}")
    print(f"  SUBJECT_UNIT:    {unit_count:,}")
    print(f"  EVENT_UNIT:      {len(unit_events):,}")

    if unit_events:
        print(f"\n  前10个EVENT_UNIT:")
        for e in unit_events[:10]:
            si = subject_info.get(e['src'], {})
            di = subject_info.get(e['dst'], {})
            print(f"    subject={si.get('name')}({si.get('type','')})  →  predObj={di.get('name')}({di.get('type','')})")

    # SUBJECT_UNIT作为subject的事件
    unit_subj = type_role_events.get(('Subject:SUBJECT_UNIT', 'as_subject'), {})
    proc_subj = type_role_events.get(('Subject:SUBJECT_PROCESS', 'as_subject'), {})
    if unit_subj:
        print(f"\n  SUBJECT_UNIT作为subject的事件:")
        for et, cnt in unit_subj.most_common(10):
            print(f"    {et:35s} {cnt:>10,}")
    if proc_subj:
        print(f"\n  SUBJECT_PROCESS作为subject的事件:")
        for et, cnt in proc_subj.most_common(10):
            print(f"    {et:35s} {cnt:>10,}")

    print(f"\n{'='*80}")
    print("五、SrcSinkObject分析")
    print(f"{'='*80}")
    srcsink_types = Counter(v[1] for v in entity_type.values() if v[0] == 'SrcSinkObject')
    total_srcsink = sum(srcsink_types.values())
    print(f"  SrcSinkObject总数: {total_srcsink:,}")
    print(f"  子类型Top10:")
    for t, c in srcsink_types.most_common(10):
        print(f"    {t:40s} {c:>10,}")

    # SrcSinkObject被引用的总事件数
    srcsink_total_events = 0
    for ekey in all_ekeys:
        if ekey.startswith('SrcSinkObject:'):
            srcsink_total_events += sum(type_role_events.get((ekey, 'as_predObj'), {}).values())
    print(f"\n  SrcSinkObject作为predObj的总事件数: {srcsink_total_events:,}")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/trace_e3/ta1-trace-e3-official-1.json.3"
    analyze(filepath)
