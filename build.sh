#!/usr/bin/env bash
# Render Build Script for Native Python Environment
set -o errexit

echo "===> Upgrading pip and installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "===> Ensuring Node.js is available for React build..."
if ! command -v npm &> /dev/null; then
    echo "Installing Node.js via nvm..."
    export NVM_DIR="$HOME/.nvm"
    if [ ! -d "$NVM_DIR" ]; then
        curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    fi
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    nvm install 20
    nvm use 20
fi

echo "===> Building React frontend..."
cd frontend
npm install
npm run build
cd ..

echo "===> Build completed successfully!"
