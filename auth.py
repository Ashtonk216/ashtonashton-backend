import bcrypt
import jwt
from datetime import datetime, timedelta
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import aiosqlite
from dotenv import load_dotenv
from database import get_db
import os

load_dotenv()
SECRET_KEY = os.getenv('SECRET_KEY')
ALGORITHM = "HS256"

security = HTTPBearer()

def hash_password(password: str) -> str:
    """Hash a password"""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash"""
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(user_id: int, username: str) -> str:
    """Create a JWT token"""
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    """Decode and verify a JWT token"""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: aiosqlite.Connection = Depends(get_db)
):
    """Get the current authenticated user"""
    token_data = decode_token(credentials.credentials)

    async with db.execute(
        "SELECT id, username, is_admin, is_banned FROM users WHERE id = ?",
        (token_data["user_id"],)
    ) as cursor:
        user = await cursor.fetchone()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Check if user is banned
    is_banned = user[3] if len(user) > 3 else 0
    if is_banned:
        raise HTTPException(status_code=403, detail="Account banned")

    is_admin = user[2] if len(user) > 2 else 0
    return {"id": user[0], "username": user[1], "is_admin": bool(is_admin)}

async def get_admin_user(
    current_user: dict = Depends(get_current_user)
):
    """Verify user is an admin"""
    if not current_user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
