"""Interval labels <-> Moomoo KLType."""

INTERVALS = [
    "1m",
    "3m",
    "5m",
    "10m",
    "15m",
    "30m",
    "1 hour",
    "2 hour",
    "4 hour",
    "daily",
    "monthly",
]

_LABEL_TO_ATTR = {
    "1m": "K_1M",
    "3m": "K_3M",
    "5m": "K_5M",
    "10m": "K_10M",
    "15m": "K_15M",
    "30m": "K_30M",
    "1 hour": "K_60M",
    "2 hour": "K_120M",
    "4 hour": "K_240M",
    "daily": "K_DAY",
    "monthly": "K_MON",
}


def kltype_for(label: str):
    from moomoo import KLType

    name = _LABEL_TO_ATTR[label]
    if not hasattr(KLType, name):
        raise RuntimeError(f"This moomoo-api build has no KLType.{name}")
    return getattr(KLType, name)


def csv_name(code: str, interval: str) -> str:
    return f"{code.replace('.', '_')}_{interval.replace(' ', '')}.csv"
