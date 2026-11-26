"""Project a confirmed batch onto the actual selected calendar dates."""

from datetime import date

import pandas as pd


def selected_days(calendar, start_date, end_date, weekdays):
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (TypeError, ValueError) as error:
        raise ValueError("请选择有效的导出开始日期和结束日期。") from error
    if end < start:
        raise ValueError("导出结束日期不能早于开始日期。")
    if calendar is None or not {"date", "week", "weekday"}.issubset(calendar.columns):
        raise ValueError("所选范围缺少校历日期，无法导出。")

    normalized = pd.to_datetime(calendar["date"], errors="coerce").dt.date
    within = calendar.loc[normalized.between(start, end)].copy()
    within["date"] = normalized.loc[within.index]
    within = within[["date", "week", "weekday"]].drop_duplicates().sort_values("date")
    if (len(within) != (end - start).days + 1 or within["date"].duplicated().any()
            or within[["week", "weekday"]].duplicated().any()
            or not within["weekday"].isin(weekdays).all() or within["week"].isna().any()):
        raise ValueError("所选范围的校历日期缺失或重复，无法可靠地导出空课表。")
    return {(int(row.week), row.weekday): row.date for row in within.itertuples(index=False)}


def scoped_rows(frame, days):
    """Use the source date where present and verify it against the teaching-day key."""
    if frame is None or frame.empty:
        return frame
    dated = pd.to_datetime(frame["date"], errors="coerce").dt.date if "date" in frame else None
    if {"week", "weekday"}.issubset(frame.columns):
        mapped = pd.Series([days.get((week, weekday)) for week, weekday in zip(frame["week"], frame["weekday"])],
                           index=frame.index, dtype=object)
        if dated is not None:
            touches_range = dated.isin(days.values()) | mapped.notna()
            supplied = frame["date"].notna() & frame["date"].astype(str).str.strip().ne("")
            if (touches_range & supplied & (dated != mapped)).any():
                raise ValueError("明细中的日期与教学周、星期不符，请核实后导出。")
        dated = mapped
    if dated is None:
        raise ValueError("缺少日期或教学周定位，无法按所选日期导出明细。")
    included = dated.isin(days.values())
    selected = frame.loc[included].copy()
    selected["date"] = dated.loc[included].map(date.isoformat)
    return selected


def free_members_by_slot(slots, days, periods):
    """Never infer an all-free day from omitted or unreviewed detail rows."""
    keys = ["week", "weekday", "period"]
    if slots is None or not {*keys, "free_members"}.issubset(slots.columns):
        raise ValueError("所选日期缺少已核对的空课明细。")
    if slots.duplicated(keys).any() or slots["free_members"].isna().any():
        raise ValueError("所选日期的空课明细含重复或未核对的节次。")
    free = {(int(row.week), row.weekday, int(row.period)): set(row.free_members.split("、")) - {""}
            for row in slots.itertuples(index=False)}
    if any((week, weekday, period) not in free for week, weekday in days for period in periods):
        raise ValueError("所选日期有缺少核对数据的节次，无法导出完整空课表。")
    return free
