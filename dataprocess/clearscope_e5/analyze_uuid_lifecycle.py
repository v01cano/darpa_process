"""
ClearScope E5 UUID 生命周期分析

参照 trace_e3/analyze_uuid_lifecycle.py。

目的：
  1. 弄清 SUBJECT_PROCESS / SUBJECT_THREAD 是否存在以及如何关联
  2. 同一进程是否存在多个 UUID（FORK 或 EXECUTE 是否产生新 UUID）
  3. parentSubject 链路是否完整
  4. UUID 大小写情况（E5 是 cdm20，可能与 E3/E4 不同）
  5. EVENT_FORK / EVENT_EXECUTE 是否存在（ClearScope E3 都没有）

输出：
  - SUBJECT 类型分布
  - 顶层字段填充
  - parent 链统计
  - thread vs process 比例
  - UUID 大小写统计（前 100k 条 Subject）
  - FORK / EXECUTE 事件计数
"""

import json
import sys
from collections import Counter, defaultdict


def analyze(filepaths, max_lines=None):
    print(f"分析文件: {filepaths}")
    print("=" * 80)

    subject_type_counter = Counter()
    subject_total = 0
    process_uuids = set()
    thread_uuids = set()
    parent_links = []  # (child_uuid, parent_uuid)
    parent_link_count = 0
    cmdline_by_uuid = {}

    # UUID 大小写
    uuid_case_counter = Counter()  # 'all_upper' / 'all_lower' / 'mixed' / 'no_letters'

    # 事件类型
    event_type_counter = Counter()
    fork_pairs = []          # (parent_uuid, child_uuid) 自 FORK 事件
    execute_records = []     # (subject_uuid, predicate_uuid)

    total = 0
    for fp in filepaths:
        print(f"\n  扫描 {fp} ...")
        with open(fp, 'r') as f:
            for line_no, line in enumerate(f, 1):
                total += 1
                if total % 500000 == 0:
                    print(f"    已扫描 {total:,} 行...")
                if max_lines and line_no > max_lines:
                    break
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                d = rec.get('datum')
                if not isinstance(d, dict) or not d:
                    continue
                full_type = next(iter(d.keys()))
                body = d[full_type]
                if not isinstance(body, dict):
                    continue
                short = full_type.rsplit('.', 1)[-1]

                if short == 'Subject':
                    subject_total += 1
                    stype = body.get('type', 'UNKNOWN')
                    subject_type_counter[stype] += 1
                    uuid = body.get('uuid')
                    if isinstance(uuid, str):
                        # 大小写
                        letters = [c for c in uuid if c.isalpha()]
                        if not letters:
                            uuid_case_counter['no_letters'] += 1
                        elif all(c.isupper() for c in letters):
                            uuid_case_counter['all_upper'] += 1
                        elif all(c.islower() for c in letters):
                            uuid_case_counter['all_lower'] += 1
                        else:
                            uuid_case_counter['mixed'] += 1

                        if stype == 'SUBJECT_PROCESS':
                            process_uuids.add(uuid)
                        elif stype == 'SUBJECT_THREAD':
                            thread_uuids.add(uuid)

                    parent = body.get('parentSubject')
                    if isinstance(parent, dict):
                        pu = (parent.get('com.bbn.tc.schema.avro.cdm20.UUID')
                              or parent.get('com.bbn.tc.schema.avro.cdm18.UUID')
                              or parent.get('UUID'))
                        if pu and len(parent_links) < 50:
                            parent_links.append((uuid, pu))
                        if pu:
                            parent_link_count += 1

                    cmd = body.get('cmdLine')
                    cmd_str = None
                    if isinstance(cmd, str):
                        cmd_str = cmd
                    elif isinstance(cmd, dict):
                        cmd_str = cmd.get('string')
                    if cmd_str and isinstance(uuid, str) and len(cmdline_by_uuid) < 30:
                        cmdline_by_uuid[uuid] = cmd_str

                elif short == 'Event':
                    etype = body.get('type', 'UNKNOWN')
                    event_type_counter[etype] += 1
                    if etype in ('EVENT_FORK', 'EVENT_CLONE', 'EVENT_EXECUTE'):
                        subj = body.get('subject')
                        po = body.get('predicateObject')
                        su = (subj.get('com.bbn.tc.schema.avro.cdm20.UUID') if isinstance(subj, dict)
                              else None)
                        pu = (po.get('com.bbn.tc.schema.avro.cdm20.UUID') if isinstance(po, dict)
                              else None)
                        if etype in ('EVENT_FORK', 'EVENT_CLONE'):
                            if su and pu and len(fork_pairs) < 30:
                                fork_pairs.append((etype, su, pu))
                        else:
                            if su and pu and len(execute_records) < 30:
                                execute_records.append((su, pu, body.get('predicateObjectPath')))

    # ---- 输出 ----
    print(f"\n总扫描行数: {total:,}")

    print(f"\n=== Subject 类型分布 ===")
    for st, cnt in subject_type_counter.most_common():
        print(f"  {st:30s} {cnt:>10,}")

    print(f"\n=== UUID 数量 ===")
    print(f"  PROCESS UUIDs: {len(process_uuids):,}")
    print(f"  THREAD  UUIDs: {len(thread_uuids):,}")
    print(f"  PROCESS ∩ THREAD: {len(process_uuids & thread_uuids):,}  (应该是 0)")

    print(f"\n=== UUID 大小写分布（前 N 条 Subject 抽样） ===")
    for case, cnt in uuid_case_counter.most_common():
        print(f"  {case:15s} {cnt:>10,}")

    print(f"\n=== parentSubject 链 ===")
    print(f"  有 parent 的 Subject: {parent_link_count:,} / {subject_total:,}")
    print(f"  样本（child → parent）:")
    for c, p in parent_links[:10]:
        print(f"    {c} → {p}")

    print(f"\n=== cmdLine 样本 ===")
    for u, cmd in list(cmdline_by_uuid.items())[:15]:
        print(f"  {u}  cmd={str(cmd)[:80]}")

    print(f"\n=== 事件类型 top 30 ===")
    for et, cnt in event_type_counter.most_common(30):
        print(f"  {et:35s} {cnt:>12,}")

    fork_count = (event_type_counter.get('EVENT_FORK', 0)
                  + event_type_counter.get('EVENT_CLONE', 0))
    exec_count = event_type_counter.get('EVENT_EXECUTE', 0)
    print(f"\n=== FORK / EXECUTE 是否存在？ ===")
    print(f"  EVENT_FORK + EVENT_CLONE: {fork_count:,}")
    print(f"  EVENT_EXECUTE:            {exec_count:,}")
    print(f"  → 如果都为 0，说明 ClearScope E5 仍是 Android 风格无 FORK/EXEC（与 E3 一致）")

    print(f"\n=== FORK 样本 ===")
    for et, su, pu in fork_pairs[:10]:
        print(f"  [{et}] subject={su}  predObj={pu}")

    print(f"\n=== EXECUTE 样本 ===")
    for su, pu, pop in execute_records[:10]:
        path = pop.get('string') if isinstance(pop, dict) else pop
        print(f"  subject={su}  predObj={pu}  path={path}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        files = [
            "/mnt/disk/darpa/clearscope_e5/ta1-clearscope-1-e5-official-1.bin.json",
            "/mnt/disk/darpa/clearscope_e5/ta1-clearscope-1-e5-official-1.bin.json.1",
        ]
    else:
        files = sys.argv[1:]
    analyze(files)
