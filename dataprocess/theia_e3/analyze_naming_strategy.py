"""
THEIA E3 进程命名策略分析

核心问题：
1. Subject.path vs EXECUTE.dst.filename — 哪个更准确作为进程名？
2. Subject.cmdLine vs Event.cmdLine — 哪个放在CLONE边上？
3. 用第几次EXECUTE的值？

方法：对所有做过EXECUTE的进程，逐一对比：
  - Subject.path（创建时）
  - 第1次EXECUTE的dst文件名 和 Event.cmdLine
  - 最后一次EXECUTE的dst文件名 和 Event.cmdLine
  - Subject.cmdLine
"""

import json
import os
import sys
from collections import defaultdict, Counter

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
    existing_files = [f for f in FILE_LIST if os.path.exists(os.path.join(INPUT_DIR, f))]
    print(f"找到 {len(existing_files)} 个文件")

    # ============ 全量收集 ============
    subject_info = {}   # uuid → {path, cmdLine}
    file_info = {}      # uuid → {filename}

    # 每个UUID的所有EXECUTE事件（按时间顺序）
    uuid_executes = defaultdict(list)  # uuid → [(ts, event_cmdline, dst_uuid)]

    # CLONE事件
    clone_events = []   # [{src, dst, ts}]

    loaded = 0
    for fname in existing_files:
        fpath = os.path.join(INPUT_DIR, fname)
        print(f"  处理: {fname}")
        with open(fpath, 'r') as fin:
            for line in fin:
                loaded += 1
                if loaded % 2000000 == 0:
                    print(f"    {loaded:,} 行...")

                datum = json.loads(line)['datum']
                rtype_full = list(datum.keys())[0]
                datum = datum[rtype_full]
                rtype = rtype_full.split('.')[-1]

                if rtype == 'Subject':
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = datum['uuid']
                        props = datum.get('properties', {}).get('map', {})
                        cmdline = datum.get('cmdLine')
                        if isinstance(cmdline, dict):
                            cmdline = cmdline.get('string')
                        subject_info[uid] = {
                            'path': props.get('path'),
                            'cmdLine': cmdline,
                        }

                elif rtype == 'FileObject':
                    uid = datum['uuid']
                    base_props = datum.get('baseObject', {}).get('properties', {}).get('map', {})
                    file_info[uid] = {'filename': base_props.get('filename')}

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

                    if etype == 'EVENT_EXECUTE' and src:
                        uuid_executes[src].append((ts, props.get('cmdLine'), dst))

                    if etype == 'EVENT_CLONE' and src and dst:
                        clone_events.append({'src': src, 'dst': dst, 'ts': ts})

    print(f"\n全量扫描完成: {loaded:,} 行")
    print(f"  Subject: {len(subject_info):,}")
    print(f"  FileObject: {len(file_info):,}")
    print(f"  做过EXECUTE的进程: {len(uuid_executes):,}")
    print(f"  CLONE事件: {len(clone_events):,}")

    # 排序每个UUID的EXECUTE事件
    for uid in uuid_executes:
        uuid_executes[uid].sort(key=lambda x: x[0])

    # ============ 分析1：Subject.path vs EXECUTE dst filename ============
    print(f"\n{'='*80}")
    print("一、Subject.path vs EXECUTE.dst.filename 对比")
    print(f"{'='*80}")

    # 对每个做过EXECUTE的进程，对比4个值
    path_eq_first_dst = 0
    path_eq_last_dst = 0
    path_ne_any_dst = 0
    first_eq_last = 0
    first_ne_last = 0

    examples_path_eq_first = []
    examples_path_eq_last = []
    examples_path_ne_any = []

    for uid, execs in uuid_executes.items():
        info = subject_info.get(uid, {})
        subj_path = info.get('path')

        first_dst_uid = execs[0][2]
        last_dst_uid = execs[-1][2]
        first_dst_name = file_info.get(first_dst_uid, {}).get('filename')
        last_dst_name = file_info.get(last_dst_uid, {}).get('filename')
        first_event_cmd = execs[0][1]
        last_event_cmd = execs[-1][1]

        if len(execs) > 1:
            if first_dst_name == last_dst_name:
                first_eq_last += 1
            else:
                first_ne_last += 1

        if subj_path == first_dst_name:
            path_eq_first_dst += 1
            if len(examples_path_eq_first) < 10:
                examples_path_eq_first.append((uid, subj_path, info.get('cmdLine'), first_dst_name, last_dst_name, first_event_cmd, last_event_cmd, len(execs)))
        elif subj_path == last_dst_name:
            path_eq_last_dst += 1
            if len(examples_path_eq_last) < 10:
                examples_path_eq_last.append((uid, subj_path, info.get('cmdLine'), first_dst_name, last_dst_name, first_event_cmd, last_event_cmd, len(execs)))
        else:
            path_ne_any_dst += 1
            if len(examples_path_ne_any) < 10:
                examples_path_ne_any.append((uid, subj_path, info.get('cmdLine'), first_dst_name, last_dst_name, first_event_cmd, last_event_cmd, len(execs)))

    total_exec_procs = len(uuid_executes)
    print(f"\n  做过EXECUTE的进程总数: {total_exec_procs:,}")
    print(f"\n  Subject.path == 第1次EXECUTE的dst文件名: {path_eq_first_dst:>8,} ({path_eq_first_dst/total_exec_procs*100:.1f}%)")
    print(f"  Subject.path == 最后EXECUTE的dst文件名:  {path_eq_last_dst:>8,} ({path_eq_last_dst/total_exec_procs*100:.1f}%)")
    print(f"  Subject.path != 任何EXECUTE的dst文件名:  {path_ne_any_dst:>8,} ({path_ne_any_dst/total_exec_procs*100:.1f}%)")

    multi_exec = sum(1 for execs in uuid_executes.values() if len(execs) > 1)
    print(f"\n  多次EXECUTE的进程: {multi_exec:,}")
    if multi_exec > 0:
        print(f"  其中第1次dst == 最后dst: {first_eq_last:,} ({first_eq_last/multi_exec*100:.1f}%)")
        print(f"  其中第1次dst != 最后dst: {first_ne_last:,} ({first_ne_last/multi_exec*100:.1f}%)")

    print(f"\n  --- Subject.path == 第1次dst 的例子 ---")
    for uid, sp, sc, fd, ld, fc, lc, n in examples_path_eq_first[:5]:
        print(f"    Subject.path={sp}")
        print(f"    Subject.cmdLine={str(sc)[:60]}")
        print(f"    第1次dst={fd}  Event.cmdLine={str(fc)[:50]}")
        if n > 1:
            print(f"    最后dst={ld}  Event.cmdLine={str(lc)[:50]}")
        print()

    print(f"\n  --- Subject.path != 任何dst（Subject.path是父进程身份）的例子 ---")
    for uid, sp, sc, fd, ld, fc, lc, n in examples_path_ne_any[:10]:
        print(f"    Subject.path={sp}")
        print(f"    Subject.cmdLine={str(sc)[:60]}")
        print(f"    第1次dst={fd}  Event.cmdLine={str(fc)[:50]}")
        if n > 1:
            print(f"    最后dst={ld}  Event.cmdLine={str(lc)[:50]}")
        print()

    # ============ 分析2：Subject.cmdLine vs Event.cmdLine 对比 ============
    print(f"\n{'='*80}")
    print("二、Subject.cmdLine vs Event.cmdLine 作为CLONE边cmdLine")
    print(f"{'='*80}")

    subcmd_eq_first_ecmd = 0
    subcmd_eq_last_ecmd = 0
    subcmd_ne_any_ecmd = 0

    examples_subcmd_eq_first = []
    examples_subcmd_ne_any = []

    for uid, execs in uuid_executes.items():
        info = subject_info.get(uid, {})
        subj_cmd = info.get('cmdLine')
        first_event_cmd = execs[0][1]
        last_event_cmd = execs[-1][1]

        if subj_cmd == first_event_cmd:
            subcmd_eq_first_ecmd += 1
            if len(examples_subcmd_eq_first) < 5:
                examples_subcmd_eq_first.append((uid, info.get('path'), subj_cmd, first_event_cmd, last_event_cmd, len(execs)))
        elif subj_cmd == last_event_cmd:
            subcmd_eq_last_ecmd += 1
        else:
            subcmd_ne_any_ecmd += 1
            if len(examples_subcmd_ne_any) < 10:
                examples_subcmd_ne_any.append((uid, info.get('path'), subj_cmd, first_event_cmd, last_event_cmd, len(execs)))

    print(f"\n  Subject.cmdLine == 第1次Event.cmdLine: {subcmd_eq_first_ecmd:>8,} ({subcmd_eq_first_ecmd/total_exec_procs*100:.1f}%)")
    print(f"  Subject.cmdLine == 最后Event.cmdLine:  {subcmd_eq_last_ecmd:>8,} ({subcmd_eq_last_ecmd/total_exec_procs*100:.1f}%)")
    print(f"  Subject.cmdLine != 任何Event.cmdLine:  {subcmd_ne_any_ecmd:>8,} ({subcmd_ne_any_ecmd/total_exec_procs*100:.1f}%)")

    print(f"\n  --- Subject.cmdLine == 第1次Event.cmdLine 的例子 ---")
    for uid, sp, sc, fc, lc, n in examples_subcmd_eq_first[:5]:
        print(f"    Subject.path={sp}  Subject.cmdLine={str(sc)[:60]}")
        print(f"    第1次Event.cmdLine={str(fc)[:60]}")
        if n > 1:
            print(f"    最后Event.cmdLine={str(lc)[:60]}")
        print()

    print(f"\n  --- Subject.cmdLine != 任何Event.cmdLine 的例子 ---")
    for uid, sp, sc, fc, lc, n in examples_subcmd_ne_any[:10]:
        print(f"    Subject.path={sp}  Subject.cmdLine={str(sc)[:60]}")
        print(f"    第1次Event.cmdLine={str(fc)[:60]}")
        if n > 1:
            print(f"    最后Event.cmdLine={str(lc)[:60]}")
        print()

    # ============ 分析3：CLONE的子进程命名分析 ============
    print(f"\n{'='*80}")
    print("三、CLONE子进程的命名策略对比")
    print(f"{'='*80}")

    # 对每个CLONE的子进程，对比各种命名来源
    clone_child_has_exec = 0
    clone_child_no_exec = 0

    # 子进程名字来源对比
    child_path_eq_first_dst = 0
    child_path_eq_last_dst = 0
    child_path_eq_parent_path = 0
    child_path_ne_all = 0

    child_cmd_eq_first_ecmd = 0
    child_cmd_ne_first_ecmd = 0

    examples_clone_with_exec = []
    examples_clone_without_exec = []

    for ce in clone_events:
        child = ce['dst']
        parent = ce['src']
        child_info = subject_info.get(child, {})
        parent_info = subject_info.get(parent, {})

        child_path = child_info.get('path')
        child_cmd = child_info.get('cmdLine')
        parent_path = parent_info.get('path')

        if child in uuid_executes:
            clone_child_has_exec += 1
            execs = uuid_executes[child]
            first_dst_name = file_info.get(execs[0][2], {}).get('filename')
            last_dst_name = file_info.get(execs[-1][2], {}).get('filename')
            first_event_cmd = execs[0][1]

            if child_path == first_dst_name:
                child_path_eq_first_dst += 1
            elif child_path == last_dst_name:
                child_path_eq_last_dst += 1
            elif child_path == parent_path:
                child_path_eq_parent_path += 1
            else:
                child_path_ne_all += 1

            if child_cmd == first_event_cmd:
                child_cmd_eq_first_ecmd += 1
            else:
                child_cmd_ne_first_ecmd += 1

            if len(examples_clone_with_exec) < 15:
                examples_clone_with_exec.append({
                    'parent_path': parent_path,
                    'child_path': child_path,
                    'child_cmd': child_cmd,
                    'first_dst': first_dst_name,
                    'last_dst': last_dst_name,
                    'first_ecmd': first_event_cmd,
                    'last_ecmd': execs[-1][1],
                    'n_exec': len(execs),
                })
        else:
            clone_child_no_exec += 1
            if len(examples_clone_without_exec) < 10:
                examples_clone_without_exec.append({
                    'parent_path': parent_path,
                    'child_path': child_path,
                    'child_cmd': child_cmd,
                })

    print(f"\n  CLONE子进程总数: {len(clone_events):,}")
    print(f"  子进程做过EXECUTE: {clone_child_has_exec:,} ({clone_child_has_exec/len(clone_events)*100:.1f}%)")
    print(f"  子进程未EXECUTE:   {clone_child_no_exec:,} ({clone_child_no_exec/len(clone_events)*100:.1f}%)")

    print(f"\n  --- 做过EXECUTE的子进程: Subject.path匹配什么？ ---")
    print(f"  Subject.path == 第1次EXECUTE dst: {child_path_eq_first_dst:>8,} ({child_path_eq_first_dst/max(clone_child_has_exec,1)*100:.1f}%)")
    print(f"  Subject.path == 最后EXECUTE dst:  {child_path_eq_last_dst:>8,} ({child_path_eq_last_dst/max(clone_child_has_exec,1)*100:.1f}%)")
    print(f"  Subject.path == 父进程path:       {child_path_eq_parent_path:>8,} ({child_path_eq_parent_path/max(clone_child_has_exec,1)*100:.1f}%)")
    print(f"  Subject.path != 以上所有:          {child_path_ne_all:>8,}")

    print(f"\n  --- 做过EXECUTE的子进程: Subject.cmdLine匹配什么？ ---")
    print(f"  Subject.cmdLine == 第1次Event.cmdLine: {child_cmd_eq_first_ecmd:>8,} ({child_cmd_eq_first_ecmd/max(clone_child_has_exec,1)*100:.1f}%)")
    print(f"  Subject.cmdLine != 第1次Event.cmdLine: {child_cmd_ne_first_ecmd:>8,} ({child_cmd_ne_first_ecmd/max(clone_child_has_exec,1)*100:.1f}%)")

    print(f"\n  --- 做过EXECUTE的CLONE子进程详细例子 ---")
    for e in examples_clone_with_exec:
        print(f"    父进程path: {e['parent_path']}")
        print(f"    子Subject.path: {e['child_path']}")
        print(f"    子Subject.cmdLine: {str(e['child_cmd'])[:60]}")
        print(f"    第1次EXECUTE dst文件: {e['first_dst']}  Event.cmdLine={str(e['first_ecmd'])[:50]}")
        if e['n_exec'] > 1:
            print(f"    最后EXECUTE dst文件: {e['last_dst']}  Event.cmdLine={str(e['last_ecmd'])[:50]}")
        # 判断Subject.path匹配哪个
        matches = []
        if e['child_path'] == e['first_dst']:
            matches.append("第1次dst")
        if e['child_path'] == e['last_dst']:
            matches.append("最后dst")
        if e['child_path'] == e['parent_path']:
            matches.append("父进程path")
        print(f"    → Subject.path 匹配: {matches if matches else '都不匹配'}")
        print()

    print(f"\n  --- 未EXECUTE的CLONE子进程例子 ---")
    for e in examples_clone_without_exec[:10]:
        same = "相同" if e['child_path'] == e['parent_path'] else "不同"
        print(f"    父={e['parent_path']}  →  子={e['child_path']} ({same})  cmdLine={str(e['child_cmd'])[:50]}")


if __name__ == '__main__':
    main()
