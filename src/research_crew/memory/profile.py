from .db import get_conn, _dumps, _loads

DEFAULT_USER_ID = "single_user"
DEFAULT_PROFILE = {
    "user_id": DEFAULT_USER_ID,
    "name": "",
    "email_group": "all",
    "preferred_topics": [],
    "tone": "professional",
    "exclusions": [],
    "cadence": "monthly",
    "timezone": "Asia/Kolkata",
    "content_length": "medium",
    "section_order": [],
    "source_prefs": [],
    "risk_sensitivity": "balanced",
    "region_focus": "global",
    "allow_duplicate": False,
    "subject_template": "",
    "exclude_keywords": [],
}


def _as_bool(value):
    return bool(int(value)) if value is not None else False


def get_profile(user_id=DEFAULT_USER_ID):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_profile WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "name": row["name"] or "",
        "email_group": row["email_group"] or "all",
        "preferred_topics": _loads(row["preferred_topics_json"], []),
        "tone": row["tone"] or "professional",
        "exclusions": _loads(row["exclusions_json"], []),
        "cadence": row["cadence"] or "monthly",
        "timezone": row["timezone"] or "",
        "content_length": row["content_length"] or "medium",
        "section_order": _loads(row["section_order_json"], []),
        "source_prefs": _loads(row["source_prefs_json"], []),
        "risk_sensitivity": row["risk_sensitivity"] or "balanced",
        "region_focus": row["region_focus"] or "global",
        "allow_duplicate": _as_bool(row["allow_duplicate"]),
        "subject_template": row["subject_template"] or "",
        "exclude_keywords": _loads(row["exclude_keywords_json"], []),
    }


def save_profile(profile, user_id=DEFAULT_USER_ID):
    current = get_profile(user_id) or {}
    merged = {**DEFAULT_PROFILE, **current, **(profile or {})}
    merged["user_id"] = user_id
    payload = (
        merged["user_id"],
        merged.get("name"),
        merged.get("email_group"),
        _dumps(merged.get("preferred_topics", [])),
        merged.get("tone"),
        _dumps(merged.get("exclusions", [])),
        merged.get("cadence"),
        merged.get("timezone"),
        merged.get("content_length"),
        _dumps(merged.get("section_order", [])),
        _dumps(merged.get("source_prefs", [])),
        merged.get("risk_sensitivity"),
        merged.get("region_focus"),
        1 if merged.get("allow_duplicate") else 0,
        merged.get("subject_template"),
        _dumps(merged.get("exclude_keywords", [])),
    )
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_profile (
                user_id, name, email_group, preferred_topics_json, tone,
                exclusions_json, cadence, timezone, content_length, section_order_json,
                source_prefs_json, risk_sensitivity, region_focus, allow_duplicate,
                subject_template, exclude_keywords_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                email_group=excluded.email_group,
                preferred_topics_json=excluded.preferred_topics_json,
                tone=excluded.tone,
                exclusions_json=excluded.exclusions_json,
                cadence=excluded.cadence,
                timezone=excluded.timezone,
                content_length=excluded.content_length,
                section_order_json=excluded.section_order_json,
                source_prefs_json=excluded.source_prefs_json,
                risk_sensitivity=excluded.risk_sensitivity,
                region_focus=excluded.region_focus,
                allow_duplicate=excluded.allow_duplicate,
                subject_template=excluded.subject_template,
                exclude_keywords_json=excluded.exclude_keywords_json
            """,
            payload,
        )
    return get_profile(user_id)


def ensure_profile(user_id=DEFAULT_USER_ID):
    profile = get_profile(user_id)
    if profile:
        return profile
    return save_profile({}, user_id=user_id)
