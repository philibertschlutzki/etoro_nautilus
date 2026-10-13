"""Soft-Reconnect für die eToro-WebSockets statt Prozess-Neustart.

Bisher beendete jeder WebSocket-Abbruch (``no close frame received``, ``keepalive ping timeout``) den ganzen Bot per
``hard_exit``; systemd startete ihn nach 30 s neu. Folgen: Strategien starten ohne Zustand, offene Positionen werden
nur übernommen, der Handel steht, und bei Neustarts im Stundentakt kommt kein Strategie-Zustand je zum Zug.

``ReconnectBudget`` begrenzt das Neuverbinden: in einem Zeitfenster sind nur wenige Versuche erlaubt, danach gilt wieder der
harte Ausstieg (Prozess-Neustart durch systemd), damit ein dauerhaft defekter Zugang nicht endlos im Kreis läuft.
"""
from __future__ import annotations

import time
from collections import deque


class ReconnectBudget:
    def __init__(self, max_events: int = 6, window_s: float = 1800.0, clock=time.monotonic) -> None:
        self._max, self._window, self._clock = int(max_events), float(window_s), clock
        self._events: deque[float] = deque()

    def allow(self) -> bool:
        """Zählt einen Reconnect-Versuch; ``False``, wenn im Fenster schon ``max_events`` stattfanden."""
        now = self._clock()
        while self._events and now - self._events[0] > self._window:
            self._events.popleft()
        if len(self._events) >= self._max:
            return False
        self._events.append(now)
        return True
