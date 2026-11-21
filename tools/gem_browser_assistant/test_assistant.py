from tools.gem_browser_assistant.assistant import is_gem_url


def test_accepts_geM_root_and_subdomains():
    assert is_gem_url("https://gem.gov.in/")
    assert is_gem_url("https://bidplus.gem.gov.in/all-bids")


def test_rejects_non_https_and_lookalike_domains():
    assert not is_gem_url("http://gem.gov.in/")
    assert not is_gem_url("https://gem.gov.in.example.com/")
    assert not is_gem_url("https://example.com/?next=https://gem.gov.in/")
