"""ייבוא רשימת סוגי השלוחות הרשמית מ-/api/sclass-types/ ל-extension_type (01-extensions.md, D-119).

הרצה (אחרי ייבוא הסריקה):
    python3 -m app.importers.types_import data/api/sclass-types-2026-10-28.json

- השם, הקבוצה, הסדר ומזהה הסוג נכתבים כשם רשמי (name_source = api), ומחליפים שם זמני (D-117).
- סכמה נכתבת רק לסוג שאין לו עדיין סכמה מהסריקה, כדי שההגדרות המוצגות יתאימו לסריקה.
"""
import argparse
import json

from app.db.connect import connect, now


def run(types_file, db_file):
    payload = json.load(open(types_file, encoding="utf-8"))
    rows = payload["data"] if isinstance(payload, dict) else payload
    db = connect(db_file)
    ts = now()
    for r in rows:
        schema = r.get("schema") or None
        db.execute(
            """INSERT INTO extension_type (cls_type, api_type_id, name, name_source, type_group, priority, schema_json,
                 schema_source, updated_at)
               VALUES (?, ?, ?, 'api', ?, ?, ?, ?, ?)
               ON CONFLICT (cls_type) DO UPDATE SET
                 api_type_id = excluded.api_type_id, name = excluded.name, name_source = 'api',
                 type_group = excluded.type_group, priority = excluded.priority,
                 schema_json = COALESCE(extension_type.schema_json, excluded.schema_json),
                 schema_source = COALESCE(extension_type.schema_source, excluded.schema_source),
                 updated_at = excluded.updated_at""",
            (r["value"].upper(), r.get("id"), (r.get("name") or "").strip() or None, r.get("group"), r.get("priority"),
             json.dumps(schema, ensure_ascii=False) if schema else None, "api-sclass-types" if schema else None, ts),
        )
    db.commit()
    used = db.execute("SELECT COUNT(DISTINCT cls_type) FROM extension").fetchone()[0]
    return {"types": len(rows), "used_in_tree": used,
            "provisional_left": db.execute("SELECT COUNT(*) FROM extension_type WHERE name_source = 'provisional'").fetchone()[0]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("types_file")
    ap.add_argument("--db", default="data/or-olam.db")
    a = ap.parse_args()
    print(json.dumps(run(a.types_file, a.db), ensure_ascii=False, indent=2))
