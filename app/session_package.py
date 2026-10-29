"""חבילת סשן (D-123): שמירה ושחזור של כל מה שמחוץ למאגר, בקובץ zip אחד שהמשתמשת שומרת אצלה.

הקונטיינר של כל סשן זמני. בסוף סשן:
    python3 -m app.session_package save        → data/or-olam-session-<תאריך>.zip
ובתחילת הסשן הבא, אחרי העלאת הקובץ:
    python3 -m app.session_package restore <קובץ zip>

בחבילה: בסיס הנתונים המעובד, קובצי המקור (סריקה, טיוטה, סוגי שלוחות), מצב הדף האחרון ו-.env עם מפתח ההצפנה.
⚠️ בגלל המפתח, מי שמחזיק את החבילה יכול לפענח את הערכים הרגישים. שומרים אותה רק אצל המשתמשת, ולא במאגר.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    "data/or-olam.db",
    "data/scan/or-olam-full-tree-settings.json",
    "data/draft/draft-2026-10-21.docx",
    "data/api/sclass-types-2026-10-28.json",
    ".env",
]
DIRS = ["data/page-state"]
SCHEMA = "app/db/schema.sql"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head():
    try:
        return subprocess.check_output(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def save(out=None):
    files = [f for f in FILES if os.path.exists(os.path.join(ROOT, f))]
    for d in DIRS:
        for base, _, names in os.walk(os.path.join(ROOT, d)):
            files += [os.path.relpath(os.path.join(base, n), ROOT) for n in sorted(names)]
    missing = [f for f in FILES if f not in files]
    db = sqlite3.connect(os.path.join(ROOT, "data/or-olam.db"))
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("extension", "extension_type", "draft_node", "draft_note", "mapping_row")}
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_head(),
        "schema_sha256": sha256(os.path.join(ROOT, SCHEMA)),
        "counts": counts,
        "files": {f: sha256(os.path.join(ROOT, f)) for f in files},
        "missing": missing,
    }
    out = out or os.path.join(ROOT, f"data/or-olam-session-{datetime.now().strftime('%Y-%m-%d')}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            z.write(os.path.join(ROOT, f), f)
        z.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return {"package": out, "size_mb": round(os.path.getsize(out) / 1e6, 1), "files": len(files), "missing": missing,
            "counts": counts}


def restore(path):
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("MANIFEST.json"))
        for name in z.namelist():
            if name == "MANIFEST.json":
                continue
            if name.startswith("/") or ".." in name.split("/") or not (name.startswith("data/") or name == ".env"):
                raise SystemExit(f"נתיב לא צפוי בחבילה: {name}")
            z.extract(name, ROOT)
    bad = [f for f, h in manifest["files"].items() if sha256(os.path.join(ROOT, f)) != h]
    if bad:
        raise SystemExit(f"קבצים פגומים בחבילה: {bad}")
    result = {"restored": len(manifest["files"]), "package_created": manifest["created_at"],
              "package_commit": manifest["git_commit"], "current_commit": git_head()}
    # המפתח שבחבילה פותח את הערכים המוצפנים?
    env = dict(line.strip().split("=", 1) for line in open(os.path.join(ROOT, ".env")) if "=" in line)
    os.environ.update(env)
    from app import crypto
    db = sqlite3.connect(os.path.join(ROOT, "data/or-olam.db"))
    token = db.execute("SELECT sensitive_enc FROM extension WHERE sensitive_enc IS NOT NULL LIMIT 1").fetchone()[0]
    crypto.decrypt(token)
    result["key_ok"] = True
    if manifest["schema_sha256"] != sha256(os.path.join(ROOT, SCHEMA)):
        result["schema_changed"] = ("הסכמה במאגר השתנתה מאז שהחבילה נוצרה. בונים מחדש לפי app/README.md "
                                    "(קובצי המקור בחבילה), ואחר כך מחילים את data/page-state.")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("save")
    s.add_argument("--out")
    r = sub.add_parser("restore")
    r.add_argument("zip_path")
    a = ap.parse_args()
    out = save(a.out) if a.cmd == "save" else restore(a.zip_path)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0)
