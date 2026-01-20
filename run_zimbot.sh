#!/bin/bash
#
# ZimBot Launch Script
# Starts zim_host and zimbot with a single command
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== ZimBot Launcher ===${NC}"

# Configuration (override with environment variables)
: "${ZIM_PATH:=/zim/}"
: "${ZIM_AUTHKEY:=zimbot_auth_key}"
: "${ZIMBOT_SQLITE_PATH:=./zimbot.db}"
: "${ZIMBOT_MODEL_PATH:=/media/v/v_storage1/v/llm/zimbot/}"
: "${ZIMBOT_LAZY_EMBEDDING:=1}"

export ZIM_PATH ZIM_AUTHKEY ZIMBOT_SQLITE_PATH ZIMBOT_MODEL_PATH ZIMBOT_LAZY_EMBEDDING

# Activate venv if it exists
if [ -f "venv/bin/activate" ]; then
    echo -e "${YELLOW}Activating local venv...${NC}"
    source venv/bin/activate
elif [ -f "/home/user/Projects/ChicagoNomadNet/venv/bin/activate" ]; then
    echo -e "${YELLOW}Activating system venv...${NC}"
    source /home/user/Projects/ChicagoNomadNet/venv/bin/activate
else
    echo -e "${YELLOW}No venv found, using system Python${NC}"
fi

# Check for required files
if [ ! -d "$ZIM_PATH" ]; then
    echo -e "${RED}Warning: ZIM_PATH ($ZIM_PATH) does not exist${NC}"
    echo "Set ZIM_PATH environment variable to your ZIM files location"
fi

if [ ! -d "$ZIMBOT_MODEL_PATH" ]; then
    echo -e "${RED}Warning: ZIMBOT_MODEL_PATH ($ZIMBOT_MODEL_PATH) does not exist${NC}"
    echo "Set ZIMBOT_MODEL_PATH to directory containing .gguf model files"
fi

# Check if port 6000 is already in use (zim_host already running)
ZIM_ALREADY_RUNNING=false
if ss -tuln 2>/dev/null | grep -q ':6000 ' || netstat -tuln 2>/dev/null | grep -q ':6000 '; then
    echo -e "${YELLOW}Port 6000 already in use - assuming zim_host is already running${NC}"
    ZIM_ALREADY_RUNNING=true
fi

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    if [ ! -z "$ZIM_PID" ] && kill -0 $ZIM_PID 2>/dev/null; then
        echo "Stopping zim_host (PID: $ZIM_PID)..."
        kill $ZIM_PID 2>/dev/null || true
        wait $ZIM_PID 2>/dev/null || true
    fi
    echo -e "${GREEN}Cleanup complete${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# Start zim_host in background (if not already running)
if [ "$ZIM_ALREADY_RUNNING" = false ]; then
    echo -e "${GREEN}Starting zim_host...${NC}"
    python "$SCRIPT_DIR/zim_host.py" &
    ZIM_PID=$!
    echo "zim_host started (PID: $ZIM_PID)"

    # Wait for zim_host to initialize
    echo "Waiting for zim_host to load archives..."
    sleep 3

    # Check if zim_host is still running
    if ! kill -0 $ZIM_PID 2>/dev/null; then
        echo -e "${RED}Error: zim_host failed to start${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}Using existing zim_host instance${NC}"
    ZIM_PID=""
fi

# Start ZimBot
echo -e "${GREEN}Starting ZimBot...${NC}"
echo "  ZIM_PATH: $ZIM_PATH"
echo "  ZIMBOT_SQLITE_PATH: $ZIMBOT_SQLITE_PATH"
echo "  ZIMBOT_MODEL_PATH: $ZIMBOT_MODEL_PATH"
echo "  ZIMBOT_LAZY_EMBEDDING: $ZIMBOT_LAZY_EMBEDDING"
echo ""

python -m projects.zimbot.zimbot

# Cleanup will be called by trap
