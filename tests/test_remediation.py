"""Regression checks for local configuration, portal capabilities and export portability."""
import json
import os
import sys

import openpyxl
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import paths
from gemsentry import profile, search
from gemsentry.live_excel import _write_sheet
from gemsentry.sources import SourceRegistry


def test_private_profile_overrides_example_and_saves_locally(tmp_path, monkeypatch):
    private, example = tmp_path / 'private.json', tmp_path / 'example.json'
    example.write_text(json.dumps({'company': {'legal_name': 'Example'}}))
    private.write_text(json.dumps({'company': {'legal_name': 'Local'}}))
    monkeypatch.setattr(paths, 'COMPANY_PROFILE_PATH', str(private))
    monkeypatch.setattr(paths, 'COMPANY_PROFILE_EXAMPLE_PATH', str(example))
    monkeypatch.setattr(profile, 'COMPANY_PROFILE_PATH', str(private))
    assert profile.load_company_profile()['company']['legal_name'] == 'Local'
    assert search.load_company_profile()['company']['legal_name'] == 'Local'
    original = example.read_bytes()
    profile.save_company_profile(profile.load_company_profile())
    assert example.read_bytes() == original


def test_unsupported_sources_are_disabled_and_cannot_be_enabled():
    registry = SourceRegistry()
    assert not registry.unsupported_sources()
    with pytest.raises(ValueError, match='no working adapter'):
        registry.toggle_source('ireps', True)


def test_export_prefers_portable_pdf_link(tmp_path):
    local = tmp_path / 'bid.pdf'
    local.write_bytes(b'%PDF-test')
    wb = openpyxl.Workbook()
    try:
        sheet = _write_sheet(wb, 'Portable', [{'bid_no': 'TEST', 'local_pdf_path': str(local),
                                              'pdf_url': 'https://example.test/bid.pdf'}])
        assert sheet.cell(2, 19).hyperlink.target == 'https://example.test/bid.pdf'
        sheet = _write_sheet(wb, 'Local', [{'bid_no': 'TEST', 'local_pdf_path': str(local)}])
        assert sheet.cell(2, 19).value == 'Local PDF (this PC)'
        assert sheet.cell(2, 19).hyperlink.target.startswith('file:///')
    finally:
        wb.close()
