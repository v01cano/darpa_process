"""
THEIA E3 实体类型与事件关系分析

分析：
1. 各实体类型是否被Event引用（决定是否保留）
2. MemoryObject的使用情况（THEIA独有，235,905个）
3. FILE_OBJECT_BLOCK的详细分析
4. EVENT_CLONE/EXECUTE的详细分析（进程生命周期）
5. Subject.cmdLine vs Event.cmdLine的关系
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    # ============ 第1遍：收集所有实体 ============
    entity_type = {}  # uuid → (record_type, subtype_or_info)
    subject_info = {}  # uuid → {path, cmdLine, tgid, ppid}
    memory_uuids = set()

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
                if datum.get('type') == 'SUBJECT_PROCESS':
                    props = datum.get('properties', {}).get('map', {})
                    cmdline = datum.get('cmdLine')
                    if isinstance(cmdline, dict):
                        cmdline = cmdline.get('string')
                    subject_info[datum['uuid']] = {
                        'path': props.get('path'),
                        'cmdLine': cmdline,
                        'tgid': props.get('tgid'),
                        'ppid': props.get('ppid'),
                    }
            elif rtype == 'FileObject':
                entity_type[datum['uuid']] = ('FileObject', datum.get('type', ''))
            elif rtype == 'NetFlowObject':
                entity_type[datum['uuid']] = ('NetFlowObject', 'NetFlow')
            elif rtype == 'MemoryObject':
                entity_type[datum['uuid']] = ('MemoryObject', 'Memory')
                memory_uuids.add(datum['uuid'])
            elif rtype == 'UnnamedPipeObject':
                entity_type[datum['uuid']] = ('UnnamedPipeObject', 'Pipe')
            elif rtype == 'SrcSinkObject':
                entity_type[datum['uuid']] = ('SrcSinkObject', datum.get('type', ''))

    print(f"  第1遍完成，共 {len(entity_type):,} 个实体")
    print(f"    Subject: {sum(1 for v in entity_type.values() if v[0]=='Subject'):,}")
    print(f"    FileObject: {sum(1 for v in entity_type.values() if v[0]=='FileObject'):,}")
    print(f"    NetFlowObject: {sum(1 for v in entity_type.values() if v[0]=='NetFlowObject'):,}")
    print(f"    MemoryObject: {sum(1 for v in entity_type.values() if v[0]=='MemoryObject'):,}")

    # ============ 第2遍：分析事件 ============
    # 各实体类型被引用的统计
    type_as_subject = Counter()
    type_as_predobj = Counter()
    type_as_predobj2 = Counter()
    type_event_count_as_subject = Counter()  # (entity_key, event_type) → count
    type_event_count_as_predobj = Counter()

    # CLONE/EXECUTE详细分析
    clone_events = []  # [{src, dst, ts, src_path, dst_path}]
    execute_events = []  # [{src, dst, ts, cmdline, src_path}]

    # Subject cmdLine vs Event cmdLine对比
    event_cmdline_map = {}  # src_uuid → Event中的cmdLine

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

            if rtype != 'Event':
                continue

            etype = datum.get('type', '')
            ts = datum.get('timestampNanos', 0)
            props = datum.get('properties', {}).get('map', {})

            src = None
            if isinstance(datum.get('subject'), dict):
                src = list(datum['subject'].values())[0]
            dst = None
            if isinstance(datum.get('predicateObject'), dict):
                dst = list(datum['predicateObject'].values())[0]
            dst2 = None
            if isinstance(datum.get('predicateObject2'), dict):
                dst2 = list(datum['predicateObject2'].values())[0]

            # 统计实体被引用情况
            if src and src in entity_type:
                ekey = f"{entity_type[src][0]}:{entity_type[src][1]}"
                type_as_subject[ekey] += 1
                type_event_count_as_subject[(ekey, etype)] += 1

            if dst and dst in entity_type:
                ekey = f"{entity_type[dst][0]}:{entity_type[dst][1]}"
                type_as_predobj[ekey] += 1
                type_event_count_as_predobj[(ekey, etype)] += 1

            if dst2 and dst2 in entity_type:
                ekey = f"{entity_type[dst2][0]}:{entity_type[dst2][1]}"
                type_as_predobj2[ekey] += 1

            # CLONE事件详情
            if etype == 'EVENT_CLONE' and src and dst:
                src_path = subject_info.get(src, {}).get('path', '?')
                dst_path = subject_info.get(dst, {}).get('path', '?')
                src_cmd = subject_info.get(src, {}).get('cmdLine', '?')
                dst_cmd = subject_info.get(dst, {}).get('cmdLine', '?')
                clone_events.append({
                    'src': src, 'dst': dst, 'ts': ts,
                    'src_path': src_path, 'dst_path': dst_path,
                    'src_cmd': src_cmd, 'dst_cmd': dst_cmd,
                })

            # EXECUTE事件详情
            if etype == 'EVENT_EXECUTE' and src:
                cmdline = props.get('cmdLine', None)
                src_path = subject_info.get(src, {}).get('path', '?')
                src_cmd_record = subject_info.get(src, {}).get('cmdLine', '?')
                execute_events.append({
                    'src': src, 'dst': dst, 'ts': ts,
                    'event_cmdline': cmdline,
                    'subject_path': src_path,
                    'subject_cmdline': src_cmd_record,
                })
                if src:
                    event_cmdline_map[src] = cmdline

    # ============ 输出 ============
    print(f"\n{'='*80}")
    print("一、各实体类型被Event引用情况")
    print(f"{'='*80}")

    all_entity_keys = sorted(set(
        list(type_as_subject.keys()) +
        list(type_as_predobj.keys()) +
        list(type_as_predobj2.keys())
    ))

    # 计算每种类型的总UUID数
    type_total_uuids = Counter()
    for v in entity_type.values():
        type_total_uuids[f"{v[0]}:{v[1]}"] += 1

    for ekey in all_entity_keys:
        total = type_total_uuids.get(ekey, 0)
        as_subj = type_as_subject.get(ekey, 0)
        as_pred = type_as_predobj.get(ekey, 0)
        as_pred2 = type_as_predobj2.get(ekey, 0)
        print(f"\n  {ekey} (共{total:,}个)")
        print(f"    作为subject: {as_subj:,}条Event")
        print(f"    作为predicateObject: {as_pred:,}条Event")
        print(f"    作为predicateObject2: {as_pred2:,}条Event")

        # 作为predObj时的事件类型
        pred_events = {k[1]: v for k, v in type_event_count_as_predobj.items() if k[0] == ekey}
        if pred_events:
            print(f"    作为predObj的事件类型:")
            for et, cnt in sorted(pred_events.items(), key=lambda x: -x[1])[:10]:
                print(f"      {et:35s} {cnt:>10,}")

    # ============ CLONE 分析 ============
    print(f"\n{'='*80}")
    print("二、EVENT_CLONE 分析（进程创建）")
    print(f"{'='*80}")
    print(f"  CLONE事件总数: {len(clone_events):,}")

    if clone_events:
        # 父子进程path对比
        same_path = sum(1 for e in clone_events if e['src_path'] == e['dst_path'])
        diff_path = sum(1 for e in clone_events if e['src_path'] != e['dst_path'])
        print(f"  父子path相同: {same_path:,} ({same_path/len(clone_events)*100:.1f}%)")
        print(f"  父子path不同: {diff_path:,} ({diff_path/len(clone_events)*100:.1f}%)")

        # 父子cmdLine对比
        same_cmd = sum(1 for e in clone_events if e['src_cmd'] == e['dst_cmd'])
        diff_cmd = sum(1 for e in clone_events if e['src_cmd'] != e['dst_cmd'])
        print(f"  父子cmdLine相同: {same_cmd:,} ({same_cmd/len(clone_events)*100:.1f}%)")
        print(f"  父子cmdLine不同: {diff_cmd:,} ({diff_cmd/len(clone_events)*100:.1f}%)")

        print(f"\n  --- 前20个CLONE事件 ---")
        for e in clone_events[:20]:
            print(f"    父={e['src_path']}  子={e['dst_path']}")
            if e['src_cmd'] != e['dst_cmd']:
                print(f"      父cmdLine={str(e['src_cmd'])[:60]}")
                print(f"      子cmdLine={str(e['dst_cmd'])[:60]}")

    # ============ EXECUTE 分析 ============
    print(f"\n{'='*80}")
    print("三、EVENT_EXECUTE 分析")
    print(f"{'='*80}")
    print(f"  EXECUTE事件总数: {len(execute_events):,}")

    if execute_events:
        # Subject.cmdLine vs Event.cmdLine 对比
        both_have = 0
        same_value = 0
        diff_value = 0
        only_subject = 0
        only_event = 0

        for e in execute_events:
            s_cmd = e['subject_cmdline']
            e_cmd = e['event_cmdline']
            if s_cmd and e_cmd:
                both_have += 1
                if s_cmd == e_cmd:
                    same_value += 1
                else:
                    diff_value += 1
            elif s_cmd and not e_cmd:
                only_subject += 1
            elif e_cmd and not s_cmd:
                only_event += 1

        print(f"\n  Subject.cmdLine vs Event.properties.map.cmdLine:")
        print(f"    两者都有值: {both_have:,}")
        print(f"      相同: {same_value:,}")
        print(f"      不同: {diff_value:,}")
        print(f"    仅Subject有: {only_subject:,}")
        print(f"    仅Event有:   {only_event:,}")

        print(f"\n  --- Subject.cmdLine vs Event.cmdLine 不同的例子 ---")
        shown = 0
        for e in execute_events:
            if e['subject_cmdline'] and e['event_cmdline'] and e['subject_cmdline'] != e['event_cmdline']:
                print(f"    Subject.path: {e['subject_path']}")
                print(f"    Subject.cmdLine: {str(e['subject_cmdline'])[:80]}")
                print(f"    Event.cmdLine:   {str(e['event_cmdline'])[:80]}")
                print()
                shown += 1
                if shown >= 10:
                    break

        print(f"\n  --- 前15个EXECUTE事件 ---")
        for e in execute_events[:15]:
            dst_type = entity_type.get(e['dst'], ('?', '?'))
            print(f"    进程={e['subject_path']}  Event.cmdLine={str(e['event_cmdline'])[:60]}")
            print(f"      Subject.cmdLine={str(e['subject_cmdline'])[:60]}")
            print(f"      dst类型={dst_type}")

    # ============ cmdLine 在 Subject 中的分析 ============
    print(f"\n{'='*80}")
    print("四、Subject.cmdLine 分析：它是节点属性还是边属性？")
    print(f"{'='*80}")

    # 检查：同一个UUID有多少个不同的Subject记录？
    # （如果只有一个Subject记录，cmdLine就是创建时的值）
    print(f"  Subject记录总数: {len(subject_info):,}")
    print(f"  有cmdLine的Subject: {sum(1 for v in subject_info.values() if v['cmdLine']):,}")

    # 对比：做过EXECUTE的进程，其Subject.cmdLine vs EXECUTE.cmdLine
    executed_subjects = set(e['src'] for e in execute_events)
    print(f"  做过EXECUTE的进程: {len(executed_subjects):,}")

    # 这些进程的Subject.cmdLine是什么？是fork时的还是exec后的？
    print(f"\n  做过EXECUTE的进程的Subject.cmdLine:")
    shown = 0
    for uuid in list(executed_subjects)[:15]:
        info = subject_info.get(uuid, {})
        exec_cmds = [e['event_cmdline'] for e in execute_events if e['src'] == uuid]
        print(f"    UUID={uuid[:16]}...")
        print(f"      Subject.path: {info.get('path')}")
        print(f"      Subject.cmdLine: {str(info.get('cmdLine'))[:70]}")
        print(f"      Event EXECUTE cmdLine: {[str(c)[:50] for c in exec_cmds[:3]]}")
        shown += 1

    # ============ CLONE后EXECUTE的模式 ============
    print(f"\n{'='*80}")
    print("五、CLONE→EXECUTE 生命周期（THEIA中的fork+exec模式）")
    print(f"{'='*80}")

    # 找子进程做了EXECUTE的情况
    clone_children = {e['dst'] for e in clone_events}
    executed_after_clone = clone_children & executed_subjects
    print(f"  CLONE创建的子进程总数: {len(clone_children):,}")
    print(f"  其中做过EXECUTE的: {len(executed_after_clone):,} ({len(executed_after_clone)/max(len(clone_children),1)*100:.1f}%)")

    # 详细例子
    print(f"\n  --- CLONE→EXECUTE 的完整例子 ---")
    shown = 0
    for e in clone_events:
        child = e['dst']
        if child in executed_subjects and shown < 10:
            child_execs = [ex for ex in execute_events if ex['src'] == child]
            print(f"\n    父进程: path={e['src_path']}  cmdLine={str(e['src_cmd'])[:50]}")
            print(f"    子进程: path={e['dst_path']}  cmdLine={str(e['dst_cmd'])[:50]}")
            for ex in child_execs[:2]:
                print(f"      EXECUTE: Event.cmdLine={str(ex['event_cmdline'])[:60]}")
            shown += 1


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/theia_e3/ta1-theia-e3-official-6r.json.8"
    analyze(filepath)
