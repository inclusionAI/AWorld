#!/bin/bash

# Set background to white for clean appearance
xsetroot -solid "#ffffff"

echo "Configuring display: ${SCREEN_WIDTH}x${SCREEN_HEIGHT} @ ${SCREEN_DPI} DPI"

# Force X11 to use the exact screen dimensions without any offsets
xrandr --output default --mode ${SCREEN_WIDTH}x${SCREEN_HEIGHT} --pos 0x0
echo "xrandr output: $(xrandr --query)" # Add logging

# Set proper DPI settings for the display
echo "Xft.dpi: ${SCREEN_DPI}" | xrdb -merge
echo "Xft.antialias: 1" | xrdb -merge
echo "Xft.hinting: 1" | xrdb -merge
echo "Xft.hintstyle: hintfull" | xrdb -merge
echo "Xft.rgba: rgb" | xrdb -merge

# Disable any screen savers or power management
xset s off
xset -dpms
xset s noblank

# Ensure consistent scaling
xrandr --dpi ${SCREEN_DPI}

echo "X11 environment configured for optimal display"