from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Form, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import aiosqlite
import os
import uuid
from datetime import datetime
from auth import hash_password, verify_password, create_token, get_current_user, get_admin_user
from database import init_db, get_db
from dotenv import load_dotenv
import hashlib
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import re
import logging

app = FastAPI(title="Drive API")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

BLOCKED_EXTENSIONS = {
    # Executables
    '.exe', '.bat', '.cmd', '.com', '.pif', '.scr', '.vbs', '.js', '.jar',
    # Scripts
    '.sh', '.bash', '.ps1', '.psm1',
    # System files
    '.dll', '.sys', '.drv',
    # Potentially dangerous
    '.msi', '.app', '.deb', '.rpm',
    # Other risky formats
    '.hta', '.cpl', '.msc', '.wsf'
}

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ashtonashton.net",
        "https://drive.ashtonashton.net"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

load_dotenv()
# Storage configuration
STORAGE_PATH = os.getenv("STORAGE_PATH")
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE"))  # 500MB limit

@app.on_event("startup")
async def startup():
    """Initialize database on startup"""
    required_env_vars = ["STORAGE_PATH", "MAX_FILE_SIZE", "SECRET_KEY"]
    missing = [var for var in required_env_vars if not os.getenv(var)]

    if missing:
        raise RuntimeError(f"Missing required environment variables: {missing}")
    await init_db()
    # Create storage directory if it doesn't exist
    os.makedirs(STORAGE_PATH, exist_ok=True)

@app.post("/register")
@limiter.limit("2/minute")
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db)
):

    # Default 1 GB capacity in bytes
    capacity_in_bytes = 1 * 1024 * 1024 * 1024
    VOLUME_LIMIT = 40 * 1024 * 1024 * 1024  # 40 GB in bytes

    cursor = await db.execute("SELECT SUM(capacity) as total_capacity FROM users")
    result = await cursor.fetchone()
    current_total_capacity = result[0] if result[0] is not None else 0
    
    if current_total_capacity + capacity_in_bytes > VOLUME_LIMIT:
        raise HTTPException(
            status_code=507,  # 507 Insufficient Storage
            detail=f"Cannot register: Volume limit for userbase exceeded"
        )

    # Hash password
    password_hash = hash_password(password)

    # Insert user into database
    try:
        await db.execute(
            """
            INSERT INTO users (username, password_hash, capacity)
            VALUES (?, ?, ?)
            """,
            (username, password_hash, capacity_in_bytes)
        )
        await db.commit()
        return {"message": "User registered successfully"}
    except aiosqlite.IntegrityError:
        raise HTTPException(status_code=400, detail="Username already exists")

@app.post("/login")
@limiter.limit("10/minute")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Login user"""
    async with db.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,)
    ) as cursor:
        user = await cursor.fetchone()

    if not user or not verify_password(password, user[2]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(user[0], user[1])
    return {"token": token, "username": user[1]}

@app.post("/refresh-token")
async def refresh_token(
    current_user: dict = Depends(get_current_user)
):
    """Refresh JWT token for authenticated user"""
    # User is already authenticated via get_current_user
    # Create a new token with fresh expiration
    new_token = create_token(current_user["id"], current_user["username"])
    return {"token": new_token, "username": current_user["username"]}

@app.get("/files")
async def list_files(
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """List all files for the current user"""
    async with db.execute(
        """SELECT id, original_filename, file_size, mime_type, upload_date
           FROM files WHERE user_id = ? ORDER BY upload_date DESC""",
        (current_user["id"],)
    ) as cursor:
        files = await cursor.fetchall()

    return {
        "files": [
            {
                "id": f[0],
                "filename": f[1],
                "size": f[2],
                "mime_type": f[3],
                "upload_date": f[4]
            }
            for f in files
        ]
    }

@app.post("/upload")
@limiter.limit("20/minute")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Upload a file"""
    # Generate unique filename
    original_filename = sanitize_filename(file.filename)
    file_ext = os.path.splitext(original_filename)[1]
    stored_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(STORAGE_PATH, stored_filename)

    if file_ext in BLOCKED_EXTENSIONS:
        raise HTTPException(
            status_code=400, 
            detail=f"File type {file_ext} is not allowed for security reasons"
        )

    async with db.execute(
        "SELECT capacity - current_usage FROM users WHERE id = ?",
        (current_user["id"],)
    ) as total_cursor:
        remaining_capacity = (await total_cursor.fetchone())[0]

    # Read and save file
    file_size = 0
    try:
        with open(file_path, "wb") as f:
            while chunk := await file.read(8192):
                file_size += len(chunk)
                if file_size > MAX_FILE_SIZE:
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail="File too large (max 500MB)")
                if (remaining_capacity - file_size) < 0:
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail="Storage Capacity has been reached")
                f.write(chunk)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise e

    # Save to database
    async with db.execute(
        """INSERT INTO files (user_id, original_filename, stored_filename,
           file_size, mime_type, file_path)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (current_user["id"], file.filename, stored_filename, file_size,
         file.content_type, file_path)
    ) as cursor:
        await db.commit()
        file_id = cursor.lastrowid

    
    await db.execute(
        "UPDATE users SET current_usage = current_usage + ? WHERE id = ?",
        (file_size, current_user["id"])
    )
    await db.commit()

    return {
        "id": file_id,
        "filename": file.filename,
        "size": file_size,
        "message": "Upload successful"
    }

@app.get("/download/{file_id}")
@limiter.limit("20/minute")
async def download_file(
    request: Request,
    file_id: int,
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Download a file"""
    async with db.execute(
        """SELECT original_filename, file_path
           FROM files WHERE id = ? AND user_id = ?""",
        (file_id, current_user["id"])
    ) as cursor:
        file_record = await cursor.fetchone()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    original_filename, file_path = file_record

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(file_path, filename=original_filename)

@app.delete("/files/{file_id}")
async def delete_file(
    file_id: int,
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Delete a file"""
    async with db.execute(
        "SELECT file_path, file_size FROM files WHERE id = ? AND user_id = ?",
        (file_id, current_user["id"])
    ) as cursor:
        file_record = await cursor.fetchone()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    file_size = file_record[1]
    file_path = file_record[0]

    await db.execute(
        "UPDATE users SET current_usage = MAX(current_usage - ?, 0) WHERE id = ?",
        (file_size, current_user["id"])
    )
    await db.commit()

    # Delete from disk
    if os.path.exists(file_path):
        os.remove(file_path)

    # Delete from database
    await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
    await db.commit()

    return {"message": "File deleted"}

@app.get("/usage")
async def get_usage(
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Return current usage and capacity for the logged-in user"""
    async with db.execute(
        "SELECT current_usage, capacity FROM users WHERE id = ?",
        (current_user["id"],)
    ) as cursor:
        row = await cursor.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    return {"current_usage": row[0], "capacity": row[1]}

# Social Media Endpoints

@app.post("/posts/text")
@limiter.limit("20/minute")
async def create_text_post(
    request: Request,
    content: str = Form(...),
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Create a text-only post"""
    # Validate content length
    if len(content) > 1000:
        raise HTTPException(status_code=400, detail="Text content exceeds 1000 character limit")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    # Insert post into database
    async with db.execute(
        """INSERT INTO posts (user_id, post_type, content)
           VALUES (?, ?, ?)""",
        (current_user["id"], "text", content)
    ) as cursor:
        await db.commit()
        post_id = cursor.lastrowid

    return {
        "id": post_id,
        "post_type": "text",
        "content": content,
        "message": "Text post created successfully"
    }

@app.post("/posts/file")
@limiter.limit("20/minute")
async def create_file_post(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(None),
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Upload a file and create a post"""
    MAX_POST_FILE_SIZE = 100 * 1024 * 1024  # 100MB limit for posts

    # Validate caption length if provided
    if caption and len(caption) > 1000:
        raise HTTPException(status_code=400, detail="Caption exceeds 1000 character limit")

    # Generate unique filename
    original_filename = sanitize_filename(file.filename)
    file_ext = os.path.splitext(original_filename)[1]
    stored_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(STORAGE_PATH, stored_filename)

    if file_ext in BLOCKED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} is not allowed for security reasons"
        )

    # Read and save file
    file_size = 0
    try:
        with open(file_path, "wb") as f:
            while chunk := await file.read(8192):
                file_size += len(chunk)
                if file_size > MAX_POST_FILE_SIZE:
                    os.remove(file_path)
                    raise HTTPException(status_code=413, detail="File too large (max 100MB for posts)")
                f.write(chunk)
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise e

    # Save file to database with is_public=1
    async with db.execute(
        """INSERT INTO files (user_id, original_filename, stored_filename,
           file_size, mime_type, file_path, is_public)
           VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (current_user["id"], file.filename, stored_filename, file_size,
         file.content_type, file_path)
    ) as cursor:
        await db.commit()
        file_id = cursor.lastrowid

    # Create post with file_id
    async with db.execute(
        """INSERT INTO posts (user_id, post_type, file_id, caption)
           VALUES (?, ?, ?, ?)""",
        (current_user["id"], "file", file_id, caption)
    ) as cursor:
        await db.commit()
        post_id = cursor.lastrowid

    return {
        "id": post_id,
        "post_type": "file",
        "file_id": file_id,
        "filename": file.filename,
        "size": file_size,
        "caption": caption,
        "message": "File post created successfully"
    }

@app.get("/feed")
async def get_feed(
    page: int = 1,
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Get chronological feed of posts (newest first, 20 per page)"""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be >= 1")

    limit = 20
    offset = (page - 1) * limit

    # Fetch posts with file information if applicable
    async with db.execute(
        """SELECT p.id, p.user_id, p.post_type, p.content, p.caption,
                  p.created_at, p.file_id,
                  f.original_filename, f.file_size, f.mime_type
           FROM posts p
           LEFT JOIN files f ON p.file_id = f.id
           ORDER BY p.created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset)
    ) as cursor:
        posts = await cursor.fetchall()

    # Build response with dislike counts
    result_posts = []
    for p in posts:
        post_id = p[0]

        # Get dislike count for this post
        async with db.execute(
            "SELECT COUNT(*) FROM reactions WHERE post_id = ?",
            (post_id,)
        ) as cursor:
            dislike_count_row = await cursor.fetchone()

        dislike_count = dislike_count_row[0] if dislike_count_row else 0

        # Check if current user disliked this post
        async with db.execute(
            "SELECT id FROM reactions WHERE post_id = ? AND user_id = ?",
            (post_id, current_user["id"])
        ) as cursor:
            user_dislike_row = await cursor.fetchone()

        is_disliked = user_dislike_row is not None

        result_posts.append({
            "id": post_id,
            "post_type": p[2],
            "content": p[3] if p[2] == "text" else None,
            "caption": p[4],
            "created_at": p[5],
            "is_deletable": p[1] == current_user["id"],
            "dislike_count": dislike_count,
            "is_disliked": is_disliked,  # True if current user disliked
            "file": {
                "id": p[6],
                "filename": p[7],
                "size": p[8],
                "mime_type": p[9]
            } if p[2] == "file" else None
        })

    return {
        "posts": result_posts,
        "page": page,
        "per_page": limit
    }

@app.delete("/posts/{post_id}")
async def delete_post(
    post_id: int,
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Delete a post (only if it belongs to current user)"""
    # Fetch post and check ownership
    async with db.execute(
        """SELECT user_id, post_type, file_id FROM posts WHERE id = ?""",
        (post_id,)
    ) as cursor:
        post = await cursor.fetchone()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post[0] != current_user["id"]:
        raise HTTPException(status_code=403, detail="You can only delete your own posts")

    post_type = post[1]
    file_id = post[2]

    # If it's a file post, get file path and delete from disk
    if post_type == "file" and file_id:
        async with db.execute(
            "SELECT file_path FROM files WHERE id = ?",
            (file_id,)
        ) as cursor:
            file_record = await cursor.fetchone()

        if file_record and os.path.exists(file_record[0]):
            os.remove(file_record[0])

        # Delete file record from database
        await db.execute("DELETE FROM files WHERE id = ?", (file_id,))

    # Delete post from database
    await db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    await db.commit()

    return {"message": "Post deleted successfully"}


@app.get("/posts/{post_id}/download")
async def download_post_file(
    post_id: int,
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Download file from a post"""
    async with db.execute(
        """SELECT f.original_filename, f.file_path
           FROM posts p
           JOIN files f ON p.file_id = f.id
           WHERE p.id = ?""",
        (post_id,)
    ) as cursor:
        file_record = await cursor.fetchone()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    original_filename, file_path = file_record

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(file_path, filename=original_filename)

@app.post("/change_password/{user_id}")
@limiter.limit("1/minute")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Login user"""
    async with db.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?",
        (username,)
    ) as cursor:
        user = await cursor.fetchone()

    if not user or not verify_password(password, user[2]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(user[0], user[1])
    return {"token": token, "username": user[1]}

@app.post("/change-password")
@limiter.limit("3/minute")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Change user password"""
    # Verify current password
    async with db.execute(
        "SELECT password_hash FROM users WHERE id = ?",
        (current_user["id"],)
    ) as cursor:
        user = await cursor.fetchone()

    if not user or not verify_password(current_password, user[0]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    # Validate new password
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    # Hash and update new password
    new_password_hash = hash_password(new_password)
    await db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (new_password_hash, current_user["id"])
    )
    await db.commit()

    return {"message": "Password changed successfully"}


@app.post("/posts/{post_id}/dislike")
async def toggle_dislike(
    post_id: int,
    current_user: dict = Depends(get_current_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Toggle dislike on a post"""
    # Check if post exists
    async with db.execute("SELECT id FROM posts WHERE id = ?", (post_id,)) as cursor:
        post = await cursor.fetchone()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Check if user already disliked this post
    async with db.execute(
        "SELECT id FROM reactions WHERE user_id = ? AND post_id = ?",
        (current_user["id"], post_id)
    ) as cursor:
        existing_dislike = await cursor.fetchone()

    if existing_dislike:
        # Remove dislike (toggle off)
        await db.execute(
            "DELETE FROM reactions WHERE id = ?",
            (existing_dislike[0],)
        )
        await db.commit()
        return {"message": "Dislike removed", "action": "removed", "is_disliked": False}
    else:
        # Add dislike
        await db.execute(
            "INSERT INTO reactions (post_id, user_id, reaction_type) VALUES (?, ?, 'dislike')",
            (post_id, current_user["id"])
        )
        await db.commit()
        return {"message": "Dislike added", "action": "added", "is_disliked": True}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/")
async def root():
    return {"message": "Drive API is running"}

# ===== ADMIN ENDPOINTS =====

@app.get("/admin/users")
async def get_all_users(
    admin_user: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Get all users (admin only)"""
    async with db.execute("""
        SELECT id, username, created_at, is_banned, is_admin,
               (SELECT COUNT(*) FROM posts WHERE user_id = users.id) as post_count
        FROM users
        ORDER BY created_at DESC
    """) as cursor:
        users = await cursor.fetchall()

    return [{
        "id": u[0],
        "username": u[1],
        "created_at": u[2],
        "is_banned": bool(u[3]),
        "is_admin": bool(u[4]),
        "post_count": u[5]
    } for u in users]

@app.post("/admin/users/{user_id}/ban")
async def ban_user(
    user_id: int,
    admin_user: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Ban a user (admin only)"""
    # Don't allow banning yourself
    if user_id == admin_user["id"]:
        raise HTTPException(status_code=400, detail="Cannot ban yourself")

    # Check if user exists
    async with db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)) as cursor:
        user = await cursor.fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Ban the user
    await db.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
    await db.commit()

    # Delete all posts made by that user
    async with db.execute("SELECT id FROM posts WHERE user_id = ?", (user_id,)) as cursor:
        posts = await cursor.fetchall()
    for p in posts:
        if not p:
            continue
        await admin_delete_post(p[0], admin_user, db)

    return {"message": f"User {user[1]} has been banned and posts have been deleted"}

@app.post("/admin/users/{user_id}/unban")
async def unban_user(
    user_id: int,
    admin_user: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Unban a user (admin only)"""
    # Check if user exists
    async with db.execute("SELECT id, username FROM users WHERE id = ?", (user_id,)) as cursor:
        user = await cursor.fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Unban the user
    await db.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
    await db.commit()

    return {"message": f"User {user[1]} has been unbanned"}

@app.delete("/admin/posts/{post_id}")
async def admin_delete_post(
    post_id: int,
    admin_user: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Delete any post (admin only)"""
    # Check if post exists and get file info
    async with db.execute("""
        SELECT posts.id, posts.post_type, posts.file_id, files.file_path, posts.user_id
        FROM posts
        LEFT JOIN files ON posts.file_id = files.id
        WHERE posts.id = ?
    """, (post_id,)) as cursor:
        post = await cursor.fetchone()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post_type = post[1]
    file_id = post[2]
    file_path = post[3]
    user_id = post[4]

    # Delete the post
    await db.execute("DELETE FROM posts WHERE id = ?", (post_id,))

    # If it's a file post, delete the file from storage and database
    if post_type == "file" and file_id:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

        # Get file size for usage tracking
        async with db.execute("SELECT file_size FROM files WHERE id = ?", (file_id,)) as cursor:
            file_info = await cursor.fetchone()
            if file_info:
                file_size = file_info[0]
                # Update user's storage usage
                await db.execute(
                    "UPDATE users SET current_usage = current_usage - ? WHERE id = ?",
                    (file_size, user_id)
                )

        await db.execute("DELETE FROM files WHERE id = ?", (file_id,))

    await db.commit()

    return {"message": "Post deleted successfully"}


"""Returns Posts the same as before but has users attached now"""
@app.get("/admin/feed")
async def get_admin_feed(
    page: int = 1,
    admin_user: dict = Depends(get_admin_user),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Get chronological feed of posts (newest first, 20 per page)"""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be >= 1")

    limit = 20
    offset = (page - 1) * limit

    # Fetch posts with file information if applicable
    async with db.execute(
        """SELECT p.id, p.user_id, p.post_type, p.content, p.caption,
                p.created_at, p.file_id,
                f.original_filename, f.file_size, f.mime_type
        FROM posts p
        LEFT JOIN files f ON p.file_id = f.id
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?""",
        (limit, offset)
    ) as cursor:
        posts = await cursor.fetchall()

    # Build response with dislike counts
    result_posts = []
    for p in posts:
        post_id = p[0]

        # Get dislike count for this post
        async with db.execute(
            "SELECT COUNT(*) FROM reactions WHERE post_id = ?",
            (post_id,)
        ) as cursor:
            dislike_count_row = await cursor.fetchone()

        dislike_count = dislike_count_row[0] if dislike_count_row else 0

        # Check if current user disliked this post
        async with db.execute(
            "SELECT id FROM reactions WHERE post_id = ? AND user_id = ?",
            (post_id, admin_user["id"])
        ) as cursor:
            user_dislike_row = await cursor.fetchone()

        is_disliked = user_dislike_row is not None

        # Get username for this post
        async with db.execute(
            "SELECT username FROM users WHERE id = ?",
            (p[1],)
        ) as cursor:
            username = await cursor.fetchone()

        username = username[0] if username else "Error"

        result_posts.append({
            "id": post_id,
            "username": username,
            "post_type": p[2],
            "content": p[3] if p[2] == "text" else None,
            "caption": p[4],
            "created_at": p[5],
            "is_deletable": p[1] == admin_user["id"],
            "dislike_count": dislike_count,
            "is_disliked": is_disliked,  # True if current user disliked
            "file": {
                "id": p[6],
                "filename": p[7],
                "size": p[8],
                "mime_type": p[9]
            } if p[2] == "file" else None
        })

    return {
        "posts": result_posts,
        "page": page,
        "per_page": limit
    }

def sanitize_filename(filename):
    # Remove path components and dangerous characters
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w\s\-\.]', '', filename)
    return filename[:255]  # Limit length
