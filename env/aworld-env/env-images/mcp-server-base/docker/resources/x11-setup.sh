#!/bin/bash

# Wait for Xvfb to be fully ready
echo "Checking X display availability..."
for i in {1..10}; do
    if xdpyinfo >/dev/null 2>&1; then
        echo "X display is available (attempt $i)"
        break
    fi
    
    if [ $i -eq 10 ]; then
        echo "ERROR: X display :0 is not available after 10 seconds"
        echo "Xvfb status: $(ps aux | grep Xvfb || echo 'Xvfb not running')"
        break
    fi
    
    echo "X display not ready, waiting... (attempt $i/10)"
    sleep 1
done

# Set background to white for clean appearance
xsetroot -solid "#ffffff"

# Setup resolution and DPI
xrandr --output screen --mode ${SCREEN_WIDTH}x${SCREEN_HEIGHT}

# Set proper DPI settings for the display
echo "Xft.dpi: ${SCREEN_DPI}" | xrdb -merge
echo "Xft.antialias: 1" | xrdb -merge
echo "Xft.hinting: 1" | xrdb -merge
echo "Xft.hintstyle: hintfull" | xrdb -merge
echo "Xft.rgba: rgb" | xrdb -merge

# Disable any screen savers or power management
xset s off
xset s noblank

echo "xrandr output: $(xrandr --query)" # Add logging
echo "xrdb output: $(xrdb -query)"

echo "X11 environment configured for optimal display"