#!/usr/bin/env bash
# Omarchy Live Wallpaper - uninstaller. Reverses everything install.sh did.
# Asks before removing your config and before any sudo step. Safe to run more than once.
set -euo pipefail

APP=omarchy-live-wallpaper
APP_DIR="$HOME/.local/share/$APP"
BIN="$HOME/.local/bin/$APP"
UNIT="$HOME/.config/systemd/user/$APP.service"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$APP"

UDEV_RULE=/etc/udev/rules.d/99-omarchy-live-wallpaper-rapl.rules
TMPFILES_CONF=/etc/tmpfiles.d/omarchy-live-wallpaper-rapl.conf
MODLOAD_CONF=/etc/modules-load.d/omarchy-live-wallpaper-drivetemp.conf

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }

ask() {
    local question=$1 default=${2:-n} hint reply
    if [[ $default == y ]]; then hint="[Y/n]"; else hint="[y/N]"; fi
    if [[ ! -t 0 ]]; then
        [[ $default == y ]]
        return
    fi
    read -r -p "$question $hint " reply || reply=""
    reply=${reply:-$default}
    [[ $reply =~ ^[Yy] ]]
}

[[ $EUID -ne 0 ]] || { echo "Run this as your normal user (not with sudo)." >&2; exit 1; }

bold "Removing Omarchy Live Wallpaper"

# 1. service
if systemctl --user cat "$APP.service" >/dev/null 2>&1; then
    systemctl --user disable --now "$APP.service" >/dev/null 2>&1 || true
    info "service stopped and disabled"
fi
if [[ -e $UNIT ]]; then
    rm -f "$UNIT"
    info "removed $UNIT"
fi
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user reset-failed "$APP.service" 2>/dev/null || true

# 2. program files
for path in "$APP_DIR" "$BIN"; do
    if [[ -e $path ]]; then
        rm -rf -- "$path"
        info "removed $path"
    fi
done

# 3. config (kept unless you say so)
if [[ -d $CONF_DIR ]]; then
    if ask "Also delete your config ($CONF_DIR)?" n; then
        rm -rf -- "$CONF_DIR"
        info "removed $CONF_DIR"
    else
        info "kept $CONF_DIR"
    fi
fi

# 4. optional system files (sudo)
sys_files=()
for f in "$UDEV_RULE" "$TMPFILES_CONF" "$MODLOAD_CONF"; do
    [[ -e $f ]] && sys_files+=("$f")
done
if [[ ${#sys_files[@]} -gt 0 ]]; then
    bold "System files created by the optional install step:"
    for f in "${sys_files[@]}"; do info "$f"; done
    echo "  Removing them restores the kernel defaults: the RAPL energy counter becomes root-only"
    echo "  again (right away) and drivetemp is no longer loaded at boot."
    if ask "Remove them now (needs sudo)?" n; then
        sudo rm -f -- "${sys_files[@]}"
        if [[ " ${sys_files[*]} " == *" $UDEV_RULE "* || " ${sys_files[*]} " == *" $TMPFILES_CONF "* ]]; then
            sudo udevadm control --reload || true
            for e in /sys/class/powercap/intel-rapl:*/energy_uj; do
                [[ -e $e ]] || continue
                { sudo chgrp root "$e" && sudo chmod 0400 "$e"; } || true
            done
            info "RAPL energy counters are root-only again"
        fi
        info "removed system files (drivetemp stays loaded until reboot; 'sudo modprobe -r drivetemp' to unload now)"
    else
        info "kept system files - remove later with: sudo rm ${sys_files[*]}"
    fi
fi

if [[ -e /etc/modules-load.d/drivetemp.conf ]]; then
    info "Note: /etc/modules-load.d/drivetemp.conf exists but was not created by this installer;"
    info "      remove it by hand if you don't need it (sudo rm /etc/modules-load.d/drivetemp.conf)."
fi

bold "Done. Omarchy's normal wallpaper is back."
