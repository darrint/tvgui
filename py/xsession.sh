#!/bin/sh
export SDL_AUDIODRIVER=dummy
openbox &
sleep 1
xset s off 2>/dev/null || true
xset s noblank 2>/dev/null || true
xset +dpms 2>/dev/null || true
xset dpms 0 0 0 2>/dev/null || true
exec /usr/bin/python3 /usr/local/share/tvgui/tvgui.py
