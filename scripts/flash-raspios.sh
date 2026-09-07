#!/usr/bin/env bash
# Write Raspberry Pi OS Lite 64-bit (Trixie) to the laptop SD slot and
# enable headless SSH. Overwrites the card. Requires sudo for dd only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="$ROOT/.cache"
IMAGE_XZ="$CACHE/2026-06-18-raspios-trixie-arm64-lite.img.xz"
IMAGE_SHA="$CACHE/2026-06-18-raspios-trixie-arm64-lite.img.xz.sha256"
CREDS="$CACHE/pi-credentials.txt"
DEVICE="${1:-/dev/mmcblk0}"
USER_NAME="${TVGUI_PI_USER:-lvuser}"
HOST_NAME="${TVGUI_PI_HOST:-tvgui}"

if [[ "$DEVICE" != /dev/mmcblk* ]]; then
  echo "Refusing to write $DEVICE (only /dev/mmcblk* SD slots are allowed)." >&2
  exit 1
fi
if [[ ! -b "$DEVICE" ]]; then
  echo "No block device $DEVICE. Insert the microSD (via adapter) and retry." >&2
  exit 1
fi
if [[ ! -f "$IMAGE_XZ" ]]; then
  echo "Missing $IMAGE_XZ — download the Trixie lite image into .cache first." >&2
  exit 1
fi

model="$(cat /sys/block/$(basename "$DEVICE")/device/name 2>/dev/null || echo unknown)"
size="$(lsblk -bdo SIZE "$DEVICE")"
echo "About to OVERWRITE:"
echo "  device: $DEVICE"
echo "  model:  $model"
echo "  size:   $size bytes"
echo "  image:  $IMAGE_XZ"
echo "  user:   $USER_NAME@$HOST_NAME"
if [[ "${TVGUI_FLASH:-}" != "1" ]]; then
  echo
  echo "Re-run with TVGUI_FLASH=1 $0 $DEVICE to proceed." >&2
  exit 1
fi

if [[ -f "$IMAGE_SHA" ]]; then
  (cd "$CACHE" && sha256sum -c "$(basename "$IMAGE_SHA")")
fi

if [[ ! -f "$CREDS" ]]; then
  pass="$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)"
  hash="$(openssl passwd -6 "$pass")"
  umask 077
  cat >"$CREDS" <<EOF
host=$HOST_NAME
user=$USER_NAME
password=$pass
hash=$hash
ssh=ssh ${USER_NAME}@${HOST_NAME}.local
EOF
  echo "Wrote $CREDS"
else
  # shellcheck disable=SC1090
  hash="$(awk -F= '/^hash=/{print $2}' "$CREDS")"
  pass="$(awk -F= '/^password=/{print $2}' "$CREDS")"
fi

echo "Unmounting ${DEVICE} partitions..."
lsblk -lnpo NAME,MOUNTPOINT "$DEVICE" | while read -r name mnt; do
  if [[ -n "${mnt:-}" ]]; then
    udisksctl unmount -b "$name" || umount "$name" || true
  fi
done

echo "Writing image (sudo dd)..."
sudo xzcat "$IMAGE_XZ" | sudo dd of="$DEVICE" bs=4M status=progress conv=fsync
sudo blockdev --rereadpt "$DEVICE" 2>/dev/null || true
sleep 2
sudo partprobe "$DEVICE" 2>/dev/null || true
sleep 1

boot_part="${DEVICE}p1"
root_part="${DEVICE}p2"
echo "Mounting $boot_part and $root_part..."
boot_mnt="$(udisksctl mount -b "$boot_part" | awk -F'at ' '{print $2}')"
root_mnt="$(udisksctl mount -b "$root_part" | awk -F'at ' '{print $2}')"
echo "  boot: $boot_mnt"
echo "  root: $root_mnt"

: >"$boot_mnt/ssh"
echo "${USER_NAME}:${hash}" >"$boot_mnt/userconf.txt"
# rootfs is root-owned; bootfs is FAT and writable without sudo.
if [[ -w "$root_mnt/etc/hostname" ]]; then
  echo "$HOST_NAME" >"$root_mnt/etc/hostname"
else
  sudo tee "$root_mnt/etc/hostname" >/dev/null <<<"$HOST_NAME"
fi
if grep -q '^127.0.1.1' "$root_mnt/etc/hosts"; then
  sudo sed -i "s/^127.0.1.1.*/127.0.1.1\t${HOST_NAME}/" "$root_mnt/etc/hosts"
else
  printf '127.0.1.1\t%s\n' "$HOST_NAME" | sudo tee -a "$root_mnt/etc/hosts" >/dev/null
fi

sync
udisksctl unmount -b "$boot_part"
udisksctl unmount -b "$root_part"

echo
echo "Image written. Eject the card, boot the Pi 3 with HDMI + Ethernet."
echo "  ssh ${USER_NAME}@${HOST_NAME}.local"
echo "  password is in $CREDS"
echo "Then rsync this repo and run scripts/setup-pi.sh on the Pi."
