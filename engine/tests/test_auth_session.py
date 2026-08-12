from atlas_engine.auth.session import create_session_token, verify_session_token


def test_create_and_verify_session_token_round_trips():
    token = create_session_token({"user_id": "abc", "org_id": "def"})

    payload = verify_session_token(token)

    assert payload["user_id"] == "abc"
    assert payload["org_id"] == "def"


def test_verify_session_token_rejects_a_tampered_token():
    token = create_session_token({"user_id": "abc"})
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    assert verify_session_token(tampered) is None


def test_verify_session_token_rejects_an_expired_token():
    token = create_session_token({"user_id": "abc"}, max_age_seconds=-10)

    assert verify_session_token(token) is None


def test_verify_session_token_rejects_malformed_input():
    assert verify_session_token("not-a-real-token") is None
    assert verify_session_token("") is None
