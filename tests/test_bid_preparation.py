from __future__ import annotations

import io

import pytest

from gemsentry.bid_preparation import service


@pytest.fixture
 def workspace(tmp_path, monkeypatch):
    tenders = tmp_path / "tenders"
    tenders.mkdir()
    monkeypatch.setattr(service, "workspace_paths", lambda: (str(tenders), str(tenders / "downloads")))
    return tenders


def test_save_and_audit(workspace):
    result = service.save("GEM/2026/B/123", {"status": "in_review", "quoted_amount": 1200,
        "notes": "Check PF/ESI", "checklist": {"psara": True}}, "tester")
    assert result["status"] == "in_review"
    assert result["checklist"]["psara"] is True
    assert result["audit"][0]["action"] == "created"


def test_pdf_registry_and_verification(workspace):
    service.save("BID-1", {}, "tester")
    pdf = b"%PDF-1.4\\n%test\\n%%EOF"
    result = service.add_document("BID-1", "psara", "PSARA.pdf", pdf, "tester")
    doc = result["documents"][0]
    assert doc["verified"] == 0
    checked = service.verify_document(doc["id"], True, "tester")
    assert checked["documents"][0]["verified"] == 1
    path, name = service.document_path(doc["id"])
    assert name == "PSARA.pdf"
    assert path.endswith(".pdf")


def test_rejects_invalid_bid_and_non_pdf(workspace):
    with pytest.raises(ValueError):
        service.save("../escape", {}, "tester")
    service.save("BID-2", {}, "tester")
    with pytest.raises(ValueError):
        service.add_document("BID-2", "other", "x.pdf", b"not pdf", "tester")
