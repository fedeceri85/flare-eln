'Genetics views and helpers.'

from urllib.parse import urlencode
import string
import unicodedata

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Prefetch
from django.db.models.functions import Lower
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse, Http404
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
    CrossingLine,
    DEFAULT_SPECIES_NAME,
    Mouse,
    MouseLine,
    Species,
    genotype_category_choices,
)

from ..forms.genetics import LineRenameForm, CrossingOrderForm
from ..genetics_catalogue import rename_line, reorder_crossing
from ..ui import build_pagination


def lines_crossings_url(species=None, request=None, tab=None):
    params = request.GET.copy() if request else {}
    for key in ("edit_line", "edit_crossing", "panel"):
        params.pop(key, None)
    if species is not None:
        params["species"] = species.pk
    if tab is not None:
        params["tab"] = tab
    url = reverse("lab:lines_crossings_page")
    return url + ("?" + urlencode(params) if params else "")


def catalogue_record(model, value):
    try:
        pk = int(value)
        if not 0 < pk < 2**63:
            raise ValueError
    except (TypeError, ValueError):
        raise Http404("Record not found")
    return get_object_or_404(model, pk=pk)


def ensure_default_species():
    species, _ = Species.objects.get_or_create(
        name=DEFAULT_SPECIES_NAME,
        defaults={"active": True},
    )
    return species


def selected_species_from_request(request):
    species_id = request.POST.get("species") or request.GET.get("species")

    if species_id:
        try:
            pk = int(species_id)
            species = Species.objects.filter(pk=pk).first() if 0 < pk < 2**63 else None
        except (ValueError, TypeError):
            species = None
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
            count_label(genotype_mouse_count, "animal genotype record")
        )

    if linked_reasons:
        messages.error(
            request,
            (
                f"Line {line.name} cannot be deleted because it is linked to "
                f"{' and '.join(linked_reasons)}."
            ),
        )
        return redirect(lines_crossings_url(species, request, "lines"))

    try:
        with transaction.atomic():
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

    return redirect(lines_crossings_url(species, request, "lines"))


def delete_crossing_from_request(request):
    crossing = get_object_or_404(
        Crossing.objects.select_related("species"),
        pk=request.POST.get("crossing_id"),
    )
    species = crossing.species
    linked_mouse_count = crossing.mice.count()

    if linked_mouse_count:
        linked_mouse_label = count_label(linked_mouse_count, "animal")
        linked_mouse_verb = "uses" if linked_mouse_count == 1 else "use"
        messages.error(
            request,
            (
                f"Crossing {crossing.display_name} cannot be deleted because "
                f"{linked_mouse_label} {linked_mouse_verb} it."
            ),
        )
        return redirect(lines_crossings_url(species, request, "crossings"))

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

    return redirect(lines_crossings_url(species, request, "crossings"))


def catalogue_letter(name):
    first = unicodedata.normalize("NFKD", name.lstrip()[:1]).upper()[:1]
    if first in string.ascii_uppercase and first:
        return first
    if first.isdecimal():
        return "0-9"
    return "other"


def lines_crossings_page(request):
    if not request.user.is_staff:
        raise PermissionDenied
    species = selected_species_from_request(request)
    tab = request.GET.get("tab", "lines")
    tab = tab if tab in {"lines", "crossings"} else "lines"
    action = request.POST.get("action") if request.method == "POST" else None
    editor = None
    editor_form = None
    edit_kind = None
    if action in {"rename_line", "delete_line"} or (request.method == "GET" and request.GET.get("edit_line")):
        editor = catalogue_record(MouseLine, request.POST.get("line_id") if action else request.GET["edit_line"])
        species, tab, edit_kind = editor.species, "lines", "line"
    elif action in {"reorder_crossing", "delete_crossing"} or (request.method == "GET" and request.GET.get("edit_crossing")):
        editor = catalogue_record(Crossing, request.POST.get("crossing_id") if action else request.GET["edit_crossing"])
        species, tab, edit_kind = editor.species, "crossings", "crossing"
    line_form = BulkMouseLineCreateForm(initial={"species": species})
    crossing_form = CrossingCreateForm(initial={"species": species}, auto_id="crossing_%s")
    editor_lines = editor.ordered_lines() if edit_kind == "crossing" else []
    if editor:
        editor_form = (LineRenameForm(initial={"name": editor.name, "aliases": editor.aliases}) if edit_kind == "line" else
                       CrossingOrderForm(initial={"selected_lines": ",".join(str(line.pk) for line in editor_lines)}))
    if request.method == "POST":
        if action not in {"add_lines", "create_crossing", "rename_line", "reorder_crossing", "delete_line", "delete_crossing"}:
            raise PermissionDenied("Unknown catalogue operation.")
        # Serialize catalogue mutations per species, including automatic single
        # crossings, label refreshes and deletions. Never lock by a forged ID.
        with transaction.atomic():
            if action in {"add_lines", "create_crossing"}:
                form_class = BulkMouseLineCreateForm if action == "add_lines" else CrossingCreateForm
                form = form_class(request.POST)
                if form.is_valid():
                    species = form.cleaned_data["species"]
                    Species.objects.select_for_update().get(pk=species.pk)
                    # Refresh choice objects after waiting for a rename/delete.
                    form = form_class(request.POST)
                    if form.is_valid():
                        if action == "add_lines":
                            created, existing = form.save()
                            messages.success(request, f"Created {len(created)} line{'s' if len(created) != 1 else ''}; skipped {len(existing)} duplicate{'s' if len(existing) != 1 else ''}.")
                        else:
                            crossing, created = form.save()
                            (messages.success if created else messages.info)(request, f"{'Created crossing' if created else 'Crossing already exists:'} {crossing.display_name}.")
                        return redirect(lines_crossings_url(species, request, "lines" if action == "add_lines" else "crossings"))
                if action == "add_lines":
                    line_form, tab = form, "lines"
                else:
                    crossing_form, tab = form, "crossings"
                    crossing_form.auto_id = "crossing_%s"
            else:
                Species.objects.select_for_update().get(pk=species.pk)
                if action == "delete_line":
                    return delete_line_from_request(request)
                if action == "delete_crossing":
                    return delete_crossing_from_request(request)
                editor_form = LineRenameForm(request.POST) if edit_kind == "line" else CrossingOrderForm(request.POST)
                if editor_form.is_valid():
                    try:
                        if edit_kind == "line":
                            rename_line(editor, editor_form.cleaned_data["name"], editor_form.cleaned_data["aliases"])
                        else:
                            ids = editor_form.cleaned_data["selected_lines"]
                            existing_ids = {line.pk for line in editor_lines}
                            if len(ids) != len(existing_ids) or set(ids) != existing_ids:
                                raise ValidationError("Only the order of the existing lines can change. Do not add, remove or repeat lines.")
                            if request.POST.get("move"):
                                try:
                                    raw_pk, direction = request.POST["move"].split(":")
                                    index = ids.index(int(raw_pk))
                                    if direction not in {"up", "down"}:
                                        raise ValueError
                                    target = index + (-1 if direction == "up" else 1)
                                    if 0 <= target < len(ids):
                                        ids[index], ids[target] = ids[target], ids[index]
                                except (ValueError, TypeError):
                                    raise ValidationError("Choose a valid line to move.")
                                by_id = {line.pk: line for line in editor_lines}
                                editor_lines = [by_id[pk] for pk in ids]
                                editor_form = CrossingOrderForm(initial={"selected_lines": ",".join(map(str, ids))})
                            else:
                                reorder_crossing(editor, ids)
                        if not request.POST.get("move"):
                            messages.success(request, "Line renamed." if edit_kind == "line" else "Crossing order updated.")
                            return redirect(lines_crossings_url(species, request, tab))
                    except ValidationError as exc:
                        editor_form.add_error(None, exc)
                    except IntegrityError:
                        editor_form.add_error(None, "The catalogue changed or this name is already in use. Reload and try again.")
    query = request.GET.get("q", "").strip()
    lines = MouseLine.objects.filter(species=species, name__icontains=query).order_by(Lower("name"), "pk")
    crossings = Crossing.objects.filter(species=species, display_name__icontains=query).prefetch_related(
        "lines", Prefetch("crossing_lines", queryset=CrossingLine.objects.select_related("line").order_by("position", "pk"))
    ).order_by(Lower("display_name"), "pk")
    try:
        size = int(request.GET.get("per_page", 25))
    except (ValueError, TypeError):
        size = 25
    size = size if size in (25, 50, 75, 100) else 25
    letters = ["0-9", *string.ascii_uppercase, "other"]
    letter = request.GET.get("letter", "all")
    if letter not in ["all", *letters]:
        letter = "all"
    catalogue = lines if tab == "lines" else crossings
    name_field = "name" if tab == "lines" else "display_name"
    # Classify lightweight names before pagination, fetching full objects only
    # for the current page. Unicode accents and leading whitespace are handled
    # consistently without depending on the database collation.
    names = list(catalogue.values_list("pk", name_field))
    names = [(pk, name, catalogue_letter(name)) for pk, name in names]
    if letter != "all":
        names = [row for row in names if row[2] == letter]
    ranks = {key: index for index, key in enumerate(letters)}
    names.sort(key=lambda row: (ranks[row[2]], unicodedata.normalize("NFKD", row[1].lstrip()).casefold(), row[0]))
    page = Paginator([row[0] for row in names], size).get_page(request.GET.get("page"))
    page_ids = list(page.object_list)
    objects = {item.pk: item for item in catalogue.filter(pk__in=page_ids)}
    page.object_list = [objects[pk] for pk in page_ids]
    params = request.GET.copy()
    for key in ("edit_line", "edit_crossing", "panel", "page"):
        params.pop(key, None)
    for key, value in {"species": species.pk, "q": query, "tab": tab, "per_page": size, "letter": letter}.items():
        params[key] = value
    def link(**values):
        result = params.copy()
        for key, value in values.items():
            result[key] = value
        return "?" + result.urlencode()
    for item in page:
        item.catalogue_letter = catalogue_letter(getattr(item, name_field))
        item.catalogue_letter_label = "Other" if item.catalogue_letter == "other" else item.catalogue_letter
        if tab == "lines":
            item.edit_url = link(edit_line=item.pk) + "#catalogue-editor"
        else:
            item.catalogue_lines = [membership.line for membership in item.crossing_lines.all()]
            item.edit_url = link(edit_crossing=item.pk) + "#catalogue-editor"
    # Populate labels from the submitted IDs, never clear valid selections after
    # a server-side error. Invalid/missing IDs stay visible as validation errors.
    selected_ids = []
    if crossing_form.is_bound:
        for value in crossing_form.data.get("selected_lines", "").split(",")[:5]:
            try:
                selected_ids.append(int(value))
            except ValueError:
                pass
    selected_by_id = MouseLine.objects.in_bulk(selected_ids)
    initial_lines = [{"id": pk, "name": selected_by_id[pk].name} for pk in dict.fromkeys(selected_ids) if pk in selected_by_id]
    hidden = [{"name": key, "value": value} for key, value in params.items() if key != "per_page"]
    return render(request, "lab/lines_crossings_page.html", {
        "selected_species": species, "species_options": Species.objects.order_by(Lower("name"), "pk"),
        "line_form": line_form, "crossing_form": crossing_form, "line_query": query,
        "active_tab": tab, "catalogue_page": page, "page_size": size,
        "active_letter": letter,
        "letter_links": [{"label": "All" if key == "all" else "Other" if key == "other" else key,
                          "active": key == letter, "url": link(letter=key)} for key in ["all", *letters]],
        "tabs": [{"label": label, "active": key == tab, "url": link(tab=key)} for key, label in (("lines", "Lines"), ("crossings", "Crossings"))],
        "page_size_control": {"name": "per_page", "label": "Rows", "hidden_fields": hidden, "options": [{"value": n, "label": n, "selected": n == size} for n in (25, 50, 75, 100)]},
        "pagination": build_pagination(page, params), "line_lookup_url": reverse("lab:line_lookup"),
        "add_lines_url": link(panel="add-lines") + "#add-lines", "create_crossing_url": link(panel="create-crossing") + "#create-crossing",
        "open_panel": request.GET.get("panel"), "editor": editor, "edit_kind": edit_kind,
        "editor_form": editor_form, "editor_lines": editor_lines, "initial_selected_lines": initial_lines,
        "cancel_edit_url": "?" + params.urlencode(),
    })


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
            | Q(lines__name__icontains=query)
            | Q(lines__aliases__icontains=query)
        ).distinct()

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
