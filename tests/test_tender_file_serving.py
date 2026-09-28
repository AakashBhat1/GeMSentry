"""The /tenders/ route serves RFP PDFs only, and GeM traffic verifies TLS."""

import os
import ssl
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app as app_module  # noqa: E402
import paths  # noqa: E402
from gemsentry.sources.gem import client as gem_client  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    (tmp_path / "personel").mkdir()
    (tmp_path / "personel" / "metadata.db").write_bytes(b"SQLite format 3\x00")
    (tmp_path / "metadata.json").write_text("[]", encoding="utf-8")
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / "GEM_2026_B_1.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(paths, "TENDERS_DIR", str(tmp_path))
    # Loopback without a configured token: the local-only mode the app ships in.
    monkeypatch.setattr(
        paths, "load_server_config",
        lambda: {"auth_token": "", "host": "127.0.0.1", "port": 5000,
                 "tenders_dir": "", "logs_dir": ""},
    )
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_pdf_is_served(client):
    resp = client.get("/tenders/downloads/GEM_2026_B_1.pdf")
    assert resp.status_code == 200
    assert resp.data.startswith(b"%PDF")


@pytest.mark.parametrize("path", [
    "/tenders/personel/metadata.db",
    "/tenders/metadata.json",
    "/tenders/personel/metadata.DB",
])
def test_non_pdf_files_are_not_served(client, path):
    assert client.get(path).status_code == 404


def test_gem_client_verifies_certificates():
    assert gem_client._SSL_CTX.verify_mode == ssl.CERT_REQUIRED
    assert gem_client._SSL_CTX.check_hostname
