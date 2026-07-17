#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 || ! $1 =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "Usage: $0 VERSION" >&2
    exit 1
fi

VERSION=$1
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
API=$ROOT/apps/api
WEB=$ROOT/apps/web
OUTPUT=$ROOT/release/$VERSION
ARCHIVE=$ROOT/release/mc-panel-$VERSION.tar.gz
STAGING=$(mktemp -d)
trap 'rm -rf "$STAGING"' EXIT

for command in uv npm rsync shasum tar; do
    command -v "$command" >/dev/null || { echo "Missing build command: $command" >&2; exit 1; }
done
[[ ! -e $OUTPUT && ! -e $ARCHIVE ]] || { echo "Release already exists." >&2; exit 1; }
find "$ROOT/deploy" -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

(
    cd "$API"
    uv sync --frozen --extra dev
    uv run ruff format --check src tests ../../helper/mc_panel_action.py \
        ../../deploy/scripts/render-runtime-profile.py
    uv run ruff check src tests ../../helper/mc_panel_action.py \
        ../../deploy/scripts/render-runtime-profile.py
    uv run mypy src
    uv run pytest
)
(
    cd "$WEB"
    npm ci
    npm test
    npm run build
)

STAGE=$STAGING/$VERSION
mkdir -p "$STAGE/wheelhouse" "$ROOT/release"
rsync -a \
    --exclude '.git/' \
    --include '.env.example' \
    --exclude '.env*' \
    --exclude '.venv/' \
    --exclude 'node_modules/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '.ruff_cache/' \
    --exclude '.coverage' \
    --exclude '.DS_Store' \
    --exclude 'local-production/' \
    --exclude '*.command' \
    --exclude 'ACCEPTANCE.md' \
    --exclude 'AUDIT-*.md' \
    --exclude 'PRODUCTION_CHANGE_PLAN.md' \
    --exclude 'PRODUCT-REVIEW-*.md' \
    --exclude 'RELEASE-*.md' \
    --exclude '*.db' \
    --exclude '*.db-shm' \
    --exclude '*.db-wal' \
    --exclude '*.log' \
    --exclude '*.pem' \
    --exclude '*.key' \
    --exclude '*.p12' \
    --exclude '*.pfx' \
    --exclude 'data/' \
    --exclude 'uploads/' \
    --exclude 'quarantine/' \
    --exclude 'secrets/' \
    --exclude 'release/' \
    --exclude 'wheelhouse/' \
    "$ROOT/" "$STAGE/"

uv export --project "$API" --format requirements-txt --no-dev \
    --no-emit-project --frozen --no-hashes \
    --output-file "$STAGE/requirements-linux.txt"
requirements_clean=$STAGE/requirements-linux.clean
sed '1,2d' "$STAGE/requirements-linux.txt" >"$requirements_clean"
mv "$requirements_clean" "$STAGE/requirements-linux.txt"
uv build --project "$API" --wheel --out-dir "$STAGE/wheelhouse"
uv run --python 3.12 --with pip==26.0.1 python -m pip download \
    --dest "$STAGE/wheelhouse" \
    --only-binary=:all: \
    --platform manylinux_2_39_x86_64 \
    --platform manylinux_2_34_x86_64 \
    --platform manylinux_2_28_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.12 \
    --abi cp312 \
    --requirement "$STAGE/requirements-linux.txt"

chmod +x "$STAGE"/deploy/scripts/*.sh "$STAGE"/deploy/scripts/*.py \
    "$STAGE/helper/mc_panel_action.py"
python3 "$STAGE/deploy/scripts/check-public-tree.py" "$STAGE"
MANIFEST=$STAGING/SHA256SUMS
(
    cd "$STAGE"
    find . -type f ! -name SHA256SUMS -print | LC_ALL=C sort | \
        while IFS= read -r file; do shasum -a 256 "$file"; done
) >"$MANIFEST"
mv "$MANIFEST" "$STAGE/SHA256SUMS"
mv "$STAGE" "$OUTPUT"
if tar --version 2>&1 | grep -qi bsdtar; then
    COPYFILE_DISABLE=1 tar -C "$ROOT/release" -czf "$ARCHIVE" \
        --no-xattrs --uid 0 --gid 0 --uname root --gname root "$VERSION"
else
    tar -C "$ROOT/release" -czf "$ARCHIVE" \
        --sort=name --no-xattrs --owner=0 --group=0 --numeric-owner "$VERSION"
fi
(
    cd "$ROOT/release"
    shasum -a 256 "$(basename "$ARCHIVE")" >"$(basename "$ARCHIVE").sha256"
)

echo "Release directory: $OUTPUT"
echo "Release archive: $ARCHIVE"
