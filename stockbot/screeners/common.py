from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Signal:
    key: str            # 검색식 키
    title: str          # 검색식 이름 (메시지에 표시)
    code: str
    name: str
    market: str
    price: float
    change_pct: float   # 전일 대비 %
    score: float        # 정렬용 (클수록 위)
    lines: list[str] = field(default_factory=list)   # 근거 설명 줄
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def trimmed_mean(values, trim_pct: float) -> float:
    """정렬 후 상·하위 trim_pct% 를 제외한 나머지의 평균 (trim 30 → 중간 40%)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    n = len(v)
    if n == 0:
        return 0.0
    v = np.sort(v)
    k = int(n * trim_pct / 100.0)
    core = v[k:n - k] if n - 2 * k >= 1 else v
    return float(core.mean())


def last_change_pct(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    c0, c1 = float(df["close"].iloc[-2]), float(df["close"].iloc[-1])
    return (c1 / c0 - 1.0) * 100.0 if c0 > 0 else 0.0


def month_label(ts: pd.Timestamp) -> str:
    return f"{ts.year}.{ts.month:02d}"
