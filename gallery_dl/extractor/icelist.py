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


class IcelistCategoryExtractor(IcelistExtractor):
    """Extractor for ICE List Wiki category pages (e.g., Category:Agents)
    
    Paginates through all category members and visits each page.
    For agent pages, extracts structured metadata from the Agent page template.
    """
    subcategory = "category"
    pattern = BASE_PATTERN + r"/index\.php\?title=Category:([^&#]+)"
    example = "https://wiki.icelist.is/index.php?title=Category:Agents"
    
    # Agent pages may not have images, so we need different defaults
    filename_fmt = "{agent_name_slug}.{extension}"
    directory_fmt = ("{category}", "agents", "{state}")
    archive_fmt = "icelist_agent_{pageid}"

    def __init__(self, match):
        IcelistExtractor.__init__(self, match)
        self.category_name = match.group(1)

    def items(self):
        category_title = f"Category:{self.category_name}"
        self.log.info("Fetching members of %s", category_title)
        
        # Paginate through category members
        members = list(self._get_category_members(category_title))
        self.log.info("Found %d pages in category", len(members))
        
        # Process each member page
        for member in members:
            page_title = member["title"]
            pageid = member["pageid"]
            
            # Get page content
            wikitext = self._get_page_wikitext(page_title)
            if not wikitext:
                self.log.debug("Could not fetch: %s", page_title)
                continue
            
            # Parse agent page template
            agent_data = self._parse_agent_page(wikitext, page_title, pageid)
            if not agent_data:
                self.log.debug("No agent template found: %s", page_title)
                continue
            
            # Check if there's an actual image (not nopfp.png)
            image_file = agent_data.get("image", "")
            has_image = image_file and image_file.lower() != "nopfp.png"
            
            if has_image:
                # Get image info and yield with URL
                file_title = f"File:{image_file}"
                image_info = self._get_image_info([file_title])
                
                if file_title in image_info:
                    info = image_info[file_title]
                    if info.get("url"):
                        agent_data["file_url"] = info["url"]
                        agent_data["file_title"] = file_title
                        agent_data["width"] = info["width"]
                        agent_data["height"] = info["height"]
                        agent_data["filesize"] = info["size"]
                        agent_data["mime"] = info["mime"]
                        agent_data["upload_timestamp"] = info["timestamp"]
                        agent_data["uploader"] = info["user"]
                        agent_data["sha1"] = info["sha1"]
                        agent_data["extension"] = info["url"].rpartition(".")[2].lower()
                        
                        yield Message.Directory, "", agent_data
                        yield Message.Url, info["url"], agent_data
                        continue
            
            # No image or image fetch failed - yield metadata only
            # Use a dummy URL that will create a .json sidecar via metadata PP
            agent_data["extension"] = "json"
            yield Message.Directory, "", agent_data
            # Yield a special marker for metadata-only entries
            yield Message.Url, "text:" + json.dumps(agent_data, indent=2), agent_data

    def _get_category_members(self, category_title):
        """Paginate through all members of a category"""
        continue_token = None
        
        while True:
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": category_title,
                "cmlimit": "500",
                "cmtype": "page",  # Only pages, not subcategories
            }
            
            if continue_token:
                params["cmcontinue"] = continue_token
            
            data = self._api_call(**params)
            
            if "query" not in data or "categorymembers" not in data["query"]:
                break
            
            members = data["query"]["categorymembers"]
            for member in members:
                yield member
            
            # Check for more pages
            if "continue" in data and "cmcontinue" in data["continue"]:
                continue_token = data["continue"]["cmcontinue"]
                self.log.debug("Fetching next page of category members...")
            else:
                break

    def _parse_agent_page(self, wikitext, page_title, pageid):
        """Parse an agent page and extract structured data
        
        Extracts:
        - Template fields (name, agency, role, field_office, state, status, image, verification, summary)
        - All wiki links
        - All external links (LinkedIn, social media, etc.)
        - Section content (Evidence and Sources, Notes)
        """
        # Find the Agent page template
        template_match = re.search(
            r'\{\{Agent page\s*\n(.*?)\}\}',
            wikitext,
            re.DOTALL | re.IGNORECASE
        )
        
        if not template_match:
            return None
        
        template_content = template_match.group(1)
        
        # Parse template fields
        fields = {}
        for m in re.finditer(r'\|(\w+)\s*=\s*([^\n|]*?)(?=\n\||$)', template_content, re.DOTALL):
            key = m.group(1).strip().lower()
            value = m.group(2).strip()
            fields[key] = value
        
        # Build agent name slug for filename
        agent_name = fields.get("name", page_title)
        agent_name_slug = re.sub(r'[^\w\s-]', '', agent_name)
        agent_name_slug = re.sub(r'\s+', '_', agent_name_slug)
        
        # Extract all wiki links from the entire page
        wiki_links = []
        for m in re.finditer(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', wikitext):
            target = m.group(1).strip()
            display = (m.group(2) or target).strip()
            
            # Skip file/image links and categories
            if target.lower().startswith(('file:', 'image:', 'category:')):
                continue
            
            wiki_url = f"{self.root}/index.php/{target.replace(' ', '_')}"
            wiki_links.append({
                "title": target,
                "text": display,
                "url": wiki_url
            })
        
        # Extract all external URLs (clean tracking params)
        external_links = []
        # Bracketed external links [url text]
        for m in re.finditer(r'\[(https?://[^\s\]]+)(?:\s+([^\]]*))?\]', wikitext):
            cleaned = text.clean_url(m.group(1))
            if cleaned not in external_links:
                external_links.append(cleaned)
        
        # Bare URLs
        for m in re.finditer(r'(?<!\[)(https?://[^\s\]<>\)]+)', wikitext):
            cleaned = text.clean_url(m.group(1))
            if cleaned not in external_links:
                external_links.append(cleaned)
        
        # Extract specific sections
        evidence_section = self._extract_section(wikitext, "Evidence and Sources")
        notes_section = self._extract_section(wikitext, "Notes")
        
        # Extract categories
        categories = re.findall(r'\[\[Category:([^\]]+)\]\]', wikitext)
        
        # Parse state from template (may have wiki link syntax)
        state_raw = fields.get("state", "Unknown")
        state = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', state_raw)
        
        # Parse agency from template
        agency_raw = fields.get("agency", "")
        agency = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', agency_raw)
        
        # Parse role from template
        role_raw = fields.get("role", "")
        role = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', role_raw)
        
        # Parse field office from template
        field_office_raw = fields.get("field_office", "")
        field_office = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', field_office_raw)
        
        # Parse verification from template
        verification_raw = fields.get("verification", "")
        verification = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', verification_raw)
        
        return {
            # Standard fields
            "category": self.category,
            "subcategory": "agent",
            
            # Page metadata
            "pageid": pageid,
            "page_title": page_title,
            "page_url": f"{self.root}/index.php/{page_title.replace(' ', '_')}",
            
            # Agent template fields
            "agent_name": agent_name,
            "agent_name_slug": agent_name_slug,
            "agency": agency,
            "role": role,
            "field_office": field_office,
            "state": state or "Unknown",
            "status": fields.get("status", ""),
            "image": fields.get("image", ""),
            "verification": verification,
            "summary": fields.get("summary", ""),
            
            # Raw template fields (with wiki syntax preserved)
            "agency_raw": agency_raw,
            "role_raw": role_raw,
            "field_office_raw": field_office_raw,
            "state_raw": state_raw,
            "verification_raw": verification_raw,
            
            # Links
            "wiki_links": wiki_links,
            "external_links": external_links,
            
            # Sections
            "evidence_sources": evidence_section,
            "notes": notes_section,
            
            # Categories
            "page_categories": categories,
            
            # Filename
            "filename": agent_name_slug,
        }

    def _extract_section(self, wikitext, section_name):
        """Extract content of a named section from wikitext"""
        # Match section header (== Section Name ==) and content until next section or end
        pattern = rf'==\s*{re.escape(section_name)}\s*==\s*\n(.*?)(?=\n==|\[\[Category:|$)'
        match = re.search(pattern, wikitext, re.DOTALL | re.IGNORECASE)
        
        if match:
            content = match.group(1).strip()
            # Remove template calls like {{IncidentsForAgent}}
            content = re.sub(r'\{\{[^}]+\}\}', '', content)
            # Clean up italic markers
            content = content.replace("''", "")
            return content.strip()
        
        return ""


class IcelistAgentExtractor(IcelistExtractor):
    """Extractor for individual ICE List Wiki agent pages"""
    subcategory = "agent"
    # Match agent pages that are NOT File:, Category:, or Special:
    pattern = BASE_PATTERN + r"/index\.php/(?!File:|Category:|Special:)([^?#]+)"
    example = "https://wiki.icelist.is/index.php/A.,_John"
    
    filename_fmt = "{agent_name_slug}.{extension}"
    directory_fmt = ("{category}", "agents", "{state}")
    archive_fmt = "icelist_agent_{pageid}"

    def __init__(self, match):
        IcelistExtractor.__init__(self, match)
        self.page_title = match.group(1).replace("_", " ")

    def items(self):
        # Get page content
        wikitext = self._get_page_wikitext(self.page_title)
        if not wikitext:
            self.log.error("Could not fetch page: %s", self.page_title)
            return
        
        # Get page ID
        page_info = self._api_call(
            action="query",
            titles=self.page_title,
            prop="info"
        )
        
        pageid = 0
        if "query" in page_info and "pages" in page_info["query"]:
            for pid, pdata in page_info["query"]["pages"].items():
                if pid != "-1":
                    pageid = int(pid)
                    break
        
        # Use category extractor's parsing logic
        cat_extractor = IcelistCategoryExtractor.__new__(IcelistCategoryExtractor)
        cat_extractor.root = self.root
        cat_extractor.category = self.category
        
        agent_data = cat_extractor._parse_agent_page(wikitext, self.page_title, pageid)
        
        if not agent_data:
            # Not an agent page - might be a table page like Unidentified
            # Fall through to article extractor behavior
            self.log.debug("No agent template, checking for table...")
            return
        
        # Check for image
        image_file = agent_data.get("image", "")
        has_image = image_file and image_file.lower() != "nopfp.png"
        
        if has_image:
            file_title = f"File:{image_file}"
            image_info = self._get_image_info([file_title])
            
            if file_title in image_info:
                info = image_info[file_title]
                if info.get("url"):
                    agent_data["file_url"] = info["url"]
                    agent_data["file_title"] = file_title
                    agent_data["width"] = info["width"]
                    agent_data["height"] = info["height"]
                    agent_data["filesize"] = info["size"]
                    agent_data["mime"] = info["mime"]
                    agent_data["upload_timestamp"] = info["timestamp"]
                    agent_data["uploader"] = info["user"]
                    agent_data["sha1"] = info["sha1"]
                    agent_data["extension"] = info["url"].rpartition(".")[2].lower()
                    
                    yield Message.Directory, "", agent_data
                    yield Message.Url, info["url"], agent_data
                    return
        
        # No image - yield metadata only
        agent_data["extension"] = "json"
        yield Message.Directory, "", agent_data
        yield Message.Url, "text:" + json.dumps(agent_data, indent=2), agent_data
