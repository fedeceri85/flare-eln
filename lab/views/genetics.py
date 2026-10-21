'Genetics views and helpers.'

from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from ..forms import (
    BulkMouseLineCreateForm,
    CrossingCreateForm,
    crossing_choice_label,
    genotype_field_name,
)
from ..models import (
    Crossing,
    DEFAULT_SPECIES_NAME,
    Mouse,
    MouseLine,
    Species,
    genotype_category_choices,
)


def lines_crossings_url(species=None):
    url = reverse("lab:lines_crossings_page")

    if species is None:
        return url

    return f"{url}?{urlencode({'species': species.pk})}"


def ensure_default_species():
    species, _ = Species.objects.get_or_create(
        name=DEFAULT_SPECIES_NAME,
        defaults={"active": True},
    )
    return species


def selected_species_from_request(request):
    species_id = request.POST.get("species") or request.GET.get("species")

    if species_id:
        species = Species.objects.filter(pk=species_id).first()

        if species is not None:
            return species

    return (
        Species.objects.filter(name=DEFAULT_SPECIES_NAME).first()
        or Species.objects.order_by("name").first()
        or ensure_default_species()
    )


def count_label(count, singular, plural=None):
    if count == 1:
        return f"1 {singular}"

    return f"{count} {plural or singular + 's'}"


def delete_line_from_request(request):
    line = get_object_or_404(
        MouseLine.objects.select_related("species"),
        pk=request.POST.get("line_id"),
    )
    species = line.species
    crossings = list(
        line.crossings
        .prefetch_related("lines")
        .order_by("display_name", "crossing_id")
    )
    deletable_single_crossings = []
    blocking_crossings = []

    for crossing in crossings:
        crossing_lines = list(crossing.lines.all())

        if (
            len(crossing_lines) == 1
            and crossing_lines[0].pk == line.pk
            and not crossing.mice.exists()
        ):
            deletable_single_crossings.append(crossing)
        else:
            blocking_crossings.append(crossing)

    genotype_mouse_count = (
        Mouse.objects
        .filter(genotype_calls__mouse_line=line)
        .distinct()
        .count()
    )
    linked_reasons = []

    if blocking_crossings:
        linked_reasons.append(
            count_label(len(blocking_crossings), "crossing")
        )

    if genotype_mouse_count:
        linked_reasons.append(
            count_label(genotype_mouse_count, "mouse genotype record")
        )

    if linked_reasons:
        messages.error(
            request,
            (
                f"Line {line.name} cannot be deleted because it is linked to "
                f"{' and '.join(linked_reasons)}."
            ),
        )
        return redirect(lines_crossings_url(species))

    try:
        for crossing in deletable_single_crossings:
            crossing.delete()

        line.delete()
    except ProtectedError:
        messages.error(
            request,
            f"Line {line.name} could not be deleted because it is still linked.",
        )
    else:
        messages.success(
            request,
            f"Line {line.name} was deleted.",
        )

    return redirect(lines_crossings_url(species))


def delete_crossing_from_request(request):
    crossing = get_object_or_404(
        Crossing.objects.select_related("species"),
        pk=request.POST.get("crossing_id"),
    )
    species = crossing.species
    linked_mouse_count = crossing.mice.count()

    if linked_mouse_count:
        linked_mouse_label = count_label(linked_mouse_count, "mouse")
        linked_mouse_verb = "uses" if linked_mouse_count == 1 else "use"
        messages.error(
            request,
            (
                f"Crossing {crossing.display_name} cannot be deleted because "
                f"{linked_mouse_label} {linked_mouse_verb} it."
            ),
        )
        return redirect(lines_crossings_url(species))

    try:
        crossing.delete()
    except ProtectedError:
        messages.error(
            request,
            (
                f"Crossing {crossing.display_name} could not be deleted "
                "because it is still linked."
            ),
        )
    else:
        messages.success(
            request,
            f"Crossing {crossing.display_name} was deleted.",
        )

    return redirect(lines_crossings_url(species))


def lines_crossings_page(request):
    if not request.user.is_staff:
        raise PermissionDenied

    selected_species = selected_species_from_request(request)
    line_form = BulkMouseLineCreateForm(initial={"species": selected_species})
    crossing_form = CrossingCreateForm(initial={"species": selected_species})

    if request.method == "POST":
        if request.POST.get("action") == "add_lines":
            line_form = BulkMouseLineCreateForm(request.POST)

            if line_form.is_valid():
                created_lines, existing_lines = line_form.save()
                selected_species = line_form.cleaned_data["species"]
                messages.success(
                    request,
                    (
                        f"Created {len(created_lines)} line"
                        f"{'' if len(created_lines) == 1 else 's'}; "
                        f"skipped {len(existing_lines)} duplicate"
                        f"{'' if len(existing_lines) == 1 else 's'}."
                    ),
                )
                return redirect(lines_crossings_url(selected_species))
        elif request.POST.get("action") == "create_crossing":
            crossing_form = CrossingCreateForm(request.POST)

            if crossing_form.is_valid():
                crossing, created = crossing_form.save()
                selected_species = crossing.species

                if created:
                    messages.success(
                        request,
                        f"Created crossing {crossing.display_name}.",
                    )
                else:
                    messages.info(
                        request,
                        f"Crossing {crossing.display_name} already exists.",
                    )

                return redirect(lines_crossings_url(selected_species))
        elif request.POST.get("action") == "delete_line":
            return delete_line_from_request(request)
        elif request.POST.get("action") == "delete_crossing":
            return delete_crossing_from_request(request)

    # These independent forms both have a species field. Keep their labels and
    # validation links unique without changing submitted field names.
    crossing_form.auto_id = "crossing_%s"
    species_options = Species.objects.order_by("name")
    lines = (
        MouseLine.objects
        .filter(species=selected_species)
        .order_by("name", "mouse_line_id")
    )
    crossings = (
        Crossing.objects
        .filter(species=selected_species)
        .prefetch_related("lines")
        .order_by("display_name", "crossing_id")
    )

    return render(
        request,
        "lab/lines_crossings_page.html",
        {
            "selected_species": selected_species,
            "species_options": species_options,
            "line_form": line_form,
            "crossing_form": crossing_form,
            "lines": lines,
            "crossings": crossings,
            "line_lookup_url": reverse("lab:line_lookup"),
        },
    )


def line_lookup(request):
    if not request.user.is_staff:
        raise PermissionDenied

    species_id = request.GET.get("species")
    query = (request.GET.get("q") or "").strip()
    lines = MouseLine.objects.filter(active=True)

    if species_id:
        lines = lines.filter(species_id=species_id)

    if query:
        lines = lines.filter(name__icontains=query)

    lines = lines.select_related("species").order_by("name", "mouse_line_id")[:20]

    return JsonResponse(
        {
            "results": [
                {
                    "id": line.pk,
                    "name": line.name,
                    "label": f"{line.name} ({line.species.name})",
                    "species_id": line.species_id,
                }
                for line in lines
            ]
        }
    )


def crossing_lookup(request):
    query = (request.GET.get("q") or "").strip()
    crossings = Crossing.objects.filter(active=True).select_related("species")

    if query:
        crossings = crossings.filter(
            Q(display_name__icontains=query)
            | Q(species__name__icontains=query)
        )

    crossings = crossings.order_by("species__name", "display_name", "crossing_id")[:20]

    return JsonResponse(
        {
            "results": [
                {
                    "id": crossing.pk,
                    "label": crossing_choice_label(crossing),
                    "display_name": crossing.display_name,
                    "species": crossing.species.name,
                }
                for crossing in crossings
            ]
        }
    )


def crossing_genotype_fields(request):
    crossing = get_object_or_404(
        Crossing.objects.prefetch_related("lines"),
        pk=request.GET.get("crossing"),
        active=True,
    )
    mouse_id = request.GET.get("mouse")
    existing_calls = {}

    if mouse_id:
        mouse = Mouse.objects.filter(pk=mouse_id).first()

        if mouse is not None:
            existing_calls = {
                genotype.mouse_line_id: genotype.zygosity
                for genotype in mouse.genotype_calls.all()
            }

    existing_uncertainty = {}
    if mouse_id and mouse is not None:
        existing_uncertainty = dict(mouse.genotype_calls.values_list(
            "mouse_line_id", "is_uncertain",
        ))
    lines = crossing.ordered_lines()

    return JsonResponse(
        {
            "crossing": {
                "id": crossing.pk,
                "label": crossing_choice_label(crossing),
            },
            "choices": [
                {"value": value, "label": label}
                for value, label in genotype_category_choices(existing_calls.values())
            ],
            "lines": [
                {
                    "id": line.pk,
                    "name": line.name,
                    "field_name": genotype_field_name(line),
                    "value": existing_calls.get(line.pk, "unknown"),
                    "uncertainty_field_name": f"{genotype_field_name(line)}_uncertain",
                    "is_uncertain": existing_uncertainty.get(line.pk, False),
                }
                for line in lines
            ],
        }
    )
