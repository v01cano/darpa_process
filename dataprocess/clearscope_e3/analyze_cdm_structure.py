"""
ClearScope E3 CDM数据结构分析脚本

ClearScope 是 Android 平台数据集，已知差异（来自各项目解析器）：
  - CDM18 格式
  - Subject 无 properties.map.path（Orthrus明确设为None）
  - Subject cmdLine 是 dict 格式 {"string": "..."}
  - FileObject 路径在 path 字段（非 filename）
  - Android 特有的进程/文件结构

本脚本分析实际的字段填充率，确认上述信息。

用法：
  python analyze_cdm_structure.py /mnt/disk/darpa/clearscope_e3/ta1-clearscope-e3-official.json
"""

import json
import sys
from collections import Counter, defaultdict

def analyze_file(filepath):
    print(f"分析文件: {filepath}")
    print("=" * 80)

    record_type_counter = Counter()

    # ============ Subject ============
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
    subject_has_cid = 0
    subject_properties_keys = Counter()
    subject_toplevel_keys = Counter()
    subject_path_samples = []
    subject_name_samples = []
    subject_cmdline_samples = []

    # ============ FileObject ============
    file_count = 0
    file_type_counter = Counter()
    file_has_filename = 0
    file_has_path = 0
    file_has_name = 0
    file_base_props_keys = Counter()
    file_filename_samples = []
    file_path_samples = []

    # ============ NetFlowObject ============
    net_count = 0
    net_has_local_addr = 0
    net_has_remote_addr = 0
    net_local_addr_type = Counter()
    net_remote_addr_type = Counter()

    # ============ Event ============
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

                # 收集所有顶层key
                for k in record_datum.keys():
                    subject_toplevel_keys[k] += 1

                if record_datum.get('type') not in ('SUBJECT_PROCESS',):
                    # 也统计其他类型的Subject
                    pass

                # properties.map 分析
                props = record_datum.get('properties', {})
                if isinstance(props, dict):
                    props_map = props.get('map', {})
                    if isinstance(props_map, dict):
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
                    else:
                        # properties不是嵌套map
                        for k in props:
                            subject_properties_keys[f"(no-map).{k}"] += 1
                        if props.get('name') is not None:
                            subject_has_props_name += 1

                # cid
                if record_datum.get('cid') is not None:
                    subject_has_cid += 1

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
                if isinstance(base_props, dict):
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
                    net_local_addr_type[type(la).__name__] += 1
                if ra is not None:
                    net_has_remote_addr += 1
                    net_remote_addr_type[type(ra).__name__] += 1

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

                if isinstance(record_datum.get('name'), dict):
                    event_has_name += 1
                    if len(event_name_samples) < 15:
                        event_name_samples.append(record_datum['name'].get('string', ''))

                raw_props = record_datum.get('properties')
                props_map = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                if not isinstance(props_map, dict):
                    props_map = {}
                if props_map:
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

    # ============ 输出 ============
    total_process = sum(cnt for st, cnt in subject_type_counter.items())

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
    print(f"\n  --- 字段填充率 (全部Subject) ---")
    if subject_count > 0:
        print(f"  properties.map.path : {subject_has_props_path:>8,} / {subject_count:,}  ({subject_has_props_path/subject_count*100:5.1f}%)")
        print(f"  properties.map.name : {subject_has_props_name:>8,} / {subject_count:,}  ({subject_has_props_name/subject_count*100:5.1f}%)")
        print(f"  properties.map.tgid : {subject_has_tgid:>8,} / {subject_count:,}  ({subject_has_tgid/subject_count*100:5.1f}%)")
        print(f"  properties.map.ppid : {subject_has_ppid:>8,} / {subject_count:,}  ({subject_has_ppid/subject_count*100:5.1f}%)")
        print(f"  cid (PID)           : {subject_has_cid:>8,} / {subject_count:,}  ({subject_has_cid/subject_count*100:5.1f}%)")
        print(f"  cmdLine (非null)    : {subject_has_cmdline:>8,} / {subject_count:,}  ({subject_has_cmdline/subject_count*100:5.1f}%)")
        print(f"    其中 string类型   : {subject_cmdline_is_str:>8,}")
        print(f"    其中 dict类型     : {subject_cmdline_is_dict:>8,}")
        print(f"    null/缺失         : {subject_cmdline_is_null:>8,}")
        print(f"  parentSubject       : {subject_has_parent:>8,} / {subject_count:,}  ({subject_has_parent/subject_count*100:5.1f}%)")
    print(f"\n  properties.map中的所有key:")
    for k, cnt in subject_properties_keys.most_common():
        print(f"    {k:30s} {cnt:>10,}")
    print(f"\n  Subject顶层key:")
    for k, cnt in subject_toplevel_keys.most_common():
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
    if file_count > 0:
        print(f"\n  --- 字段填充率 ---")
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
        print(f"  localAddress  非null: {net_has_local_addr:>8,} / {net_count:,}")
        print(f"    类型: {dict(net_local_addr_type)}")
        print(f"  remoteAddress 非null: {net_has_remote_addr:>8,} / {net_count:,}")
        print(f"    类型: {dict(net_remote_addr_type)}")

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

    print(f"\n{'='*80}")
    print("七、与 CADETS/THEIA E3 的关键差异（预期 vs 实际）")
    print(f"{'='*80}")
    print(f"  Subject.properties.map.path 填充率:  {subject_has_props_path/max(subject_count,1)*100:.1f}% (CADETS=0%, THEIA=99.3%)")
    print(f"  Subject.properties.map.name 填充率:  {subject_has_props_name/max(subject_count,1)*100:.1f}% (CADETS=0%, THEIA=0%)")
    print(f"  Subject.cmdLine 填充率:              {subject_has_cmdline/max(subject_count,1)*100:.1f}% (CADETS=0%, THEIA=99.3%)")
    print(f"  FileObject filename 填充率:          {file_has_filename/max(file_count,1)*100:.1f}% (CADETS=0%, THEIA=97.4%)")
    print(f"  FileObject path 填充率:              {file_has_path/max(file_count,1)*100:.1f}% (CADETS=0%, THEIA=0%)")
    print(f"  Event.exec 填充率:                   {event_has_exec/max(event_count,1)*100:.1f}% (CADETS=99.9%, THEIA=0%)")
    print(f"  Event.cmdLine 填充率:                {event_has_cmdline/max(event_count,1)*100:.1f}% (CADETS=0.5%, THEIA=0.1%)")
    print(f"  Event.predicateObjectPath 填充率:    {event_has_predicate_object_path/max(event_count,1)*100:.1f}% (CADETS=44.2%, THEIA=0%)")


if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else "/mnt/disk/darpa/clearscope_e3/ta1-clearscope-e3-official.json"
    analyze_file(filepath)
