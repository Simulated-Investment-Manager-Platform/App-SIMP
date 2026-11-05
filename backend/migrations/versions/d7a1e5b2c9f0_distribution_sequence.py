"""corporate_action: distribution sequence number in the key; wider type codes

Revision ID: d7a1e5b2c9f0
Revises: c048898323c4
"""

import sqlalchemy as sa
from alembic import op

revision = "d7a1e5b2c9f0"
down_revision = "c048898323c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("corporate_action", sa.Column("seq", sa.Integer(), server_default="0", nullable=False))
    op.alter_column("corporate_action", "type", type_=sa.String(16))
    op.drop_constraint("corporate_action_pkey", "corporate_action", type_="primary")
    op.create_primary_key("corporate_action_pkey", "corporate_action", ["permno", "ex_date", "type", "seq"])
    op.alter_column("ca_event", "kind", type_=sa.String(16))


def downgrade() -> None:
    op.drop_constraint("corporate_action_pkey", "corporate_action", type_="primary")
    op.execute("delete from corporate_action where seq <> 0")
    op.create_primary_key("corporate_action_pkey", "corporate_action", ["permno", "ex_date", "type"])
    op.drop_column("corporate_action", "seq")
