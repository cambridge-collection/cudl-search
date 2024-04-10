#!/usr/bin/env python3

import json
import logging
import re
import requests
import urllib.parse
from typing import Union, List
from fastapi import FastAPI, Request, Query, HTTPException

logger = logging.getLogger(__name__)

SOLR_HOST = "localhost"
SOLR_PORT = 8983
SOLR_URL = 'http://%s:%s' % (SOLR_HOST, SOLR_PORT)

INTERNAL_ERROR_STATUS_CODE = '999'

# Core names
ITEM_CORE = 'cdcp'
COLLECTION_JSON_CORE = 'collection'

app = FastAPI()


def get_core_name(resource_type: str):
    core = ''

    resource_type_trimmed = re.sub(r's$', '', resource_type)
    if resource_type_trimmed == 'item':
        core = ITEM_CORE
    elif resource_type_trimmed == 'collection':
        core = COLLECTION_JSON_CORE

    return core


def delete_resource(resource_type: str, file_id: str):
    delete_query = "fileID:%s" % file_id
    delete_cmd = {'delete': {'query': delete_query}}

    core = get_core_name(resource_type)
    if core:
        r = requests.post(url="%s/solr/%s/update" % (SOLR_URL, core),
                          headers={"content-type": "application/json; charset=UTF-8"},
                          json=delete_cmd)
        status_code = r.status_code
    else:
        status_code = INTERNAL_ERROR_STATUS_CODE

    return status_code


def get_request(resource_type: str, **kwargs):
    core = get_core_name(resource_type)
    try:
        solr_params = kwargs.copy()
        if 'original_sort' in solr_params:
            del solr_params['original_sort']
        r = requests.get("%s/solr/%s/select" % (SOLR_URL, core), params=solr_params)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        if hasattr(e.response, 'text'):
            results = json.loads(e.response.text)
            raise HTTPException(status_code=results["responseHeader"]["status"], detail=results["error"]["msg"])
        else:
            raise HTTPException(status_code=502, detail=str(e).split(':')[-1])
    result = r.json()
    if 'original_sort' in kwargs and 'sort' in result['responseHeader']['params']:
        result['responseHeader']['params']['sort'] = kwargs["original_sort"]
    return result


def put_item(resource_type: str, data, params):
    core = get_core_name(resource_type)
    path = 'update/json/docs'
    try:
        r = requests.post(url="%s/solr/%s/%s" % (SOLR_URL, core, path),
                          params=params,
                          headers={"content-type": "application/json; charset=UTF-8"},
                          data=data)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise e
    status_code = r.status_code

    return status_code


# Does FastAPI escape params automatically?
def ensure_urlencoded(var, safe=''):
    if type(var) is str:
        return urllib.parse.quote(urllib.parse.unquote(var, safe))
    elif type(var) is dict:
        dict_new = {}
        for key, value in var.items():
            if value is not None:
                value_final = ''
                if type(value) is str:
                    value_final = urllib.parse.quote(urllib.parse.unquote(value), safe=safe)
                elif type(value) is list:
                    values = []
                    for i in value:
                        values.append(urllib.parse.quote(urllib.parse.unquote(i), safe=safe))
                    value_final = values
                dict_new.update({key: value_final})
        return dict_new


@app.get("/collections")
async def get_collections(q: List[str] = Query(default=None),
                          fq: List[str] = Query(default=None),
                          sort: Union[str, None] = None,
                          start: Union[str, None] = None,
                          rows: Union[int, None] = None):
    q_final = ' AND '.join(q) if hasattr(q, '__iter__') else q
    rows_final = rows if rows in [8, 20] else 20

    # Limit params passed through to SOLR
    # Add facet to exclude collections from results
    params = {"q": q_final, "fq": fq, "sort": sort, "start": start, "rows": rows_final}
    r = get_request('collections', **params)
    return r


@app.get("/items")
def get_items(q: List[str] = Query(default=None),
              fq: List[str] = Query(default=None),
              sort: Union[str, None] = None,
              start: Union[str, None] = None,
              rows: Union[int, None] = None):
    original_sort = None
    r = re.compile("^collection:")
    fq_filtered = list(filter(r.match, fq))
    collection_facet = fq_filtered[0] if fq_filtered else None
    if sort and re.search(r'^collection_sort', sort):
        original_sort = sort
        if collection_facet:
            if sort and re.search(r'collection_sort\s*(asc|desc)', sort.strip()):
                collection_name_raw = re.sub(r'^collection:', '', collection_facet)
                collection_name = re.sub(r'\s', '_', collection_name_raw)
                sort_field = "%s_sort" % collection_name
                sort_direction = re.sub(r'^.*collection_sort (asc|desc).*$', r'\1', sort)
                sort = " ".join((sort_field, sort_direction))
    q_final = ' AND '.join(q) if hasattr(q, '__iter__') else q
    rows_final = rows if rows in [8, 20] else 20

    # Limit params passed through to SOLR
    # Add facet to exclude collections from results
    params = {"q": q_final, "fq": fq, "sort": sort, "start": start, "rows": rows_final, "original_sort": original_sort}
    r = get_request('items', **params)
    return r


@app.get("/summary")
def get_summary(q: List[str] = Query(default=None),
                fq: Union[str, None] = None):
    q_final = ' AND '.join(q) if hasattr(q, '__iter__') else q

    # Very few params are relevant to the summary view
    params = {"q": q_final, "fq": fq}

    r = get_request('items', **params)

    # This query returns the first page of results and the areas of the response that will
    # principally be useful are the responseHeader, response (but not response > docs),
    # facet_counts and possibly highlighting (i.e. snippets).
    # Ultimately, we would change the data structure at this point to the common
    # format needed. Rather than spend time doing  this, I just deleted the docs, which
    # we wouldn't use in this view
    del r['response']['docs']
    return r


# All destructive requests (post, put, delete) will be in a separate API
# that's kept in a private subnet. All access to them would be limited to
# the services that require them (CUDL Indexer - for post, SNS Message on
# deletion of a TEI file in cudl-source-data).
@app.put("/collection")
async def update_collection(request: Request):
    # Receive data via a data-binary curl request from the CUDL Indexer lambda
    data = await request.body()

    # status_code = ''
    json_dict = json.loads(data)
    if json_dict['name']:
        url_slug = json_dict['name']["url-slug"]
        logger.info(f"Indexing %s" % url_slug)
        status_code = put_item('collection', data, {'f': ['$FQN:/**', 'id:/name/url-slug']})
    else:
        logger.info(f"Collection JSON does not seem to conform to expectations")
        # I wasn't sure what status_code to use for invalid document.
        status_code = '999'
    return status_code


@app.put("/item")
async def update_item(request: Request):
    # Receive data via a data-binary curl request from the CUDL Indexer lambda
    data = await request.body()

    json_dict = json.loads(data)
    if json_dict['descriptiveMetadata']:
        logger.info(f"Indexing %s" % json_dict['fileID'])
        status_code = put_item('item', data, {'split': '/pages', 'f': ['/pages/*', '/*']})
    else:
        logger.info(f"JSON does not seem to conform to expectations: %s" % json_dict['fileID'])
        # I wasn't sure what status_code to use for invalid document.
        status_code = '999'
    return status_code


@app.delete("/item/{file_id}")
def delete_item(file_id: str):
    return delete_resource('item', file_id)


@app.delete("/collection/{file_id}")
def delete_collection(file_id: str):
    return delete_resource('collection', file_id)
