"""
FiveDirections E3 实体与事件关系深度分析

核心问题：
1. SUBJECT_THREAD vs SUBJECT_PROCESS 的关系（thread是process的线程？）
2. EVENT_EXECUTE 的 dst 类型（Subject? FileObject?）
3. EVENT_LOADLIBRARY 的 dst 类型（预期是FileObject）
4. EVENT_CREATE_THREAD 的结构
5. RegistryKeyObject 参与哪些事件
6. 各实体类型被Event引用情况
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    entity_type = {}
    subject_info = {}
    file_info = {}
    reg_info = {}

    loaded = 0
    with open(filepath, 'r') as f:
        for line in f:
            loaded += 1
            datum = json.loads(line)['datum']
            rtype_full = list(datum.keys())[0]
            datum = datum[rtype_full]
            rtype = rtype_full.split('.')[-1]

            if rtype == 'Subject':
                uid = datum['uuid']
                entity_type[uid] = ('Subject', datum.get('type', ''))
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
                }
            elif rtype == 'FileObject':
                entity_type[datum['uuid']] = ('FileObject', datum.get('type', ''))
                file_info[datum['uuid']] = {'type': datum.get('type')}
            elif rtype == 'RegistryKeyObject':
                entity_type[datum['uuid']] = ('RegistryKeyObject', 'Reg')
                reg_info[datum['uuid']] = {'key': datum.get('key')}
            elif rtype == 'NetFlowObject':
                entity_type[datum['uuid']] = ('NetFlowObject', 'NetFlow')
            elif rtype == 'MemoryObject':
                entity_type[datum['uuid']] = ('MemoryObject', 'Memory')
            elif rtype == 'SrcSinkObject':
                entity_type[datum['uuid']] = ('SrcSinkObject', datum.get('type', ''))
            elif rtype == 'PacketSocketObject':
                entity_type[datum['uuid']] = ('PacketSocketObject', 'Packet')

    print(f"  第1遍完成: {len(entity_type):,} 个实体")
    tc = Counter(f"{v[0]}:{v[1]}" for v in entity_type.values())
    for t, c in tc.most_common(15):
        print(f"    {t}: {c:,}")

    # 第2遍：事件分析
    type_role_events = defaultdict(Counter)
    type_event_has_path = defaultdict(int)
    type_event_total = defaultdict(int)
    type_got_path = defaultdict(set)
    type_path_samples = defaultdict(list)

    execute_dst_types = Counter()
    loadlib_dst_types = Counter()
    execute_events = []
    loadlib_events = []
    fork_events = []
    create_thread_events = []

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

            src = None
            if isinstance(datum.get('subject'), dict):
                src = list(datum['subject'].values())[0]
            dst = None
            if isinstance(datum.get('predicateObject'), dict):
                dst = list(datum['predicateObject'].values())[0]
            dst2 = None
            if isinstance(datum.get('predicateObject2'), dict):
                dst2 = list(datum['predicateObject2'].values())[0]

            path = ''
            if isinstance(datum.get('predicateObjectPath'), dict):
                path = datum['predicateObjectPath'].get('string', '')

            if src and src in entity_type:
                ekey = f"{entity_type[src][0]}:{entity_type[src][1]}"
                type_role_events[(ekey, 'as_subject')][etype] += 1

            if dst and dst in entity_type:
                ekey = f"{entity_type[dst][0]}:{entity_type[dst][1]}"
                type_role_events[(ekey, 'as_predObj')][etype] += 1
                type_event_total[(ekey, etype)] += 1
                if path:
                    type_event_has_path[(ekey, etype)] += 1
                    type_got_path[ekey].add(dst)
                    if len(type_path_samples[ekey]) < 5:
                        type_path_samples[ekey].append(path)

            if dst2 and dst2 in entity_type:
                ekey = f"{entity_type[dst2][0]}:{entity_type[dst2][1]}"
                type_role_events[(ekey, 'as_predObj2')][etype] += 1

            # EXECUTE/LOADLIBRARY dst类型
            if etype == 'EVENT_EXECUTE':
                if dst in entity_type:
                    execute_dst_types[f"{entity_type[dst][0]}:{entity_type[dst][1]}"] += 1
                else:
                    execute_dst_types["NOT_FOUND"] += 1
                if len(execute_events) < 20:
                    execute_events.append({'src': src, 'dst': dst, 'path': path})

            if etype == 'EVENT_LOADLIBRARY':
                if dst in entity_type:
                    loadlib_dst_types[f"{entity_type[dst][0]}:{entity_type[dst][1]}"] += 1
                else:
                    loadlib_dst_types["NOT_FOUND"] += 1
                if len(loadlib_events) < 10:
                    loadlib_events.append({'src': src, 'dst': dst, 'path': path})

            if etype == 'EVENT_FORK' and len(fork_events) < 10:
                fork_events.append({'src': src, 'dst': dst})

            if etype == 'EVENT_CREATE_THREAD' and len(create_thread_events) < 10:
                create_thread_events.append({'src': src, 'dst': dst})

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
        print(f"    作为subject: {as_subj:,}  predObj: {as_pred:,}  predObj2: {as_pred2:,}")
        pred_ev = type_role_events.get((ekey, 'as_predObj'), {})
        if pred_ev:
            print(f"    作为predObj的事件(Top8):")
            for et, cnt in pred_ev.most_common(8):
                t = type_event_total.get((ekey, et), 0)
                hp = type_event_has_path.get((ekey, et), 0)
                pp = hp/t*100 if t > 0 else 0
                print(f"      {et:35s} {cnt:>10,}  (有path={hp:,} {pp:.0f}%)")
        got = type_got_path.get(ekey, set())
        if total > 0 and len(got) > 0:
            print(f"    通过predObjPath获得名字: {len(got):,}/{total:,}")
            for s in type_path_samples.get(ekey, [])[:3]:
                print(f"      {s}")

    print(f"\n{'='*80}")
    print("二、EVENT_EXECUTE dst类型")
    print(f"{'='*80}")
    for t, c in execute_dst_types.most_common():
        print(f"  {t:40s} {c:>8,}")
    print(f"\n  前10个EXECUTE详情:")
    for e in execute_events[:10]:
        si = subject_info.get(e['src'], {})
        dst_desc = '?'
        if e['dst'] in entity_type:
            dst_desc = f"{entity_type[e['dst']][0]}:{entity_type[e['dst']][1]}"
        print(f"    src={str(si.get('cmdLine','?'))[:40]}({si.get('type','?')})")
        print(f"      dst={dst_desc}  path={e['path']}")

    print(f"\n{'='*80}")
    print("三、EVENT_LOADLIBRARY dst类型")
    print(f"{'='*80}")
    for t, c in loadlib_dst_types.most_common():
        print(f"  {t:40s} {c:>8,}")
    print(f"\n  前5个LOADLIBRARY详情:")
    for e in loadlib_events[:5]:
        si = subject_info.get(e['src'], {})
        print(f"    src={str(si.get('cmdLine','?'))[:30]}({si.get('type','?')})  path={e['path'][:80]}")

    print(f"\n{'='*80}")
    print("四、SUBJECT_THREAD vs SUBJECT_PROCESS")
    print(f"{'='*80}")
    thread_count = sum(1 for v in subject_info.values() if v['type'] == 'SUBJECT_THREAD')
    process_count = sum(1 for v in subject_info.values() if v['type'] == 'SUBJECT_PROCESS')
    print(f"  SUBJECT_PROCESS: {process_count:,}")
    print(f"  SUBJECT_THREAD:  {thread_count:,}")

    # SUBJECT_THREAD的父进程类型
    thread_parent_types = Counter()
    for uid, info in subject_info.items():
        if info['type'] == 'SUBJECT_THREAD':
            parent = info.get('parent')
            if parent and parent in subject_info:
                thread_parent_types[subject_info[parent]['type']] += 1
            else:
                thread_parent_types['NOT_FOUND_OR_NONE'] += 1
    print(f"\n  SUBJECT_THREAD的父进程类型:")
    for t, c in thread_parent_types.most_common():
        print(f"    {t}: {c:,}")

    # SUBJECT_THREAD的cmdLine情况
    thread_with_cmd = sum(1 for v in subject_info.values() if v['type']=='SUBJECT_THREAD' and v['cmdLine'])
    process_with_cmd = sum(1 for v in subject_info.values() if v['type']=='SUBJECT_PROCESS' and v['cmdLine'])
    print(f"\n  有cmdLine的:")
    print(f"    SUBJECT_PROCESS: {process_with_cmd}/{process_count} ({process_with_cmd/max(process_count,1)*100:.1f}%)")
    print(f"    SUBJECT_THREAD:  {thread_with_cmd}/{thread_count} ({thread_with_cmd/max(thread_count,1)*100:.1f}%)")

    print(f"\n{'='*80}")
    print("五、EVENT_FORK / EVENT_CREATE_THREAD")
    print(f"{'='*80}")
    print(f"\n  前10个FORK:")
    for e in fork_events[:10]:
        si = subject_info.get(e['src'], {})
        di = subject_info.get(e['dst'], {})
        print(f"    src={str(si.get('cmdLine','?'))[:30]}({si.get('type','?')}) → dst={str(di.get('cmdLine','?'))[:30]}({di.get('type','?')})")

    print(f"\n  前10个CREATE_THREAD:")
    for e in create_thread_events[:10]:
        si = subject_info.get(e['src'], {})
        di = subject_info.get(e['dst'], {})
        print(f"    src={str(si.get('cmdLine','?'))[:30]}({si.get('type','?')}) → dst={str(di.get('cmdLine','?'))[:30]}({di.get('type','?')})")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/fivedirections_e3/ta1-fivedirections-e3-official-2.json"
    analyze(filepath)
