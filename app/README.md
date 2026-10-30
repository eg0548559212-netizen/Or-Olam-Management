# app · בסיס הנתונים של העץ ומודול התכנון

בנייה ראשונית לפי D-109. בסיס הנתונים הוא SQLite (D-110). המבנה מתועד ב-`docs/data-model/01-extensions.md` וב-`06-planning.md`, והמסמכים שם הם מקור האמת.

## דרישות

- Python 3.11 ומעלה, עם `cryptography` ו-`python-docx`.
- מפתח הצפנה במשתנה הסביבה `OR_OLAM_DB_KEY`. ליצירת מפתח: `python3 -m app.crypto`. שומרים אותו ב-`.env` (מוחרג מהמאגר). **בלי המפתח אי אפשר לקרוא את השדות המוצפנים.**

## חבילת סשן (D-123)

בתחילת סשן: `python3 -m app.session_package restore <zip>`. בסוף סשן: `python3 -m app.session_package save`. הקובץ נשמר אצל המשתמשת, כי הוא כולל את המפתח.

## בנייה מאפס

הקבצים לא נמצאים במאגר. מניחים אותם תחת `data/`:
- `data/scan/or-olam-full-tree-settings.json`: מתוך `or-olam-complete-export.zip` (סריקת 21.10).
- `data/draft/draft-2026-10-21.docx`: הטיוטה מ-21.10, בייצוא docx מ-Google Docs, כדי לשמור על הצבעים.

```bash
set -a; . ./.env; set +a
rm -f data/or-olam.db
python3 -m app.importers.scan_import data/scan/or-olam-full-tree-settings.json
python3 -m app.importers.types_import data/api/sclass-types-2026-10-28.json   # D-119
python3 -m app.importers.draft_import data/draft/draft-2026-10-21.docx
python3 -m app.planning.analyze
python3 -m app.demo.build      # דף ההצגה: data/demo/or-olam-demo.html
```

## מה כל שלב עושה

| שלב | קובץ | טבלאות |
|---|---|---|
| ייבוא הסריקה | `importers/scan_import.py` | `extension_type`, `extension`, `extension_path_alias`, `extension_sensitive_setting`, `tree_sync_run`, `tree_sync_raw`, `extension_setting_ref`, `extension_link` |
| ייבוא סוגי השלוחות (D-119) | `importers/types_import.py` | `extension_type` |
| ייבוא הטיוטה | `importers/draft_import.py` | `migration_plan`, `base_tree_snapshot`, `uploaded_file`, `draft` (`imported` + `working`), `draft_node`, `draft_note` |
| דף ההצגה (D-112) | `demo/build.py`, `demo/template.html` | קורא בלבד. לא כולל ערכים מוצפנים, ומסתיר טלפונים בשמות |
| החלת מצב הדף (D-113, D-128, D-152) | `planning/apply_decisions.py <decisions> --props <nodeprops> --retired <retired> --globalnotes <globalnotes>` | קודם המבנה (`draft_node`: צמתים חדשים, `parent_node_id`, `digit`, `deleted`, `full_path`), אחר כך `mapping_row` (`approved` / `rejected`) והמאפיינים |
| מיפוי ודוח פערים | `planning/analyze.py` | `mapping_row`, `mapping_source`, `mapping_target`, `gap_report`, `gap_item`, `validation_result` |

- קטלוג מפתחות ההפניה והשדות הרגישים: `settings_catalog.py`.
- הצפנה: `crypto.py` (Fernet).
- הסכמה: `db/schema.sql`.
