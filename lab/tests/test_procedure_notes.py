from urllib.parse import quote, urlencode

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from ..models import Mouse, Procedure, ProcedureType, Protocol
from .test_navigation import Markup


@override_settings(ALLOWED_HOSTS=["testserver"])
class ProcedureNotesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="procedure-notes")
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(
            protocol_number=51, licence_reference="NOTES-PROC", name="Notes protocol",
        )
        self.mouse = Mouse.objects.create(mouse_id="NOTES-PROC-1", protocol=self.protocol)
        self.procedure = Procedure.objects.create(
            mouse=self.mouse, procedure_type=ProcedureType.objects.create(name="Notes kind"),
        )

    def test_animal_summary_links_to_the_procedure_editor(self):
        response = self.client.get(reverse("lab:animal_summary", args=[self.mouse.pk]))
        self.assertEqual(response.status_code, 200)
        html = Markup(response.content.decode())
        expected_path = reverse("lab:edit_mouse_procedure", args=[self.mouse.pk, self.procedure.pk])
        edit_links = [
            link for link in html.find("a", **{"class": "table-row-action"})
            if expected_path in link.attrs.get("href", "")
        ]
        self.assertEqual(len(edit_links), 1)
        self.assertIn("return_url=", edit_links[0].attrs["href"])
        self.assertIn("summary_url", response.context)

    def test_procedure_editor_offers_a_notes_field_and_saves_it(self):
        url = reverse("lab:edit_mouse_procedure", args=[self.mouse.pk, self.procedure.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = Markup(response.content.decode())
        self.assertTrue(html.find("textarea", name="notes"))
        response = self.client.post(url, {
            "procedure_type": self.procedure.procedure_type_id,
            "date": "", "lab_member": "AB",
            "notes": "Newly captured notes",
        })
        self.assertEqual(response.status_code, 302)
        self.procedure.refresh_from_db()
        self.assertEqual(self.procedure.notes, "Newly captured notes")

    def test_saving_a_procedure_returns_to_the_procedures_tab(self):
        url = reverse("lab:edit_mouse_procedure", args=[self.mouse.pk, self.procedure.pk])
        response = self.client.post(
            url,
            {
                "procedure_type": self.procedure.procedure_type_id,
                "date": "", "lab_member": "AB",
                "notes": "Procedure notes after save",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        # The first hop (edit_mouse) must carry the procedures tab so the
        # unified editor does not fall back to the shared "Animal details" tab.
        self.assertIn("mouse_editor_tab=procedures", response.redirect_chain[0][0])
        html = Markup(response.content.decode())
        self.assertEqual(
            html.find("input", name="mouse_editor_tab")[0].attrs["value"], "procedures"
        )

    def test_saving_a_procedure_from_the_procedures_tab_keeps_the_tab(self):
        # Reproduce the full user flow: open the unified editor on the
        # Procedures tab, follow its "Edit" link to the individual editor,
        # then save. The realistic return_url must not clobber the tab.
        editor_url = (
            reverse("lab:edit_selected_mice_all")
            + f"?selected_mouse={self.mouse.pk}&mouse_editor_tab=procedures"
        )
        editor = Markup(self.client.get(editor_url).content.decode())
        individual_path = reverse(
            "lab:edit_mouse_procedure", args=[self.mouse.pk, self.procedure.pk]
        )
        edit_link = next(
            link.attrs["href"]
            for link in editor.find("a", **{"class": "table-row-action"})
            if individual_path in link.attrs.get("href", "")
        )
        self.assertIn("return_url=", edit_link)

        individual = self.client.get(edit_link)
        self.assertEqual(individual.status_code, 200)
        saved = self.client.post(
            individual.request["PATH_INFO"],
            {
                "procedure_type": self.procedure.procedure_type_id,
                "date": "", "lab_member": "AB",
                "notes": "Saved from the procedures tab",
            },
            follow=True,
        )
        self.assertEqual(saved.status_code, 200)
        # edit_mouse hop and the final editor page must both land on procedures.
        self.assertIn("mouse_editor_tab=procedures", saved.redirect_chain[0][0])
        self.assertIn("mouse_editor_tab=procedures", saved.redirect_chain[-1][0])
        html = Markup(saved.content.decode())
        self.assertEqual(
            html.find("input", name="mouse_editor_tab")[0].attrs["value"], "procedures"
        )

    def test_saving_a_procedure_then_changes_returns_to_the_summary_in_one_click(self):
        # Full user flow for the "Save changes" redirect bug: from the animal
        # summary, open the "Edit animals" page on the procedures tab, save an
        # individual procedure, then click "Save changes". The first "Save
        # changes" must land on the summary -- it used to bounce back to this
        # edit page on the shared tab, forcing a second click.
        summary_url = reverse("lab:animal_summary", args=[self.mouse.pk])
        editor_url = (
            reverse("lab:edit_selected_mice_all")
            + f"?selected_mouse={self.mouse.pk}&mouse_editor_tab=procedures"
            + "&" + urlencode({"return_url": summary_url})
        )
        editor = Markup(self.client.get(editor_url).content.decode())
        individual_path = reverse(
            "lab:edit_mouse_procedure", args=[self.mouse.pk, self.procedure.pk]
        )
        edit_link = next(
            link.attrs["href"]
            for link in editor.find("a", **{"class": "table-row-action"})
            if individual_path in link.attrs.get("href", "")
        )
        # The individual editor must target the summary, not this edit page.
        self.assertIn("return_url=" + quote(summary_url, safe=""), edit_link)

        saved = self.client.post(
            edit_link,
            {
                "procedure_type": self.procedure.procedure_type_id,
                "date": "", "lab_member": "AB",
                "notes": "Saved from the procedures tab",
            },
            follow=True,
        )
        self.assertEqual(saved.status_code, 200)
        edit_page_url = saved.redirect_chain[-1][0]
        # Back on the edit page, whose return_url must still be the summary.
        page = Markup(saved.content.decode())
        return_urls = [i.attrs["value"] for i in page.find("input", name="return_url")]
        self.assertIn(summary_url, return_urls)

        # The first "Save changes" must land on the summary.
        final = self.client.post(
            edit_page_url,
            {
                "selected_mouse": self.mouse.pk,
                "apply_bulk_edit": "1",
                "update_status": "on", "status": "Alive",
                "return_url": summary_url,
            },
        )
        self.assertEqual(final.status_code, 302)
        self.assertEqual(final["Location"], summary_url)

    def test_deleting_a_procedure_returns_to_the_procedures_tab(self):
        url = reverse("lab:delete_mouse_procedure", args=[self.mouse.pk, self.procedure.pk])
        response = self.client.post(url, {"return_url": ""}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Procedure.objects.filter(pk=self.procedure.pk).exists())
        self.assertIn("mouse_editor_tab=procedures", response.redirect_chain[0][0])
        html = Markup(response.content.decode())
        self.assertEqual(
            html.find("input", name="mouse_editor_tab")[0].attrs["value"], "procedures"
        )
