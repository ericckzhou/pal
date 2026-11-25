import json

import httpx
import pytest

import os

from pal import backup_crypto
from pal.backup import backup_files, encrypt_legacy, restore_check, run_backup
from pal.llm import LLMError, OllamaGateway


def ndjson(*objs):
    return "\n".join(json.dumps(o) for o in objs).encode()


def gateway_with(body: bytes, status=200) -> OllamaGateway:
    transport = httpx.MockTransport(lambda req: httpx.Response(status, content=body))
    return OllamaGateway("http://127.0.0.1:11434", "qwen3.5:4b", 8192, client=httpx.Client(transport=transport))


def test_gateway_streams_tokens():
    gw = gateway_with(ndjson({"message": {"content": "a"}}, {"message": {"content": "b"}, "done": True}))
    assert list(gw.stream_chat([{"role": "user", "content": "hi"}])) == ["a", "b"]


def test_gateway_surfaces_model_errors():
    with pytest.raises(LLMError):
        list(gateway_with(ndjson({"error": "model not found"})).stream_chat([]))


# S9: model allowlist + loopback only
def test_s9_gateway_rejects_unlisted_model_and_remote_url():
    with pytest.raises(ValueError):
        OllamaGateway("http://127.0.0.1:11434", "some-cloud-model", 8192)
    with pytest.raises(ValueError):
        OllamaGateway("https://ollama.com", "qwen3.5:4b", 8192)


KEY = bytes(range(32))


# S11: backup + tested restore (hardening: the file on disk is encrypted)
def test_s11_backup_and_restore_roundtrip(authed, db, settings, tmp_path):
    conv = authed.post("/api/conversations").json()
    db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s, 'user', 'backup me')", (conv["id"],))
    dump = run_backup(tmp_path, KEY, db_name=settings.db_name, keep=2)
    blob = dump.read_bytes()
    assert dump.name.endswith(".dump.enc") and backup_crypto.is_encrypted(blob)
    assert b"backup me" not in blob and b"PGDMP" not in blob
    report = restore_check(dump, KEY, source_db=settings.db_name)
    assert report["ok"], report
    assert report["tables"]["messages"] == (1, 1)


def test_backup_rotation_keeps_newest_and_encrypts_legacy(tmp_path, settings):
    for i in range(3):
        (tmp_path / f"pal-20260101-00000{i}.dump").write_bytes(b"PGDMP legacy " + bytes([i]))
    run_backup(tmp_path, KEY, db_name=settings.db_name, keep=2)
    files = backup_files(tmp_path)
    assert len(files) == 2 and all(f.name.endswith(".dump.enc") for f in files)
    assert not list(tmp_path.glob("pal-*.dump")) and not list(tmp_path.glob("*.tmp"))
    assert files[0].name == "pal-20260101-000002.dump.enc"
    assert backup_crypto.read_dump(files[0], KEY) == b"PGDMP legacy " + bytes([2])


def test_encrypt_legacy_keeps_mtime_and_skips_already_encrypted(tmp_path):
    plain = tmp_path / "pal-20260101-000000.dump"
    plain.write_bytes(b"PGDMP old")
    os.utime(plain, (1_700_000_000, 1_700_000_000))
    odd = tmp_path / "pal-20260102-000000.dump"
    odd.write_bytes(backup_crypto.encrypt(b"already", KEY))
    assert encrypt_legacy(tmp_path, KEY) == 2
    enc = tmp_path / "pal-20260101-000000.dump.enc"
    assert int(enc.stat().st_mtime) == 1_700_000_000
    assert backup_crypto.read_dump(tmp_path / "pal-20260102-000000.dump.enc", KEY) == b"already"


def test_backup_crypto_rejects_wrong_key_and_tampering(tmp_path):
    blob = backup_crypto.encrypt(b"secret", KEY)
    assert backup_crypto.decrypt(blob, KEY) == b"secret"
    with pytest.raises(backup_crypto.BackupKeyError):
        backup_crypto.decrypt(blob, bytes(32))
    flipped = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(backup_crypto.BackupKeyError):
        backup_crypto.decrypt(flipped, KEY)
    with pytest.raises(backup_crypto.BackupKeyError):
        backup_crypto.decrypt(b"PGDMP plain", KEY)
    path = tmp_path / "x.dump.enc"
    path.write_bytes(blob)
    with pytest.raises(backup_crypto.BackupKeyError):
        backup_crypto.read_dump(path, None)


def test_backup_key_encoding_and_keyring(monkeypatch):
    store: dict = {}
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda svc, acct: store.get((svc, acct)))
    monkeypatch.setattr(keyring, "set_password", lambda svc, acct, val: store.__setitem__((svc, acct), val))
    with pytest.raises(backup_crypto.BackupKeyError):
        backup_crypto.load_key(create=False)
    key = backup_crypto.load_key()
    assert len(key) == 32 and backup_crypto.load_key(create=False) == key
    assert backup_crypto.decode_key(backup_crypto.encode_key(key)) == key
    for bad in ("not base64!!", backup_crypto.encode_key(b"short")):
        with pytest.raises(backup_crypto.BackupKeyError):
            backup_crypto.decode_key(bad)
    backup_crypto.store_key(KEY)
    assert backup_crypto.load_key() == KEY
