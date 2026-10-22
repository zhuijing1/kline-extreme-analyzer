"""
数据层
======
行情获取与标的搜索。

设计要点：
  * pandas / numpy / akshare / baostock 全部 **函数内延迟导入**，
    保证 GUI 启动阶段不触发任何重型 import（akshare 单独就要 6 秒）。
  * 股票列表走本地 JSON 缓存（1 天 TTL），过期时才在线刷新，
    且刷新必须发生在后台线程，绝不阻塞界面。
  * A 股 5 分钟 K 线以 Baostock 为主源（稳定、导入快），AkShare 为备源。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("KlineData")

# ── 常量 ────────────────────────────────────────────────────────────────────
A_STOCK_BARS = 48          # A股每日 5 分钟 K 线根数（上午24 + 下午24）
A_MORNING_BARS = 24

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STOCK_CACHE_FILE = os.path.join(_BASE_DIR, ".stock_list_cache.json")
_STOCK_CACHE_TTL = 86400   # 缓存有效期 1 天

_stock_list_cache: list[dict[str, str]] | None = None


def build_stock_bar_labels() -> list[str]:
    """生成 A 股 48 根 5 分钟 K 线的时间标签（收盘时刻）。"""
    labels: list[str] = []
    for i in range(A_MORNING_BARS):
        h, m = divmod(9 * 60 + 35 + i * 5, 60)
        labels.append(f"{h:02d}:{m:02d}")
    for i in range(A_MORNING_BARS):
        h, m = divmod(13 * 60 + 5 + i * 5, 60)
        labels.append(f"{h:02d}:{m:02d}")
    return labels


# 全局时间标签（每根 K 线的收盘时刻）
BAR_TIME_LABELS: list[str] = build_stock_bar_labels()

# 常见指数（在线列表拉取失败时的兜底）
_INDEX_LIST: list[dict[str, str]] = [
    {"code": "000001", "name": "上证指数"}, {"code": "399001", "name": "深证成指"},
    {"code": "399006", "name": "创业板指"}, {"code": "000300", "name": "沪深300"},
    {"code": "000016", "name": "上证50"}, {"code": "000905", "name": "中证500"},
    {"code": "000852", "name": "中证1000"}, {"code": "399300", "name": "国证300"},
]


# ═══════════════════════════════════════════════════════════════════════════
# 股票列表：缓存 / 搜索
# ═══════════════════════════════════════════════════════════════════════════

def _read_cache_file() -> tuple[list[dict[str, str]] | None, bool]:
    """读取本地缓存，返回 (列表, 是否仍在有效期内)。"""
    try:
        if not os.path.exists(_STOCK_CACHE_FILE):
            return None, False
        fresh = (time.time() - os.path.getmtime(_STOCK_CACHE_FILE)) < _STOCK_CACHE_TTL
        with open(_STOCK_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list) and raw:
            return raw, fresh
    except Exception as e:
        logger.warning("读取股票列表缓存失败: %s", e)
    return None, False


def _write_cache_file(items: list[dict[str, str]]) -> None:
    try:
        with open(_STOCK_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception as e:
        logger.warning("写入股票列表缓存失败: %s", e)


def _fetch_stock_list_online() -> list[dict[str, str]] | None:
    """在线拉取全市场股票列表（需要 akshare，约 6 秒导入开销）。"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        online = [
            {"code": str(r.get("code", "")).strip(), "name": str(r.get("name", "")).strip()}
            for _, r in df.iterrows() if r.get("code") and r.get("name")
        ]
        existing = {s["code"] for s in online}
        for idx in _INDEX_LIST:
            if idx["code"] not in existing:
                online.insert(0, idx)
        logger.info("在线获取股票列表: %d 只", len(online))
        return online
    except Exception as e:
        logger.error("在线获取股票列表失败: %s", e)
        return None


def load_stock_list(force_refresh: bool = False) -> list[dict[str, str]]:
    """
    加载股票列表：缓存优先，缓存过期时才联网刷新。

    注意：本函数会触发 akshare 的导入（约 6 秒），
    因此只应在 **后台线程** 中调用，或在缓存有效时调用。
    """
    global _stock_list_cache
    if _stock_list_cache is not None and not force_refresh:
        return _stock_list_cache

    cached, fresh = _read_cache_file()
    if cached is not None and fresh and not force_refresh:
        _stock_list_cache = cached
        return cached

    online = _fetch_stock_list_online()
    if online:
        _write_cache_file(online)
        _stock_list_cache = online
    else:
        _stock_list_cache = cached if cached is not None else list(_INDEX_LIST)
        if cached is None:
            logger.warning("股票列表仅使用指数兜底")
    return _stock_list_cache


def is_stock_cache_fresh() -> bool:
    """本地缓存是否存在且未过期（用于决定要不要后台刷新）。"""
    if _stock_list_cache is not None:
        return True
    _, fresh = _read_cache_file()
    return fresh


def preload_stock_cache() -> None:
    """后台预热股票列表（缓存过期时才真正联网）。"""
    try:
        load_stock_list()
        logger.info("股票列表预加载完成")
    except Exception as e:
        logger.warning("股票列表预加载失败: %s", e)


def search_stocks(keyword: str) -> list[dict[str, str]]:
    """按代码或名称搜索股票。"""
    kw = keyword.strip()
    if not kw:
        return []

    digits = "".join(c for c in kw if c.isdigit())
    kw_upper = kw.upper()
    all_stocks = load_stock_list()

    # 纯 6 位代码：直接命中，无需遍历
    if len(digits) == 6 and kw == digits:
        for s in all_stocks:
            if s["code"] == digits:
                return [s]
        return [{"code": digits, "name": "（可直接分析）"}]

    results: list[dict[str, str]] = []
    for s in all_stocks:
        if kw_upper in s["code"].upper() or kw_upper in s["name"].upper():
            results.append(s)
            if len(results) >= 50:
                break
    return results


def normalize_symbol(symbol: str) -> str:
    """规整为 6 位数字代码。"""
    raw = (symbol or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    return digits[-6:] if len(digits) >= 6 else digits


def _to_baostock_code(code: str) -> str:
    """转换为 Baostock 需要的 sh./sz. 前缀格式。"""
    if code.startswith(("6", "5", "9")):
        return f"sh.{code}"
    if len(code) == 6:
        return f"sz.{code}"
    return code


# ═══════════════════════════════════════════════════════════════════════════
# K 线数据
# ═══════════════════════════════════════════════════════════════════════════

def fetch_stock_5min(symbol: str, start_date: str, end_date: str) -> Any:
    """
    获取 A 股 5 分钟 K 线。Baostock 优先，AkShare 备用（3 次重试）。

    返回 DataFrame: date, high, low, open, close, volume（按时间升序）
    """
    code = normalize_symbol(symbol)
    if not code:
        raise ValueError("股票代码无效")

    logger.info("获取 %s 的5分钟K线 (%s ~ %s)...", code, start_date, end_date)

    # ── 数据源1：Baostock ──
    try:
        import baostock as bs
        import pandas as pd

        bs_code = _to_baostock_code(code)
        bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, "date,time,open,high,low,close,volume",
                start_date=start_date, end_date=end_date,
                frequency="5", adjustflag="2",
            )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        finally:
            bs.logout()

        if rows:
            df = pd.DataFrame(rows, columns=[
                "date", "time", "open", "high", "low", "close", "volume"])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            # Baostock 的 time 形如 20250102093500000，取前14位按秒解析
            df["datetime"] = pd.to_datetime(
                df["time"].str.slice(0, 14), format="%Y%m%d%H%M%S", errors="coerce")
            df["date"] = df["datetime"].dt.date
            df = (df.dropna(subset=["high", "low"])
                    .sort_values("datetime").reset_index(drop=True))
            if not df.empty:
                logger.info("Baostock 获取到 %d 条数据", len(df))
                return df
    except Exception as e:
        logger.warning("Baostock 获取失败: %s, 尝试 AkShare", e)

    # ── 数据源2：AkShare（3 次重试）──
    import pandas as pd

    for attempt in range(3):
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist_min_em(
                symbol=code, period="5",
                start_date=f"{start_date} 09:30:00",
                end_date=f"{end_date} 15:00:00",
                adjust="qfq",
            )
            if df is not None and not df.empty:
                col_map = {}
                for col in df.columns:
                    c = str(col).strip()
                    if c == "时间":
                        col_map[col] = "datetime"
                    elif c == "开盘":
                        col_map[col] = "open"
                    elif c == "收盘":
                        col_map[col] = "close"
                    elif c == "最高":
                        col_map[col] = "high"
                    elif c == "最低":
                        col_map[col] = "low"
                    elif c == "成交量":
                        col_map[col] = "volume"
                df = df.rename(columns=col_map)
                df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
                df = df.dropna(subset=["datetime"])
                df["date"] = df["datetime"].dt.date
                for col in ["open", "high", "low", "close"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = (df.dropna(subset=["high", "low"])
                        .sort_values("datetime").reset_index(drop=True))
                logger.info("AkShare 获取到 %d 条数据 (第%d次尝试)", len(df), attempt + 1)
                return df
        except Exception as e:
            logger.warning("AkShare 第%d次尝试失败: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2)

    raise ValueError(f"无法获取 {code} 的5分钟K线数据")


def fetch_stock_daily(symbol: str, start_date: str, end_date: str) -> Any:
    """获取 A 股日线数据。Baostock 优先，AkShare 备用。

    返回 DataFrame: date, open, high, low, close, volume, pctChg
    """
    code = normalize_symbol(symbol)
    if not code:
        raise ValueError("股票代码无效")

    logger.info("获取 %s 的日线数据 (%s ~ %s)...", code, start_date, end_date)

    # ── 数据源1：Baostock ──
    try:
        import baostock as bs
        import pandas as pd

        bs_code = _to_baostock_code(code)
        bs.login()
        try:
            rs = bs.query_history_k_data_plus(
                bs_code, "date,open,high,low,close,volume,pctChg",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2",
            )
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        finally:
            bs.logout()

        if rows:
            df = pd.DataFrame(rows, columns=[
                "date", "open", "high", "low", "close", "volume", "pctChg"])
            for col in ["open", "high", "low", "close", "volume", "pctChg"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["date"] = pd.to_datetime(df["date"]).dt.date
            df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            logger.info("Baostock 日线获取到 %d 条", len(df))
            return df
    except Exception as e:
        logger.warning("Baostock 日线获取失败: %s, 尝试 AkShare", e)

    # ── 数据源2：AkShare ──
    import pandas as pd

    for attempt in range(3):
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adjust="qfq",
            )
            if df is not None and not df.empty:
                col_map = {}
                for col in df.columns:
                    c = str(col).strip()
                    if c == "日期":
                        col_map[col] = "date"
                    elif c == "开盘":
                        col_map[col] = "open"
                    elif c == "收盘":
                        col_map[col] = "close"
                    elif c == "最高":
                        col_map[col] = "high"
                    elif c == "最低":
                        col_map[col] = "low"
                    elif c == "成交量":
                        col_map[col] = "volume"
                    elif c == "涨跌幅":
                        col_map[col] = "pctChg"
                df = df.rename(columns=col_map)
                df["date"] = pd.to_datetime(df["date"]).dt.date
                for col in ["open", "high", "low", "close", "volume", "pctChg"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
                logger.info("AkShare 日线获取到 %d 条 (第%d次)", len(df), attempt + 1)
                return df
        except Exception as e:
            logger.warning("AkShare 日线第%d次尝试失败: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(2)

    raise ValueError(f"无法获取 {code} 的日线数据")
