#!/bin/bash

# Exit on error
set -e

echo "Setup Stalkineitor 2.0 Environment..."

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed."
    exit 1
fi

# Create Venv
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
else
    echo "Virtual environment already exists."
fi

# Activate and Install
echo "Installing dependencies..."
source venv/bin/activate
pip install -r requirements.txt

# Docker
echo "Starting Docker containers..."
if command -v docker-compose &> /dev/null; then
    docker-compose up -d
else
    echo "docker-compose not found. Trying 'docker compose'..."
    docker compose up -d
fi

# Wait for DB
echo "Waiting for Database to be ready..."
sleep 5

# Migrations
echo "Running migrations..."
python manage.py makemigrations core
python manage.py migrate

echo "Setup Complete!"
echo ""
echo "To run the project:"
echo "1. Activate venv: source venv/bin/activate"
echo "2. Start Celery worker (default queue): celery -A stalkineitor_project worker -l info -Q celery"
echo "3. Start Celery worker (ratings queue): celery -A stalkineitor_project worker -l info -Q ratings -c 2"
echo "4. Start Celery beat: celery -A stalkineitor_project beat -l info"
echo "5. Start Django: python manage.py runserver"
