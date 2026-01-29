# -*- coding: utf-8 -*-

# Copyright 2026 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.alamy.com/"""

from .common import Extractor, Message
from .. import text

BASE_PATTERN = r"(?:https?://)?(?:www\.)?alamy\.com"


class AlamyExtractor(Extractor):
    """Base class for alamy extractors"""
    category = "alamy"
    root = "https://www.alamy.com"
    filename_fmt = "{postId}_{title[:50]}.{extension}"
    directory_fmt = ("{category}", "{search_query}")
    archive_fmt = "{postId}"
    request_interval = (1.0, 2.0)
    request_interval_min = 0.5

    def _init(self):
        self.api = AlamyAPI(self)

    def items(self):
        metadata = self.metadata()

        for item in self.media():
            data = self._transform_item(item)
            data.update(metadata)

            url = data.get("imageUrl") or ""
            if not url:
                self.log.warning("%s: No download URL found", data.get("postId"))
                continue

            yield Message.Directory, "", data
            yield Message.Url, url, text.nameext_from_url(url, data)

    def media(self):
        """Return an iterable of media items"""
        return ()

    def metadata(self):
        """Return general metadata"""
        return {}

    def _transform_item(self, item):
        """Transform API item to sidecar-compatible format"""
        altids = item.get("altids", {})
        pseudo = item.get("pseudo", {})
        renditions = item.get("renditions", {})

        # Get best available image/video URL
        if item.get("type") == "video":
            sample_video = renditions.get("sample_video", {})
            image_url = sample_video.get("href", "")
        else:
            comp = renditions.get("comp", {})
            image_url = comp.get("href", "")

        # Normalize channel ID (lowercase, replace spaces with hyphens)
        contributor_name = pseudo.get("name", "")
        channel_id = text.slugify(contributor_name) if contributor_name else ""

        # Truncate caption for title (first 100 chars, break at word)
        caption = item.get("caption", "")
        title = caption[:100].rsplit(" ", 1)[0] if len(caption) > 100 else caption

        # Build sidecar-compatible data structure
        data = {
            # Core sidecar fields
            "url": item.get("uri", ""),
            "title": title,
            "description": caption,
            "channelId": channel_id,
            "channelName": contributor_name,
            "publishDate": item.get("firstcreated", ""),
            "postId": altids.get("ref", ""),
            "imageUrl": image_url,

            # Additional Alamy metadata
            "alamyId": altids.get("id", ""),
            "alamySeq": altids.get("seq"),
            "license": item.get("license", ""),
            "licenseType": "Rights Managed" if item.get("license") == "RM" else "Royalty Free",
            "collectionType": item.get("collectionType", ""),
            "type": item.get("type", ""),
            "subType": item.get("subType", ""),
            "width": item.get("width"),
            "height": item.get("height"),
            "uploadDate": item.get("uploaddate", ""),
            "language": item.get("language", ""),
            "modelRelease": item.get("modelrelease"),
            "propertyRelease": item.get("propertyrelease"),
            "restriction": item.get("restriction"),

            # Contributor info
            "contributor": {
                "id": pseudo.get("id", ""),
                "name": contributor_name,
            },

            # All renditions for reference
            "renditions": {
                key: {
                    "href": val.get("href", ""),
                    "width": val.get("width"),
                    "height": val.get("height"),
                    "mimetype": val.get("mimetype", ""),
                }
                for key, val in renditions.items()
                if isinstance(val, dict)
            },
        }

        # Add duration for videos
        if item.get("type") == "video" and "duration" in item:
            data["duration"] = item["duration"]

        return data


class AlamySearchExtractor(AlamyExtractor):
    """Extractor for alamy.com search results"""
    subcategory = "search"
    directory_fmt = ("{category}", "Search", "{search_query}")
    archive_fmt = "s_{search_query}_{postId}"
    pattern = (BASE_PATTERN + r"/stock-photo/([^/?#]+)\.html"
               r"(?:\?([^#]*))?")
    example = "https://www.alamy.com/stock-photo/ice-minnesota.html"

    def metadata(self):
        query_slug, params = self.groups
        self.search_query = text.unquote(query_slug.replace("-", " "))
        self.params = text.parse_query(params) if params else {}
        return {"search_query": self.search_query}

    def media(self):
        # Map URL params to API params
        collection = self._get_collection()
        sort = self._get_sort()

        return self.api.search(
            query=self.search_query,
            collection=collection,
            sort=sort,
        )

    def _get_collection(self):
        """Map URL collectiontype param to API searchCollection"""
        collection_map = {
            "editorial-core": "editorial-core",
            "editorial-archive": "editorial-archive",
            "creative": "ultimate",
            "vital": "vital",
            "uncut": "uncut",
            "foundation": "foundation",
        }
        url_collection = self.params.get("collectiontype", "editorial-core")
        return collection_map.get(url_collection, "editorial-core")

    def _get_sort(self):
        """Map URL sortBy param to API sort format"""
        sort_map = {
            "newest-datetaken": "DateTaken:desc",
            "oldest-datetaken": "DateTaken:asc",
            "newest": "DateTaken:desc",
            "oldest": "DateTaken:asc",
        }
        url_sort = self.params.get("sortBy", "newest-datetaken")
        return sort_map.get(url_sort, "DateTaken:desc")


class AlamyImageExtractor(AlamyExtractor):
    """Extractor for individual alamy images"""
    subcategory = "image"
    directory_fmt = ("{category}", "{channelName}")
    archive_fmt = "{postId}"
    pattern = BASE_PATTERN + r"/([^/?#]+-image(\d+))\.html"
    example = "https://www.alamy.com/caption-slug-image12345678.html"

    def metadata(self):
        self.slug, self.image_seq = self.groups
        return {}

    def media(self):
        # Search for this specific image by its sequence ID
        # The API doesn't have a direct lookup, so we search and filter
        # This is a workaround - ideally we'd scrape the detail page
        return self.api.search_by_id(self.image_seq)


class AlamyAPI:
    """Interface for Alamy's search API"""

    API_URL = "https://www.alamy.com/search-api/v2/search/"

    def __init__(self, extractor):
        self.extractor = extractor
        self.log = extractor.log
        self.headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }

    def search(self, query, collection="editorial-core", sort="DateTaken:desc",
               page_size=100):
        """Search for images/videos"""
        params = {
            "qt": query,
            "searchCollection": collection,
            "sort": sort,
            "langCode": "en",
            "pageNumber": 1,
            "pageSize": page_size,
            "count": "true",
            "lexical": "true",
            "allowProhibited": "false",
            "public": "true",
            "isBot": "false",
        }
        return self._pagination(params)

    def search_by_id(self, seq_id):
        """Search for a specific image by sequence ID"""
        # Use the seq ID as query - this may return multiple results
        # but we filter to the exact match
        params = {
            "qt": seq_id,
            "searchCollection": "editorial-core",
            "sort": "DateTaken:desc",
            "langCode": "en",
            "pageNumber": 1,
            "pageSize": 10,
            "count": "true",
            "lexical": "true",
        }

        data = self._call(params)
        items = data.get("items", [])

        # Find exact match
        for item in items:
            altids = item.get("altids", {})
            if str(altids.get("seq")) == str(seq_id):
                return [item]

        # If no exact match found in editorial-core, try other collections
        for collection in ["vital", "uncut", "ultimate", "foundation"]:
            params["searchCollection"] = collection
            data = self._call(params)
            items = data.get("items", [])
            for item in items:
                altids = item.get("altids", {})
                if str(altids.get("seq")) == str(seq_id):
                    return [item]

        self.log.warning("Image %s not found in API", seq_id)
        return []

    def _call(self, params):
        """Make an API request"""
        response = self.extractor.request(
            self.API_URL,
            params=params,
            headers=self.headers,
        )
        return response.json()

    def _pagination(self, params):
        """Paginate through search results"""
        while True:
            data = self._call(params)
            items = data.get("items", [])

            if not items:
                return

            yield from items

            # Check if we've reached the last page
            if len(items) < params["pageSize"]:
                return

            params["pageNumber"] += 1
