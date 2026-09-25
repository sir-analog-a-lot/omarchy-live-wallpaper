#!/usr/bin/env bash
# Omarchy Live Wallpaper - installer.
# Safe to run again at any time (updates files, never duplicates anything).
#
#   ./install.sh               interactive install
#   ./install.sh --yes         don't ask about installing missing packages / enabling the service
#   ./install.sh --no-system   skip the optional sudo step (drivetemp / RAPL permissions)
#   ./install.sh --force       skip the Arch/Hyprland checks (unsupported)
set -euo pipefail

APP=omarchy-live-wallpaper
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/$APP"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/$APP"

UDEV_RULE=/etc/udev/rules.d/99-omarchy-live-wallpaper-rapl.rules
TMPFILES_CONF=/etc/tmpfiles.d/omarchy-live-wallpaper-rapl.conf
MODLOAD_CONF=/etc/modules-load.d/omarchy-live-wallpaper-drivetemp.conf

# Runtime dependencies (verified from the imports: gi + Gtk 4 + Gtk4LayerShell + cairo, fonts C059/Nimbus Mono PS)
DEPS=(python python-gobject python-cairo gtk4 gtk4-layer-shell gsfonts)

ASSUME_YES=0
NO_SYSTEM=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        --no-system) NO_SYSTEM=1 ;;
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (try --help)" >&2; exit 2 ;;
    esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

# ask "question" y|n  -> returns 0 for yes. Without a terminal the default answer is used.
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

# ---------------------------------------------------------------------------------------------
bold "Omarchy Live Wallpaper installer"

[[ $EUID -ne 0 ]] || die "run this as your normal user (not with sudo); it asks for sudo only when needed."
[[ -f "$SRC/omarchy_live_wallpaper.py" ]] || die "run install.sh from the project folder."

# --- 1. system checks ---------------------------------------------------------------------------
os_id="" os_like=""
if [[ -r /etc/os-release ]]; then
    # shellcheck source=/dev/null
    os_id="$(. /etc/os-release && echo "${ID:-}")"
    # shellcheck source=/dev/null
    os_like="$(. /etc/os-release && echo "${ID_LIKE:-}")"
fi
if [[ $os_id == arch || $os_id == omarchy || " $os_like " == *" arch "* ]]; then
    info "OS: ${os_id} (Arch-based) - ok"
elif [[ $FORCE -eq 1 ]]; then
    warn "not an Arch-based system (ID=${os_id:-unknown}); continuing because of --force"
else
    die "this installer supports Arch Linux / Omarchy only (found ID=${os_id:-unknown}). Use --force to try anyway."
fi

if [[ $os_id == omarchy || -d /usr/share/omarchy || -d "$HOME/.local/share/omarchy" ]]; then
    info "Omarchy detected - ok"
else
    warn "Omarchy not detected. Plain Arch + Hyprland should work too."
fi

if command -v Hyprland >/dev/null 2>&1 || command -v hyprland >/dev/null 2>&1; then
    info "Hyprland installed - ok"
elif [[ $FORCE -eq 1 ]]; then
    warn "Hyprland not found; continuing because of --force (any wlr-layer-shell compositor may work)"
else
    die "Hyprland not found. Use --force to try with another wlr-layer-shell compositor."
fi
command -v systemctl >/dev/null 2>&1 || die "systemctl not found (systemd is required)."

# --- 2. dependencies ----------------------------------------------------------------------------
bold "Checking dependencies: ${DEPS[*]}"
missing=()
while IFS= read -r pkg; do
    [[ -n $pkg ]] && missing+=("$pkg")
done < <(pacman -T "${DEPS[@]}" 2>/dev/null || true)

if [[ ${#missing[@]} -eq 0 ]]; then
    info "all dependencies are installed"
else
    info "missing packages: ${missing[*]}"
    repo=() aur=()
    for pkg in "${missing[@]}"; do
        if pacman -Si "$pkg" >/dev/null 2>&1; then repo+=("$pkg"); else aur+=("$pkg"); fi
    done
    [[ ${#repo[@]} -gt 0 ]] && info "will run: sudo pacman -S --needed ${repo[*]}"
    helper=""
    if [[ ${#aur[@]} -gt 0 ]]; then
        if command -v yay >/dev/null 2>&1; then helper=yay
        elif command -v paru >/dev/null 2>&1; then helper=paru
        else die "not in the official repos and no AUR helper (yay/paru) found: ${aur[*]}"
        fi
        info "will run: $helper -S --needed ${aur[*]}"
    fi
    if [[ $ASSUME_YES -eq 0 && ! -t 0 ]]; then
        die "missing packages and no terminal to ask - install them yourself or re-run with --yes."
    fi
    if [[ $ASSUME_YES -eq 1 ]] || ask "Install them now?" y; then
        [[ ${#repo[@]} -gt 0 ]] && sudo pacman -S --needed "${repo[@]}"
        [[ ${#aur[@]} -gt 0 ]] && "$helper" -S --needed "${aur[@]}"
    else
        die "cannot continue without: ${missing[*]}"
    fi
fi

# --- 3. files -----------------------------------------------------------------------------------
bold "Installing files"
install -d "$APP_DIR" "$BIN_DIR" "$UNIT_DIR" "$CONF_DIR"
install -m 755 "$SRC/omarchy_live_wallpaper.py" "$APP_DIR/"
install -m 644 "$SRC/background.png" "$SRC/make_background.py" "$SRC/README.md" \
               "$SRC/config.example.toml" "$APP_DIR/"
install -m 755 "$SRC/bin/$APP" "$BIN_DIR/$APP"
install -m 644 "$SRC/systemd/$APP.service" "$UNIT_DIR/$APP.service"
info "$APP_DIR/"
info "$BIN_DIR/$APP"
info "$UNIT_DIR/$APP.service"
if [[ -e "$CONF_DIR/config.toml" ]]; then
    info "$CONF_DIR/config.toml (kept your existing config)"
else
    install -m 644 "$SRC/config.example.toml" "$CONF_DIR/config.toml"
    info "$CONF_DIR/config.toml (all options commented out = auto-detect)"
fi
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not in your PATH (the service still works; add it to run the command by hand)" ;;
esac

# --- 4. systemd user service --------------------------------------------------------------------
bold "Enabling the systemd user service"
systemctl --user daemon-reload
systemctl --user enable "$APP.service" >/dev/null 2>&1 || systemctl --user enable "$APP.service"
info "enabled (starts with your graphical session)"
if systemctl --user is-active --quiet graphical-session.target; then
    systemctl --user restart "$APP.service"
    info "started / restarted now"
else
    info "no graphical session detected - it will start at your next Hyprland login"
fi

# --- 5. optional system tweaks (sudo) ------------------------------------------------------------
bold "Detected sensors"
python3 "$APP_DIR/omarchy_live_wallpaper.py" --print-sensors 2>/dev/null | sed 's/^/  /' || true

need_drivetemp=0
if compgen -G "/sys/block/sd*" >/dev/null; then
    if ! grep -qsxF drivetemp /etc/modules-load.d/*.conf /usr/lib/modules-load.d/*.conf; then
        need_drivetemp=1
    elif [[ ! -d /sys/module/drivetemp ]]; then
        need_drivetemp=1
    fi
fi

rapl_group=wheel
id -nG | tr ' ' '\n' | grep -qx wheel || rapl_group="$(id -gn)"
rapl_group="${OLW_RAPL_GROUP:-$rapl_group}"
udev_text="# Omarchy Live Wallpaper: let group $rapl_group read the RAPL energy counters (CPU POWER gauge)
ACTION==\"add\", SUBSYSTEM==\"powercap\", KERNEL==\"intel-rapl:*\", RUN+=\"/usr/bin/chgrp $rapl_group /sys%p/energy_uj\", RUN+=\"/usr/bin/chmod g+r /sys%p/energy_uj\""
tmpfiles_text="# Omarchy Live Wallpaper: RAPL energy counters readable by $rapl_group at boot (backs up the udev rule)
z /sys/class/powercap/intel-rapl:*/energy_uj 0440 root $rapl_group -"

# Compare rule files ignoring comments, so re-running the installer is a no-op.
rules_of() { grep -v '^#' | sed '/^[[:space:]]*$/d'; }
need_rapl=0
rapl_file=/sys/class/powercap/intel-rapl:0/energy_uj
if [[ -e $rapl_file ]]; then
    if [[ ! -r $rapl_file ]]; then
        need_rapl=1                                   # counter is root-only (kernel default)
    elif [[ -e $UDEV_RULE ]] && [[ "$(rules_of <"$UDEV_RULE")" != "$(rules_of <<<"$udev_text")" ]]; then
        need_rapl=1                                   # our old rule differs (e.g. another group)
    fi
fi

if [[ $need_drivetemp -eq 0 && $need_rapl -eq 0 ]]; then
    info "no system changes needed (drivetemp / RAPL already fine or not applicable)"
elif [[ $NO_SYSTEM -eq 1 ]]; then
    info "skipping the optional system step (--no-system)"
else
    bold "Optional: system tweaks (need sudo, each one is optional - default is No)"
    if [[ $need_drivetemp -eq 1 ]]; then
        cat <<TXT

  DISK TEMP for SATA drives needs the 'drivetemp' kernel module (NVMe drives don't need it).
  This loads it now and at every boot by creating:
      $MODLOAD_CONF   (contains the single word 'drivetemp')
  Harmless: it only lets the kernel report the drive's own temperature sensor.
TXT
        if ask "  Load drivetemp now and at boot?" n; then
            if ! grep -qsxF drivetemp /etc/modules-load.d/*.conf /usr/lib/modules-load.d/*.conf; then
                echo drivetemp | sudo tee "$MODLOAD_CONF" >/dev/null
                info "wrote $MODLOAD_CONF"
            fi
            sudo modprobe drivetemp && info "drivetemp loaded"
        else
            info "skipped - DISK TEMP will show '--' on SATA drives"
        fi
    fi
    if [[ $need_rapl -eq 1 ]]; then
        cat <<TXT

  CPU POWER reads the Intel/AMD RAPL energy counter. Since 2020 the kernel lets only root
  read it, because of the "PLATYPUS" attack (CVE-2020-8694/8695): very fine-grained power
  readings can leak information about what other code on the CPU is doing, in theory even
  bits of cryptographic keys. This step makes the counter readable by group '$rapl_group'
  again, so any program running as a member of that group could read it too.
  On a single-user desktop the practical risk is small, but it is a real trade-off.
  If you say no, the gauge simply shows CPU LOAD % instead.
  Files created:
      $UDEV_RULE
      $TMPFILES_CONF
TXT
        if ask "  Make RAPL energy readable by group '$rapl_group'?" n; then
            printf '%s\n' "$udev_text" | sudo tee "$UDEV_RULE" >/dev/null
            printf '%s\n' "$tmpfiles_text" | sudo tee "$TMPFILES_CONF" >/dev/null
            sudo udevadm control --reload
            sudo systemd-tmpfiles --create "$TMPFILES_CONF"
            info "done - the gauge switches to CPU POWER within a second"
        else
            info "skipped - the CPU gauge shows LOAD % instead of watts"
        fi
    fi
fi

echo
bold "All done."
info "Configure:  \$EDITOR $CONF_DIR/config.toml  then  systemctl --user restart $APP"
info "Check:      $APP --print-sensors"
info "Remove:     ./uninstall.sh"
