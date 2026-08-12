import urllib.request


def test_fixture_portal_serves_login_page(fixture_portal_url):
    with urllib.request.urlopen(f"{fixture_portal_url}/login.html") as response:
        body = response.read().decode()

    assert response.status == 200
    assert 'id="login-form"' in body
