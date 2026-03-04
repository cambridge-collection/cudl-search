#!/usr/bin/env python3

import json
import logging
import re
import os
import requests
import urllib.parse
from datetime import datetime, timezone
from typing import Union, List, Dict, Tuple
from fastapi import FastAPI, Request, Query, HTTPException, Response

logger = logging.getLogger('gunicorn.error')

if 'SOLR_HOST' in os.environ:
    SOLR_HOST = os.environ['SOLR_HOST']
else:
    print('ERROR: SOLR_HOST environment variable not set')

if 'SOLR_PORT' in os.environ:
    SOLR_PORT = os.environ['SOLR_PORT']
else:
    print('WARN: SOLR_PORT environment variable not set')

SOLR_URL = 'http://%s:%s' % (SOLR_HOST, SOLR_PORT)

INTERNAL_ERROR_STATUS_CODE = 500

# Core names
ITEM_CORE = 'cdcp'
COLLECTION_JSON_CORE = 'collection'

app = FastAPI()

FACET_LABELS = {
    "facet-collection": "Collection",
    "facet-subjects": "Subjects",
    "facet-pageHasTranscription": "Has transcription",
    "facet-pageHasTranslation": "Has translation",
    "facet-origin-place": "Origin place",
    "facet-languages": "Languages",
    "facet-creations-century": "Creation century",
    "facet-hasImage": "Has image",
}

METRIC_LABELS = {
    "pages": "Total pages",
    "manuscripts": "Total manuscripts",
    "pageHasTranscription": "Pages with transcription",
    "pageHasTranslation": "Pages with translation",
    "hasImage": "Pages Imaged",
}


def get_core_name(resource_type: str):
    core = ''

    resource_type_trimmed = re.sub(r's$', '', resource_type)
    if resource_type_trimmed == 'item':
        core = ITEM_CORE
    elif resource_type_trimmed == 'collection':
        core = COLLECTION_JSON_CORE

    return core


def http_exception_from_request_error(e: requests.exceptions.RequestException) -> HTTPException:
    response = getattr(e, "response", None)
    if response is None:
        return HTTPException(status_code=502, detail=str(e).split(':')[-1])

    status_code = response.status_code or 502
    detail = None
    response_text = response.text if hasattr(response, "text") else None

    try:
        results = response.json()
    except ValueError:
        results = None

    if isinstance(results, dict):
        error_data = results.get("error")
        if isinstance(error_data, dict):
            detail = error_data.get("msg") or error_data.get("message")
        if not detail:
            detail = results.get("message")
        if not detail and isinstance(results.get("responseHeader"), dict):
            header_status = results["responseHeader"].get("status")
            if isinstance(header_status, int) and header_status > 0:
                status_code = header_status

    if not detail:
        detail = response_text or str(e).split(':')[-1]

    return HTTPException(status_code=status_code, detail=detail)


async def delete_resource(resource_type: str, file_id: str):
    delete_query = "fileID:%s" % file_id
    delete_cmd = {'delete': {'query': delete_query}}

    core = get_core_name(resource_type)
    if not core:
        raise HTTPException(status_code=INTERNAL_ERROR_STATUS_CODE, detail="Invalid resource type")
    try:
        r = requests.post(url="%s/solr/%s/update" % (SOLR_URL, core),
                          headers={"content-type": "application/json; charset=UTF-8"},
                          json=delete_cmd,
                          timeout=60)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise http_exception_from_request_error(e)


async def get_request(resource_type: str, **kwargs):
    core = get_core_name(resource_type)
    try:
        solr_params = kwargs.copy()
        if 'original_sort' in solr_params:
            del solr_params['original_sort']
        r = requests.get("%s/solr/%s/spell" % (SOLR_URL, core), params=solr_params, timeout=60)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise http_exception_from_request_error(e)
    result = r.json()
    if 'original_sort' in kwargs and 'sort' in result['responseHeader']['params']:
        result['responseHeader']['params']['sort'] = kwargs["original_sort"]
    return result


async def put_item(resource_type: str, data, params):
    core = get_core_name(resource_type)
    path = 'update/json/docs'
    if not core:
        raise HTTPException(status_code=INTERNAL_ERROR_STATUS_CODE, detail="Invalid resource type")
    try:
        r = requests.post(url="%s/solr/%s/%s" % (SOLR_URL, core, path),
                          params=params,
                          headers={"content-type": "application/json; charset=UTF-8"},
                          data=data,
                          timeout=60)
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise http_exception_from_request_error(e)


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


def dedupe_preserve_order(values: List[str]) -> List[str]:
    unique_values = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        unique_values.append(value)
        seen.add(value)
    return unique_values


def normalize_item_id(item: str) -> str:
    item_trimmed = str(item).strip()
    if item_trimmed.startswith("json/"):
        return item_trimmed if item_trimmed.endswith(".json") else f"{item_trimmed}.json"
    if item_trimmed.endswith(".json"):
        return f"json/{item_trimmed}"
    return f"json/{item_trimmed}.json"


def split_and_normalize_items(items: List[str]) -> List[str]:
    split_items = []
    for item in items:
        for value in str(item).split(","):
            value_trimmed = value.strip()
            if value_trimmed:
                split_items.append(normalize_item_id(value_trimmed))
    return dedupe_preserve_order(split_items)


def escape_solr_phrase_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def build_collection_lookup_query(item_ids: List[str]) -> str:
    clauses = []
    for item_id in item_ids:
        safe_item = escape_solr_phrase_value(item_id)
        clauses.append(f"items._id:\"{safe_item}\"")
        clauses.append(f"{{!join from=id to=collections._id}}items._id:\"{safe_item}\"")
    return f"({' OR '.join(clauses)})"


def build_collection_lookup_facets(item_ids: List[str]) -> Tuple[Dict[str, dict], Dict[str, Tuple[str, str]]]:
    facets = {}
    facet_key_map = {}
    if len(item_ids) == 1:
        item_id = item_ids[0]
        safe_item_id = escape_solr_phrase_value(item_id)
        facets["direct"] = {
            "type": "terms",
            "field": "id",
            "limit": -1,
            "domain": {"filter": f"items._id:\"{safe_item_id}\""},
        }
        facets["parents"] = {
            "type": "terms",
            "field": "id",
            "limit": -1,
            "domain": {"filter": f"{{!join from=id to=collections._id}}items._id:\"{safe_item_id}\""},
        }
        facet_key_map[item_id] = ("direct", "parents")
        return facets, facet_key_map

    for index, item_id in enumerate(item_ids):
        safe_item_id = escape_solr_phrase_value(item_id)
        direct_key = f"i_{index}_direct"
        parent_key = f"i_{index}_parents"
        facets[direct_key] = {
            "type": "terms",
            "field": "id",
            "limit": -1,
            "domain": {"filter": f"items._id:\"{safe_item_id}\""},
        }
        facets[parent_key] = {
            "type": "terms",
            "field": "id",
            "limit": -1,
            "domain": {"filter": f"{{!join from=id to=collections._id}}items._id:\"{safe_item_id}\""},
        }
        facet_key_map[item_id] = (direct_key, parent_key)
    return facets, facet_key_map


def build_collection_ref(collection_id: str) -> str:
    return f"collections/{collection_id}.collection.json"


def get_collection_title_en(collection_doc):
    if not collection_doc:
        return None
    name_full = collection_doc.get("name.full")
    if isinstance(name_full, list) and name_full:
        return str(name_full[0])
    if isinstance(name_full, str):
        return name_full
    name_short = collection_doc.get("name.short")
    if isinstance(name_short, list) and name_short:
        return str(name_short[0])
    if isinstance(name_short, str):
        return name_short
    return None


def extract_facet_bucket_ids(facets, key: str) -> List[str]:
    facet_data = (facets or {}).get(key) or {}
    buckets = facet_data.get("buckets") or []
    values = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        value = bucket.get("val")
        if value is not None:
            values.append(str(value))
    return dedupe_preserve_order(values)


def build_collection_lookup_response(solr_response, item_ids: List[str], facet_key_map: Dict[str, Tuple[str, str]]):
    response = solr_response.get("response") or {}
    facets = solr_response.get("facets") or {}
    docs = response.get("docs") or []

    docs_by_id = {}
    for doc in docs:
        doc_id = doc.get("id")
        if doc_id:
            docs_by_id[doc_id] = doc

    output_items = []
    for item_id in item_ids:
        direct_key, parent_key = facet_key_map[item_id]
        direct_ids = extract_facet_bucket_ids(facets, direct_key)
        parent_ids = extract_facet_bucket_ids(facets, parent_key)

        positions = []
        for collection_id in dedupe_preserve_order(direct_ids + parent_ids):
            collection_doc = docs_by_id.get(collection_id) or {}
            collection_items = collection_doc.get("items._id") or []
            try:
                item_position = collection_items.index(item_id) + 1
            except ValueError:
                continue
            positions.append(
                {
                    "collectionId": collection_id,
                    "itemPosition": item_position,
                }
            )

        subcollection_positions = []
        for parent_id in parent_ids:
            parent_doc = docs_by_id.get(parent_id) or {}
            parent_collections = parent_doc.get("collections._id") or []
            for direct_id in direct_ids:
                child_ref = build_collection_ref(direct_id)
                try:
                    subcollection_position = parent_collections.index(child_ref) + 1
                except ValueError:
                    continue
                subcollection_positions.append(
                    {
                        "parentCollectionId": parent_id,
                        "subcollectionId": direct_id,
                        "subcollectionPosition": subcollection_position,
                    }
                )

        positions_by_collection = {
            position["collectionId"]: position["itemPosition"] for position in positions
        }
        parent_relations_by_child = {}
        seen_parent_ids_by_child = {}
        for relation in subcollection_positions:
            parent_collection_id = relation.get("parentCollectionId")
            subcollection_id = relation.get("subcollectionId")
            if not parent_collection_id or not subcollection_id:
                continue
            parent_relations_by_child.setdefault(subcollection_id, [])
            seen_parent_ids_by_child.setdefault(subcollection_id, set())
            if parent_collection_id in seen_parent_ids_by_child[subcollection_id]:
                continue
            seen_parent_ids_by_child[subcollection_id].add(parent_collection_id)
            parent_relations_by_child[subcollection_id].append(
                {
                    "parentCollectionId": parent_collection_id,
                    "subcollectionPosition": relation.get("subcollectionPosition"),
                }
            )

        child_parent_collections = []
        for direct_id in direct_ids:
            parent_relations = parent_relations_by_child.get(direct_id) or []
            parent_list = []
            for parent_relation in parent_relations:
                parent_id = parent_relation.get("parentCollectionId")
                if not parent_id:
                    continue
                parent_list.append(
                    {
                        "slug": parent_id,
                        "titleEn": get_collection_title_en(docs_by_id.get(parent_id)),
                        "position": parent_relation.get("subcollectionPosition"),
                    }
                )
            child_parent_collections.append(
                {
                    "slug": direct_id,
                    "titleEn": get_collection_title_en(docs_by_id.get(direct_id)),
                    "position": positions_by_collection.get(direct_id),
                    "parent": parent_list,
                }
            )

        output_items.append(
            {
                "item": item_id,
                "collections": child_parent_collections,
            }
        )

    return {"items": output_items}


def solr_facet_pairs_to_dict(pairs):
    if not pairs:
        return {}
    facet_dict = {}
    for i in range(0, len(pairs), 2):
        if i + 1 >= len(pairs):
            break
        label = pairs[i]
        count = pairs[i + 1]
        facet_dict[str(label)] = int(count) if count is not None else 0
    return facet_dict


def get_true_count(facet_dict):
    if not facet_dict:
        return 0
    for label, count in facet_dict.items():
        if str(label).lower() in ['true','yes']:
            return int(count) if count is not None else 0
    return 0


def build_sdmx_summary(solr_response):
    facet_fields_raw = solr_response.get("facet_counts", {}).get("facet_fields", {}) or {}
    # facet-itemLevel and facet-hasPage are excluded from the facets dataset but used for service stats.
    excluded_facets = {"facet-itemLevel", "facet-hasPage"}
    facet_names = [name for name in facet_fields_raw.keys() if name not in excluded_facets]

    structures = []
    data_sets = []

    def make_value_code(label, used_codes):
        base = re.sub(r"[^A-Za-z0-9_@$-]+", "_", str(label)).strip("_")
        code = re.sub(r"[^A-Za-z0-9_@$-]+", "_", str(base)).strip("_")
        if not code:
            code = "value"
        candidate = code
        counter = 1
        while candidate in used_codes:
            candidate = f"{code}_{counter}"
            counter += 1
        used_codes.add(candidate)
        return candidate

    pages = int(solr_response.get("response", {}).get("numFound") or 0)
    manuscripts = get_true_count(solr_facet_pairs_to_dict(facet_fields_raw.get("facet-itemLevel")))
    page_has_transcription = get_true_count(solr_facet_pairs_to_dict(facet_fields_raw.get("facet-pageHasTranscription")))
    page_has_translation = get_true_count(solr_facet_pairs_to_dict(facet_fields_raw.get("facet-pageHasTranslation")))
    has_image = get_true_count(solr_facet_pairs_to_dict(facet_fields_raw.get("facet-hasImage")))

    metric_codes = ["pages", "manuscripts", "pageHasTranscription", "pageHasTranslation", "hasImage"]
    observations_service = {
        "0": [pages],
        "1": [manuscripts],
        "2": [page_has_transcription],
        "3": [page_has_translation],
        "4": [has_image],
    }

    service_structure = {
        "links": [],
        "name": "service_stats",
        "dimensions": {
            "dataSet": [],
            "series": [],
            "observation": [
                {
                    "id": "metric",
                    "name": "metric",
                    "keyPosition": 0,
                    "values": [{"id": code, "name": METRIC_LABELS.get(code, code)} for code in metric_codes],
                }
            ],
        },
        "measures": {
            "observation": [
                {
                    "id": "count",
                    "name": "count",
                }
            ]
        },
        "attributes": {
            "dataSet": [],
            "series": [],
            "observation": [],
        },
    }
    service_structure_index = len(structures)
    structures.append(service_structure)
    data_sets.append(
        {
            "structure": service_structure_index,
            "action": "Information",
            "observations": observations_service,
        }
    )

    for facet_name in facet_names:
        pairs = facet_fields_raw.get(facet_name) or []
        value_entries = []
        observations = {}
        used_value_codes = set()
        for i in range(0, len(pairs), 2):
            if i + 1 >= len(pairs):
                break
            label = pairs[i]
            label_str = str(label)
            code = make_value_code(label_str, used_value_codes)
            value_entries.append({"id": code, "name": label_str})
            count = pairs[i + 1]
            observations[str(len(value_entries) - 1)] = [int(count)]

        facet_structure = {
            "links": [],
            "name": facet_name,
            "dimensions": {
                "dataSet": [],
                "series": [],
                "observation": [
                    {
                        "id": "value",
                        "name": "value",
                        "keyPosition": 0,
                        "values": value_entries,
                    }
                ],
            },
            "measures": {
                "observation": [
                    {
                        "id": "count",
                        "name": "count",
                    }
                ]
            },
            "attributes": {
                "dataSet": [],
                "series": [],
                "observation": [],
            },
        }

        structure_index = len(structures)
        structures.append(facet_structure)
        data_sets.append(
            {
                "structure": structure_index,
                "action": "Information",
                "observations": observations,
            }
        )

    return {
        "$schema": "https://json.sdmx.org/2.1/sdmx-json-data-schema.json",
        "meta": {
            "schema": "https://json.sdmx.org/2.1/sdmx-json-data-schema.json",
            "id": "summary",
            "prepared": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "test": False,
            "contentLanguages": ["en"],
            "sender": {
                "id": "CUDL",
                "name": "CUDL API",
                "names": {"en": "Lorem ipsum dolor sit amet, consectetur adipiscing elit."},
            },
        },
        "data": {
            "structures": structures,
            "dataSets": data_sets,
        },
    }


@app.get("/collections")
async def get_collections(q: List[str] = Query(default=None),
                          fq: List[str] = Query(default=None),
                          spellcheck: Union[bool, None] = None,
                          facet: Union[bool, None] = None,
                          omitHeader: Union[bool, None] = None,
                          echoParams: Union[str, None] = None,
                          hl: Union[bool, None] = None,
                          sort: Union[str, None] = None,
                          start: Union[str, None] = None,
                          rows: Union[int, None] = None):
    q_final = ' AND '.join(q) if hasattr(q, '__iter__') else q
    rows_final = rows if rows in [8, 20] else 20

    # Limit params passed through to SOLR
    # Add facet to exclude collections from results
    params = {"q": q_final, "fq": fq, "sort": sort, "start": start, "rows": rows_final, "spellcheck": spellcheck, "facet": facet, "hl": hl, "omitHeader": omitHeader, "echoParams": echoParams}
    r = await get_request('collections', **params)
    return r


@app.get("/items")
async def get_items(q: List[str] = Query(default=None),
              fq: List[str] = Query(default=None),
              sort: Union[str, None] = None,
              start: Union[str, None] = None,
              rows: Union[int, None] = None):
    original_sort = None
    r = re.compile("^collection-slug:")

    if fq:
        fq_filtered = list(filter(r.match, fq))
    else:
        fq_filtered = None
    collection_facet = fq_filtered[0] if fq_filtered else None
    if sort and re.search(r'collection_sort', sort):
        original_sort = sort
        if collection_facet:
            if sort and re.search(r'collection_sort\s+(asc|desc)', sort.strip()):
                collection_name_raw = re.sub(r'^collection-slug:', '', collection_facet)
                collection_name = re.sub(r'\s', '_', collection_name_raw)
                sort_field = "%s_sort" % collection_name
                sort = re.sub(r'(^|\s|,)collection_sort\s+(asc|desc)', r'\1%s \2' % sort_field, sort)

    q_final = ' AND '.join(q) if hasattr(q, '__iter__') else q
    rows_final = rows if rows in [8, 20] else 20

    # Limit params passed through to SOLR
    # Add facet to exclude collections from results
    params = {"q": q_final, "fq": fq, "sort": sort, "start": start, "rows": rows_final, "original_sort": original_sort}
    r = await get_request('items', **params)
    return r


@app.get("/summary")
async def get_summary(q: List[str] = Query(default=None),
                fq: Union[str, None] = None,
                facet_field: List[str] = Query(default=['facet-collection', 'facet-subjects', 'facet-pageHasTranscription', 'facet-pageHasTranslation', 'facet-origin-place', 'facet-languages', 'facet-creations-century', 'facet-hasImage', 'facet-itemLevel'], alias="facet.field"),
                f_facet_collection_facet_sort: Union[str, None] = Query(default=None, alias="f.facet-collection.facet.sort"),
                f_facet_subjects_facet_sort: Union[str, None] = Query(default='count', alias="f.facet-subjects.facet.sort"),
                f_facet_pageHasTranscription_facet_sort: Union[str, None] = Query(default=None, alias="f.facet-pageHasTranscription.facet.sort"),
                f_facet_pageHasTranslation_facet_sort: Union[str, None] = Query(default=None, alias="f.facet-pageHasTranslation.facet.sort"),
                f_facet_languages_facet_sort: Union[str, None] = Query(default='count', alias="f.facet-languages.facet.sort"),
                f_facet_origin_place_facet_sort: Union[str, None] = Query(default='count', alias="f.facet-origin-place.facet.sort"),
                f_facet_creations_century_facet_sort: Union[str, None] = Query(default=None, alias="f.facet-creations-century.facet.sort"),
                f_facet_hasImage_facet_sort: Union[str, None] = Query(default=None, alias="f.facet-hasImage.facet.sort"),
                f_facet_itemLevel_facet_sort: Union[str, None] = Query(default=None, alias="f.facet-itemLevel.facet.sort"),
                format: Union[str, None] = None,
                ):
    q_final = ' AND '.join(q) if hasattr(q, '__iter__') else q

    # Very few params are relevant to the summary view
    params = {
        "q": q_final,
        "fq": fq,
        "rows": 0,
        "facet.field": facet_field,
        "f.facet-collection.facet.sort": f_facet_collection_facet_sort,
        "f.facet-subjects.facet.sort": f_facet_subjects_facet_sort,
        "f.facet-pageHasTranscription.facet.sort": f_facet_pageHasTranscription_facet_sort,
        "f.facet-pageHasTranslation.facet.sort": f_facet_pageHasTranslation_facet_sort,
        "f.facet-languages.facet.sort": f_facet_languages_facet_sort,
        "f.facet-origin-place.facet.sort": f_facet_origin_place_facet_sort,
        "f.facet-creations-century.facet.sort": f_facet_creations_century_facet_sort,
        "f.facet-hasImage.facet.sort": f_facet_hasImage_facet_sort,
        "f.facet-itemLevel.facet.sort": f_facet_itemLevel_facet_sort,
    }

    r = await get_request('items', **params)
    return build_sdmx_summary(r) if format == "sdmx" else r


@app.get("/item-collections")
async def get_collection_lookup(
    item_id: List[str] = Query(default=None),
    item: List[str] = Query(default=None),
):
    item_ids = split_and_normalize_items((item_id or []) + (item or []))
    if not item_ids:
        raise HTTPException(status_code=400, detail="At least one item ID is required")

    facets, facet_key_map = build_collection_lookup_facets(item_ids)
    params = {
        "rows": 99,
        "fl": "id,name.full,name.short,items._id,collections._id",
        "q": build_collection_lookup_query(item_ids),
        "omitHeader": "true",
        "json.facet": json.dumps(facets, separators=(",", ":")),
    }
    solr_response = await get_request("collections", **params)
    return build_collection_lookup_response(solr_response, item_ids, facet_key_map)


# All destructive requests (post, put, delete) will be in a separate API
# that's kept in a private subnet. All access to them would be limited to
# the services that require them (CUDL Indexer - for post, SNS Message on
# deletion of a TEI file in cudl-source-data).
@app.put("/collection")
async def update_collection(request: Request):
    # Receive data via a data-binary curl request from the CUDL Indexer lambda
    data = await request.body()

    try:
        json_dict = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(json_dict, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    name_data = json_dict.get("name")
    if not isinstance(name_data, dict) or not name_data.get("url-slug"):
        logger.info("ERROR: Collection JSON does not seem to conform to expectations")
        raise HTTPException(status_code=400, detail="Collection JSON does not seem to conform to expectations")

    logger.info("Indexing %s", name_data["url-slug"])
    await put_item('collection', data, {'f': ['$FQN:/**', 'id:/name/url-slug']})
    return Response(status_code=204)


@app.put("/item")
async def update_item(request: Request):
    # Receive data via a data-binary curl request from the CUDL Indexer lambda
    data = await request.body()

    try:
        json_dict = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(json_dict, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    file_id = json_dict.get("fileID", "unknown")
    if not json_dict.get("pages"):
        logger.info("ERROR: JSON does not seem to conform to expectations: %s", file_id)
        raise HTTPException(status_code=400, detail=f"JSON does not seem to conform to expectations: {file_id}")

    logger.info("Indexing %s", file_id)
    await put_item('item', data, {'split': '/pages', 'f': ['/pages/*', '/*']})
    return Response(status_code=204)


@app.delete("/item/{file_id}")
async def delete_item(file_id: str):
    await delete_resource('item', file_id)
    return Response(status_code=204)


@app.delete("/collection/{file_id}")
async def delete_collection(file_id: str):
    await delete_resource('collection', file_id)
    return Response(status_code=204)
