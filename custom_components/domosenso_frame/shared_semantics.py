"""Portable assessment/operability contract, pinned to the shared browser fixtures.

Operability only describes state. Permission and action support remain independent.
"""
import math


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def assessment(value, kind, limits):
    if not _finite(value) or not isinstance(limits, dict):
        return None
    keys = ('low', 'high') if kind == 'range' else ('fair', 'poor')
    if kind not in ('range', 'rising', 'falling') or any(not _finite(limits.get(key)) for key in keys):
        return None
    first, second = (limits[key] for key in keys)
    if (kind in ('range', 'rising') and first > second) or (kind == 'falling' and first < second):
        return None
    if kind == 'range':
        return {'level': 'poor', 'side': 'low' if value < first else 'high'} if value < first or value > second else {'level': 'good'}
    if kind == 'rising':
        return {'level': 'poor' if value >= second else 'fair' if value >= first else 'good'}
    return {'level': 'poor' if value < second else 'fair' if value < first else 'good'}


def operability(domain, state):
    available = state is not None and state != 'unavailable'
    return {'operable': available, 'shown': 'value' if available and state != 'unknown' else 'none'}
