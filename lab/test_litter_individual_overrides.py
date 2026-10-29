from datetime import date
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import LitterCreateForm, genotype_field_name
from .models import Mouse, MouseLine, Procedure, ProcedureType, Protocol, Species, resolve_crossing
from .views.mice import create_litter_mice


@override_settings(ALLOWED_HOSTS=['testserver'])
class LitterIndividualOverrideTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='add-overrides')
        self.client.force_login(self.user)
        self.protocol = Protocol.objects.create(protocol_number=81, licence_reference='OVERRIDES', name='Allowed')
        self.other_protocol = Protocol.objects.create(protocol_number=82, licence_reference='OVERRIDES', name='Other')
        species = Species.objects.create(name='Override species')
        line = MouseLine.objects.create(species=species, name='Original')
        other = MouseLine.objects.create(species=species, name='Different')
        self.crossing, _ = resolve_crossing(species, [line])
        self.other_crossing, _ = resolve_crossing(species, [other])
        self.other_line = other
        self.mother = Mouse.objects.create(mouse_id='Mother', protocol=self.protocol, sex='F')
        self.father = Mouse.objects.create(mouse_id='Father', protocol=self.protocol, sex='M')
        self.url = reverse('lab:add_litter_all')

    def payload(self, **extra):
        return {'mouse_id_prefix': 'NEW-', 'mouse_numbers': '1-2', 'protocol': self.protocol.pk,
                'crossing_definition': self.crossing.pk, 'notes': 'Shared notes', 'status': 'Alive',
                'mother_external_id': self.mother.pk, 'father_external_id': self.father.pk, **extra}

    def form(self, **extra):
        form = LitterCreateForm(self.payload(**extra))
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_checked_details_override_shared_values_and_blank_clears(self):
        form = self.form(pup_1_override_notes_enabled='on', pup_1_override_notes='',
                         pup_1_override_status_enabled='on', pup_1_override_status='Culled',
                         pup_1_override_date_of_birth_enabled='on', pup_1_override_date_of_birth='2026-10-01',
                         pup_1_override_protocol_enabled='on', pup_1_override_protocol=self.other_protocol.pk,
                         pup_2_override_notes='Unchecked tampering', pup_2_override_status='invalid')
        mice, _ = create_litter_mice(None, form)
        self.assertEqual((mice[0].notes, mice[0].status, mice[0].date_of_birth, mice[0].protocol),
                         ('', 'Culled', date(2026, 10, 1), self.other_protocol))
        self.assertEqual((mice[1].notes, mice[1].status), ('Shared notes', 'Alive'))

    def test_crossing_override_uses_its_own_genotypes(self):
        field = f'pup_1_{genotype_field_name(self.other_line)}'
        form = self.form(pup_1_override_crossing_definition_enabled='on',
                         pup_1_override_crossing_definition=self.other_crossing.pk, genotype_modes_present="1", **{field: 'het'})
        mice, _ = create_litter_mice(None, form)
        self.assertEqual(mice[0].crossing_definition_id, self.other_crossing.pk)
        self.assertEqual(mice[0].genotype_calls.get().mouse_line_id, self.other_line.pk)
        self.assertEqual(mice[0].genotype_calls.get().zygosity, 'het')
        self.assertEqual(mice[1].crossing_definition_id, self.crossing.pk)

    def test_parent_overrides_take_precedence_and_blank_keeps_parent_unknown(self):
        form = self.form(pup_1_parent_update_mother='on', pup_1_parent_mother_external_id='',
                         pup_1_parent_update_father='on', pup_1_parent_father_external_id='External sire',
                         pup_2_parent_mother_external_id='Unchecked tampering')
        mice, _ = create_litter_mice(None, form)
        for mouse in mice:
            mouse.refresh_from_db()
        self.assertIsNone(mice[0].mother_id)
        self.assertEqual(mice[0].mother_external_id, '')
        self.assertIsNone(mice[0].father_id)
        self.assertEqual(mice[0].father_external_id, 'External sire')
        self.assertEqual((mice[1].mother_id, mice[1].father_id), (self.mother.pk, self.father.pk))

    def test_invalid_override_renders_open_and_creates_nothing(self):
        response = self.client.post(self.url, self.payload(pup_1_override_status_enabled='on', pup_1_override_status='invalid'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('pup_1_override_status', response.context['form'].errors)
        self.assertContains(response, 'individual-animal-overrides" open')
        self.assertFalse(Mouse.objects.filter(pk__startswith='NEW-').exists())

    def test_preview_returns_detail_and_parent_overrides_with_entered_values(self):
        response = self.client.post(self.url, self.payload(preview_mice='1', mouse_numbers='1-3',
            pup_1_override_notes_enabled='on', pup_1_override_notes='Keep my draft',
            pup_1_parent_update_mother='on', pup_1_parent_mother_external_id='External dam'),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result['mouse_count'], 3)
        self.assertIn('Keep my draft', result['detail_overrides_html'])
        self.assertIn('pup_3_override_status', result['detail_overrides_html'])
        self.assertIn('External dam', result['parent_overrides_html'])
        self.assertIn('pup_3_parent_mother_external_id', result['parent_overrides_html'])
        self.assertFalse(Mouse.objects.filter(pk__startswith='NEW-').exists())

    def test_individual_protocol_cannot_bypass_procedure_permissions(self):
        self.other_protocol.allows_regulated_procedures = False
        self.other_protocol.save()
        procedure = ProcedureType.objects.create(name='Override procedure')
        response = self.client.post(self.url, self.payload(
            pup_1_override_protocol_enabled='on', pup_1_override_protocol=self.other_protocol.pk,
            procedure_drafts=json.dumps([{'procedure_type': procedure.pk}])))
        self.assertEqual(response.status_code, 200)
        self.assertIn('pup_1_override_protocol', response.context['form'].errors)
        self.assertFalse(Mouse.objects.filter(pk__startswith='NEW-').exists())
        self.assertFalse(Procedure.objects.exists())

    def test_parent_conflict_rolls_back_entire_creation(self):
        response = self.client.post(self.url, self.payload(pup_1_parent_update_mother='on',
                                                         pup_1_parent_mother_external_id=self.father.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'same linked mother and father')
        self.assertFalse(Mouse.objects.filter(pk__startswith='NEW-').exists())
