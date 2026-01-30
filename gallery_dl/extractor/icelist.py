# -*- coding: utf-8 -*-

# Copyright 2026 Mike Fährmann
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

"""Extractors for https://wiki.icelist.is/"""

from .common import Extractor, Message
from .. import text
import re
import json


BASE_PATTERN = r"(?:https?://)?wiki\.icelist\.is"


class IcelistExtractor(Extractor):
    """Base class for ICE List Wiki extractors"""
    category = "icelist"
    root = "https://wiki.icelist.is"
    filename_fmt = "{code}.{extension}"
    directory_fmt = ("{category}", "{page}", "{state}")
    archive_fmt = "icelist_{page}_{code}"
    request_interval = (1.0, 2.0)

    def __init__(self, match):
        Extractor.__init__(self, match)
        self.api_url = f"{self.root}/api.php"

    def _api_call(self, **params):
        """Make a MediaWiki API call"""
        params.setdefault("format", "json")
        response = self.request(self.api_url, params=params)
        return response.json()

    def _get_page_wikitext(self, page_title):
        """Get wikitext content for a page"""
        data = self._api_call(
            action="parse",
            page=page_title,
            prop="wikitext"
        )
        if "parse" in data and "wikitext" in data["parse"]:
            return data["parse"]["wikitext"]["*"]
        return None

    def _get_image_info(self, file_titles):
        """Get image URLs and metadata for a list of file titles"""
        if not file_titles:
            return {}

        # API accepts up to 50 titles at once
        results = {}
        for i in range(0, len(file_titles), 50):
            batch = file_titles[i:i+50]
            titles_str = "|".join(batch)

            data = self._api_call(
                action="query",
                titles=titles_str,
                prop="imageinfo",
                iiprop="url|size|mime|timestamp|user|sha1"
            )

            if "query" in data and "pages" in data["query"]:
                for page_id, page_data in data["query"]["pages"].items():
                    if "imageinfo" in page_data:
                        title = page_data["title"]
                        info = page_data["imageinfo"][0]
                        results[title] = {
                            "url": info.get("url", ""),
                            "width": info.get("width", 0),
                            "height": info.get("height", 0),
                            "size": info.get("size", 0),
                            "mime": info.get("mime", ""),
                            "timestamp": info.get("timestamp", ""),
                            "user": info.get("user", ""),
                            "sha1": info.get("sha1", ""),
                        }

        return results

    def _parse_wikitable(self, wikitext):
        """Parse a wikitable and extract rows with file references
        
        Returns list of dicts with columns mapped to values.
        Each column gets both a text value and any links extracted.
        """
        results = []
        
        # Find table content
        table_match = re.search(
            r'\{\|\s*class="wikitable[^"]*"(.*?)\|\}',
            wikitext,
            re.DOTALL
        )
        if not table_match:
            return results

        table_content = table_match.group(1)

        # Split by row separator |- (with optional whitespace)
        parts = re.split(r'\n\|-\s*', table_content)
        
        for part in parts:
            if not part.strip():
                continue
            
            # Skip header row (starts with !)
            if part.strip().startswith('!'):
                continue
            
            # Find file reference in this row
            file_match = re.search(r'\[\[File:([^\]|]+)', part)
            if not file_match:
                continue
            
            file_name = file_match.group(1)
            
            # Split row into cells - cells start with | at line start or ||
            # First, normalize: replace || with newline |
            normalized = re.sub(r'\|\|', '\n|', part)
            cells = []
            for line in normalized.split('\n'):
                line = line.strip()
                if line.startswith('|'):
                    cells.append(line[1:].strip())
            
            # Expected order: Photo, Code, State, Incident/context, Notes, Status
            row_data = {
                "photo_file": f"File:{file_name}",
            }
            
            # Parse cells based on position, extracting both text and links
            # Column 0 = Photo (already handled via file_match)
            # Column 1 = Code
            if len(cells) >= 2:
                parsed = self._parse_cell(cells[1])
                row_data["code"] = parsed["text"]
                if parsed["wiki_links"]:
                    row_data["code_wiki_links"] = parsed["wiki_links"]
                if parsed["external_links"]:
                    row_data["code_external_links"] = parsed["external_links"]
            
            # Column 2 = State
            if len(cells) >= 3:
                parsed = self._parse_cell(cells[2])
                row_data["state"] = parsed["text"]
                if parsed["wiki_links"]:
                    row_data["state_wiki_links"] = parsed["wiki_links"]
                if parsed["external_links"]:
                    row_data["state_external_links"] = parsed["external_links"]
            
            # Column 3 = Incident/context
            if len(cells) >= 4:
                parsed = self._parse_cell(cells[3])
                row_data["incident_context"] = parsed["text"]
                if parsed["wiki_links"]:
                    row_data["incident_wiki_links"] = parsed["wiki_links"]
                if parsed["external_links"]:
                    row_data["incident_external_links"] = parsed["external_links"]
            
            # Column 4 = Notes
            if len(cells) >= 5:
                parsed = self._parse_cell(cells[4])
                row_data["notes"] = parsed["text"]
                if parsed["wiki_links"]:
                    row_data["notes_wiki_links"] = parsed["wiki_links"]
                if parsed["external_links"]:
                    row_data["notes_external_links"] = parsed["external_links"]
            
            # Column 5 = Status
            if len(cells) >= 6:
                parsed = self._parse_cell(cells[5])
                row_data["status"] = parsed["text"]
                if parsed["wiki_links"]:
                    row_data["status_wiki_links"] = parsed["wiki_links"]
                if parsed["external_links"]:
                    row_data["status_external_links"] = parsed["external_links"]
            
            results.append(row_data)

        return results
    
    def _parse_cell(self, cell):
        """Parse a wiki cell, extracting text and all links
        
        Returns dict with:
          - text: cleaned text content
          - wiki_links: list of {title, text, url} for internal wiki links
          - external_links: list of URLs for external links
        """
        if not cell:
            return {"text": "", "wiki_links": [], "external_links": []}
        
        wiki_links = []
        external_links = []
        
        # Extract wiki links [[Target|Text]] or [[Target]]
        for m in re.finditer(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', cell):
            target = m.group(1).strip()
            display = (m.group(2) or target).strip()
            
            # Skip file links
            if target.lower().startswith('file:'):
                continue
            
            # Build full URL for wiki link
            wiki_url = f"{self.root}/index.php/{target.replace(' ', '_')}"
            wiki_links.append({
                "title": target,
                "text": display,
                "url": wiki_url
            })
        
        # Extract external links [url text] or bare URLs
        # Clean tracking parameters from URLs
        for m in re.finditer(r'\[(https?://[^\s\]]+)(?:\s+([^\]]*))?\]', cell):
            cleaned = text.clean_url(m.group(1))
            external_links.append(cleaned)
        
        # Also find bare URLs not in brackets
        for m in re.finditer(r'(?<!\[)(https?://[^\s\]<>]+)', cell):
            cleaned = text.clean_url(m.group(1))
            if cleaned not in external_links:
                external_links.append(cleaned)
        
        # Build clean text
        cell_text = cell
        
        # Replace wiki links with their display text
        cell_text = re.sub(
            r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]',
            lambda m: m.group(2) if m.group(2) else m.group(1),
            cell_text
        )
        
        # Replace external links with their text or URL
        cell_text = re.sub(
            r'\[(https?://[^\s\]]+)(?:\s+([^\]]*))?\]',
            lambda m: m.group(2) if m.group(2) else m.group(1),
            cell_text
        )
        
        cell_text = cell_text.strip()
        
        return {
            "text": cell_text,
            "wiki_links": wiki_links,
            "external_links": external_links
        }


class IcelistArticleExtractor(IcelistExtractor):
    """Extractor for ICE List Wiki article pages with tables"""
    subcategory = "article"
    pattern = BASE_PATTERN + r"/index\.php/([^?#]+)"
    example = "https://wiki.icelist.is/index.php/Unidentified"

    def __init__(self, match):
        IcelistExtractor.__init__(self, match)
        self.page_title = match.group(1)

    def items(self):
        # Get page wikitext
        wikitext = self._get_page_wikitext(self.page_title)
        if not wikitext:
            self.log.error("Could not fetch page content for: %s", self.page_title)
            return

        # Parse table(s)
        table_rows = self._parse_wikitable(wikitext)
        if not table_rows:
            self.log.warning("No table data found on page: %s", self.page_title)
            return

        # Collect all file titles for batch API call
        file_titles = []
        for row in table_rows:
            if "photo_file" in row:
                file_titles.append(row["photo_file"])

        # Get image info for all files
        self.log.info("Found %d entries, fetching image info...", len(table_rows))
        image_info = self._get_image_info(file_titles)

        # Yield items
        for row in table_rows:
            # Get the file reference
            file_title = row.get("photo_file")

            if not file_title or file_title not in image_info:
                self.log.debug("No image info for: %s", file_title)
                continue

            info = image_info[file_title]
            if not info.get("url"):
                continue

            # Build metadata
            code = row.get("code", "unknown")
            state = row.get("state", "Unknown") or "Unknown"

            data = {
                # Standard fields
                "category": self.category,
                "subcategory": self.subcategory,
                "page": self.page_title,
                "page_url": f"{self.root}/index.php/{self.page_title}",
                
                # Table metadata - text values
                "code": code,
                "state": state,
                "incident_context": row.get("incident_context", ""),
                "notes": row.get("notes", ""),
                "status": row.get("status", ""),
                
                # Table metadata - wiki links (internal links to wiki pages)
                "state_wiki_links": row.get("state_wiki_links", []),
                "incident_wiki_links": row.get("incident_wiki_links", []),
                "notes_wiki_links": row.get("notes_wiki_links", []),
                
                # Table metadata - external links (URLs to external sites)
                "incident_external_links": row.get("incident_external_links", []),
                "notes_external_links": row.get("notes_external_links", []),
                
                # Image metadata from MediaWiki API
                "file_title": file_title,
                "file_url": info["url"],
                "file_page_url": f"{self.root}/index.php/{file_title.replace(' ', '_')}",
                "width": info["width"],
                "height": info["height"],
                "filesize": info["size"],
                "mime": info["mime"],
                "upload_timestamp": info["timestamp"],
                "uploader": info["user"],
                "sha1": info["sha1"],
                
                # For filename/URL
                "filename": code,
                "extension": info["url"].rpartition(".")[2].lower(),
            }

            yield Message.Directory, "", data
            yield Message.Url, info["url"], data


class IcelistFileExtractor(IcelistExtractor):
    """Extractor for individual ICE List Wiki file pages"""
    subcategory = "file"
    pattern = BASE_PATTERN + r"/index\.php/File:([^?#]+)"
    example = "https://wiki.icelist.is/index.php/File:NJ-0001.jpg"

    def __init__(self, match):
        IcelistExtractor.__init__(self, match)
        self.file_name = match.group(1)

    def items(self):
        file_title = f"File:{self.file_name}"
        image_info = self._get_image_info([file_title])

        if file_title not in image_info:
            self.log.error("Could not get info for: %s", file_title)
            return

        info = image_info[file_title]
        if not info.get("url"):
            return

        # Extract code from filename (e.g., NJ-0001.jpg -> NJ-0001)
        code = self.file_name.rpartition(".")[0]

        data = {
            "category": self.category,
            "subcategory": self.subcategory,
            "page": "File",
            "code": code,
            "state": "Unknown",
            "file_title": file_title,
            "url": info["url"],
            "width": info["width"],
            "height": info["height"],
            "filesize": info["size"],
            "mime": info["mime"],
            "timestamp": info["timestamp"],
            "uploader": info["user"],
            "sha1": info["sha1"],
            "filename": code,
            "extension": info["url"].rpartition(".")[2].lower(),
        }

        yield Message.Directory, "", data
        yield Message.Url, info["url"], data
