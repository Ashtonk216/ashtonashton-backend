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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "files",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("upload_date", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_files_user_id", "files", ["user_id"])
    op.create_index("ix_files_stored_filename", "files", ["stored_filename"], unique=True)

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("post_type", sa.String(length=10), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("parent_post_id", sa.Integer(), nullable=True),
        sa.Column("is_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("post_type IN ('text', 'file')", name="ck_posts_post_type"),
    )
    op.create_index("ix_posts_user_id", "posts", ["user_id"])
    op.create_foreign_key(
        "fk_posts_parent_post_id", "posts", "posts", ["parent_post_id"], ["id"], ondelete="CASCADE"
    )

    op.create_table(
        "reactions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("reaction_type", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("reaction_type = 'dislike'", name="ck_reactions_type"),
        sa.UniqueConstraint("post_id", "user_id", name="uq_reactions_post_user"),
    )


def downgrade() -> None:
    op.drop_table("reactions")
    op.drop_constraint("fk_posts_parent_post_id", "posts", type_="foreignkey")
    op.drop_index("ix_posts_user_id", table_name="posts")
    op.drop_table("posts")
    op.drop_index("ix_files_stored_filename", table_name="files")
    op.drop_index("ix_files_user_id", table_name="files")
    op.drop_table("files")
    op.drop_table("profiles")
