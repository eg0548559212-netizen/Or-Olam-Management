"""בונה את דף ההצגה (כלי עבודה, לא עיצוב סופי) מתוך בסיס הנתונים.

הרצה:
    python3 -m app.demo.build            → data/demo/or-olam-demo.html

הדף לא כולל אף ערך מוצפן: לא נקרא כאן שום שדה *_enc, ובמקום ערכים רגישים יש רק סימונים.
הקובץ שנוצר נשמר תחת data/ (מחוץ למאגר), כי יש בו את כל שמות השלוחות וההגדרות.
"""
import argparse
import json
import os
import re
import sqlite3
from collections import Counter

PHONE_RE = re.compile(r"(?<![\d.])0\d{1,2}-?\d{7}(?![\d.])")
TEMPLATE = os.path.join(os.path.dirname(__file__), "template.html")


def flat_titles(schema):
    """מפתח → {t: כותרת, e: {ערך: כותרת}} לרמה העליונה של הסכמה."""
    out = {}
    for k, v in (schema or {}).get("properties", {}).items():
        if not isinstance(v, dict):
            continue
        entry = {}
        if v.get("title"):
            entry["t"] = v["title"].strip()
        enum, titles = v.get("enum"), (v.get("options") or {}).get("enum_titles")
        if enum and titles and len(enum) == len(titles):
            entry["e"] = {json.dumps(a, ensure_ascii=False): b for a, b in zip(enum, titles)}
        if entry:
            out[k] = entry
    return out


def build(db_file):
    db = sqlite3.connect(db_file)
    db.row_factory = sqlite3.Row
    q = lambda sql, *a: [dict(r) for r in db.execute(sql, a)]

    types, defaults = {}, {}
    for r in q("SELECT cls_type, name, name_source, type_group, priority, schema_json FROM extension_type"):
        schema = json.loads(r["schema_json"]) if r["schema_json"] else {}
        types[r["cls_type"]] = {"n": r["name"], "p": r["name_source"] == "provisional", "g": r["type_group"], "o": r["priority"],
                                "k": flat_titles(schema)}
        defaults[r["cls_type"]] = {k: v.get("default") for k, v in schema.get("properties", {}).items()
                                   if isinstance(v, dict) and "default" in v}

    ext = []
    for r in q("""SELECT sclass_id, parent_sclass_id, path, path_sort, name, cls_type, settings, count_sub_sclass,
                         is_open, has_teacher_phone, has_pin_code_extension, conf_id
                  FROM extension WHERE missing_since IS NULL ORDER BY path_sort"""):
        s = json.loads(r["settings"]) if r["settings"] else None
        if s is not None:
            d = defaults.get(r["cls_type"], {})
            s = {k: v for k, v in s.items() if not (k in d and d[k] == v)}
        ext.append([r["sclass_id"], r["parent_sclass_id"], r["path"], r["name"], r["cls_type"], s,
                    r["is_open"], r["has_teacher_phone"], r["has_pin_code_extension"]])

    refs = [[r["sclass_id"], r["setting_key"], r["ref_kind"], r["target_text"], r["target_form"], r["target_sclass_id"],
             r["status"]] for r in q("SELECT * FROM extension_setting_ref ORDER BY sclass_id, ref_id")]
    aliases = [[r["old_path"], r["sclass_id"]] for r in q("SELECT * FROM extension_path_alias")]

    plan = q("SELECT * FROM migration_plan ORDER BY plan_id LIMIT 1")[0]
    draft = q("SELECT * FROM draft WHERE plan_id = ? AND kind = 'working'", plan["plan_id"])[0]
    nodes = [[r["node_id"], r["parent_node_id"], r["digit"], r["full_path"], r["title"], r["node_status"], r["node_kind"],
              json.loads(r["hints"]) if r["hints"] else None, r["link_status"], r["special_build"], r["planned_cls_type"],
              r["deleted"], json.loads(r["flags"]) if r.get("flags") else []]
             for r in q("SELECT * FROM draft_node WHERE draft_id = ? ORDER BY position", draft["draft_id"])]
    notes = [[r["node_id"], r["scope"], r["note_kind"], r["text"]]
             for r in q("SELECT * FROM draft_note WHERE draft_id = ? ORDER BY note_id", draft["draft_id"])]
    maps = [[r["node_id"], r["sclass_id"], r["relation"], r["confidence"], r["status"], json.loads(r["signals"])]
            for r in q("""SELECT t.node_id, s.sclass_id, m.relation, m.confidence, m.status, m.signals
                          FROM mapping_row m JOIN mapping_target t USING (mapping_id) JOIN mapping_source s USING (mapping_id)
                          WHERE m.plan_id = ? AND m.status IN ('suggested', 'needs_decision')""", plan["plan_id"])]
    # החלטות שכבר הוחלו על בסיס הנתונים: בסיס לדף, וההחלטות החיות בדף גוברות עליהן
    rel_action = {"keep": "same", "move": "same", "rebuild": "same", "split": "same", "merge": "merge"}
    linked = {}
    for r in q("""SELECT t.node_id, m.relation, m.decided_by, m.decided_at, GROUP_CONCAT(s.sclass_id) AS ids
                  FROM mapping_row m JOIN mapping_target t USING (mapping_id) JOIN mapping_source s USING (mapping_id)
                  WHERE m.plan_id = ? AND m.status = 'approved' GROUP BY m.mapping_id""", plan["plan_id"]):
        linked[r["node_id"]] = {"node_id": r["node_id"], "action": rel_action.get(r["relation"], "same"),
                                "sclass_ids": [int(x) for x in r["ids"].split(",")], "decided_by": r["decided_by"],
                                "decided_at": r["decided_at"], "source": "db"}
    # D-152: שלוחות שסומנו "נסגרת" והוחלו. בדף, האוסף retired החי גובר עליהן
    retired = [r["sclass_id"] for r in q("""SELECT s.sclass_id FROM mapping_row m JOIN mapping_source s USING (mapping_id)
                                            WHERE m.plan_id = ? AND m.relation = 'retire' AND m.status = 'approved'""", plan["plan_id"])]
    report = q("SELECT * FROM gap_report WHERE draft_id = ? ORDER BY report_id DESC LIMIT 1", draft["draft_id"])[0]
    gaps = [[r["gap_kind"], r["severity"], r["draft_node_id"], r["sclass_id"], json.loads(r["detail"]) if r["detail"] else {}]
            for r in q("SELECT * FROM gap_item WHERE report_id = ? ORDER BY item_id", report["report_id"])]
    validation = dict(Counter(r["rule_id"] for r in q("SELECT rule_id FROM validation_result")))
    run = q("SELECT * FROM tree_sync_run ORDER BY run_id DESC LIMIT 1")[0]

    return {
        "meta": {"scan_ref": run["source_ref"], "imported_at": run["finished_at"], "run_log": json.loads(run["log"]),
                 "plan": plan["name"], "draft_label": draft["label"], "report_at": report["created_at"],
                 "validation": validation},
        "types": types, "ext": ext, "refs": refs, "aliases": aliases,
        "nodes": nodes, "notes": notes, "maps": maps, "gaps": gaps, "linked": list(linked.values()), "retired": retired,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/or-olam.db")
    ap.add_argument("--out", default="data/demo/or-olam-demo.html")
    args = ap.parse_args()
    data = json.dumps(build(args.db), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # רשת ביטחון: מספר טלפון שמופיע בשם או בהערה מוסתר גם בדף (D-019, D-111).
    data = PHONE_RE.sub("‹טלפון מוסתר›", data)
    html = open(TEMPLATE, encoding="utf-8").read().replace("/*__DATA__*/null", data)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"{args.out}: {len(html.encode()) / 1e6:.1f}MB")


if __name__ == "__main__":
    main()
