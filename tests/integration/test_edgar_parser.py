from __future__ import annotations

from services.pollers.edgar_8k import _parse_accession_from_link, _parse_feed

SAMPLE_ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Latest Filings - Tue, 18 Apr 2026 10:00:00 EDT</title>
  <entry>
    <title>8-K - NVIDIA CORP (0001045810) (Filer)</title>
    <link rel="alternate" type="text/html" href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001045810&amp;type=8-K&amp;accession-number=0001045810-26-000047" />
    <summary type="html">
      Form Type: 8-K Accession Number: 0001045810-26-000047 Filed: 2026-04-18
    </summary>
    <updated>2026-04-18T10:00:00-04:00</updated>
    <category scheme="https://www.sec.gov/" label="form type" term="8-K" />
  </entry>
</feed>
"""


def test_parse_accession_from_link_via_query() -> None:
    link = "https://www.sec.gov/?accession-number=0001045810-26-000047"
    assert _parse_accession_from_link(link, "") == "0001045810-26-000047"


def test_parse_accession_from_title() -> None:
    assert (
        _parse_accession_from_link("https://x", "Accession 0001045810-26-000047 Filed")
        == "0001045810-26-000047"
    )


def test_parse_feed_extracts_filing() -> None:
    filings = _parse_feed(SAMPLE_ATOM, form_filter={"8-K"})
    assert len(filings) == 1
    f = filings[0]
    assert f.accession == "0001045810-26-000047"
    assert f.form_type == "8-K"
    assert f.cik == "0001045810"


def test_parse_feed_filter_excludes_other_forms() -> None:
    filings = _parse_feed(SAMPLE_ATOM, form_filter={"10-Q"})
    assert len(filings) == 0
