# badge-kiosk

Full-screen Team Roboto shop kiosk: who’s-here list, ACR122U badge in/out, HDMI speech.

Python 3 + Debian Trixie packages only. No compile, no cross-build. Pygame on Xorg/Openbox (no Wayland).

## Raspberry Pi 3

Raspberry Pi OS Lite 64-bit (Trixie). SSH/kiosk user **`lvuser`** (FRC RoboRIO/SystemCore convention). HDMI via Xorg. Reader via **pcscd**. Speech: speech-dispatcher → PipeWire HDMI sink (`alsa-hdmi`, never suspends).

NTAG write-password comes from `TVGUI_TAG_SECRET` in `/etc/tvgui/kiosk.env` (not in git). systemd loads it via `EnvironmentFile`.

```bash
./scripts/setup-pi.sh
# /etc/tvgui/kiosk.env must contain: TVGUI_TAG_SECRET=...
sudo systemctl start tvgui-kiosk
```

Enroll (kiosk running; hold the tag):

```bash
python3 /usr/local/share/tvgui/tvgui.py enroll \
  --name "Darrin Thompson" --username dthompson --role mentor
```

Attendance DB:

```bash
sqlite3 /var/lib/tvgui/attendance.sqlite
.tables
SELECT * FROM punches;
```

Tag URL: `https://members.teamroboto.org/?name=…&pronounce=…&username=…&role=mentor|student|parent`

HDMI blanks after 10 minutes idle (`tvgui.py blank` / `unblank`). Live shot: `/run/tvgui/screen.png`. New and first-punch tags get a write password.

## Layout

| Path | Role |
|---|---|
| `py/tvgui.py` | Kiosk + enroll CLI |
| `py/nfc.py` | pcscd / NDEF / LED |
| `py/attendance.py` | SQLite toggle in/out |
| `py/xsession.sh` | openbox + kiosk |
| `deploy/tvgui-kiosk.service` | systemd |
