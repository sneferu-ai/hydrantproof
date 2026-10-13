"""Bulk exports and printable forms.

Every bulk export writes an ``ExportAudit`` row.  Printable forms are original
district layouts drawn with ReportLab base-14 Helvetica only — no system
graphics dependency, fully offline; "NFPA" appears only in the disclaimer.
"""
from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from drf_spectacular.utils import OpenApiTypes, extend_schema

from . import constants as C
from .csvio import dispatch_csv
from .models import ExportAudit, Hydrant
from .services import derive_hydrants, params_from_settings


def audit_export(*, path: str, kind: str, row_count: int, user) -> None:
    ExportAudit.objects.create(
        export_path=path,
        kind=kind,
        row_count=row_count,
        user=user if getattr(user, "is_authenticated", False) else None,
    )


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class DispatchExportView(APIView):
    """The dispatch CSV: fixed column order, verify literal + disclaimer per row.
    Viewer-readable (§4: viewers read everything, exports included)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        hydrants = list(Hydrant.objects.all().order_by("asset_tag"))
        derivations = derive_hydrants(hydrants, params_from_settings())
        data_as_of = timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        body = dispatch_csv(hydrants, derivations, data_as_of=data_as_of)
        audit_export(path="/api/v1/exports/dispatch/", kind="dispatch", row_count=len(hydrants),
                     user=request.user)
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="dispatch.csv"'
        return response


# ---------------------------------------------------------------------------
# Printable forms (blank field sheet + inventory sheet)
# ---------------------------------------------------------------------------
def _pdf_response(buffer, filename: str) -> HttpResponse:
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _footer(canvas, page_size, page_number: int) -> None:
    """Every page footer carries the provisional banner and the disclaimer
    in full — wrapped to the measured width, never sliced (§3: mandatory,
    not decorative).  The wrapper is shared with the pack renderer so both
    PDF surfaces repeat the identical canonical strings."""
    from .pack_render import FOOTER_FONT_SIZE, FOOTER_LINE_HEIGHT, _footer_lines

    canvas.saveState()
    canvas.setFont("Helvetica", FOOTER_FONT_SIZE)
    banner_lines = _footer_lines(C.PROVISIONAL_BANNER)
    disclaimer_lines = _footer_lines(C.DISCLAIMER)
    y = 16 + (len(banner_lines) + len(disclaimer_lines) - 1) * FOOTER_LINE_HEIGHT
    for line in banner_lines + disclaimer_lines:
        canvas.drawString(36, y, line)
        y -= FOOTER_LINE_HEIGHT
    canvas.drawRightString(page_size[0] - 36, 16, f"Page {page_number}")
    canvas.restoreState()


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class FlowTestFormView(APIView):
    """Blank district flow-test field sheet (per-outlet grid, up to 4 rows)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as pdf_canvas

        import io

        buffer = io.BytesIO()
        page = pdf_canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        page.setTitle("District flow-test field sheet")
        page.setFont("Helvetica-Bold", 14)
        page.drawString(36, height - 48, "District flow-test field sheet")
        page.setFont("Helvetica", 9)
        page.drawString(36, height - 66, "Hydrant tag: ______________________   Date: ____________   Crew: ______________________")
        page.drawString(36, height - 84, "Static pressure (psi): ____________      Residual pressure (psi): ____________")
        page.setFont("Helvetica-Bold", 9)
        page.drawString(36, height - 112, "Outlet readings (up to 4)")
        page.setFont("Helvetica", 9)
        headers = ["#", "Diameter (in)", "Coefficient", "Pitot (psi)", "Discharge (gpm)"]
        x_positions = [36, 70, 170, 270, 370]
        for x, header in zip(x_positions, headers):
            page.drawString(x, height - 130, header)
        y = height - 152
        for index in range(1, C.MAX_OUTLETS_PER_TEST + 1):
            page.drawString(36, y, str(index))
            page.drawString(70, y, "________")
            page.drawString(170, y, "________")
            page.drawString(270, y, "________")
            page.drawString(370, y, "________")
            y -= 22
        page.drawString(36, y - 8, "Meter total (gpm, optional): ______________")
        page.drawString(36, y - 34, "Photo note: ______________________________________________")
        page.drawString(36, y - 60, "Notes:")
        page.rect(36, y - 120, width - 72, 50)
        page.drawString(36, y - 140, "Signature: ______________________________    Inspector: ______________________________")
        page.setFont("Helvetica-Oblique", 7)
        page.drawString(36, y - 164, C.SINGLE_TEST_SNAPSHOT_NOTE)
        _footer(page, (width, height), 1)
        page.showPage()
        page.save()
        audit_export(path="/api/v1/exports/forms/flow-test/", kind="form_flow_test", row_count=1,
                     user=request.user)
        return _pdf_response(buffer, "flow-test-field-sheet.pdf")


@extend_schema(request=None, responses=OpenApiTypes.OBJECT)
class InventoryFormView(APIView):
    """Blank hydrant inventory sheet."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas as pdf_canvas

        import io

        buffer = io.BytesIO()
        page = pdf_canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        page.setTitle("Hydrant inventory sheet")
        page.setFont("Helvetica-Bold", 14)
        page.drawString(36, height - 48, "Hydrant inventory sheet")
        page.setFont("Helvetica", 8)
        columns = ["Tag", "Latitude", "Longitude", "Location", "Status", "Outlet (in)", "Notes"]
        x_positions = [36, 110, 180, 250, 400, 450, 510]
        for x, header in zip(x_positions, columns):
            page.drawString(x, height - 70, header)
        page.line(36, height - 74, width - 36, height - 74)
        y = height - 92
        while y > 80:
            page.line(36, y + 12, width - 36, y + 12)
            y -= 20
        _footer(page, (width, height), 1)
        page.showPage()
        page.save()
        audit_export(path="/api/v1/exports/forms/inventory/", kind="form_inventory", row_count=1,
                     user=request.user)
        return _pdf_response(buffer, "hydrant-inventory-sheet.pdf")
