"""
One-time, idempotent migration: text posts + reactions from the old
combined app's SQLite backup -> social's Postgres (socialdb).

Scope, per explicit decision with the user:
  - Text posts only. File posts (and any post/reaction that references one)
    are dropped entirely -- restoring them would require also copying the
    actual uploaded file bytes off the source droplet's filesystem, which
    is out of scope for this pass.
  - Posts/reactions from the users excluded in migrate_users.py (blank
    username, and known test/junk accounts) are dropped too.
  - Original created_at timestamps are preserved so feed ordering/history
    stays accurate.

Must be run AFTER migrate_users.py -- this script looks up each post's
author by username in auth-service's Postgres and skips any post whose
author isn't there.

Usage:
    SQLITE_PATH=/path/to/drive.db \\
    SOCIAL_DATABASE_URL=postgresql+asyncpg://social:...@host/socialdb \\
    AUTH_DATABASE_URL=postgresql+asyncpg://authservice:...@host/authdb \\
        python migrate_posts.py [--dry-run]

Idempotent: tags every migrated post with its old id in a comment-free way
by checking (via a side table) whether that old id was already migrated;
safe to re-run after a partial failure.
"""
import argparse
import asyncio
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey,
    Integer, String, Table, Text, UniqueConstraint, select, text,
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SocialBase(DeclarativeBase):
    pass


class Post(SocialBase):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    post_type: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parent_post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)


class Reaction(SocialBase):
    __tablename__ = "reactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reaction_type: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# Migration bookkeeping tables, created if they don't exist -- map old
# SQLite ids to new Postgres ids so re-runs are safe (skip already-migrated
# rows instead of re-inserting and hitting unique constraints), and so
# replies can resolve their parent's new post id.
MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _post_migration_map (
    old_post_id INTEGER PRIMARY KEY,
    new_post_id INTEGER NOT NULL
)
"""
REACTION_MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _reaction_migration_map (
    old_post_id INTEGER NOT NULL,
    old_user_id INTEGER NOT NULL,
    PRIMARY KEY (old_post_id, old_user_id)
)
"""

EXCLUDED_USER_IDS = {27, 24, 30, 31, 33, 34, 45}  # same set as migrate_users.py


async def main(sqlite_path: str, social_db_url: str, auth_db_url: str, dry_run: bool) -> None:
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row

    old_users = {row["id"]: row["username"] for row in conn.execute("SELECT id, username FROM users")}

    all_posts = conn.execute(
        "SELECT id, user_id, post_type, content, caption, created_at, parent_post_id, is_reply "
        "FROM posts ORDER BY is_reply ASC, id ASC"  # parents before replies
    ).fetchall()
    all_reactions = conn.execute(
        "SELECT post_id, user_id, reaction_type, created_at FROM reactions"
    ).fetchall()
    conn.close()

    auth_engine = create_async_engine(auth_db_url)
    AuthSession = async_sessionmaker(auth_engine, expire_on_commit=False)
    username_to_uuid: dict[str, str] = {}
    async with AuthSession() as db:
        result = await db.execute(text("SELECT id, username FROM users"))
        for row in result:
            username_to_uuid[row.username] = str(row.id)
    await auth_engine.dispose()

    social_engine = create_async_engine(social_db_url)
    SocialSession = async_sessionmaker(social_engine, expire_on_commit=False)

    async with SocialSession() as db:
        await db.execute(text(MIGRATION_TABLE_SQL))
        await db.execute(text(REACTION_MIGRATION_TABLE_SQL))
        await db.commit()

        already_migrated_reactions = {
            (row.old_post_id, row.old_user_id)
            for row in (await db.execute(text("SELECT old_post_id, old_user_id FROM _reaction_migration_map")))
        }

        already_migrated = {
            row.old_post_id: row.new_post_id
            for row in (await db.execute(text("SELECT old_post_id, new_post_id FROM _post_migration_map")))
        }

        # First pass: figure out which old post ids are eligible at all,
        # so replies can check whether their parent survived.
        eligible_post_ids: set[int] = set()
        skipped_file = skipped_excluded_user = skipped_orphan_reply = skipped_no_author = 0

        for p in all_posts:
            if p["post_type"] == "file":
                skipped_file += 1
                continue
            if p["user_id"] in EXCLUDED_USER_IDS:
                skipped_excluded_user += 1
                continue
            author = old_users.get(p["user_id"])
            if author not in username_to_uuid:
                skipped_no_author += 1
                continue
            if p["is_reply"] and p["parent_post_id"] not in eligible_post_ids:
                skipped_orphan_reply += 1
                continue
            eligible_post_ids.add(p["id"])

        migrated_posts = 0
        for p in all_posts:
            if p["id"] not in eligible_post_ids:
                continue
            if p["id"] in already_migrated:
                continue

            author_uuid = username_to_uuid[old_users[p["user_id"]]]
            new_parent_id = already_migrated.get(p["parent_post_id"]) if p["is_reply"] else None

            if dry_run:
                # Fake an id so downstream reply/reaction lookups in this
                # dry run can still resolve against it -- real run assigns
                # the true Postgres-generated id via db.flush() below.
                already_migrated[p["id"]] = f"dryrun-{p['id']}"
                migrated_posts += 1
                continue

            new_post = Post(
                user_id=author_uuid,
                post_type="text",
                content=p["content"],
                caption=None,
                created_at=datetime.fromisoformat(p["created_at"]).replace(tzinfo=timezone.utc),
                parent_post_id=new_parent_id,
                is_reply=bool(p["is_reply"]),
            )
            db.add(new_post)
            await db.flush()
            already_migrated[p["id"]] = new_post.id
            await db.execute(
                text("INSERT INTO _post_migration_map (old_post_id, new_post_id) VALUES (:o, :n)"),
                {"o": p["id"], "n": new_post.id},
            )
            migrated_posts += 1

        if not dry_run:
            await db.commit()

        migrated_reactions = already_present_reactions = 0
        skipped_reaction_no_post = skipped_reaction_excluded_user = skipped_reaction_no_author = 0
        for r in all_reactions:
            if (r["post_id"], r["user_id"]) in already_migrated_reactions:
                already_present_reactions += 1
                continue
            new_post_id = already_migrated.get(r["post_id"])
            if new_post_id is None:
                skipped_reaction_no_post += 1
                continue
            if r["user_id"] in EXCLUDED_USER_IDS:
                skipped_reaction_excluded_user += 1
                continue
            author = old_users.get(r["user_id"])
            if author not in username_to_uuid:
                skipped_reaction_no_author += 1
                continue

            if dry_run:
                migrated_reactions += 1
                continue

            db.add(Reaction(
                post_id=new_post_id,
                user_id=username_to_uuid[author],
                reaction_type=r["reaction_type"],
                created_at=datetime.fromisoformat(r["created_at"]).replace(tzinfo=timezone.utc),
            ))
            await db.execute(
                text("INSERT INTO _reaction_migration_map (old_post_id, old_user_id) VALUES (:p, :u)"),
                {"p": r["post_id"], "u": r["user_id"]},
            )
            migrated_reactions += 1

        if not dry_run:
            await db.commit()

    await social_engine.dispose()

    label = "[DRY RUN] Would migrate" if dry_run else "Migrated"
    print(f"{label} {migrated_posts} posts, {migrated_reactions} reactions")
    print(
        f"Skipped -- file posts: {skipped_file}, excluded-user posts: {skipped_excluded_user}, "
        f"no matching auth-service account: {skipped_no_author}, orphaned reply (parent was a file post): {skipped_orphan_reply}"
    )
    print(
        f"Skipped reactions -- target post not migrated: {skipped_reaction_no_post}, "
        f"excluded user: {skipped_reaction_excluded_user}, no matching account: {skipped_reaction_no_author}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sqlite_path = os.environ["SQLITE_PATH"]
    social_db_url = os.environ["SOCIAL_DATABASE_URL"]
    auth_db_url = os.environ["AUTH_DATABASE_URL"]
    assert os.path.exists(sqlite_path), f"SQLite file not found: {sqlite_path}"

    asyncio.run(main(sqlite_path, social_db_url, auth_db_url, args.dry_run))
