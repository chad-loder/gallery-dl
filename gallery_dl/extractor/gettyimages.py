# -*- coding: utf-8 -*-

# Copyright 2026 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://www.gettyimages.com/"""

from .common import Extractor, Message
from .. import text
import re

BASE_URL = "https://www.gettyimages.com"
BASE_PATTERN = r"(?:https?://)?(?:www\.)?gettyimages\.com"


class GettyImagesExtractor(Extractor):
    """Base class for Getty Images extractors"""
    category = "gettyimages"
    root = BASE_URL
    filename_fmt = "{postId}.{extension}"
    # Default: Getty/Photographer Name/Date/
    directory_fmt = ("{category}", "{channelName}", "{publishDate[:10]}")
    archive_fmt = "{postId}_{imageSize}"
    request_interval = (1.0, 2.0)
    request_interval_min = 0.5

    def items(self):
        metadata = self.metadata()

        for item in self.media():
            data = self._transform_item(item)
            data.update(metadata)
            data["source_url"] = self.url

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
        asset_id = item.get("id") or item.get("assetId", "")
        caption = item.get("caption", "")
        title = item.get("title", "")
        artist = item.get("artist", "")
        asset_type = item.get("assetType", "image")
        is_video = asset_type == "film"

        # Get upload date (ISO format) or date submitted
        upload_date = item.get("uploadDate", "")
        if not upload_date:
            # Convert "January 29, 2026" to ISO format
            date_created = item.get("dateCreated", "")
            if date_created:
                upload_date = self._parse_date(date_created)

        # Fetch detail page for high-res URL and additional metadata
        landing_url = item.get("landingUrl", "")
        detail_data = self._get_detail_page_data(landing_url, is_video=is_video)

        media_url = detail_data.get("url")
        media_size = detail_data.get("size")
        location = detail_data.get("location")
        primary_event = detail_data.get("primaryEvent") or {}

        # Fall back to search API thumb URL if detail page fetch fails
        if not media_url:
            thumb_url = item.get("thumbUrl", "")
            if thumb_url:
                media_url = thumb_url
                # Extract size from thumb URL
                size_match = re.search(r's=(\d+)x\d+', thumb_url)
                media_size = int(size_match.group(1)) if size_match else 612
                self.log.debug("Falling back to thumbUrl (size=%d) for %s",
                               media_size, asset_id)
            else:
                media_size = 0
                self.log.warning("No media URL found for %s", asset_id)

        # Normalize channel ID (lowercase, replace spaces with hyphens)
        channel_id = text.slugify(artist) if artist else ""

        # Build sidecar-compatible data structure
        data = {
            # Core sidecar fields
            "url": f"{BASE_URL}{item.get('landingUrl', '')}",
            "title": title,
            "description": caption,
            "channelId": channel_id,
            "channelName": artist,
            "publishDate": upload_date,
            "postId": asset_id,
            "imageUrl": media_url,  # Keep as imageUrl for compatibility
            "imageSize": media_size,  # Resolution for archive deduplication

            # Getty-specific fields
            "id": asset_id,
            "assetType": asset_type,
            "family": item.get("family", ""),
            "licenseType": item.get("licenseType", ""),
            "releaseCode": item.get("releaseCode", ""),
            "collectionCode": item.get("collectionCode", ""),
            "collectionName": item.get("collectionName", ""),
            "orientation": item.get("orientation", ""),
            "maxWidth": item.get("maxDimensions", {}).get("width"),
            "maxHeight": item.get("maxDimensions", {}).get("height"),
            "dateSubmitted": item.get("dateSubmitted", ""),
            "dateCreated": item.get("dateCreated", ""),
            "altText": item.get("altText", ""),
            "seoTitle": item.get("seoTitle", ""),
            "seoCaption": item.get("seoCaption", ""),
            "isEmbeddable": item.get("isEmbeddable", False),
            "isFilm": is_video,

            # Location (from detail page)
            "location": location,

            # Event info (from search API or detail page primaryEvent)
            "eventName": item.get("eventName") or primary_event.get("name"),
            "eventId": item.get("eventId") or primary_event.get("id"),
            "eventDate": item.get("eventDate") or primary_event.get("date"),

            # Video-specific fields
            "clipLength": item.get("clipLength") if is_video else None,
        }

        return data

    # Image URL fields in order of preference (highest resolution first)
    # HighResComp: 2048x2048, defaultMainImageURL: 1024x1024, compUrl: 594x594
    _IMAGE_URL_FIELDS = ("HighResComp", "defaultMainImageURL", "compUrl")

    # Video URL field
    _VIDEO_URL_FIELD = "filmCompUrl"

    def _get_detail_page_data(self, landing_url, is_video=False):
        """Fetch detail page and extract media URL and additional metadata

        For images, tries URL fields in order of resolution preference:
        1. HighResComp (2048x2048)
        2. defaultMainImageURL (1024x1024)
        3. compUrl (594x594)

        For videos, uses filmCompUrl.

        Returns dict with keys: url, size, location, primaryEvent
        """
        result = {
            "url": None,
            "size": None,
            "location": None,
            "primaryEvent": None,
        }

        if not landing_url:
            return result

        try:
            detail_url = f"{self.root}{landing_url}"
            response = self.request(detail_url)
            html = response.text

            # Extract location
            loc_match = re.search(r'"location"\s*:\s*"([^"]+)"', html)
            if loc_match:
                result["location"] = loc_match.group(1)

            # Extract primaryEvent
            event_match = re.search(
                r'"primaryEvent"\s*:\s*\{([^}]+)\}', html
            )
            if event_match:
                event_str = "{" + event_match.group(1) + "}"
                try:
                    # Parse the event JSON
                    import json
                    # Fix unicode escapes
                    event_str = event_str.encode().decode('unicode_escape')
                    result["primaryEvent"] = json.loads(event_str)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            # Get media URL based on asset type
            if is_video:
                # For videos, use filmCompUrl
                match = re.search(
                    rf'"{self._VIDEO_URL_FIELD}"\s*:\s*"([^"]+)"',
                    html
                )
                if match:
                    url = match.group(1).encode().decode('unicode_escape')
                    # Extract size from video URL (e.g., s=mp4-640x640-gi -> 640)
                    size_match = re.search(r's=mp4-(\d+)x\d+', url)
                    result["size"] = int(size_match.group(1)) if size_match else 640
                    result["url"] = url
                    self.log.debug("Using %s (size=%d) for %s",
                                   self._VIDEO_URL_FIELD, result["size"], landing_url)
            else:
                # For images, try each URL field in order of preference
                for field in self._IMAGE_URL_FIELDS:
                    match = re.search(
                        rf'"{field}"\s*:\s*"([^"]+)"',
                        html
                    )
                    if match:
                        url = match.group(1).encode().decode('unicode_escape')
                        # Extract size from URL (e.g., s=2048x2048 -> 2048)
                        size_match = re.search(r's=(\d+)x\d+', url)
                        result["size"] = int(size_match.group(1)) if size_match else 0
                        result["url"] = url
                        self.log.debug("Using %s (size=%d) for %s",
                                       field, result["size"], landing_url)
                        break

            if not result["url"]:
                self.log.debug("No URL fields found in detail page: %s", landing_url)

        except Exception as e:
            self.log.debug("Failed to get detail page data for %s: %s", landing_url, e)

        return result

    def _parse_date(self, date_str):
        """Parse 'January 29, 2026' to ISO format '2026-01-29'"""
        import datetime
        try:
            dt = datetime.datetime.strptime(date_str, "%B %d, %Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return date_str


class GettyImagesSearchExtractor(GettyImagesExtractor):
    """Extractor for Getty Images search results"""
    subcategory = "search"
    archive_fmt = "s_{postId}_{imageSize}"
    pattern = (BASE_PATTERN + r"/search/2/image\?([^#]+)")
    example = "https://www.gettyimages.com/search/2/image?family=editorial&phrase=border%20agent&sort=newest"

    def __init__(self, match):
        GettyImagesExtractor.__init__(self, match)
        self.params = text.parse_query(match.group(1))

    def metadata(self):
        self.search_query = self.params.get("phrase", "").replace("+", " ")
        return {"search_query": self.search_query}

    def media(self):
        return self._pagination()

    def _pagination(self):
        """Paginate through search results"""
        # Build API URL from search params
        params = dict(self.params)
        page = int(params.pop("page", 1))

        while True:
            params["page"] = page
            api_url = f"{self.root}/search/2/image.json"

            response = self.request(api_url, params=params)
            data = response.json()

            gallery = data.get("gallery", {})
            assets = gallery.get("assets", [])

            if not assets:
                return

            yield from assets

            # Check if there are more pages
            current_page = gallery.get("page", page)
            last_page = gallery.get("lastPage", 1)

            if current_page >= last_page:
                return

            page += 1


class GettyImagesVideoSearchExtractor(GettyImagesExtractor):
    """Extractor for Getty Images video search results"""
    subcategory = "video-search"
    archive_fmt = "vs_{postId}_{imageSize}"
    pattern = (BASE_PATTERN + r"/search/2/film\?([^#]+)")
    example = "https://www.gettyimages.com/search/2/film?family=editorial&phrase=border%20agent&sort=newest"

    def __init__(self, match):
        GettyImagesExtractor.__init__(self, match)
        self.params = text.parse_query(match.group(1))

    def metadata(self):
        self.search_query = self.params.get("phrase", "").replace("+", " ")
        return {"search_query": self.search_query}

    def media(self):
        return self._pagination()

    def _pagination(self):
        """Paginate through video search results"""
        params = dict(self.params)
        page = int(params.pop("page", 1))

        while True:
            params["page"] = page
            api_url = f"{self.root}/search/2/film.json"

            response = self.request(api_url, params=params)
            data = response.json()

            gallery = data.get("gallery", {})
            assets = gallery.get("assets", [])

            if not assets:
                return

            yield from assets

            current_page = gallery.get("page", page)
            last_page = gallery.get("lastPage", 1)

            if current_page >= last_page:
                return

            page += 1


class GettyImagesEventExtractor(GettyImagesExtractor):
    """Extractor for Getty Images editorial events"""
    subcategory = "event"
    archive_fmt = "e_{eventId}_{postId}_{imageSize}"
    pattern = (BASE_PATTERN + r"/editorial-images/(?:news|entertainment|sport|archive)"
               r"/event/([^/]+)/(\d+)")
    example = "https://www.gettyimages.com/editorial-images/news/event/federal-agents-descend/776440446"

    def __init__(self, match):
        GettyImagesExtractor.__init__(self, match)
        self.event_slug = match.group(1)
        self.event_id = match.group(2)

    def metadata(self):
        # Event name from slug
        event_name = self.event_slug.replace("-", " ").title()
        return {
            "event_id": self.event_id,
            "event_name": event_name,
            "search_query": event_name,
        }

    def media(self):
        """Get all images and videos from an event"""
        # Fetch both images and videos from the event
        for media_type in ("image", "film"):
            yield from self._fetch_event_media(media_type)

    def _fetch_event_media(self, media_type):
        """Fetch images or videos for this event"""
        params = {
            "events": self.event_id,
            "family": "editorial",
            "sort": "newest",
        }

        page = 1
        api_endpoint = "image" if media_type == "image" else "film"

        while True:
            params["page"] = page
            api_url = f"{self.root}/search/2/{api_endpoint}.json"

            response = self.request(api_url, params=params)
            data = response.json()

            gallery = data.get("gallery", {})
            assets = gallery.get("assets", [])

            if not assets:
                return

            for asset in assets:
                # Add event info to each asset
                asset["eventId"] = self.event_id
                asset["eventName"] = self.event_slug.replace("-", " ").title()
                yield asset

            current_page = gallery.get("page", page)
            last_page = gallery.get("lastPage", 1)

            if current_page >= last_page:
                return

            page += 1


class GettyImagesVideoExtractor(GettyImagesExtractor):
    """Extractor for individual Getty Videos"""
    subcategory = "video"
    archive_fmt = "v_{postId}_{imageSize}"
    pattern = BASE_PATTERN + r"/detail/video/([^/]+)-video/(\d+)"
    example = "https://www.gettyimages.com/detail/video/anti-ice-protesters-video/2258774689"

    def __init__(self, match):
        GettyImagesExtractor.__init__(self, match)
        self.slug = match.group(1)
        self.video_id = match.group(2)

    def media(self):
        """Get single video by searching for its ID"""
        params = {
            "phrase": self.video_id,
            "family": "editorial",
        }

        api_url = f"{self.root}/search/2/film.json"
        response = self.request(api_url, params=params)
        data = response.json()

        gallery = data.get("gallery", {})
        assets = gallery.get("assets", [])

        for asset in assets:
            if asset.get("id") == self.video_id:
                yield asset
                return

        self.log.warning("Video %s not found in API search", self.video_id)


class GettyImagesImageExtractor(GettyImagesExtractor):
    """Extractor for individual Getty Images"""
    subcategory = "image"
    archive_fmt = "{postId}_{imageSize}"
    pattern = BASE_PATTERN + r"/detail/(?:news-photo|photo)/([^/]+)-(?:news-photo|photo)/(\d+)"
    example = "https://www.gettyimages.com/detail/news-photo/caption-slug-news-photo/2258890151"

    def __init__(self, match):
        GettyImagesExtractor.__init__(self, match)
        self.slug = match.group(1)
        self.image_id = match.group(2)

    def media(self):
        """Get single image by searching for its ID"""
        # Search by the specific image ID
        params = {
            "phrase": self.image_id,
            "family": "editorial",
        }

        api_url = f"{self.root}/search/2/image.json"
        response = self.request(api_url, params=params)
        data = response.json()

        gallery = data.get("gallery", {})
        assets = gallery.get("assets", [])

        # Find the matching asset
        for asset in assets:
            if asset.get("id") == self.image_id:
                yield asset
                return

        # If not found by ID search, try to extract from detail page
        # TODO: Could parse detail page HTML as fallback
        self.log.warning("Image %s not found in API search", self.image_id)
