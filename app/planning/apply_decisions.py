"""מחיל את החלטות השיוך שנשמרו בדף ההצגה על mapping_row (06-planning.md, D-008, D-113).

ההחלטות נשמרות ב-db של הדף, באוסף decisions (מסמך לכל node_id). Claude מוריד אותן לתיקייה
(ArtifactData list עם out_dir), והסקריפט קורא את קובצי ה-JSON משם:
    python3 -m app.planning.apply_decisions data/decisions/decisions

פעולות בדף → mapping_row:
- same    → keep (אותו נתיב) או move (נתיב אחר)
- rebuild / merge / split → אותו relation
- none    → שלוחה חדשה: כל שורה אוטומטית של הצומת מסומנת rejected, ואין שורה מאושרת
- routing → (ישן, D-116, הוחלף ב-D-118) נקרא כסוג "תפריט מורחב" (LMENU), שלא צריך שיוך
draft_node.link_status מתעדכן: none → new, same/split → existing, merge → merged.

מאפייני צומת (D-118) נשמרים באוסף nodeprops ומוחלים עם --props <תיקייה>:
planned_type → draft_node.planned_cls_type, special_build → draft_node.special_build,
notes (D-120, רשימת הערות ערוכה) → מחליפה את הערות הצומת ב-draft_note,
title (D-122, כותרת ערוכה) → draft_node.title.
עריכת מבנה (D-124), מוחלת לפני ההחלטות כדי שצמתים חדשים יהיו קיימים:
created → צומת חדש ב-draft_node (node_status = new), parent_id / digit → מיקום חדש,
deleted → draft_node.deleted לצומת ולכל הצאצאים שלו. אחר כך full_path מחושב מחדש,
שורות מיפוי של צמתים שנמחקו נדחות, ו-keep / move מחושב מחדש לפי הנתיב החדש.
הרצה חוזרת של המיפוי האוטומטי לא נוגעת בשורות approved / rejected.
"""
import argparse
import glob
import json
import os

from app.db.connect import connect


def run(src_dir, db_file):
    db = connect(db_file)
    plan_id = db.execute("SELECT plan_id FROM migration_plan ORDER BY plan_id LIMIT 1").fetchone()[0]
    draft_id = db.execute("SELECT draft_id FROM draft WHERE plan_id = ? AND kind = 'working'", (plan_id,)).fetchone()[0]
    applied, skipped = 0, []
    for f in sorted(glob.glob(os.path.join(src_dir, "*.json"))):
        doc = json.load(open(f, encoding="utf-8"))
        d = doc.get("data", doc)
        node_id, action, ids = int(d["node_id"]), d.get("action"), [int(x) for x in d.get("sclass_ids") or []]
        node = db.execute("SELECT full_path FROM draft_node WHERE draft_id = ? AND node_id = ?", (draft_id, node_id)).fetchone()
        if node is None or action not in ("same", "rebuild", "merge", "split", "none", "routing"):
            skipped.append(os.path.basename(f))
            continue
        old = [r[0] for r in db.execute(
            "SELECT mapping_id FROM mapping_row JOIN mapping_target USING (mapping_id) WHERE plan_id = ? AND node_id = ?",
            (plan_id, node_id))]
        for mid in old:
            db.execute("UPDATE mapping_row SET status = 'rejected', decided_by = ?, decided_at = ? WHERE mapping_id = ?",
                       (d.get("decided_by"), d.get("decided_at"), mid))
        if action == "routing":
            db.execute("UPDATE draft_node SET planned_cls_type = COALESCE(planned_cls_type, 'LMENU'), link_status = 'unset' "
                       "WHERE draft_id = ? AND node_id = ?", (draft_id, node_id))
        else:
            status = {"none": "new", "same": "existing", "split": "existing", "rebuild": "existing", "merge": "merged"}[action]
            db.execute("UPDATE draft_node SET link_status = ? WHERE draft_id = ? AND node_id = ?", (status, draft_id, node_id))
        if action not in ("none", "routing"):
            exts = [db.execute("SELECT sclass_id, path, name FROM extension WHERE sclass_id = ?", (i,)).fetchone() for i in ids]
            exts = [e for e in exts if e]
            relation = action
            if action == "same":
                relation = "keep" if exts and exts[0]["path"] == node["full_path"] else "move"
            mid = db.execute(
                """INSERT INTO mapping_row (plan_id, relation, confidence, signals, status, decided_by, decided_at, note)
                   VALUES (?, ?, 1, ?, 'approved', ?, ?, ?)""",
                (plan_id, relation, json.dumps({"source": d.get("source", "manual")}), d.get("decided_by"),
                 d.get("decided_at"), d.get("note") or None),
            ).lastrowid
            for e in exts:
                db.execute("INSERT INTO mapping_source (mapping_id, sclass_id, path, name) VALUES (?, ?, ?, ?)",
                           (mid, e["sclass_id"], e["path"], e["name"]))
            db.execute("INSERT INTO mapping_target (mapping_id, node_id) VALUES (?, ?)", (mid, node_id))
        applied += 1
    db.commit()
    return {"applied": applied, "skipped": skipped}


def _docs(src_dir):
    out = []
    for f in sorted(glob.glob(os.path.join(src_dir, "*.json"))):
        doc = json.load(open(f, encoding="utf-8"))
        d = doc.get("data", doc)
        if d.get("node_id") is not None:
            out.append(d)
    return out


def _working_draft(db):
    return db.execute("SELECT draft_id FROM draft WHERE kind = 'working' ORDER BY draft_id LIMIT 1").fetchone()[0]


def apply_structure(src_dir, db_file):
    """D-124: צמתים חדשים, הזזות ומחיקות מ-nodeprops."""
    db = connect(db_file)
    if "deleted" not in [r[1] for r in db.execute("PRAGMA table_info(draft_node)")]:
        db.execute("ALTER TABLE draft_node ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    draft_id = _working_draft(db)
    docs = sorted(_docs(src_dir), key=lambda d: int(d["node_id"]))
    pos = db.execute("SELECT COALESCE(MAX(position), 0) FROM draft_node WHERE draft_id = ?", (draft_id,)).fetchone()[0]
    created = moved = 0
    for d in docs:
        nid = int(d["node_id"])
        exists = db.execute("SELECT 1 FROM draft_node WHERE draft_id = ? AND node_id = ?", (draft_id, nid)).fetchone()
        if d.get("created") and not exists:
            pos += 1
            db.execute(
                """INSERT INTO draft_node (draft_id, node_id, parent_node_id, position, digit, raw_text, title,
                                           node_status, node_kind, link_status)
                   VALUES (?, ?, ?, ?, ?, '', ?, 'new', 'extension', 'new')""",
                (draft_id, nid, d.get("parent_id"), pos, d.get("digit"), d.get("title") or ""))
            created += 1
        elif exists and ("parent_id" in d or "digit" in d):
            sets, args = [], []
            if "parent_id" in d:
                sets.append("parent_node_id = ?"); args.append(d["parent_id"])
            if "digit" in d:
                sets.append("digit = ?"); args.append(d["digit"])
            db.execute(f"UPDATE draft_node SET {', '.join(sets)} WHERE draft_id = ? AND node_id = ?", (*args, draft_id, nid))
            moved += 1
    rows = db.execute("SELECT node_id, parent_node_id, digit FROM draft_node WHERE draft_id = ?", (draft_id,)).fetchall()
    kids = {}
    for r in rows:
        kids.setdefault(r["parent_node_id"], []).append(r)
    # נתיב מלא מחדש. NULL אם לאחד האבות אין ספרה
    def walk(parent, base):
        for r in kids.get(parent, []):
            path = r["digit"] if parent is None else (f"{base}.{r['digit']}" if base and r["digit"] is not None else None)
            db.execute("UPDATE draft_node SET full_path = ? WHERE draft_id = ? AND node_id = ?", (path, draft_id, r["node_id"]))
            walk(r["node_id"], path)
    walk(None, None)
    # מחיקה: הצומת וכל הצאצאים שלו. מצב הדף מלא, ולכן מתחילים מאפס
    db.execute("UPDATE draft_node SET deleted = 0 WHERE draft_id = ?", (draft_id,))
    gone = []
    def mark(nid):
        gone.append(nid)
        for r in kids.get(nid, []):
            mark(r["node_id"])
    for d in docs:
        if d.get("deleted"):
            mark(int(d["node_id"]))
    for nid in gone:
        db.execute("UPDATE draft_node SET deleted = 1 WHERE draft_id = ? AND node_id = ?", (draft_id, nid))
    db.commit()
    return {"created": created, "moved": moved, "deleted": len(set(gone))}


def finalize_structure(db_file):
    """אחרי ההחלטות: שורות מיפוי של צמתים שנמחקו נדחות, ו-keep / move לפי הנתיב החדש."""
    db = connect(db_file)
    draft_id = _working_draft(db)
    db.execute("""UPDATE mapping_row SET status = 'rejected' WHERE status != 'rejected' AND mapping_id IN (
                    SELECT t.mapping_id FROM mapping_target t JOIN draft_node n ON n.node_id = t.node_id
                    WHERE n.draft_id = ? AND n.deleted = 1)""", (draft_id,))
    for r in db.execute("""SELECT m.mapping_id, m.relation, s.path, n.full_path FROM mapping_row m
                           JOIN mapping_target t USING (mapping_id) JOIN mapping_source s USING (mapping_id)
                           JOIN draft_node n ON n.node_id = t.node_id AND n.draft_id = ?
                           WHERE m.status = 'approved' AND m.relation IN ('keep', 'move')""", (draft_id,)).fetchall():
        rel = "keep" if r["path"] == r["full_path"] else "move"
        if rel != r["relation"]:
            db.execute("UPDATE mapping_row SET relation = ? WHERE mapping_id = ?", (rel, r["mapping_id"]))
    db.commit()


def apply_props(src_dir, db_file):
    db = connect(db_file)
    draft_id = db.execute("SELECT draft_id FROM draft WHERE kind = 'working' ORDER BY draft_id LIMIT 1").fetchone()[0]
    n = 0
    for f in sorted(glob.glob(os.path.join(src_dir, "*.json"))):
        doc = json.load(open(f, encoding="utf-8"))
        d = doc.get("data", doc)
        sets, args = [], []
        if "planned_type" in d:
            sets.append("planned_cls_type = ?"); args.append(d["planned_type"] or None)
        if "title" in d:
            # D-122: כותרת ערוכה. null מחזיר לכותרת מהטיוטה, שנשמרת ב-raw_text
            sets.append("title = COALESCE(?, title)"); args.append(d["title"] or None)
        if "special_build" in d:
            sets.append("special_build = ?"); args.append(int(bool(d["special_build"])))
        if sets:
            db.execute(f"UPDATE draft_node SET {', '.join(sets)} WHERE draft_id = ? AND node_id = ?",
                       (*args, draft_id, int(d["node_id"])))
        if isinstance(d.get("notes"), list):
            db.execute("DELETE FROM draft_note WHERE draft_id = ? AND node_id = ?", (draft_id, int(d["node_id"])))
            for text in d["notes"]:
                db.execute(
                    """INSERT INTO draft_note (draft_id, node_id, scope, note_kind, text, status, author, created_at)
                       VALUES (?, ?, 'node', 'comment', ?, 'open', ?, ?)""",
                    (draft_id, int(d["node_id"]), text, d.get("updated_by"), d.get("updated_at")))
        if sets or isinstance(d.get("notes"), list):
            n += 1
    db.commit()
    return {"props_applied": n}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src_dir")
    ap.add_argument("--props", help="תיקיית nodeprops (D-118)")
    ap.add_argument("--db", default="data/or-olam.db")
    a = ap.parse_args()
    out = apply_structure(a.props, a.db) if a.props else {}
    out.update(run(a.src_dir, a.db))
    if a.props:
        out.update(apply_props(a.props, a.db))
        finalize_structure(a.db)
    print(json.dumps(out, ensure_ascii=False, indent=2))
