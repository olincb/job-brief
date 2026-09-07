import argparse
import re

from jobbrief import brief
from jobbrief.brief import markdown_to_html


def test_numbered_picks_stay_one_list_across_blank_lines(fixture_dir):
    html = markdown_to_html((fixture_dir / "brief.md").read_text())
    lists = re.findall(r"<ol.*?</ol>", html, re.S)
    assert len(lists) == 1
    assert lists[0].count("<li") == 3
    assert "start=" not in lists[0]


def test_links_and_bold_render(fixture_dir):
    html = markdown_to_html((fixture_dir / "brief.md").read_text())
    assert "<strong>Northwind Tooling</strong>" in html
    assert re.search(r'<a [^>]*href="https://example.com/jobs/1">https://example.com/jobs/1</a>', html)
    assert re.search(r'<a [^>]*href="https://example.com/jobs/2">Posting</a>', html)


def test_render_appends_sheet_link_footer(out, fixture_dir):
    (out / "brief.md").write_text((fixture_dir / "brief.md").read_text())
    brief.cmd_render(argparse.Namespace(sheet_id="sample-sheet-id", postings=str(out / "postings.json")))
    html = (out / "brief.html").read_text()
    assert 'href="https://docs.google.com/spreadsheets/d/sample-sheet-id"' in html
