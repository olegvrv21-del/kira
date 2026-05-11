#!/bin/bash
set -e
# Start browser daemon in background
python3 /opt/browser_daemon.py > /tmp/browser.log 2>&1 &
# Keep container alive
exec sleep infinity
