#!/bin/bash
set -e

echo "Starting services..."
echo "DISPLAY=$DISPLAY"

exec supervisord -c /etc/supervisor/supervisord.conf --pidfile /var/run/supervisord.pid
