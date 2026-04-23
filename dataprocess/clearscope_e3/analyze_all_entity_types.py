"""
ClearScope E3 实体类型与事件关系分析

分析：
1. 各实体类型（含Android特有的类型）是否被Event引用
2. 各实体类型参与了哪些事件
3. 各实体能否获取名字
4. EXECUTE事件的dst是什么类型
5. CLONE/FORK事件的结构
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    # 第1遍：收集实体
    entity_type = {}  # uuid → (record_type, subtype)
    subject_info = {}  # uuid → {path, cmdLine, ...}
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
                props = datum.get('properties', {}).get('map', {}) if isinstance(datum.get('properties'), dict) else {}
                cmdline = datum.get('cmdLine')
                if isinstance(cmdline, dict):
                    cmdline = cmdline.get('string')
                subject_info[datum['uuid']] = {
                    'path': props.get('path') if isinstance(props, dict) else None,
                    'cmdLine': cmdline,
                }
            elif rtype == 'FileObject':
                entity_type[datum['uuid']] = ('FileObject', datum.get('type', ''))
                base_props = datum.get('baseObject', {}).get('properties', {}).get('map', {})
                file_info[datum['uuid']] = {
                    'filename': base_props.get('filename') if isinstance(base_props, dict) else None,
                    'path': base_props.get('path') if isinstance(base_props, dict) else None,
                }
            elif rtype == 'NetFlowObject':
                entity_type[datum['uuid']] = ('NetFlowObject', 'NetFlow')
            elif rtype == 'MemoryObject':
                entity_type[datum['uuid']] = ('MemoryObject', 'Memory')
            elif rtype == 'UnnamedPipeObject':
                entity_type[datum['uuid']] = ('UnnamedPipeObject', 'Pipe')
            elif rtype == 'SrcSinkObject':
                entity_type[datum['uuid']] = ('SrcSinkObject', datum.get('type', ''))

    print(f"  第1遍完成，共 {len(entity_type):,} 个实体")
    type_counts = Counter(v[0] for v in entity_type.values())
    for t, c in type_counts.most_common():
        print(f"    {t}: {c:,}")

    # 第2遍：分析事件
    type_role_events = defaultdict(Counter)
    type_event_has_path = defaultdict(int)
    type_event_total = defaultdict(int)
    type_got_name = defaultdict(set)
    type_name_samples = defaultdict(list)

    # EXECUTE/CLONE/FORK 详情
    execute_dst_types = Counter()
    execute_dst2_types = Counter()
    clone_events = []
    fork_events = []

    loaded = 0
    with open(filepath, 'r') as f:
        for line in f:
            loaded += 1
            if loaded % 200000 == 0:
                print(f"  已扫描 {loaded:,} 行...")

            datum = json.loads(line)['datum']
            rtype_full = list(datum.keys())[0]
            datum = datum[rtype_full]
            rtype = rtype_full.split('.')[-1]

            if rtype != 'Event':
                continue

            etype = datum.get('type', '')
            raw_props = datum.get('properties')
            props = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
            if not isinstance(props, dict):
                props = {}

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
                type_role_events[(ekey, 'as_predicateObject')][etype] += 1
                type_event_total[(ekey, etype)] += 1

                if isinstance(datum.get('predicateObjectPath'), dict):
                    path = datum['predicateObjectPath'].get('string', '')
                    if path:
                        type_event_has_path[(ekey, etype)] += 1
                        type_got_name[ekey].add(dst)
                        if len(type_name_samples[ekey]) < 10:
                            type_name_samples[ekey].append(path)

            if dst2 and dst2 in entity_type:
                ekey = f"{entity_type[dst2][0]}:{entity_type[dst2][1]}"
                type_role_events[(ekey, 'as_predicateObject2')][etype] += 1

            # EXECUTE dst类型
            if etype == 'EVENT_EXECUTE' and dst:
                if dst in entity_type:
                    execute_dst_types[f"{entity_type[dst][0]}:{entity_type[dst][1]}"] += 1
                else:
                    execute_dst_types["NOT_FOUND"] += 1
                if dst2 and dst2 in entity_type:
                    execute_dst2_types[f"{entity_type[dst2][0]}:{entity_type[dst2][1]}"] += 1

            # CLONE/FORK
            if etype == 'EVENT_CLONE' and src and dst:
                clone_events.append({'src': src, 'dst': dst, 'props': dict(props) if isinstance(props, dict) else {}})
            if etype == 'EVENT_FORK' and src and dst:
                fork_events.append({'src': src, 'dst': dst, 'props': dict(props) if isinstance(props, dict) else {}})

    # ============ 输出 ============
    all_entity_keys = sorted(set(
        list(k[0] for k in type_role_events.keys())
    ))

    type_total_uuids = Counter()
    for v in entity_type.values():
        type_total_uuids[f"{v[0]}:{v[1]}"] += 1

    print(f"\n{'='*80}")
    print("一、各实体类型被Event引用情况")
    print(f"{'='*80}")
    for ekey in all_entity_keys:
        total = type_total_uuids.get(ekey, 0)
        as_subj = sum(type_role_events.get((ekey, 'as_subject'), {}).values())
        as_pred = sum(type_role_events.get((ekey, 'as_predicateObject'), {}).values())
        as_pred2 = sum(type_role_events.get((ekey, 'as_predicateObject2'), {}).values())
        print(f"\n  {ekey} (共{total:,}个)")
        print(f"    作为subject: {as_subj:,}条Event")
        print(f"    作为predicateObject: {as_pred:,}条Event")
        print(f"    作为predicateObject2: {as_pred2:,}条Event")

        pred_events = type_role_events.get((ekey, 'as_predicateObject'), {})
        if pred_events:
            print(f"    作为predObj的事件类型:")
            for et, cnt in pred_events.most_common(10):
                total_ev = type_event_total.get((ekey, et), 0)
                has_path = type_event_has_path.get((ekey, et), 0)
                path_pct = has_path / total_ev * 100 if total_ev > 0 else 0
                print(f"      {et:35s} {cnt:>10,}  (有path: {has_path:,} = {path_pct:.1f}%)")

        got_names = type_got_name.get(ekey, set())
        print(f"    通过predicateObjectPath获得名字: {len(got_names):,} / {total:,}")
        samples = type_name_samples.get(ekey, [])
        if samples:
            for s in samples[:5]:
                print(f"      {s}")

    print(f"\n{'='*80}")
    print("二、EXECUTE事件的dst类型")
    print(f"{'='*80}")
    for t, cnt in execute_dst_types.most_common():
        print(f"  dst: {t:40s} {cnt:>8,}")
    for t, cnt in execute_dst2_types.most_common():
        print(f"  dst2: {t:40s} {cnt:>8,}")

    print(f"\n{'='*80}")
    print("三、CLONE/FORK事件")
    print(f"{'='*80}")
    print(f"  EVENT_CLONE: {len(clone_events):,}")
    print(f"  EVENT_FORK:  {len(fork_events):,}")

    if clone_events:
        print(f"\n  前5个CLONE事件:")
        for e in clone_events[:5]:
            src_info = subject_info.get(e['src'], {})
            dst_info = subject_info.get(e['dst'], {})
            print(f"    父={src_info.get('cmdLine','?')[:40]}  →  子={dst_info.get('cmdLine','?')[:40]}")
            print(f"    props keys: {list(e['props'].keys())}")

    if fork_events:
        print(f"\n  前5个FORK事件:")
        for e in fork_events[:5]:
            src_info = subject_info.get(e['src'], {})
            dst_info = subject_info.get(e['dst'], {})
            print(f"    父={src_info.get('cmdLine','?')[:40]}  →  子={dst_info.get('cmdLine','?')[:40]}")
            print(f"    props keys: {list(e['props'].keys())}")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/clearscope_e3/ta1-clearscope-e3-official.json"
    analyze(filepath)
