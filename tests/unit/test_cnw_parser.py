from __future__ import annotations

from praxis_core.newswire.cnw import _href_as_str, parse_cnw_listing


def test_parse_cnw_listing_accepts_string_href() -> None:
    html = """
    <div class="row newsCards">
      <a href="/news-releases/sample-release-873169666.html">
        <h3><small>09:00 ET</small> Sample title</h3>
        <p class="remove-outline">Issuer update (TSX: ABC)</p>
      </a>
    </div>
    """

    releases = parse_cnw_listing(html)

    assert len(releases) == 1
    assert releases[0].url == "https://www.newswire.ca/news-releases/sample-release-873169666.html"
    assert releases[0].ticker == "ABC"
    assert releases[0].exchange == "TSX"


def test_href_as_str_accepts_attribute_lists() -> None:
    assert _href_as_str(["/news-releases/sample-release-873169666.html"]) == (
        "/news-releases/sample-release-873169666.html"
    )
