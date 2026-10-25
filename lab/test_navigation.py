from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urlsplit

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse

from .models import Experiment, Mouse, Procedure, ProcedureType, Project, Protocol, Recording, RecordingType
from .navigation import build_navigation


class Element:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.text = tag, dict(attrs), parent, ""

    def within(self, tag, **attrs):
        node = self.parent
        while node:
            if node.tag == tag and all(key in node.attrs and node.attrs[key] == value for key, value in attrs.items()):
                return True
            node = node.parent
        return False


class Markup(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements, self.stack = [], []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs, self.stack[-1] if self.stack else None)
        self.elements.append(node)
        if tag not in {"input", "meta", "link", "br", "hr", "img", "source", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for node in self.stack:
            node.text += data

    def find(self, tag, **attrs):
        return [node for node in self.elements if node.tag == tag and all(key in node.attrs and node.attrs[key] == value for key, value in attrs.items())]


@override_settings(ALLOWED_HOSTS=["testserver"])
class NavigationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ui-review")
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(
            protocol_number=17, licence_reference="UI-LICENCE", name="Behaviour protocol",
        )
        self.mice = [Mouse.objects.create(mouse_id=f"UI-{index:02}", protocol=self.protocol) for index in range(30)]
        self.mouse = self.mice[0]
        self.project = Project.objects.create(name="Hearing study")
        self.kind = RecordingType.objects.create(name="UI recording", slug="ui-recording")
        self.experiment = Experiment.objects.create(name="Auditory responses", project=self.project, recording_type=self.kind)
        self.recording = Recording.objects.create(
            experiment=self.experiment, recording_type=self.kind, owner=self.user,
            recording_date=date(2026, 1, 1),
        )
        self.procedure = Procedure.objects.create(
            mouse=self.mouse, procedure_type=ProcedureType.objects.create(name="UI procedure"),
        )

    def page(self, name, args=(), data=None, method="get"):
        response = getattr(self.client, method)(reverse(f"lab:{name}", args=args), data or {}, follow=True)
        self.assertEqual(response.status_code, 200, name)
        return response, Markup(response.content.decode())

    def test_consistent_home_and_current_location_on_all_lab_pages(self):
        cases = [
            ("home", (), "Home"), ("mice_page", (), "Animals"),
            ("procedure_page", (), "Procedures"), ("projects_page", (), "Projects"),
            ("services_page", (), "Services"), ("reports_page", (), "Reports"),
            ("settings_page", (), "Settings"), ("import_templates", (), "Settings"),
            ("add_mouse_all", (), "Animals"), ("add_litter_all", (), "Animals"),
            ("add_mouse_licence_protocol", (self.protocol.licence_reference, 17), "Procedures"),
            ("animal_summary", (self.mouse.pk,), "Animals"), ("edit_mouse", (self.mouse.pk,), "Procedures"),
            ("rename_mouse", (self.mouse.pk,), "Procedures"), ("delete_mouse", (self.mouse.pk,), "Procedures"),
            ("edit_mouse_procedure", (self.mouse.pk, self.procedure.pk), "Procedures"),
            ("add_project", (), "Projects"), ("edit_project", (self.project.pk,), "Projects"),
            ("experiments_page", (self.project.pk,), "Projects"),
            ("add_experiment", (self.project.pk,), "Projects"),
            ("edit_experiment", (self.project.pk, self.experiment.pk), "Projects"),
            ("recordings_page", (self.project.pk, self.experiment.pk), "Projects"),
            ("add_recording", (self.project.pk, self.experiment.pk), "Projects"),
            ("edit_recording", (self.project.pk, self.experiment.pk, self.recording.pk), "Projects"),
            ("delete_recording", (self.project.pk, self.experiment.pk, self.recording.pk), "Projects"),
            ("select_recording_mouse", (self.project.pk, self.experiment.pk), "Projects"),
        ]
        for name, args, section in cases:
            with self.subTest(page=name):
                _, html = self.page(name, args)
                main_links = [node for node in html.find("a") if node.within("nav", **{"aria-label": "Main navigation"})]
                self.assertEqual([node.text.strip() for node in main_links if node.attrs.get("aria-current")], [] if section == "Animals" else [section])
                self.assertNotIn("Animals", [node.text.strip() for node in main_links])
                self.assertEqual(main_links[-1].text.strip(), "Settings")
                self.assertEqual(len([node for node in main_links if node.text.strip() == "Home"]), 1)
                self.assertEqual(len(html.find("span", **{"aria-current": "page"})), 1)

    def test_staff_navigation_respects_permissions(self):
        response, _ = self.page("home")
        self.assertNotContains(response, reverse("lab:lines_crossings_page"))
        self.assertEqual(self.client.get(reverse("lab:lines_crossings_page")).status_code, 403)
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        response, _ = self.page("lines_crossings_page")
        self.assertContains(response, "Lines &amp; Crossings")
        self.assertContains(response, reverse("admin:index"))

    def test_services_remains_active_in_nested_recordings(self):
        self.project.is_service = True
        self.project.save(update_fields=["is_service"])
        _, html = self.page("add_recording", (self.project.pk, self.experiment.pk))
        active = [node.text.strip() for node in html.find("a", **{"aria-current": "true"})]
        self.assertEqual(active, ["Services"])

    def test_breadcrumbs_use_loaded_names_without_database_queries(self):
        request = RequestFactory().get(reverse("lab:add_recording", args=[self.project.pk, self.experiment.pk]))
        request.resolver_match = resolve(request.path)
        request.user = self.user
        with self.assertNumQueries(0):
            nav = build_navigation({"request": request, "project": self.project, "experiment": self.experiment})
        labels = [item["label"] for item in nav["breadcrumbs"]]
        self.assertIn("Hearing study", labels)
        self.assertIn("Auditory responses", labels)
        self.assertEqual(labels[-1], "Add recording")

    def test_mouse_links_and_return_actions_preserve_list_state(self):
        url = reverse("lab:mice_page") + "?page=2&sort=mouse_id&direction=asc&per_page=25"
        response = self.client.get(url)
        html = Markup(response.content.decode())
        record = next(node for node in html.find("a") if node.attrs.get("class") == "mouse-link")
        self.assertEqual(parse_qs(urlsplit(record.attrs["href"]).query)["return_url"], [url])
        _, html = self.page("edit_mouse", (self.mouse.pk,), {"return_url": url})
        back = html.find("a", **{"class": "back-link"})[0]
        self.assertEqual(back.attrs["href"], url)
        cancel = [node for node in html.find("a") if node.text.strip() == "Cancel"]
        self.assertTrue(cancel)
        self.assertTrue(all(node.attrs["href"] == url for node in cancel))
        crumb = next(node for node in html.find("a") if node.within("nav", **{"aria-label": "Breadcrumb"}) and node.text.strip() == "Animals")
        self.assertEqual(crumb.attrs["href"], url)

    def test_summary_to_edit_to_list_keeps_original_context(self):
        destination = reverse("lab:mice_page") + "?mouse_id=UI&sort=date_of_birth&page=2"
        record_url = reverse("lab:animal_summary", args=[self.mouse.pk]) + "?" + urlencode({"return_url": destination})
        response = self.client.get(record_url)
        html = Markup(response.content.decode())
        edit = html.find("a", **{"data-mouse-summary-edit": None})[0]
        summary_return = parse_qs(urlsplit(edit.attrs["href"]).query)["return_url"][0]
        self.assertEqual(urlsplit(summary_return).path, urlsplit(record_url).path)
        self.assertEqual(parse_qs(urlsplit(summary_return).query)["return_url"], [destination])
        response = self.client.get(edit.attrs["href"], follow=True)
        html = Markup(response.content.decode())
        self.assertEqual(html.find("a", **{"class": "back-link"})[0].attrs["href"], summary_return)
        response = self.client.get(summary_return)
        html = Markup(response.content.decode())
        self.assertEqual(html.find("a", **{"class": "back-link"})[0].attrs["href"], destination)
        self.assertEqual(html.find("a", **{"aria-current": "true"}), [])

    def test_procedure_context_has_licence_and_protocol_without_extra_queries(self):
        destination = reverse("lab:procedure_page_licence_protocol", args=[self.protocol.licence_reference, 17]) + "?status=Alive&page=2"
        request = RequestFactory().get(reverse("lab:edit_mouse", args=[self.mouse.pk]), {"return_url": destination})
        request.resolver_match = resolve(request.path)
        request.user = self.user
        mouse = Mouse.objects.select_related("protocol").get(pk=self.mouse.pk)
        with self.assertNumQueries(0):
            nav = build_navigation({"request": request, "mouse": mouse})
        self.assertEqual(nav["section"], "procedures")
        labels = [item["label"] for item in nav["breadcrumbs"]]
        self.assertEqual(labels, ["Home", "Procedures", "UI-LICENCE", "Behaviour protocol", self.mouse.pk, "Edit animal"])
        self.assertEqual(nav["breadcrumbs"][3]["url"], destination)

    def test_recording_return_actions_preserve_filtered_list(self):
        destination = reverse("lab:recordings_page", args=[self.project.pk, self.experiment.pk]) + "?mouse_id=UI&per_page=50&page=2"
        _, html = self.page("edit_recording", (self.project.pk, self.experiment.pk, self.recording.pk), {"return_url": destination})
        self.assertEqual(html.find("a", **{"class": "back-link"})[0].attrs["href"], destination)
        self.assertTrue(all(node.attrs["href"] == destination for node in html.find("a") if node.text.strip() == "Cancel"))

    def test_external_return_destination_is_not_used(self):
        _, html = self.page("add_mouse_all", data={"return_url": "https://example.invalid/steal"})
        self.assertEqual(html.find("a", **{"class": "back-link"})[0].attrs["href"], reverse("lab:mice_page"))

    def test_sticky_actions_target_main_form_and_preserve_submit_name(self):
        _, html = self.page("edit_mouse", (self.mouse.pk,))
        self.assertEqual(len(html.find("form", id="primary-form")), 1)
        sticky_save = html.find("button", form="primary-form")[0]
        self.assertIsNone(sticky_save.attrs.get("name"))
        self.assertTrue(html.find("input", name="apply_bulk_edit", value="1"))
        
        secondary = [button for button in html.find("button") if button.text.strip() == "Add procedure"]
        self.assertEqual(len(secondary), 1)
        self.assertFalse(secondary[0].within("form", id="primary-form"))

    def test_main_form_submission_still_saves_and_returns(self):
        destination = reverse("lab:projects_page") + "?status=active&name=Hearing"
        response = self.client.post(reverse("lab:add_project"), {"name": "New UI project", "return_url": destination})
        self.assertRedirects(response, destination)
        self.assertTrue(Project.objects.filter(name="New UI project").exists())

    def test_error_summary_links_to_visible_invalid_fields(self):
        response, html = self.page("add_project", method="post")
        self.assertContains(response, "Please check the following")
        summary_links = [node for node in html.find("a") if node.within("div", **{"data-error-summary": None})]
        self.assertTrue(any(node.attrs.get("href") == "#id_name" for node in summary_links))
        self.assertTrue(html.find("input", id="id_name"))

    def test_bulk_selection_and_delete_confirmation_keep_targets(self):
        _, html = self.page("edit_selected_mice_all", data={"selected_mouse": [self.mouse.pk, self.mice[1].pk]}, method="post")
        self.assertTrue(html.find("input", name="selected_mouse", value=self.mouse.pk))
        self.assertTrue(html.find("button", form="primary-form"))
        destructive = html.find("button", formaction=reverse("lab:delete_selected_mice_all"))
        self.assertEqual(len(destructive), 1)
        _, html = self.page("delete_selected_mice_all", data={"selected_mouse": [self.mouse.pk, self.mice[1].pk]}, method="post")
        self.assertFalse(html.find("button", form="primary-form"))
        self.assertTrue(Mouse.objects.filter(pk=self.mouse.pk).exists())

    def test_login_has_brand_and_errors_without_authenticated_navigation(self):
        self.client.logout()
        response, html = self.page("login")
        self.assertContains(response, "FLARE-ELN")
        self.assertFalse(html.find("nav", **{"aria-label": "Main navigation"}))
        self.assertFalse(html.find("div", **{"data-page-actions": None}))

    @override_settings(FLARE_VERSION="v1.2.3")
    def test_header_shows_running_version_on_lab_pages_and_login(self):
        for name in ("home", "mice_page", "login"):
            with self.subTest(page=name):
                if name == "login":
                    self.client.logout()
                _, html = self.page(name)
                badge = html.find("span", **{"class": "brand-version"})
                self.assertEqual(len(badge), 1)
                self.assertEqual(badge[0].text, "v1.2.3")
                self.assertTrue(badge[0].within("a", **{"class": "brand"}))

    def test_crossing_validation_links_target_its_own_form(self):
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        _, html = self.page("lines_crossings_page", data={"action": "create_crossing"}, method="post")
        ids = [node.attrs["id"] for node in html.elements if "id" in node.attrs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(html.find("select", id="crossing_species", name="species"))
        self.assertTrue(html.find("a", href="#crossing_species"))
        self.assertFalse(html.find("button", form="primary-form"))
