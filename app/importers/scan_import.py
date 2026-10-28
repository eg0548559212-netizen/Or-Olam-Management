"""ייבוא סריקת העץ מ-21.10.2026 (D-103) לטבלאות העץ (01-extensions.md).

הרצה:
    OR_OLAM_DB_KEY=... python3 -m app.importers.scan_import data/scan/or-olam-full-tree-settings.json

הייבוא בונה מחדש את טבלאות העץ מאפס. מאפייני שלוחה (D-015) ומודול התכנון לא נמחקים.
"""
import argparse
import copy
import json
import re
from collections import Counter

from app import crypto
from app.db.connect import connect, now
from app.settings_catalog import HANGUP_WORDS, HIDDEN_KEYS, REDACTED, REF_KEYS, SECRET_KEYS

# שמות הסוגים הידועים מ-/api/sclass-types/ (api-samples.md). השאר מוצגים לפי הקוד.
KNOWN_TYPES = {
    "LMENU": (23, "תפריט מורחב", "שלוחות נפוצות"),
    "MB": (22, "תיבת הודעות (הקלטה / שמיעה / פרסום)", "שלוחות נפוצות"),
    "RTZ": (10, "רישום לצינתוק", "שלוחות נפוצות"),
    "FORM": (21, "שאלון", "שלוחות נפוצות"),
}

# D-117: שמות זמניים לשאר הסוגים, שנגזרו מהסכמה ומשמות השלוחות. מוחלפים בשם הרשמי כשהוא מגיע.
PROVISIONAL_TYPES = {
    "ACAMP": "חיוג (לא מזוהה)", "API": "שלוחת API", "COM": "שינוי הודעת פתיחה", "CONF": "ועידה ושידור חי",
    "DI": "העברה למספר", "DIAL": "חיוג והעברת שיחה", "DIQ": "תור נציגים", "DOC": "שליחת מסמך בפקס או במייל",
    "GT": "מעבר לשלוחה", "HEBD": "לפי תאריך עברי", "LL": "חזרה למיקום אחרון", "ONMSG": "הודעה בודדת",
    "PCRED": "גבייה באשראי", "PD": "הקראת נתונים מגיליון", "PERS": "הודעות אישיות", "RNU": "רישום משתמש",
    "SCORE": "השמעת ניקוד ונתונים", "SEARC": "חיפוש שלוחות", "SENDZ": "קבלת צינתוק", "SMOD": "מודול מיוחד",
    "STARS": "מחסן אישי",
}

UNKNOWN = {"$unknown": "redacted_in_export"}
HIDDEN = {"$hidden": True}

TREE_TABLES = [
    "extension_link WHERE source_kind = 'settings'",
    "extension_setting_ref",
    "extension_sensitive_setting",
    "extension_path_alias",
    "tree_sync_raw",
]


def path_sort(path):
    return ".".join(seg.zfill(5) for seg in path.split("."))


def yes_no_unknown(value):
    if value == REDACTED:
        return "unknown"
    return "yes" if value not in (None, "", []) else "no"


def normalize_phones(value):
    if value in (None, ""):
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


def process_settings(settings, sclass_id, hidden_rows, counters):
    """מחזיר עותק של settings אחרי D-107 (סיסמאות), D-106 (הסתרה) והחלפת [REDACTED].

    ערכים להסתרה נאספים ל-hidden_rows כ-(sclass_id, json_path, value).
    """

    def walk(node, key_path, json_path):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                kp = f"{key_path}.{k}" if key_path else k
                jp = f"{json_path}.{k}"
                if k in SECRET_KEYS:
                    if v == REDACTED:
                        out[k] = {"$secret": "unknown"}
                    else:
                        out[k] = {"$secret": "present" if v not in (None, "") else "absent"}
                    counters["secret"] += 1
                elif kp in HIDDEN_KEYS or k in HIDDEN_KEYS:
                    if v in (None, "", []):
                        out[k] = v
                    elif v == REDACTED:
                        out[k] = UNKNOWN
                    else:
                        hidden_rows.append((sclass_id, jp, v))
                        out[k] = HIDDEN
                        counters["hidden"] += 1
                else:
                    out[k] = walk(v, kp, jp)
            return out
        if isinstance(node, list):
            return [walk(x, f"{key_path}[]", f"{json_path}[{i}]") for i, x in enumerate(node)]
        if node == REDACTED:
            counters["redacted_unknown"] += 1
            return UNKNOWN
        return node

    return walk(settings, "", "$")


def iter_ref_values(settings):
    """מחזיר (setting_key, raw_value) לכל מפתח הפניה, כולל בתוך מערכים ואובייקטים."""

    def walk(node, key_path):
        if isinstance(node, dict):
            for k, v in node.items():
                kp = f"{key_path}.{k}" if key_path else k
                if kp in REF_KEYS and isinstance(v, str):
                    yield kp, v
                else:
                    yield from walk(v, kp)
        elif isinstance(node, list):
            for x in node:
                yield from walk(x, f"{key_path}[]")

    yield from walk(settings or {}, "")


PATH_RE = re.compile(r"^\d+(\.\d+)*$")
REL_RE = re.compile(r"^\./(\d+(\.\d+)*)$")


def parse_target(text, own_path, ref_kind, by_path, by_name):
    """מחזיר (ref_kind, target_form, target_path, target_sclass_id, status)."""
    t = text.strip()
    if ref_kind == "external_line":
        return ref_kind, "absolute" if PATH_RE.match(t) else "unparsed", t, None, "external"
    if t in HANGUP_WORDS:
        return "hangup", "hangup", None, None, "hangup"
    if t == "*":
        return "play_from_all", "all", None, None, "pattern"
    if t.startswith("~"):
        inner = t[1:].strip()
        _, form, tpath, tid, status = parse_target(inner, own_path, ref_kind, by_path, by_name)
        if form in ("absolute", "parent_relative"):
            return "exclude", "exclude", tpath, tid, status
        return "exclude", "exclude", None, None, "unparsed"
    if t.endswith("*") and PATH_RE.match(t[:-1].rstrip(".")):
        return ref_kind, "prefix", t[:-1], None, "pattern"
    m = REL_RE.match(t)
    if m:
        parent = own_path.rsplit(".", 1)[0] if "." in own_path else ""
        tpath = f"{parent}.{m.group(1)}" if parent else m.group(1)
        tid = by_path.get(tpath)
        return ref_kind, "parent_relative", tpath, tid, "ok" if tid else "broken"
    if PATH_RE.match(t):
        tid = by_path.get(t)
        return ref_kind, "absolute", t, tid, "ok" if tid else "broken"
    ids = by_name.get(t, [])
    if len(ids) == 1:
        return ref_kind, "name", None, ids[0], "by_name"
    if len(ids) > 1:
        return ref_kind, "name", None, None, "ambiguous_name"
    return ref_kind, "name", None, None, "broken"


def run(scan_file, db_file):
    scan = json.load(open(scan_file, encoding="utf-8"))
    meta = scan["metadata"]
    db = connect(db_file)
    ts = now()
    counters = Counter()

    cur = db.execute(
        "INSERT INTO tree_sync_run (source, source_ref, started_at, status) VALUES ('scan_import', ?, ?, 'running')",
        (meta.get("scan_id"), ts),
    )
    run_id = cur.lastrowid

    for t in TREE_TABLES:
        db.execute(f"DELETE FROM {t}")

    # ── סוגים וסכמות ──
    for cls_type, entry in scan["schemas"].items():
        api_id, name, group = KNOWN_TYPES.get(cls_type, (None, None, None))
        source = "api" if name else None
        if not name and cls_type in PROVISIONAL_TYPES:
            name, source = PROVISIONAL_TYPES[cls_type], "provisional"
        db.execute(
            """INSERT INTO extension_type (cls_type, api_type_id, name, name_source, type_group, schema_json, schema_source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'scan-2026-10-21', ?)
               ON CONFLICT (cls_type) DO UPDATE SET schema_json = excluded.schema_json,
                 schema_source = excluded.schema_source, updated_at = excluded.updated_at,
                 api_type_id = COALESCE(extension_type.api_type_id, excluded.api_type_id),
                 name = CASE WHEN extension_type.name_source = 'api' THEN extension_type.name ELSE excluded.name END,
                 name_source = CASE WHEN extension_type.name_source = 'api' THEN 'api' ELSE excluded.name_source END,
                 type_group = COALESCE(extension_type.type_group, excluded.type_group)""",
            (cls_type, api_id, name, source, group,
             json.dumps(entry["response"]["data"]["schema"], ensure_ascii=False), ts),
        )

    # ── שלוחות. הנתיב בפרטים הוא העדכני (D-103) ──
    exts = [e["detail_response"]["data"] for e in scan["extensions"]]
    for e in exts:
        e["cls_type"] = (e.get("cls_type") or "").upper() or None
    by_path = {e["path"]: e["id"] for e in exts}
    if len(by_path) != len(exts):
        raise SystemExit("נתיב כפול בפרטי השלוחות")
    by_name = {}
    for e in exts:
        if e.get("name") and e["name"].strip():
            by_name.setdefault(e["name"].strip(), []).append(e["id"])

    seen_ids = set()
    hidden_rows = []
    for e in sorted(exts, key=lambda x: x["path"].count(".")):
        path = e["path"]
        parent_path = path.rsplit(".", 1)[0] if "." in path else None
        parent_id = by_path.get(parent_path) if parent_path else None
        if parent_path and parent_id is None:
            raise SystemExit(f"לא נמצא אב לשלוחה {e['id']} ({path})")
        settings = e.get("settings")
        clean = None
        if settings is not None:
            clean = process_settings(copy.deepcopy(settings), e["id"], hidden_rows, counters)
        phones = normalize_phones(e.get("teacher_phone"))
        sensitive = {
            "teacher_phone": phones,
            "pin_code_extension": None if e.get("pin_code_extension") == REDACTED else e.get("pin_code_extension"),
            "pin_code_admin_only": None if e.get("pin_code_admin_only") == REDACTED else e.get("pin_code_admin_only"),
        }
        s = settings or {}
        db.execute(
            """INSERT INTO extension (sclass_id, parent_sclass_id, path, path_sort, path_segments, depth, "order",
                 name, cls_type, settings, open_msg_id, sf_name_id, sclass_audio_name_id, count_sub_sclass,
                 is_open, required_login, time_active, conf_id, var_bool, sensitive_enc,
                 has_teacher_phone, has_pin_code_extension, has_pin_code_admin_only,
                 first_seen_at, last_synced_at, last_sync_run_id, missing_since)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
               ON CONFLICT (sclass_id) DO UPDATE SET
                 parent_sclass_id=excluded.parent_sclass_id, path=excluded.path, path_sort=excluded.path_sort,
                 path_segments=excluded.path_segments, depth=excluded.depth, "order"=excluded."order",
                 name=excluded.name, cls_type=excluded.cls_type, settings=excluded.settings,
                 open_msg_id=excluded.open_msg_id, sf_name_id=excluded.sf_name_id,
                 sclass_audio_name_id=excluded.sclass_audio_name_id, count_sub_sclass=excluded.count_sub_sclass,
                 is_open=excluded.is_open, required_login=excluded.required_login, time_active=excluded.time_active,
                 conf_id=excluded.conf_id, var_bool=excluded.var_bool, sensitive_enc=excluded.sensitive_enc,
                 has_teacher_phone=excluded.has_teacher_phone, has_pin_code_extension=excluded.has_pin_code_extension,
                 has_pin_code_admin_only=excluded.has_pin_code_admin_only,
                 last_synced_at=excluded.last_synced_at, last_sync_run_id=excluded.last_sync_run_id,
                 missing_since=NULL""",
            (
                e["id"], parent_id, path, path_sort(path), json.dumps(path.split(".")), path.count("."),
                e.get("order"), e.get("name"), e.get("cls_type"),
                json.dumps(clean, ensure_ascii=False) if clean is not None else None,
                e.get("open_msg_id"), e.get("sf_name_id"), e.get("sclass_audio_name_id"), e.get("count_sub_sclass"),
                None if "is_open" not in s else int(bool(s["is_open"])),
                None if "required_login" not in s else json.dumps(s["required_login"], ensure_ascii=False),
                None if "time_active" not in s else json.dumps(s["time_active"], ensure_ascii=False),
                e.get("conf_id"), None if e.get("var_bool") is None else int(bool(e["var_bool"])),
                crypto.encrypt(sensitive),
                "yes" if phones else "no",
                yes_no_unknown(e.get("pin_code_extension")),
                yes_no_unknown(e.get("pin_code_admin_only")),
                ts, ts, run_id,
            ),
        )
        seen_ids.add(e["id"])

        # שכבת הגולמי: אחרי סינון D-107, מוצפנת כולה.
        raw = {k: (UNKNOWN if v == REDACTED else v) for k, v in e.items()}
        if raw.get("settings") is not None:
            raw["settings"] = process_settings(copy.deepcopy(e["settings"]), e["id"], [], Counter())
            # בגולמי ערכי D-106 נשמרים (מוצפנים יחד עם כל התגובה), ולכן מחזירים אותם.
            raw["settings"] = _restore_hidden(raw["settings"], e["settings"])
        db.execute(
            "INSERT INTO tree_sync_raw (run_id, sclass_id, fetched_at, payload_enc) VALUES (?, ?, ?, ?)",
            (run_id, e["id"], None, crypto.encrypt(raw)),
        )

    # שלוחות שלא חזרו בייבוא הזה
    missing = db.execute(
        f"UPDATE extension SET missing_since = ? WHERE missing_since IS NULL AND sclass_id NOT IN ({','.join('?' * len(seen_ids))})",
        (ts, *seen_ids),
    ).rowcount

    db.executemany(
        "INSERT INTO extension_sensitive_setting (sclass_id, json_path, value_enc) VALUES (?, ?, ?)",
        [(sid, jp, crypto.encrypt(v)) for sid, jp, v in hidden_rows],
    )

    # ── כינויי נתיב: הנתיב במבנה שונה מהנתיב בפרטים ──
    for disc in scan.get("observed_detail_discrepancies", []):
        if disc.get("field") == "path":
            db.execute(
                "INSERT OR IGNORE INTO extension_path_alias (old_path, sclass_id, detected_at, source) VALUES (?, ?, ?, 'scan_structure_vs_detail')",
                (disc["structure_value"], disc["id"], disc.get("detail_received_at") or ts),
            )

    # ── הפניות מתוך settings ──
    refs = []
    for e in exts:
        for key, raw_value in iter_ref_values(e.get("settings")):
            ref_kind = REF_KEYS[key]
            for part in raw_value.split(","):
                if not part.strip():
                    continue
                kind, form, tpath, tid, status = parse_target(part, e["path"], ref_kind, by_path, by_name)
                refs.append((e["id"], key, raw_value, kind, part.strip(), form, tpath, tid, status, ts))
    db.executemany(
        """INSERT INTO extension_setting_ref (sclass_id, setting_key, raw_value, ref_kind, target_text, target_form,
             target_path, target_sclass_id, status, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        refs,
    )
    db.execute(
        """INSERT INTO extension_link (from_sclass_id, to_sclass_id, link_kind, source_kind)
           SELECT DISTINCT sclass_id, target_sclass_id, ref_kind, 'settings' FROM extension_setting_ref
           WHERE target_sclass_id IS NOT NULL AND ref_kind <> 'exclude' AND sclass_id <> target_sclass_id"""
    )

    # ── בדיקת שלמות: count_sub_sclass מול הילדים בפועל ──
    mismatches = db.execute(
        """SELECT COUNT(*) FROM extension e WHERE e.count_sub_sclass IS NOT NULL AND e.count_sub_sclass <>
             (SELECT COUNT(*) FROM extension c WHERE c.parent_sclass_id = e.sclass_id)"""
    ).fetchone()[0]

    log = {
        "extensions": len(exts),
        "missing_marked": missing,
        "aliases": db.execute("SELECT COUNT(*) FROM extension_path_alias").fetchone()[0],
        "hidden_values": counters["hidden"],
        "secret_keys_dropped": counters["secret"],
        "redacted_to_unknown": counters["redacted_unknown"],
        "refs": len(refs),
        "refs_from_extensions": len({r[0] for r in refs}),
        "refs_by_status": dict(Counter(r[8] for r in refs)),
        "absolute_broken": sum(1 for r in refs if r[5] == "absolute" and r[8] == "broken"),
        "count_sub_sclass_mismatches": mismatches,
    }
    db.execute(
        "UPDATE tree_sync_run SET finished_at = ?, status = 'ok', node_count = ?, completeness_ok = ?, log = ? WHERE run_id = ?",
        (now(), len(exts), int(mismatches == 0), json.dumps(log, ensure_ascii=False), run_id),
    )
    db.commit()
    return log


def _restore_hidden(clean, original):
    """בשכבת הגולמי מחזירים את ערכי D-106 (היא מוצפנת כולה), ומשאירים את סינון D-107."""
    if isinstance(clean, dict) and isinstance(original, dict):
        return {k: (original[k] if v == HIDDEN else _restore_hidden(v, original.get(k))) for k, v in clean.items()}
    if isinstance(clean, list) and isinstance(original, list):
        return [_restore_hidden(c, o) for c, o in zip(clean, original)]
    return clean


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scan_file")
    ap.add_argument("--db", default="data/or-olam.db")
    args = ap.parse_args()
    print(json.dumps(run(args.scan_file, args.db), ensure_ascii=False, indent=2))
