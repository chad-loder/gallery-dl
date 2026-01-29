# -*- coding: utf-8 -*-

# Copyright 2026 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.anadoluimages.com/"""

from .common import Extractor, Message
from .. import text

BASE_PATTERN = r"(?:https?://)?(?:www\.)?anadoluimages\.com"


class AnadoluExtractor(Extractor):
    """Base class for Anadolu Images extractors"""
    category = "anadolu"
    root = "https://www.anadoluimages.com"
    filename_fmt = "{id}.{extension}"
    # Default: Anadolu/Photographer Name/Date/
    directory_fmt = ("{category}", "{channelName}", "{publishDate[:10]}")
    archive_fmt = "anadolu_{id}"
    request_interval = (1.0, 2.0)
    request_interval_min = 0.5

    def _init(self):
        self.api = AnadoluAPI(self)

    def items(self):
        for item in self.media():
            data = self._transform_item(item)
            data["source_url"] = self.url  # Original URL used to find this item

            # Get preview URL (largest free version)
            url = data.get("imageUrl") or ""
            if not url:
                self.log.warning("%s: No image URL found", data.get("id"))
                continue

            yield Message.Directory, "", data
            yield Message.Url, url, text.nameext_from_url(url, data)

    def media(self):
        """Return an iterable of media items"""
        return ()

    def _transform_item(self, item, event_data=None):
        """Transform API item to sidecar-compatible format"""
        item_id = item.get("Id")
        title = item.get("Title", "")
        seo = item.get("Seo", "")
        pub_date = item.get("PubDate", "")

        # Get preview URL - either directly from PrevPath or convert from ThumbPath
        prev_path = item.get("PrevPath", "")
        thumb_path = item.get("ThumbPath", "")

        if prev_path and prev_path.startswith("http"):
            image_url = prev_path
        elif thumb_path:
            # Convert thumb URL to preview URL
            image_url = thumb_path.replace("/Thumb/", "/Preview/").replace(
                "_Thumb.", "_Preview."
            )
        else:
            image_url = ""

        # Build detail page URL
        detail_url = f"{self.root}/p/{seo}/{item_id}" if seo else ""

        # Fetch additional metadata from detail page
        detail_data = self._get_detail_page_data(detail_url)

        # Build sidecar-compatible data structure
        data = {
            # Core sidecar fields
            "url": detail_url,
            "title": title,
            "description": detail_data.get("description") or title,
            "channelId": text.slugify(detail_data.get("photographer", "")),
            "channelName": detail_data.get("photographer") or "Anadolu Images",
            "publishDate": pub_date,
            "postId": str(item_id),
            "imageUrl": image_url,

            # Anadolu-specific fields
            "id": item_id,
            "seo": seo,
            "type": "photo" if item.get("Type") == 1 else "video",
            "source": "AA" if item.get("Source") == 1 else "Custom",
            "editorChoice": item.get("EChoice", False),

            # Fields from detail page
            "photographer": detail_data.get("photographer"),
            "location": detail_data.get("location"),
            "anadoluId": detail_data.get("anadoluId"),
            "anadoluCategory": detail_data.get("category"),
        }

        # Add event data if provided
        if event_data:
            data["event_id"] = event_data.get("id")
            data["event_title"] = event_data.get("title", "")
            data["event_count"] = event_data.get("count", 0)
        else:
            # Use item title as event title if no event data
            data["event_title"] = title

        return data

    def _get_detail_page_data(self, detail_url):
        """Fetch detail page and extract additional metadata

        Extracts: photographer, location, anadoluId, category, description
        """
        result = {
            "photographer": None,
            "location": None,
            "anadoluId": None,
            "category": None,
            "description": None,
        }

        if not detail_url:
            return result

        try:
            response = self.request(detail_url)
            html = response.text

            # Extract photographer: <a id="lnkPhotographer" href="Name">
            result["photographer"] = text.extr(
                html, 'id="lnkPhotographer" href="', '"'
            ) or None

            # Extract AA ID: AA-12345678 (appears in format AA-40396441)
            aa_id = text.extr(html, "AA-", "<")
            if aa_id and aa_id.isdigit():
                result["anadoluId"] = "AA-" + aa_id

            # Extract location: <a id="lnkLocation" ...>Location Name</a>
            result["location"] = text.extr(
                html, 'id="lnkLocation"', '</a>'
            )
            if result["location"]:
                # Extract just the text after the last >
                result["location"] = result["location"].rpartition(">")[2]

            # Extract category: <li>Category<span>News</span>
            result["category"] = text.extr(
                html, "<li>Category<span>", "</span>"
            ) or None

            # Extract full description from og:description meta tag
            desc = text.extr(
                html, 'property="og:description" content="', '"'
            )
            if desc:
                result["description"] = text.unescape(desc)

        except Exception as e:
            self.log.debug("Failed to get detail page data for %s: %s",
                           detail_url, e)

        return result


class AnadoluCollectionExtractor(AnadoluExtractor):
    """Extractor for Anadolu Images collections/events"""
    subcategory = "collection"
    pattern = BASE_PATTERN + r"/Search\?collection=(\d+)"
    example = "https://www.anadoluimages.com/Search?collection=4531572"

    def _init(self):
        AnadoluExtractor._init(self)
        self.collection_id = self.groups[0]

    def media(self):
        # Get collection metadata
        collection_title = self.api.get_collection_title(self.collection_id)
        event_data = {
            "id": int(self.collection_id),
            "title": collection_title,
            "count": 0,  # Will be updated from items response
        }

        # Get all items in collection
        items = list(self.api.get_collection_items(self.collection_id))
        event_data["count"] = len(items)

        for item in items:
            item["_event_data"] = event_data
            yield item

    def _transform_item(self, item, event_data=None):
        event_data = item.pop("_event_data", None)
        return AnadoluExtractor._transform_item(self, item, event_data)


class AnadoluSearchExtractor(AnadoluExtractor):
    """Extractor for Anadolu Images search results"""
    subcategory = "search"
    directory_fmt = ("{category}", "{event_title}")
    pattern = BASE_PATTERN + r"/Search\?(?!collection=)([^#]+)"
    example = "https://www.anadoluimages.com/Search?phrase=minnesota+ICE&groupbyevent=true"

    def _init(self):
        AnadoluExtractor._init(self)
        self.params = text.parse_query(self.groups[0])

    def media(self):
        # Check if grouping by event
        group_by_event = self.params.get("groupbyevent", "").lower() == "true"

        if group_by_event:
            # Get events, then items within each event
            for event in self.api.search_events(self.params):
                event_data = {
                    "id": event.get("Id"),
                    "title": event.get("Title", ""),
                    "count": event.get("Count", 0),
                }

                # Get items in this event
                for item in self.api.get_collection_items(event["Id"]):
                    item["_event_data"] = event_data
                    yield item
        else:
            # Direct item search (no event grouping)
            for item in self.api.search_items(self.params):
                yield item

    def _transform_item(self, item, event_data=None):
        event_data = item.pop("_event_data", None)
        return AnadoluExtractor._transform_item(self, item, event_data)


class AnadoluAPI:
    """Interface for Anadolu Images API"""

    API_URL = "https://www.anadoluimages.com/Search"

    def __init__(self, extractor):
        self.extractor = extractor
        self.log = extractor.log
        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }

    def get_collection_title(self, collection_id):
        """Get the title of a collection/event"""
        url = f"{self.API_URL}/Collection/{collection_id}"
        try:
            data = self._call(url)
            return data.get("Title", "")
        except Exception as exc:
            self.log.debug("Failed to get collection title: %s", exc)
            return ""

    def get_collection_items(self, collection_id, page_size=100):
        """Get all items in a collection"""
        params = {
            "Collection": collection_id,
            "PageSize": page_size,
        }
        return self._pagination(params)

    def search_events(self, search_params, page_size=100):
        """Search for events (GroupByEvent=true)"""
        params = self._build_search_params(search_params)
        params["GroupByEvent"] = "true"
        params["PageSize"] = page_size
        return self._pagination(params)

    def search_items(self, search_params, page_size=100):
        """Search for individual items (no event grouping)"""
        params = self._build_search_params(search_params)
        params["GroupByEvent"] = "false"
        params["PageSize"] = page_size
        return self._pagination(params)

    def _build_search_params(self, url_params):
        """Convert URL parameters to API parameters"""
        params = {}

        # Map URL params to API params
        if "phrase" in url_params:
            params["Phrase"] = url_params["phrase"]

        if "contenttype" in url_params:
            # Can be "photo", "video", "photo-video", etc.
            content_type = url_params["contenttype"]
            if content_type == "photo-video":
                params["ContentType"] = ["photo", "video"]
            else:
                params["ContentType"] = content_type

        if "language" in url_params:
            params["Language"] = url_params["language"]

        if "category" in url_params:
            # Can be "news-politics" or similar
            categories = url_params["category"].split("-")
            params["Category"] = categories

        if "begindate" in url_params:
            params["BeginDate"] = url_params["begindate"]

        if "enddate" in url_params:
            params["EndDate"] = url_params["enddate"]

        return params

    def _call(self, url, params=None):
        """Make an API request"""
        response = self.extractor.request(
            url,
            params=params,
            headers=self.headers,
        )
        return response.json()

    def _pagination(self, params, page_size=100):
        """Paginate through search/list results"""
        params = dict(params)  # Copy to avoid modifying original
        params["PageSize"] = page_size
        page = 1

        while True:
            params["Page"] = page
            url = f"{self.API_URL}/List"

            data = self._call(url, params)
            documents = data.get("Documents", [])

            if not documents:
                return

            yield from documents

            # Check if we've reached the last page
            total = data.get("Total", 0)
            if page * page_size >= total:
                return

            page += 1
