"""Generic, configuration-driven scraper for list/catalogue pages with pagination.

No code needed for a new website: describe the item container, the fields
(CSS selector + optional attribute / regex / value map) and the "next page"
link in the project file. Optional *detail pages* can be followed for extra
fields.

Example (product catalogue)::

    - type: html_list
      start_url: https://books.toscrape.com/catalogue/page-1.html
      item_selector: article.product_pod
      fields:
        title:        {selector: "h3 a", attr: title}
        price:        {selector: "p.price_color"}
        rating:       {selector: "p.star-rating", attr: class, regex: "star-rating (\\w+)",
                       map: {One: 1, Two: 2, Three: 3, Four: 4, Five: 5}}
        availability: {selector: "p.instock.availability"}
        product_url:  {selector: "h3 a", attr: href, absolute: true}
      next_page_selector: "li.next a"
      max_pages: 3
      detail:
        url_field: product_url
        fields:
          upc:         {selector: "table.table-striped tr:nth-of-type(1) td"}
          description: {selector: "#product_description ~ p"}
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..models import RawRecord
from .base import BaseSource, SourceError, register

log = logging.getLogger(__name__)


def extract_field(node: Tag, spec: dict[str, Any] | str, base_url: str) -> Any:
    """Apply one field spec to an element. Returns None when nothing matched."""
    if isinstance(spec, str):
        spec = {"selector": spec}
    selector = spec.get("selector")
    target: Tag | None = node
    if selector:
        target = node.select_one(selector)
    if target is None:
        return spec.get("default")
    attr = spec.get("attr")
    if attr:
        raw = target.get(attr)
        if isinstance(raw, list):
            raw = " ".join(raw)
        value: str | None = raw if raw is not None else None
    else:
        value = target.get_text(" ", strip=True)
    if value is None or value == "":
        return spec.get("default")
    if spec.get("absolute") and isinstance(value, str):
        value = urljoin(base_url, value)
    regex = spec.get("regex")
    if regex and isinstance(value, str):
        m = re.search(regex, value)
        if not m:
            return spec.get("default")
        value = m.group(1) if m.groups() else m.group(0)
    mapping = spec.get("map")
    if mapping and isinstance(value, str):
        value = mapping.get(value, mapping.get(value.strip(), spec.get("default", value)))
    if spec.get("strip", True) and isinstance(value, str):
        value = value.strip()
    return value


def parse_list_page(html: str, page_url: str, item_selector: str, fields: dict[str, Any]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for node in soup.select(item_selector):
        values = {name: extract_field(node, spec, page_url) for name, spec in fields.items()}
        items.append(values)
    return items


def find_next_page(html: str, page_url: str, selector: str | None) -> str | None:
    if not selector:
        return None
    soup = BeautifulSoup(html, "lxml")
    link = soup.select_one(selector)
    if link is None:
        return None
    href = link.get("href") if link.name == "a" else (link.select_one("a") or {}).get("href")
    return urljoin(page_url, href) if href else None


@register
class HtmlListSource(BaseSource):
    type = "html_list"
    description = "Any listing/catalogue website via CSS selectors and pagination (no code). Optional detail pages."
    produces = "products"

    def extract(self, limit: int | None = None) -> Iterator[RawRecord]:
        url: str | None = self.require_opt("start_url")
        item_selector: str = self.require_opt("item_selector")
        fields: dict[str, Any] = self.require_opt("fields")
        next_selector = self.opt("next_page_selector")
        max_pages = int(self.opt("max_pages", 10))
        delay = float(self.opt("delay_seconds", 1.0))
        detail = self.opt("detail") or {}
        url_field = self.opt("url_field") or (detail.get("url_field") if detail else None)
        constants: dict[str, Any] = self.opt("constants") or {}

        seen_pages: set[str] = set()
        count = 0
        page_no = 0
        while url and page_no < max_pages and url not in seen_pages:
            seen_pages.add(url)
            page_no += 1
            result = self.http.fetch(url, min_delay=delay)
            if not result.ok:
                self.warn(f"page {page_no} ({url}) could not be fetched: {result.error or result.status}")
                break
            items = parse_list_page(result.text, result.final_url or url, item_selector, fields)
            log.info("html_list: page %d -> %d items (%s)", page_no, len(items), url)
            if not items:
                self.warn(f"no items matched '{item_selector}' on {url}")
            for values in items:
                values.update(constants)
                item_url = values.get(url_field) if url_field else None
                if detail and item_url:
                    values.update(self._fetch_detail(item_url, detail, delay))
                yield RawRecord(
                    source=self.label,
                    source_id=str(item_url or f"{url}#{count + 1}"),
                    source_url=str(item_url or url),
                    values=values,
                    raw={"page": page_no, "page_url": url},
                )
                count += 1
                if limit and count >= limit:
                    return
            url = find_next_page(result.text, result.final_url or url, next_selector)

    def _fetch_detail(self, item_url: str, detail: dict[str, Any], delay: float) -> dict[str, Any]:
        result = self.http.fetch(item_url, min_delay=delay)
        if not result.ok:
            return {"_detail_error": result.error or f"HTTP {result.status}"}
        soup = BeautifulSoup(result.text, "lxml")
        root = soup.select_one(detail.get("root_selector")) if detail.get("root_selector") else soup
        if root is None:
            return {}
        return {name: extract_field(root, spec, item_url) for name, spec in (detail.get("fields") or {}).items()}


def validate_spec(options: dict[str, Any]) -> None:
    for key in ("start_url", "item_selector", "fields"):
        if not options.get(key):
            raise SourceError(f"html_list needs '{key}'")
