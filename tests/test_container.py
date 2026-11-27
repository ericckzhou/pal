"""The Docker path: secrets in a folder, settings from PAL_* variables, the container bind rule, local services."""

import socket
from pathlib import Path

import pytest

from pal import backup, egress, secret_store
from pal.cli import _local_services, check_loopback
from pal.config import Settings, data_dir


@pytest.fixture
def secrets_dir(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "secrets"
    monkeypatch.setenv(secret_store.ENV_DIR, str(folder))
    return folder


def test_secret_folder_get_put_delete(secrets_dir):
    assert secret_store.get("backup-key") is None
    secret_store.put("backup-key", "abc")
    assert (secrets_dir / "backup-key").read_text() == "abc"
    assert secret_store.get("backup-key") == "abc"
    assert "secrets folder" in secret_store.where()
    assert secret_store.delete("backup-key") is True
    assert secret_store.delete("backup-key") is False
    assert secret_store.get("backup-key") is None


def test_secret_folder_ignores_trailing_newline_and_empty_files(secrets_dir):
    secrets_dir.mkdir()
    (secrets_dir / "postgres").write_text("s3cret\n")
    (secrets_dir / "anthropic-api-key").write_text("\n")
    assert secret_store.get("postgres") == "s3cret"
    assert secret_store.get("anthropic-api-key") is None


def test_keyring_is_used_without_a_secrets_folder(monkeypatch):
    import keyring

    store: dict = {}
    monkeypatch.delenv(secret_store.ENV_DIR, raising=False)
    monkeypatch.setattr(keyring, "get_password", lambda svc, acct: store.get((svc, acct)))
    monkeypatch.setattr(keyring, "set_password", lambda svc, acct, val: store.__setitem__((svc, acct), val))
    secret_store.put("postgres", "pw")
    assert store == {("pal", "postgres"): "pw"}
    assert secret_store.get("postgres") == "pw"
    assert secret_store.where() == "Windows Credential Manager"


def test_settings_from_env(monkeypatch):
    for name, value in {
        "PAL_HOST": "0.0.0.0", "PAL_DB_HOST": "db", "PAL_DB_PORT": "5433",
        "PAL_OLLAMA_URL": "http://ollama:11434/", "PAL_BACKUP_DIR": "/data/b",
    }.items():
        monkeypatch.setenv(name, value)
    s = Settings.from_env()
    assert (s.host, s.db_host, s.db_port, s.ollama_url) == ("0.0.0.0", "db", 5433, "http://ollama:11434")
    assert s.backup_dir == Path("/data/b")
    assert set(_local_services(s)) == {"db", "ollama"}


def test_settings_from_env_public_port(monkeypatch):
    # Docker publishes pal on PAL_PORT on the host; the browser sends that port in Host and Origin.
    monkeypatch.setenv("PAL_PUBLIC_PORT", "8766")
    s = Settings.from_env()
    assert s.allowed_hosts == ("127.0.0.1:8766", "localhost:8766")
    assert "http://localhost:8766" in s.allowed_origins
    assert "http://127.0.0.1:8765" not in s.allowed_origins


@pytest.mark.parametrize("value", ["abc", "0", "70000"])
def test_settings_from_env_rejects_bad_public_port(monkeypatch, value):
    monkeypatch.setenv("PAL_PUBLIC_PORT", value)
    with pytest.raises(SystemExit, match="PAL_PUBLIC_PORT"):
        Settings.from_env()


def test_settings_from_env_defaults_without_variables(monkeypatch):
    for name in ("PAL_HOST", "PAL_DB_HOST", "PAL_DB_PORT", "PAL_OLLAMA_URL", "PAL_BACKUP_DIR", "PAL_PUBLIC_PORT"):
        monkeypatch.delenv(name, raising=False)
    assert Settings.from_env() == Settings()
    assert _local_services(Settings()) == ()


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PAL_DATA_DIR", str(tmp_path))
    assert data_dir() == tmp_path


def test_wildcard_bind_only_inside_the_container(monkeypatch):
    monkeypatch.delenv("PAL_CONTAINER", raising=False)
    with pytest.raises(SystemExit):
        check_loopback("0.0.0.0")
    monkeypatch.setenv("PAL_CONTAINER", "1")
    check_loopback("0.0.0.0")
    with pytest.raises(SystemExit):
        check_loopback("192.168.1.5")  # only the container wildcard is excepted, never a real interface


def test_local_service_names_allow_only_their_private_addresses(monkeypatch):
    monkeypatch.setattr(egress, "_service_names", {"db"})
    monkeypatch.setattr(egress, "_service_ips", set())
    assert egress._is_allowed("db")
    assert not egress._is_allowed("example.com")
    results = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.2", 5432)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 5432)),  # public: never allowed
    ]
    egress._remember_service_ips(results)
    assert egress._is_allowed("172.18.0.2")
    assert not egress._is_allowed("93.184.216.34")
    assert not egress._is_allowed("172.18.0.3")


def test_backup_tools_run_directly_in_the_container(monkeypatch, secrets_dir):
    secret_store.put("postgres", "pw")
    monkeypatch.setenv("PAL_CONTAINER", "1")
    monkeypatch.setenv("PAL_DB_HOST", "db")
    command, env = backup._command(("pg_dump", "-U", "pal"))
    assert command == ["pg_dump", "-U", "pal"]
    assert (env["PGHOST"], env["PGPASSWORD"]) == ("db", "pw")
    monkeypatch.delenv("PAL_CONTAINER")
    command, env = backup._command(("pg_dump",))
    assert command[:3] == ["docker", "exec", "-i"] and env is None


def test_ollama_service_name_allowed_only_in_the_container(monkeypatch):
    from pal.llm import OllamaGateway

    monkeypatch.delenv("PAL_CONTAINER", raising=False)
    with pytest.raises(ValueError):
        OllamaGateway("http://ollama:11434", "qwen3.5:4b", 4096)
    monkeypatch.setenv("PAL_CONTAINER", "1")
    OllamaGateway("http://ollama:11434", "qwen3.5:4b", 4096)
    with pytest.raises(ValueError):  # a dotted name could be anywhere on the internet
        OllamaGateway("http://ollama.example.com:11434", "qwen3.5:4b", 4096)
