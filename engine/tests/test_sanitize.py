from atlas_engine.llm.sanitize import sanitize_dom, sanitize_text_value


def test_sanitize_dom_strips_text_but_keeps_structure():
    html = """
    <div id="patient-card">
      <span class="name">Jane Alvarez</span>
      <input type="password" value="Sup3rSecret!">
      <button data-testid="submit-btn">Submit Referral</button>
    </div>
    """
    sanitized = sanitize_dom(html)

    assert "Jane Alvarez" not in sanitized
    assert "Sup3rSecret!" not in sanitized
    assert "Submit Referral" not in sanitized
    assert 'data-testid="submit-btn"' in sanitized
    assert 'id="patient-card"' in sanitized
    assert "<button" in sanitized


def test_sanitize_text_value_redacts_everything():
    assert sanitize_text_value("Jane Alvarez") == "<REDACTED>"
    assert sanitize_text_value("") == ""
