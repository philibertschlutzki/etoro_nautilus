"""
automation/strategies/ebc_common.py
===================================
Gemeinsame Bausteine der drei Tagesachsen-Archetypen (EBC-H1 §6.1): inkrementelle Indikatoren ohne Nautilus-Abhängigkeit
und eine Mixin-Klasse für Signal-Exit, Seitenregel und Regime-Gate. Die Archetypen leiten von ``HourlyStrategyBase`` ab
und nutzen deren Exit-Vertrag (ATR-Trailing-Stop auf Schlusskurs, Zeitstopp über ``max_bars_in_trade``).
"""
from __future__ import annotations

from collections import deque
from math import sqrt


class WilderAtr:
    """ATR (Wilder, alpha = 1/n, mit der ersten True Range gesät) — gleiche Rekursion wie ``MovingAverageType.WILDER``."""

    def __init__(self, period: int):
        self.period = period
        self.value = 0.0
        self.count = 0
        self._prev_close: float | None = None

    @property
    def initialized(self) -> bool:
        return self.count >= self.period

    def update(self, high: float, low: float, close: float) -> float:
        tr = high - low if self._prev_close is None else max(high - low, abs(high - self._prev_close),
                                                           abs(low - self._prev_close))
        self.value = tr if self.count == 0 else self.value + (tr - self.value) / self.period
        self.count += 1
        self._prev_close = close
        return self.value


class WilderRsi:
    """RSI (Wilder); die ersten ``period`` Differenzen werden gemittelt, danach rekursiv geglättet."""

    def __init__(self, period: int):
        self.period = period
        self._prev: float | None = None
        self._gains: list[float] = []
        self._losses: list[float] = []
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self.value = 50.0
        self.initialized = False

    def update(self, close: float) -> float:
        if self._prev is None:
            self._prev = close
            return self.value
        change = close - self._prev
        self._prev = close
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if not self.initialized:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) == self.period:
                self._avg_gain = sum(self._gains) / self.period
                self._avg_loss = sum(self._losses) / self.period
                self.initialized = True
            else:
                return self.value
        else:
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        if self._avg_loss == 0.0:
            self.value = 100.0 if self._avg_gain > 0 else 50.0
        else:
            self.value = 100.0 - 100.0 / (1.0 + self._avg_gain / self._avg_loss)
        return self.value


class Ema:
    def __init__(self, period: int):
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.value = 0.0
        self.count = 0

    @property
    def initialized(self) -> bool:
        return self.count >= self.period

    def update(self, x: float) -> float:
        self.value = x if self.count == 0 else self.value + self.alpha * (x - self.value)
        self.count += 1
        return self.value


class RollingWindow:
    """Feste Fensterlänge über Schlusskurse: Summe, Standardabweichung (Population), Extrema der VORHERIGEN n Werte."""

    def __init__(self, size: int):
        self.size = size
        self.data: deque[float] = deque(maxlen=size)

    def push(self, x: float) -> None:
        self.data.append(x)

    @property
    def full(self) -> bool:
        return len(self.data) == self.size

    def mean(self) -> float:
        return sum(self.data) / len(self.data)

    def std(self) -> float:
        m = self.mean()
        return sqrt(sum((v - m) ** 2 for v in self.data) / len(self.data))


class SideRuleMixin:
    """Seitenregel + Regime-Gate der Archetypen: Short nur mit ``allow_short`` und (optional) im Abwärtsregime
    (Schluss < SMA200 und SMA200 über 20 Tage fallend). Die Hilfsgrössen liefert die Strategie selbst."""

    def _short_allowed(self, close: float, sma200: float | None, sma200_20_ago: float | None) -> bool:
        if not self.config.allow_short:
            return False
        if not getattr(self.config, "short_regime_gate", False):
            return True
        return (sma200 is not None and sma200_20_ago is not None and close < sma200 and sma200 < sma200_20_ago)
