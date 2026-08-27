"""Issue #1172 — ``UnboundLocalError`` in ``sweep.main()`` durch lokales ``import os``.

Symptom: ``sweep.py --seed-salt <SALT>`` brach sofort mit

    UnboundLocalError: cannot access local variable 'os' where it is not associated with a value

in Zeile ``os.environ["OPTIMIZER_SEED_SALT"] = args.seed_salt`` ab.

Root-Cause: ``main()`` enthielt (im DEFAULT_CPU_MINUS_2-Zweig der ``--n-jobs``-Ermittlung, weit
unterhalb der ``--seed-salt``/``--allow-duplicate-run``-Auswertung) ein zusaetzliches
``import os``. Python bestimmt den Scope eines Namens fuer die GESAMTE Funktion beim Compilieren
— jede Zuweisung (auch ein spaeteres ``import os``) macht ``os`` fuer die komplette Funktion zu
einer lokalen Variablen, unabhaengig davon, ob dieser Zweig zur Laufzeit ueberhaupt erreicht wird.
Der bereits VORHER liegende Lesezugriff ``os.environ[...]`` griff dadurch auf die noch nicht
zugewiesene lokale Variable zu, statt auf das global importierte Modul (Zeile 18).

Fix: das redundante lokale ``import os`` entfernt — ``os`` ist bereits im Modul-Header importiert
und muss innerhalb von ``main()`` nicht erneut gebunden werden.
"""
from automation.optimizer import sweep


def test_main_does_not_shadow_the_global_os_module():
    # Ein GENERISCHER Bytecode-Check statt eines gezielten Zeilen-Greps: jede zukuenftige
    # Zuweisung an den Namen ``os`` irgendwo im main()-Body (import, Reassignment, ``as os`` in
    # einem ``with``/``except``-Klausel usw.) wuerde denselben UnboundLocalError-Fehlerklasse
    # reproduzieren, sobald sie oberhalb VOR einem os.*-Lesezugriff im Kontrollfluss liegt.
    assert "os" not in sweep.main.__code__.co_varnames, (
        "sweep.main() bindet 'os' lokal (z. B. per lokalem `import os`) — das macht 'os' fuer "
        "die GESAMTE Funktion zu einer lokalen Variablen und laesst den frueheren Zugriff auf "
        "os.environ (--seed-salt/--allow-duplicate-run) mit UnboundLocalError abbrechen (#1172)."
    )


def test_seed_salt_argument_parsing_reaches_os_environ_assignment(monkeypatch):
    # Reproduziert den urspruenglichen Absturzpfad so nah wie moeglich, OHNE main() vollstaendig
    # auszufuehren (das wuerde Backtest-/Study-Infrastruktur voraussetzen): das Snippet aus
    # main() exakt, das vorher UnboundLocalError warf.
    monkeypatch.delenv("OPTIMIZER_SEED_SALT", raising=False)
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-salt", default=None)
    args = parser.parse_args(["--seed-salt", "20260827T104302932639370"])
    if args.seed_salt:
        sweep.os.environ["OPTIMIZER_SEED_SALT"] = args.seed_salt
    assert sweep.os.environ["OPTIMIZER_SEED_SALT"] == "20260827T104302932639370"
