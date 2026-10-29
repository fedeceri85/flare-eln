"""Reuse the oldest code for an animal selection; keep older aliases working."""
from hashlib import sha256

from django.db import migrations


def canonicalize_animal_codes(apps, schema_editor):
    SharedLink = apps.get_model('lab', 'SharedLink')
    links = SharedLink.objects.using(schema_editor.connection.alias).filter(kind='m')
    # Release the old tab-specific keys before assigning selection-only keys.
    links.update(selection_key=None)
    seen = set()
    for link in links.order_by('created_at', 'pk').iterator():
        identities = sorted(str(pk) for pk in link.animals.values_list('pk', flat=True))
        key = sha256(','.join(identities).encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        link.selection_key = key
        link.animal_tab = 'shared' if len(identities) == 1 else 'individual'
        link.save(update_fields=['selection_key', 'animal_tab'])


class Migration(migrations.Migration):
    dependencies = [('lab', '0064_shared_links')]
    operations = [migrations.RunPython(canonicalize_animal_codes, migrations.RunPython.noop)]
