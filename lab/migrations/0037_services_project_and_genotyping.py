from django.db import migrations, models


SERVICES_PROJECT_NAME = "Services"
SERVICES_PROJECT_DESCRIPTION = "Lab-wide service experiments and shared records."

GENOTYPING_RECORDING_TYPE = {
    "name": "Genotyping",
    "plural_name": "Genotyping records",
    "slug": "genotyping",
    "description": "Gel image references and genotyping metadata.",
}

GENOTYPING_FIELDS = [
    {
        "key": "lab_member",
        "label": "Lab member",
        "data_type": "text",
        "required": False,
        "display_in_table": True,
        "filterable": False,
        "order": 10,
    },
    {
        "key": "crossing",
        "label": "Crossing",
        "data_type": "text",
        "required": False,
        "display_in_table": True,
        "filterable": True,
        "order": 20,
    },
]


def ensure_services_project(apps):
    Project = apps.get_model("lab", "Project")

    project = (
        Project.objects
        .filter(name=SERVICES_PROJECT_NAME)
        .order_by("project_id")
        .first()
    )

    if project is None:
        return Project.objects.create(
            name=SERVICES_PROJECT_NAME,
            description=SERVICES_PROJECT_DESCRIPTION,
            active=True,
            is_service=True,
        )

    updates = []

    if not project.is_service:
        project.is_service = True
        updates.append("is_service")

    if not project.active:
        project.active = True
        updates.append("active")

    if not project.description:
        project.description = SERVICES_PROJECT_DESCRIPTION
        updates.append("description")

    if updates:
        project.save(update_fields=updates)

    return project


def create_genotyping_setup(apps, schema_editor):
    Experiment = apps.get_model("lab", "Experiment")
    RecordingField = apps.get_model("lab", "RecordingField")
    RecordingType = apps.get_model("lab", "RecordingType")

    services_project = ensure_services_project(apps)
    recording_type, _ = RecordingType.objects.update_or_create(
        slug=GENOTYPING_RECORDING_TYPE["slug"],
        defaults={
            "name": GENOTYPING_RECORDING_TYPE["name"],
            "plural_name": GENOTYPING_RECORDING_TYPE["plural_name"],
            "description": GENOTYPING_RECORDING_TYPE["description"],
            "active": True,
        },
    )

    for field_spec in GENOTYPING_FIELDS:
        RecordingField.objects.update_or_create(
            recording_type=recording_type,
            key=field_spec["key"],
            defaults={
                "label": field_spec["label"],
                "data_type": field_spec["data_type"],
                "required": field_spec["required"],
                "choices": [],
                "units": "",
                "default_value": None,
                "active": True,
                "display_in_table": field_spec["display_in_table"],
                "filterable": field_spec["filterable"],
                "order": field_spec["order"],
            },
        )

    Experiment.objects.update_or_create(
        project=services_project,
        name="Genotyping",
        defaults={
            "description": (
                "Shared genotyping records and gel image references."
            ),
            "recording_type": recording_type,
            "archived": False,
        },
    )


def remove_genotyping_setup(apps, schema_editor):
    Experiment = apps.get_model("lab", "Experiment")
    Project = apps.get_model("lab", "Project")
    Recording = apps.get_model("lab", "Recording")
    RecordingType = apps.get_model("lab", "RecordingType")

    genotyping_type = RecordingType.objects.filter(
        slug=GENOTYPING_RECORDING_TYPE["slug"],
    ).first()

    if genotyping_type is None:
        return

    genotyping_experiments = Experiment.objects.filter(
        project__name=SERVICES_PROJECT_NAME,
        name="Genotyping",
        recording_type_id=genotyping_type.pk,
    )
    genotyping_experiment_ids = list(
        genotyping_experiments.values_list("pk", flat=True)
    )

    has_recordings = Recording.objects.filter(
        experiment_id__in=genotyping_experiment_ids,
    ).exists()

    if not has_recordings:
        Experiment.objects.filter(
            pk__in=genotyping_experiment_ids,
        ).update(recording_type_id=None)

    type_in_use = (
        Recording.objects.filter(recording_type_id=genotyping_type.pk).exists()
        or Experiment.objects.filter(
            recording_type_id=genotyping_type.pk,
        ).exists()
    )

    if not type_in_use:
        genotyping_type.delete()

    Project.objects.filter(
        name=SERVICES_PROJECT_NAME,
        is_service=True,
        experiments__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0036_recording_mice"),
    ]

    operations = [
        migrations.AddField(
            model_name="project",
            name="is_service",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            create_genotyping_setup,
            reverse_code=remove_genotyping_setup,
        ),
    ]
