"""מיפוי אוטומטי ראשוני (D-008) ודוח פערים (D-009) לטיוטת העבודה מול העץ הנוכחי.

הרצה:
    python3 -m app.planning.analyze

- המיפוי חל על צמתים "קיימים" (שחורים) בלבד, מלמעלה למטה.
  צומת שהאב שלו מופה מחפש מועמדים קודם בתוך הענף של השלוחה של האב.
- האותות (signals): דמיון שם, ספרה זהה, נתיב זהה, שם מרצה מהטקסט, והקשר האב.
- ביטחון 0.75 ומעלה, בלי מועמד שני קרוב ובלי התנגשות: suggested. אחרת: needs_decision (התור "דורש החלטה").
  מתחת ל-0.4 לא נוצרת שורה, והצומת נכנס לדוח כ-existing_not_found.
- הרצה חוזרת מוחקת רק שורות אוטומטיות שלא הוכרעו. שורות approved / rejected נשמרות.
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from app.db.connect import connect, now

SUGGEST_THRESHOLD = 0.75
MIN_CONFIDENCE = 0.4
AMBIGUITY_MARGIN = 0.05
GENERIC_NAME_COUNT = 5
PATTERN_DIGITS = ["7", "8", "9", "0"]
GENERIC_PREFIXES = ("שיעורים", "הודעות", "חזרה", "צינתוק", "רישום", "קבלת צינתוק", "מקום אחרון", "הקלטת שיעורים", "עדכונים", "ארכיון", "הסבר")


def norm(text):
    text = (text or "").replace('"', "").replace("'", "").replace("״", "").replace("׳", "")
    text = re.sub(r"[()\[\]\-–_.,:/?!]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb)
    seq = 0.85 * SequenceMatcher(None, a, b).ratio()
    short, long_ = sorted((a, b), key=len)
    ratio = len(short) / len(long_)
    contain = 0.6 + 0.3 * ratio if short in long_ and (len(short.split()) >= 2 or ratio > 0.6) else 0.0
    return max(jac, seq, contain)


def name_sim(title, name):
    """דמיון שם. משווה גם בלי הסוגריים (בטיוטה הם בדרך כלל שם המרצה)."""
    bare = re.sub(r"\([^)]*\)", " ", title or "")
    return max(_sim(title, name), _sim(bare, name))


def load(db, draft_id):
    nodes = {r["node_id"]: dict(r) for r in db.execute("SELECT * FROM draft_node WHERE draft_id = ? ORDER BY position", (draft_id,))}
    for n in nodes.values():
        n["hints"] = json.loads(n["hints"]) if n["hints"] else {}
    exts = {r["sclass_id"]: dict(r) for r in db.execute(
        "SELECT sclass_id, parent_sclass_id, path, name, cls_type, \"order\" FROM extension WHERE missing_since IS NULL")}
    return nodes, exts


def descendants(exts, children, root_id, max_depth):
    out, frontier = [], [(root_id, 0)]
    while frontier:
        sid, d = frontier.pop()
        if d >= max_depth:
            continue
        for c in children.get(sid, []):
            out.append(c)
            frontier.append((c, d + 1))
    return out


def automap(db, plan_id, draft_id):
    nodes, exts = load(db, draft_id)
    children = defaultdict(list)
    for e in exts.values():
        children[e["parent_sclass_id"]].append(e["sclass_id"])
    name_freq = Counter(norm(e["name"]) for e in exts.values() if e["name"])
    by_path = {e["path"]: sid for sid, e in exts.items()}

    db.execute(
        """DELETE FROM mapping_source WHERE mapping_id IN
             (SELECT mapping_id FROM mapping_row WHERE plan_id = ? AND decided_by IS NULL AND status IN ('suggested','needs_decision'))""",
        (plan_id,))
    db.execute(
        """DELETE FROM mapping_target WHERE mapping_id IN
             (SELECT mapping_id FROM mapping_row WHERE plan_id = ? AND decided_by IS NULL AND status IN ('suggested','needs_decision'))""",
        (plan_id,))
    db.execute("DELETE FROM mapping_row WHERE plan_id = ? AND decided_by IS NULL AND status IN ('suggested','needs_decision')", (plan_id,))
    decided = {r["node_id"]: r["sclass_id"] for r in db.execute(
        """SELECT t.node_id, s.sclass_id FROM mapping_row m JOIN mapping_target t USING (mapping_id)
           JOIN mapping_source s USING (mapping_id) WHERE m.plan_id = ? AND m.status = 'approved'""", (plan_id,))}

    mapped = dict(decided)  # node_id → sclass_id (גם הצעות, לצורך הקשר האב)
    results = {}
    # שורש הטיוטה ("תפריט ראשי") הוא השלוחה 1.
    for n in nodes.values():
        if n["parent_node_id"] is None and n["full_path"] in by_path:
            mapped.setdefault(n["node_id"], by_path[n["full_path"]])

    claimed = set(decided.values()) | set(mapped.values())  # כולל השורש, שמופה לפי הנתיב
    weak = {}  # צמתים בלי התאמה מעל הסף: המועמדים החלשים, להצגה בדוח
    for n in nodes.values():  # לפי position: אב לפני ילד
        if n["node_id"] in decided or n["parent_node_id"] is None:
            continue
        if n["node_status"] != "existing" or n["node_kind"] != "extension" or not n["title"]:
            continue
        parent_sid = mapped.get(n["parent_node_id"])
        scope = set(descendants(exts, children, parent_sid, 2)) if parent_sid else set()
        generic = name_freq[norm(n["title"])] >= GENERIC_NAME_COUNT or any(
            norm(n["title"]).startswith(w) for w in GENERIC_PREFIXES)
        lecturers = [norm(x) for x in n["hints"].get("lecturers_text", [])]
        scored = []
        # שם גנרי ("שיעורים", "חזרה") מחפשים רק בענף של האב. שם ייחודי מחפשים בכל העץ, עם בונוס לענף.
        pool = scope if generic else exts.keys()
        for sid in pool:
            if sid in claimed:
                continue
            e = exts[sid]
            ns = name_sim(n["title"], e["name"])
            lect = any(l and len(l) >= 3 and l in norm(e["name"]) for l in lecturers)
            in_scope = sid in scope
            if ns < 0.3 and not in_scope and not lect:
                continue
            digit_match = n["digit"] is not None and e["path"].rsplit(".", 1)[-1] == n["digit"]
            path_match = n["full_path"] is not None and e["path"] == n["full_path"]
            direct = parent_sid is not None and e["parent_sclass_id"] == parent_sid
            if generic:
                conf = 0.35 * ns + 0.35 * digit_match + 0.15 * direct + 0.1 + 0.05 * lect
                if digit_match and direct and ns >= 0.25:
                    conf = max(conf, 0.76)
            else:
                conf = 0.75 * ns + 0.15 * lect + 0.1 * in_scope + 0.05 * digit_match + 0.05 * path_match
                if not in_scope and name_freq[norm(e["name"])] >= GENERIC_NAME_COUNT:
                    conf *= 0.6
            scored.append((conf, sid, {"name_sim": round(ns, 3), "digit_match": digit_match, "path_match": path_match,
                                       "lecturer_match": lect, "in_parent_branch": in_scope, "generic_name": generic}))
        if not scored:
            continue
        scored.sort(key=lambda x: -x[0])
        best = scored[0]
        # מועמדים נוספים, להצגה כחלופות במסך ההחלטה
        alts = [{"sclass_id": c[1], "confidence": round(c[0], 3), **c[2]} for c in scored[1:4] if c[0] >= 0.25]
        if best[0] < MIN_CONFIDENCE:
            weak[n["node_id"]] = [{"sclass_id": c[1], "confidence": round(c[0], 3), **c[2]} for c in scored[:3] if c[0] >= 0.25]
            continue
        runner = scored[1] if len(scored) > 1 else None
        signals = dict(best[2], candidates=alts)
        if runner:
            signals["runner_up"] = {"sclass_id": runner[1], "path": exts[runner[1]]["path"], "confidence": round(runner[0], 3)}
        ambiguous = runner is not None and best[0] - runner[0] < AMBIGUITY_MARGIN
        depends = best[2]["in_parent_branch"] and n["parent_node_id"] in results and (
            results[n["parent_node_id"]]["pending"])
        conf = round(min(best[0], 1.0), 3)
        pending = ambiguous or depends or conf < SUGGEST_THRESHOLD
        results[n["node_id"]] = {"sclass_id": best[1], "confidence": conf, "signals": signals,
                                 "ambiguous": ambiguous, "depends": depends, "pending": pending}
        mapped[n["node_id"]] = best[1]
        claimed.add(best[1])

    # התנגשות: אותה שלוחה הוצעה לכמה צמתים
    claims = Counter(r["sclass_id"] for r in results.values())
    ts = now()
    stats = Counter()
    for node_id, r in results.items():
        n, e = nodes[node_id], exts[r["sclass_id"]]
        reasons = []
        if r["ambiguous"]:
            reasons.append("מועמד שני קרוב")
        if claims[r["sclass_id"]] > 1:
            reasons.append("אותה שלוחה הוצעה לכמה צמתים")
        if r["depends"]:
            reasons.append("תלוי בהחלטה על האב")
        if r["confidence"] < SUGGEST_THRESHOLD:
            reasons.append("ביטחון מתחת לסף")
        status = "needs_decision" if reasons else "suggested"
        relation = "keep" if e["path"] == n["full_path"] else "move"
        signals = dict(r["signals"], reasons=reasons)
        mid = db.execute(
            """INSERT INTO mapping_row (plan_id, relation, effective_from, confidence, signals, status)
               VALUES (?, ?, NULL, ?, ?, ?)""",
            (plan_id, relation, r["confidence"], json.dumps(signals, ensure_ascii=False), status),
        ).lastrowid
        db.execute("INSERT INTO mapping_source (mapping_id, sclass_id, path, name) VALUES (?, ?, ?, ?)",
                   (mid, e["sclass_id"], e["path"], e["name"]))
        db.execute("INSERT INTO mapping_target (mapping_id, node_id, new_sclass_id) VALUES (?, ?, NULL)", (mid, node_id))
        stats[status] += 1
        stats[relation] += 1
    candidates = sum(1 for n in nodes.values() if n["parent_node_id"] is not None and n["node_status"] == "existing"
                     and n["node_kind"] == "extension" and n["title"])
    return {"existing_nodes": candidates, "mapped": len(results), "not_found": candidates - len(results) - len(decided),
            **stats}, weak


def gap_report(db, plan_id, draft_id, weak=None):
    nodes, exts = load(db, draft_id)
    ts = now()
    report_id = db.execute(
        "INSERT INTO gap_report (draft_id, compared_to, created_at) VALUES (?, 'current_tree', ?)", (draft_id, ts)
    ).lastrowid
    items = []

    def add(kind, severity, node_id=None, sclass_id=None, **detail):
        items.append((report_id, kind, severity, node_id, sclass_id, json.dumps(detail, ensure_ascii=False)))

    maps = {}
    for r in db.execute(
        """SELECT m.mapping_id, m.relation, m.confidence, m.status, m.signals, t.node_id, s.sclass_id, s.path, s.name
           FROM mapping_row m JOIN mapping_target t USING (mapping_id) JOIN mapping_source s USING (mapping_id)
           WHERE m.plan_id = ? AND m.status <> 'rejected'""", (plan_id,)):
        maps[r["node_id"]] = dict(r)
    mapped_sids = {m["sclass_id"]: m for m in maps.values()}

    kids = defaultdict(list)
    for n in nodes.values():
        if n["parent_node_id"]:
            kids[n["parent_node_id"]].append(n)

    for n in nodes.values():
        nid = n["node_id"]
        if n["parent_node_id"] is None:
            continue
        label = {"path": n["full_path"], "title": n["title"]}
        if n["node_status"] in ("new", "new_special_build"):
            add("added", "info", nid, None, **label, node_status=n["node_status"])
        elif n["node_kind"] == "extension" and n["title"] and nid not in maps:
            add("existing_not_found", "warning", nid, None, **label, candidates=(weak or {}).get(nid, []))
        if n["digit"] is None:
            add("unnumbered", "warning", nid, None, **label)
        stated = n["hints"].get("stated_path")
        if stated and n["full_path"] and stated != n["full_path"]:
            add("number_conflict", "warning", nid, None, **label, reason="stated_path_mismatch", stated_path=stated)
        m = maps.get(nid)
        if m:
            if m["relation"] == "move":
                add("moved", "info", nid, m["sclass_id"], old_path=m["path"], new_path=n["full_path"], title=n["title"],
                    mapping_status=m["status"])
            if name_sim(n["title"], m["name"]) < 0.9:
                add("renamed", "info", nid, m["sclass_id"], old_name=m["name"], new_title=n["title"], path=n["full_path"])

    # ספרה כפולה תחת אותו אב
    for parent_id, ch in kids.items():
        digits = Counter(c["digit"] for c in ch if c["digit"] is not None)
        for d, cnt in digits.items():
            if cnt > 1:
                for c in ch:
                    if c["digit"] == d:
                        add("number_conflict", "error", c["node_id"], None, path=c["full_path"], title=c["title"],
                            reason="duplicate_digit", digit=d)
        # D-016: אזהרה בלבד
        if "1" in digits and len(ch) >= 2:
            missing = [d for d in PATTERN_DIGITS if d not in digits]
            if missing:
                p = nodes[parent_id]
                add("pattern_deviation", "warning", parent_id, None, path=p["full_path"], title=p["title"], missing=missing)

    # שלוחות בעץ הנוכחי שלא מופיעות בטיוטה: רק הראש של כל ענף שלא מופה, עם מספר הצאצאים.
    children = defaultdict(list)
    for e in exts.values():
        children[e["parent_sclass_id"]].append(e["sclass_id"])

    def count_desc(sid):
        return sum(1 + count_desc(c) for c in children.get(sid, []))

    root_ids = {sid for sid, e in exts.items() if e["parent_sclass_id"] is None}
    main_root = next((sid for sid in root_ids if exts[sid]["path"] == "1"), None)

    def walk(sid):
        for c in children.get(sid, []):
            if c in mapped_sids:
                walk(c)
            else:
                e = exts[c]
                add("current_not_planned", "warning", None, c, path=e["path"], name=e["name"], cls_type=e["cls_type"],
                    descendants=count_desc(c), listens=None)

    for r in sorted(root_ids, key=lambda s: exts[s]["path"]):
        if r == main_root:
            walk(r)
        else:
            e = exts[r]
            add("current_not_planned", "info", None, r, path=e["path"], name=e["name"], cls_type=e["cls_type"],
                descendants=count_desc(r), listens=None, note="שורש מחוץ לתפריט הראשי")

    # הפניות ב-settings לשלוחה שהנתיב שלה ישתנה
    for ref in db.execute(
        """SELECT r.sclass_id, r.setting_key, r.target_text, r.target_sclass_id, e.path AS from_path
           FROM extension_setting_ref r JOIN extension e USING (sclass_id)
           WHERE r.target_sclass_id IS NOT NULL AND r.target_form IN ('absolute', 'parent_relative', 'exclude')"""):
        m = mapped_sids.get(ref["target_sclass_id"])
        if m and m["relation"] == "move":
            add("ref_needs_rewrite", "warning", m["node_id"], ref["sclass_id"], from_path=ref["from_path"],
                setting_key=ref["setting_key"], old_target=ref["target_text"], new_target=nodes[m["node_id"]]["full_path"])

    for note in db.execute(
        "SELECT note_id, node_id, scope, text FROM draft_note WHERE draft_id = ? AND note_kind = 'question' AND status = 'open'",
        (draft_id,)):
        add("open_note", "info", note["node_id"], None, note_id=note["note_id"], scope=note["scope"], text=note["text"])

    db.executemany(
        "INSERT INTO gap_item (report_id, gap_kind, severity, draft_node_id, sclass_id, detail) VALUES (?,?,?,?,?,?)", items)
    return {"report_id": report_id, "items": len(items), "by_kind": dict(Counter(i[1] for i in items))}


def validate_current_tree(db):
    """בדיקות תקינות על העץ הנוכחי: הפניות שבורות ודפוס 1/7/8/9/0 (אזהרה, D-016)."""
    ts = now()
    db.execute("DELETE FROM validation_result WHERE target LIKE 'sclass:%'")
    rows = []
    for r in db.execute(
        """SELECT r.sclass_id, e.path, r.setting_key, r.target_text FROM extension_setting_ref r
           JOIN extension e USING (sclass_id) WHERE r.status = 'broken'"""):
        rows.append(("broken_ref", f"sclass:{r['sclass_id']}", "warning",
                     f"{r['path']}: {r['setting_key']} מפנה אל '{r['target_text']}', שלא קיים בעץ", ts))
    kids = defaultdict(set)
    for r in db.execute("SELECT parent_sclass_id, \"order\" FROM extension WHERE parent_sclass_id IS NOT NULL AND missing_since IS NULL"):
        kids[r["parent_sclass_id"]].add(str(r["order"]))
    for r in db.execute("SELECT sclass_id, path FROM extension WHERE missing_since IS NULL"):
        ch = kids.get(r["sclass_id"], set())
        if "1" in ch and len(ch) >= 2:
            missing = [d for d in PATTERN_DIGITS if d not in ch]
            if missing:
                rows.append(("pattern_17890", f"sclass:{r['sclass_id']}", "warning",
                             f"{r['path']}: חסרות הספרות {', '.join(missing)} בדפוס 1/7/8/9/0", ts))
    db.executemany("INSERT INTO validation_result (rule_id, target, severity, message, computed_at) VALUES (?,?,?,?,?)", rows)
    return dict(Counter(r[0] for r in rows))


def run(db_file):
    db = connect(db_file)
    plan = db.execute("SELECT plan_id FROM migration_plan ORDER BY plan_id LIMIT 1").fetchone()
    if not plan:
        raise SystemExit("אין תכנית. קודם מייבאים טיוטה.")
    plan_id = plan["plan_id"]
    draft_id = db.execute("SELECT draft_id FROM draft WHERE plan_id = ? AND kind = 'working'", (plan_id,)).fetchone()[0]
    stats, weak = automap(db, plan_id, draft_id)
    out = {"automap": stats, "gap_report": gap_report(db, plan_id, draft_id, weak),
           "validation_current_tree": validate_current_tree(db)}
    db.commit()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/or-olam.db")
    print(json.dumps(run(ap.parse_args().db), ensure_ascii=False, indent=2))
