#!/usr/bin/env bash
set -euo pipefail

readonly certificate_name=${MC_PANEL_CERTIFICATE_NAME:?Set MC_PANEL_CERTIFICATE_NAME}
[[ $certificate_name =~ ^[A-Za-z0-9._-]{1,253}$ ]] || {
    printf 'Invalid certificate name.\n' >&2
    exit 1
}
readonly source_dir=/etc/letsencrypt/live/$certificate_name
readonly target_dir=/etc/caddy/certs/mc-panel

for name in fullchain.pem privkey.pem; do
    if [[ ! -r "$source_dir/$name" ]]; then
        printf 'Certificate source is not readable: %s\n' "$source_dir/$name" >&2
        exit 1
    fi
done

install -d -o root -g caddy -m 0750 "$target_dir"
temp_dir=$(mktemp -d "$target_dir/.install.XXXXXX")
previous_dir=$(mktemp -d "$target_dir/.previous.XXXXXX")
rollback_needed=false

cleanup() {
    rm -rf "$temp_dir" "$previous_dir"
}

restore_previous() {
    local name
    for name in fullchain.pem privkey.pem; do
        rm -f "$target_dir/$name"
        if [[ -f "$previous_dir/$name" ]]; then
            install -o root -g caddy -m 0640 "$previous_dir/$name" "$target_dir/$name"
        fi
    done
}

handle_error() {
    local status=$?
    trap - ERR
    if [[ $rollback_needed == true ]]; then
        restore_previous || true
        if systemctl is-active --quiet caddy; then
            caddy validate --config /etc/caddy/Caddyfile || true
            systemctl reload caddy || true
        fi
    fi
    exit "$status"
}

trap cleanup EXIT
trap handle_error ERR

install -o root -g caddy -m 0640 "$source_dir/fullchain.pem" "$temp_dir/fullchain.pem"
install -o root -g caddy -m 0640 "$source_dir/privkey.pem" "$temp_dir/privkey.pem"

openssl x509 -in "$temp_dir/fullchain.pem" -noout
certificate_public_key=$(openssl x509 -in "$temp_dir/fullchain.pem" -pubkey -noout | openssl sha256)
private_public_key=$(openssl pkey -in "$temp_dir/privkey.pem" -pubout | openssl sha256)
if [[ $certificate_public_key != "$private_public_key" ]]; then
    printf 'Certificate and private key do not match.\n' >&2
    exit 1
fi

for name in fullchain.pem privkey.pem; do
    if [[ -L "$target_dir/$name" || ( -e "$target_dir/$name" && ! -f "$target_dir/$name" ) ]]; then
        printf 'Certificate target is not a regular file: %s\n' "$target_dir/$name" >&2
        exit 1
    fi
    if [[ -f "$target_dir/$name" ]]; then
        install -o root -g root -m 0600 "$target_dir/$name" "$previous_dir/$name"
    fi
done

rollback_needed=true
mv -f "$temp_dir/fullchain.pem" "$target_dir/fullchain.pem"
mv -f "$temp_dir/privkey.pem" "$target_dir/privkey.pem"

if systemctl is-active --quiet caddy; then
    caddy validate --config /etc/caddy/Caddyfile
    systemctl reload caddy
fi

rollback_needed=false
trap - ERR
