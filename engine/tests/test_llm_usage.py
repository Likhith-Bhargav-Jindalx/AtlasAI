import uuid

from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, UsageLedgerEntry
from atlas_engine.llm.usage import list_llm_usage_summary, record_llm_usage


def test_record_llm_usage_computes_cost_from_the_pricing_table():
    with session_scope() as session:
        org = Organization(name="Usage Org", slug=f"usage-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=1000, output_tokens=1000)

    with session_scope() as session:
        entry = session.query(UsageLedgerEntry).filter_by(org_id=uuid.UUID(org_id)).one()
        assert entry.tokens_in == 1000
        assert entry.tokens_out == 1000
        assert entry.provider == "azure_openai"
        assert float(entry.cost_usd) > 0


def test_record_llm_usage_defaults_unknown_model_cost_to_zero():
    with session_scope() as session:
        org = Organization(name="Usage Unknown Model Org", slug=f"usage-unknown-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    record_llm_usage(org_id, None, model="some-future-model", input_tokens=500, output_tokens=500)

    with session_scope() as session:
        entry = session.query(UsageLedgerEntry).filter_by(org_id=uuid.UUID(org_id)).one()
        assert float(entry.cost_usd) == 0.0


def test_list_llm_usage_summary_aggregates_totals_and_by_day():
    with session_scope() as session:
        org = Organization(name="Usage Summary Org", slug=f"usage-summary-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=100, output_tokens=50)
    record_llm_usage(org_id, None, model="gpt-4o", input_tokens=200, output_tokens=100)

    summary = list_llm_usage_summary(org_id)
    assert summary["total_calls"] == 2
    assert summary["total_input_tokens"] == 300
    assert summary["total_output_tokens"] == 150
    assert summary["total_cost_usd"] > 0
    assert len(summary["by_day"]) == 1
    assert summary["by_day"][0]["calls"] == 2
