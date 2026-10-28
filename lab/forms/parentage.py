"""Grouped parent editing, exact identity resolution and individual overrides."""
from django import forms
from django.urls import reverse

from ..family import group_mice_by_parents, parent_group_version
from ..models import Mouse
from ..parentage import ROLES, fingerprint, validate_parentage


class ParentInput(forms.TextInput):
    def render(self, name, value, attrs=None, renderer=None):
        # Server-side disabled fields ignore unchecked edits; text entry still
        # works without JS. The browser enhancement disables them on load.
        attrs = dict(attrs or {})
        attrs.pop('disabled', None)
        return super().render(name, value, attrs, renderer)


def configure_parent_controls(form, mice=(), bulk=False):
    mice = list(mice)
    groups = group_mice_by_parents(mice) if mice else [{'key': 'new', 'mice': []}]
    form.parent_groups, form.individual_parent_controls, form._parent_specs = [], [], []
    form.parentage_errors, form.parentage_resolution_warnings = [], []
    form._parent_group_version = parent_group_version(mice)
    if bulk and len(mice) > 1:
        form.fields['parent_group_version'] = forms.CharField(required=False, widget=forms.HiddenInput)
        form.initial['parent_group_version'] = form._parent_group_version
    if form.is_bound:
        form.data = form.data.copy()

    def add(role, prefix, members, individual=False):
        text_name, ref_name = f'{prefix}{role}_external_id', f'{prefix}{role}'
        toggle = f'{prefix}update_{role}'
        previous = members[0] if members else None
        initial_ref = getattr(previous, f'{role}_id') if previous else None
        initial_text = (initial_ref or getattr(previous, f'{role}_external_id')) if previous else ''
        if bulk:
            form.fields[toggle] = forms.BooleanField(required=False, label=f'{"Override" if individual else "Update"} {role}')
        enabled = not bulk or (form.is_bound and form.fields[toggle].clean(form.data.get(form.add_prefix(toggle))))
        form.fields[text_name] = forms.CharField(required=False, max_length=255, label=f'{role.title()} ID', disabled=not enabled,
            widget=ParentInput(attrs={'data-parent-text': '', 'autocomplete': 'off', 'data-parent-lookup': reverse('lab:mouse_lookup'),
                                     'data-parent-exclude': members[0].pk if len(members) == 1 else ''}))
        # Resolve against the authoritative text, rather than rejecting a stale
        # hidden reference before blank/changed text can invalidate it.
        form.fields[ref_name] = forms.CharField(required=False, max_length=100, disabled=not enabled,
            widget=ParentInput(attrs={'hidden': True, 'data-parent-reference': '', 'aria-label': f'Linked {role} Animal ID'}))
        form.initial[text_name], form.initial[ref_name] = initial_text, initial_ref
        if enabled and form.is_bound and not str(form.data.get(form.add_prefix(text_name), '')).strip():
            form.data[form.add_prefix(ref_name)] = ''
        form._parent_specs.append({'role': role, 'text_name': text_name, 'ref_name': ref_name, 'toggle': toggle if bulk else None,
                                   'members': members, 'individual': individual, 'initial_text': initial_text, 'initial_ref': initial_ref})
        return {'role': role, 'text': form[text_name], 'reference': form[ref_name], 'checkbox': form[toggle] if bulk else None,
                'had_parent': bool(initial_text or initial_ref), 'has_overrides': bulk and len(mice) > 1 and not individual,
                'target': f'animal {previous.pk}' if len(members) == 1 else f'this group of {len(members)} animals' if members else 'these new animals'}

    for group in groups:
        prefix = '' if len(groups) == 1 else f'family_{group["key"][:16]}_'
        controls = [add(role, prefix, group['mice']) for role in ROLES]
        overrides = []
        if bulk and len(mice) > 1:
            for mouse in group['mice']:
                row = {'mouse_id': mouse.pk, 'controls': [add(role, f'parent_{fingerprint(mouse.pk)[:16]}_', [mouse], True) for role in ROLES]}
                overrides.append(row)
                form.individual_parent_controls.append(row)
        form.parent_groups.append({'key': group['key'], 'mouse_ids': [mouse.pk for mouse in group['mice']],
                                   'count': len(group['mice']), 'controls': controls, 'overrides': overrides})
    form.parent_controls = form.parent_groups[0]['controls']


def clean_parent_controls(form):
    grouped, individual, shared, matches = {}, {}, {}, {}
    requested = form.is_bound and any(
        name.endswith(tuple(f'update_{role}' for role in ROLES)) and forms.BooleanField(required=False).clean(value)
        for name, value in form.data.items()
    )
    if requested and 'parent_group_version' in form.fields and form.cleaned_data.get('parent_group_version') != form._parent_group_version:
        form.parentage_errors = ['Parent groups changed. Reload the editor before changing parents.']
        raise forms.ValidationError(form.parentage_errors)
    changed = False
    for spec in form._parent_specs:
        if spec['toggle'] and not form.cleaned_data.get(spec['toggle']):
            continue
        changed = True
        role = spec['role']
        text = form.cleaned_data.get(spec['text_name'], '').strip()
        submitted_ref = form.cleaned_data.get(spec['ref_name'], '')
        parent_id = None
        if text:
            # Preserve an untouched confirmed link, even after an ID rename.
            if spec['initial_ref'] and submitted_ref == spec['initial_ref'] and text == spec['initial_text']:
                parent_id = spec['initial_ref']
            else:
                if text.casefold() not in matches:
                    matches[text.casefold()] = list(Mouse.objects.filter(pk__iexact=text).values_list('pk', flat=True)[:2])
                candidates = matches[text.casefold()]
                if len(candidates) == 1:
                    parent_id = candidates[0]
                elif submitted_ref == text and (submitted_ref in candidates or Mouse.objects.filter(pk=submitted_ref).exists()):
                    parent_id = submitted_ref
                elif len(candidates) > 1:
                    form.parentage_resolution_warnings.append(f'{role.title()} ID {text} matches multiple animals; kept as external text.')
        pair = {f'{role}_external_id': text, f'{role}_id': parent_id}
        if not spec['members']:
            shared.update(pair)
        for mouse in spec['members']:
            current_pair = dict(pair)
            if parent_id and parent_id == getattr(mouse, f'{role}_id'):
                current_pair[f'{role}_external_id'] = getattr(mouse, f'{role}_external_id')
            target = individual if spec['individual'] else grouped
            target.setdefault(mouse.pk, {}).update(current_pair)
    changes = {pk: {**grouped.get(pk, {}), **individual.get(pk, {})} for pk in grouped.keys() | individual.keys()}
    form.cleaned_data['shared_parentage'], form.cleaned_data['parentage_updates'] = shared, changes
    try:
        if changes:
            form.parentage_warnings = validate_parentage(changes)
    except forms.ValidationError as exc:
        form.parentage_errors = exc.messages
        raise
    return changed
