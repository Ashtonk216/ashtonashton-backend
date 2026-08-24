from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import os
import uuid
from datetime import datetime
from identity import get_identity, Identity
from database import get_db, ensure_profile
from models import File as FileModel, Profile
from config import get_settings
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
        "https://drive.ashtonashton.net",
        "http://localhost:3000",  # React development server
        "http://127.0.0.1:3000",  # Alternative localhost
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings = get_settings()
STORAGE_PATH = settings.storage_path
MAX_FILE_SIZE = settings.max_file_size

@app.on_event("startup")
async def startup():
    # Schema is managed by Alembic migrations, not created here.
    os.makedirs(STORAGE_PATH, exist_ok=True)

@app.get("/me")
async def me(identity: Identity = Depends(get_identity)):
    return {"username": identity.username, "role": identity.role}

@app.get("/files")
async def list_files(
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """List all files for the current user"""
    result = await db.execute(
        select(FileModel)
        .where(FileModel.user_id == identity.user_id)
        .order_by(FileModel.upload_date.desc())
    )
    files = result.scalars().all()

    return {
        "files": [
            {
                "id": f.id,
                "filename": f.original_filename,
                "size": f.file_size,
                "mime_type": f.mime_type,
                "upload_date": f.upload_date.isoformat() if f.upload_date else None,
            }
            for f in files
        ]
    }

@app.post("/upload")
@limiter.limit("50/minute")
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Upload a file"""
    await ensure_profile(identity.user_id, db)

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

    profile = await db.get(Profile, identity.user_id)
    remaining_capacity = profile.capacity - profile.current_usage

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

    new_file = FileModel(
        user_id=identity.user_id,
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_size=file_size,
        mime_type=file.content_type,
        file_path=file_path,
    )
    db.add(new_file)
    profile.current_usage += file_size
    await db.commit()
    await db.refresh(new_file)

    return {
        "id": new_file.id,
        "filename": file.filename,
        "size": file_size,
        "message": "Upload successful"
    }

@app.get("/download/{file_id}")
@limiter.limit("50/minute")
async def download_file(
    request: Request,
    file_id: int,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Download a file"""
    result = await db.execute(
        select(FileModel).where(FileModel.id == file_id, FileModel.user_id == identity.user_id)
    )
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    if not os.path.exists(file_record.file_path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(file_record.file_path, filename=file_record.original_filename)

@app.delete("/files/{file_id}")
async def delete_file(
    file_id: int,
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Delete a file"""
    result = await db.execute(
        select(FileModel).where(FileModel.id == file_id, FileModel.user_id == identity.user_id)
    )
    file_record = result.scalar_one_or_none()

    if not file_record:
        raise HTTPException(status_code=404, detail="File not found")

    profile = await db.get(Profile, identity.user_id)
    profile.current_usage = max(profile.current_usage - file_record.file_size, 0)

    # Delete from disk
    if os.path.exists(file_record.file_path):
        os.remove(file_record.file_path)

    await db.delete(file_record)
    await db.commit()

    return {"message": "File deleted"}

@app.get("/usage")
async def get_usage(
    identity: Identity = Depends(get_identity),
    db: AsyncSession = Depends(get_db)
):
    """Return current usage and capacity for the logged-in user"""
    await ensure_profile(identity.user_id, db)

    profile = await db.get(Profile, identity.user_id)

    return {"current_usage": profile.current_usage, "capacity": profile.capacity}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.get("/")
async def root():
    return {"message": "Drive API is running"}

def sanitize_filename(filename):
    # Remove path components and dangerous characters
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w\s\-\.]', '', filename)
    return filename[:255]  # Limit length
