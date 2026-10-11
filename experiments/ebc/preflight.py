"""EBC Preflight: /me-Pruefung (read-only), Konto-Isolation, Repo-Stand. Schluessel werden nie ausgegeben."""
import json, os, subprocess, sys, datetime
sys.path.insert(0, os.getcwd())
from automation import account_guard as ag

def load_env(path=".env"):
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def main(out):
    load_env()
    me = ag.fetch_me(os.environ["ETORO_API_KEY"], os.environ["ETORO_USER_KEY"])
    env = ag.verify_paper_account(os.environ["ETORO_API_KEY"], os.environ["ETORO_USER_KEY"])
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    res = {"ts_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "me_realCid_matches_pinned": str(me.get("realCid")) == ag.pinned_cid(),
           "me_keys": sorted(me.keys()), "api_environment": env, "clone_head": head,
           "expected_head": "5f737ed495e70eab85b2dbbb7673eb7369c428ce",
           "head_ok": head.startswith("5f737ed4")}
    json.dump(res, open(out, "w"), indent=1)
    print(json.dumps(res, indent=1))

if __name__ == "__main__":
    main(sys.argv[1])
