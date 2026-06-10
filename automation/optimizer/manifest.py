import hashlib
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WORK = PROJECT_ROOT / "data" / "optimizer"

def git_commit() -> str:
    """Returns the current git short hash, or 'unknown' if not available."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT)
        ).decode("utf-8").strip()
    except Exception:
        return "unknown"

def sha256_file(path: Path) -> str:
    """Returns the SHA-256 hash of the file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()

def catalog_fingerprint(catalog: Path | None = None) -> str:
    """Returns a stable fingerprint for the given catalog path based on its data.parquet files."""
    if catalog is None:
        catalog = PROJECT_ROOT / "data" / "nautilus"

    if not catalog.exists() or not catalog.is_dir():
        return "unknown_catalog_missing"

    parts = []
    for p in sorted(catalog.rglob("data.parquet")):
        rel = p.relative_to(catalog)
        st = p.stat()
        parts.append(f"{rel}:{st.st_size}:{int(st.st_mtime)}")

    if not parts:
        return "empty_catalog"

    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
