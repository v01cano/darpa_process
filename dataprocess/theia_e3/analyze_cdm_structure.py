"""
THEIA E3 CDM数据结构分析脚本

与 CADETS E3 对比，THEIA 的已知差异（来自 CAPTAIN 的 theia_parser.py）：
  - Subject: 进程名在 properties.map.path（非 properties.name）
  - Subject: PID 在 properties.map.tgid（非顶层 cid）
  - Subject: cmdLine 是 dict 格式 {"string": "..."}（非直接字符串）
  - FileObject: 路径在 baseObject.properties.map.filename（非 path）
  - Event: 无 properties.map.exec 字段（CADETS 独有）
  - Event: 事件名在 datum['name']['string']
  - Event: mprotect 使用 properties.map.prot（非 arg_mem_flags）

本脚本分析 THEIA E3 的实际字段填充率，验证上述差异。

用法：
  python analyze_cdm_structure.py /mnt/disk/darpa/theia_e3/ta1-theia-e3-official-6r.json.8
"""

import json
import sys
from collections import Counter, defaultdict

def analyze_file(filepath):
    print(f"分析文件: {filepath}")
    print("=" * 80)

    record_type_counter = Counter()

    # ============ Subject 分析 ============
    subject_count = 0
    subject_type_counter = Counter()
    subject_has_props_path = 0
    subject_has_props_name = 0
    subject_has_cmdline = 0
    subject_cmdline_is_str = 0
    subject_cmdline_is_dict = 0
    subject_cmdline_is_null = 0
    subject_has_tgid = 0
    subject_has_ppid = 0
    subject_has_parent = 0
    subject_properties_keys = Counter()
    subject_path_samples = []
    subject_name_samples = []
    subject_cmdline_samples = []

    # ============ FileObject 分析 ============
    file_count = 0
    file_type_counter = Counter()
    file_has_filename = 0
    file_has_path = 0
    file_has_name = 0
    file_base_props_keys = Counter()
    file_filename_samples = []
    file_path_samples = []

    # ============ NetFlowObject 分析 ============
    net_count = 0
    net_has_local_addr = 0
    net_has_remote_addr = 0
    net_local_addr_is_str = 0
    net_local_addr_is_dict = 0
    net_remote_addr_is_str = 0
    net_remote_addr_is_dict = 0

    # ============ Event 分析 ============
    event_count = 0
    event_type_counter = Counter()
    event_has_subject = 0
    event_has_predicate_object = 0
    event_has_predicate_object2 = 0
    event_has_predicate_object_path = 0
    event_has_predicate_object2_path = 0
    event_has_exec = 0
    event_has_cmdline = 0
    event_has_name = 0
    event_properties_keys = Counter()
    event_exec_samples = []
    event_cmdline_samples = []
    event_name_samples = []
    event_type_has_exec = Counter()
    event_type_has_cmdline = Counter()
    event_type_has_pred_obj = Counter()
    event_type_has_pred_obj_path = Counter()

    # ============ 其他 ============
    other_record_types = Counter()

    loaded_line = 0
    with open(filepath, 'r') as fin:
        for line in fin:
            loaded_line += 1
            if loaded_line % 200000 == 0:
                print(f"  已扫描 {loaded_line:,} 行...")

            record_datum = json.loads(line)['datum']
            record_type_full = list(record_datum.keys())[0]
            record_datum = record_datum[record_type_full]
            record_type = record_type_full.split('.')[-1]
            record_type_counter[record_type] += 1

            # ---------- Subject ----------
            if record_type == 'Subject':
                subject_count += 1
                subject_type_counter[record_datum.get('type', 'UNKNOWN')] += 1

                if record_datum.get('type') != 'SUBJECT_PROCESS':
                    continue

                # properties.map 分析
                props_map = record_datum.get('properties', {}).get('map', {})
                for k in props_map:
                    subject_properties_keys[k] += 1

                if props_map.get('path') is not None:
                    subject_has_props_path += 1
                    if len(subject_path_samples) < 10:
                        subject_path_samples.append(props_map['path'])
                if props_map.get('name') is not None:
                    subject_has_props_name += 1
                    if len(subject_name_samples) < 10:
                        subject_name_samples.append(props_map['name'])
                if props_map.get('tgid') is not None:
                    subject_has_tgid += 1
                if props_map.get('ppid') is not None:
                    subject_has_ppid += 1

                # parentSubject
                if record_datum.get('parentSubject') is not None:
                    subject_has_parent += 1

                # cmdLine
                cmdline_val = record_datum.get('cmdLine')
                if cmdline_val is None:
                    subject_cmdline_is_null += 1
                elif isinstance(cmdline_val, str):
                    subject_has_cmdline += 1
                    subject_cmdline_is_str += 1
                    if len(subject_cmdline_samples) < 10:
                        subject_cmdline_samples.append(cmdline_val)
                elif isinstance(cmdline_val, dict):
                    val = cmdline_val.get('string')
                    if val:
                        subject_has_cmdline += 1
                        subject_cmdline_is_dict += 1
                        if len(subject_cmdline_samples) < 10:
                            subject_cmdline_samples.append(val)
                    else:
                        subject_cmdline_is_null += 1

            # ---------- FileObject ----------
            elif record_type == 'FileObject':
                file_count += 1
                file_type_counter[record_datum.get('type', 'UNKNOWN')] += 1

                base = record_datum.get('baseObject', {})
                base_props = base.get('properties', {}).get('map', {})
                for k in base_props:
                    file_base_props_keys[k] += 1

                if base_props.get('filename') is not None:
                    file_has_filename += 1
                    if len(file_filename_samples) < 10:
                        file_filename_samples.append(base_props['filename'])
                if base_props.get('path') is not None:
                    file_has_path += 1
                    if len(file_path_samples) < 10:
                        file_path_samples.append(base_props['path'])
                if base_props.get('name') is not None:
                    file_has_name += 1

            # ---------- NetFlowObject ----------
            elif record_type == 'NetFlowObject':
                net_count += 1
                la = record_datum.get('localAddress')
                ra = record_datum.get('remoteAddress')
                if la is not None:
                    net_has_local_addr += 1
                    if isinstance(la, str): net_local_addr_is_str += 1
                    elif isinstance(la, dict): net_local_addr_is_dict += 1
                if ra is not None:
                    net_has_remote_addr += 1
                    if isinstance(ra, str): net_remote_addr_is_str += 1
                    elif isinstance(ra, dict): net_remote_addr_is_dict += 1

            # ---------- Event ----------
            elif record_type == 'Event':
                event_count += 1
                etype = record_datum.get('type', 'UNKNOWN')
                event_type_counter[etype] += 1

                has_subj = isinstance(record_datum.get('subject'), dict)
                has_po = isinstance(record_datum.get('predicateObject'), dict)
                has_po2 = isinstance(record_datum.get('predicateObject2'), dict)
                has_pop = isinstance(record_datum.get('predicateObjectPath'), dict)
                has_po2p = isinstance(record_datum.get('predicateObject2Path'), dict)

                if has_subj: event_has_subject += 1
                if has_po:
                    event_has_predicate_object += 1
                    event_type_has_pred_obj[etype] += 1
                if has_po2: event_has_predicate_object2 += 1
                if has_pop:
                    event_has_predicate_object_path += 1
                    event_type_has_pred_obj_path[etype] += 1
                if has_po2p: event_has_predicate_object2_path += 1

                # Event name field
                if isinstance(record_datum.get('name'), dict):
                    event_has_name += 1
                    name_val = record_datum['name'].get('string', '')
                    if len(event_name_samples) < 20:
                        event_name_samples.append(name_val)

                # properties.map
                props_map = record_datum.get('properties', {}).get('map', {})
                for k in props_map:
                    event_properties_keys[k] += 1

                if 'exec' in props_map:
                    event_has_exec += 1
                    event_type_has_exec[etype] += 1
                    if len(event_exec_samples) < 10:
                        event_exec_samples.append(props_map['exec'])

                if 'cmdLine' in props_map:
                    event_has_cmdline += 1
                    event_type_has_cmdline[etype] += 1
                    if len(event_cmdline_samples) < 10:
                        event_cmdline_samples.append(props_map['cmdLine'])

            else:
                other_record_types[record_type] += 1

    # ============ 输出报告 ============
    total_process = subject_type_counter.get('SUBJECT_PROCESS', 0)

    print(f"\n总行数: {loaded_line:,}")
    print(f"\n{'='*80}")
    print("一、记录类型分布")
    print(f"{'='*80}")
    for rt, cnt in record_type_counter.most_common():
        print(f"  {rt:30s} {cnt:>12,}")

    print(f"\n{'='*80}")
    print("二、Subject 分析")
    print(f"{'='*80}")
    print(f"  Subject总数: {subject_count:,}")
    print(f"  类型分布:")
    for st, cnt in subject_type_counter.most_common():
        print(f"    {st:30s} {cnt:>10,}")
    print(f"\n  --- SUBJECT_PROCESS ({total_process:,}) 字段填充率 ---")
    if total_process > 0:
        print(f"  properties.map.path : {subject_has_props_path:>8,} / {total_process:,}  ({subject_has_props_path/total_process*100:5.1f}%)")
        print(f"  properties.map.name : {subject_has_props_name:>8,} / {total_process:,}  ({subject_has_props_name/total_process*100:5.1f}%)")
        print(f"  properties.map.tgid : {subject_has_tgid:>8,} / {total_process:,}  ({subject_has_tgid/total_process*100:5.1f}%)")
        print(f"  properties.map.ppid : {subject_has_ppid:>8,} / {total_process:,}  ({subject_has_ppid/total_process*100:5.1f}%)")
        print(f"  cmdLine (非null)    : {subject_has_cmdline:>8,} / {total_process:,}  ({subject_has_cmdline/total_process*100:5.1f}%)")
        print(f"    其中 string类型   : {subject_cmdline_is_str:>8,}")
        print(f"    其中 dict类型     : {subject_cmdline_is_dict:>8,}")
        print(f"    null/缺失         : {subject_cmdline_is_null:>8,}")
        print(f"  parentSubject       : {subject_has_parent:>8,} / {total_process:,}  ({subject_has_parent/total_process*100:5.1f}%)")
    print(f"\n  properties.map中的所有key:")
    for k, cnt in subject_properties_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  path样例: {subject_path_samples[:5]}")
    print(f"  name样例: {subject_name_samples[:5]}")
    print(f"  cmdLine样例: {subject_cmdline_samples[:5]}")

    print(f"\n{'='*80}")
    print("三、FileObject 分析")
    print(f"{'='*80}")
    print(f"  FileObject总数: {file_count:,}")
    print(f"  类型分布:")
    for ft, cnt in file_type_counter.most_common():
        print(f"    {ft:30s} {cnt:>10,}")
    print(f"\n  --- 字段填充率 ---")
    if file_count > 0:
        print(f"  baseObject.props.map.filename  : {file_has_filename:>8,} / {file_count:,}  ({file_has_filename/file_count*100:5.1f}%)")
        print(f"  baseObject.props.map.path      : {file_has_path:>8,} / {file_count:,}  ({file_has_path/file_count*100:5.1f}%)")
        print(f"  baseObject.props.map.name      : {file_has_name:>8,} / {file_count:,}  ({file_has_name/file_count*100:5.1f}%)")
    print(f"\n  baseObject.properties.map中的所有key:")
    for k, cnt in file_base_props_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  filename样例: {file_filename_samples[:5]}")
    print(f"  path样例: {file_path_samples[:5]}")

    print(f"\n{'='*80}")
    print("四、NetFlowObject 分析")
    print(f"{'='*80}")
    print(f"  NetFlowObject总数: {net_count:,}")
    if net_count > 0:
        print(f"  localAddress  非null: {net_has_local_addr:>8,} / {net_count:,}  ({net_has_local_addr/net_count*100:5.1f}%)")
        print(f"    string类型: {net_local_addr_is_str:,}  dict类型: {net_local_addr_is_dict:,}")
        print(f"  remoteAddress 非null: {net_has_remote_addr:>8,} / {net_count:,}  ({net_has_remote_addr/net_count*100:5.1f}%)")
        print(f"    string类型: {net_remote_addr_is_str:,}  dict类型: {net_remote_addr_is_dict:,}")

    print(f"\n{'='*80}")
    print("五、Event 分析")
    print(f"{'='*80}")
    print(f"  Event总数: {event_count:,}")
    if event_count > 0:
        print(f"\n  --- 关联字段填充率 ---")
        print(f"  subject              : {event_has_subject:>10,} / {event_count:,}  ({event_has_subject/event_count*100:5.1f}%)")
        print(f"  predicateObject      : {event_has_predicate_object:>10,} / {event_count:,}  ({event_has_predicate_object/event_count*100:5.1f}%)")
        print(f"  predicateObject2     : {event_has_predicate_object2:>10,} / {event_count:,}  ({event_has_predicate_object2/event_count*100:5.1f}%)")
        print(f"  predicateObjectPath  : {event_has_predicate_object_path:>10,} / {event_count:,}  ({event_has_predicate_object_path/event_count*100:5.1f}%)")
        print(f"  predicateObject2Path : {event_has_predicate_object2_path:>10,} / {event_count:,}  ({event_has_predicate_object2_path/event_count*100:5.1f}%)")
        print(f"  name字段             : {event_has_name:>10,} / {event_count:,}  ({event_has_name/event_count*100:5.1f}%)")
        print(f"\n  --- properties.map中exec/cmdLine ---")
        print(f"  有exec字段的Event    : {event_has_exec:>10,} / {event_count:,}  ({event_has_exec/event_count*100:5.1f}%)")
        print(f"  有cmdLine字段的Event : {event_has_cmdline:>10,} / {event_count:,}  ({event_has_cmdline/event_count*100:5.1f}%)")

    print(f"\n  --- 事件类型分布 ---")
    for et, cnt in event_type_counter.most_common():
        exec_cnt = event_type_has_exec.get(et, 0)
        cmd_cnt = event_type_has_cmdline.get(et, 0)
        po_cnt = event_type_has_pred_obj.get(et, 0)
        pop_cnt = event_type_has_pred_obj_path.get(et, 0)
        extras = []
        if exec_cnt > 0: extras.append(f"exec={exec_cnt}")
        if cmd_cnt > 0: extras.append(f"cmdLine={cmd_cnt}")
        extra_str = f"  [{', '.join(extras)}]" if extras else ""
        print(f"    {et:35s} {cnt:>10,}  (有predObj={po_cnt:,}, 有path={pop_cnt:,}){extra_str}")

    print(f"\n  properties.map中的所有key:")
    for k, cnt in event_properties_keys.most_common(30):
        print(f"    {k:30s} {cnt:>10,}")

    print(f"\n  exec样例: {event_exec_samples[:5]}")
    print(f"  cmdLine样例: {event_cmdline_samples[:5]}")
    print(f"  Event.name样例: {event_name_samples[:10]}")

    print(f"\n{'='*80}")
    print("六、其他记录类型")
    print(f"{'='*80}")
    for ot, cnt in other_record_types.most_common():
        print(f"  {ot}: {cnt:,}")

    # ============ 与 CADETS E3 的关键差异总结 ============
    print(f"\n{'='*80}")
    print("七、与 CADETS E3 的关键差异（预期 vs 实际）")
    print(f"{'='*80}")
    print(f"  Subject.properties.map.path 填充率: {subject_has_props_path/max(total_process,1)*100:.1f}% (CADETS=0%)")
    print(f"  Subject.properties.map.name 填充率: {subject_has_props_name/max(total_process,1)*100:.1f}% (CADETS=0%)")
    print(f"  Subject.cmdLine 填充率:             {subject_has_cmdline/max(total_process,1)*100:.1f}% (CADETS=0%)")
    print(f"  FileObject filename 填充率:         {file_has_filename/max(file_count,1)*100:.1f}% (CADETS=0%)")
    print(f"  Event.exec 填充率:                  {event_has_exec/max(event_count,1)*100:.1f}% (CADETS=99.9%)")
    print(f"  Event.cmdLine 填充率:               {event_has_cmdline/max(event_count,1)*100:.1f}% (CADETS=0.5%)")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/theia_e3/ta1-theia-e3-official-6r.json.8"
    analyze_file(filepath)
