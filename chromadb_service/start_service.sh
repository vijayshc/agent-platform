#!/bin/bash

# ChromaDB Service Startup Script

# Set environment variables
export CHROMADB_SERVICE_HOST=${CHROMADB_SERVICE_HOST:-"0.0.0.0"}
export CHROMADB_SERVICE_PORT=${CHROMADB_SERVICE_PORT:-8001}
export CHROMADB_SERVICE_DEBUG=${CHROMADB_SERVICE_DEBUG:-"False"}
export CHROMA_PERSIST_DIRECTORY=${CHROMA_PERSIST_DIRECTORY:-"./chroma_data"}

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change to the service directory
cd "$SCRIPT_DIR"

# Create logs directory if it doesn't exist
mkdir -p logs

# Install dependencies if requirements.txt is newer than last install
if [ requirements.txt -nt .requirements_installed ] || [ ! -f .requirements_installed ]; then
    echo "Installing/updating Python dependencies..."
    /home/vijay/anaconda3/bin/python -m pip install -r requirements.txt
    touch .requirements_installed
fi

# Start the service
echo "Starting ChromaDB Service on $CHROMADB_SERVICE_HOST:$CHROMADB_SERVICE_PORT..."
echo "Data directory: $CHROMA_PERSIST_DIRECTORY"
echo "Debug mode: $CHROMADB_SERVICE_DEBUG"

# Run the service with logging
/home/vijay/anaconda3/bin/python app.py 2>&1 | tee logs/chromadb_service.log
