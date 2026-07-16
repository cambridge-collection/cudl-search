#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: clear-local-item-index-for-dev.sh [SOLR_BASE_URL]

Delete every document from the item (cdcp) core in a locally running Solr.
SOLR_BASE_URL defaults to http://localhost:8983 and must resolve to localhost.

This helper is destructive and is for local development only.
EOF
}

if (( $# > 1 )); then
    usage >&2
    exit 2
fi

solr_url=${1:-http://localhost:8983}
solr_url=${solr_url%/}

if [[ ! "$solr_url" =~ ^https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?$ ]]; then
    printf 'Refusing to clear an index on non-local Solr: %s\n' "$solr_url" >&2
    printf 'This helper is for local development only.\n' >&2
    exit 2
fi

printf '%s\n' 'This will delete ALL documents from the local Solr item core (cdcp).'

if ! read -r -p 'Continue? [y/N] ' confirmation; then
    printf '\nNo input received; the index was not changed.\n' >&2
    exit 2
fi

if [[ ! "$confirmation" =~ ^[Yy]([Ee][Ss])?$ ]]; then
    printf 'Cancelled; the index was not changed.\n'
    exit 0
fi

core=cdcp
printf 'Clearing %s...\n' "$core"

if ! response=$(curl \
    --fail-with-body \
    --silent \
    --show-error \
    --request POST \
    --header 'Content-Type: application/json' \
    --data-binary '{"delete":{"query":"*:*"}}' \
    "$solr_url/solr/$core/update?commit=true" 2>&1); then
    printf 'Failed to clear %s\n%s\n' "$core" "$response" >&2
    exit 1
fi

if ! verification=$(curl \
    --fail-with-body \
    --silent \
    --show-error \
    --get \
    --data-urlencode 'q=*:*' \
    --data 'rows=0' \
    --data 'wt=json' \
    "$solr_url/solr/$core/select" 2>&1); then
    printf 'Failed to verify %s\n%s\n' "$core" "$verification" >&2
    exit 1
fi

if ! grep -Eq '"numFound"[[:space:]]*:[[:space:]]*0([,}])' <<<"$verification"; then
    printf '%s still contains documents after the delete request.\n' "$core" >&2
    printf '%s\n' "$verification" >&2
    exit 1
fi

printf 'Local item index cleared successfully.\n'
