import uuid

from click.testing import CliRunner

from atlas_engine.auth.passwords import verify_password
from atlas_engine.cli import main
from atlas_engine.db import session_scope
from atlas_engine.db.models import Organization, User


def test_create_user_command_creates_a_user_with_hashed_password():
    with session_scope() as session:
        org = Organization(name="Create User Org", slug=f"create-user-org-{uuid.uuid4()}")
        session.add(org)
        session.flush()
        org_id = str(org.id)

    email = f"newuser-{uuid.uuid4()}@example.com"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "create-user",
            "--org-id", org_id,
            "--email", email,
            "--password", "hunter2-but-better",
            "--role", "member",
            "--job-function", "developer",
        ],
    )

    assert result.exit_code == 0, result.output

    with session_scope() as session:
        user = session.query(User).filter_by(email=email).one()
        assert user.role == "member"
        assert user.job_function == "developer"
        assert user.password_hash != "hunter2-but-better"
        assert verify_password("hunter2-but-better", user.password_hash) is True
