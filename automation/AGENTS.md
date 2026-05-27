# automation/ — Eigenständiges Betriebs- und Entwickler-Handbuch

## Table of Contents
1. Produktübersicht
2. Repository-Struktur
3. Architektur & Datenfluss
4. Umgebungs-Setup (.env, requirements.txt, systemd)
5. Dienste & Komponenten
   5.1 catalog_service.py (24/7 Tick-Sammlung)
   5.2 universe_fetcher.py (Smart Portfolio Universe)
   5.3 api_backfiller.py (Historische Backfill)
   5.4 backtest_runner.py (Matrix-Backtest)
   5.5 daily_orchestrator.py (End-to-End-Pipeline)
6. Konfigurationssystem (automation/config/)
7. Datenfluss & Verzeichnisstruktur
8. systemd-Integration
9. Testing & Validierung
10. Bekannte Pitfalls & Problemlösungen
11. Conventions für KI-Coding-Agents (Jules)
12. Changelog (Agent-Maintained)

## 1. Produktübersicht
Das `automation/`-Paket ist ein vollständig isoliertes, autonomes Daten- und Ausführungs-Framework für das eToro Nautilus Trading-Ökosystem. Es verwaltet die Beschaffung von Kursdaten, die Pflege des Anlageuniversums und die orchestrierte Ausführung von Backtests und Live-Trading-Pipelines ohne externe Code-Abhängigkeiten innerhalb des Hauptprojekts.

## 2. Repository-Struktur
`automation/` enthält alle wesentlichen Komponenten in einer flachen Struktur oder dedizierten Unterordnern wie `config/` und `strategies/`. Keine Datei innerhalb von `automation/` importiert aus `adapters/` oder der Root-Ebene.

## 3. Architektur & Datenfluss
Die Daten fließen von eToro (Live/API) in den Nautilus-Catalog (`data/nautilus`). Die tägliche Pipeline synchronisiert das Universum, holt Daten ab, führt ein Backtest-Tournament durch und entscheidet über Live-Ausführungen.

## 4. Umgebungs-Setup (.env, requirements.txt, systemd)
Vollständige `.env`-Referenztabelle:

| Variable | Pflicht | Default | Verwendet von |
|----------|---------|---------|---------------|
| `ETORO_API_KEY` | Ja | — | alle Dienste |
| `ETORO_USER_KEY` | Ja | — | alle Dienste |
| `MOMENTUM_LS_USERNAME` | Ja | — | universe_fetcher.py |

Abhängigkeiten werden über `automation/requirements.txt` installiert.

## 5. Dienste & Komponenten

### 5.1 catalog_service.py
Sammelt 24/7 Ticks und persistiert diese fortlaufend.

### 5.2 universe_fetcher.py
Holt das Smart Portfolio Universe (basierend auf `MOMENTUM_LS_USERNAME`) und filtert nach bekannten Instrumenten.

### 5.3 api_backfiller.py
Füllt historische Kerzendaten via eToro API ab, um Datenlücken im Nautilus Catalog zu schließen.

### 5.4 backtest_runner.py
Führt Matrix-Backtesting auf dem aktuellen Universum mit den Strategien aus `automation/config/strategies.json` durch.

### 5.5 daily_orchestrator.py
Steuert die tägliche End-to-End Pipeline in definierten Phasen (Universe → Data → Backtest/Tournament → Live Deployment).

## 6. Konfigurationssystem (automation/config/)
Zentrale Konfigurationen (z. B. `instrument_map.json`, `strategies.json`, `tournament.json`) befinden sich im Unterverzeichnis `automation/config/`.

## 7. Datenfluss & Verzeichnisstruktur
Alle Daten (Ticks, Kerzen, Logs) werden in `data/nautilus`, `data/universe` und `logs/` gespeichert, immer relativ zum Projekt-Root oder als Fallback isoliert verwaltet.

## 8. systemd-Integration
systemd Unit-File-Template für `catalog_service.py`:
```ini
[Unit]
Description=eToro Nautilus Catalog Service — kontinuierliche Tick-Sammlung
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<YOUR_USER>
WorkingDirectory=<PROJECT_ROOT>
ExecStart=/usr/bin/python3 <PROJECT_ROOT>/automation/catalog_service.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Deployment-Befehle:
```bash
sudo cp automation/config/catalog_service.service \
    /etc/systemd/system/catalog_service.service
sudo systemctl daemon-reload
sudo systemctl enable catalog_service
sudo systemctl start catalog_service
sudo systemctl status catalog_service
journalctl -u catalog_service -f
```

## 9. Testing & Validierung
Alle Tests befinden sich in `tests/` und lassen sich via `pytest` ausführen. Die Testumgebung stellt sicher, dass Isolation und Funktionalität (z.B. Fallback Precisions) bestehen bleiben (siehe `automation/testing.md`).

## 10. Bekannte Pitfalls & Problemlösungen
- OOM Issues während des Backtestings: Nutze `--max-workers` Limits.
- Precision Issues: Es wird ein Fallback auf `_fallback_precisions` genutzt, wenn Parquet-Metadaten fehlen.
- Fractional Equities via By-Amount Endpoint.

## 11. Conventions für KI-Coding-Agents (Jules)
- **Standalone-Constraint:** kein Import aus `adapters/`, `config/` (Root), `strategies/` (Root).
- **.env-Pfad-Konvention:** `automation/.env` → parent `.env` Fallback.
- **Strategien:** Alle neuen Strategien → `automation/strategies/`.
- **Instrumente:** Neue Instrumente → `automation/config/instrument_map.json`.
- **Logging:** immer `logging.getLogger(__name__)`, niemals `print()`.
- **os._exit(1):** os._exit(1) Konvention für WebSocket-Fehler (systemd-Restart).
- **Subprocess-Logging:** Subprocess-stdout/stderr immer via Logger weiterleiten.

## 12. Changelog (Agent-Maintained)

| Datum | Änderung | Dateien |
|-------|----------|---------|
| 2026-05-27 | `automation/` als eigenständiges Produkt — kein adapters/-Import | alle automation/*.py |
| 2026-05-27 | `automation/universe_fetcher.py` — SmartPortfolio-Fetch standalone | automation/universe_fetcher.py |
| 2026-05-27 | `automation/backtest_runner.py` — Migration von backtesting/run_backtest.py | automation/backtest_runner.py |
| 2026-05-27 | `automation/strategies/` — alle Strategies migriert | automation/strategies/*.py |
| 2026-05-27 | `automation/momentum_ls_allocator.py` — migriert aus adapters/ | automation/momentum_ls_allocator.py |
| 2026-05-27 | `automation/config/instrument_map.json` — erweitertes Format | automation/config/instrument_map.json |
| 2026-05-27 | `automation/config/strategies.json` — Pfade auf automation.strategies.* aktualisiert | automation/config/strategies.json |
| 2026-05-27 | `automation/.env.example` — vollständige .env-Dokumentation | automation/.env.example |
| 2026-05-27 | `automation/requirements.txt` — flache standalone requirements | automation/requirements.txt |
| 2026-05-27 | Safety-Interlocks entfernt — Steuerung via API-Keys | automation/daily_orchestrator.py |
| 2026-05-27 | testing.md ersetzt — konsistent mit _fallback_precisions, neue Tests | automation/testing.md |
| 2026-05-27 | Root-AGENTS.md und Root-requirements.txt nach archive/ verschoben | archive/ |
