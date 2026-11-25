"""CLI for the backup key: show, restore, and decrypting a backup for a manual pg_restore."""

import keyring
import pytest

from pal import backup_crypto, cli

KEY = bytes(range(32))


@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(keyring, "get_password", lambda svc, acct: store.get((svc, acct)))
    monkeypatch.setattr(keyring, "set_password", lambda svc, acct, val: store.__setitem__((svc, acct), val))
    return store


def test_backup_key_set_then_show(fake_keyring, monkeypatch, capsys):
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": backup_crypto.encode_key(KEY))
    cli.cmd_backup_key(show=False, set_key=True)
    cli.cmd_backup_key(show=True, set_key=False)
    assert backup_crypto.encode_key(KEY) in capsys.readouterr().out


def test_backup_key_rejects_bad_key_and_reports_missing(fake_keyring, monkeypatch):
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "nope")
    with pytest.raises(SystemExit):
        cli.cmd_backup_key(show=False, set_key=True)
    with pytest.raises(backup_crypto.BackupKeyError):
        cli.cmd_backup_key(show=False, set_key=False)


def test_decrypt_backup_writes_plain_dump(fake_keyring, tmp_path):
    backup_crypto.store_key(KEY)
    src = tmp_path / "pal-1.dump.enc"
    src.write_bytes(backup_crypto.encrypt(b"PGDMP data", KEY))
    out = tmp_path / "plain.dump"
    cli.cmd_decrypt_backup(str(src), str(out))
    assert out.read_bytes() == b"PGDMP data"
    with pytest.raises(SystemExit):  # never overwrites
        cli.cmd_decrypt_backup(str(src), str(out))
    backup_crypto.store_key(bytes(32))
    with pytest.raises(SystemExit):  # wrong key gives a clean message
        cli.cmd_decrypt_backup(str(src), str(tmp_path / "other.dump"))
