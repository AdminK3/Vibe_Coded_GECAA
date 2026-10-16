# SPDX-License-Identifier: Apache-2.0
"""Client for the federal legal information system NeuRIS (rechtsinformationen.bund.de).

The API is in a public test phase, so endpoints may change. The request and
response shapes used here follow the public test-phase API as used by
existing open-source clients:

  GET {base}/v1/legislation?searchTerm=...&size=...   -> {"member": [{"item": {...}}]}
  GET {base}/v1/legislation/eli/bund/...               -> document, HTML or XML via Accept header

Each search result item carries "@id" (work level), "abbreviation", "name" and
"workExample" (the current expression) with "encoding" entries that point to
the HTML or XML rendition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from ..models import Provision
from .store import ChangeEvent, SnapshotStore

DEFAULT_BASE_URL = "https://testphase.rechtsinformationen.bund.de"


@dataclass
class LegislationHit:
    work_id: str
    expression_id: str | None
    name: str
    abbreviation: str | None
    html_url: str | None


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "section", "article", "dt", "dd", "tr"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


_CITATION = re.compile(r"§+\s*(?P<para>\d+[a-z]?)(?:\s*Abs\.\s*(?P<abs>\d+))?")


def parse_citation(citation: str) -> tuple[str | None, str | None]:
    """'§ 55 Abs. 2 WHG' -> ('55', '2')."""
    m = _CITATION.search(citation)
    if not m:
        return None, None
    return m.group("para"), m.group("abs")


def extract_paragraph(text: str, para: str, absatz: str | None = None) -> str | None:
    """Cut one § (and optionally one Absatz) out of a law's plain text.

    Heuristic: a paragraph starts at a line beginning with '§ <para>' and ends
    at the next line beginning with '§ '. An Absatz starts at '(<n>)'.
    """
    lines = text.splitlines()
    start = None
    head = re.compile(rf"^§\s*{re.escape(para)}(?![0-9a-z])")
    for i, line in enumerate(lines):
        if head.match(line):
            start = i
            break
    if start is None:
        return None
    body = [lines[start]]
    for line in lines[start + 1:]:
        if re.match(r"^§\s*\d", line):
            break
        body.append(line)
    para_text = "\n".join(body)
    if not absatz:
        return para_text
    m = re.search(rf"\({re.escape(absatz)}\)\s*(.*?)(?=\n?\(\d+\)|\Z)", para_text, re.S)
    return m.group(0).strip() if m else None


class NeurisClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, client: httpx.Client | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout, headers={"User-Agent": "gecaa/0.1 (+open-source)"})

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def search(self, term: str, size: int = 10) -> list[LegislationHit]:
        resp = self.client.get(self._url("/v1/legislation"), params={"searchTerm": term, "size": min(size, 100)})
        resp.raise_for_status()
        hits = []
        for member in resp.json().get("member", []):
            item = member.get("item", member)
            example = item.get("workExample") or {}
            html_url = None
            for enc in example.get("encoding", []) or []:
                if enc.get("encodingFormat") == "text/html" and enc.get("contentUrl"):
                    html_url = enc["contentUrl"]
                    break
            hits.append(LegislationHit(
                work_id=item.get("@id", ""),
                expression_id=example.get("@id"),
                name=item.get("name", ""),
                abbreviation=item.get("abbreviation"),
                html_url=html_url,
            ))
        return hits

    def fetch_html(self, path: str) -> str:
        resp = self.client.get(self._url(path), headers={"Accept": "text/html"})
        resp.raise_for_status()
        return resp.text

    def find_by_eli(self, eli: str, term: str) -> LegislationHit | None:
        for hit in self.search(term, size=20):
            if eli in hit.work_id or (hit.expression_id and eli in hit.expression_id):
                return hit
        return None

    def refresh(self, provision: Provision, store: SnapshotStore, search_term: str) -> ChangeEvent | None:
        """Fetch the current text of a federal provision and snapshot it.

        Returns a ChangeEvent when the text is new or changed, None when unchanged.
        """
        if not provision.eli:
            raise ValueError(f"{provision.id} has no ELI; only federal law can be fetched from NeuRIS")
        hit = self.find_by_eli(provision.eli, search_term)
        if hit is None:
            raise LookupError(f"{provision.eli} not found in NeuRIS for search term {search_term!r}")
        doc_path = hit.html_url or (hit.expression_id or hit.work_id)
        text = html_to_text(self.fetch_html(doc_path))
        para, absatz = parse_citation(provision.citation)
        excerpt = extract_paragraph(text, para, absatz) if para else None
        if not excerpt:
            raise LookupError(f"could not locate {provision.citation} in {doc_path}")
        span = f"§ {para}" + (f" Abs. {absatz}" if absatz else "")
        return store.save(provision.id, excerpt, self._url(doc_path), "neuris-api", text_span=span)
