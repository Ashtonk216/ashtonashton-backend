# Drive Backend API

FastAPI backend for the Ashton Ashton social network and drive applications.

## Features

- **User Authentication**: JWT-based authentication with bcrypt password hashing
- **File Storage**: Upload and manage files with storage quotas
- **Social Features**: Posts, reactions (dislikes), feed system
- **Admin Panel**: User and content moderation
- **Rate Limiting**: Protection against brute force attacks
- **Security**: HTTPS, CORS, SQL injection prevention

## Tech Stack

- **Framework**: FastAPI
- **Database**: SQLite with aiosqlite (async)
- **Authentication**: JWT tokens (7-day expiration)
- **Server**: Uvicorn (ASGI server)

## Prerequisites

- Python 3.8 or higher
- pip (Python package manager)
- 40GB+ storage for user data (configurable)

## Quick Start

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd drive-backend
```

### 2. Run Setup Script

```bash
./setup-dev.sh
```

This will:
- Create a Python virtual environment
- Install all dependencies
- Create directory structure
- Generate .env.example template

### 3. Configure Environment Variables

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
```

Edit `.env` and set these required variables:

```env
# Database
DATABASE_PATH=/mnt/volume_nyc1_02/drive-storage/database/drive.db

# Storage
STORAGE_PATH=/mnt/volume_nyc1_02/drive-storage/files
MAX_FILE_SIZE=524288000

# Security
SECRET_KEY=your-super-secret-key-change-this

# Server
HOST=127.0.0.1
PORT=8001
```

**Important**: Generate a secure SECRET_KEY:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 4. Start Development Server

```bash
source venv/bin/activate
uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

The API will be available at:
- API: http://127.0.0.1:8001
- Interactive docs: http://127.0.0.1:8001/docs
- Alternative docs: http://127.0.0.1:8001/redoc

## Manual Setup (Alternative)

If you prefer manual setup:

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create .env file
nano .env

# Run server
uvicorn main:app --reload
```

## Project Structure

```
drive-backend/
├── main.py           # Main application and API endpoints
├── auth.py           # Authentication and JWT handling
├── database.py       # Database initialization and connection
├── requirements.txt  # Python dependencies
├── .env             # Environment variables (create this)
├── .gitignore       # Git ignore rules
├── setup-dev.sh     # Development setup script
└── README.md        # This file
```

## API Endpoints

### Authentication
- `POST /register` - Register new user
- `POST /login` - Login and get JWT token
- `POST /refresh-token` - Refresh JWT token
- `POST /change-password` - Change user password

### Files
- `POST /upload` - Upload file
- `GET /files` - List user's files
- `GET /files/{file_id}` - Download file
- `DELETE /files/{file_id}` - Delete file

### Social
- `GET /feed` - Get paginated feed of posts
- `POST /posts/text` - Create text post
- `POST /posts/file` - Create file post
- `DELETE /posts/{post_id}` - Delete own post
- `POST /posts/{post_id}/dislike` - Toggle dislike on post

### Admin (requires admin privileges)
- `GET /admin/users` - List all users
- `POST /admin/users/{user_id}/ban` - Ban user
- `POST /admin/users/{user_id}/unban` - Unban user
- `DELETE /admin/posts/{post_id}` - Delete any post

### Health
- `GET /health` - Health check endpoint

## Security Features

1. **JWT Authentication**: 7-day token expiration with refresh capability
2. **Password Hashing**: bcrypt with salt
3. **Rate Limiting**: 2 login/register attempts per minute per IP
4. **CORS**: Configured for specific domains
5. **SQL Injection Prevention**: Parameterized queries
6. **File Type Restrictions**: Blocks dangerous file extensions

## Development

### Database Schema

**Users Table**:
- id, username, password_hash
- capacity, current_usage
- is_admin, is_banned
- created_at

**Files Table**:
- id, user_id, original_filename, stored_filename
- file_size, mime_type, file_path
- is_public, upload_date

**Posts Table**:
- id, user_id, post_type (text/file)
- content, file_id, caption
- created_at

**Reactions Table**:
- id, post_id, user_id, reaction_type (dislike)
- created_at

### Running Tests

```bash
# Install dev dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest
```

### Database Migrations

The database auto-migrates on startup. New columns are added via ALTER TABLE statements in `database.py`.

## Production Deployment

### Using Systemd

1. Create service file:

```bash
sudo nano /etc/systemd/system/drive-backend.service
```

```ini
[Unit]
Description=Drive Backend FastAPI Service
After=network.target

[Service]
User=your-user
WorkingDirectory=/path/to/drive-backend
Environment="PATH=/path/to/drive-backend/venv/bin"
ExecStart=/path/to/drive-backend/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001
Restart=always

[Install]
WantedBy=multi-user.target
```

2. Enable and start:

```bash
sudo systemctl enable drive-backend
sudo systemctl start drive-backend
```

### Using Nginx (Reverse Proxy)

```nginx
server {
    listen 443 ssl;
    server_name api.yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## Environment Variables Reference

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_PATH` | SQLite database file path | `/data/drive.db` |
| `STORAGE_PATH` | File storage directory | `/data/files` |
| `MAX_FILE_SIZE` | Max file size in bytes | `524288000` (500MB) |
| `SECRET_KEY` | JWT secret key | Generate with `secrets.token_urlsafe(32)` |
| `HOST` | Server host (dev only) | `127.0.0.1` |
| `PORT` | Server port (dev only) | `8001` |

## Troubleshooting

### "Missing required environment variables"
- Make sure `.env` file exists and all required variables are set

### "Permission denied" on storage directory
- Ensure the user running the app has write permissions to `STORAGE_PATH`

### "Database is locked"
- SQLite doesn't handle high concurrency well. Consider PostgreSQL for production with high traffic

### Can't connect from frontend
- Check CORS settings in `main.py`
- Verify the frontend URL is in the `allow_origins` list

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

Private project - All rights reserved

## Support

For issues or questions, contact the repository owner.
