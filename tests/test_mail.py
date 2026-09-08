from jobbrief.mail import USER_FAILURE_NOTICE, build_message, operator_failure_notice


def test_build_message_headers_and_alternative_order():
    message = build_message(
        "user@example.com", "Your job brief", "<p>hi</p>", "hi", "brief@example.com"
    )
    assert message["To"] == "user@example.com"
    assert message["From"] == "brief@example.com"
    assert message["Subject"] == "Your job brief"
    assert message.get_content_type() == "multipart/alternative"
    parts = message.get_payload()
    # Plain text first so a client without HTML still shows the readable brief.
    assert parts[0].get_content_type() == "text/plain"
    assert parts[1].get_content_type() == "text/html"


def test_failure_notices_name_the_failure_not_the_user():
    operator = operator_failure_notice("sheet-abc123", "RuntimeError: boom")
    assert "sheet-abc123" in operator
    assert "RuntimeError: boom" in operator
    assert "the operator has been told" in USER_FAILURE_NOTICE.lower()
    # No email address in either body; the address rides only the To header.
    assert "@" not in operator and "@" not in USER_FAILURE_NOTICE
