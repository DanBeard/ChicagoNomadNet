#!/bin/bash
set -e

# Create required directories
mkdir -p /root/.nomadnetwork/storage/files/tmp

# Start ZIM host in background
python /app/zim_host.py &
ZIM_PID=$!

# Give zim_host time to load archives
sleep 5

# Start NomadNet (foreground)
exec nomadnet
