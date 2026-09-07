"""Authentication, redirect confinement and secret redaction without live Google calls."""
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gemsentry import google_webhook as webhook

CONFIG = {"apps_script_url": "https://script.google.com/macros/s/test/exec", "webhook_secret": "test-secret"}


def response(status=200, location=None, data=None):
    return Mock(status_code=status, headers={"Location": location} if location else {},
                json=Mock(return_value=data or {"status": "ok"}))


@pytest.mark.parametrize("config", [{}, {**CONFIG, "webhook_secret": ""},
                                   {**CONFIG, "apps_script_url": "http://127.0.0.1/"}])
def test_invalid_config_never_sends_credentials(monkeypatch, config):
    post = Mock()
    monkeypatch.setattr(webhook.requests, "post", post)
    with pytest.raises(ValueError):
        webhook.post_webhook(config, {"action": "delete_tender"})
    post.assert_not_called()


def test_authenticates_post_and_follows_google_content_redirect_without_secret(monkeypatch):
    location = "https://script.googleusercontent.com/macros/echo?content=test"
    post, get = Mock(return_value=response(302, location)), Mock(return_value=response())
    monkeypatch.setattr(webhook.requests, "post", post)
    monkeypatch.setattr(webhook.requests, "get", get)
    payload = {"action": "append_tender"}
    assert webhook.post_webhook(CONFIG, payload)["status"] == "ok"
    assert post.call_args.kwargs["json"]["webhook_secret"] == "test-secret"
    assert post.call_args.kwargs["allow_redirects"] is False
    get.assert_called_once_with(location, timeout=12, allow_redirects=False)
    assert "webhook_secret" not in payload


@pytest.mark.parametrize("location", ["https://evil.example/", "http://script.google.com/", "https://script.google.com@evil.example/"])
def test_untrusted_redirect_is_not_followed(monkeypatch, location):
    post, get = Mock(return_value=response(307, location)), Mock()
    monkeypatch.setattr(webhook.requests, "post", post)
    monkeypatch.setattr(webhook.requests, "get", get)
    with pytest.raises(ValueError, match="untrusted"):
        webhook.post_webhook(CONFIG, {})
    assert post.call_count == 1
    get.assert_not_called()


def test_config_response_redacts_secret():
    public = webhook.public_config(CONFIG)
    assert "webhook_secret" not in public
    assert public["webhook_secret_configured"] is True
    assert CONFIG["webhook_secret"] == "test-secret"


def test_secret_environment_override(monkeypatch, tmp_path):
    from gemsentry import master_sheet
    monkeypatch.setattr(master_sheet, "CONFIG_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setenv("GEMSENTRY_WEBHOOK_SECRET", "environment-secret")
    manager = master_sheet.MasterSheetManager.__new__(master_sheet.MasterSheetManager)
    assert manager._load_config()["webhook_secret"] == "environment-secret"


def test_blank_secret_save_preserves_private_value_and_response_redacts_it(monkeypatch, tmp_path):
    import threading
    import app
    from gemsentry import master_sheet
    monkeypatch.setattr(master_sheet, "CONFIG_PATH", str(tmp_path / "sync.json"))
    manager = master_sheet.MasterSheetManager.__new__(master_sheet.MasterSheetManager)
    manager.lock = threading.RLock()
    manager.config = dict(CONFIG)
    monkeypatch.setattr("gemsentry.web.master_sheet.master_sheet_manager", manager)
    monkeypatch.setattr("paths.load_server_config", lambda: {"auth_token": ""})
    with app.app.test_client() as client:
        response = client.post('/api/finalized/config', json={'webhook_secret': '   '})
        assert response.status_code == 200
        assert manager.config['webhook_secret'] == 'test-secret'
        assert 'test-secret' not in response.get_data(as_text=True)
        assert response.json['config']['webhook_secret_configured'] is True


def test_vendor_names_come_from_local_configuration():
    from gemsentry.master_sheet import detect_vendor
    vendor = detect_vendor({}, 'Example supplier', {'drone': {'name': 'Example supplier'}})
    assert vendor['id'] == 'drone'
    assert vendor['name'] == 'Example supplier'


def test_script_download_uses_public_template_not_private_deployment_copy(monkeypatch, tmp_path):
    import app
    folder = tmp_path / 'gemsentry'
    folder.mkdir()
    (folder / 'google_sync_script.example.gs').write_text('// public template')
    (folder / 'google_sync_script.gs').write_text('// private deployment configuration')
    monkeypatch.setattr('paths.ROOT', str(tmp_path))
    monkeypatch.setattr('paths.load_server_config', lambda: {'auth_token': ''})
    with app.app.test_client() as client:
        response = client.get('/api/finalized/script')
        assert response.status_code == 200
        assert response.json['script'] == '// public template'
