# CUDL Search API

## Prerequisites

1. Docker installed
2. `SOLR_HOST` and `SOLR_PORT` environment variables set

## Running locally

    docker compose up --build --force-recreate

## Accessing the API

The API will be available on port 80.
http://localhost/items?q=*:*