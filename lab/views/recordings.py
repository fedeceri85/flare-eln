'Recordings views and helpers.'

from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    BigIntegerField, DateField, F, FloatField, OuterRef,
    Q, Subquery, TextField, Value,
)
from django.db.models.fields.json import KeyTextTransform, compile_json_path
from django.db.models.functions import Cast, Lower, NullIf
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from ..forms import RecordingForm, normalize_choice_values
from ..forms.recording_images import RecordingImageUploadForm
from ..recording_images import discard_image_files, save_images
from ..models import Mouse, RecordingField
from ..ui import (
    CROSSING_FILTER_AUTOCOMPLETE,
    RECORDING_FILTER_SPECS,
    apply_filter_specs,
    build_filter_form,
    build_mice_page_filter_specs,
    build_mouse_selector_table,
    build_pagination,
    build_record_count,
    build_recordings_table,
    build_table_page_size_control,
    mice_page_sortable_fields,
    read_filter_values,
    read_table_page_size,
)
from .common import (
    add_recording_url,
    animal_summary_context,
    edit_selected_mice_all_url,
    experiment_for_project_or_404,
    experiment_recordings_url,
    experiments_page_url,
    order_mice_page_queryset,
    recording_mice_query_value,
    safe_return_url,
    select_recording_mouse_url,
    selected_mice_for_ids,
    selected_mice_from_request,
    selected_mouse_ids,
    url_with_query_params,
)


def recording_form_draft_initial(request):
    draft = request.session.get("recording_form_drafts", {}).get(
        request.GET.get("draft"), {}
    )
    if draft.get("path") == request.path:
        return draft.get("initial", {})
    return {}


def discard_recording_form_draft(request):
    drafts = request.session.get("recording_form_drafts", {}).copy()
    if drafts.pop(request.GET.get("draft"), None) is not None:
        request.session["recording_form_drafts"] = drafts


def recording_mouse_selection_redirect(request, form, project, experiment, form_url):
    if request.POST.get("recording_action") != "select_mice":
        return None

    # Store raw widget values without validating an unfinished recording.
    initial = {
        name: field.widget.value_from_datadict(request.POST, request.FILES, name)
        for name, field in form.fields.items()
        if name != "mice"
    }
    drafts = request.session.get("recording_form_drafts", {}).copy()
    token = request.GET.get("draft")
    if token not in drafts or drafts[token].get("path") != request.path:
        token = uuid4().hex
    drafts.pop(token, None)
    drafts[token] = {"path": request.path, "initial": initial}
    # Bound abandoned drafts per session, while keeping the current one.
    drafts = dict(list(drafts.items())[-20:])
    request.session["recording_form_drafts"] = drafts
    form_url = url_with_query_params(
        form_url,
        draft=token,
        mice=recording_mice_query_value(form.selected_mouse_ids()),
        mouse=None,
    )
    return redirect(recording_form_mouse_workflow_urls(
        project, experiment, form_url, form.selected_mouse_ids(),
    )["select_mouse_url"])


def recording_form_mouse_workflow_urls(
    project,
    experiment,
    form_url,
    selected_mouse_ids=None,
):
    return {
        "select_mouse_url": url_with_query_params(
            select_recording_mouse_url(project, experiment),
            return_url=form_url,
            selected_mouse=selected_mouse_ids or [],
        ),
    }


def recording_for_experiment_or_404(experiment, recording_pk):
    return get_object_or_404(
        experiment.recordings.select_related(
            "owner",
            "experiment",
            "recording_type",
        ).prefetch_related("mice", "images__storage_location"),
        pk=recording_pk,
    )


def require_recording_modify_permission(user, recording):
    if not recording.can_be_modified_by(user):
        raise PermissionDenied(
            "You do not have permission to modify this recording."
        )


def positive_int_or_none(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


class NullableJSONTextTransform(KeyTextTransform):
    """Match PostgreSQL's SQL NULL extraction for JSON null on SQLite too."""

    def as_sqlite(self, compiler, connection):
        sql, params = super().as_sqlite(compiler, connection)
        lhs, lhs_params, keys = self.preprocess_lhs(compiler, connection)
        # Inspect the JSON type so a literal string "null" stays sortable text.
        return (
            f"CASE WHEN JSON_TYPE({lhs}, %s) = 'null' THEN NULL ELSE {sql} END",
            (*lhs_params, compile_json_path(keys), *params),
        )


def order_recordings(recordings, recording_type, sort, direction):
    first_animal = Mouse.objects.filter(recordings=OuterRef("pk")).order_by(Lower("mouse_id"), "mouse_id").values("mouse_id")[:1]
    expressions = {
        "recording_date": F("recording_date"),
        "mice": Lower(Subquery(first_animal)),
        "data_paths": Lower(NullIf(KeyTextTransform(0, "data_paths"), Value(""), output_field=TextField())),
        "notes": Lower(NullIf(F("notes"), Value(""), output_field=TextField())),
    }
    if recording_type is not None:
        if recording_type.hide_data_paths_in_table:
            expressions.pop("data_paths")
        if recording_type.hide_notes_in_table:
            expressions.pop("notes")
        for field in recording_type.fields.all():
            if not field.active or not field.display_in_table:
                continue
            value = NullableJSONTextTransform(field.key, "values")
            value = NullIf(value, Value(""), output_field=TextField())
            if field.data_type == RecordingField.INTEGER:
                value = Cast(value, BigIntegerField())
            elif field.data_type == RecordingField.FLOAT:
                value = Cast(value, FloatField())
            elif field.data_type == RecordingField.DATE:
                value = Cast(value, DateField())
            else:
                value = Lower(value)
            expressions[f"field:{field.key}"] = value
    if sort not in expressions:
        sort, direction = "recording_date", "desc"
    if direction not in {"asc", "desc"}:
        direction = "desc"
    recordings = recordings.annotate(_recording_sort_value=expressions[sort])
    ordering = F("_recording_sort_value").asc(nulls_last=True) if direction == "asc" else F("_recording_sort_value").desc(nulls_last=True)
    return recordings.order_by(ordering, "-recording_id"), sort, direction


def selected_recording_page_number(recordings, selected_recording_id, per_page, direction):
    selected = (
        recordings
        .filter(pk=selected_recording_id)
        .values("_recording_sort_value", "recording_id")
        .first()
    )

    if selected is None:
        return None

    value = selected["_recording_sort_value"]
    tied_before = Q(_recording_sort_value=value, recording_id__gt=selected["recording_id"])
    if value is None:
        before = Q(_recording_sort_value__isnull=False)
    else:
        comparison = "lt" if direction == "asc" else "gt"
        before = Q(**{f"_recording_sort_value__{comparison}": value})
    earlier_rows = recordings.filter(before | tied_before).count()

    return earlier_rows // per_page + 1


def recording_field_filter_name(recording_field):
    return f"field_{recording_field.pk}"


def recording_field_filter_label(recording_field):
    if recording_field.units:
        return f"{recording_field.label} ({recording_field.units})"

    return recording_field.label


def recording_field_filter_specs(recording_type):
    if recording_type is None:
        return []

    filter_specs = []
    recording_fields = (
        recording_type.fields
        .filter(active=True, filterable=True)
        .order_by("order", "label")
    )

    for recording_field in recording_fields:
        spec = {
            "name": recording_field_filter_name(recording_field),
            "label": recording_field_filter_label(recording_field),
            "recording_field": recording_field,
        }

        if recording_field.data_type == RecordingField.CHOICE:
            spec.update(
                {
                    "type": "select",
                    "choices": [
                        ("", "All"),
                        *normalize_choice_values(recording_field.choices),
                    ],
                }
            )
        elif recording_field.data_type == RecordingField.BOOLEAN:
            spec.update(
                {
                    "type": "select",
                    "choices": [
                        ("", "All"),
                        ("true", "Yes"),
                        ("false", "No"),
                    ],
                }
            )
        elif recording_field.data_type == RecordingField.DATE:
            spec["type"] = "date"
        else:
            spec.update(
                {
                    "type": "text",
                    "placeholder": f"Type {recording_field.label.lower()}",
                }
            )

            if recording_field.key == "crossing":
                spec["autocomplete"] = CROSSING_FILTER_AUTOCOMPLETE

        filter_specs.append(spec)

    return filter_specs


def parse_recording_field_filter_value(recording_field, value):
    if recording_field.data_type == RecordingField.BOOLEAN:
        return value == "true", True

    if recording_field.data_type == RecordingField.INTEGER:
        try:
            return int(value), True
        except ValueError:
            return None, False

    if recording_field.data_type == RecordingField.FLOAT:
        try:
            return float(value), True
        except ValueError:
            return None, False

    return value, True


def apply_recording_field_filter_specs(queryset, filter_specs, values):
    for spec in filter_specs:
        recording_field = spec["recording_field"]
        value = values.get(spec["name"])

        if not value:
            continue

        lookup_root = f"values__{recording_field.key}"

        if recording_field.data_type == RecordingField.TEXT:
            queryset = queryset.filter(**{f"{lookup_root}__icontains": value})
            continue

        typed_value, is_valid = parse_recording_field_filter_value(
            recording_field,
            value,
        )

        if not is_valid:
            return queryset.none()

        queryset = queryset.filter(**{lookup_root: typed_value})

    return queryset


@require_safe
def recording_animals(request, project_pk, experiment_pk, recording_pk):
    project, experiment = experiment_for_project_or_404(project_pk, experiment_pk)
    recording = recording_for_experiment_or_404(experiment, recording_pk)
    mice = list(recording.mice.select_related(
        "protocol", "added_by", "crossing_definition__species",
    ).order_by("mouse_id"))
    return_url = safe_return_url(request, experiment_recordings_url(project, experiment))
    return render(request, "lab/animal_summary.html", {
        **animal_summary_context(
            request, mice, summary_url=url_with_query_params(request.path, return_url=return_url),
            edit_url=edit_selected_mice_all_url(),
        ),
        "project": project,
        "experiment": experiment,
        "recording": recording,
        "return_url": return_url,
    })


def recordings_page(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    dynamic_filter_specs = recording_field_filter_specs(experiment.recording_type)
    filter_specs = [*RECORDING_FILTER_SPECS, *dynamic_filter_specs]
    filter_values = read_filter_values(request.GET, filter_specs)
    recordings = (
        experiment.recordings
        .select_related("owner", "experiment", "recording_type")
        .prefetch_related("mice")
        .all()
    )
    recordings = apply_filter_specs(
        recordings,
        RECORDING_FILTER_SPECS,
        filter_values,
    )
    recordings = apply_recording_field_filter_specs(
        recordings,
        dynamic_filter_specs,
        filter_values,
    )

    if filter_values.get("date_from"):
        recordings = recordings.filter(
            recording_date__gte=filter_values["date_from"],
        )

    if filter_values.get("date_to"):
        recordings = recordings.filter(
            recording_date__lte=filter_values["date_to"],
        )

    recordings, sort, direction = order_recordings(
        recordings.distinct(), experiment.recording_type,
        request.GET.get("sort", "recording_date"), request.GET.get("direction", "desc"),
    )

    page_size = read_table_page_size(request.GET)
    paginator = Paginator(recordings, page_size)
    selected_recording_id = positive_int_or_none(
        request.GET.get("selected_recording")
    )
    page_number = request.GET.get("page")

    if selected_recording_id and not page_number:
        page_number = selected_recording_page_number(
            recordings,
            selected_recording_id,
            paginator.per_page,
            direction,
        )

    page_obj = paginator.get_page(page_number)
    query_params = request.GET.copy()
    query_params.pop("page", None)
    recordings_url = experiment_recordings_url(project, experiment)
    experiments_url = (
        reverse("lab:services_page")
        if project.is_service
        else experiments_page_url(project)
    )
    experiments_link_label = "Services" if project.is_service else "Experiment Page"

    return render(
        request,
        "lab/recordings_page.html",
        {
            "project": project,
            "experiment": experiment,
            "has_active_filters": any(filter_values.values()),
            "table": build_recordings_table(
                page_obj,
                experiment.recording_type,
                request.user,
                selected_recording_id=selected_recording_id,
                current_sort=sort, current_direction=direction, query_params=query_params,
            ),
            "filter_form": build_filter_form(
                filter_specs,
                filter_values,
                hidden_fields=[
                    {"name": "per_page", "value": page_size},
                    {"name": "sort", "value": sort},
                    {"name": "direction", "value": direction},
                ],
                clear_url=recordings_url,
            ),
            "record_count": build_record_count(page_obj, "recording"),
            "pagination": build_pagination(page_obj, query_params),
            "page_size_control": build_table_page_size_control(
                request.GET,
                page_size,
            ),
            "recordings_url": recordings_url,
            "add_recording_url": reverse(
                "lab:add_recording",
                args=[project.pk, experiment.pk],
            ),
            "experiments_url": experiments_url,
            "experiments_link_label": experiments_link_label,
        },
    )


def select_recording_mouse(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    fallback_url = add_recording_url(project, experiment)
    return_url = safe_return_url(request, fallback_url)
    selected_mouse_id_values = selected_mouse_ids(request)

    if request.method == "POST":
        return redirect(
            url_with_query_params(
                return_url,
                mice=recording_mice_query_value(selected_mouse_id_values),
                mouse=None,
            )
        )

    sort = request.GET.get("sort", "date_of_birth")
    direction = request.GET.get("direction", "desc")

    if direction not in {"asc", "desc"}:
        direction = "desc"

    if sort not in mice_page_sortable_fields():
        sort = "date_of_birth"

    filter_specs = build_mice_page_filter_specs()
    filter_values = read_filter_values(request.GET, filter_specs)
    mice = Mouse.objects.select_related("protocol")
    mice = apply_filter_specs(mice, filter_specs, filter_values)
    mice = order_mice_page_queryset(mice, sort, direction)

    page_size = read_table_page_size(request.GET)
    paginator = Paginator(mice, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    selector_url = select_recording_mouse_url(project, experiment)
    visible_mouse_ids = {
        mouse.pk
        for mouse in page_obj
    }
    hidden_selected_mouse_ids = [
        mouse_id
        for mouse_id in selected_mouse_id_values
        if mouse_id not in visible_mouse_ids
    ]
    selected_mice = []

    if selected_mouse_id_values:
        try:
            selected_mice = selected_mice_for_ids(selected_mouse_id_values)
        except Mouse.DoesNotExist:
            selected_mice = []

    selected_return_url = url_with_query_params(
        return_url,
        mice=recording_mice_query_value(selected_mouse_id_values),
        mouse=None,
    )

    return render(
        request,
        "lab/select_recording_mouse.html",
        {
            "project": project,
            "experiment": experiment,
            "table": build_mouse_selector_table(
                page_obj,
                sort,
                direction,
                query_params,
                selected_mouse_id_values,
            ),
            "filter_form": build_filter_form(
                filter_specs,
                filter_values,
                hidden_fields=[
                    {"name": "return_url", "value": return_url},
                    *[
                        {"name": "selected_mouse", "value": mouse_id}
                        for mouse_id in selected_mouse_id_values
                    ],
                    {"name": "sort", "value": sort},
                    {"name": "direction", "value": direction},
                    {"name": "per_page", "value": page_size},
                ],
                clear_url=url_with_query_params(
                    selector_url,
                    return_url=return_url,
                ),
            ),
            "record_count": build_record_count(page_obj, "animal"),
            "pagination": build_pagination(page_obj, query_params),
            "page_size_control": build_table_page_size_control(
                request.GET,
                page_size,
            ),
            "return_url": return_url,
            "selected_mice": selected_mice,
            "hidden_selected_mouse_ids": hidden_selected_mouse_ids,
            "add_animals_url": url_with_query_params(
                reverse("lab:add_litter_all"),
                return_url=selected_return_url,
                return_with_mouse="1",
            ),
        },
    )


def add_recording(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    recordings_url = experiment_recordings_url(project, experiment)
    return_url = safe_return_url(request, recordings_url)
    form_url = (
        request.get_full_path()
        if request.method == "GET"
        else url_with_query_params(
            add_recording_url(project, experiment),
            return_url=return_url,
        )
    )
    selected_mice = (
        selected_mice_from_request(request)
        if request.method == "GET"
        else []
    )

    if experiment.recording_type_id is None:
        messages.error(
            request,
            "Select an experiment type before adding recordings.",
        )
        return redirect(recordings_url)

    media_form = RecordingImageUploadForm(
        request.POST if request.method == "POST" else None,
        request.FILES if request.method == "POST" else None,
        prefix="media", optional=True,
    )
    if request.method == "POST":
        form = RecordingForm(
            request.POST,
            experiment=experiment,
            owner=request.user,
        )

        selection_redirect = recording_mouse_selection_redirect(
            request, form, project, experiment, form_url,
        )
        if selection_redirect is not None:
            return selection_redirect

        if form.is_valid() and media_form.is_valid():
            images = []
            try:
                with transaction.atomic():
                    recording = form.save()
                    if media_form.cleaned_data["images"]:
                        images = save_images(recording, media_form.cleaned_data["images"])
            except (ValidationError, OSError, IntegrityError) as exc:
                discard_image_files(images)
                form.add_error(None, exc if isinstance(exc, ValidationError) else "The recording or its images could not be saved. Check image storage and try again.")
            else:
                discard_recording_form_draft(request)
                messages.success(request, f"Recording {recording.pk} was created successfully.")
                return redirect(return_url)
    else:
        form = RecordingForm(
            experiment=experiment,
            owner=request.user,
            selected_mice=selected_mice,
            initial=recording_form_draft_initial(request),
        )

    mouse_workflow_urls = recording_form_mouse_workflow_urls(
        project,
        experiment,
        form_url,
        form.selected_mouse_ids(),
    )

    return render(
        request,
        "lab/add_recording.html",
        {
            "form": form,
            "project": project,
            "experiment": experiment,
            "media_form": media_form,
            "return_url": return_url,
            "selected_mice": form.selected_mice_for_display(),
            **mouse_workflow_urls,
        },
    )



def edit_recording(request, project_pk, experiment_pk, recording_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    recording = recording_for_experiment_or_404(experiment, recording_pk)
    require_recording_modify_permission(request.user, recording)
    recordings_url = experiment_recordings_url(project, experiment)
    return_url = safe_return_url(request, recordings_url)
    edit_url = reverse(
        "lab:edit_recording",
        args=[project.pk, experiment.pk, recording.pk],
    )
    form_url = (
        request.get_full_path()
        if request.method == "GET"
        else url_with_query_params(edit_url, return_url=return_url)
    )
    selected_mice = (
        selected_mice_from_request(request)
        if request.method == "GET"
        else []
    )

    if request.method == "POST":
        form = RecordingForm(
            request.POST,
            experiment=experiment,
            instance=recording,
        )

        selection_redirect = recording_mouse_selection_redirect(
            request, form, project, experiment, form_url,
        )
        if selection_redirect is not None:
            return selection_redirect

        if form.is_valid():
            recording = form.save()
            discard_recording_form_draft(request)
            messages.success(
                request,
                f"Recording {recording.pk} was updated successfully.",
            )
            return redirect(return_url)
    else:
        form = RecordingForm(
            experiment=experiment,
            instance=recording,
            selected_mice=selected_mice,
            initial=recording_form_draft_initial(request),
        )

    mouse_workflow_urls = recording_form_mouse_workflow_urls(
        project,
        experiment,
        form_url,
        form.selected_mouse_ids(),
    )

    return render(
        request,
        "lab/edit_recording.html",
        {
            "form": form,
            "project": project,
            "experiment": experiment,
            "recording": recording,
            "return_url": return_url,
            "selected_mice": form.selected_mice_for_display(),
            **mouse_workflow_urls,
        },
    )


@require_POST
def delete_selected_recordings(request, project_pk, experiment_pk):
    project, experiment = experiment_for_project_or_404(project_pk, experiment_pk)
    return_url = safe_return_url(request, experiment_recordings_url(project, experiment))
    selected = request.POST.getlist("selected_recording")
    if not selected:
        messages.error(request, "Select at least one recording to delete.")
        return redirect(return_url)
    ids = {positive_int_or_none(value) for value in selected}
    if None in ids:
        raise Http404("Invalid recording selection.")
    with transaction.atomic():
        recordings = list(
            experiment.recordings.select_for_update().filter(pk__in=ids)
            .prefetch_related("mice").order_by("-recording_date", "-recording_id")
        )
        if len(recordings) != len(ids):
            raise Http404("One or more selected recordings no longer belong to this experiment.")
        for recording in recordings:
            require_recording_modify_permission(request.user, recording)
        if request.POST.get("confirm_delete") == "yes":
            count = len(recordings)
            for recording in recordings:
                recording.delete()
            messages.success(request, f"Deleted {count} recording{'s' if count != 1 else ''}.")
            return redirect(return_url)
    return render(request, "lab/delete_selected_recordings.html", {
        "project": project, "experiment": experiment,
        "recordings": recordings, "return_url": return_url,
    })


def delete_recording(request, project_pk, experiment_pk, recording_pk):
    project, experiment = experiment_for_project_or_404(
        project_pk,
        experiment_pk,
    )
    recording = recording_for_experiment_or_404(experiment, recording_pk)
    require_recording_modify_permission(request.user, recording)
    recordings_url = experiment_recordings_url(project, experiment)
    return_url = safe_return_url(request, recordings_url)

    if request.method == "POST":
        recording_pk = recording.pk
        recording.delete()
        messages.success(
            request,
            f"Recording {recording_pk} was deleted successfully.",
        )
        return redirect(return_url)

    return render(
        request,
        "lab/delete_recording.html",
        {
            "project": project,
            "experiment": experiment,
            "recording": recording,
            "return_url": return_url,
        },
    )
