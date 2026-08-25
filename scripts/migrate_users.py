"""
One-time, idempotent migration: the old combined app's SQLite users table
(from a DigitalOcean droplet backup) -> auth-service's Postgres.

Does NOT migrate passwords -- old and new both happen to use bcrypt, but a
clean break to random passwords + manual reset was chosen deliberately.
Migrated users get a random password and must be told to reset it
out-of-band; this script only prints who was migrated, it sends nothing.

Usage:
    SQLITE_PATH=/path/to/drive.db DATABASE_URL=postgresql+asyncpg://... \\
        python migrate_users.py [--dry-run]

Idempotent: checks username existence in Postgres before each insert, safe
to re-run after a partial failure.
"""
import argparse
import asyncio
import os
import secrets
import sqlite3

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

# Minimal inline model -- avoids requiring home-server-auth to be checked
# out at a specific relative path just to run this script.
from sqlalchemy import Boolean, DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
import uuid


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        Enum("free", "power", "super", name="user_role"), nullable=False, default="free"
    )
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[uuid.UUID] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Old SQLite user ids to skip entirely -- confirmed with the user as blank
# username, or obvious test/junk accounts with no real activity.
EXCLUDED_IDS = {
    27,  # blank username
    24,  # non_admin
    30,  # U723
    31,  # UAE73
    33,  # ABC
    34,  # test
    45,  # 12345
}

# Old is_admin=1 users who should land as "super" in the new role model.
# is_admin=0 users default to "free". No old-schema concept of "power",
# so any power-tier assignments need to be added here manually.
POWER_USERNAMES: set[str] = set()


async def main(sqlite_path: str, database_url: str, dry_run: bool) -> None:
    conn = sqlite3.connect(sqlite_path)
    rows = conn.execute("SELECT id, username, is_admin FROM users ORDER BY id").fetchall()
    conn.close()

    to_migrate = [(uid, username, is_admin) for uid, username, is_admin in rows if uid not in EXCLUDED_IDS]

    engine = create_async_engine(database_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    migrated, skipped = [], []
    async with Session() as db:
        for old_id, username, is_admin in to_migrate:
            existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
            if existing:
                skipped.append(username)
                continue

            if username in POWER_USERNAMES:
                role = "power"
            elif is_admin:
                role = "super"
            else:
                role = "free"

            random_pw = secrets.token_urlsafe(24)
            pw_hash = bcrypt.hashpw(random_pw.encode(), bcrypt.gensalt()).decode()

            if dry_run:
                migrated.append(f"{username} (old id {old_id}) -> role={role}")
                continue

            db.add(User(username=username, password_hash=pw_hash, role=role))
            migrated.append(f"{username} (old id {old_id}) -> role={role}")

        if not dry_run:
            await db.commit()

    await engine.dispose()

    print(f"{'[DRY RUN] Would migrate' if dry_run else 'Migrated'} ({len(migrated)}):")
    for m in migrated:
        print(f"  {m}")
    if skipped:
        print(f"\nSkipped, already exist ({len(skipped)}):")
        for s in skipped:
            print(f"  {s}")
    if not dry_run:
        print(
            "\nNo passwords were preserved. Migrated users need a password "
            "reset communicated manually (out of band -- this script does "
            "not send anything)."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sqlite_path = os.environ["SQLITE_PATH"]
    database_url = os.environ["DATABASE_URL"]
    assert os.path.exists(sqlite_path), f"SQLite file not found: {sqlite_path}"

    asyncio.run(main(sqlite_path, database_url, args.dry_run))
