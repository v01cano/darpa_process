"""
THEIA E3 UUID生命周期与cmdLine深度分析

分析：
1. UUID生成机制：fork时生成还是exec后生成？
2. Subject.cmdLine vs Event.cmdLine 的精确关系
3. CLONE事件的详细结构（是否有cmdLine？src/dst分别是什么？）
4. EXECUTE事件的dst到底指向什么？为什么找不到？
5. MemoryObject的详细使用场景
6. 同一UUID是否会经历多次EXECUTE？
"""

import json
import sys
from collections import defaultdict, Counter

def analyze(filepath):
    print(f"分析文件: {filepath}\n")

    # ============ 第1遍：全面收集 ============
    subject_info = {}     # uuid → {path, cmdLine, tgid, ppid, parent_uuid}
    file_info = {}        # uuid → {filename}
    netflow_info = {}     # uuid → {addr}
    memory_info = {}      # uuid → True
    all_entity_uuids = set()

    # Event详情
    clone_events = []
    execute_events = []

    # 每个UUID作为subject时的事件序列
    uuid_event_sequence = defaultdict(list)  # uuid → [(ts, etype, dst_uuid)]

    # 每个UUID经历的EXECUTE次数
    uuid_execute_count = Counter()

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
                all_entity_uuids.add(uid)
                if datum.get('type') == 'SUBJECT_PROCESS':
                    props = datum.get('properties', {}).get('map', {})
                    cmdline = datum.get('cmdLine')
                    if isinstance(cmdline, dict):
                        cmdline = cmdline.get('string')
                    parent = None
                    if isinstance(datum.get('parentSubject'), dict):
                        parent = list(datum['parentSubject'].values())[0]
                    subject_info[uid] = {
                        'path': props.get('path'),
                        'cmdLine': cmdline,
                        'tgid': props.get('tgid'),
                        'ppid': props.get('ppid'),
                        'parent': parent,
                    }

            elif rtype == 'FileObject':
                uid = datum['uuid']
                all_entity_uuids.add(uid)
                base_props = datum.get('baseObject', {}).get('properties', {}).get('map', {})
                file_info[uid] = {'filename': base_props.get('filename')}

            elif rtype == 'NetFlowObject':
                uid = datum['uuid']
                all_entity_uuids.add(uid)
                netflow_info[uid] = True

            elif rtype == 'MemoryObject':
                uid = datum['uuid']
                all_entity_uuids.add(uid)
                memory_info[uid] = True

            elif rtype == 'Event':
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

                # 记录每个UUID的事件序列
                if src:
                    uuid_event_sequence[src].append((ts, etype, dst, dst2))

                # CLONE事件
                if etype == 'EVENT_CLONE':
                    clone_events.append({
                        'src': src, 'dst': dst, 'dst2': dst2, 'ts': ts,
                        'props': dict(props),
                        'raw_subject': datum.get('subject'),
                        'raw_predObj': datum.get('predicateObject'),
                        'raw_predObj2': datum.get('predicateObject2'),
                    })

                # EXECUTE事件
                if etype == 'EVENT_EXECUTE':
                    uuid_execute_count[src] += 1
                    execute_events.append({
                        'src': src, 'dst': dst, 'dst2': dst2, 'ts': ts,
                        'event_cmdline': props.get('cmdLine'),
                        'props_keys': list(props.keys()),
                        'raw_predObj': datum.get('predicateObject'),
                        'raw_predObj2': datum.get('predicateObject2'),
                    })

    print(f"\n  实体统计:")
    print(f"    Subject: {len(subject_info):,}")
    print(f"    FileObject: {len(file_info):,}")
    print(f"    NetFlowObject: {len(netflow_info):,}")
    print(f"    MemoryObject: {len(memory_info):,}")

    # ============ 分析1：UUID生成机制 ============
    print(f"\n{'='*80}")
    print("一、UUID生成机制")
    print(f"{'='*80}")

    # CLONE事件数 vs Subject记录数
    print(f"  Subject记录数: {len(subject_info):,}")
    print(f"  CLONE事件数:   {len(clone_events):,}")

    # CLONE的dst是否都有Subject记录？
    clone_dst_has_subject = sum(1 for e in clone_events if e['dst'] in subject_info)
    clone_dst_no_subject = sum(1 for e in clone_events if e['dst'] not in subject_info)
    print(f"\n  CLONE的dst(子进程)有Subject记录: {clone_dst_has_subject:,}")
    print(f"  CLONE的dst(子进程)无Subject记录: {clone_dst_no_subject:,}")

    # 没有对应CLONE事件的Subject（系统启动时就存在的进程）
    clone_children = {e['dst'] for e in clone_events}
    subjects_without_clone = set(subject_info.keys()) - clone_children
    print(f"  无CLONE事件的Subject（已存在的进程）: {len(subjects_without_clone):,}")

    # ============ 分析2：CLONE事件详细结构 ============
    print(f"\n{'='*80}")
    print("二、CLONE事件详细结构")
    print(f"{'='*80}")

    print(f"\n  前15个CLONE事件的完整信息:")
    for i, e in enumerate(clone_events[:15]):
        src_path = subject_info.get(e['src'], {}).get('path', '?')
        src_cmd = subject_info.get(e['src'], {}).get('cmdLine', '?')
        dst_path = subject_info.get(e['dst'], {}).get('path', '?')
        dst_cmd = subject_info.get(e['dst'], {}).get('cmdLine', '?')

        print(f"\n  CLONE #{i+1}:")
        print(f"    subject(src): UUID={str(e['src'])[:20]}...")
        print(f"      Subject.path={src_path}")
        print(f"      Subject.cmdLine={str(src_cmd)[:70]}")
        print(f"    predicateObject(dst): UUID={str(e['dst'])[:20]}...")
        print(f"      Subject.path={dst_path}")
        print(f"      Subject.cmdLine={str(dst_cmd)[:70]}")
        print(f"    predicateObject2(dst2): {e['dst2']}")
        print(f"    properties.map keys: {list(e['props'].keys())}")
        if e['props']:
            for k, v in list(e['props'].items())[:5]:
                print(f"      {k} = {str(v)[:60]}")

    # CLONE事件的properties.map有什么？
    clone_props_keys = Counter()
    for e in clone_events:
        for k in e['props']:
            clone_props_keys[k] += 1
    print(f"\n  CLONE事件properties.map中的key:")
    for k, cnt in clone_props_keys.most_common():
        print(f"    {k:30s} {cnt:>8,}")

    # ============ 分析3：EXECUTE事件的dst到底是什么？ ============
    print(f"\n{'='*80}")
    print("三、EXECUTE事件的dst（predicateObject）分析")
    print(f"{'='*80}")

    exe_dst_is_file = 0
    exe_dst_is_memory = 0
    exe_dst_is_subject = 0
    exe_dst_is_netflow = 0
    exe_dst_not_found = 0
    exe_dst_is_none = 0

    for e in execute_events:
        dst = e['dst']
        if dst is None:
            exe_dst_is_none += 1
        elif dst in file_info:
            exe_dst_is_file += 1
        elif dst in memory_info:
            exe_dst_is_memory += 1
        elif dst in subject_info:
            exe_dst_is_subject += 1
        elif dst in netflow_info:
            exe_dst_is_netflow += 1
        else:
            exe_dst_not_found += 1

    print(f"  EXECUTE事件总数: {len(execute_events):,}")
    print(f"  dst类型分布:")
    print(f"    FileObject:    {exe_dst_is_file:>8,}")
    print(f"    MemoryObject:  {exe_dst_is_memory:>8,}")
    print(f"    Subject:       {exe_dst_is_subject:>8,}")
    print(f"    NetFlowObject: {exe_dst_is_netflow:>8,}")
    print(f"    不在任何实体中: {exe_dst_not_found:>8,}")
    print(f"    None:          {exe_dst_is_none:>8,}")

    # EXECUTE的dst2呢？
    exe_dst2_is_file = sum(1 for e in execute_events if e['dst2'] in file_info)
    exe_dst2_is_memory = sum(1 for e in execute_events if e['dst2'] in memory_info)
    exe_dst2_is_subject = sum(1 for e in execute_events if e['dst2'] in subject_info)
    exe_dst2_none = sum(1 for e in execute_events if e['dst2'] is None)
    exe_dst2_other = len(execute_events) - exe_dst2_is_file - exe_dst2_is_memory - exe_dst2_is_subject - exe_dst2_none

    print(f"\n  EXECUTE事件的dst2（predicateObject2）类型分布:")
    print(f"    FileObject:    {exe_dst2_is_file:>8,}")
    print(f"    MemoryObject:  {exe_dst2_is_memory:>8,}")
    print(f"    Subject:       {exe_dst2_is_subject:>8,}")
    print(f"    None:          {exe_dst2_none:>8,}")
    print(f"    其他:          {exe_dst2_other:>8,}")

    # 详细看几个EXECUTE事件
    print(f"\n  前15个EXECUTE事件详情:")
    for i, e in enumerate(execute_events[:15]):
        src_path = subject_info.get(e['src'], {}).get('path', '?')
        src_cmd = subject_info.get(e['src'], {}).get('cmdLine', '?')

        dst_desc = '?'
        if e['dst'] in file_info:
            dst_desc = f"FILE: {file_info[e['dst']]['filename']}"
        elif e['dst'] in memory_info:
            dst_desc = "MemoryObject"
        elif e['dst'] in subject_info:
            dst_desc = f"Subject: {subject_info[e['dst']]['path']}"
        elif e['dst'] is None:
            dst_desc = "None"

        dst2_desc = '?'
        if e['dst2'] in file_info:
            dst2_desc = f"FILE: {file_info[e['dst2']]['filename']}"
        elif e['dst2'] in memory_info:
            dst2_desc = "MemoryObject"
        elif e['dst2'] in subject_info:
            dst2_desc = f"Subject: {subject_info[e['dst2']]['path']}"
        elif e['dst2'] is None:
            dst2_desc = "None"

        print(f"\n  EXECUTE #{i+1}:")
        print(f"    subject(src): {src_path}  cmdLine={str(src_cmd)[:50]}")
        print(f"    Event.cmdLine: {e['event_cmdline']}")
        print(f"    predicateObject(dst): {dst_desc}")
        print(f"    predicateObject2(dst2): {dst2_desc}")
        print(f"    props keys: {e['props_keys']}")

    # ============ 分析4：同一UUID多次EXECUTE ============
    print(f"\n{'='*80}")
    print("四、同一UUID经历多次EXECUTE")
    print(f"{'='*80}")

    exe_count_dist = Counter(uuid_execute_count.values())
    print(f"  做过EXECUTE的UUID: {len(uuid_execute_count):,}")
    print(f"  EXECUTE次数分布:")
    for cnt, num_uuids in sorted(exe_count_dist.items()):
        print(f"    {cnt}次: {num_uuids:,} 个UUID")
        if cnt > 10:
            break

    # 多次EXECUTE的例子
    multi_exec = [(uid, cnt) for uid, cnt in uuid_execute_count.items() if cnt > 1]
    if multi_exec:
        print(f"\n  多次EXECUTE的进程示例(前10个):")
        for uid, cnt in multi_exec[:10]:
            info = subject_info.get(uid, {})
            execs = [e for e in execute_events if e['src'] == uid]
            print(f"\n    UUID={uid[:20]}... ({cnt}次EXECUTE)")
            print(f"      Subject.path: {info.get('path')}")
            print(f"      Subject.cmdLine: {str(info.get('cmdLine'))[:60]}")
            for ex in execs[:5]:
                print(f"      EXECUTE → Event.cmdLine={str(ex['event_cmdline'])[:60]}")

    # ============ 分析5：CLONE后子进程的事件序列 ============
    print(f"\n{'='*80}")
    print("五、CLONE后子进程的事件序列（验证UUID生成时机）")
    print(f"{'='*80}")

    # 找子进程在CLONE之后的第一批事件
    shown = 0
    for e in clone_events[:50]:
        child = e['dst']
        if child not in uuid_event_sequence:
            continue
        child_events = uuid_event_sequence[child]
        if not child_events:
            continue

        child_info = subject_info.get(child, {})
        parent_info = subject_info.get(e['src'], {})

        # 只显示有exec的
        has_execute = any(ev[1] == 'EVENT_EXECUTE' for ev in child_events)
        if not has_execute:
            continue
        if shown >= 10:
            break

        print(f"\n  子进程 UUID={child[:16]}...")
        print(f"    Subject.path: {child_info.get('path')}")
        print(f"    Subject.cmdLine: {str(child_info.get('cmdLine'))[:60]}")
        print(f"    父进程.path: {parent_info.get('path')}")
        print(f"    事件序列（前10条作为subject）:")
        for ts, etype, dst, dst2 in child_events[:10]:
            dst_desc = ''
            if dst in file_info:
                dst_desc = f"→ FILE:{file_info[dst].get('filename', '?')}"
            elif dst in subject_info:
                dst_desc = f"→ PROC:{subject_info[dst].get('path', '?')}"
            elif dst in memory_info:
                dst_desc = f"→ MEM"
            print(f"      {etype:30s} {dst_desc}")
        shown += 1

    # ============ 分析6：MemoryObject 详细分析 ============
    print(f"\n{'='*80}")
    print("六、MemoryObject 详细分析")
    print(f"{'='*80}")
    print(f"  MemoryObject总数: {len(memory_info):,}")

    # 统计MemoryObject参与的事件
    mem_events = Counter()
    mem_as_dst2 = 0
    for uid in list(memory_info.keys())[:1000]:  # 抽样
        pass  # 已在第1遍中通过event_type分析知道

    # EXECUTE中dst指向MemoryObject的情况
    print(f"  EXECUTE事件中dst是MemoryObject: {exe_dst_is_memory:,} / {len(execute_events):,}")
    print(f"  EXECUTE事件中dst2是MemoryObject: {exe_dst2_is_memory:,} / {len(execute_events):,}")

    # 是否有攻击相关进程的EXECUTE指向MemoryObject？
    print(f"\n  EXECUTE→MemoryObject的进程:")
    mem_exec_processes = Counter()
    for e in execute_events:
        if e['dst'] in memory_info:
            src_path = subject_info.get(e['src'], {}).get('path', '?')
            mem_exec_processes[src_path] += 1
    for path, cnt in mem_exec_processes.most_common(20):
        print(f"    {path:50s} {cnt:>5}")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/theia_e3/ta1-theia-e3-official-6r.json.8"
    analyze(filepath)
