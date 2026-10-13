"""Server-rendered pages (no frontend build step).

Only the entry surfaces live here for now: login/logout, the dashboard shell
and the service-worker file.  Page modules for the ledger/coverage/pack
surfaces live beside this one.
"""
from __future__ import annotations

from django.contrib.auth import login as auth_login, logout as auth_logout
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from . import constants as C
from .auth import ThrottleExceeded, authenticate_with_throttle
from .health import healthz as healthz_view
from .models import Hydrant, LoginAttempt


def _safe_next(request) -> str:
    """The local path to return to after sign-in, or "".

    The anonymous redirects carry ``?next=<path>``; only a path on this site is
    ever honoured. The judgement is Django's own ``url_has_allowed_host_and_scheme``,
    never a hand-rolled prefix check: browsers treat a backslash as a slash, so
    ``/\\evil.example`` is a protocol-relative URL a ``startswith("//")`` test
    cannot see.
    """
    candidate = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}):
        return candidate
    return ""


def login_view(request):
    """GET returns HTML with a CSRF token; POST returns 302 or 200 with error."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    error = ""
    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        try:
            user = authenticate_with_throttle(
                request, username, password, surface=LoginAttempt.SURFACE_FORM
            )
        except ThrottleExceeded as exc:
            response = render(request, "ledger/login.html", {"error": "Too many attempts. Try again later."})
            response.status_code = 429
            response["Retry-After"] = str(exc.retry_after)
            return response
        if user is None:
            error = "Invalid username or password."
        else:
            auth_login(request, user)
            return HttpResponseRedirect(_safe_next(request) or reverse("dashboard"))
    return render(request, "ledger/login.html", {"error": error, "next": _safe_next(request)})


@require_http_methods(["GET", "POST"])
def logout_view(request):
    auth_logout(request)
    return redirect("login")


def dashboard(request):
    from .pages import dashboard_context

    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    return render(request, "ledger/dashboard.html", dashboard_context(request))


def field_page(request):
    """The /field/ offline-capable capture page (v0: online capture + queue)."""
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    return render(
        request,
        "ledger/field.html",
        {
            "hydrants": Hydrant.objects.order_by("asset_tag")[:500],
            # The same engine bounds the office sheet declares on its inputs —
            # the field door's offline validation reads them from the markup.
            "max_static_psi": C.MAX_STATIC_PSI,
            "max_pitot_psi": C.MAX_PITOT_PSI,
        },
    )


def hydrant_list(request):
    from .pages import hydrant_list_context

    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    return render(request, "ledger/hydrants.html", hydrant_list_context(request))


def hydrant_detail(request, pk):
    from django.shortcuts import get_object_or_404

    from .pages import hydrant_detail_context

    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    hydrant = get_object_or_404(Hydrant, pk=pk)
    return render(request, "ledger/hydrant_detail.html", hydrant_detail_context(request, hydrant))


def coverage_page(request):
    from .pages import coverage_context

    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    return render(request, "ledger/coverage.html", coverage_context(request))


def pack_page(request):
    from .pages import pack_context

    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    return render(request, "ledger/packs.html", pack_context(request))


# ---------------------------------------------------------------------------
# Record surfaces (spec §11): the registry forms, the parcel register, the
# paper/interchange page, the guide and the read-only settings reference.
# Every one of them is a page over routes the API already owns; none of them
# writes anything itself.
# ---------------------------------------------------------------------------

def _login_redirect(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('login')}?next={request.path}")
    return None


def hydrant_create(request):
    from .pages import hydrant_form_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    return render(request, "ledger/hydrant_form.html", hydrant_form_context(request))


def hydrant_edit(request, pk):
    from django.shortcuts import get_object_or_404

    from .pages import hydrant_form_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    hydrant = get_object_or_404(Hydrant, pk=pk)
    return render(request, "ledger/hydrant_form.html", hydrant_form_context(request, hydrant))


def flow_test_new(request, pk):
    from django.shortcuts import get_object_or_404

    from .pages import flow_test_form_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    hydrant = get_object_or_404(Hydrant, pk=pk)
    return render(request, "ledger/flow_test_form.html", flow_test_form_context(request, hydrant))


def inspection_new(request, pk):
    from django.shortcuts import get_object_or_404

    from .pages import inspection_form_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    hydrant = get_object_or_404(Hydrant, pk=pk)
    return render(request, "ledger/inspection_form.html", inspection_form_context(request, hydrant))


def parcel_list(request):
    from .pages import parcel_list_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    return render(request, "ledger/parcels.html", parcel_list_context(request))


def parcel_detail(request, pk):
    from django.shortcuts import get_object_or_404

    from .models import Parcel
    from .pages import parcel_detail_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    parcel = get_object_or_404(Parcel, pk=pk)
    return render(request, "ledger/parcel_detail.html", parcel_detail_context(request, parcel))


def parcel_edit(request, pk):
    from django.shortcuts import get_object_or_404

    from .models import Parcel
    from .pages import parcel_form_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    parcel = get_object_or_404(Parcel, pk=pk)
    return render(request, "ledger/parcel_form.html", parcel_form_context(request, parcel))


def exports_page(request):
    from .pages import exports_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    return render(request, "ledger/exports.html", exports_context(request))


def guide_page(request):
    from .pages import guide_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    return render(request, "ledger/guide.html", guide_context(request))


def settings_page(request):
    from .pages import settings_context

    guard = _login_redirect(request)
    if guard is not None:
        return guard
    return render(request, "ledger/settings.html", settings_context(request))


def service_worker(request):
    """The /field/ service worker shell (anonymous surface, exactly enumerated)."""
    body = SERVICE_WORKER_JS
    response = HttpResponse(body, content_type="application/javascript")
    response["Cache-Control"] = "no-cache"
    return response


SERVICE_WORKER_JS = """\
/* HydrantProof service worker: network-first shell (so a connected load always
   refreshes the CSRF token), cache-first for vendored assets. */
const SHELL_CACHE = 'hydrantproof-shell-v1';
const ASSET_CACHE = 'hydrantproof-assets-v1';
const SHELL_URLS = ['/field/'];
const ASSET_PREFIXES = ['/static/'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (ASSET_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
    event.respondWith(caches.open(ASSET_CACHE).then((cache) =>
      cache.match(request).then((hit) => hit || fetch(request).then((response) => {
        cache.put(request, response.clone());
        return response;
      }))
    ));
    return;
  }
  if (url.pathname.startsWith('/field/')) {
    event.respondWith(fetch(request).then((response) => {
      // Only a real /field/ render refreshes the shell cache: a followed
      // 302-to-login (the session check) must never replace the cached
      // capture shell with the login page.
      if (response.ok && !response.redirected) {
        const copy = response.clone();
        caches.open(SHELL_CACHE).then((cache) => cache.put('/field/', copy));
      }
      return response;
    }).catch(() => caches.match('/field/')));
  }
});
"""
