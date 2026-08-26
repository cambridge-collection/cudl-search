#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: index-collections-for-dev.sh COLLECTIONS_DIRECTORY [API_BASE_URL]

Index collection JSON files into a locally running CUDL Search API.
API_BASE_URL defaults to http://localhost:80 and must resolve to localhost.

This helper is for local development only.
EOF
}

if (( $# < 1 || $# > 2 )); then
    usage >&2
    exit 2
fi

collections_dir=$1
api_url=${2:-http://localhost:80}
api_url=${api_url%/}

if [[ ! -d "$collections_dir" ]]; then
    printf 'Collection directory does not exist: %s\n' "$collections_dir" >&2
    exit 2
fi

if [[ ! "$api_url" =~ ^https?://(localhost|127\.0\.0\.1|\[::1\])(:[0-9]+)?$ ]]; then
    printf 'Refusing to index a non-local API: %s\n' "$api_url" >&2
    printf 'This helper is for local development only.\n' >&2
    exit 2
fi

shopt -s nullglob
collection_files=("$collections_dir"/*.json)

if (( ${#collection_files[@]} == 0 )); then
    printf 'No JSON files found in: %s\n' "$collections_dir" >&2
    exit 2
fi

total=${#collection_files[@]}
for (( index = 0; index < total; index++ )); do
    file=${collection_files[index]}
    printf '[%d/%d] Indexing %s\n' "$((index + 1))" "$total" "$(basename "$file")"

    if ! response=$(curl \
        --fail-with-body \
        --silent \
        --show-error \
        --request PUT \
        --header 'Content-Type: application/json' \
        --data-binary "@$file" \
        "$api_url/collection" 2>&1); then
        printf 'Failed to index %s\n%s\n' "$file" "$response" >&2
        exit 1
    fi
done

printf 'Indexed %d collection file(s) successfully.\n' "$total"
