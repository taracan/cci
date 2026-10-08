"""Unit tests for auth — password hashing + JSON helpers (isolated to tmp files)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self):
        h = auth.hash_password("secret123")
        assert h != "secret123"
        assert h.startswith("$2b$")

    def test_verify_correct_password(self):
        h = auth.hash_password("secret123")
        assert auth.verify_password("secret123", h) is True

    def test_verify_wrong_password(self):
        h = auth.hash_password("secret123")
        assert auth.verify_password("wrong", h) is False

    def test_two_hashes_differ_but_both_verify(self):
        h1 = auth.hash_password("same")
        h2 = auth.hash_password("same")
        assert h1 != h2   # random salt
        assert auth.verify_password("same", h1)
        assert auth.verify_password("same", h2)


class TestJsonPersistence:
    def test_load_missing_returns_empty(self, tmp_path):
        assert auth.load_json(tmp_path / "nope.json") == []

    def test_save_then_load_roundtrip(self, tmp_path):
        f = tmp_path / "data.json"
        auth.save_json(f, [{"a": 1}])
        assert auth.load_json(f) == [{"a": 1}]

    def test_save_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "nested" / "deep" / "data.json"
        auth.save_json(f, {"ok": True})
        assert f.exists()


class TestRegistrationAndLogin:
    def _isolate(self, tmp_path, monkeypatch):
        """Point auth's JSON files at a tmp dir so tests don't touch real data."""
        monkeypatch.setattr(auth, "CANDIDATES_FILE", tmp_path / "candidates.json")
        monkeypatch.setattr(auth, "ADMINS_FILE", tmp_path / "admins.json")

    def test_register_candidate_success(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        ok, msg, uid = auth.register_candidate("Al", "al@x.com", "pw", "Dev")
        assert ok is True and uid and uid.startswith("cand_")

    def test_register_duplicate_email_fails(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        auth.register_candidate("Al", "al@x.com", "pw", "Dev")
        ok, msg, uid = auth.register_candidate("Al2", "al@x.com", "pw", "Dev")
        assert ok is False and uid is None

    def test_authenticate_success(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        auth.register_candidate("Al", "al@x.com", "pw", "Dev")
        ok, uid, name, role = auth.authenticate_user("al@x.com", "pw")
        assert ok is True and role == "candidate" and name == "Al"

    def test_authenticate_wrong_password(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        auth.register_candidate("Al", "al@x.com", "pw", "Dev")
        ok, *_ = auth.authenticate_user("al@x.com", "bad")
        assert ok is False

    def test_authenticate_unknown_user(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        ok, *_ = auth.authenticate_user("ghost@x.com", "pw")
        assert ok is False

    def test_register_admin_wrong_key_fails(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        ok, msg, uid = auth.register_admin("A", "a@x.com", "pw", "WRONG_KEY")
        assert ok is False and uid is None
