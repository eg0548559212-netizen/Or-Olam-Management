"""קטלוג המפתחות ב-settings: הפניות לשלוחות אחרות (01 §3) ושדות רגישים (D-106, D-107).

המפתחות מזוהים לפי שם, ולא לפי סוג שלוחה: בסריקה יש מפתחות שנשארו משינוי סוג
(למשל goto_sclass תחת LMENU), והם עדיין מפנים לשלוחות.
מפתח בתוך מערך נכתב כ-`table[].field`.
"""

# מפתח → ref_kind
REF_KEYS = {
    # הקלטה
    "record_to": "record_to",
    # פרסום והעתקה
    "more_public_to": "publish_to",
    "copy_to_sclasses": "publish_to",
    "more_public_to_lines[].path": "external_line",
    # השמעה ושליפה משלוחות אחרות
    "play_from_other_sclass": "play_from",
    "sclass_play_other": "play_from",
    "sclass_list": "play_from",
    "questionnaire_sclass_list": "play_from",
    "sclass_test_from": "play_from",
    "score_from": "play_from",
    "sclass_search_path": "play_from",
    "sclass_form": "play_from",
    "run_questions_sclass": "play_from",
    "sclass": "play_from",
    # מקור רשימת הרשומים לצינתוק
    "sclass_to_zintokim": "zintuk_source",
    "time_table[].sclass_register": "zintuk_source",
    "zintuk_tasks[].sclass_register": "zintuk_source",
    "dial_if_register_to": "zintuk_source",
    # רישום
    "register_to": "register_to",
    "register_to_sclass": "register_to",
    "register_table[].sclass_check": "register_to",
    # ניתוב
    "goto_sclass": "move_to",
    "go_to_sclass": "move_to",
    "on_exit_goto": "move_to",
    "success_move_to_sclass": "move_to",
    "failure_move_to_sclass": "move_to",
    "error_move_to_sclass": "move_to",
    "limited_move_to_sclass": "move_to",
    "if_not_msg_move_to_sclass": "move_to",
    "time_close_move_to_sclass": "move_to",
    "move_to_sclass": "move_to",
    "after_register_move_to_sclass": "move_to",
    "after_unregister_move_to_sclass": "move_to",
    "if_no_register_to_go_to": "move_to",
    "if_no_members_go_to": "move_to",
    "time_out_duration_goto": "move_to",
    "after_hear_goto_sclass": "move_to",
    "specific_sclass": "move_to",
    "register_user_go_to_sclass": "move_to",
    "other_class": "move_to",
    "random_threshold_settings.threshold_goto_sclass": "move_to",
    "go_to_sclass_by_score_rules[].sclass": "move_to",
    "move_to_sclass_options[].sclass": "move_to",
    "time_table[].goto_sclass": "move_to",
    "register_table[].goto_sclass": "move_to",
    "user_detail_table[].goto_sclass": "move_to",
    "more_options_in_keys_confbridge[].path": "move_to",
}

# D-107: לא נשמרים בכלל. נשמר רק "יש ערך / אין".
SECRET_KEYS = {"masof_password", "api_password", "user_authorization"}

# D-106: נשמרים מוצפנים, ומוסתרים בתצוגה.
HIDDEN_KEYS = {
    "send_msg_to_mail",
    "zintok_callerid",
    "zintuk_tasks[].caller_id",
    "caller_id",
    "more_public_to_lines[].phone",
    "more_public_to_lines[].code",
}

# ערך שהושחר בעותק הייצוא. לא נשמר כערך אמיתי.
REDACTED = "[REDACTED]"

HANGUP_WORDS = {"ניתוק", "hangup"}
