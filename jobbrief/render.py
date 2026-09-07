"""HTML for the two emails: the brief (Markdown from the model, plus the backlog line and
the sheet link) and the heartbeat. Inline styles because mail clients strip <style>."""

import html
import re


STYLE = {
    "body": "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:15px;line-height:1.45;color:#1c1c1c;max-width:720px;margin:0 auto;padding:16px",
    "h1": "font-size:20px;margin:24px 0 8px", "h2": "font-size:17px;margin:22px 0 6px", "h3": "font-size:15px;margin:18px 0 4px",
    "p": "margin:0 0 10px", "li": "margin:0 0 8px", "a": "color:#0b57d0",
    "footer": "margin-top:28px;padding-top:12px;border-top:1px solid #ddd;font-size:13px;color:#555",
}


def inline_markdown(text):
    """Bold, links, and bare URLs. Everything else is escaped."""
    text = html.escape(text, quote=False)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", rf'<a style="{STYLE["a"]}" href="\2">\1</a>', text)
    text = re.sub(r"(?<![\"'>=])(https?://[^\s<)]+)", rf'<a style="{STYLE["a"]}" href="\1">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text


def markdown_to_html(markdown):
    """Enough Markdown for the brief: headings, ordered and bulleted lists, paragraphs,
    bold, links. Line breaks inside a list item or paragraph become <br> so the
    per-pick lines stay stacked. Blank lines end a paragraph but not a list, since the
    model separates numbered picks with blank lines. A list that does start fresh keeps
    the number the model wrote. Inline styles because mail clients strip <style>."""
    out, block, kind, list_start = [], [], None, 1

    def flush():
        nonlocal block, kind
        if not block:
            return
        if kind in ("ol", "ul"):
            items = "".join(f'<li style="{STYLE["li"]}">{"<br>".join(map(inline_markdown, item))}</li>' for item in block)
            start_attr = f' start="{list_start}"' if kind == "ol" and list_start != 1 else ""
            out.append(f'<{kind}{start_attr} style="padding-left:22px;margin:0 0 12px">{items}</{kind}>')
        else:
            out.append(f'<p style="{STYLE["p"]}">{"<br>".join(map(inline_markdown, block))}</p>')
        block, kind = [], None

    for raw in markdown.splitlines():
        line = raw.rstrip()
        heading = re.match(r"^(#{1,3})\s+(.*)", line)
        item = re.match(r"^(\d+)\.\s+(.*)|^[-*]\s+(.*)", line)
        if not line:
            if kind == "p":
                flush()
        elif heading:
            flush()
            tag = f"h{len(heading.group(1))}"
            out.append(f'<{tag} style="{STYLE[tag]}">{inline_markdown(heading.group(2))}</{tag}>')
        elif item:
            new_kind = "ol" if item.group(1) else "ul"
            if kind != new_kind:
                flush()
                list_start = int(item.group(1)) if new_kind == "ol" else 1
            kind = new_kind
            block.append([item.group(2) if new_kind == "ol" else item.group(3)])
        elif kind in ("ol", "ul") and raw.startswith((" ", "\t")):
            block[-1].append(line.strip())  # indented continuation of the current item
        else:
            if kind in ("ol", "ul"):
                flush()
            kind = "p"
            block.append(line)
    flush()
    return "\n".join(out)


def backlog_html(postings_rows):
    """One line: how many picks already in the sheet still have a blank status, and how
    old the oldest is. Computed here, not by the model, so the number is exact."""
    if not postings_rows:
        return ""
    pending = [r for r in postings_rows if not r.get("status", "").strip()]
    if not pending:
        line = "Every pick in the sheet has a status."
    else:
        oldest = min((r["date_seen"] for r in pending if r.get("date_seen")), default="")
        line = f"<strong>{len(pending)} earlier picks still have no status</strong>, oldest from {oldest}."
    return f'<p style="{STYLE["footer"]}">{line}</p>'


def render(brief_markdown, sheet_id, postings_rows):
    """The email body: the brief, the backlog line, and a link to the tracking sheet."""
    body = markdown_to_html(brief_markdown) + backlog_html(postings_rows)
    footer = ""
    if sheet_id:
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        footer = (f'<div style="{STYLE["footer"]}">Tracking sheet: <a style="{STYLE["a"]}" href="{url}">{url}</a>'
                  f"<br>Mark status on the Postings tab to change what the next brief says.</div>")
    return f'<div style="{STYLE["body"]}">{body}{footer}</div>'


def heartbeat_html(quiet_days, stats, postings_rows):
    skipped = stats.get("skipped_sources", [])
    since = f"{quiet_days} days since the last email" if quiet_days is not None else "no email on record yet"
    sources = f"{len(skipped)} sources failed to fetch:<br>" + "<br>".join(html.escape(u) for u in skipped) if skipped else "all sources reachable"
    return (f'<div style="{STYLE["body"]}"><p style="{STYLE["p"]}">Still running. {since}, nothing new to show today.</p>'
            f'<p style="{STYLE["p"]}">Today: {stats.get("new_candidates", 0)} new candidates after dedup, {sources}.</p>'
            f'{backlog_html(postings_rows)}</div>')
