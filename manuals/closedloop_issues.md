Issue 1: Feature - AI Engine Scaffold (Client, Parsing, Ledger)ZielImplementierung des Grundgerüsts für die AI-Optimierungsschleife. Dazu gehört ein asynchroner API-Client für DeepSeek sowie das Parsing-Modul, um historische Backtest-Logs und Gate-Rejections als strukturierten Prompt-Kontext aufzubereiten.Root-CauseDas System verfügt bisher über keine Schnittstelle zu DeepSeek R1/V3 und keine standardisierte Methode, um Leistungsdaten (Sharpe, Sortino, PBO) und Fehlermeldungen (Gate-Rejections) aus vergangenen Läufen automatisiert in einen für LLMs verständlichen JSON-Kontext zu übersetzen.FixErstellung der neuen Module client.py und ingestion.py im Verzeichnis automation/ai_loop/. Einrichten des memory.py Ledgers.Betroffener Code:Datei: automation/ai_loop/ingestion.py (Neue Datei)# ab Zeile 1: Basis-Struktur für das Parsing
1: import json
2: from pathlib import Path
3: 
4: class PerformanceParser:
5:     def __init__(self, log_dir: Path):
6:         self.log_dir = log_dir
7:
8:     def extract_run_context(self, symbol: str, strategy: str, history_depth: int = 3) -> dict:
9:         # Implementierung des Parsings von logs/run_<id>.json 
10:        # und logs/gate_eval_<id>.json
11:        pass
Datei: automation/ai_loop/client.py (Neue Datei)# ab Zeile 1: Async DeepSeek Client
1: import os
2: import httpx
3:
4: class DeepSeekClient:
5:     def __init__(self):
6:         self.api_key = os.getenv("DEEPSEEK_API_KEY")
7:         
8:     async def call_reasoner(self, payload: dict) -> str:
9:         # API Call an deepseek-reasoner (R1)
10:        pass
11:
12:    async def call_chat(self, prompt: str) -> str:
13:        # API Call an deepseek-chat (V3)
14:        pass
Akzeptanzkriterien (Definition of Done)[ ] Async Client für DeepSeek (call_reasoner und call_chat) ist funktionsfähig und verarbeitet Retries/Timeouts.[ ] Umgebungsvariablen (DEEPSEEK_API_KEY, etc.) werden aus .env geladen.[ ] PerformanceParser kann logs/run_<id>.json und logs/gate_eval_<id>.json lesen und in die spezifizierte Payload-Struktur übersetzen.[ ] memory.py initialisiert logs/ai_optimization_ledger.jsonl.Betroffene Dateienautomation/ai_loop/client.py (Neu)automation/ai_loop/ingestion.py (Neu)automation/ai_loop/memory.py (Neu).env.exampleIssue 2: Feature - Static Verification & Self-Healing PipelineZielEinführung einer lokalen Validierungsschicht (validator.py), die von der KI generierten Code vor der Ausführung prüft. Enthalten sind AST-Checks gegen Lookahead-Bias sowie automatisierte ruff-, mypy- und pytest-Aufrufe inkl. Self-Correction Schleife.Root-CauseKI-generierter Code enthält oft Syntaxfehler, Typisierungsprobleme oder konzeptionelle Fehler (Lookahead-Bias durch falschen Bar-Referenzen). Werden diese ungeprüft in die Backtesting-Engine geladen, führt dies zu zeitintensiven Abstürzen der Worker-Prozesse.FixErstellung des validator.py Moduls. Implementierung der AST-Analyse und einer subprocess basierten Linter-Ausführung.Betroffener Code:Datei: automation/ai_loop/validator.py (Neue Datei)# ab Zeile 1: Validierungs-Pipeline
1: import ast
2: import subprocess
3: from pathlib import Path
4:
5: class StaticValidator:
6:     def check_ast_safety(self, file_path: Path) -> bool:
7:         # Parse AST, blockiere time.sleep und prüfe auf Lookahead-Bias
8:         pass
9:
10:    def run_linters(self, target_path: str) -> tuple[bool, str]:
11:        # Subprocess Call für ruff und mypy --strict
12:        pass
13:
14:    def validate_code(self, file_path: Path, max_retries: int = 3) -> bool:
15:        # Orchestriert AST, Linting und pytest. 
16:        # Bei Fehler: Generiert Self-Correction Prompt für deepseek-chat
17:        pass
Akzeptanzkriterien (Definition of Done)[ ] check_ast_safety erkennt und blockiert synchrone blockierende Calls (z.B. time.sleep).[ ] run_linters führt ruff und mypy aus und fängt stderr/stdout korrekt ab.[ ] Unit-Tests (pytest) für die modifizierte Strategie werden via Subprocess gestartet.[ ] Bei Fehlern wird ein Feedback-Prompt (Traceback) an das V3-Modell (max 3 Retries) gesendet (Self-Healing).Betroffene Dateienautomation/ai_loop/validator.py (Neu)Issue 3: Feature - Reasoning & Code Synthesis (R1/V3 Cascade)ZielIntegration der zweistufigen KI-Logik. Der Reasoner (R1) analysiert die Daten und wählt den Pfad (Parameter oder Logik). Der Synthesizer (V3) generiert daraufhin die korrekten JSON-Overrides oder manipuliert den Python-Strategiecode.Root-CauseLarge Language Models degradieren in ihrer Leistungsfähigkeit, wenn sie komplexe Fehleranalysen und sofortige Code-Generierung in einem einzigen Prompt erledigen müssen. Eine Kaskadierung (Trennung von "Denken" und "Schreiben") ist zwingend erforderlich.FixErstellung von reasoning.py und synthesizer.py.Betroffener Code:Datei: automation/ai_loop/reasoning.py (Neue Datei)# ab Zeile 10: R1 Hypothesen-Generierung
10: class StrategyReasoner:
11:     def __init__(self, client):
12:         self.client = client
13:
14:     async def formulate_hypothesis(self, context_payload: dict) -> dict:
15:         # Sendet Payload an R1, entscheidet Pfad A (Params) oder B (Logic)
16:         # Gibt strukturierten Aktionsplan zurück
17:         pass
Datei: automation/ai_loop/synthesizer.py (Neue Datei)# ab Zeile 10: V3 Code-Generierung
10: class CodeSynthesizer:
11:     async def apply_mutation(self, hypothesis: dict, strategy_file: str):
12:         if hypothesis['path'] == 'A':
13:             # Generiere JSON in automation/config/search_space_overrides.json
14:             pass
15:         elif hypothesis['path'] == 'B':
16:             # Ersetze Code in automation/strategies/
17:             pass
Akzeptanzkriterien (Definition of Done)[ ] StrategyReasoner nimmt das Dictionary aus ingestion.py und extrahiert einen konkreten Optimierungspfad (A oder B).[ ] CodeSynthesizer überschreibt search_space_overrides.json korrekt (Pfad A).[ ] CodeSynthesizer führt sauberes File-I/O auf automation/strategies/<strategy>.py aus (Pfad B).[ ] Einhaltung strenger Typisierung im Python-Code durch V3-Prompting.Betroffene Dateienautomation/ai_loop/reasoning.py (Neu)automation/ai_loop/synthesizer.py (Neu)Issue 4: Feature - Orchestrator State Machine & Git RollbackZielZusammenführung aller Module in eine vollautonome State Machine (orchestrator.py). Implementierung des Subprocess-Aufrufs für den lokalen Backtest sowie das Rollback/Promotion-Verhalten via Git nach Auswertung des Deployment Gates.Root-CauseDamit das System nachts ohne menschliches Eingreifen Iterationen fahren kann, muss eine übergeordnete Schleife existieren. Fehlgeschlagene Mutationen (Gate Rejected) müssen via Git Hard-Reset sicher rückgängig gemacht werden, während erfolgreiche Runs per Commit fixiert werden.FixImplementierung von orchestrator.py, welche die Loop von Ingestion bis Gate evaluiert.Betroffener Code:Datei: automation/ai_loop/orchestrator.py (Neue Datei)# ab Zeile 25: Hauptschleife und Git-Logik
25: class AILoopOrchestrator:
26:     def run_cycle(self, symbol: str, strategy: str):
27:         # 1. Ingest -> 2. Reason -> 3. Synth -> 4. Validate
28:         # 5. Backtest via Subprocess
29:         result = subprocess.run(["python", "-m", "automation.optimizer.run_optimization", ...])
30:         
31:         # 6. Gate Check & Rollback
32:         if gate_passed:
33:             self._commit_champion(strategy)
34:         else:
35:             self._rollback_changes(strategy)
36:
37:     def _rollback_changes(self, strategy: str):
38:         subprocess.run(["git", "checkout", "--", f"automation/strategies/{strategy}.py"])
39:         subprocess.run(["git", "checkout", "--", "automation/config/search_space_overrides.json"])
Akzeptanzkriterien (Definition of Done)[ ] Die Methode run_cycle verknüpft Ingestion, Reasoner, Synthesizer und Validator sequenziell.[ ] Der Backtest wird über subprocess.run (CLI Integration) gestartet.[ ] Bei Gate-Rejection wird git checkout -- <file> auf modifizierte Dateien ausgeführt und das Ledger (memory.py) geupdatet.[ ] Bei Gate-Approval (PROMOTED) wird ein automatisierter Git Commit ausgelöst (z.B. feat(ai-loop): promote champion...).[ ] Begrenzung der Schleife durch AI_LOOP_MAX_ITERATIONS aus .env.Betroffene Dateienautomation/ai_loop/orchestrator.py (Neu)automation/ai_loop/__init__.py (Neu)
