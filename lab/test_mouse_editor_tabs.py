import json
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import individual_mouse_field_prefix
from .models import Mouse, MouseLine, Procedure, ProcedureType, Protocol, Species, resolve_crossing
from .test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class MouseEditorTabTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(username="mouse-tabs"))
        self.protocol = Protocol.objects.create(protocol_number=91, licence_reference="TABS", name="Tab trial")
        self.mice = [Mouse.objects.create(mouse_id=f"Tabs-{index}", protocol=self.protocol) for index in range(2)]
        self.procedure_type = ProcedureType.objects.create(name="Tab procedure")
        species = Species.objects.create(name="Tab species")
        line = MouseLine.objects.create(name="Tab gene", species=species)
        self.crossing, _ = resolve_crossing(species, [line])

    def editor(self, name, **kwargs):
        args = [] if name.endswith("_all") else [self.protocol.licence_reference, self.protocol.protocol_number]
        response = self.client.get(reverse(f"lab:{name}", args=args), kwargs)
        self.assertEqual(response.status_code, 200)
        return response, Markup(response.content.decode())

    def assert_tabs(self, html):
        self.assertEqual(len(html.find("button", role="tab")), 5)
        self.assertEqual(len(html.find("div", role="tablist")), 1)
        for node in html.elements:
            # Without JS, all sections remain accessible and native form controls stay enabled.
            if "data-mouse-tab-panel" in node.attrs and node.attrs.get("id") != "mouse-panel-individual":
                self.assertNotIn("hidden", node.attrs)
        for form in html.find("form"):
            self.assertFalse(form.within("form"))
        save_buttons = html.find("button", form="primary-form")
        self.assertTrue(save_buttons)
        self.assertTrue(all(button.within("div", **{"data-page-actions": None}) for button in save_buttons))

    def test_bulk_edit_panels_keep_forms_and_targets_separate_on_both_routes(self):
        for name in ("edit_selected_mice_all", "edit_selected_mice_licence_protocol"):
            with self.subTest(name=name):
                _, html = self.editor(name, selected_mouse=[mouse.pk for mouse in self.mice])
                self.assert_tabs(html)
                shared = html.find("input", name="status")[0]
                self.assertTrue(shared.within("section", id="mouse-panel-shared"))
                individual = html.find("select", name=f"{individual_mouse_field_prefix(self.mice[0])}_sex")[0]
                self.assertTrue(individual.within("section", id="mouse-panel-individual"))
                creation = html.find("select", name="new_procedure-procedure_type")[0]
                self.assertTrue(creation.within("section", id="mouse-panel-procedures"))
                self.assertFalse(creation.within("form", id="primary-form"))
                self.assertTrue(creation.within("form", id="new-procedure-form"))
                existing_form = html.find("form", **{"data-existing-procedure-editor": None})[0]
                self.assertTrue(existing_form.within("section", id="mouse-panel-existing-procedures"))
                self.assertFalse(existing_form.within("form", id="primary-form"))

    def test_add_panels_include_ids_and_explain_services_after_creation(self):
        for name in ("add_litter_all", "add_litter_licence_protocol"):
            with self.subTest(name=name):
                response, html = self.editor(name)
                self.assert_tabs(html)
                prefix = html.find("input", name="mouse_id_prefix")[0]
                self.assertTrue(prefix.within("section", id="mouse-panel-ids"))
                self.assertEqual(len(html.find("input", name="crossing_search")), 1)
                self.assertContains(response, "Save the mice before adding service records.")
                self.assertFalse(html.find("form", **{"data-existing-procedure-editor": None}))

    def test_save_while_services_tab_active_creates_mice_and_procedures_from_other_tabs(self):
        response = self.client.post(reverse("lab:add_litter_all"), {
            "mouse_editor_tab": "services", "mouse_id_prefix": "Created-", "mouse_numbers": "1-2",
            "crossing_definition": self.crossing.pk,
            "protocol": self.protocol.pk, "notes": "Shared notes", "procedure_drafts": json.dumps([{"procedure_type": self.procedure_type.pk, "lab_member": "AB"}]), "pup_1_sex": "M", "pup_2_sex": "F",
        })
        self.assertEqual(response.status_code, 302, response.context["form"].errors if response.context else "")
        created = Mouse.objects.filter(mouse_id__startswith="Created-").order_by("mouse_id")
        self.assertEqual(list(created.values_list("sex", "notes")), [("M", "Shared notes"), ("F", "Shared notes")])
        self.assertEqual(Procedure.objects.filter(mouse__in=created, lab_member="AB").count(), 2)

    def test_validation_preserves_active_tab_and_unsaved_values(self):
        response = self.client.post(reverse("lab:add_litter_all"), {
            "mouse_editor_tab": "individual", "mouse_id_prefix": "Uncommitted-", "mouse_numbers": "1",
            "protocol": self.protocol.pk, "procedure_drafts": json.dumps([{"procedure_type": self.procedure_type.pk, "date": "invalid"}]), "pup_1_sex": "F",
        })
        self.assertEqual(response.status_code, 200)
        html = Markup(response.content.decode())
        self.assertEqual(html.find("input", name="mouse_editor_tab")[0].attrs["value"], "individual")
        self.assertEqual(html.find("input", name="mouse_id_prefix")[0].attrs["value"], "Uncommitted-")
        self.assertTrue(any(node.within("section", id="mouse-panel-procedures") for node in html.find("ul", **{"class": "error-list"})))
        self.assertFalse(Mouse.objects.filter(pk="Uncommitted-1").exists())
