-- סכמת בסיס הנתונים: עץ השלוחות (docs/data-model/01-extensions.md) ומודול התכנון (06-planning.md).
-- SQLite (D-110). ה-SQL נשאר פשוט ככל האפשר, כדי שאפשר יהיה להעביר אותו ל-PostgreSQL.
-- JSON נשמר כ-TEXT. זמנים נשמרים כ-TEXT בפורמט ISO 8601 (UTC).
-- ערכים מוצפנים (D-102, D-106) נשמרים כ-TEXT של Fernet. המפתח נמצא רק במשתנה הסביבה OR_OLAM_DB_KEY.

PRAGMA foreign_keys = ON;

-- ════════════════════════════════════════════════════════════════
-- 01 · עץ השלוחות
-- ════════════════════════════════════════════════════════════════

-- סוגי שלוחה. השם מגיע רק מ-/api/sclass-types/. כשאין שם, מוצג הקוד.
CREATE TABLE extension_type (
    cls_type        TEXT PRIMARY KEY,           -- באותיות גדולות, למשל MB
    api_type_id     INTEGER,                    -- id ב-/api/sclass-types/, אם ידוע
    name            TEXT,                       -- שם תצוגה בעברית, אם ידוע
    name_source     TEXT CHECK (name_source IN ('api', 'provisional')),  -- D-117
    type_group      TEXT,                       -- group
    priority        INTEGER,
    schema_json     TEXT,                       -- JSON Schema של settings
    schema_source   TEXT,                       -- מאיפה הגיעה הסכמה (scan-2026-10-21 / api)
    updated_at      TEXT NOT NULL
);

-- כל ריצת סנכרון או ייבוא של העץ.
CREATE TABLE tree_sync_run (
    run_id          INTEGER PRIMARY KEY,
    source          TEXT NOT NULL,              -- scan_import / api_sync
    source_ref      TEXT,                       -- למשל מזהה הסריקה 42c6831d…
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed')),
    node_count      INTEGER,
    completeness_ok INTEGER,                    -- 0/1: count_sub_sclass תאם למספר הילדים
    log             TEXT
);

-- שלוחה בעץ הנוכחי (D-105). שורה אחת לכל sclass_id.
CREATE TABLE extension (
    sclass_id               INTEGER PRIMARY KEY,
    parent_sclass_id        INTEGER REFERENCES extension (sclass_id),
    path                    TEXT NOT NULL,
    path_sort               TEXT NOT NULL,      -- מקטעים מרופדים באפסים, למיון נכון (1.10 אחרי 1.9)
    path_segments           TEXT NOT NULL,      -- JSON: ["1","2","1"]
    depth                   INTEGER NOT NULL,   -- 0 לשורש
    "order"                 INTEGER,
    name                    TEXT,               -- יכול להיות NULL
    cls_type                TEXT REFERENCES extension_type (cls_type),
    settings                TEXT,               -- JSON אחרי סינון D-107 והסתרת D-106. NULL כשהמקור החזיר null
    open_msg_id             INTEGER,
    sf_name_id              INTEGER,
    sclass_audio_name_id    INTEGER,
    count_sub_sclass        INTEGER,
    is_open                 INTEGER,            -- מתוך settings
    required_login          INTEGER,            -- מתוך settings
    time_active             TEXT,               -- JSON, מתוך settings
    conf_id                 TEXT,
    var_bool                INTEGER,
    -- D-102: מוצפן. JSON של {teacher_phone: [...], pin_code_extension, pin_code_admin_only}
    sensitive_enc           TEXT,
    -- סימוני "יש / אין / לא ידוע" לתצוגה, בלי הערך עצמו: 'yes' / 'no' / 'unknown'
    has_teacher_phone       TEXT,
    has_pin_code_extension  TEXT,
    has_pin_code_admin_only TEXT,
    first_seen_at           TEXT NOT NULL,
    last_synced_at          TEXT NOT NULL,
    last_sync_run_id        INTEGER REFERENCES tree_sync_run (run_id),
    missing_since           TEXT                -- לא חזרה בסנכרון האחרון. לא נמחקת
);
CREATE UNIQUE INDEX extension_path_uq ON extension (path) WHERE missing_since IS NULL;
CREATE INDEX extension_parent_ix ON extension (parent_sclass_id);
CREATE INDEX extension_path_sort_ix ON extension (path_sort);

-- נתיב ששלוחה עזבה (D-017). לא מודל היסטוריה.
CREATE TABLE extension_path_alias (
    old_path        TEXT NOT NULL,
    sclass_id       INTEGER NOT NULL REFERENCES extension (sclass_id),
    detected_at     TEXT NOT NULL,
    source          TEXT,                       -- למשל scan_structure_vs_detail
    PRIMARY KEY (old_path, sclass_id)
);

-- ערכים אישיים בתוך settings (D-106), מוצפנים. ב-extension.settings הערך מוחלף בסימון.
CREATE TABLE extension_sensitive_setting (
    sclass_id       INTEGER NOT NULL REFERENCES extension (sclass_id),
    json_path       TEXT NOT NULL,              -- למשל $.zintuk_tasks[2].caller_id
    value_enc       TEXT NOT NULL,
    PRIMARY KEY (sclass_id, json_path)
);

-- יומן חשיפת ערכים רגישים (D-102). רק המנהל הראשי חושף.
CREATE TABLE sensitive_access_log (
    access_id       INTEGER PRIMARY KEY,
    accessed_at     TEXT NOT NULL,
    accessed_by     TEXT NOT NULL,
    sclass_id       INTEGER,
    field           TEXT NOT NULL,              -- teacher_phone / pin_code_extension / json_path מתוך settings
    reason          TEXT
);

-- התגובות הגולמיות של הריצה האחרונה (בלי היסטוריה), אחרי סינון D-107, מוצפנות כולן.
CREATE TABLE tree_sync_raw (
    run_id          INTEGER NOT NULL REFERENCES tree_sync_run (run_id),
    sclass_id       INTEGER NOT NULL,
    fetched_at      TEXT,
    payload_enc     TEXT NOT NULL,
    PRIMARY KEY (run_id, sclass_id)
);

-- הפניות משלוחה לשלוחה מתוך settings. נגזרת, ומחושבת מחדש בכל סנכרון.
CREATE TABLE extension_setting_ref (
    ref_id              INTEGER PRIMARY KEY,
    sclass_id           INTEGER NOT NULL REFERENCES extension (sclass_id),
    setting_key         TEXT NOT NULL,          -- למשל more_public_to, או zintuk_tasks[].sclass
    raw_value           TEXT,
    ref_kind            TEXT NOT NULL,          -- record_to / publish_to / play_from / ... (01 §3)
    target_text         TEXT,                   -- ערך בודד אחרי פיצול לפי פסיקים
    target_form         TEXT NOT NULL CHECK (target_form IN
                          ('absolute', 'parent_relative', 'prefix', 'all', 'exclude', 'name', 'hangup', 'unparsed')),
    target_path         TEXT,                   -- הנתיב אחרי פענוח (גם ל-parent_relative ול-exclude)
    target_sclass_id    INTEGER REFERENCES extension (sclass_id),
    status              TEXT NOT NULL CHECK (status IN
                          ('ok', 'broken', 'by_name', 'ambiguous_name', 'external', 'pattern', 'hangup', 'unparsed')),
    computed_at         TEXT NOT NULL
);
CREATE INDEX extension_setting_ref_src_ix ON extension_setting_ref (sclass_id);
CREATE INDEX extension_setting_ref_tgt_ix ON extension_setting_ref (target_sclass_id);

-- ── מאפייני שלוחה שהמערכת מנהלת (D-015, D-104). צמודים ל-sclass_id ──

CREATE TABLE extension_profile (
    sclass_id           INTEGER PRIMARY KEY REFERENCES extension (sclass_id),
    content_type        TEXT,
    short_description   TEXT,
    caller_explanation  TEXT,
    renewal_rule        TEXT,
    renewal_grace       TEXT,
    updated_by          TEXT,
    updated_at          TEXT
);

-- lecturer_id: טבלת המרצים תתוכנן במסמך 04.
CREATE TABLE extension_lecturer (
    sclass_id       INTEGER NOT NULL REFERENCES extension (sclass_id),
    lecturer_id     INTEGER NOT NULL,
    role            TEXT CHECK (role IN ('main', 'partner', 'guest')),
    PRIMARY KEY (sclass_id, lecturer_id)
);

CREATE TABLE tag (
    tag_id          INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('tag', 'topic')),
    parent_tag_id   INTEGER REFERENCES tag (tag_id)
);

CREATE TABLE extension_tag (
    sclass_id       INTEGER NOT NULL REFERENCES extension (sclass_id),
    tag_id          INTEGER NOT NULL REFERENCES tag (tag_id),
    PRIMARY KEY (sclass_id, tag_id)
);

CREATE TABLE extension_link (
    link_id         INTEGER PRIMARY KEY,
    from_sclass_id  INTEGER NOT NULL REFERENCES extension (sclass_id),
    to_sclass_id    INTEGER NOT NULL REFERENCES extension (sclass_id),
    link_kind       TEXT NOT NULL,              -- mirror / copy_target / redirect / related, או ref_kind כש-source_kind=settings
    source_kind     TEXT NOT NULL CHECK (source_kind IN ('manual', 'settings')),
    note            TEXT
);
CREATE INDEX extension_link_from_ix ON extension_link (from_sclass_id);

CREATE TABLE attribute_change (
    change_id       INTEGER PRIMARY KEY,
    entity          TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    field           TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    source_kind     TEXT CHECK (source_kind IN
                      ('manual', 'inherited_via_mapping', 'suggested_from_draft', 'suggested_from_data')),
    changed_by      TEXT,
    changed_at      TEXT NOT NULL
);

-- ════════════════════════════════════════════════════════════════
-- 06 · מודול התכנון
-- ════════════════════════════════════════════════════════════════

CREATE TABLE migration_plan (
    plan_id         INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('drafting', 'migrating', 'done')),
    created_by      TEXT,
    created_at      TEXT NOT NULL
);

-- עותק קפוא של העץ הקיים. לא נמחק לעולם (D-105).
CREATE TABLE base_tree_snapshot (
    snapshot_id     INTEGER PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES migration_plan (plan_id),
    taken_at        TEXT NOT NULL,
    source_run_id   INTEGER,                    -- בלי FK, כי העץ נמחק ביום המעבר
    note            TEXT
);

-- בלי FK ל-extension, בכוונה: השורות שורדות את מחיקת העץ.
CREATE TABLE base_tree_snapshot_node (
    snapshot_id         INTEGER NOT NULL REFERENCES base_tree_snapshot (snapshot_id),
    sclass_id           INTEGER NOT NULL,
    path                TEXT NOT NULL,
    name                TEXT,
    cls_type            TEXT,
    parent_sclass_id    INTEGER,
    PRIMARY KEY (snapshot_id, sclass_id)
);
CREATE INDEX base_tree_snapshot_node_path_ix ON base_tree_snapshot_node (snapshot_id, path);

CREATE TABLE uploaded_file (
    file_id         INTEGER PRIMARY KEY,
    filename        TEXT NOT NULL,
    format          TEXT NOT NULL CHECK (format IN ('docx', 'gdoc', 'html', 'pdf', 'txt')),
    uploaded_by     TEXT,
    uploaded_at     TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    stored_path     TEXT,                       -- הקובץ המקורי נשמר מחוץ למאגר (data/)
    parse_status    TEXT CHECK (parse_status IN ('pending', 'ok', 'partial', 'failed')),
    parse_log       TEXT
);

-- D-108: טיוטת עבודה אחת לכל תכנית. snapshot ו-imported הן גרסאות להשוואה.
CREATE TABLE draft (
    draft_id            INTEGER PRIMARY KEY,
    plan_id             INTEGER NOT NULL REFERENCES migration_plan (plan_id),
    kind                TEXT NOT NULL CHECK (kind IN ('working', 'snapshot', 'imported')),
    source_file_id      INTEGER REFERENCES uploaded_file (file_id),
    based_on_draft_id   INTEGER REFERENCES draft (draft_id),
    label               TEXT,
    created_by          TEXT,
    created_at          TEXT NOT NULL
);
CREATE UNIQUE INDEX draft_one_working_uq ON draft (plan_id) WHERE kind = 'working';

-- node_id נשמר בין גרסאות של אותה טיוטה, ולכן המפתח הוא (draft_id, node_id).
CREATE TABLE draft_node (
    draft_id            INTEGER NOT NULL REFERENCES draft (draft_id),
    node_id             INTEGER NOT NULL,
    parent_node_id      INTEGER,
    position            INTEGER NOT NULL,       -- סדר במסמך
    digit               TEXT,                   -- הספרה המקומית. NULL אם אין מספר
    full_path           TEXT,                   -- מחושב. NULL אם לאחד האבות אין ספרה
    raw_number_text     TEXT,
    raw_text            TEXT,
    title               TEXT,
    node_status         TEXT NOT NULL CHECK (node_status IN ('existing', 'new', 'new_special_build', 'unknown')),
    node_kind           TEXT NOT NULL CHECK (node_kind IN ('extension', 'heading', 'placeholder')),
    planned_cls_type    TEXT,                   -- סוג השלוחה שייבנה (D-118)
    link_status         TEXT NOT NULL DEFAULT 'unset' CHECK (link_status IN ('unset', 'new', 'existing', 'merged')),  -- D-118
    special_build       INTEGER NOT NULL DEFAULT 0,  -- תגית "בנייה מיוחדת" (D-118)
    deleted             INTEGER NOT NULL DEFAULT 0,  -- נמחק בעריכת המבנה (D-124). נשמר לשחזור
    flags               TEXT,                        -- הערות קבועות (D-156): JSON, למשל ["lecturer", "rename"]
    hints               TEXT,                   -- JSON
    source_color        TEXT,                   -- הצבע הדומיננטי בקובץ, כמקור בלבד
    source_span         TEXT,                   -- JSON: מיקום בקובץ המקור
    PRIMARY KEY (draft_id, node_id),
    FOREIGN KEY (draft_id, parent_node_id) REFERENCES draft_node (draft_id, node_id)
);
CREATE INDEX draft_node_path_ix ON draft_node (draft_id, full_path);

CREATE TABLE draft_note (
    note_id         INTEGER PRIMARY KEY,
    draft_id        INTEGER NOT NULL REFERENCES draft (draft_id),
    node_id         INTEGER,                    -- NULL = הערה על כל הטיוטה
    scope           TEXT NOT NULL CHECK (scope IN ('global', 'section', 'node', 'open_question')),
    note_kind       TEXT NOT NULL CHECK (note_kind IN ('question', 'build_note', 'comment')),
    text            TEXT NOT NULL,
    source_color    TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'wont_do')),
    author          TEXT,
    created_at      TEXT NOT NULL,
    resolved_by     TEXT,
    resolved_at     TEXT,
    parent_note_id  INTEGER REFERENCES draft_note (note_id),
    FOREIGN KEY (draft_id, node_id) REFERENCES draft_node (draft_id, node_id)
);

CREATE TABLE gap_report (
    report_id       INTEGER PRIMARY KEY,
    draft_id        INTEGER NOT NULL REFERENCES draft (draft_id),
    compared_to     TEXT NOT NULL,              -- draft:<id> / current_tree / base_snapshot
    created_at      TEXT NOT NULL
);

CREATE TABLE gap_item (
    item_id         INTEGER PRIMARY KEY,
    report_id       INTEGER NOT NULL REFERENCES gap_report (report_id),
    gap_kind        TEXT NOT NULL CHECK (gap_kind IN
                      ('added', 'removed', 'renamed', 'moved', 'number_conflict', 'unnumbered',
                       'existing_not_found', 'current_not_planned', 'pattern_deviation',
                       'ref_needs_rewrite', 'open_note')),
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    draft_node_id   INTEGER,
    sclass_id       INTEGER,                    -- בלי FK: הדוח שורד שינויים בעץ
    detail          TEXT,                       -- JSON
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'fixed'))
);
CREATE INDEX gap_item_report_ix ON gap_item (report_id, gap_kind);

-- מיפוי קיים→עתידי (D-006, D-008, D-011). sources[] ו-targets[] מנורמלים לטבלאות בנות.
CREATE TABLE mapping_row (
    mapping_id      INTEGER PRIMARY KEY,
    plan_id         INTEGER NOT NULL REFERENCES migration_plan (plan_id),
    relation        TEXT NOT NULL CHECK (relation IN ('keep', 'move', 'rebuild', 'merge', 'split', 'retire')),
    effective_from  TEXT,
    confidence      REAL CHECK (confidence BETWEEN 0 AND 1),
    signals         TEXT,                       -- JSON
    status          TEXT NOT NULL CHECK (status IN ('suggested', 'needs_decision', 'approved', 'rejected')),
    decided_by      TEXT,
    decided_at      TEXT,
    note            TEXT
);

CREATE TABLE mapping_source (
    mapping_id      INTEGER NOT NULL REFERENCES mapping_row (mapping_id),
    sclass_id       INTEGER NOT NULL,           -- בלי FK: שורד את מחיקת העץ
    path            TEXT,                       -- מתוך base_tree_snapshot
    name            TEXT,
    PRIMARY KEY (mapping_id, sclass_id)
);

-- node_id מתייחס לטיוטת העבודה של התכנית (D-108).
CREATE TABLE mapping_target (
    mapping_id      INTEGER NOT NULL REFERENCES mapping_row (mapping_id),
    node_id         INTEGER NOT NULL,
    new_sclass_id   INTEGER,
    PRIMARY KEY (mapping_id, node_id)
);

-- D-013. תלוי במודל השיעורים (מסמך 02). ריק בינתיים.
CREATE TABLE split_rule (
    mapping_id      INTEGER NOT NULL REFERENCES mapping_row (mapping_id),
    lesson_id       INTEGER NOT NULL,
    target_node_id  INTEGER NOT NULL,
    PRIMARY KEY (mapping_id, lesson_id)
);

CREATE TABLE branch_status (
    plan_id         INTEGER NOT NULL REFERENCES migration_plan (plan_id),
    node_id         INTEGER NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('planned', 'in_progress', 'moved', 'verified')),
    changed_by      TEXT,
    changed_at      TEXT NOT NULL,
    note            TEXT,
    PRIMARY KEY (plan_id, node_id)
);

CREATE TABLE new_tree_binding (
    plan_id         INTEGER NOT NULL REFERENCES migration_plan (plan_id),
    node_id         INTEGER NOT NULL,
    new_sclass_id   INTEGER NOT NULL,
    method          TEXT NOT NULL CHECK (method IN ('auto_path_name', 'manual')),
    confirmed_by    TEXT,
    PRIMARY KEY (plan_id, node_id)
);

-- טבלה נגזרת. תתמלא כשיהיו האזנות (מסמכים 03, 09).
CREATE TABLE plan_coverage (
    plan_id             INTEGER NOT NULL REFERENCES migration_plan (plan_id),
    computed_at         TEXT NOT NULL,
    period              TEXT NOT NULL,
    branch_node_id      INTEGER,                -- NULL = כל התכנית
    listens_total       INTEGER,
    listens_mapped      INTEGER,
    listens_retired     INTEGER,
    listens_unmapped    INTEGER
);

CREATE TABLE validation_rule (
    rule_id         TEXT PRIMARY KEY,
    description     TEXT NOT NULL,
    default_severity TEXT NOT NULL CHECK (default_severity IN ('info', 'warning', 'error')),
    applies_to      TEXT NOT NULL CHECK (applies_to IN ('draft', 'current_tree', 'both'))
);

CREATE TABLE validation_result (
    result_id       INTEGER PRIMARY KEY,
    rule_id         TEXT NOT NULL REFERENCES validation_rule (rule_id),
    target          TEXT NOT NULL,              -- draft:<id>:node:<id> / sclass:<id>
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    message         TEXT NOT NULL,
    computed_at     TEXT NOT NULL
);

INSERT INTO validation_rule VALUES
    ('pattern_17890',   'חריגה מהדפוס 1/7/8/9/0 (D-016). אזהרה בלבד',             'warning', 'both'),
    ('duplicate_digit', 'שני צמתים עם אותה ספרה תחת אותו אב',                     'error',   'draft'),
    ('empty_node',      'צומת בלי שם או placeholder',                              'warning', 'draft'),
    ('broken_ref',      'הפניה ב-settings לנתיב שלא קיים בעץ',                     'warning', 'current_tree'),
    ('unmapped_existing','שלוחה קיימת שלא מופתה לטיוטה',                           'warning', 'current_tree'),
    ('branch_no_lecturer','ענף בלי מרצה',                                          'info',    'both');
