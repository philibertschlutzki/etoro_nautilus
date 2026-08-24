import sys
import os

# Dynamically add the root directory (parent of automation) to sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Issue #1080/#1228 — mehrere aeltere Testmodule installieren unbedingt einen unvollstaendigen
# nautilus_trader-Mock in sys.modules (geschrieben, als das echte Paket in der Sandbox nicht
# installierbar war), per ``if "nautilus_trader" not in sys.modules: ...``. Ist das echte Paket in
# der Umgebung installiert (siehe automation/requirements.txt), IMPORTIEREN wir es hier EINMALIG,
# VOR jeder Testkollektion — jeder der alten Mock-Guards sieht es dann bereits in sys.modules und
# ueberspringt seinen Mock. Ohne diesen frühen Import "gewinnt" je nach Kollektionsreihenfolge mal
# das echte, mal das gemockte Paket, mit teils bizarren Cross-Test-Interferenzen (ein Testmodul, das
# frueh eine echte Bar/Price-Klasse importiert, kollidiert mit einem spaeteren Modul, das
# nautilus_trader zwischenzeitlich neu importiert — Cython-Erweiterungen wie tick_scheme sind nicht
# idempotent reimportierbar). Best-effort: fehlt das Paket (aeltere/Minimal-Sandbox ohne
# requirements.txt), bleibt das bisherige Mock-basierte Verhalten unveraendert.
try:
    import nautilus_trader  # noqa: F401
except ImportError:
    pass
