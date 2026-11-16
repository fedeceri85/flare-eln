from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse


class OptionalAppBoundaryTests(TestCase):
    def test_lab_python_does_not_import_optional_app(self):
        lab_root = Path(__file__).resolve().parents[2] / "lab"
        offenders = []
        for path in lab_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "import flare_ai" in text or "from flare_ai" in text:
                offenders.append(str(path.relative_to(lab_root)))
        self.assertEqual(offenders, [])

    @override_settings(
        ROOT_URLCONF="flare_ai.tests.urls",
        FLARE_AI_ENABLED=False,
    )
    def test_disabled_assistant_is_absent_from_normal_pages(self):
        user = get_user_model().objects.create_user(username="disabled-ai-user")
        self.client.force_login(user)
        response = self.client.get(reverse("lab:home"))
        self.assertNotContains(response, "data-flare-ai-chat")
