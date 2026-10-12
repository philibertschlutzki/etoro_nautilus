"""Festes Bot-Kapital: die Risikorechnung hängt nur am Ergebnis der Bot-Trades, nicht am Kontostand.

Auf dem Demo-Konto liegen Copy-Trading-Portfolios, die Geld zwischen ``credit`` und den Portfolios verschieben, und
Einzahlungen/Aufstockungen ändern das Guthaben ebenfalls. ``Equity = credit + Investiertes + PnL`` (account_view)
würde daraus Drawdowns von über 30 % ablesen und den Breaker auslösen; die Positionsgröße (Anteil der Equity)
schwankte mit dem Guthaben.

  Bot-Equity = Kapital (Config ``live_risk.bot_capital_usd``) + realisiertes Ergebnis + unrealisierter PnL der Positionen

Das realisierte Ergebnis entsteht aus den eToro-Snapshots selbst: verschwindet eine Position zwischen zwei Abrufen, gilt
ihr zuletzt gesehener PnL als realisiert (bei Teil-Schließung der anteilige PnL). Ungenauigkeit: höchstens die
Kursbewegung zwischen zwei Abrufen (~10 s). Der Zustand liegt in ``data/state/bot_capital_<env>.json`` (neustartfest).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = PROJECT_ROOT / "data" / "state"


class BotCapital:
    def __init__(self, capital_usd: float, path: Path) -> None:
        if not capital_usd or capital_usd <= 0:
            raise ValueError("bot_capital_usd muss > 0 sein")
        self.capital = float(capital_usd)
        self.path = Path(path)
        self._lock = threading.Lock()
        self.realized = 0.0
        self._known: dict[str, dict] = {}     # positionId -> {"amount", "pnl"}
        self.fresh = True                      # True: kein Zustand vorhanden oder Kapital geändert (Basiswechsel)
        self._load()

    @classmethod
    def for_environment(cls, capital_usd: float, environment: str, state_dir: Path | None = None) -> "BotCapital":
        return cls(capital_usd, (state_dir or STATE_DIR) / f"bot_capital_{environment}.json")

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or abs(float(data.get("capital", 0.0)) - self.capital) > 1e-9:
            return                              # anderes Kapital = neue Basis
        self.fresh = False
        self.realized = float(data.get("realized", 0.0))
        self._known = {str(k): {"amount": float(v["amount"]), "pnl": float(v["pnl"])}
                       for k, v in (data.get("positions") or {}).items()}

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"capital": self.capital, "realized": self.realized, "positions": self._known}, fh)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def observe(self, positions) -> float:
        """``positions``: Iterable mit ``position_id``, ``amount``, ``pnl`` (aus einem erfolgreichen Snapshot).
        Gibt die Bot-Equity zurück und schreibt den Zustand fort."""
        with self._lock:
            current = {str(p.position_id): {"amount": float(p.amount), "pnl": float(p.pnl)} for p in positions}
            for pid, old in self._known.items():
                new = current.get(pid)
                if new is None:
                    self.realized += old["pnl"]                                   # Position geschlossen
                elif new["amount"] < old["amount"] - 1e-9 and old["amount"] > 0:   # Teil-Schließung
                    self.realized += old["pnl"] * (old["amount"] - new["amount"]) / old["amount"]
            changed = current != self._known or self.fresh
            self._known = current
            self.fresh = False
            if changed:
                self._persist()
            return self.capital + self.realized + sum(v["pnl"] for v in current.values())
