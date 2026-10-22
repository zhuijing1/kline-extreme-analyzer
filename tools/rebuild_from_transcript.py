#!/usr/bin/env python3
"""
从对话记录(jsonl)中重建 kline_extreme_analyzer.py 及其他配套文件。
按顺序重放所有 Write/Edit function_call，还原最终版本。
"""

import json
import os
import sys

JSONL = r"C:\Users\Administrator\.workbuddy-ai\projects\E-WorkBuddyData-workspaces-2026-07-04-14-44-30\50d84871-49f9-4baf-a2aa-527cf4438cc8.jsonl"
# 程序目录（2026-10-22 迁入 DSH 工作区）
OUT_DIR = r"D:\dsh\dsh\K线极值统计工具"

# 需要重建的目标文件（全部）
# 注：部分文件在后续重构中已被移除，下面保留旧名单是为了在
#     回放历史 jsonl 时仍能找回这些文件。
TARGETS = {
    "kline_extreme_analyzer.py",
    "data_sources.json",
    "data_source_dialog.py",
    "data_source_manager.py",
    "数据源管理.py",
    "创建图标.py",
    "测试数据源GUI.py",
    "创建快捷方式.py",
    "更新图标.ps1",
    "overview.md",
    "使用说明.md",
    "数据源管理使用指南.md",
    "test_date_range.py",
    "test_logic.py",
    "test_quick.py",
    "test_search.py",
    "test_v2.py",
    "2026-07-04.md",  # memory
    "MEMORY.md",      # memory
    "rebuild_from_transcript.py",  # 排除自身
}
TARGETS.discard("rebuild_from_transcript.py")

# bat文件也重建
BAT_TARGETS = {"启动程序.bat", "数据源管理.bat"}

# 记忆文件放工作区的 memory 子目录（绝对路径，os.path.join 会自动忽略前面的 OUT_DIR）
_MEMORY_DIR = r"C:\Users\Administrator\WorkBuddy\2026-07-04-14-44-30\.workbuddy\memory"
SUBDIR = {
    "2026-07-04.md": _MEMORY_DIR,
    "MEMORY.md": _MEMORY_DIR,
}

def basename_match(fp: str, names: set) -> str | None:
    b = os.path.basename(fp.replace("\\", "/"))
    return b if b in names else None

def main():
    files: dict[str, str] = {}   # 文件名 -> 当前内容
    ops: dict[str, list] = {}    # 文件名 -> 操作记录

    with open(JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("type") != "function_call":
                continue

            name = entry.get("name", "")
            if name not in ("Write", "Edit"):
                continue
            try:
                args = json.loads(entry.get("arguments") or "{}")
            except Exception:
                continue
            fp = str(args.get("file_path", ""))
            base = basename_match(fp, TARGETS | BAT_TARGETS)
            if not base:
                continue

            ops.setdefault(base, [])
            if name == "Write":
                content = args.get("content", "")
                files[base] = content
                ops[base].append(("Write", len(content)))
            else:  # Edit
                old = args.get("old_string", "")
                new = args.get("new_string", "")
                replace_all = bool(args.get("replace_all", False))
                if base not in files:
                    ops[base].append(("Edit-SKIP(no prior Write)", 0))
                    continue
                cur = files[base]
                if replace_all:
                    if old not in cur:
                        ops[base].append(("Edit-WARN(not found, replace_all)", len(cur)))
                    cur = cur.replace(old, new)
                else:
                    cnt = cur.count(old)
                    if cnt == 0:
                        ops[base].append((f"Edit-WARN(not found): {old[:60]!r}", len(cur)))
                    elif cnt > 1:
                        ops[base].append((f"Edit-WARN({cnt} matches)", len(cur)))
                        cur = cur.replace(old, new, 1)
                    else:
                        cur = cur.replace(old, new, 1)
                        ops[base].append(("Edit", len(cur)))
                files[base] = cur

    if not files:
        print("未找到任何目标文件的写操作")
        return 1

    total = 0
    for base in sorted(files.keys()):
        content = files[base]
        subdir = SUBDIR.get(base)
        if subdir:
            out_dir = os.path.join(OUT_DIR, subdir)
            os.makedirs(out_dir, exist_ok=True)
        else:
            out_dir = OUT_DIR
        out_path = os.path.join(out_dir, base)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        n_lines = content.count("\n") + 1
        rel = os.path.join(subdir, base) if subdir else base
        print(f"[OK] {rel}: {n_lines} 行, {len(content)} 字符, {len(ops.get(base, []))} 个操作")
        for op, size in ops.get(base, []):
            print(f"      - {op} -> {size}")
        total += 1

    print(f"\n共重建 {total} 个文件到: {OUT_DIR}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
