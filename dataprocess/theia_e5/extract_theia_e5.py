"""
THEIA E5 数据提取脚本（本地磁盘版）

基于对 2367 万行原始 CDM20 数据的统计分析，与 THEIA E3 对比设计的方案。

THEIA E5 与 E3 的关键差异：
  1. CDM 命名空间: cdm20（E3 是 cdm18）
  2. Subject.path 填充率 100%（E3 99.3%）→ 单遍提取即可，无需 EXECUTE.dst 更新
  3. Subject.cmdLine 是 dict {"string": "..."}（E3 是 str）
  4. NetFlow 地址 / 端口是 dict {"string":...} / {"int": n}（E3 直接 str/int）
  5. UUID 全部大写（含 00000000-... 全零占位）→ 提取时统一 .lower()
  6. FileObject 只有 FILE_OBJECT_BLOCK 类型（E3 是 FILE/DIR 区分）
  7. Event.predicateObject2 100% 总是 subject 自身（冗余 marker，忽略）
  8. EVENT_EXECUTE 仍然有 properties.cmdLine（100%），用于 EXECUTE 边
  9. EVENT_CLONE 子进程后续会有 Subject 记录带 cmdLine，可作 FORK 边的 cmdLine 回填
 10. fork+exec 分离模型保留（416/416 EXECUTE subject 100% 已定义为 SUBJECT_PROCESS）

节点类型（3 种，与其他 6 个数据集统一）：
  - subject  : SUBJECT_PROCESS, name = Subject.properties.map.path
  - file     : FileObject (FILE_OBJECT_BLOCK), name = baseObject.properties.map.filename
  - netflow  : NetFlowObject (dict 解包)

边过滤 + 反转（11 种保留，5 种反转）：
  保留: READ, WRITE, OPEN, EXECUTE, CLONE, CONNECT,
        SENDTO, RECVFROM, SENDMSG, RECVMSG, UNLINK
  反转: READ, RECVFROM, RECVMSG, OPEN, EXECUTE (file/netflow → process)

用法：
  python extract_theia_e5.py \\
      --input_dir /mnt/disk/darpa/cch_refine/theia_e5_json \\
      --output_dir /mnt/disk/darpa/theia_e5_output \\
      --batch_size 3
"""

import json
import os
import time
import argparse
import pickle
import glob
from collections import Counter

# ============================================================================
# 配置
# ============================================================================

# THEIA E5 完整文件列表（357 个 json 分片，按时间/编号顺序）
DEFAULT_FILE_LIST = """
ta1-theia-1-e5-official-1.json
ta1-theia-1-e5-official-1.json.1
ta1-theia-1-e5-official-1.json.2
ta1-theia-1-e5-official-1.bin.json
ta1-theia-1-e5-official-1.bin.json.1
ta1-theia-1-e5-official-1.bin.json.2
ta1-theia-1-e5-official-1.bin.1.json
ta1-theia-1-e5-official-1.bin.1.json.1
ta1-theia-1-e5-official-1.bin.1.json.2
ta1-theia-1-e5-official-1.bin.2.json
ta1-theia-1-e5-official-2.bin.json
ta1-theia-1-e5-official-2.bin.json.1
ta1-theia-1-e5-official-2.bin.json.2
ta1-theia-1-e5-official-2.bin.1.json
ta1-theia-1-e5-official-2.bin.1.json.1
ta1-theia-1-e5-official-2.bin.1.json.2
ta1-theia-1-e5-official-2.bin.2.json
ta1-theia-1-e5-official-2.bin.2.json.1
ta1-theia-1-e5-official-2.bin.2.json.2
ta1-theia-1-e5-official-2.bin.3.json
ta1-theia-1-e5-official-2.bin.3.json.1
ta1-theia-1-e5-official-2.bin.3.json.2
ta1-theia-1-e5-official-2.bin.4.json
ta1-theia-1-e5-official-2.bin.4.json.1
ta1-theia-1-e5-official-2.bin.4.json.2
ta1-theia-1-e5-official-2.bin.5.json
ta1-theia-1-e5-official-2.bin.5.json.1
ta1-theia-1-e5-official-2.bin.5.json.2
ta1-theia-1-e5-official-2.bin.6.json
ta1-theia-1-e5-official-2.bin.6.json.1
ta1-theia-1-e5-official-2.bin.6.json.2
ta1-theia-1-e5-official-2.bin.7.json
ta1-theia-1-e5-official-2.bin.7.json.1
ta1-theia-1-e5-official-2.bin.7.json.2
ta1-theia-1-e5-official-2.bin.8.json
ta1-theia-1-e5-official-2.bin.8.json.1
ta1-theia-1-e5-official-2.bin.8.json.2
ta1-theia-1-e5-official-2.bin.9.json
ta1-theia-1-e5-official-2.bin.9.json.1
ta1-theia-1-e5-official-2.bin.9.json.2
ta1-theia-1-e5-official-2.bin.10.json
ta1-theia-1-e5-official-2.bin.10.json.1
ta1-theia-1-e5-official-2.bin.10.json.2
ta1-theia-1-e5-official-2.bin.11.json
ta1-theia-1-e5-official-2.bin.11.json.1
ta1-theia-1-e5-official-2.bin.11.json.2
ta1-theia-1-e5-official-2.bin.12.json
ta1-theia-1-e5-official-2.bin.12.json.1
ta1-theia-1-e5-official-2.bin.12.json.2
ta1-theia-1-e5-official-2.bin.13.json
ta1-theia-1-e5-official-2.bin.13.json.1
ta1-theia-1-e5-official-2.bin.13.json.2
ta1-theia-1-e5-official-2.bin.14.json
ta1-theia-1-e5-official-2.bin.14.json.1
ta1-theia-1-e5-official-2.bin.14.json.2
ta1-theia-1-e5-official-2.bin.15.json
ta1-theia-1-e5-official-2.bin.15.json.1
ta1-theia-1-e5-official-2.bin.15.json.2
ta1-theia-1-e5-official-2.bin.16.json
ta1-theia-1-e5-official-2.bin.16.json.1
ta1-theia-1-e5-official-2.bin.16.json.2
ta1-theia-1-e5-official-2.bin.17.json
ta1-theia-1-e5-official-2.bin.17.json.1
ta1-theia-1-e5-official-2.bin.17.json.2
ta1-theia-1-e5-official-2.bin.18.json
ta1-theia-1-e5-official-2.bin.18.json.1
ta1-theia-1-e5-official-2.bin.18.json.2
ta1-theia-1-e5-official-2.bin.19.json
ta1-theia-1-e5-official-2.bin.19.json.1
ta1-theia-1-e5-official-2.bin.19.json.2
ta1-theia-1-e5-official-2.bin.20.json
ta1-theia-1-e5-official-2.bin.20.json.1
ta1-theia-1-e5-official-2.bin.20.json.2
ta1-theia-1-e5-official-2.bin.21.json
ta1-theia-1-e5-official-2.bin.21.json.1
ta1-theia-1-e5-official-2.bin.21.json.2
ta1-theia-1-e5-official-2.bin.22.json
ta1-theia-1-e5-official-2.bin.22.json.1
ta1-theia-1-e5-official-2.bin.22.json.2
ta1-theia-1-e5-official-2.bin.23.json
ta1-theia-1-e5-official-2.bin.23.json.1
ta1-theia-1-e5-official-2.bin.23.json.2
ta1-theia-1-e5-official-2.bin.24.json
ta1-theia-1-e5-official-2.bin.24.json.1
ta1-theia-1-e5-official-2.bin.24.json.2
ta1-theia-1-e5-official-2.bin.25.json
ta1-theia-1-e5-official-2.bin.25.json.1
ta1-theia-1-e5-official-2.bin.25.json.2
ta1-theia-1-e5-official-2.bin.26.json
ta1-theia-1-e5-official-2.bin.26.json.1
ta1-theia-1-e5-official-2.bin.26.json.2
ta1-theia-1-e5-official-2.bin.27.json
ta1-theia-1-e5-official-2.bin.27.json.1
ta1-theia-1-e5-official-2.bin.27.json.2
ta1-theia-1-e5-official-2.bin.28.json
ta1-theia-1-e5-official-2.bin.28.json.1
ta1-theia-1-e5-official-2.bin.28.json.2
ta1-theia-1-e5-official-2.bin.29.json
ta1-theia-1-e5-official-2.bin.29.json.1
ta1-theia-1-e5-official-2.bin.29.json.2
ta1-theia-1-e5-official-2.bin.30.json
ta1-theia-1-e5-official-2.bin.30.json.1
ta1-theia-1-e5-official-2.bin.30.json.2
ta1-theia-1-e5-official-2.bin.31.json
ta1-theia-1-e5-official-2.bin.31.json.1
ta1-theia-1-e5-official-2.bin.31.json.2
ta1-theia-1-e5-official-2.bin.32.json
ta1-theia-1-e5-official-2.bin.32.json.1
ta1-theia-1-e5-official-2.bin.32.json.2
ta1-theia-1-e5-official-2.bin.33.json
ta1-theia-1-e5-official-2.bin.33.json.1
ta1-theia-1-e5-official-2.bin.33.json.2
ta1-theia-1-e5-official-2.bin.34.json
ta1-theia-1-e5-official-2.bin.34.json.1
ta1-theia-1-e5-official-2.bin.34.json.2
ta1-theia-1-e5-official-2.bin.35.json
ta1-theia-1-e5-official-2.bin.35.json.1
ta1-theia-1-e5-official-2.bin.35.json.2
ta1-theia-1-e5-official-2.bin.36.json
ta1-theia-1-e5-official-2.bin.36.json.1
ta1-theia-1-e5-official-2.bin.36.json.2
ta1-theia-1-e5-official-2.bin.37.json
ta1-theia-1-e5-official-2.bin.37.json.1
ta1-theia-1-e5-official-2.bin.37.json.2
ta1-theia-1-e5-official-2.bin.38.json
ta1-theia-1-e5-official-2.bin.38.json.1
ta1-theia-1-e5-official-2.bin.38.json.2
ta1-theia-1-e5-official-2.bin.39.json
ta1-theia-2-e5-official-1.bin.json
ta1-theia-2-e5-official-1.bin.1.json
ta1-theia-2-e5-official-1.bin.1.json.1
ta1-theia-2-e5-official-1.bin.1.json.2
ta1-theia-2-e5-official-1.bin.2.json
ta1-theia-2-e5-official-1.bin.2.json.1
ta1-theia-2-e5-official-1.bin.json.1
ta1-theia-2-e5-official-1.bin.json.2
ta1-theia-2-e5-official-2.bin.json
ta1-theia-2-e5-official-2.bin.json.1
ta1-theia-2-e5-official-2.bin.json.2
ta1-theia-2-e5-official-2.bin.1.json
ta1-theia-2-e5-official-2.bin.1.json.1
ta1-theia-2-e5-official-2.bin.1.json.2
ta1-theia-2-e5-official-2.bin.2.json
ta1-theia-2-e5-official-2.bin.2.json.1
ta1-theia-2-e5-official-2.bin.2.json.2
ta1-theia-2-e5-official-2.bin.3.json
ta1-theia-2-e5-official-2.bin.3.json.1
ta1-theia-2-e5-official-2.bin.3.json.2
ta1-theia-2-e5-official-2.bin.4.json
ta1-theia-2-e5-official-2.bin.4.json.1
ta1-theia-2-e5-official-2.bin.4.json.2
ta1-theia-2-e5-official-2.bin.5.json
ta1-theia-2-e5-official-2.bin.5.json.1
ta1-theia-2-e5-official-2.bin.5.json.2
ta1-theia-2-e5-official-2.bin.6.json
ta1-theia-2-e5-official-2.bin.6.json.1
ta1-theia-2-e5-official-2.bin.6.json.2
ta1-theia-2-e5-official-2.bin.7.json
ta1-theia-2-e5-official-2.bin.7.json.1
ta1-theia-2-e5-official-2.bin.7.json.2
ta1-theia-2-e5-official-2.bin.8.json
ta1-theia-2-e5-official-2.bin.8.json.1
ta1-theia-2-e5-official-2.bin.8.json.2
ta1-theia-2-e5-official-2.bin.9.json
ta1-theia-2-e5-official-2.bin.9.json.1
ta1-theia-2-e5-official-2.bin.9.json.2
ta1-theia-2-e5-official-2.bin.10.json
ta1-theia-2-e5-official-2.bin.10.json.1
ta1-theia-2-e5-official-2.bin.10.json.2
ta1-theia-2-e5-official-2.bin.11.json
ta1-theia-2-e5-official-2.bin.11.json.1
ta1-theia-2-e5-official-2.bin.11.json.2
ta1-theia-2-e5-official-2.bin.12.json
ta1-theia-2-e5-official-2.bin.12.json.1
ta1-theia-2-e5-official-2.bin.12.json.2
ta1-theia-2-e5-official-2.bin.13.json
ta1-theia-2-e5-official-2.bin.13.json.1
ta1-theia-2-e5-official-2.bin.13.json.2
ta1-theia-2-e5-official-2.bin.14.json
ta1-theia-2-e5-official-2.bin.14.json.1
ta1-theia-2-e5-official-2.bin.14.json.2
ta1-theia-2-e5-official-2.bin.15.json
ta1-theia-2-e5-official-2.bin.15.json.1
ta1-theia-2-e5-official-2.bin.15.json.2
ta1-theia-2-e5-official-2.bin.16.json
ta1-theia-2-e5-official-2.bin.16.json.1
ta1-theia-2-e5-official-2.bin.16.json.2
ta1-theia-2-e5-official-2.bin.17.json
ta1-theia-2-e5-official-2.bin.17.json.1
ta1-theia-2-e5-official-2.bin.17.json.2
ta1-theia-2-e5-official-2.bin.18.json
ta1-theia-2-e5-official-2.bin.18.json.1
ta1-theia-2-e5-official-2.bin.18.json.2
ta1-theia-2-e5-official-2.bin.19.json
ta1-theia-2-e5-official-2.bin.19.json.1
ta1-theia-2-e5-official-2.bin.19.json.2
ta1-theia-2-e5-official-2.bin.20.json
ta1-theia-2-e5-official-2.bin.20.json.1
ta1-theia-2-e5-official-2.bin.20.json.2
ta1-theia-2-e5-official-2.bin.21.json
ta1-theia-2-e5-official-2.bin.21.json.1
ta1-theia-2-e5-official-2.bin.21.json.2
ta1-theia-2-e5-official-2.bin.22.json
ta1-theia-2-e5-official-2.bin.22.json.1
ta1-theia-2-e5-official-2.bin.22.json.2
ta1-theia-2-e5-official-2.bin.23.json
ta1-theia-2-e5-official-2.bin.23.json.1
ta1-theia-2-e5-official-2.bin.23.json.2
ta1-theia-2-e5-official-2.bin.24.json
ta1-theia-2-e5-official-2.bin.24.json.1
ta1-theia-2-e5-official-2.bin.24.json.2
ta1-theia-2-e5-official-2.bin.25.json
ta1-theia-2-e5-official-2.bin.25.json.1
ta1-theia-2-e5-official-2.bin.25.json.2
ta1-theia-2-e5-official-2.bin.26.json
ta1-theia-2-e5-official-2.bin.26.json.1
ta1-theia-2-e5-official-2.bin.26.json.2
ta1-theia-2-e5-official-2.bin.27.json
ta1-theia-2-e5-official-2.bin.27.json.1
ta1-theia-2-e5-official-2.bin.27.json.2
ta1-theia-2-e5-official-2.bin.28.json
ta1-theia-2-e5-official-2.bin.28.json.1
ta1-theia-2-e5-official-2.bin.28.json.2
ta1-theia-2-e5-official-2.bin.29.json
ta1-theia-2-e5-official-2.bin.29.json.1
ta1-theia-2-e5-official-2.bin.29.json.2
ta1-theia-2-e5-official-2.bin.30.json
ta1-theia-2-e5-official-2.bin.30.json.1
ta1-theia-2-e5-official-2.bin.30.json.2
ta1-theia-2-e5-official-2.bin.31.json
ta1-theia-2-e5-official-2.bin.31.json.1
ta1-theia-2-e5-official-2.bin.31.json.2
ta1-theia-2-e5-official-2.bin.32.json
ta1-theia-2-e5-official-2.bin.32.json.1
ta1-theia-2-e5-official-2.bin.32.json.2
ta1-theia-2-e5-official-2.bin.33.json
ta1-theia-2-e5-official-2.bin.33.json.1
ta1-theia-2-e5-official-2.bin.33.json.2
ta1-theia-2-e5-official-2.bin.34.json
ta1-theia-2-e5-official-2.bin.34.json.1
ta1-theia-2-e5-official-2.bin.34.json.2
ta1-theia-2-e5-official-2.bin.35.json
ta1-theia-2-e5-official-2.bin.35.json.1
ta1-theia-2-e5-official-2.bin.35.json.2
ta1-theia-2-e5-official-2.bin.36.json
ta1-theia-2-e5-official-2.bin.36.json.1
ta1-theia-2-e5-official-2.bin.36.json.2
ta1-theia-2-e5-official-2.bin.37.json
ta1-theia-2-e5-official-2.bin.37.json.1
ta1-theia-2-e5-official-2.bin.37.json.2
ta1-theia-2-e5-official-2.bin.38.json
ta1-theia-2-e5-official-2.bin.38.json.1
ta1-theia-2-e5-official-2.bin.38.json.2
ta1-theia-2-e5-official-2.bin.39.json
ta1-theia-2-e5-official-2.bin.39.json.1
ta1-theia-2-e5-official-2.bin.39.json.2
ta1-theia-2-e5-official-2.bin.40.json
ta1-theia-2-e5-official-2.bin.40.json.1
ta1-theia-2-e5-official-2.bin.40.json.2
ta1-theia-2-e5-official-2.bin.41.json
ta1-theia-2-e5-official-2.bin.41.json.1
ta1-theia-3-e5-official-1.bin.json
ta1-theia-3-e5-official-1.bin.json.1
ta1-theia-3-e5-official-1.bin.json.2
ta1-theia-3-e5-official-1.bin.1.json
ta1-theia-3-e5-official-1.bin.1.json.1
ta1-theia-3-e5-official-1.bin.1.json.2
ta1-theia-3-e5-official-1.bin.2.json
ta1-theia-3-e5-official-2.bin.json
ta1-theia-3-e5-official-2.bin.json.1
ta1-theia-3-e5-official-2.bin.json.2
ta1-theia-3-e5-official-2.bin.1.json
ta1-theia-3-e5-official-2.bin.1.json.1
ta1-theia-3-e5-official-2.bin.1.json.2
ta1-theia-3-e5-official-2.bin.2.json
ta1-theia-3-e5-official-2.bin.2.json.1
ta1-theia-3-e5-official-2.bin.2.json.2
ta1-theia-3-e5-official-2.bin.3.json
ta1-theia-3-e5-official-2.bin.3.json.1
ta1-theia-3-e5-official-2.bin.3.json.2
ta1-theia-3-e5-official-2.bin.4.json
ta1-theia-3-e5-official-2.bin.4.json.1
ta1-theia-3-e5-official-2.bin.4.json.2
ta1-theia-3-e5-official-2.bin.5.json
ta1-theia-3-e5-official-2.bin.5.json.1
ta1-theia-3-e5-official-2.bin.5.json.2
ta1-theia-3-e5-official-2.bin.6.json
ta1-theia-3-e5-official-2.bin.6.json.1
ta1-theia-3-e5-official-2.bin.6.json.2
ta1-theia-3-e5-official-2.bin.7.json
ta1-theia-3-e5-official-2.bin.7.json.1
ta1-theia-3-e5-official-2.bin.7.json.2
ta1-theia-3-e5-official-2.bin.8.json
ta1-theia-3-e5-official-2.bin.8.json.1
ta1-theia-3-e5-official-2.bin.8.json.2
ta1-theia-3-e5-official-2.bin.9.json
ta1-theia-3-e5-official-2.bin.9.json.1
ta1-theia-3-e5-official-2.bin.9.json.2
ta1-theia-3-e5-official-2.bin.10.json
ta1-theia-3-e5-official-2.bin.10.json.1
ta1-theia-3-e5-official-2.bin.10.json.2
ta1-theia-3-e5-official-2.bin.11.json
ta1-theia-3-e5-official-2.bin.11.json.1
ta1-theia-3-e5-official-2.bin.11.json.2
ta1-theia-3-e5-official-2.bin.12.json
ta1-theia-3-e5-official-2.bin.12.json.1
ta1-theia-3-e5-official-2.bin.12.json.2
ta1-theia-3-e5-official-2.bin.13.json
ta1-theia-3-e5-official-2.bin.13.json.1
ta1-theia-3-e5-official-2.bin.13.json.2
ta1-theia-3-e5-official-2.bin.14.json
ta1-theia-3-e5-official-2.bin.14.json.1
ta1-theia-3-e5-official-2.bin.14.json.2
ta1-theia-3-e5-official-2.bin.15.json
ta1-theia-3-e5-official-2.bin.15.json.1
ta1-theia-3-e5-official-2.bin.15.json.2
ta1-theia-3-e5-official-2.bin.16.json
ta1-theia-3-e5-official-2.bin.16.json.1
ta1-theia-3-e5-official-2.bin.16.json.2
ta1-theia-3-e5-official-2.bin.17.json
ta1-theia-3-e5-official-2.bin.17.json.1
ta1-theia-3-e5-official-2.bin.17.json.2
ta1-theia-3-e5-official-2.bin.18.json
ta1-theia-3-e5-official-2.bin.18.json.1
ta1-theia-3-e5-official-2.bin.18.json.2
ta1-theia-3-e5-official-2.bin.19.json
ta1-theia-3-e5-official-2.bin.19.json.1
ta1-theia-3-e5-official-2.bin.19.json.2
ta1-theia-3-e5-official-2.bin.20.json
ta1-theia-3-e5-official-2.bin.20.json.1
ta1-theia-3-e5-official-2.bin.20.json.2
ta1-theia-3-e5-official-2.bin.21.json
ta1-theia-3-e5-official-2.bin.21.json.1
ta1-theia-3-e5-official-2.bin.21.json.2
ta1-theia-3-e5-official-2.bin.22.json
ta1-theia-3-e5-official-2.bin.22.json.1
ta1-theia-3-e5-official-2.bin.22.json.2
ta1-theia-3-e5-official-2.bin.23.json
ta1-theia-3-e5-official-2.bin.23.json.1
ta1-theia-3-e5-official-2.bin.23.json.2
ta1-theia-3-e5-official-2.bin.24.json
ta1-theia-3-e5-official-2.bin.24.json.1
ta1-theia-3-e5-official-2.bin.24.json.2
ta1-theia-3-e5-official-2.bin.25.json
ta1-theia-3-e5-official-2.bin.25.json.1
ta1-theia-3-e5-official-2.bin.25.json.2
ta1-theia-3-e5-official-2.bin.26.json
ta1-theia-3-e5-official-2.bin.26.json.1
ta1-theia-3-e5-official-2.bin.26.json.2
ta1-theia-3-e5-official-2.bin.27.json
ta1-theia-3-e5-official-2.bin.27.json.1
ta1-theia-3-e5-official-2.bin.27.json.2
ta1-theia-3-e5-official-2.bin.28.json
ta1-theia-3-e5-official-2.bin.28.json.1
ta1-theia-3-e5-official-2.bin.28.json.2
ta1-theia-3-e5-official-2.bin.29.json
ta1-theia-3-e5-official-2.bin.29.json.1
""".split()

# 保留的边类型（11 种）
INCLUDE_EDGE_TYPE = {
    'EVENT_READ',
    'EVENT_WRITE',
    'EVENT_OPEN',
    'EVENT_EXECUTE',
    'EVENT_CLONE',           # THEIA E5 用 CLONE 而非 FORK
    'EVENT_CONNECT',
    'EVENT_SENDTO',
    'EVENT_RECVFROM',
    'EVENT_SENDMSG',
    'EVENT_RECVMSG',
    'EVENT_UNLINK',
}

# 反转的边（让边方向 = 数据流向）
EDGE_REVERSED = {
    'EVENT_READ',
    'EVENT_RECVFROM',
    'EVENT_RECVMSG',
    'EVENT_OPEN',
    'EVENT_EXECUTE',   # file → process
}


# ============================================================================
# 辅助
# ============================================================================

def norm_uuid(u):
    """THEIA E5 UUID 大部分大写，统一转小写。"""
    return u.lower() if isinstance(u, str) else u


def unpack_dict_str(v):
    if isinstance(v, dict):
        return v.get('string', '')
    return v if v is not None else ''


def unpack_dict_int(v):
    if isinstance(v, dict):
        return v.get('int', '')
    return v if v is not None else ''


def get_uuid(ref):
    """从 {"com.bbn.tc.schema.avro.cdm20.UUID": "..."} 取 UUID 并 lower。"""
    if isinstance(ref, dict):
        for v in ref.values():
            if isinstance(v, str):
                return norm_uuid(v)
    return ''


def list_input_files(input_dir, override=None):
    if override:
        files = [os.path.join(input_dir, f) for f in override]
    else:
        files = [os.path.join(input_dir, f) for f in DEFAULT_FILE_LIST]
    return [f for f in files if os.path.exists(f)]


# ============================================================================
# 单遍提取（THEIA E5 Subject 信息完整，无需两遍）
# ============================================================================

def extract_all(input_files, output_dir, batch_size_files=3):
    """
    单遍扫描所有文件，同时完成：
      1. 从 Subject/File/NetFlowObject 创建 uuid2name
         (Subject.path 100% / FileObject.filename 88.6% / NetFlow 自带四元组)
      2. 从 Subject.cmdLine 收集进程 cmdLine（用于 CLONE 回填）
      3. 提取边并按 batch_size_files 个文件 flush 一个 edges_part_*.pkl

    注意：Subject 记录在 CDM 日志中按时间顺序出现，可能晚于其某些 Event。
          但根据实测：23.6M 行下，绝大多数 Event 的 subject UUID 已在 Subject 表中。
          少量提前出现的 Event 会被 skipped_no_node 统计。
    """
    uuid2name = {}        # uuid (lower) → [type, name]
    uuid_cmdline = {}     # uuid → cmdLine (from Subject.cmdLine.string)
    netflow_info = {}     # uuid → (la, lp, ra, rp)

    edge_type_count = Counter()
    skipped_no_node = 0
    skipped_filtered = 0

    cur_batch = []
    part_idx = 0
    files_in_batch = 0
    edges_part_paths = []

    def flush_batch():
        nonlocal cur_batch, part_idx, files_in_batch
        if not cur_batch:
            return
        path = os.path.join(output_dir, f'edges_part_{part_idx:03d}.pkl')
        with open(path, 'wb') as f:
            pickle.dump(cur_batch, f)
        edges_part_paths.append(path)
        print(f"  [flush] edges_part_{part_idx:03d}.pkl ({len(cur_batch):,} 条)")
        cur_batch = []
        part_idx += 1
        files_in_batch = 0

    loaded_line = 0
    begin = time.time()

    for vidx, vp in enumerate(input_files):
        print(f"  处理 [{vidx+1}/{len(input_files)}]: {os.path.basename(vp)}")
        with open(vp, 'r') as fin:
            for line in fin:
                loaded_line += 1
                if loaded_line % 1000000 == 0:
                    print(f"    已扫描 {loaded_line:,} 行... uuid2name={len(uuid2name):,} edges_buf={len(cur_batch):,}")

                try:
                    record = json.loads(line)['datum']
                except Exception:
                    continue
                if not isinstance(record, dict) or not record:
                    continue
                rtype_full = next(iter(record.keys()))
                datum = record[rtype_full]
                if not isinstance(datum, dict):
                    continue
                rtype = rtype_full.rsplit('.', 1)[-1]

                # ---------- Subject ----------
                if rtype == 'Subject':
                    if datum.get('type') == 'SUBJECT_PROCESS':
                        uid = norm_uuid(datum.get('uuid'))
                        if not uid:
                            continue
                        # path 在 properties.map.path
                        props = datum.get('properties') or {}
                        pmap = props.get('map') if isinstance(props, dict) else None
                        path = pmap.get('path') if isinstance(pmap, dict) else None
                        uuid2name[uid] = ['process', path]

                        # cmdLine 是 dict
                        cmd = datum.get('cmdLine')
                        if isinstance(cmd, dict):
                            cmd_val = cmd.get('string')
                        elif isinstance(cmd, str):
                            cmd_val = cmd
                        else:
                            cmd_val = None
                        if cmd_val:
                            uuid_cmdline[uid] = cmd_val

                # ---------- FileObject ----------
                elif rtype == 'FileObject':
                    # THEIA E5 只有 FILE_OBJECT_BLOCK，但保留更宽容的过滤
                    ftype = datum.get('type', '')
                    if ftype in ('FILE_OBJECT_FILE', 'FILE_OBJECT_DIR',
                                 'FILE_OBJECT_BLOCK'):
                        uid = norm_uuid(datum.get('uuid'))
                        if not uid:
                            continue
                        base = datum.get('baseObject') or {}
                        base_props = base.get('properties') if isinstance(base, dict) else None
                        base_map = base_props.get('map') if isinstance(base_props, dict) else None
                        filename = base_map.get('filename') if isinstance(base_map, dict) else None
                        uuid2name[uid] = ['file', filename]

                # ---------- NetFlowObject ----------
                elif rtype == 'NetFlowObject':
                    uid = norm_uuid(datum.get('uuid'))
                    if not uid:
                        continue
                    la = unpack_dict_str(datum.get('localAddress'))
                    lp = unpack_dict_int(datum.get('localPort'))
                    ra = unpack_dict_str(datum.get('remoteAddress'))
                    rp = unpack_dict_int(datum.get('remotePort'))
                    uuid2name[uid] = ['netflow', f"{la}:{lp}->{ra}:{rp}"]
                    netflow_info[uid] = (str(la), str(lp), str(ra), str(rp))

                # ---------- Event ----------
                elif rtype == 'Event':
                    eventtype = datum.get('type', '')
                    if eventtype not in INCLUDE_EDGE_TYPE:
                        skipped_filtered += 1
                        continue

                    eventtime = datum.get('timestampNanos', 0)
                    raw_props = datum.get('properties')
                    pmap = raw_props.get('map', {}) if isinstance(raw_props, dict) else {}
                    if not isinstance(pmap, dict):
                        pmap = {}

                    src = get_uuid(datum.get('subject'))
                    dst = get_uuid(datum.get('predicateObject'))
                    # predicateObject2 在 THEIA E5 总是 subject 自身（冗余），忽略

                    # cmdLine 处理：
                    cmdline = None
                    if eventtype == 'EVENT_EXECUTE':
                        cmdline = pmap.get('cmdLine', None)
                    elif eventtype == 'EVENT_CLONE':
                        # 回填子进程的 Subject.cmdLine（子进程的 Subject 通常在前面出现过）
                        cmdline = uuid_cmdline.get(dst, None)

                    if not src or not dst:
                        skipped_no_node += 1
                        continue
                    if src not in uuid2name or dst not in uuid2name:
                        skipped_no_node += 1
                        continue

                    # 反转
                    if eventtype in EDGE_REVERSED:
                        edge_src, edge_dst = dst, src
                    else:
                        edge_src, edge_dst = src, dst

                    cur_batch.append((
                        eventtime,
                        eventtype,
                        edge_src,
                        uuid2name[edge_src][0],
                        uuid2name[edge_src][1],
                        edge_dst,
                        uuid2name[edge_dst][0],
                        uuid2name[edge_dst][1],
                        cmdline,
                    ))
                    edge_type_count[eventtype] += 1

                # MemoryObject / IpcObject / Principal / Host → 丢弃

        files_in_batch += 1
        if files_in_batch >= batch_size_files:
            flush_batch()

    flush_batch()

    elapsed = time.time() - begin
    print(f"\n  完成: {loaded_line:,} 行, {elapsed:.1f}s")

    type_count = Counter(t for t, _ in uuid2name.values())
    name_filled = Counter(t for t, n in uuid2name.values() if n is not None)
    print(f"  实体统计:")
    for t in ['process', 'file', 'netflow']:
        total = type_count.get(t, 0)
        filled = name_filled.get(t, 0)
        pct = filled / total * 100 if total > 0 else 0
        print(f"    {t:10s}: {total:>10,}  (有名字: {filled:,} = {pct:.1f}%)")
    print(f"  有 cmdLine 的进程: {len(uuid_cmdline):,}")

    total_edges = sum(edge_type_count.values())
    print(f"  提取的边: {total_edges:,} （分 {len(edges_part_paths)} 个 part）")
    print(f"  过滤的边(类型不在列表): {skipped_filtered:,}")
    print(f"  跳过的边(节点不存在):   {skipped_no_node:,}")
    print(f"\n  边类型分布:")
    for etype, cnt in edge_type_count.most_common():
        print(f"    {etype:30s} {cnt:>12,}")

    return uuid2name, uuid_cmdline, netflow_info, edges_part_paths


# ============================================================================
# 下游辅助
# ============================================================================

def iter_edges(output_dir):
    for path in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(path, 'rb') as f:
            edges = pickle.load(f)
        for e in edges:
            yield e


def load_all_edges(output_dir):
    edges = []
    for path in sorted(glob.glob(os.path.join(output_dir, 'edges_part_*.pkl'))):
        with open(path, 'rb') as f:
            edges.extend(pickle.load(f))
    return edges


# ============================================================================
# 主流程
# ============================================================================

def main(args):
    print("=" * 60)
    print("THEIA E5 数据提取（本地版）")
    print("=" * 60)
    print(f"\n输入目录: {args.input_dir}")
    print(f"输出目录: {args.output_dir}")
    print(f"批大小:   {args.batch_size}")
    os.makedirs(args.output_dir, exist_ok=True)

    input_files = list_input_files(args.input_dir)
    print(f"\n输入文件 ({len(input_files)} 个):")
    for f in input_files:
        print(f"  {os.path.basename(f)}")

    uuid2name, uuid_cmdline, netflow_info, parts = extract_all(
        input_files, args.output_dir, batch_size_files=args.batch_size,
    )

    print(f"\n{'='*60}\n保存元数据\n{'='*60}")

    with open(os.path.join(args.output_dir, 'uuid2name.pkl'), 'wb') as f:
        pickle.dump(uuid2name, f)
    print(f"  uuid2name.pkl ({len(uuid2name):,} 实体)")

    with open(os.path.join(args.output_dir, 'uuid_cmdline.pkl'), 'wb') as f:
        pickle.dump(uuid_cmdline, f)
    print(f"  uuid_cmdline.pkl ({len(uuid_cmdline):,} 进程)")

    with open(os.path.join(args.output_dir, 'netflow_info.pkl'), 'wb') as f:
        pickle.dump(netflow_info, f)
    print(f"  netflow_info.pkl ({len(netflow_info):,})")

    csv_path = os.path.join(args.output_dir, 'edges.csv')
    with open(csv_path, 'w') as f:
        f.write('timestamp,event_type,src_uuid,src_type,src_name,dst_uuid,dst_type,dst_name,cmdline\n')
        cnt = 0
        for row in iter_edges(args.output_dir):
            fields = [str(x) if x is not None else '' for x in row]
            fields = [field.replace(',', ';') for field in fields]
            f.write(','.join(fields) + '\n')
            cnt += 1
            if cnt >= 100000:
                break
    print(f"  edges.csv (前 10 万条)")

    print(f"\n{'='*60}\n完成\n{'='*60}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="THEIA E5 Data Extractor")
    parser.add_argument("--input_dir", type=str,
                        default="/mnt/disk/darpa/cch_refine/theia_e5_json")
    parser.add_argument("--output_dir", type=str,
                        default="/mnt/disk/darpa/theia_e5_output")
    parser.add_argument("--batch_size", type=int, default=3,
                        help="每多少个输入文件 flush 一次 edges_part_*.pkl")
    args = parser.parse_args()
    main(args)
