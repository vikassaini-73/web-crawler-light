#!/bin/bash
set -e

# Configuration
BIN_DIR="src/lightpanda_bin"
BIN_NAME="lightpanda"
BIN_PATH="$BIN_DIR/$BIN_NAME"
URL="https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux"

echo "--- Lightpanda Build Step ---"

# Create bin directory if it doesn't exist
mkdir -p "$BIN_DIR"

# Download binary
echo "Downloading Lightpanda binary (Linux x86_64) from GitHub..."
curl -L "$URL" -o "$BIN_PATH"

# Set permissions
echo "Setting executable permissions..."
chmod +x "$BIN_PATH"

# Verification
if [ -f "$BIN_PATH" ]; then
    SIZE=$(du -h "$BIN_PATH" | cut -f1)
    echo "✓ Success: Lightpanda binary bundled at $BIN_PATH ($SIZE)"
else
    echo "✗ Error: Failed to download Lightpanda binary"
    exit 1
fi

echo "--- Build Step Complete ---"
