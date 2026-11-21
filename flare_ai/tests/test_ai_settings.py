"""Tests for the AI assistant settings page and provider configuration."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from flare_ai.agent import get_agent
from flare_ai.context_processors import assistant
from flare_ai.models import AIProviderSettings


@override_settings(
    ROOT_URLCONF="flare_ai.tests.urls",
    FLARE_AI_ENABLED=True,
    FLARE_AI_GROUP_NAME="FLARE AI test users",
)
class AIProviderSettingsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_superuser(
            username="ai-staff", password="test-password"
        )
        cls.non_staff = get_user_model().objects.create_user(
            username="ai-nonstaff", password="test-password"
        )

    def setUp(self):
        get_agent.cache_clear()

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("flare_ai:ai_settings"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_non_staff_user_gets_403(self):
        self.client.force_login(self.non_staff)
        response = self.client.get(reverse("flare_ai:ai_settings"))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_view_settings_page(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("flare_ai:ai_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Assistant")
        self.assertContains(response, "Base URL")
        self.assertContains(response, "API key")

    def test_api_key_is_never_rendered(self):
        cfg = AIProviderSettings.singleton()
        cfg.api_key = "sk-secret-key-12345"
        cfg.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("flare_ai:ai_settings"))
        self.assertNotContains(response, "sk-secret-key-12345")
        self.assertContains(response, "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022")

    def test_key_status_shows_not_set_when_empty(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("flare_ai:ai_settings"))
        self.assertContains(response, "not set")

    def test_staff_can_save_settings(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("flare_ai:ai_settings"), {
            "base_url": "http://localhost:1234/v1",
            "api_key": "sk-new-key",
            "model_name": "gpt-4",
            "assistant_name": "Test Bot",
        })
        self.assertEqual(response.status_code, 302)
        cfg = AIProviderSettings.singleton()
        self.assertEqual(cfg.base_url, "http://localhost:1234/v1")
        self.assertEqual(cfg.api_key, "sk-new-key")
        self.assertEqual(cfg.model_name, "gpt-4")
        self.assertEqual(cfg.assistant_name, "Test Bot")

    def test_blank_api_key_preserves_existing(self):
        cfg = AIProviderSettings.singleton()
        cfg.api_key = "sk-existing-key"
        cfg.save()
        self.client.force_login(self.staff)
        self.client.post(reverse("flare_ai:ai_settings"), {
            "base_url": "http://localhost:1234/v1",
            "api_key": "",
            "model_name": "gpt-4",
            "assistant_name": "Test Bot",
        })
        cfg.refresh_from_db()
        self.assertEqual(cfg.api_key, "sk-existing-key")

    def test_new_api_key_replaces_existing(self):
        cfg = AIProviderSettings.singleton()
        cfg.api_key = "sk-old-key"
        cfg.save()
        self.client.force_login(self.staff)
        self.client.post(reverse("flare_ai:ai_settings"), {
            "base_url": "http://localhost:1234/v1",
            "api_key": "sk-new-key",
            "model_name": "gpt-4",
            "assistant_name": "Test Bot",
        })
        cfg.refresh_from_db()
        self.assertEqual(cfg.api_key, "sk-new-key")

    def test_unconfigured_agent_raises_improperly_configured(self):
        cfg = AIProviderSettings.singleton()
        cfg.base_url = ""
        cfg.api_key = ""
        cfg.model_name = ""
        cfg.save()
        get_agent.cache_clear()
        with self.assertRaises(ImproperlyConfigured):
            get_agent()

    def test_context_processor_reads_assistant_name_from_db(self):
        cfg = AIProviderSettings.singleton()
        cfg.assistant_name = "Custom Bot"
        cfg.save()

        ai_group = Group.objects.create(name="FLARE AI test users")
        user = get_user_model().objects.create_user(username="ctx-test-user")
        user.groups.add(ai_group)

        factory = RequestFactory()
        request = factory.get("/")
        request.user = user

        context = assistant(request)
        self.assertEqual(context["flare_ai_assistant_name"], "Custom Bot")

    def test_context_processor_falls_back_to_default_name(self):
        cfg = AIProviderSettings.singleton()
        cfg.assistant_name = ""
        cfg.save()

        ai_group = Group.objects.create(name="FLARE AI test users")
        user = get_user_model().objects.create_user(username="ctx-fallback-user")
        user.groups.add(ai_group)

        factory = RequestFactory()
        request = factory.get("/")
        request.user = user

        context = assistant(request)
        self.assertEqual(context["flare_ai_assistant_name"], "Marcott-E")

    def test_settings_card_appears_for_staff_when_ai_enabled(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("lab:settings_page"))
        self.assertContains(response, "AI Assistant")
        self.assertContains(response, reverse("flare_ai:ai_settings"))


@override_settings(
    ROOT_URLCONF="flare_ai.tests.urls",
    FLARE_AI_ENABLED=False,
)
class AIProviderSettingsDisabledTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_superuser(
            username="ai-disabled-staff", password="test-password"
        )

    def test_settings_card_hidden_when_ai_disabled(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("lab:settings_page"))
        self.assertNotContains(response, "AI Assistant")