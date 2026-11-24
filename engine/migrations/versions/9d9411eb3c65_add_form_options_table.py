"""add form options table

Revision ID: 9d9411eb3c65
Revises: aa6b254c56a8
Create Date: 2026-11-24 13:28:00.189718

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d9411eb3c65'
down_revision: Union[str, Sequence[str], None] = 'aa6b254c56a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Seed values: every string currently hardcoded in frontend/src/lib/recordingFormSchema.ts's
# CLIENT_OPTIONS/ACTION_OPTIONS/EHR_OPTIONS (as of 2026-11-24), so the cutover to this
# DB-backed table loses nothing. Seeded per-organization, once for every existing org.
_CLIENT_OPTIONS = {
    "AR": [
        "Audentes CSSL CSSC THSC", "Audentes CPI", "Audentes CSSA", "Audentes XRS",
        "Houston Heart Health", "Maiden Lane Medical", "MRI", "Pediatric Associates at Ridge",
        "Parkside Psychiatric Hospital Clinic", "Penta", "Tricity", "Voyage Health",
        "Urgent Care", "NHS", "PRMC",
    ],
    "EV": [
        "Audentes Verification", "Voyage sales Verification", "Voyage intake Verification",
        "Housten Heart", "Pediatrics Ridge", "MRI Memorial Verification", "MLM", "TUC",
        "STKS", "NHS", "Healing Biologics",
    ],
}
_ACTION_OPTIONS = {
    "AR": [
        "Report Extraction", "Claim Submission", "Claim Correction", "Claim Resubmission",
        "Claim Reconsideration", "Void Claim", "Replacement Claim", "Secondary/Tertiary Billing",
        "Claim Rejection", "Denial Review", "Denial Correction", "Denial Resubmission",
        "Denial Appeal", "Appeal", "Peer-to-Peer Review", "Medical Necessity Appeal",
        "Coding Appeal", "Authorization Appeal", "Claim Status Follow-up", "Payer Call",
        "Payer Portal Follow-up", "Pending Claim Follow-up", "Medical Records Request",
        "Documentation Submission", "Payer Escalation", "Payment Posting",
        "Underpayment Review", "Underpayment Appeal", "Contractual Adjustment",
        "Payment Variance Review", "Recoupment Review", "Refund Processing",
        "Eligibility Verification", "Insurance Discovery", "Insurance Update", "COB Update",
        "Payer Correction", "Subscriber Information Correction", "Authorization Verification",
        "Patient Balance Review", "Patient Statement Generation", "Patient Billing",
        "Patient Payment Follow-up", "Payment Plan Setup", "Financial Assistance Review",
        "Patient Responsibility Adjustment", "Bad Debt Transfer", "Write-off",
        "Small Balance Write-off", "Administrative Adjustment", "Charity Care Adjustment",
        "Bad Debt", "Account Closure", "Zero Balance Resolution",
        "Duplicate Account/Claim Resolution", "Credit Balance Resolution",
    ],
    "EV": ["Portal Verification", "Note Posting", "Report Extraction"],
}
_EHR_OPTIONS = {
    "AR": ["HST", "eCW", "EXA", "Netsmart", "Modmed", "Brightree", "Experity", "Cerner", "Athena"],
    "EV": ["eCW", "Brightree", "Exa", "Experity", "Cerner", "Healing Biologix"],
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "form_options",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("lob", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "lob", "category", "label"),
    )

    bind = op.get_bind()
    org_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM organizations")).fetchall()]
    form_options_table = sa.table(
        "form_options",
        sa.column("id", sa.Uuid()), sa.column("org_id", sa.Uuid()),
        sa.column("lob", sa.String()), sa.column("category", sa.String()),
        sa.column("label", sa.String()), sa.column("active", sa.Boolean()),
    )
    for org_id in org_ids:
        rows = []
        for category, options_by_lob in (
            ("client", _CLIENT_OPTIONS), ("action", _ACTION_OPTIONS), ("ehr", _EHR_OPTIONS),
        ):
            for lob, labels in options_by_lob.items():
                rows.extend(
                    {"id": uuid.uuid4(), "org_id": org_id, "lob": lob, "category": category, "label": label, "active": True}
                    for label in labels
                )
        bind.execute(form_options_table.insert(), rows)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("form_options")
