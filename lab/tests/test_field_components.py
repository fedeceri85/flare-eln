"""Rendering contracts for shared form controls and their specialized wrappers."""

from django import forms
from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .test_navigation import Markup


class ExampleForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    name = forms.CharField(label="Mouse name", help_text="Keep the leading zero.")
    enabled = forms.BooleanField(required=False)


class FieldComponentTests(SimpleTestCase):
    def render(self, template, **context):
        return Markup(render_to_string(f"lab/components/{template}.html", context))

    def test_standard_form_keeps_labels_help_hidden_errors_and_checkbox(self):
        form = ExampleForm({"mouse-name": "", "mouse-enabled": "on"}, prefix="mouse")
        html = self.render("form_fields", form=form)
        self.assertEqual(len(html.find("input", name="mouse-token", type="hidden")), 1)
        self.assertEqual(len(html.find("label", **{"for": "id_mouse-name"})), 1)
        self.assertEqual(html.find("small")[0].text, "Keep the leading zero.")
        self.assertEqual(len(html.find("div", **{"class": "form-field"})), 2)
        self.assertEqual(len(html.find("ul", **{"class": "error-list"})), 2)
        self.assertEqual(len(html.find("input", name="mouse-enabled", type="checkbox")), 1)
        self.assertIn("checked", html.find("input", name="mouse-enabled")[0].attrs)
        self.assertTrue(html.find("div", role="alert"))

    def test_shared_mouse_input_preserves_values_and_optional_help(self):
        field = ExampleForm(initial={"name": "<mouse>"}, prefix="litter")["name"]
        for hide_help in (False, True):
            with self.subTest(hide_help=hide_help):
                html = self.render("shared_mouse_input", field=field, hide_help=hide_help)
                self.assertEqual(len(html.find("div", **{"class": "bulk-input"})), 1)
                self.assertEqual(html.find("input", name="litter-name")[0].attrs["value"], "<mouse>")
                self.assertTrue(html.find("label", **{"for": "id_litter-name"}))
                self.assertEqual(len(html.find("small")), 0 if hide_help else 1)

    def test_bulk_update_keeps_separate_toggle_label_and_error_hooks(self):
        form = ExampleForm({"bulk-token": "ok", "bulk-name": "", "bulk-enabled": "on"}, prefix="bulk")
        html = self.render("shared_bulk_update_field", item={
            "checkbox": form["enabled"], "field": form["name"],
        }, field_class="origin-field")
        self.assertTrue(html.find("div", **{"class": "bulk-field origin-field", "data-shared-update": None}))
        self.assertEqual(html.find("label", **{"for": "id_bulk-enabled"})[0].text, "Mouse name")
        self.assertTrue(html.find("span", **{"class": "shared-input-label"}))
        self.assertTrue(html.find("label", **{"for": "id_bulk-name"}))
        self.assertEqual(len(html.find("input", type="checkbox")), 1)
        self.assertIn("checked", html.find("input", name="bulk-enabled")[0].attrs)
        self.assertEqual(len(html.find("ul", **{"class": "error-list"})), 1)
        self.assertFalse(html.find("small"))

    def test_structured_form_keeps_genotype_fieldset_and_ordinary_fields(self):
        form = ExampleForm(initial={"token": "ok", "name": "01"})
        form.visible_field_sections = [
            {"title": "Genotype", "class": "genotype-fields", "fields": [form["name"]]},
            {"title": "", "fields": [form["enabled"]]},
        ]
        html = self.render("form_fields", form=form)
        fieldset = html.find("fieldset", **{"data-genotype-subset": "true"})[0]
        self.assertEqual(fieldset.attrs["class"], "form-subset genotype-fields")
        self.assertEqual(html.find("legend")[0].text, "Genotype")
        self.assertEqual(len(html.find("div", **{"class": "genotype-result-controls"})), 1)
        self.assertEqual(len(html.find("input", name="name")), 1)
        self.assertEqual(len(html.find("input", name="enabled")), 1)
