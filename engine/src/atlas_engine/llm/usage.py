import uuid
from collections import defaultdict
from decimal import Decimal

from atlas_engine.db import session_scope
from atlas_engine.db.models import UsageLedgerEntry

# $ per 1,000 tokens (input, output). Unrecognized models cost $0 rather than
# erroring — this table only needs entries for models actually configured via
# ATLAS_AZURE_OPENAI_MODEL / AZURE_OPENAI_MODEL; extend it as new models are used.
PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.0025, 0.01),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-5": (0.005, 0.015),
}


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    input_rate, output_rate = PRICING.get(model, (0.0, 0.0))
    return Decimal(str(input_tokens / 1000 * input_rate + output_tokens / 1000 * output_rate))


def record_llm_usage(
    org_id: str, recording_id: str | None, *, model: str, input_tokens: int | None, output_tokens: int | None,
) -> None:
    tokens_in = input_tokens or 0
    tokens_out = output_tokens or 0
    with session_scope() as session:
        session.add(UsageLedgerEntry(
            org_id=uuid.UUID(org_id),
            recording_id=uuid.UUID(recording_id) if recording_id else None,
            provider="azure_openai",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_cost_usd(model, tokens_in, tokens_out),
        ))


def list_llm_usage_summary(org_id: str) -> dict:
    with session_scope() as session:
        entries = session.query(UsageLedgerEntry).filter_by(org_id=uuid.UUID(org_id)).all()

        by_day: dict[str, dict] = defaultdict(lambda: {"calls": 0, "cost_usd": 0.0})
        total_input = total_output = 0
        total_cost = Decimal("0")
        for entry in entries:
            total_input += entry.tokens_in
            total_output += entry.tokens_out
            total_cost += entry.cost_usd
            day_key = entry.created_at.date().isoformat()
            by_day[day_key]["calls"] += 1
            by_day[day_key]["cost_usd"] += float(entry.cost_usd)

        return {
            "total_calls": len(entries),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": float(total_cost),
            "by_day": [{"date": day, **stats} for day, stats in sorted(by_day.items())],
        }
