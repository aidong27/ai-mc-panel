#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root." >&2
    exit 1
fi
if [[ ${MC_PANEL_APPROVE_APT:-} != YES ]]; then
    echo "Refusing package changes. Set MC_PANEL_APPROVE_APT=YES after reviewing the change plan." >&2
    exit 1
fi

apt-get update
apt-get install --yes --no-install-recommends python3-venv acl

python3 --version
setfacl --version | head -n 1
