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
COLLECTION_RELATION_CORE = 'collection_relation'

ITEM_EDGE_TYPE = "item"
SUBCOLLECTION_EDGE_TYPE = "subcollection"
COLLECTION_REF_PREFIX = "collections/"
COLLECTION_REF_SUFFIX = ".collection.json"

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
    elif resource_type_trimmed in ['collection-relation', 'collection_relation']:
        core = COLLECTION_RELATION_CORE

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
    await delete_by_query(resource_type, delete_query)


async def delete_by_query(resource_type: str, query: str):
    delete_cmd = {'delete': {'query': query}}

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


async def put_docs(resource_type: str, docs: List[dict], params=None):
    core = get_core_name(resource_type)
    path = 'update/json/docs'
    if not core:
        raise HTTPException(status_code=INTERNAL_ERROR_STATUS_CODE, detail="Invalid resource type")
    try:
        r = requests.post(url="%s/solr/%s/%s" % (SOLR_URL, core, path),
                          params=params,
                          headers={"content-type": "application/json; charset=UTF-8"},
                          data=json.dumps(docs),
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


def escape_solr_local_param_single_quoted_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def extract_translated_text(value) -> Union[str, None]:
    if isinstance(value, str):
        value_trimmed = value.strip()
        return value_trimmed if value_trimmed else None
    if isinstance(value, list):
        for entry in value:
            extracted = extract_translated_text(entry)
            if extracted:
                return extracted
        return None
    if isinstance(value, dict):
        preferred_keys = ["en", "value", "@value"]
        for key in preferred_keys:
            if key in value:
                extracted = extract_translated_text(value.get(key))
                if extracted:
                    return extracted
        for entry in value.values():
            extracted = extract_translated_text(entry)
            if extracted:
                return extracted
    return None


def normalize_collection_id(value: str) -> Union[str, None]:
    value_trimmed = str(value).strip()
    if not value_trimmed:
        return None
    if value_trimmed.startswith(COLLECTION_REF_PREFIX):
        value_trimmed = value_trimmed[len(COLLECTION_REF_PREFIX):]
    if value_trimmed.endswith(COLLECTION_REF_SUFFIX):
        value_trimmed = value_trimmed[:-len(COLLECTION_REF_SUFFIX)]
    return value_trimmed if value_trimmed else None


def extract_embedded_id(entry) -> Union[str, None]:
    if isinstance(entry, dict):
        entry_id = entry.get("_id")
        if entry_id is None:
            entry_id = entry.get("@id")
        if entry_id is None:
            entry_id = entry.get("id")
        if entry_id is None:
            return None
        return str(entry_id).strip()
    if isinstance(entry, str):
        return entry.strip()
    return None

def get_collection_slug(collection_doc: Dict[str, Union[str, dict]]) -> Union[str, None]:
    if not isinstance(collection_doc, dict):
        return None
    name_data = collection_doc.get("name")
    if not isinstance(name_data, dict):
        return None
    collection_id = name_data.get("url-slug")
    if collection_id is None:
        return None
    collection_id_str = str(collection_id).strip()
    return collection_id_str if collection_id_str else None


def get_collection_title_en_from_source(collection_doc: Dict[str, Union[str, dict]]) -> Union[str, None]:
    if not isinstance(collection_doc, dict):
        return None

    name_data = collection_doc.get("name")
    if isinstance(name_data, dict):
        title_full = extract_translated_text(name_data.get("full"))
        if title_full:
            return title_full
        title_short = extract_translated_text(name_data.get("short"))
        if title_short:
            return title_short

    flattened_full = extract_translated_text(collection_doc.get("name.full"))
    if flattened_full:
        return flattened_full
    flattened_short = extract_translated_text(collection_doc.get("name.short"))
    if flattened_short:
        return flattened_short
    return None


def extract_collection_item_ids(collection_doc: Dict[str, Union[str, list]]) -> List[str]:
    items = collection_doc.get("items")
    if not isinstance(items, list):
        return []
    item_ids = []
    for item in items:
        item_id_raw = extract_embedded_id(item)
        if not item_id_raw:
            continue
        item_ids.append(normalize_item_id(item_id_raw))
    return dedupe_preserve_order(item_ids)


def extract_collection_child_ids(collection_doc: Dict[str, Union[str, list]]) -> List[str]:
    child_collections = collection_doc.get("collections")
    if not isinstance(child_collections, list):
        return []
    child_ids = []
    for child in child_collections:
        child_ref = extract_embedded_id(child)
        if not child_ref:
            continue
        child_id = normalize_collection_id(child_ref)
        if child_id:
            child_ids.append(child_id)
    return dedupe_preserve_order(child_ids)


def build_relation_doc_id(edge_type: str, collection_id: str, target_id: str) -> str:
    collection_id_encoded = urllib.parse.quote(collection_id, safe="")
    target_id_encoded = urllib.parse.quote(target_id, safe="")
    return f"{edge_type}:{collection_id_encoded}:{target_id_encoded}"


def build_collection_relation_docs(collection_doc: Dict[str, Union[str, dict, list]]) -> Tuple[str, List[dict]]:
    collection_id = get_collection_slug(collection_doc)
    if not collection_id:
        raise ValueError("Collection JSON does not seem to conform to expectations")
    collection_title_en = get_collection_title_en_from_source(collection_doc)

    relation_docs = []
    item_ids = extract_collection_item_ids(collection_doc)
    for position, item_id in enumerate(item_ids, start=1):
        relation_doc = {
            "id": build_relation_doc_id(ITEM_EDGE_TYPE, collection_id, item_id),
            "edge_type_s": ITEM_EDGE_TYPE,
            "collection_id_s": collection_id,
            "member_item_id_s": item_id,
            "position_i": position,
        }
        if collection_title_en:
            relation_doc["collection_title_en_s"] = collection_title_en
        relation_docs.append(relation_doc)

    child_ids = extract_collection_child_ids(collection_doc)
    for position, child_id in enumerate(child_ids, start=1):
        relation_doc = {
            "id": build_relation_doc_id(SUBCOLLECTION_EDGE_TYPE, collection_id, child_id),
            "edge_type_s": SUBCOLLECTION_EDGE_TYPE,
            "collection_id_s": collection_id,
            "child_collection_id_s": child_id,
            "position_i": position,
        }
        if collection_title_en:
            relation_doc["collection_title_en_s"] = collection_title_en
        relation_docs.append(relation_doc)
    return collection_id, relation_docs


def parse_int_or_none(value) -> Union[int, None]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def doc_value_as_scalar(doc: dict, key: str):
    value = doc.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def extract_item_collection_positions(relation_docs: List[dict], item_id: str) -> List[dict]:
    direct_relations = []
    seen_collection_ids = set()
    item_id_str = str(item_id)
    for relation_doc in relation_docs:
        relation_item_id = doc_value_as_scalar(relation_doc, "member_item_id_s")
        collection_id = doc_value_as_scalar(relation_doc, "collection_id_s")
        position = parse_int_or_none(doc_value_as_scalar(relation_doc, "position_i"))
        collection_title_en = doc_value_as_scalar(relation_doc, "collection_title_en_s")
        if relation_item_id is None or str(relation_item_id) != item_id_str:
            continue
        if not collection_id or position is None:
            continue

        collection_id_str = str(collection_id)
        if collection_id_str in seen_collection_ids:
            continue
        seen_collection_ids.add(collection_id_str)
        direct_relations.append(
            {
                "collectionId": collection_id_str,
                "itemPosition": position,
                "collectionTitleEn": str(collection_title_en) if collection_title_en is not None else None,
            }
        )
    return direct_relations


def extract_parent_collection_positions(relation_docs: List[dict]) -> Dict[str, List[dict]]:
    parent_edges_by_child = {}
    seen_parent_collection_ids = {}
    for relation_doc in relation_docs:
        child_collection_id = doc_value_as_scalar(relation_doc, "child_collection_id_s")
        parent_collection_id = doc_value_as_scalar(relation_doc, "collection_id_s")
        position = parse_int_or_none(doc_value_as_scalar(relation_doc, "position_i"))
        parent_collection_title_en = doc_value_as_scalar(relation_doc, "collection_title_en_s")
        if not child_collection_id or not parent_collection_id or position is None:
            continue

        child_collection_id_str = str(child_collection_id)
        parent_collection_id_str = str(parent_collection_id)
        parent_edges_by_child.setdefault(child_collection_id_str, [])
        seen_parent_collection_ids.setdefault(child_collection_id_str, set())
        if parent_collection_id_str in seen_parent_collection_ids[child_collection_id_str]:
            continue
        seen_parent_collection_ids[child_collection_id_str].add(parent_collection_id_str)
        parent_edges_by_child[child_collection_id_str].append(
            {
                "parentCollectionId": parent_collection_id_str,
                "subcollectionPosition": position,
                "parentCollectionTitleEn": str(parent_collection_title_en) if parent_collection_title_en is not None else None,
            }
        )
    return parent_edges_by_child


def build_collection_lookup_response(
    item_id: str,
    direct_relations: List[dict],
    parent_relations_by_child: Dict[str, List[dict]],
):
    child_parent_collections = []
    for direct_relation in direct_relations:
        direct_id = direct_relation.get("collectionId")
        if not direct_id:
            continue
        parent_relations = parent_relations_by_child.get(direct_id) or []
        parent_list = []
        for parent_relation in parent_relations:
            parent_id = parent_relation.get("parentCollectionId")
            if not parent_id:
                continue
            parent_list.append(
                {
                    "slug": parent_id,
                    "titleEn": parent_relation.get("parentCollectionTitleEn"),
                    "position": parent_relation.get("subcollectionPosition"),
                }
            )
        child_parent_collections.append(
            {
                "slug": direct_id,
                "titleEn": direct_relation.get("collectionTitleEn"),
                "position": direct_relation.get("itemPosition"),
                "parent": parent_list,
            }
        )
    return {"items": [{"item": item_id, "collections": child_parent_collections}]}


def extract_response_docs(solr_response) -> List[dict]:
    response = solr_response.get("response") or {}
    docs = response.get("docs") or []
    return docs if isinstance(docs, list) else []


def build_item_and_parent_relation_clause(item_id: str) -> str:
    safe_item_id = escape_solr_phrase_value(item_id)
    item_edge_query = f'edge_type_s:"{ITEM_EDGE_TYPE}" AND member_item_id_s:"{safe_item_id}"'
    join_query = escape_solr_local_param_single_quoted_value(item_edge_query)
    return (
        f'({item_edge_query}) OR '
        f'(edge_type_s:"{SUBCOLLECTION_EDGE_TYPE}" '
        f'AND {{!join from=collection_id_s to=child_collection_id_s v=\'{join_query}\'}})'
    )


def build_item_collection_relation_query(item_id: str) -> str:
    item_id_trimmed = str(item_id).strip()
    if not item_id_trimmed:
        return ""
    return build_item_and_parent_relation_clause(item_id_trimmed)


async def rebuild_collection_relation_index(collection_doc: Dict[str, Union[str, dict, list]]):
    collection_id, relation_docs = build_collection_relation_docs(collection_doc)
    safe_collection_id = escape_solr_phrase_value(collection_id)
    await delete_by_query("collection-relation", f'collection_id_s:"{safe_collection_id}"')
    if relation_docs:
        await put_docs("collection-relation", relation_docs)


async def delete_collection_relation_index(collection_id: str):
    collection_id_normalized = normalize_collection_id(collection_id)
    if not collection_id_normalized:
        return
    safe_collection_id = escape_solr_phrase_value(collection_id_normalized)
    await delete_by_query("collection-relation", f'collection_id_s:"{safe_collection_id}"')


async def fetch_item_and_parent_relations(item_id: str) -> Tuple[List[dict], Dict[str, List[dict]]]:
    relation_query = build_item_collection_relation_query(item_id)
    if not relation_query:
        return [], {}

    params = {
        "rows": 100000,
        "fl": "member_item_id_s,collection_id_s,child_collection_id_s,position_i,collection_title_en_s",
        "q": relation_query,
        "sort": "collection_id_s asc,position_i asc",
        "omitHeader": "true",
    }
    response = await get_request("collection-relation", **params)
    relation_docs = extract_response_docs(response)
    return extract_item_collection_positions(relation_docs, item_id), extract_parent_collection_positions(relation_docs)


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


@app.get("/items/{file_id}/collections")
async def get_item_collections_by_file_id(file_id: str):
    normalized_item_id = normalize_item_id(file_id)
    direct_relations, parent_relations_by_child = await fetch_item_and_parent_relations(normalized_item_id)
    return build_collection_lookup_response(normalized_item_id, direct_relations, parent_relations_by_child)

@app.put("/item-collections")
async def update_item_collections(request: Request):
    data = await request.body()
    try:
        json_dict = json.loads(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(json_dict, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    collection_id = get_collection_slug(json_dict)
    if not collection_id:
        logger.error("Collection JSON does not seem to conform to expectations")
        raise HTTPException(status_code=400, detail="Collection JSON does not seem to conform to expectations")

    logger.info("Indexing item-collection relations for %s", collection_id)
    await rebuild_collection_relation_index(json_dict)
    return Response(status_code=204)


@app.delete("/item-collections/{collection_id}")
async def delete_item_collections(collection_id: str):
    await delete_collection_relation_index(collection_id)
    return Response(status_code=204)


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
        logger.error("Collection JSON does not seem to conform to expectations")
        raise HTTPException(status_code=400, detail="Collection JSON does not seem to conform to expectations")

    logger.info("Indexing %s", name_data["url-slug"])
    await put_item('collection', data, {'f': ['$FQN:/**', 'id:/name/url-slug']})
    await rebuild_collection_relation_index(json_dict)
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
        logger.error("JSON does not seem to conform to expectations: %s", file_id)
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
    await delete_collection_relation_index(file_id)
    return Response(status_code=204)
