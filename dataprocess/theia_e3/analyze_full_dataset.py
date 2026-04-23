"""
THEIA E3 全量数据分析

处理所有文件（按正确顺序），验证：
1. EXECUTE事件的dst在全量数据中是否能解析
2. Subject.cmdLine的真正含义（跨文件验证）
3. UUID生命周期（全量）
4. 是否有进程在CLONE时Subject记录还未出现的情况
"""

import json
import os
import sys
from collections import defaultdict, Counter

# 按正确顺序排列的文件列表
FILE_LIST = [
    'ta1-theia-e3-official-1r.json',
    'ta1-theia-e3-official-1r.json.1',
    'ta1-theia-e3-official-1r.json.2',
    'ta1-theia-e3-official-1r.json.3',
    'ta1-theia-e3-official-1r.json.4',
    'ta1-theia-e3-official-1r.json.5',
    'ta1-theia-e3-official-1r.json.6',
    'ta1-theia-e3-official-1r.json.7',
    'ta1-theia-e3-official-1r.json.8',
    'ta1-theia-e3-official-1r.json.9',
    'ta1-theia-e3-official-3.json',
    'ta1-theia-e3-official-5m.json',
    'ta1-theia-e3-official-6r.json',
    'ta1-theia-e3-official-6r.json.1',
    'ta1-theia-e3-official-6r.json.2',
    'ta1-theia-e3-official-6r.json.3',
    'ta1-theia-e3-official-6r.json.4',
    'ta1-theia-e3-official-6r.json.5',
    'ta1-theia-e3-official-6r.json.6',
    'ta1-theia-e3-official-6r.json.7',
    'ta1-theia-e3-official-6r.json.8',
    'ta1-theia-e3-official-6r.json.9',
    'ta1-theia-e3-official-6r.json.10',
    'ta1-theia-e3-official-6r.json.11',
    'ta1-theia-e3-official-6r.json.12',
]

INPUT_DIR = "/mnt/disk/darpa/theia_e3"


def main():
    # 验证文件存在
    existing_files = []
    for f in FILE_LIST:
        path = os.path.join(INPUT_DIR, f)
        if os.path.exists(path):
            existing_files.append(f)
    print(f"找到 {len(existing_files)}/{len(FILE_LIST)} 个文件")
    for f in existing_files:
        print(f"  {f}")

    # ============ 全量收集 ============
    subject_info = {}      # uuid → {path, cmdLine, parent}
    file_info = {}         # uuid → {filename}
    netflow_info = {}      # uuid → True
    memory_info = {}       # uuid → True
    all_uuids = set()

    # EXECUTE事件
    execute_events = []
    # CLONE事件
    clone_events = []
    # 每个UUID的EXECUTE次数
    uuid_execute_count = Counter()

    loaded = 0
    print(f"\n开始全量扫描...")

    for fname in existing_files:
        fpath = os.path.join(INPUT_DIR, fname)
        print(f"  处理: {fname}")
        with open(fpath, 'r') as fin:
            for line in fin:
                loaded += 1
                if loaded % 2000000 == 0:
                    print(f"    已扫描 {loaded:,} 行...")

                datum = json.loads(line)['datum']
                rtype_full = list(datum.keys())[0]
                datum = datum[rtype_full]
                rtype = rtype_full.split('.')[-1]

                if rtype == 'Subject':
                    uid = datum['uuid']
                    all_uuids.add(uid)
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
                            'parent': parent,
                        }

                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    all_uuids.add(uid)
                    base_props = datum.get('baseObject', {}).get('properties', {}).get('map', {})
                    file_info[uid] = {'filename': base_props.get('filename')}

                elif rtype == 'NetFlowObject':
                    uid = datum['uuid']
                    all_uuids.add(uid)
                    netflow_info[uid] = True

                elif rtype == 'MemoryObject':
                    uid = datum['uuid']
                    all_uuids.add(uid)
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

                    if etype == 'EVENT_EXECUTE' and src:
                        uuid_execute_count[src] += 1
                        execute_events.append({
                            'src': src, 'dst': dst, 'dst2': dst2,
                            'ts': ts,
                            'event_cmdline': props.get('cmdLine'),
                        })

                    if etype == 'EVENT_CLONE' and src and dst:
                        clone_events.append({
                            'src': src, 'dst': dst, 'ts': ts,
                        })

    print(f"\n全量扫描完成: {loaded:,} 行")
    print(f"\n实体统计:")
    print(f"  Subject: {len(subject_info):,}")
    print(f"  FileObject: {len(file_info):,}")
    print(f"  NetFlowObject: {len(netflow_info):,}")
    print(f"  MemoryObject: {len(memory_info):,}")
    print(f"  CLONE事件: {len(clone_events):,}")
    print(f"  EXECUTE事件: {len(execute_events):,}")

    # ============ 分析1：EXECUTE的dst在全量中能否解析 ============
    print(f"\n{'='*80}")
    print("一、EXECUTE事件的dst（全量数据验证）")
    print(f"{'='*80}")

    exe_dst_file = 0
    exe_dst_memory = 0
    exe_dst_subject = 0
    exe_dst_netflow = 0
    exe_dst_not_found = 0
    exe_dst_none = 0
    exe_dst_null_uuid = 0  # 全零UUID

    NULL_UUID = '00000000-0000-0000-0000-000000000000'

    for e in execute_events:
        dst = e['dst']
        if dst is None:
            exe_dst_none += 1
        elif dst == NULL_UUID:
            exe_dst_null_uuid += 1
        elif dst in file_info:
            exe_dst_file += 1
        elif dst in memory_info:
            exe_dst_memory += 1
        elif dst in subject_info:
            exe_dst_subject += 1
        elif dst in netflow_info:
            exe_dst_netflow += 1
        else:
            exe_dst_not_found += 1

    print(f"  EXECUTE事件总数: {len(execute_events):,}")
    print(f"  dst类型分布（全量）:")
    print(f"    FileObject:     {exe_dst_file:>8,}  ({exe_dst_file/len(execute_events)*100:.1f}%)")
    print(f"    MemoryObject:   {exe_dst_memory:>8,}")
    print(f"    Subject:        {exe_dst_subject:>8,}")
    print(f"    NetFlowObject:  {exe_dst_netflow:>8,}")
    print(f"    全零UUID:       {exe_dst_null_uuid:>8,}")
    print(f"    不在任何实体中: {exe_dst_not_found:>8,}")
    print(f"    None:           {exe_dst_none:>8,}")

    # dst2也分析
    exe_dst2_file = sum(1 for e in execute_events if e['dst2'] in file_info)
    exe_dst2_memory = sum(1 for e in execute_events if e['dst2'] in memory_info)
    exe_dst2_subject = sum(1 for e in execute_events if e['dst2'] in subject_info)
    exe_dst2_null = sum(1 for e in execute_events if e['dst2'] == NULL_UUID)
    exe_dst2_none = sum(1 for e in execute_events if e['dst2'] is None)
    print(f"\n  dst2类型分布（全量）:")
    print(f"    FileObject:     {exe_dst2_file:>8,}")
    print(f"    MemoryObject:   {exe_dst2_memory:>8,}")
    print(f"    Subject:        {exe_dst2_subject:>8,}")
    print(f"    全零UUID:       {exe_dst2_null:>8,}")
    print(f"    None:           {exe_dst2_none:>8,}")

    # 展示几个dst能解析为FileObject的EXECUTE
    print(f"\n  --- dst指向FileObject的EXECUTE事件 (前20个) ---")
    shown = 0
    for e in execute_events:
        if e['dst'] in file_info and shown < 20:
            src_info = subject_info.get(e['src'], {})
            print(f"    进程={src_info.get('path')}  cmdLine={src_info.get('cmdLine','')[:50]}")
            print(f"      Event.cmdLine={e['event_cmdline']}")
            print(f"      dst FILE={file_info[e['dst']]['filename']}")
            print()
            shown += 1

    # 展示几个dst找不到的
    print(f"\n  --- dst不在任何实体中的EXECUTE事件 (前10个，显示dst UUID) ---")
    shown = 0
    for e in execute_events:
        if e['dst'] not in file_info and e['dst'] not in memory_info and e['dst'] not in subject_info and e['dst'] != NULL_UUID and e['dst'] is not None:
            src_info = subject_info.get(e['src'], {})
            print(f"    进程={src_info.get('path')}  Event.cmdLine={e['event_cmdline']}")
            print(f"      dst UUID={e['dst']}")
            print(f"      dst2 UUID={e['dst2']}")
            shown += 1
            if shown >= 10:
                break

    # ============ 分析2：Subject.cmdLine vs Event.cmdLine ============
    print(f"\n{'='*80}")
    print("二、Subject.cmdLine vs Event.cmdLine（全量验证）")
    print(f"{'='*80}")

    same = 0
    diff = 0
    only_subject = 0
    only_event = 0
    both_none = 0

    diff_examples = []
    for e in execute_events:
        s_cmd = subject_info.get(e['src'], {}).get('cmdLine')
        e_cmd = e['event_cmdline']
        if s_cmd and e_cmd:
            if s_cmd == e_cmd:
                same += 1
            else:
                diff += 1
                if len(diff_examples) < 15:
                    diff_examples.append((subject_info.get(e['src'], {}), e))
        elif s_cmd and not e_cmd:
            only_subject += 1
        elif e_cmd and not s_cmd:
            only_event += 1
        else:
            both_none += 1

    print(f"  两者都有值: same={same:,}, different={diff:,}")
    print(f"  仅Subject有: {only_subject:,}")
    print(f"  仅Event有: {only_event:,}")
    print(f"  两者都None: {both_none:,}")

    if diff_examples:
        print(f"\n  --- Subject.cmdLine ≠ Event.cmdLine 的例子 ---")
        for subj, ev in diff_examples[:10]:
            print(f"    Subject.path={subj.get('path')}")
            print(f"    Subject.cmdLine={str(subj.get('cmdLine'))[:70]}")
            print(f"    Event.cmdLine=  {str(ev['event_cmdline'])[:70]}")
            print()

    # ============ 分析3：CLONE后是否立刻EXECUTE ============
    print(f"\n{'='*80}")
    print("三、CLONE与EXECUTE的时序关系")
    print(f"{'='*80}")

    # 子进程的Subject记录是否在CLONE之前就存在？
    # （如果THEIA是延迟创建Subject的话，Subject记录应该在CLONE事件之后出现在日志中）
    clone_children = {e['dst'] for e in clone_events}
    children_with_execute = clone_children & set(uuid_execute_count.keys())
    children_without_execute = clone_children - set(uuid_execute_count.keys())

    print(f"  CLONE创建的子进程: {len(clone_children):,}")
    print(f"  其中做过EXECUTE的: {len(children_with_execute):,} ({len(children_with_execute)/len(clone_children)*100:.1f}%)")
    print(f"  从未EXECUTE的: {len(children_without_execute):,} ({len(children_without_execute)/len(clone_children)*100:.1f}%)")

    # 从未EXECUTE的子进程，其Subject.cmdLine是什么？
    print(f"\n  --- 从未EXECUTE的子进程的Subject.cmdLine（前20个） ---")
    shown = 0
    for child_uuid in children_without_execute:
        if child_uuid in subject_info and shown < 20:
            info = subject_info[child_uuid]
            parent_info = subject_info.get(info.get('parent'), {})
            print(f"    子Subject.path={info.get('path')}  cmdLine={str(info.get('cmdLine'))[:50]}")
            print(f"      父Subject.path={parent_info.get('path')}")
            shown += 1

    # ============ 分析4：多次EXECUTE的完整生命周期 ============
    print(f"\n{'='*80}")
    print("四、多次EXECUTE的UUID生命周期")
    print(f"{'='*80}")

    exe_dist = Counter(uuid_execute_count.values())
    print(f"  EXECUTE次数分布:")
    for cnt, num in sorted(exe_dist.items()):
        print(f"    {cnt}次: {num:,}")
        if cnt > 5:
            break

    # 多次EXECUTE的进程，Subject.cmdLine是第一次还是最后一次？
    print(f"\n  --- 多次EXECUTE进程的Subject.cmdLine分析 ---")
    multi_exec_uuids = [uid for uid, cnt in uuid_execute_count.items() if cnt >= 2]
    print(f"  多次EXECUTE的进程数: {len(multi_exec_uuids):,}")

    shown = 0
    for uid in multi_exec_uuids[:15]:
        info = subject_info.get(uid, {})
        execs = sorted([e for e in execute_events if e['src'] == uid], key=lambda x: x['ts'])
        first_exec_cmd = execs[0]['event_cmdline'] if execs else '?'
        last_exec_cmd = execs[-1]['event_cmdline'] if execs else '?'

        print(f"\n    UUID={uid[:20]}... ({len(execs)}次EXECUTE)")
        print(f"      Subject.path: {info.get('path')}")
        print(f"      Subject.cmdLine: {str(info.get('cmdLine'))[:60]}")
        print(f"      第1次Event.cmdLine: {str(first_exec_cmd)[:60]}")
        print(f"      最后Event.cmdLine:  {str(last_exec_cmd)[:60]}")
        # Subject.cmdLine匹配哪个？
        if info.get('cmdLine') == first_exec_cmd:
            print(f"      → Subject.cmdLine == 第1次EXECUTE ✓")
        elif info.get('cmdLine') == last_exec_cmd:
            print(f"      → Subject.cmdLine == 最后EXECUTE ✓")
        else:
            print(f"      → Subject.cmdLine 与所有EXECUTE.cmdLine都不同")
        shown += 1


if __name__ == '__main__':
    main()
