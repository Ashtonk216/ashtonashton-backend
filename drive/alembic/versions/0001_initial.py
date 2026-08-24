"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.String(length=64), primary_key=True),
        sa.Column("capacity", sa.BigInteger(), nullable=False, server_default="5368709120"),
        sa.Column("current_usage", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(length=64),
            sa.ForeignKey("profiles.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("upload_date", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_files_user_id", "files", ["user_id"])
    op.create_index("ix_files_stored_filename", "files", ["stored_filename"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_files_stored_filename", table_name="files")
    op.drop_index("ix_files_user_id", table_name="files")
    op.drop_table("files")
    op.drop_table("profiles")
