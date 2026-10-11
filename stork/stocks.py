"""Supported instruments and their research context; legacy data belongs to MU."""
STOCKS = {
    "US.MU": {"symbol": "US.MU", "ticker": "MU", "name": "Micron", "company": "Micron Technology", "focus": "DRAM, NAND, HBM, memory pricing and AI demand"},
    "US.SNDK": {"symbol": "US.SNDK", "ticker": "SNDK", "name": "SanDisk", "company": "SanDisk", "focus": "NAND flash, SSD demand, flash pricing, Kioxia partnerships and storage demand. Use the current company separated from Western Digital in 2025; do not mix pre-2016 SNDK history"},
}


def stock_symbol(value="US.MU"):
    if not isinstance(value, str) or value not in STOCKS:
        raise ValueError("Choose Micron (US.MU) or SanDisk (US.SNDK)")
    return value


def stock_key(key, symbol="US.MU"):
    symbol = stock_symbol(symbol)
    return key if symbol == "US.MU" else f"{key}:{symbol}"
