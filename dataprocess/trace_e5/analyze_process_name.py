"""
TRACE E5 进程命名策略探索

目的：对比 Subject.cmdLine.string 和 Subject.properties.map.name 作为进程命名
的优劣，决定提取脚本应该用哪个。

观察维度：
  1. 填充率
  2. 同一进程的 name 和 cmdLine 的对应关系
  3. name 的去重情况（多少个不同的 name）
  4. cmdLine 的去重情况
  5. cmdLine 中是否含大量参数/路径噪声 → 难以聚类
  6. name 是否够短、稳定 → 适合聚类
  7. 同一 name 下有多少种不同的 cmdLine（说明 name 抽象层次高）

用法：
  python analyze_process_name.py [<file>...]
"""

import json
import sys
from collections import Counter, defaultdict


def analyze(filepaths, max_lines=None):
    print(f"分析文件: {filepaths}")
    print("=" * 80)

    n_subjects = 0
    n_process = 0
    n_unit = 0

    # 单字段统计
    name_filled = 0
    cmd_filled = 0
    both_filled = 0
    only_name = 0
    only_cmd = 0
    neither = 0

    # name 与 cmdLine 关系（仅对 PROCESS）
    process_name_set = set()
    process_cmd_set = set()
    name_to_cmds = defaultdict(set)   # name → set of cmdLines
    cmd_to_names = defaultdict(set)   # cmdLine → set of names

    # 长度统计
    name_len_dist = Counter()  # 分桶
    cmd_len_dist = Counter()

    # 样本
    name_only_samples = []
    cmd_only_samples = []
    pair_samples = []

    # 抓取 5 个具体 process 的所有 (name, cmd, parent) 看看
    process_records = []

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
                if short != 'Subject':
                    continue

                n_subjects += 1
                stype = body.get('type', 'UNKNOWN')
                if stype == 'SUBJECT_PROCESS':
                    n_process += 1
                elif stype == 'SUBJECT_UNIT':
                    n_unit += 1

                # 仅对 PROCESS 做对比（UNIT 是 TRACE 特殊产物）
                if stype != 'SUBJECT_PROCESS':
                    continue

                # cmdLine
                cmd = body.get('cmdLine')
                cmd_val = None
                if isinstance(cmd, dict):
                    cmd_val = cmd.get('string')
                elif isinstance(cmd, str):
                    cmd_val = cmd

                # props.map.name
                props = body.get('properties') or {}
                pmap = props.get('map') if isinstance(props, dict) else None
                name_val = pmap.get('name') if isinstance(pmap, dict) else None

                # 双字段联合统计
                has_name = bool(name_val)
                has_cmd = bool(cmd_val)
                if has_name:
                    name_filled += 1
                    process_name_set.add(name_val)
                if has_cmd:
                    cmd_filled += 1
                    process_cmd_set.add(cmd_val)

                if has_name and has_cmd:
                    both_filled += 1
                    name_to_cmds[name_val].add(cmd_val)
                    cmd_to_names[cmd_val].add(name_val)
                elif has_name:
                    only_name += 1
                    if len(name_only_samples) < 10:
                        name_only_samples.append(name_val)
                elif has_cmd:
                    only_cmd += 1
                    if len(cmd_only_samples) < 10:
                        cmd_only_samples.append(cmd_val)
                else:
                    neither += 1

                # 长度分桶
                if has_name:
                    L = len(name_val)
                    bucket = (L // 10) * 10  # 0-9, 10-19, 20-29, ...
                    name_len_dist[bucket] += 1
                if has_cmd:
                    L = len(cmd_val)
                    bucket = (L // 20) * 20  # 0-19, 20-39, ...
                    cmd_len_dist[bucket] += 1

                # 配对样本
                if has_name and has_cmd and len(pair_samples) < 30:
                    pair_samples.append((name_val, cmd_val))

                # 抓 5 个具体 process 看完整记录
                if len(process_records) < 8:
                    process_records.append({
                        'uuid': body.get('uuid'),
                        'cid': body.get('cid'),
                        'name': name_val,
                        'cmd': cmd_val,
                        'props_map': dict(pmap) if isinstance(pmap, dict) else {},
                    })

    # ---- 输出 ----
    print(f"\n总扫描行数: {total:,}")
    print(f"Subject 总数: {n_subjects:,}  PROCESS: {n_process:,}  UNIT: {n_unit:,}")
    print(f"\n仅对 SUBJECT_PROCESS 做命名对比 ({n_process:,} 条)")

    print(f"\n=== 一、填充率对比 ===")
    if n_process > 0:
        print(f"  Subject.props.name 填充     : {name_filled:>8,} / {n_process:,}  ({name_filled/n_process*100:5.1f}%)")
        print(f"  Subject.cmdLine.string 填充 : {cmd_filled:>8,} / {n_process:,}  ({cmd_filled/n_process*100:5.1f}%)")
        print(f"  两者都有                    : {both_filled:>8,}  ({both_filled/n_process*100:5.1f}%)")
        print(f"  仅有 name                   : {only_name:>8,}  ({only_name/n_process*100:5.1f}%)")
        print(f"  仅有 cmdLine                : {only_cmd:>8,}  ({only_cmd/n_process*100:5.1f}%)")
        print(f"  两者都无                    : {neither:>8,}  ({neither/n_process*100:5.1f}%)")

    print(f"\n=== 二、去重数（聚类能力） ===")
    print(f"  distinct name 数:    {len(process_name_set):>8,}  ← name 把进程聚成几类")
    print(f"  distinct cmdLine 数: {len(process_cmd_set):>8,}  ← cmdLine 几乎每个进程一个")
    if len(process_name_set) and len(process_cmd_set):
        ratio = len(process_cmd_set) / len(process_name_set)
        print(f"  比例 cmd/name: {ratio:.1f}× → cmdLine 是 name 的 {ratio:.1f} 倍细")

    print(f"\n=== 三、长度分布 ===")
    print(f"  --- name 长度分桶 ---")
    for b in sorted(name_len_dist.keys()):
        cnt = name_len_dist[b]
        bar = '#' * min(50, cnt * 50 // max(name_filled, 1))
        print(f"    [{b:>3}-{b+9:>3}] {cnt:>8,}  {bar}")
    print(f"\n  --- cmdLine 长度分桶 ---")
    for b in sorted(cmd_len_dist.keys()):
        cnt = cmd_len_dist[b]
        bar = '#' * min(50, cnt * 50 // max(cmd_filled, 1))
        print(f"    [{b:>3}-{b+19:>3}] {cnt:>8,}  {bar}")

    print(f"\n=== 四、name → cmdLine 一对多分析（同 name 下有多少不同 cmd） ===")
    if name_to_cmds:
        cmds_per_name = sorted([(len(v), k) for k, v in name_to_cmds.items()], reverse=True)
        print(f"  前 15 个 name 对应的 cmdLine 数（说明 name 抽象层次高）:")
        for cnt, name in cmds_per_name[:15]:
            sample_cmds = list(name_to_cmds[name])[:3]
            print(f"    name={name!r:25s} → {cnt} 个不同 cmdLine")
            for cmd in sample_cmds:
                print(f"        ↳ {cmd[:100]}")
        print(f"\n  --- 分布统计 ---")
        bucket = Counter()
        for cnt, _ in cmds_per_name:
            if cnt == 1:
                bucket['1'] += 1
            elif cnt <= 5:
                bucket['2-5'] += 1
            elif cnt <= 20:
                bucket['6-20'] += 1
            elif cnt <= 100:
                bucket['21-100'] += 1
            else:
                bucket['100+'] += 1
        for k in ['1', '2-5', '6-20', '21-100', '100+']:
            print(f"    name 对应 {k:>6s} 个 cmdLine: {bucket.get(k, 0):>6,}")

    print(f"\n=== 五、cmdLine → name 一对多分析 ===")
    if cmd_to_names:
        names_per_cmd = sorted([(len(v), k) for k, v in cmd_to_names.items()], reverse=True)
        print(f"  前 10 个 cmdLine 对应多个 name 的情况（理想为 0，说明 cmd 不归一）:")
        cnt_more_than_1 = 0
        for cnt, cmd in names_per_cmd[:10]:
            if cnt > 1:
                cnt_more_than_1 += 1
                print(f"    cmd={cmd[:80]!r}  → {cnt} 个不同 name: {list(cmd_to_names[cmd])[:5]}")
        if cnt_more_than_1 == 0:
            print(f"    ✓ 全部 cmdLine 仅对应 1 个 name（cmdLine 是 name 的细化）")

    print(f"\n=== 六、配对样本（前 30 个 PROCESS 的 name / cmdLine） ===")
    for name, cmd in pair_samples[:30]:
        print(f"  name={name!r:25s}  cmd={cmd[:100]}")

    print(f"\n=== 七、单字段样本 ===")
    if name_only_samples:
        print(f"  仅有 name 的样本:")
        for n in name_only_samples:
            print(f"    {n}")
    if cmd_only_samples:
        print(f"\n  仅有 cmdLine 的样本:")
        for c in cmd_only_samples:
            print(f"    {c[:120]}")

    print(f"\n=== 八、完整 PROCESS 记录样本 ===")
    for rec in process_records:
        print(f"\n  uuid={rec['uuid']}")
        print(f"    cid={rec['cid']}  name={rec['name']!r}  cmd={(rec['cmd'] or '')[:120]!r}")
        print(f"    props.map={rec['props_map']}")

    # ---- 结论建议 ----
    print(f"\n{'='*80}\n命名策略建议\n{'='*80}")
    print(f"  name 填充率高 ({name_filled/max(n_process,1)*100:.1f}%) → 可以作为主命名源")
    print(f"  cmdLine 填充率 {cmd_filled/max(n_process,1)*100:.1f}% → 但含参数/路径噪声，聚类粒度过细")
    print(f"  distinct name {len(process_name_set):,} vs distinct cmd {len(process_cmd_set):,}")
    print(f"  name 适合做节点 label/聚类 key，cmdLine 适合放边属性留作详情")
    print(f"\n  推荐方案: ")
    print(f"    name (主) → fallback cmdLine.string 提取第一个 token 的 basename")
    print(f"    完整 cmdLine 仍可存 uuid_cmdline.pkl 用于 FORK/EXECUTE 边的 cmdline 字段")


def _default_files():
    prefix = "/mnt/disk1/darpa_e5/trace/example/"
    return [
        prefix + 'ta1-trace-1-e5-official-1.bin.1.json',
        prefix + 'ta1-trace-1-e5-official-1.bin.1.json.1',
    ]


if __name__ == '__main__':
    if len(sys.argv) < 2:
        files = _default_files()
    else:
        files = sys.argv[1:]
    analyze(files)
