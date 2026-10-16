# SPDX-License-Identifier: Apache-2.0
from datetime import datetime, timezone

import httpx
import pytest

from gecaa.knowledge import SnapshotStore
from gecaa.knowledge.neuris import NeurisClient, extract_paragraph, html_to_text, parse_citation

# Shaped like the NeuRIS test-phase API. Invented content for testing only.
SEARCH = {
    "member": [{
        "item": {
            "@id": "/v1/legislation/eli/bund/bgbl-1/2009/s2585/regelungstext-1",
            "name": "Gesetz zur Ordnung des Wasserhaushalts",
            "abbreviation": "WHG",
            "workExample": {
                "@id": "/v1/legislation/eli/bund/bgbl-1/2009/s2585/2025-01-01/1/deu/regelungstext-1",
                "encoding": [{"encodingFormat": "text/html",
                              "contentUrl": "/v1/legislation/eli/bund/bgbl-1/2009/s2585/2025-01-01/1/deu/regelungstext-1.html"}],
            },
        }
    }]
}
HTML = """<html><body>
<h3>§ 54 Begriffsbestimmungen</h3><p>(1) Test text for paragraph fifty-four.</p>
<h3>§ 55 Grundsätze der Abwasserbeseitigung</h3>
<p>(1) First sentence of the test paragraph.</p>
<p>(2) Second sentence: rainwater should be handled locally (test).</p>
<p>(3) Third sentence.</p>
<h3>§ 56 Pflicht zur Abwasserbeseitigung</h3><p>(1) Next paragraph.</p>
</body></html>"""


def mock_client(html=HTML):
    def handler(request: httpx.Request):
        if request.url.path == "/v1/legislation":
            assert request.url.params["searchTerm"]
            return httpx.Response(200, json=SEARCH)
        if request.url.path.endswith(".html"):
            return httpx.Response(200, text=html)
        return httpx.Response(404)
    return NeurisClient("https://neuris.test", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_parse_citation():
    assert parse_citation("§ 55 Abs. 2 WHG") == ("55", "2")
    assert parse_citation("§ 8 WHG") == ("8", None)
    assert parse_citation("§§ 51, 52 WHG") == ("51", None)


def test_extract_paragraph_and_absatz():
    text = html_to_text(HTML)
    para = extract_paragraph(text, "55")
    assert para.startswith("§ 55") and "§ 56" not in para and "(3)" in para
    absatz = extract_paragraph(text, "55", "2")
    assert absatz.startswith("(2)") and "rainwater" in absatz and "(3)" not in absatz
    assert extract_paragraph(text, "5") is None


def test_snapshot_change_detection(tmp_path):
    store = SnapshotStore(tmp_path)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = store.save("p1", "Text A", "https://x", "test", now=t0)
    assert first and first.is_new
    assert store.save("p1", "Text   A", "https://x", "test", now=t0.replace(day=2)) is None  # whitespace only
    changed = store.save("p1", "Text B", "https://x", "test", now=t0.replace(day=3))
    assert changed and not changed.is_new and "review" in changed.describe()
    assert len(store.history("p1")) == 2


def test_refresh_from_neuris(tmp_path, gecaa_app):
    store = SnapshotStore(tmp_path)
    prov = gecaa_app.provisions["whg_55_2"]
    event = mock_client().refresh(prov, store, "Wasserhaushaltsgesetz")
    assert event and event.is_new
    snap = store.latest("whg_55_2")
    assert snap["text"].startswith("(2)") and snap["text_span"] == "§ 55 Abs. 2"
    assert snap["retrieved_from"].endswith("regelungstext-1.html")
    # the provision now carries text and provenance
    applied = store.apply({"whg_55_2": prov})["whg_55_2"]
    assert applied.text and applied.provenance.content_sha256 == snap["content_sha256"]
    # unchanged on second run
    assert mock_client().refresh(prov, store, "Wasserhaushaltsgesetz") is None


def test_refresh_reports_missing_paragraph(tmp_path, gecaa_app):
    with pytest.raises(LookupError):
        mock_client("<p>nothing here</p>").refresh(gecaa_app.provisions["whg_8"], SnapshotStore(tmp_path), "WHG")
