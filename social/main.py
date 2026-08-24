from fastapi import FastAPI, APIRouter, File, UploadFile, Depends, HTTPException, Form, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
import os
import uuid
from datetime import datetime
from identity import get_identity, require_role, Identity
from database import get_db, ensure_profile
from models import Post, File as FileModel, Reaction
from config import get_settings
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import re
import logging

app = FastAPI(title="Social API")

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
        "http://localhost:3000",  # React development server
        "http://127.0.0.1:3000",  # Alternative localhost
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = get_settings()
STORAGE_PATH = settings.storage_path
MAX_POST_FILE_SIZE = 100 * 1024 * 1024  # 100MB limit for posts

@app.on_event("startup")
async def startup():
    # Schema is managed by Alembic migrations, not created here.
    os.makedirs(STORAGE_PATH, exist_ok=True)

# Real API routes live under /api -- lets Traefik route by a single
# PathPrefix(`/api`) to this service and everything else to the frontend,
# without needing a Traefik rule per endpoint. /health and / stay
# unprefixed since Kubernetes probes and basic liveness checks hit them
# directly.
api = APIRouter(prefix="/api")

@api.get("/me")
async def me(identity: Identity = Depends(get_identity)):
    return {"username": identity.username, "role": identity.role}

@api.post("/posts/text")
@limiter.limit("30/minute")
async def create_text_post(
    request: Request,
    content: str = Form(...),
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Create a text-only post"""
    await ensure_profile(identity.user_id, db)

    if len(content) > 1000:
        raise HTTPException(status_code=400, detail="Text content exceeds 1000 character limit")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    post = Post(user_id=identity.user_id, post_type="text", content=content)
    db.add(post)
    await db.commit()
    await db.refresh(post)

    return {
        "id": post.id,
        "post_type": "text",
        "content": content,
        "message": "Text post created successfully"
    }

@api.post("/posts/file")
@limiter.limit("30/minute")
async def create_file_post(
    request: Request,
    file: UploadFile = File(...),
    caption: str = Form(None),
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Upload a file and create a post"""
    await ensure_profile(identity.user_id, db)

    if caption and len(caption) > 1000:
        raise HTTPException(status_code=400, detail="Caption exceeds 1000 character limit")

    original_filename = sanitize_filename(file.filename)
    file_ext = os.path.splitext(original_filename)[1]
    stored_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(STORAGE_PATH, stored_filename)

    if file_ext in BLOCKED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} is not allowed for security reasons"
        )

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

    new_file = FileModel(
        user_id=identity.user_id,
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_size=file_size,
        mime_type=file.content_type,
        file_path=file_path,
        is_public=True,
    )
    db.add(new_file)
    await db.flush()  # get new_file.id without committing yet

    post = Post(user_id=identity.user_id, post_type="file", file_id=new_file.id, caption=caption)
    db.add(post)
    await db.commit()
    await db.refresh(post)

    return {
        "id": post.id,
        "post_type": "file",
        "file_id": new_file.id,
        "filename": file.filename,
        "size": file_size,
        "caption": caption,
        "message": "File post created successfully"
    }

@api.get("/feed")
async def get_feed(
    page: int = 1,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Get chronological feed of posts (newest first, 20 per page)"""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be >= 1")

    limit = 20
    offset = (page - 1) * limit

    result = await db.execute(
        select(Post, FileModel)
        .outerjoin(FileModel, Post.file_id == FileModel.id)
        .where((Post.is_reply == False) | (Post.is_reply.is_(None)))
        .order_by(Post.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()

    result_posts = []
    for post, file in rows:
        dislike_count = (await db.execute(
            select(func.count()).select_from(Reaction).where(Reaction.post_id == post.id)
        )).scalar_one()

        reply_count = (await db.execute(
            select(func.count()).select_from(Post)
            .where(Post.parent_post_id == post.id, Post.is_reply == True)
        )).scalar_one()

        is_disliked = (await db.execute(
            select(Reaction.id).where(Reaction.post_id == post.id, Reaction.user_id == identity.user_id)
        )).scalar_one_or_none() is not None

        result_posts.append({
            "id": post.id,
            "post_type": post.post_type,
            "content": post.content if post.post_type == "text" else None,
            "caption": post.caption,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "is_deletable": post.user_id == identity.user_id,
            "dislike_count": dislike_count,
            "reply_count": reply_count,
            "is_disliked": is_disliked,
            "file": {
                "id": file.id,
                "filename": file.original_filename,
                "size": file.file_size,
                "mime_type": file.mime_type,
            } if post.post_type == "file" and file else None
        })

    return {
        "posts": result_posts,
        "page": page,
        "per_page": limit
    }

@api.delete("/posts/{post_id}")
async def delete_post(
    post_id: int,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Delete a post (only if it belongs to current user)"""
    post = await db.get(Post, post_id)

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.user_id != identity.user_id:
        raise HTTPException(status_code=403, detail="You can only delete your own posts")

    if post.post_type == "file" and post.file_id:
        file_record = await db.get(FileModel, post.file_id)
        if file_record and os.path.exists(file_record.file_path):
            os.remove(file_record.file_path)
        if file_record:
            await db.delete(file_record)

    await db.delete(post)
    await db.commit()

    return {"message": "Post deleted successfully"}

@api.get("/posts/{post_id}/download")
async def download_post_file(
    post_id: int,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Download file from a post"""
    post = await db.get(Post, post_id)
    if not post or not post.file_id:
        raise HTTPException(status_code=404, detail="File not found")

    file_record = await db.get(FileModel, post.file_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.exists(file_record.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(file_record.file_path, filename=file_record.original_filename)

@api.post("/posts/{post_id}/dislike")
async def toggle_dislike(
    post_id: int,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Toggle dislike on a post"""
    post = await db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = (await db.execute(
        select(Reaction).where(Reaction.user_id == identity.user_id, Reaction.post_id == post_id)
    )).scalar_one_or_none()

    if existing:
        await db.delete(existing)
        await db.commit()
        return {"message": "Dislike removed", "action": "removed", "is_disliked": False}
    else:
        db.add(Reaction(post_id=post_id, user_id=identity.user_id, reaction_type="dislike"))
        await db.commit()
        return {"message": "Dislike added", "action": "added", "is_disliked": True}

# Reply Endpoints

@api.post("/posts/{post_id}/reply/text")
@limiter.limit("30/minute")
async def create_text_reply(
    request: Request,
    post_id: int,
    content: str = Form(...),
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Create a text reply to a post"""
    await ensure_profile(identity.user_id, db)

    parent_post = await db.get(Post, post_id)
    if not parent_post:
        raise HTTPException(status_code=404, detail="Parent post not found")

    if parent_post.is_reply:
        raise HTTPException(status_code=400, detail="Cannot reply to a reply")

    if len(content) > 1000:
        raise HTTPException(status_code=400, detail="Text content exceeds 1000 character limit")

    if not content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    reply = Post(
        user_id=identity.user_id, post_type="text", content=content,
        is_reply=True, parent_post_id=post_id,
    )
    db.add(reply)
    await db.commit()
    await db.refresh(reply)

    return {
        "id": reply.id,
        "post_type": "text",
        "content": content,
        "parent_post_id": post_id,
        "message": "Reply created successfully"
    }

@api.post("/posts/{post_id}/reply/file")
@limiter.limit("30/minute")
async def create_file_reply(
    request: Request,
    post_id: int,
    file: UploadFile = File(...),
    caption: str = Form(None),
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Create a file reply to a post"""
    await ensure_profile(identity.user_id, db)

    parent_post = await db.get(Post, post_id)
    if not parent_post:
        raise HTTPException(status_code=404, detail="Parent post not found")

    if parent_post.is_reply:
        raise HTTPException(status_code=400, detail="Cannot reply to a reply")

    if caption and len(caption) > 1000:
        raise HTTPException(status_code=400, detail="Caption exceeds 1000 character limit")

    original_filename = sanitize_filename(file.filename)
    file_ext = os.path.splitext(original_filename)[1]
    stored_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = os.path.join(STORAGE_PATH, stored_filename)

    if file_ext in BLOCKED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} is not allowed for security reasons"
        )

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

    new_file = FileModel(
        user_id=identity.user_id,
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_size=file_size,
        mime_type=file.content_type,
        file_path=file_path,
        is_public=True,
    )
    db.add(new_file)
    await db.flush()

    reply = Post(
        user_id=identity.user_id, post_type="file", file_id=new_file.id, caption=caption,
        is_reply=True, parent_post_id=post_id,
    )
    db.add(reply)
    await db.commit()
    await db.refresh(reply)

    return {
        "id": reply.id,
        "post_type": "file",
        "file_id": new_file.id,
        "filename": file.filename,
        "size": file_size,
        "caption": caption,
        "parent_post_id": post_id,
        "message": "Reply created successfully"
    }

@api.get("/posts/{post_id}/replies")
async def get_replies(
    post_id: int,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Get all replies for a specific post (anonymous - no usernames shown)"""
    parent_post = await db.get(Post, post_id)
    if not parent_post:
        raise HTTPException(status_code=404, detail="Post not found")

    if parent_post.is_reply:
        raise HTTPException(status_code=400, detail="Cannot get replies of a reply")

    result = await db.execute(
        select(Post, FileModel)
        .outerjoin(FileModel, Post.file_id == FileModel.id)
        .where(Post.parent_post_id == post_id, Post.is_reply == True)
        .order_by(Post.created_at.asc())
    )
    rows = result.all()

    result_replies = []
    for reply, file in rows:
        dislike_count = (await db.execute(
            select(func.count()).select_from(Reaction).where(Reaction.post_id == reply.id)
        )).scalar_one()

        is_disliked = (await db.execute(
            select(Reaction.id).where(Reaction.post_id == reply.id, Reaction.user_id == identity.user_id)
        )).scalar_one_or_none() is not None

        result_replies.append({
            "id": reply.id,
            "post_type": reply.post_type,
            "content": reply.content if reply.post_type == "text" else None,
            "caption": reply.caption,
            "created_at": reply.created_at.isoformat() if reply.created_at else None,
            "is_deletable": reply.user_id == identity.user_id,
            "dislike_count": dislike_count,
            "is_disliked": is_disliked,
            "file": {
                "id": file.id,
                "filename": file.original_filename,
                "size": file.file_size,
                "mime_type": file.mime_type,
            } if reply.post_type == "file" and file else None
        })

    return {
        "replies": result_replies,
        "count": len(result_replies)
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/")
async def root():
    return {"message": "Social API is running"}

# ===== ADMIN ENDPOINTS =====
# User/ban management lives entirely in auth-service now (GET /admin/users,
# POST /admin/users/{id}/ban, POST /admin/users/{id}/unban). Only
# content-moderation endpoints that need this app's own posts/reactions
# tables stay here, gated on the super role from the identity headers.

@api.delete("/admin/posts/{post_id}")
async def admin_delete_post(
    post_id: int,
    identity: Identity = Depends(require_role("super")),
    db: AsyncSession = Depends(get_db)
):
    """Delete any post (admin only)"""
    post = await db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.post_type == "file" and post.file_id:
        file_record = await db.get(FileModel, post.file_id)
        if file_record and os.path.exists(file_record.file_path):
            os.remove(file_record.file_path)
        if file_record:
            await db.delete(file_record)

    await db.delete(post)
    await db.commit()

    return {"message": "Post deleted successfully"}

@api.get("/admin/feed")
async def get_admin_feed(
    page: int = 1,
    identity: Identity = Depends(require_role("super")),
    db: AsyncSession = Depends(get_db)
):
    """Get chronological feed of posts (newest first, 20 per page), with user_id attached"""
    if page < 1:
        raise HTTPException(status_code=400, detail="Page must be >= 1")

    limit = 20
    offset = (page - 1) * limit

    result = await db.execute(
        select(Post, FileModel)
        .outerjoin(FileModel, Post.file_id == FileModel.id)
        .where((Post.is_reply == False) | (Post.is_reply.is_(None)))
        .order_by(Post.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()

    result_posts = []
    for post, file in rows:
        dislike_count = (await db.execute(
            select(func.count()).select_from(Reaction).where(Reaction.post_id == post.id)
        )).scalar_one()

        reply_count = (await db.execute(
            select(func.count()).select_from(Post)
            .where(Post.parent_post_id == post.id, Post.is_reply == True)
        )).scalar_one()

        is_disliked = (await db.execute(
            select(Reaction.id).where(Reaction.post_id == post.id, Reaction.user_id == identity.user_id)
        )).scalar_one_or_none() is not None

        result_posts.append({
            "id": post.id,
            "user_id": post.user_id,
            "post_type": post.post_type,
            "content": post.content if post.post_type == "text" else None,
            "caption": post.caption,
            "created_at": post.created_at.isoformat() if post.created_at else None,
            "is_deletable": post.user_id == identity.user_id,
            "dislike_count": dislike_count,
            "reply_count": reply_count,
            "is_disliked": is_disliked,
            "file": {
                "id": file.id,
                "filename": file.original_filename,
                "size": file.file_size,
                "mime_type": file.mime_type,
            } if post.post_type == "file" and file else None
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

app.include_router(api)
