# -*- coding: utf-8 -*-

# Copyright 2026 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.pond5.com/"""

from .common import Extractor, Message
from .. import text
import json


BASE_PATTERN = r"(?:https?://)?(?:www\.)?pond5\.com"


class Pond5Extractor(Extractor):
    """Base class for Pond5 extractors"""
    category = "pond5"
    root = "https://www.pond5.com"
    filename_fmt = "{postId}.{extension}"
    directory_fmt = ("{category}", "{channelName}", "{publishDate[:10]}")
    archive_fmt = "pond5_{postId}"
    cookies_domain = ".pond5.com"
    cookies_names = ("datadome", "PHPSESSID")

    # Sort options
    SORT_BEST_MATCH = "1"
    SORT_POPULAR = "2"
    SORT_NEWEST = "6"
    SORT_DURATION_SHORT = "3"
    SORT_DURATION_LONG = "4"

    def _api_search(self, query, sort="6", media_type="footage", page=1,
                    per_page=48, filters=None):
        """Call the Pond5 search API"""
        url = f"{self.root}/index.php"

        # Build query string with any filters
        q = query
        if filters:
            q = " ".join(f"{k}:{v}" for k, v in filters.items()) + " " + query

        params = {
            "page": "ajax_search",
            "bmtext": media_type,
            "pagenum": page,
            "q": q,
            "sb": sort,
            "perPage": per_page,
            "searchLayout": "paginated",
            "previewSize": "small",
        }

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
        }

        response = self.request(url, params=params, headers=headers)
        return response.json()

    def _get_detail_page_data(self, detail_url):
        """Fetch detail page and extract JSON-LD metadata"""
        result = {
            "frameRate": None,
            "location": None,
            "usage": None,
            "modelReleased": None,
            "propertyReleased": None,
            "bitrate": None,
            "bitDepth": None,
            "looping": None,
            "alphaMatte": None,
            "alphaChannel": None,
            "keywords": [],
            "durationPrecise": None,
            "codec": None,
            "containerFormat": None,
            "fileSize": None,
        }

        if not detail_url:
            return result

        try:
            response = self.request(detail_url, fatal=False)

            # Check for bot protection (DataDome)
            if response.status_code in (403, 429):
                if not hasattr(self, "_detail_warning_shown"):
                    self._detail_warning_shown = True
                    self.log.warning(
                        "Detail pages blocked by DataDome bot protection. "
                        "For full metadata, configure browser cookies: "
                        "https://github.com/mikf/gallery-dl#cookies"
                    )
                self.log.debug("Detail page blocked (403): %s", detail_url)
                return result

            html = response.text

            # Additional check for captcha page
            if len(html) < 2000 and "captcha-delivery" in html:
                if not hasattr(self, "_detail_warning_shown"):
                    self._detail_warning_shown = True
                    self.log.warning(
                        "Detail pages blocked by DataDome bot protection. "
                        "For full metadata, configure browser cookies."
                    )
                return result

            # Find JSON-LD script tags containing @graph
            # Use text.extr to find script content between tags
            for json_str in text.extract_iter(
                html, 'application/ld+json">', '</script>'
            ):
                if "@graph" not in json_str:
                    continue

                try:
                    data = json.loads(json_str)
                    graph = data.get("@graph", [])

                    for item in graph:
                        item_type = item.get("@type", "")

                        # Extract from VideoObject
                        if item_type == "VideoObject":
                            result["keywords"] = item.get("keywords", [])

                        # Extract from Product offers
                        if item_type == "Product":
                            offers = item.get("offers", [])
                            if offers and isinstance(offers, list):
                                offer = offers[0]
                                props = offer.get("additionalProperty", [])

                                for prop in props:
                                    name = prop.get("name", "")
                                    value = prop.get("value")

                                    if name == "Frame Rate":
                                        result["frameRate"] = value
                                    elif name == "Location":
                                        result["location"] = value if value != "N/A" else None
                                    elif name == "Usage":
                                        result["usage"] = value
                                    elif name == "Model Released":
                                        result["modelReleased"] = value
                                    elif name == "Property Released":
                                        result["propertyReleased"] = value
                                    elif name == "Average Bitrate":
                                        result["bitrate"] = value
                                    elif name == "Bit Depth":
                                        result["bitDepth"] = value
                                    elif name == "Looping":
                                        result["looping"] = value
                                    elif name == "Alpha Matte":
                                        result["alphaMatte"] = value
                                    elif name == "Alpha Channel":
                                        result["alphaChannel"] = value
                                    elif name == "Duration":
                                        result["durationPrecise"] = value
                                    elif name == "HD Codec":
                                        result["codec"] = value
                                    elif name == "HD Container Format":
                                        result["containerFormat"] = value
                                    elif name == "HD File Size":
                                        result["fileSize"] = value

                    # Found and parsed the graph, no need to continue
                    break

                except json.JSONDecodeError as e:
                    self.log.debug("Failed to parse JSON-LD: %s", e)

        except Exception as e:
            self.log.debug("Failed to fetch detail page %s: %s", detail_url, e)

        return result

    def _transform_item(self, item):
        """Transform a search result item into gallery-dl format"""
        # Parse the embedded JSON data for additional fields
        json_data = {}
        if item.get("jsonData"):
            try:
                # Unescape HTML entities in JSON string
                json_str = item["jsonData"]
                json_str = json_str.replace("&quot;", '"')
                json_str = json_str.replace("&#039;", "'")
                json_str = json_str.replace("&amp;", "&")
                json_data = json.loads(json_str)
            except (json.JSONDecodeError, TypeError):
                pass

        # Get video schema for description and upload date
        video_schema = item.get("videoSchema", {})

        # Extract date from category field (e.g., "Footage/2026-01-28")
        cat = json_data.get("cat", "")
        date_from_cat = ""
        if "/" in cat:
            date_from_cat = cat.split("/")[-1]

        # Get upload date from schema or category
        upload_date = video_schema.get("uploadDate", "")
        if not upload_date and date_from_cat:
            upload_date = date_from_cat + "T00:00:00Z"

        # Determine best video URL
        # Priority: downloadPreviewUrl (direct S3) > itemUrl.m4v > itemUrl.webmv
        video_url = ""
        item_urls = item.get("itemUrl", {})

        if item.get("downloadPreviewUrl"):
            video_url = item["downloadPreviewUrl"]
        elif item_urls.get("m4v"):
            video_url = item_urls["m4v"]
        elif item_urls.get("webmv"):
            video_url = item_urls["webmv"]

        # Get extension from URL
        extension = "mp4"
        if video_url:
            if ".webm" in video_url:
                extension = "webm"
            elif ".mp4" in video_url:
                extension = "mp4"

        # Unescape title
        title = text.unescape(item.get("name", "") or item.get("itemTitle", ""))

        # Get artist name
        artist = item.get("username") or json_data.get("artistname", "")

        # Resolution info
        resolution_x = json_data.get("x", 0)
        resolution_y = json_data.get("y", 0)
        variant = json_data.get("variant", "")

        # Duration
        duration_ms = item.get("duration", 0)
        duration_formatted = item.get("durationFormatted", "")

        # Description
        description = text.unescape(video_schema.get("description", ""))

        # Get detail page URL
        detail_url = item.get("itemLink", "")

        # Fetch additional metadata from detail page
        detail_data = self._get_detail_page_data(detail_url)

        # Determine usage/editorial status from detail page (more reliable)
        is_editorial = json_data.get("isEditorial", False)
        if detail_data.get("usage"):
            is_editorial = detail_data["usage"].lower() == "editorial"

        data = {
            # Standard gallery-dl fields
            "url": video_url,
            "title": title,
            "description": description,
            "channelId": text.slugify(artist),
            "channelName": artist or "Unknown",
            "publishDate": upload_date,
            "postId": str(item.get("oid") or item.get("id", "")),
            "extension": extension,

            # Pond5-specific fields
            "pond5Id": item.get("oid") or item.get("id"),
            "itemLink": detail_url,
            "thumbnailUrl": item.get("imgSrc", ""),
            "duration": duration_ms,
            "durationFormatted": duration_formatted,
            "durationPrecise": detail_data.get("durationPrecise"),
            "resolutionX": resolution_x,
            "resolutionY": resolution_y,
            "resolution": f"{resolution_x}x{resolution_y}" if resolution_x else "",
            "variant": variant,
            "isEditorial": is_editorial,
            "usage": detail_data.get("usage"),
            "isVR360": json_data.get("isVR360", False),
            "isStereoscopic": json_data.get("isStereoscopic", False),
            "isFree": json_data.get("isFree", False),
            "priceRange": json_data.get("prang", ""),

            # Detail page metadata
            "frameRate": detail_data.get("frameRate"),
            "location": detail_data.get("location"),
            "modelReleased": detail_data.get("modelReleased"),
            "propertyReleased": detail_data.get("propertyReleased"),
            "bitrate": detail_data.get("bitrate"),
            "bitDepth": detail_data.get("bitDepth"),
            "looping": detail_data.get("looping"),
            "alphaMatte": detail_data.get("alphaMatte"),
            "alphaChannel": detail_data.get("alphaChannel"),
            "codec": detail_data.get("codec"),
            "containerFormat": detail_data.get("containerFormat"),
            "fileSize": detail_data.get("fileSize"),
            "keywords": detail_data.get("keywords", []),
        }

        # Add source URL if we have it
        if hasattr(self, "source_url"):
            data["source_url"] = self.source_url

        return data

    def _items_from_search(self, query, sort="6", media_type="footage",
                           filters=None):
        """Yield items from a search query with pagination"""
        page = 1
        per_page = 48

        while True:
            data = self._api_search(
                query, sort=sort, media_type=media_type,
                page=page, per_page=per_page, filters=filters
            )

            output = data.get("output", {})
            results_data = output.get("searchResultsData", {})
            items = results_data.get("prePromoResults", [])

            if not items:
                break

            for item in items:
                yield self._transform_item(item)

            # Check if there are more pages
            page_count = output.get("pageCount", 1)
            if page >= page_count:
                break

            page += 1


class Pond5SearchExtractor(Pond5Extractor):
    """Extractor for Pond5 search results"""
    subcategory = "search"
    pattern = (BASE_PATTERN + r"/search\?(?:.*&)?kw=([^&#]+)"
               r"(?:&media=([^&#]+))?(?:&([^#]+))?")
    example = "https://www.pond5.com/search?kw=nature&media=footage"

    def __init__(self, match):
        Pond5Extractor.__init__(self, match)
        self.search_query = text.unquote(match.group(1).replace("-", " "))
        self.media_type = match.group(2) or "footage"
        self.extra_params = match.group(3) or ""
        self.source_url = match.group(0)

    def metadata(self):
        return {
            "search_query": self.search_query,
            "media_type": self.media_type,
            "source_url": self.source_url,
        }

    def items(self):
        meta = self.metadata()

        # Parse extra params for filters
        filters = {}
        if self.extra_params:
            params = text.parse_query(self.extra_params)
            # Map URL params to API filters
            if params.get("news_archival") == "1":
                filters["news_archival"] = "1"

        # Default to newest sort
        sort = self.SORT_NEWEST

        for item_data in self._items_from_search(
            self.search_query, sort=sort, media_type=self.media_type,
            filters=filters
        ):
            item_data.update(meta)
            url = item_data.get("url", "")
            if url:
                yield Message.Directory, "", item_data
                yield Message.Url, url, text.nameext_from_url(url, item_data)


class Pond5VideoExtractor(Pond5Extractor):
    """Extractor for individual Pond5 videos"""
    subcategory = "video"
    pattern = BASE_PATTERN + r"/stock-footage/item/(\d+)(?:-[^/?#]*)?"
    example = "https://www.pond5.com/stock-footage/item/12345678-example-video"

    def __init__(self, match):
        Pond5Extractor.__init__(self, match)
        self.video_id = match.group(1)
        self.source_url = match.group(0)

    def items(self):
        # Search for this specific video ID
        # The API doesn't have a direct "get by ID" endpoint, so we search
        data = self._api_search(self.video_id, per_page=10)

        output = data.get("output", {})
        results_data = output.get("searchResultsData", {})
        items = results_data.get("prePromoResults", [])

        # Find the matching item
        target_id = int(self.video_id)
        item_data = None

        for item in items:
            if item.get("oid") == target_id or item.get("id") == target_id:
                item_data = self._transform_item(item)
                break

        if not item_data:
            self.log.error("Video %s not found", self.video_id)
            return

        item_data["source_url"] = self.source_url

        yield Message.Directory, "", item_data

        url = item_data.get("url", "")
        if url:
            yield Message.Url, url, text.nameext_from_url(url, item_data)
