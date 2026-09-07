#!/usr/bin/env bash
# Run on the Pi after first SSH login (from the tvgui git checkout).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIOSK_USER=lvuser

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3-pygame python3-pyscard python3-speechd sqlite3 espeak-ng \
  xserver-xorg xinit openbox x11-xserver-utils \
  pcscd libccid \
  pipewire pipewire-pulse wireplumber \
  libegl1 libgles2 \
  fonts-freefont-ttf

sudo mkdir -p /usr/local/share/tvgui /var/lib/tvgui /etc/X11 /etc/tvgui
sudo cp "$ROOT/py/"*.py "$ROOT/py/xsession.sh" /usr/local/share/tvgui/
sudo chmod 755 /usr/local/share/tvgui/tvgui.py /usr/local/share/tvgui/xsession.sh
sudo cp "$ROOT/deploy/tvgui-kiosk.service" /etc/systemd/system/tvgui-kiosk.service
sudo cp "$ROOT/deploy/pw-hdmi-hold.service" /etc/systemd/system/pw-hdmi-hold.service
sudo cp "$ROOT/deploy/blacklist-pn533.conf" /etc/modprobe.d/
sudo sed -i 's/^AudioOutputMethod .*/AudioOutputMethod "pipewire"/' /etc/speech-dispatcher/speechd.conf
sudo cp "$ROOT/deploy/50-pcscd.rules" /etc/polkit-1/rules.d/
printf "allowed_users=anybody\nneeds_root_rights=yes\n" | sudo tee /etc/X11/Xwrapper.config >/dev/null
if ! id -u "$KIOSK_USER" >/dev/null 2>&1; then
  sudo adduser --gecos "FRC lvuser" --disabled-password "$KIOSK_USER"
  echo "Set a password: sudo passwd $KIOSK_USER"
fi
for g in sudo video render input audio tty adm dialout plugdev netdev gpio spi i2c; do
  getent group "$g" >/dev/null && sudo usermod -aG "$g" "$KIOSK_USER" || true
done
echo "${KIOSK_USER} ALL=(ALL) NOPASSWD:ALL" | sudo tee "/etc/sudoers.d/${KIOSK_USER}" >/dev/null
sudo chmod 440 "/etc/sudoers.d/${KIOSK_USER}"
if [[ -f "$HOME/.ssh/authorized_keys" ]]; then
  sudo mkdir -p "/home/${KIOSK_USER}/.ssh"
  sudo cp "$HOME/.ssh/authorized_keys" "/home/${KIOSK_USER}/.ssh/"
  sudo chown -R "${KIOSK_USER}:${KIOSK_USER}" "/home/${KIOSK_USER}/.ssh"
  sudo chmod 700 "/home/${KIOSK_USER}/.ssh"
  sudo chmod 600 "/home/${KIOSK_USER}/.ssh/authorized_keys"
fi
sudo mkdir -p "/home/${KIOSK_USER}/.config/pipewire/pipewire.conf.d"
sudo cp "$ROOT/deploy/pipewire-hdmi.conf" "/home/${KIOSK_USER}/.config/pipewire/pipewire.conf.d/hdmi.conf"
sudo chown -R "${KIOSK_USER}:${KIOSK_USER}" "/home/${KIOSK_USER}/.config"
sudo chown -R "${KIOSK_USER}:${KIOSK_USER}" /var/lib/tvgui
sudo loginctl enable-linger "$KIOSK_USER"
sudo systemctl enable --now pcscd.socket seatd pw-hdmi-hold
sudo systemctl daemon-reload
sudo systemctl enable tvgui-kiosk pw-hdmi-hold

if [[ ! -f /etc/tvgui/kiosk.env ]]; then
  echo "Missing /etc/tvgui/kiosk.env — add TVGUI_TAG_SECRET=... (Vaultwarden item badge-kiosk NTAG)"
fi
echo "Then: sudo systemctl start tvgui-kiosk"
echo "Enroll: python3 /usr/local/share/tvgui/tvgui.py enroll --name NAME --username USER --role student"
echo "SQLite: sqlite3 /var/lib/tvgui/attendance.sqlite"
