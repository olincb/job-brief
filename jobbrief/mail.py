"""Send one email through Amazon SES over SMTP: `send` builds a multipart/alternative
message and delivers it with STARTTLS. Standard library only; the code never calls the
SES API, so there is no AWS request signing. The credentials and sender are deployment
values read from the environment, the same way `cli` reads the Gemini key."""

import os
import smtplib
import ssl
from email.message import EmailMessage


SMTP_PORT = 587  # SES STARTTLS submission port; the same for every deployment

# The user sees no detail: a failed run is the operator's to diagnose, and the sheet id
# and exception that name it must never reach the recipient.
USER_FAILURE_NOTICE = "Your brief did not go out today. The operator has been told and will look into it."


def operator_failure_notice(sheet_id, error):
    """The operator's copy of a failed run: which sheet to open and what went wrong. The
    user's address rides only the To header, never the subject or body."""
    return f"A run failed for sheet {sheet_id}.\n\n{error}"


def build_message(to, subject, html, text, from_addr):
    """A multipart/alternative message with the plain-text part first, so a client that
    ignores HTML still shows the readable Markdown the brief was rendered from."""
    message = EmailMessage()
    message["From"] = from_addr
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def send(to, subject, html, text):
    """Deliver one email to `to` through SES over STARTTLS."""
    message = build_message(to, subject, html, text, os.environ["BRIEF_FROM"])
    with smtplib.SMTP(os.environ["SES_SMTP_HOST"], SMTP_PORT) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(os.environ["SES_SMTP_USER"], os.environ["SES_SMTP_PASS"])
        smtp.send_message(message)
