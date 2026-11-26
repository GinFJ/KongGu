"""Calibrate the known English timetable clock against all printed columns.

The profile includes unnumbered lunch slots, but geometry must agree across
morning, afternoon and evening before any unlabelled cell can use them.
"""
from __future__ import annotations

import functools
import numpy as np


@functools.lru_cache(maxsize=1)
def clock_minutes():
    from core.schedule_core import default_timetable
    def minutes(value):
        hour, minute = map(int, value.split(':'))
        return hour * 60 + minute
    return {int(row['period']): (minutes(row['start']), minutes(row['end']))
            for row in default_timetable().to_dict('records')}


def fit_clock_axis(columns):
    by_period = {int(c['period']): c for c in columns}
    if not all(p in by_period for p in range(1, 12)):
        return None
    times = clock_minutes()
    minutes = np.array([sum(times[p])/2 for p in range(1, 12)])
    centers = np.array([float(by_period[p]['center']) for p in range(1, 12)])
    scale, offset = np.polyfit(minutes, centers, 1)
    # Double border lines and five-minute breaks can move a detected center
    # slightly. A distorted lunch spacer fails the complete-axis agreement.
    if scale <= 0 or np.max(np.abs(centers-(minutes*scale+offset))) > scale*3:
        return None
    return float(scale), float(offset)


def midday_periods(axis, left, right):
    if axis is None:
        return []
    scale, offset = axis
    start, end = (left-offset)/scale, (right-offset)/scale
    times = clock_minutes()
    for periods in ([12], [13], [12, 13]):
        if abs(start-times[periods[0]][0]) <= 3.5 and abs(end-times[periods[-1]][1]) <= 3.5:
            return list(periods)
    return []
