import json
import os
import subprocess
import sys

from django.http import HttpResponse
from django.middleware.csrf import CsrfViewMiddleware, get_token
from django.test import RequestFactory, SimpleTestCase, override_settings


@override_settings(
    ALLOWED_HOSTS=["marcotti-nas"],
    CSRF_TRUSTED_ORIGINS=["https://marcotti-nas:8000"],
)
class ReverseProxyCsrfTests(SimpleTestCase):
    def check_post(self, origin, include_token=True):
        request = RequestFactory().post(
            "/login/", HTTP_HOST="marcotti-nas:8000", HTTP_ORIGIN=origin,
        )
        token = get_token(request)
        request.COOKIES["csrftoken"] = request.META["CSRF_COOKIE"]
        if include_token:
            request.POST = request.POST.copy()
            request.POST["csrfmiddlewaretoken"] = token
        middleware = CsrfViewMiddleware(lambda request: HttpResponse())
        middleware.process_request(request)
        return middleware.process_view(request, lambda request: HttpResponse(), (), {})

    def test_explicit_https_origin_works_over_http_backend(self):
        self.assertIsNone(self.check_post("https://marcotti-nas:8000"))

    def test_other_origins_and_ports_are_rejected(self):
        for origin in ("https://untrusted.example", "https://marcotti-nas:8001"):
            with self.subTest(origin=origin):
                self.assertEqual(self.check_post(origin).status_code, 403)

    def test_trusted_origin_still_requires_csrf_token(self):
        self.assertEqual(self.check_post("https://marcotti-nas:8000", False).status_code, 403)

    def test_origins_are_loaded_from_environment(self):
        for raw, expected in (
            ("", []),
            ("https://marcotti-nas:8000, https://eln.example.org", [
                "https://marcotti-nas:8000", "https://eln.example.org",
            ]),
        ):
            with self.subTest(raw=raw):
                result = subprocess.run(
                    [sys.executable, "-c", "import json; from labdb.settings import CSRF_TRUSTED_ORIGINS; print(json.dumps(CSRF_TRUSTED_ORIGINS))"],
                    env={**os.environ, "DJANGO_CSRF_TRUSTED_ORIGINS": raw},
                    capture_output=True, text=True, check=True,
                )
                self.assertEqual(json.loads(result.stdout), expected)
