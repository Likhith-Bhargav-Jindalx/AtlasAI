from atlas_engine.brain.models import TrajectoryStep
from atlas_engine.llm.sanitize import (
    hint_tokens_for_step,
    hint_tokens_from_selectors,
    sanitize_dom,
    sanitize_html_for_diagnosis,
    sanitize_text_value,
)


def test_sanitize_dom_redacts_free_text_but_preserves_controls():
    html = """
    <div id="patient-card">
      <span class="name">Jane Alvarez</span>
      <p class="notes">Patient diagnosed with acute hypertension.</p>
      <input type="password" value="Sup3rSecret!">
      <button data-testid="submit-btn">Submit Referral</button>
      <a href="/reports" class="nav-link">Monthly Reports</a>
    </div>
    """
    sanitized = sanitize_dom(html)

    # Free-text patient data redacted
    assert "Jane Alvarez" not in sanitized
    assert "acute hypertension" not in sanitized
    assert "Sup3rSecret!" not in sanitized

    # Structure & control labels preserved
    assert 'data-testid="submit-btn"' in sanitized
    assert 'id="patient-card"' in sanitized
    assert "Submit Referral" in sanitized
    assert "Monthly Reports" in sanitized


def test_hint_tokens_from_selectors():
    selectors = [
        {"selector": 'button.bx--btn:has-text("Export Claims") >> visible=true'},
        {"selector": '#claim-table [data-testid="download-btn"]'},
    ]
    tokens = hint_tokens_from_selectors(selectors)
    assert "Export Claims" in tokens
    assert "bx--btn" in tokens
    assert "claim-table" in tokens
    assert "download-btn" in tokens


def test_hint_tokens_for_step():
    step = TrajectoryStep(
        index=3,
        kind="click",
        selector_candidates=[{"selector": 'button#submit-form[name="action"]', "rank": 0}],
        value=None,
        expected_effect="dom_changed",
        identity={"id": "submit-form", "text": "Submit Form Now"},
    )
    tokens = hint_tokens_for_step(step)
    assert "submit-form" in tokens or "submit" in tokens
    assert "Submit" in tokens or "Form" in tokens


def test_sanitize_html_windowing():
    html = """
    <html>
      <head><title>Long Document</title></head>
      <body>
        <div>""" + "A" * 3000 + """</div>
        <div id="target-section">
          <button id="target-btn">Target Action</button>
        </div>
        <div>""" + "B" * 3000 + """</div>
      </body>
    </html>
    """
    sanitized = sanitize_html_for_diagnosis(html, hint_tokens=["target-btn"])
    assert "target-btn" in sanitized
    assert "Target Action" in sanitized


def test_sanitize_text_value_redacts_everything():
    assert sanitize_text_value("Jane Alvarez") == "<REDACTED>"
    assert sanitize_text_value("") == ""

