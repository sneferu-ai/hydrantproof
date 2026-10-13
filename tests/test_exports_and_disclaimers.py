"""Dispatch CSV, printable forms, and the mechanical forbidden-phrase test."""
from __future__ import annotations

import csv
import io
import pathlib
import re
import zlib
from datetime import timedelta

import pytest
from django.utils import timezone

from ledger import constants as C
from ledger.csvio import neutralize_cell, write_csv
from ledger.models import ExportAudit

pytestmark = pytest.mark.django_db

REPO = pathlib.Path(__file__).resolve().parent.parent


_PDF_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _decode_pdf_stream(raw: bytes) -> bytes:
    """Decode one PDF content stream.

    ReportLab's default filter chain is ``[ /ASCII85Decode /FlateDecode ]``;
    a plain Flate or uncompressed stream is accepted too.  Returns the raw
    bytes when nothing decodes (conservative — never raises).
    """
    import base64

    body = raw.strip()
    candidates = []
    if body.endswith(b"~>"):
        try:
            candidates.append(base64.a85decode(body[:-2], ignorechars=b" \t\r\n\v\f"))
        except ValueError:
            pass
    candidates.append(body)
    for candidate in candidates:
        try:
            return zlib.decompress(candidate)
        except zlib.error:
            pass
    return candidates[-1]


def pdf_text(data: bytes) -> str:
    """Extract the drawn text runs from a ReportLab PDF (stdlib only).

    ReportLab writes each page's content as a compressed stream and each drawn
    string as a literal-string ``(...) Tj`` operand.  Literal strings escape
    ``(``, ``)``, ``\\`` and non-ASCII bytes as octal escapes in the font's
    WinAnsi encoding; unescape, then decode as cp1252.  Conservative on
    purpose: it reports only what is provably in the bytes.
    """
    parts: list[str] = []
    for stream_match in re.finditer(rb"(?<!end)stream\r?\n", data):
        start = stream_match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = _decode_pdf_stream(data[start:end])
        for text_match in re.finditer(rb"\(((?:[^()\\]|\\.)*)\)\s*Tj", raw):
            body = re.sub(
                rb"\\([nrtbf()\\])",
                lambda m: _PDF_ESCAPES.get(m.group(1), m.group(1)),
                text_match.group(1),
            )
            body = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), body)
            parts.append(body.decode("cp1252", "replace"))
    return "\n".join(parts)


def seed(api, asset_tag="D-1", **extra):
    payload = {"asset_tag": asset_tag, "lat": 38.0, "lon": -122.0}
    payload.update(extra)
    response = api.post("/api/v1/hydrants/", payload, format="json")
    assert response.status_code == 201, response.data
    return response.data


def add_test(api, hydrant_id, **overrides):
    payload = {
        "tested_on": str(timezone.localdate()),
        "static_psi": "80.00",
        "residual_psi": "40.00",
        "outlets": [{"outlet_diameter_in": "2.50", "outlet_coefficient": "0.90", "pitot_psi": "28.00"}],
    }
    payload.update(overrides)
    response = api.post(f"/api/v1/hydrants/{hydrant_id}/tests/", payload, format="json")
    assert response.status_code == 201, response.data
    return response.data


class TestNeutralization:
    @pytest.mark.parametrize("hostile", ["=cmd|' /C calc'!A0", "+1-800", "-2", "@SUM(A1)", "\tx", "\rx"])
    def test_text_cells_are_prefixed(self, hostile):
        assert neutralize_cell(hostile).startswith("'")

    @pytest.mark.parametrize("safe", ["Main St", "", "hydrant-7", "38.0000000"])
    def test_ordinary_text_is_untouched(self, safe):
        assert neutralize_cell(safe) == safe

    def test_csv_writer_uses_crlf(self):
        body = write_csv(["a", "b"], [{"a": "1", "b": "2"}])
        assert body == "a,b\r\n1,2\r\n"


class TestDispatchCsv:
    def test_every_row_carries_verify_literal_and_disclaimer(self, api):
        seed(api, "D-VERIFY")
        response = api.get("/api/v1/exports/dispatch/")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))
        assert rows, "dispatch export returned no rows"
        for row in rows:
            assert row["verify_in_field"] == C.VERIFY_IN_FIELD
            assert row["disclaimer"] == C.DISCLAIMER

    def test_retired_rows_are_excluded(self, api):
        retired = seed(api, "D-RETIRED")
        api.post(
            f"/api/v1/hydrants/{retired['id']}/status-events/",
            {"to_status": "RETIRED", "reason": "Removed with main replacement"}, format="json",
        )
        rows = list(csv.DictReader(io.StringIO(api.get("/api/v1/exports/dispatch/").content.decode())))
        assert "D-RETIRED" not in {row["asset_tag"] for row in rows}

    def test_expired_oos_carries_escalation(self, api):
        hydrant = seed(
            api, "D-EXPIRED",
            initial_status="OUT_OF_SERVICE",
            expected_return_on=str(timezone.localdate() - timedelta(days=5)),
            initial_status_reason="Main break",
        )
        rows = list(csv.DictReader(io.StringIO(api.get("/api/v1/exports/dispatch/").content.decode())))
        row = next(r for r in rows if r["asset_tag"] == "D-EXPIRED")
        assert row["escalation"] == C.ESCALATION_EXPIRED_OOS
        assert row["oos_expired"] == "true"

    def test_hostile_location_is_neutralized_but_negative_longitude_is_not(self, api):
        seed(api, "D-HOSTILE", location_description="=cmd|calc", lon=-122.4)
        body = api.get("/api/v1/exports/dispatch/").content.decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(body)))
        row = next(r for r in rows if r["asset_tag"] == "D-HOSTILE")
        assert row["location_description"].startswith("'=")
        assert row["longitude"] == "-122.4000000"  # typed numeric column, never prefixed

    def test_export_writes_an_audit_row(self, api):
        seed(api, "D-AUDIT")
        before = ExportAudit.objects.count()
        api.get("/api/v1/exports/dispatch/")
        assert ExportAudit.objects.count() == before + 1

    def test_unqualified_current_test_blanks_rated_and_reports_the_warning(self, api):
        hydrant = seed(api, "D-UNQUAL")
        add_test(api, hydrant["id"], static_psi="18.00", residual_psi="9.00")
        rows = list(csv.DictReader(io.StringIO(api.get("/api/v1/exports/dispatch/").content.decode())))
        row = next(r for r in rows if r["asset_tag"] == "D-UNQUAL")
        assert row["latest_rated_gpm_20"] == ""
        assert row["nfpa_class"] == ""
        assert C.WARNING_STATIC_BELOW_TARGET in row["test_warnings"]


class TestPrintableForms:
    def test_flow_test_field_sheet_is_a_pdf_with_the_notes(self, api):
        response = api.get("/api/v1/exports/forms/flow-test/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")

    def test_inventory_sheet_is_a_pdf(self, api):
        response = api.get("/api/v1/exports/forms/inventory/")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")

    def test_forms_write_audit_rows(self, api):
        before = ExportAudit.objects.count()
        api.get("/api/v1/exports/forms/flow-test/")
        api.get("/api/v1/exports/forms/inventory/")
        assert ExportAudit.objects.count() == before + 2


class TestForbiddenPhraseDiscipline:
    """Strip every occurrence of the canonical sentence, then grep the rest."""

    def test_readme_is_clean(self):
        text = (REPO / "README.md").read_text()
        stripped = text.replace(C.DISCLAIMER, "")
        for phrase in C.FORBIDDEN_PHRASES:
            assert phrase.lower() not in stripped.lower(), f"README.md contains {phrase!r}"

    def test_fixed_string_constants_are_clean(self):
        """Every fixed string the renderer emits, scrubbed then grepped.

        The checker's own vocabulary (FORBIDDEN_PHRASES) is not an artifact
        string; the module's other uppercase string constants are.
        """
        values = [
            value for name, value in vars(C).items()
            if name.isupper() and name != "FORBIDDEN_PHRASES"
            and isinstance(value, str)
        ]
        assert len(values) > 8, "expected the module's fixed strings to be collected"
        for value in values:
            stripped = value.replace(C.DISCLAIMER, "")
            for phrase in C.FORBIDDEN_PHRASES:
                assert phrase.lower() not in stripped.lower(), f"fixed string contains {phrase!r}"

    def test_templates_are_clean(self):
        for path in (REPO / "ledger" / "templates").rglob("*.html"):
            stripped = path.read_text().replace(C.DISCLAIMER, "")
            for phrase in C.FORBIDDEN_PHRASES:
                assert phrase.lower() not in stripped.lower(), f"{path.name} contains {phrase!r}"

    def test_dispatch_csv_cells_are_clean(self, api):
        seed(api, "D-SCRUB")
        body = api.get("/api/v1/exports/dispatch/").content.decode("utf-8")
        stripped = body.replace(C.DISCLAIMER, "")
        for phrase in C.FORBIDDEN_PHRASES:
            assert phrase.lower() not in stripped.lower()

    def test_canonical_sentence_is_contiguous_in_the_readme(self):
        """The mechanical strip only works while the sentence is verbatim."""
        text = (REPO / "README.md").read_text()
        assert C.DISCLAIMER in text


class TestPdfTextScrub:
    """§3 scopes the scrub to PDF text — each page's footer repeats the
    canonical sentence — and the strip only works while the sentence is
    verbatim.  The footers wrap it across 6pt lines, so the extraction
    normalizes whitespace before stripping; a sliced or truncated footer
    leaves a fragment that survives the strip and trips the grep."""

    def _assert_pdf_clean(self, data: bytes):
        assert data.startswith(b"%PDF")
        text = re.sub(r"\s+", " ", pdf_text(data))
        assert C.DISCLAIMER in text, "the canonical disclaimer is not verbatim in the PDF text"
        assert C.PROVISIONAL_BANNER in text, "the provisional banner is not verbatim in the PDF text"
        stripped = text.replace(C.DISCLAIMER, "")
        for phrase in C.FORBIDDEN_PHRASES:
            assert phrase.lower() not in stripped.lower(), f"PDF text contains {phrase!r}"

    def test_printable_forms_pdf_text_is_clean(self, api):
        for path in ("/api/v1/exports/forms/flow-test/", "/api/v1/exports/forms/inventory/"):
            response = api.get(path)
            assert response.status_code == 200
            self._assert_pdf_clean(response.content)

    def test_pack_pdf_text_is_clean(self):
        from django.conf import settings

        from test_packs import completed_run, render_sync

        run = completed_run()
        pack = render_sync(run)
        assert pack.status == C.PACK_STATUS_COMPLETE
        self._assert_pdf_clean((settings.MEDIA_ROOT / pack.pdf_path).read_bytes())


class TestProvisionalBanner:
    def test_constants_carry_the_banner(self):
        assert "provisional constants" in C.PROVISIONAL_BANNER.lower()
        assert C.PROVISIONAL_BANNER in (REPO / "README.md").read_text()

    def test_capacity_colors_cover_every_class(self):
        for name in C.CAPACITY_CLASSES:
            assert name in C.CAPACITY_CLASS_COLORS
        assert None in C.CAPACITY_CLASS_COLORS
