#!/bin/bash
# OVH rescue : injecte la cle SSH pour ubuntu (sans mot de passe apres reboot).
set -euo pipefail

PUBKEY="${1:-}"

if [[ -z "$PUBKEY" ]]; then
  echo "Usage: bash ovh-rescue-inject-ssh.sh 'ssh-ed25519 AAAA...'"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Lancez en root (mode rescue)."
  exit 1
fi

PART=""
for cand in /dev/sda1 /dev/sdb1 /dev/vda1; do
  if blkid "$cand" 2>/dev/null | grep -q ext4; then
    PART="$cand"
    break
  fi
done
if [[ -z "$PART" ]]; then
  PART="$(lsblk -nrpo NAME,FSTYPE | awk '$2=="ext4" {print $1; exit}')"
fi
if [[ -z "$PART" || ! -b "$PART" ]]; then
  echo "Partition ext4 introuvable. lsblk :"
  lsblk
  exit 1
fi

echo "Montage $PART ..."
mkdir -p /mnt
mount "$PART" /mnt

if ! grep -q '^ubuntu:' /mnt/etc/passwd; then
  echo "Utilisateur ubuntu introuvable dans /mnt/etc/passwd"
  exit 1
fi

UID="$(grep '^ubuntu:' /mnt/etc/passwd | cut -d: -f3)"
GID="$(grep '^ubuntu:' /mnt/etc/passwd | cut -d: -f4)"

mkdir -p /mnt/home/ubuntu/.ssh
AUTH=/mnt/home/ubuntu/.ssh/authorized_keys
grep -qF "$PUBKEY" "$AUTH" 2>/dev/null || echo "$PUBKEY" >> "$AUTH"
chmod 700 /mnt/home/ubuntu/.ssh
chmod 600 "$AUTH"
chown -R "$UID:$GID" /mnt/home/ubuntu/.ssh

echo "Cle SSH injectee pour ubuntu. Redemarrage..."
umount /mnt
reboot
