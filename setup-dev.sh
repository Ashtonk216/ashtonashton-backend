#!/bin/bash

# Backend Development Environment Setup Script
# Run this script to set up your local development environment

set -e  # Exit on error

echo "========================================"
echo "Setting up Backend Development Environment"
echo "========================================"

# Check Python version
echo ""
echo "Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed. Please install Python 3.8 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d" " -f2)
echo "Found Python $PYTHON_VERSION"

# Create virtual environment
echo ""
echo "Creating virtual environment..."
if [ -d "venv" ]; then
    echo "Virtual environment already exists. Skipping creation."
else
    python3 -m venv venv
    echo "Virtual environment created."
fi

# Activate virtual environment
echo ""
echo "Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo ""
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies
echo ""
echo "Installing dependencies from requirements.txt..."
pip install -r requirements.txt

# Check if .env file exists
echo ""
if [ -f ".env" ]; then
    echo ".env file exists."
else
    echo "WARNING: .env file not found!"
    echo "Creating .env.example as a template..."
    cat > .env.example << 'EOF'
# Database
DATABASE_PATH=/path/to/database/drive.db

# Storage
STORAGE_PATH=/path/to/storage
MAX_FILE_SIZE=524288000

# Security
SECRET_KEY=your-secret-key-here

# Server (for development only)
HOST=127.0.0.1
PORT=8001
EOF
    echo ""
    echo "Please create a .env file based on .env.example"
    echo "You need to set:"
    echo "  - DATABASE_PATH: Where SQLite database will be stored"
    echo "  - STORAGE_PATH: Where uploaded files will be stored"
    echo "  - MAX_FILE_SIZE: Maximum file size in bytes (default: 500MB)"
    echo "  - SECRET_KEY: Random secret key for JWT tokens"
fi

# Create necessary directories
echo ""
echo "Creating necessary directories..."
if [ -f ".env" ]; then
    # Try to create directories from .env if they exist
    if grep -q "STORAGE_PATH" .env; then
        STORAGE_DIR=$(grep "STORAGE_PATH" .env | cut -d '=' -f2)
        if [ ! -z "$STORAGE_DIR" ] && [ "$STORAGE_DIR" != "/path/to/storage" ]; then
            mkdir -p "$STORAGE_DIR"
            echo "Created storage directory: $STORAGE_DIR"
        fi
    fi

    if grep -q "DATABASE_PATH" .env; then
        DB_PATH=$(grep "DATABASE_PATH" .env | cut -d '=' -f2)
        if [ ! -z "$DB_PATH" ] && [ "$DB_PATH" != "/path/to/database/drive.db" ]; then
            DB_DIR=$(dirname "$DB_PATH")
            mkdir -p "$DB_DIR"
            echo "Created database directory: $DB_DIR"
        fi
    fi
fi

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "To start the development server:"
echo "  1. Activate virtual environment: source venv/bin/activate"
echo "  2. Make sure your .env file is configured"
echo "  3. Run: uvicorn main:app --reload --host 127.0.0.1 --port 8001"
echo ""
echo "The API will be available at: http://127.0.0.1:8001"
echo "API docs will be at: http://127.0.0.1:8001/docs"
echo ""
