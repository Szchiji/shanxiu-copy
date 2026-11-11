"""Pure helpers for proven edit snapshots. No database or Telegram."""
from bot.services import listing_history as h


def test_sanitize_and_diff_ignore_system_fields():
    before = h.snapshot_values(type("L", (), dict(
        city="深圳", title="原", tags=["a"], price=10, price_text="10", description="d",
        district="南山", approx_label="附近", media=[{"type": "image", "url": "old", "file_id": "old"}],
        photos=["old"], status="active", expires_at=None,
    ))(), {"身高": "170", "_bc_chat": "x", "_hours": [{"dow": [1], "start": "10:00", "end": "18:00"}]})
    assert "_bc_chat" not in before["extras"] and before["extras"]["身高"] == "170"
    assert before["hours"]
    after = dict(before)
    after["title"] = "新"
    after["status"] = "hidden"  # not a tracked field in changed_fields
    assert h.changed_fields(before, after) == ["title"]
    payload = h.payload_from_snapshot({**after, "status": "hidden", "expires_at": "x", "user_id": 1}, include_media=False, current_media=before["media"])
    assert "status" not in payload and "expires_at" not in payload and "media" not in payload


def test_media_availability_uses_current_or_local_cache(tmp_path, monkeypatch):
    current = [{"type": "image", "url": "keep"}]
    gone = [{"type": "image", "file_id": "missing-id-xyz"}]
    assert h.media_availability(current, current)["available"] is True
    miss = h.media_availability(gone, current)
    assert miss["available"] is False and miss["missing"]
    monkeypatch.setattr(h, "_DISK_DIR", tmp_path)
    import hashlib
    name = hashlib.sha256(b"missing-id-xyz").hexdigest()[:40]
    (tmp_path / name).write_bytes(b"cached")
    assert h.media_availability(gone, current)["available"] is True


def test_actor_labels_only_use_proven_identity():
    row = type("V", (), dict(source="baseline", actor_kind="system", actor_id=None))()
    assert "原文" in h.actor_label(row, admin=False, owner_id=8)
    teacher = type("V", (), dict(source="teacher_edit", actor_kind="teacher", actor_id=8))()
    assert h.actor_label(teacher, admin=False, owner_id=8) == "老师本人"
    admin = type("V", (), dict(source="admin_edit", actor_kind="admin", actor_id=99))()
    assert h.actor_label(admin, admin=False, owner_id=8) == "管理员"
    assert h.actor_label(admin, admin=True, owner_id=8) == "管理员 99"
