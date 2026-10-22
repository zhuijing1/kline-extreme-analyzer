"""
测试脚本（合并版）
==================
用法:
    python test_all.py          # 仅离线单元测试（不联网，秒级完成）
    python test_all.py --online # 追加联网测试（搜索 / 拉取 5 分钟 K 线 / 日线）

离线部分直接 import kline_stats，不依赖 PyQt6，因此可在任意环境运行。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd

from kline_data import A_STOCK_BARS, BAR_TIME_LABELS, search_stocks
from kline_stats import analyze_daily_extremes, compute_daily_updown_stats, compute_statistics

PASS = "✅"
FAIL = "❌"

# 部分 Windows 控制台（GBK）无法输出 emoji，自动降级为纯文本标记
try:
    sys.stdout.encoding and "✅".encode(sys.stdout.encoding)
except Exception:
    PASS, FAIL = "[OK]", "[FAIL]"


def _make_day(day: int, high_bar: int, low_bar: int, n_bars: int = A_STOCK_BARS) -> list[dict]:
    """构造一天的 5 分钟 K 线：最高价出现在 high_bar，最低价出现在 low_bar。"""
    rng = np.random.default_rng(42 + day)
    base = 10.0
    bars = []
    for i in range(1, n_bars + 1):
        h = base + 5.0 if i == high_bar else base + rng.uniform(0, 2)
        l = base - 5.0 if i == low_bar else base - rng.uniform(0, 2)
        o = base + rng.uniform(-1, 1)
        c = base + rng.uniform(-1, 1)
        bars.append({
            "date": pd.Timestamp(f"2024-01-{day:02d}").date(),
            "open": o, "high": max(h, o, c), "low": min(l, o, c), "close": c,
        })
    return bars


def test_extremes() -> None:
    """极值位置识别 + 累积概率。"""
    print("=" * 60)
    print("测试1: 极值位置识别")
    print("=" * 60)

    df = pd.DataFrame(
        _make_day(1, high_bar=3, low_bar=10)
        + _make_day(2, high_bar=1, low_bar=20)
        + _make_day(3, high_bar=15, low_bar=5)
    )
    daily = analyze_daily_extremes(df)
    print(daily[["date", "day_high", "day_low", "high_bar", "low_bar", "ext_bar"]].to_string(index=False))

    assert daily.iloc[0]["ext_bar"] == 3, "第1天极值应在第3根"
    assert daily.iloc[1]["ext_bar"] == 1, "第2天极值应在第1根"
    assert daily.iloc[2]["ext_bar"] == 5, "第3天极值应在第5根"
    assert daily.iloc[0]["high_bar"] == 3 and daily.iloc[0]["low_bar"] == 10
    print(f"{PASS} 极值位置识别正确")

    print("-" * 60)
    print("测试2: 累积概率计算")
    print("-" * 60)
    stats = compute_statistics(daily, [1, 7, 18], "ext", A_STOCK_BARS, labels=BAR_TIME_LABELS)
    for cb in (1, 7, 18):
        cp = stats["check_points"][cb]
        print(f"  第{cb:2d}根({BAR_TIME_LABELS[cb - 1]}): {cp['prob']:.2%} ({cp['count']}/{cp['total']})")

    assert abs(stats["check_points"][1]["prob"] - 1 / 3) < 0.01, "第1根概率应为 1/3"
    assert abs(stats["check_points"][7]["prob"] - 1.0) < 0.01, "第7根概率应为 100%"
    assert abs(stats["check_points"][18]["prob"] - 1.0) < 0.01, "第18根概率应为 100%"
    assert stats["mean_val"] == (3 + 1 + 5) / 3
    print(f"{PASS} 累积概率计算正确（均值 {stats['mean_val']:.2f} 根）")

    print("-" * 60)
    print("测试3: 三种分析模式")
    print("-" * 60)
    for mode in ("ext", "high", "low"):
        s = compute_statistics(daily, [18], mode, A_STOCK_BARS)
        cp = s["check_points"][18]
        print(f"  {s['mode_label']:16s} 第18根: {cp['prob']:.2%}")
    s_high = compute_statistics(daily, [18], "high", A_STOCK_BARS)
    s_low = compute_statistics(daily, [18], "low", A_STOCK_BARS)
    # 最高价出现在第3/1/15根 → 全部 ≤18；最低价出现在第10/20/5根 → 第2天不满足
    assert s_high["check_points"][18]["prob"] == 1.0, "最高价全部在第18根前出现"
    assert abs(s_low["check_points"][18]["prob"] - 2 / 3) < 0.01, "最低价第18根概率应为 2/3"
    print(f"{PASS} 三种模式均可计算")

    print("-" * 60)
    print("测试4: 边界情况")
    print("-" * 60)
    assert compute_statistics(pd.DataFrame(), [1, 7, 18]) == {}, "空数据应返回空字典"
    print(f"{PASS} 空数据处理正确")

    same = analyze_daily_extremes(pd.DataFrame(_make_day(1, high_bar=5, low_bar=5)))
    assert same.iloc[0]["ext_bar"] == 5 and same.iloc[0]["high_bar"] == 5
    print(f"{PASS} 同一根K线同时出现最高/最低处理正确")


def test_daily_updown() -> None:
    """日线涨跌统计。"""
    print("=" * 60)
    print("测试5: 日线涨跌统计")
    print("=" * 60)

    closes = [10.0, 11.0, 10.5, 10.5, 12.0, 11.5]
    df = pd.DataFrame({
        "date": [pd.Timestamp(f"2024-01-{i + 1:02d}").date() for i in range(len(closes))],
        "close": closes,
    })
    stats = compute_daily_updown_stats(df)
    assert stats["up_count"] == 2 and stats["down_count"] == 2 and stats["flat_count"] == 1, \
        f"涨/跌/平计数错误: {stats['up_count']}/{stats['down_count']}/{stats['flat_count']}"
    assert stats["max_up_streak"] == 1 and stats["max_down_streak"] == 1
    print(f"  涨 {stats['up_count']} / 跌 {stats['down_count']} / 平 {stats['flat_count']}"
          f"  上涨概率 {stats['up_prob']:.2%}")
    print(f"{PASS} 日线涨跌统计正确")


def test_online() -> None:
    """联网测试：搜索 + 5 分钟 K 线 + 日线。"""
    print("=" * 60)
    print("测试6: 联网 — 标的搜索")
    print("=" * 60)
    import time
    t0 = time.time()
    r = search_stocks("600519")
    print(f"  耗时 {time.time() - t0:.2f}s，结果: {r[:2]}")
    assert r and r[0]["code"] == "600519", "应能搜到 600519"

    print("-" * 60)
    print("测试7: 联网 — 5 分钟 K 线 + 极值统计")
    print("-" * 60)
    from kline_data import fetch_stock_5min
    df = fetch_stock_5min("600519", "2025-06-01", "2025-06-30")
    print(f"  {len(df)} 条K线, {df['date'].nunique()} 个交易日")
    daily = analyze_daily_extremes(df)
    stats = compute_statistics(daily, [1, 7, 18], "ext", A_STOCK_BARS, labels=BAR_TIME_LABELS)
    for cb in (1, 7, 18):
        cp = stats["check_points"][cb]
        print(f"  第{cb:2d}根: {cp['prob']:.2%} ({cp['count']}/{cp['total']})")
    assert stats["total_days"] > 0
    print(f"{PASS} 联网统计完成")

    print("-" * 60)
    print("测试8: 联网 — 日线涨跌统计")
    print("-" * 60)
    from kline_data import fetch_stock_daily
    daily_df = fetch_stock_daily("600519", "2025-01-01", "2025-06-30")
    st = compute_daily_updown_stats(daily_df)
    print(f"  {st['total_days']} 个交易日, 上涨概率 {st['up_prob']:.2%}")
    print(f"{PASS} 日线统计完成")


def main() -> int:
    print("K线极值统计工具 — 测试套件\n")
    test_extremes()
    test_daily_updown()

    if "--online" in sys.argv:
        print()
        test_online()
    else:
        print("\n（跳过联网测试，加 --online 参数可运行）")

    print("\n" + "=" * 60)
    print("全部测试通过 ✅")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
