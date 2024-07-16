# CUDL Search API

## Prerequisites

1. Docker installed
2. `SOLR_HOST`, `SOLR_PORT` and `API_PORT` environment variables set in shell or in `.env` file.

## Running locally

    docker compose --env-file .env up --build --force-recreate

## Accessing the API

The API will be available on port defined in `API_PORT`. If set to 90, it would be available at [http://localhost:90/items?q=*](http://localhost:90/items?q=*).If set to 80, it would be available at [http://localhost/items?q=*](http://localhost/items?q=*)
