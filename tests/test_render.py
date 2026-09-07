import re

from jobbrief.render import markdown_to_html, render


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


def test_render_appends_sheet_link_footer(fixture_dir):
    html = render((fixture_dir / "brief.md").read_text(), "sample-sheet-id", [])
    assert 'href="https://docs.google.com/spreadsheets/d/sample-sheet-id"' in html


def test_render_counts_picks_without_a_status(fixture_dir):
    rows = [{"date_seen": "2026-08-20", "status": ""}, {"date_seen": "2026-08-28", "status": " "}, {"date_seen": "2026-08-01", "status": "skip"}]
    html = render((fixture_dir / "brief.md").read_text(), "", rows)
    assert "2 earlier picks still have no status</strong>, oldest from 2026-08-20" in html
