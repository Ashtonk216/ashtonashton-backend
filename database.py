import aiosqlite
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_PATH = os.getenv('DATABASE_PATH')

async def init_db():
    """Initialize the database with required tables"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Create users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                capacity BIGINT DEFAULT 5368709120,
                current_usage BIGINT DEFAULT 0
            )
        """)

        # Create files table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT UNIQUE NOT NULL,
                file_size BIGINT NOT NULL,
                mime_type TEXT,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_path TEXT NOT NULL,
                is_public BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # Create posts table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                post_type TEXT NOT NULL CHECK(post_type IN ('text', 'file')),
                content TEXT,
                file_id INTEGER,
                caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            )
        """)

        # Create reactions table (SQLite syntax) - dislikes only
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reaction_type TEXT NOT NULL CHECK(reaction_type = 'dislike'),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE (post_id, user_id)
            )
        """)


        # Add is_public column to existing files table if it doesn't exist
        try:
            await db.execute("ALTER TABLE files ADD COLUMN is_public BOOLEAN DEFAULT 0")
        except aiosqlite.OperationalError:
            # Column already exists, ignore
            pass

        # Add is_admin column to users table if it doesn't exist
        try:
            await db.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
        except aiosqlite.OperationalError:
            # Column already exists, ignore
            pass

        # Add is_banned column to users table if it doesn't exist
        try:
            await db.execute("ALTER TABLE users ADD COLUMN is_banned BOOLEAN DEFAULT 0")
        except aiosqlite.OperationalError:
            # Column already exists, ignore
            pass

        await db.commit()

async def get_db():
    """Get database connection"""
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()
