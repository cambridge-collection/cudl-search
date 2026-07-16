# CUDL Search API

## Prerequisites

1. Docker installed
2. `SOLR_HOST`, `SOLR_PORT` and `API_PORT` environment variables set in shell or in `.env` file.

## Running locally

    docker compose --env-file .env up --build --force-recreate

To run Solr and the API together, check out `cudl-solr` alongside this
repository and run:

    docker compose -f docker-compose-local-search.yml up -d --build --wait

The API is then available at <http://localhost> and Solr at
<http://localhost:8983>.

## Indexing collections locally

These scripts are for local development only. With Solr and the search API
running, clear any existing local collection data:

    ./scripts/clear-local-collection-indexes-for-dev.sh

Then index the JSON files from the appropriate release's downloaded
`collections` directory:

    ./scripts/index-collections-for-dev.sh /path/to/collections

The indexing script defaults to `http://localhost:80`. Pass a different
localhost URL as the second argument if necessary.

## Clearing items locally

To remove every document from the local item index:

    ./scripts/clear-local-item-index-for-dev.sh

## Accessing the API

The API will be available on port defined in `API_PORT`. If set to 90, it would be available at [http://localhost:90/items?q=*](http://localhost:90/items?q=*).If set to 80, it would be available at [http://localhost/items?q=*](http://localhost/items?q=*)
