"""
统计引擎
========
纯计算模块：不依赖 PyQt6 / pyqtgraph，pandas 与 numpy 均在函数内部延迟导入。

这样做的目的：
  1. GUI 启动时不需要加载 pandas(≈4s) / numpy(≈2s)，程序秒开；
  2. 本模块可被测试脚本直接 import，也可在后台线程中独立调用。
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

logger = logging.getLogger("KlineStats")

# ── 分析模式 ────────────────────────────────────────────────────────────────
ANALYSIS_MODES: dict[str, str] = {
    "ext": "极值(最高或最低)",
    "high": "仅最高价",
    "low": "仅最低价",
}

# 模式 → 每日明细表中的 K 线序号列
MODE_BAR_COLUMN: dict[str, str] = {
    "ext": "ext_bar",
    "high": "high_bar",
    "low": "low_bar",
}


# ═══════════════════════════════════════════════════════════════════════════
# 日内极值分析
# ═══════════════════════════════════════════════════════════════════════════

def analyze_daily_extremes(df) -> Any:
    """
    分析每个交易日最高价 / 最低价在 5 分钟 K 线中首次出现的位置。

    参数:
        df: 含 date / high / low 列的 DataFrame（按时间升序）

    返回:
        DataFrame: date, day_high, day_low, high_bar, low_bar, ext_bar, total_bars
        ext_bar = min(high_bar, low_bar)，即任一极值首次出现的 K 线序号。
    """
    import pandas as pd

    results: list[dict[str, Any]] = []
    for day, group in df.groupby("date"):
        group = group.reset_index(drop=True)
        if len(group) < 2:
            continue
        bars = group.to_dict("records")

        highs = [b["high"] for b in bars if pd.notna(b["high"])]
        lows = [b["low"] for b in bars if pd.notna(b["low"])]
        if not highs or not lows:
            continue

        day_high = max(highs)
        day_low = min(lows)

        high_bar: int | None = None
        low_bar: int | None = None
        for i, b in enumerate(bars):
            if high_bar is None and pd.notna(b["high"]) and abs(b["high"] - day_high) < 1e-6:
                high_bar = i + 1
            if low_bar is None and pd.notna(b["low"]) and abs(b["low"] - day_low) < 1e-6:
                low_bar = i + 1
            if high_bar is not None and low_bar is not None:
                break

        if high_bar is None or low_bar is None:
            continue

        results.append({
            "date": day,
            "day_high": day_high,
            "day_low": day_low,
            "high_bar": high_bar,
            "low_bar": low_bar,
            "ext_bar": min(high_bar, low_bar),
            "total_bars": len(group),
        })

    return pd.DataFrame(results)


def compute_statistics(
    daily_df,
    check_bars: Sequence[int],
    mode: str = "ext",
    max_bars: int = 48,
    labels: Sequence[str] | None = None,
) -> dict[str, Any]:
    """
    计算指定检查点 K 线序号处的累积概率。

    参数:
        daily_df: analyze_daily_extremes 的输出
        check_bars: 检查点 K 线序号，如 [1, 7, 18]
        mode: "ext"(极值) / "high"(仅最高) / "low"(仅最低)
        max_bars: 每日最大 K 线数（图表 x 轴长度）
        labels: 每根 K 线的时间标签（用于图表 / 报告）
    """
    import numpy as np

    total = len(daily_df)
    if total == 0:
        return {}

    bar_col = MODE_BAR_COLUMN.get(mode, "ext_bar")
    bars = daily_df[bar_col].values

    # 检查点累积概率：P(极值在前 N 根内已出现)
    check_points: dict[int, dict[str, Any]] = {}
    for cb in sorted(set(check_bars)):
        if cb < 1 or cb > max_bars:
            continue
        count = int(np.sum(bars <= cb))
        check_points[cb] = {
            "bar": cb,
            "count": count,
            "total": total,
            "prob": count / total,
        }

    # 全量频率分布（1..max_bars）
    distribution = np.zeros(max_bars, dtype=int)
    for b in bars:
        idx = int(b) - 1
        if 0 <= idx < max_bars:
            distribution[idx] += 1

    cumulative = (np.cumsum(distribution) / total).tolist()

    # 方向定型时点：累积概率首次达到 50% / 80% / 90% 的 K 线序号
    def _first_bar_reaching(threshold: float) -> int | None:
        for i, c in enumerate(cumulative):
            if c >= threshold:
                return i + 1
        return None

    percentiles = {
        "T50": _first_bar_reaching(0.50),
        "T80": _first_bar_reaching(0.80),
        "T90": _first_bar_reaching(0.90),
    }

    if labels is None:
        labels = []
    return {
        "total_days": total,
        "check_points": check_points,
        "distribution": distribution.tolist(),
        "cumulative": cumulative,
        "labels": list(labels)[:max_bars],
        "mean_val": float(np.mean(bars)),
        "median_val": float(np.median(bars)),
        "min_val": int(np.min(bars)),
        "max_val": int(np.max(bars)),
        "std_val": float(np.std(bars)),
        "max_bars": max_bars,
        "mode": mode,
        "mode_label": ANALYSIS_MODES.get(mode, mode),
        "percentiles": percentiles,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 日线涨跌统计
# ═══════════════════════════════════════════════════════════════════════════

def compute_daily_updown_stats(df) -> dict[str, Any]:
    """
    计算日线涨跌统计。

    参数:
        df: 含 date / close 列的 DataFrame（按日期排序）

    返回:
        dict: 涨/跌/平次数与概率、最大连涨/连跌、按星期统计、每日明细
    """
    import pandas as pd

    if df is None or len(df) < 2:
        return {}

    df = df.sort_values("date").reset_index(drop=True).copy()
    df["prev_close"] = df["close"].shift(1)
    df = df.dropna(subset=["prev_close"]).reset_index(drop=True)

    df["change"] = df["close"] - df["prev_close"]
    df["pct"] = df["change"] / df["prev_close"] * 100
    df["direction"] = df["change"].apply(
        lambda x: "涨" if x > 0 else ("跌" if x < 0 else "平")
    )

    total = len(df)
    up_count = int((df["direction"] == "涨").sum())
    down_count = int((df["direction"] == "跌").sum())
    flat_count = int((df["direction"] == "平").sum())

    # 连续涨跌
    max_up_streak = 0
    max_down_streak = 0
    current = 0
    prev_dir = None
    for d in df["direction"]:
        if d == prev_dir and d != "平":
            current += 1
        else:
            current = 0 if d == "平" else 1
        if d == "涨":
            max_up_streak = max(max_up_streak, current)
        elif d == "跌":
            max_down_streak = max(max_down_streak, current)
        prev_dir = d

    # 按星期统计
    weekdays_cn = ["一", "二", "三", "四", "五", "六", "日"]
    weekday_stats: dict[int, dict[str, Any]] = {}
    for wd in range(7):
        subset = df[df["date"].apply(lambda d: d.weekday() == wd)]
        if len(subset):
            weekday_stats[wd] = {
                "weekday": weekdays_cn[wd],
                "total": len(subset),
                "up": int((subset["direction"] == "涨").sum()),
                "down": int((subset["direction"] == "跌").sum()),
                "up_prob": (subset["direction"] == "涨").sum() / len(subset),
            }

    return {
        "total_days": total,
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": flat_count,
        "up_prob": up_count / total,
        "down_prob": down_count / total,
        "flat_prob": flat_count / total,
        "max_up_streak": max_up_streak,
        "max_down_streak": max_down_streak,
        "avg_pct": float(df["pct"].mean()),
        "max_up_pct": float(df["pct"].max()),
        "max_down_pct": float(df["pct"].min()),
        "weekday_stats": weekday_stats,
        "daily_df": df,
        "date_range": f"{df['date'].min()} ~ {df['date'].max()}",
    }
