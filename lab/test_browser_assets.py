import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import Mouse, Protocol
from .test_navigation import Markup


class BrowserAssetConfigurationTests(SimpleTestCase):
    def test_crossing_configuration_round_trips_special_characters_as_data(self):
        mouse_id = 'Mouse / "42" & <script>alert("x")</script>'
        lookup = '/lookup/?q="quoted"&next=<value>'
        html = Markup(render_to_string("lab/components/crossing_selector_assets.html", {
            "crossing_lookup_url": lookup,
            "crossing_genotype_fields_url": "/genotype/",
            "crossing_mouse_id": mouse_id,
        }))
        config = html.find("div", **{"data-crossing-selector-config": None})[0]
        self.assertEqual(config.attrs["data-mouse-id"], mouse_id)
        self.assertEqual(config.attrs["data-lookup-url"], lookup)
        scripts = html.find("script")
        self.assertEqual(len(scripts), 1)
        self.assertTrue(scripts[0].attrs["src"].endswith("/lab/crossing_selector.js"))
        self.assertFalse(scripts[0].text.strip())

    def test_template_javascript_assets_exist_and_templates_contain_no_executable_code(self):
        root = Path(__file__).parent / "templates" / "lab"
        assets = set()
        for path in root.rglob("*.html"):
            text = path.read_text()
            for script in Markup(text).find("script"):
                self.assertIn("src", script.attrs, f"Executable inline script in {path.name}")
                self.assertFalse(script.text.strip(), path.name)
            assets.update(re.findall(r"\{% static ['\"](lab/[^'\"]+\.js)['\"] %\}", text))
        self.assertTrue(assets)
        for asset in assets:
            self.assertTrue(finders.find(asset), asset)


@override_settings(ALLOWED_HOSTS=["testserver"])
class BrowserAssetPageTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="assets", is_staff=True, is_superuser=True))
        self.protocol = Protocol.objects.create(protocol_number=18, licence_reference="ASSETS", name="Assets")
        self.mice = [Mouse.objects.create(mouse_id=f"Assets-{number}", protocol=self.protocol) for number in (1, 2)]

    def test_mouse_editors_load_each_dependency_once_before_its_consumer(self):
        for name, data, consumer in (
            ("add_litter_all", {}, "litter_mouse_rows.js"),
            ("edit_selected_mice_all", {"selected_mouse": [mouse.pk for mouse in self.mice]}, "bulk_mouse_rows.js"),
        ):
            response = self.client.get(reverse("lab:" + name), data)
            self.assertEqual(response.status_code, 200)
            scripts = Markup(response.content.decode()).find("script")
            assets = [node.attrs["src"].rsplit("/", 1)[-1] for node in scripts if "src" in node.attrs]
            self.assertEqual(len(assets), len(set(assets)))
            self.assertLess(assets.index("mouse_row_editor.js"), assets.index(consumer))
            genotype_consumer = "litter_genotypes.js" if name == "add_litter_all" else consumer
            self.assertLess(assets.index("mouse_genotype_editor.js"), assets.index(genotype_consumer))
            for node in scripts:
                self.assertTrue("src" in node.attrs or node.attrs.get("type") == "application/json")

    def test_mouse_id_and_line_lookup_urls_are_explicit_html_configuration(self):
        response = self.client.get(reverse("lab:add_litter_all"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="mouse_id_prefix"')
        self.assertNotContains(response, "mouse_id_preview.js")
        response = self.client.get(reverse("lab:lines_crossings_page"))
        self.assertEqual(response.status_code, 200)
        field = Markup(response.content.decode()).find("input", **{"data-line-lookup-input": "true"})[0]
        self.assertEqual(field.attrs["data-lookup-url"], reverse("lab:line_lookup"))
