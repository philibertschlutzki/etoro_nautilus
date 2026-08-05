"""automation/optimizer/_contracts.py
=====================================
Issue #858 (Pitfall #271) — neutrales Konstantenmodul für Vertragswerte, die mehrere Module
unabhängig voneinander pflegten. Zwei unabhängig gepflegte Konstanten für denselben Vertrag
erzeugen garantiert Fehlalarme in die eine oder Blindheit in die andere Richtung — Single Source
of Truth statt drei Kopien (``invariants._MAX_BARS_IN_TRADE_CAP``, ``spaces._MAX_BARS_IN_TRADE_CAP``,
``hourly_strategy_base.MAX_BARS_IN_TRADE_HARD_CAP``, vor diesem Fix drei unabhängige Literale).

Bewusst OHNE jede Abhängigkeit (kein nautilus_trader, kein Optuna, kein pandas) — importierbar von
``invariants.py`` (deklariert sich im eigenen Moduldocstring als frei von nautilus_trader),
``spaces.py`` UND ``automation.strategies.hourly_strategy_base`` (importiert nautilus_trader
bereits selbst; schliesst sich hier nur der gemeinsamen Konstantenquelle an) ohne einen
Import-Zyklus zu riskieren.
"""
from __future__ import annotations

# Issue #714/GR-01 — die 24-Bar-Zeitbox-Obergrenze für ``max_bars_in_trade``. Der Bar-Zähler-Exit
# in ``HourlyStrategyBase`` erzwingt sie unabhängig vom je Trial gesampelten Wert; der Optuna-
# Suchraum (``spaces.py``) und die Report-Invarianten (``invariants.py``) dürfen NIE grösser
# suchen/prüfen als dieser Deckel.
MAX_BARS_IN_TRADE_HARD_CAP = 24
