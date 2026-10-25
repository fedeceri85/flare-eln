"""Navigation built from resolved routes and objects already loaded by views."""
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from django.urls import Resolver404, resolve, reverse
from django.utils.http import url_has_allowed_host_and_scheme


SECTIONS = (
    ("home", "Home", "home"),
    ("mice", "Animals", "mice_page"),
    ("procedures", "Procedures", "procedure_page"),
    ("projects", "Projects", "projects_page"),
    ("services", "Services", "services_page"),
    ("reports", "Reports", "reports_page"),
    ("genetics", "Lines & Crossings", "lines_crossings_page"),
    ("settings", "Settings", "settings_page"),
)
PAGE_TITLES = {
    "home": "Home", "login": "Log in", "mice_page": "Animals",
    "procedure_page": "Procedures", "projects_page": "Projects",
    "experiments_page": "Experiments", "recordings_page": "Recordings",
    "services_page": "Services", "reports_page": "Reports",
    "lines_crossings_page": "Lines & Crossings", "animal_summary": "Animal summary",
    "add_project": "Add project", "edit_project": "Edit project",
    "add_experiment": "Add experiment", "edit_experiment": "Edit experiment",
    "add_recording": "Add recording", "edit_recording": "Edit recording",
    "import_recordings": "Import from spreadsheet", "import_recordings_draft": "Import from spreadsheet",
    "settings_page": "Settings", "import_templates": "Import templates",
    "image_storage_settings": "Image storage", "recording_images": "Recording images",
    "rename_import_template": "Rename import template", "delete_import_template": "Delete import template",
    "genotype_categories": "Genotype categories", "add_genotype_category": "Add genotype category", "edit_genotype_category": "Edit genotype category",
    "recording_types": "Recording types", "add_recording_type": "Add recording type", "edit_recording_type": "Edit recording type",
    "delete_recording_type": "Delete recording type", "add_recording_field": "Add recording field", "edit_recording_field": "Edit recording field",
    "delete_recording": "Delete recording", "delete_selected_recordings": "Delete selected recordings", "select_recording_mouse": "Select animals",
    "edit_mouse": "Edit animal", "rename_mouse": "Rename animal",
    "delete_mouse": "Delete animal", "edit_mouse_procedure": "Edit procedure",
    "recording_animals": "Animal summary",
    "view_selected_mice": "Animal summary",
}
PRIMARY_ACTIONS = {
    "add_mouse": "Save animal", "add_litter": "Add animals",
    "edit_mouse": "Save changes", "rename_mouse": "Rename animal",
    "edit_mouse_procedure": "Save procedure", "edit_selected_mice": "Save bulk changes",
    "add_project": "Save project", "edit_project": "Save changes",
    "add_experiment": "Save experiment", "edit_experiment": "Save changes",
    "add_recording": "Save recording", "edit_recording": "Save changes",
    "select_recording_mouse": "Add selected animals",
}
CONTEXTUAL_ROUTES = {
    "animal_summary", "edit_mouse", "rename_mouse", "delete_mouse",
    "edit_selected_mice_all", "edit_selected_mice_licence_protocol", "edit_selected_mice",
    "edit_mouse_procedure", "experiments_page", "recordings_page",
    "edit_project", "edit_experiment", "edit_recording", "delete_recording", "delete_selected_recordings",
    "recording_animals",
    "recording_images",
    "view_selected_mice_licence_protocol",
}


def route_name(url):
    try:
        match = resolve(urlsplit(url).path)
        return match.url_name if match.namespace == "lab" else ""
    except (Resolver404, ValueError):
        return ""


def safe_local_url(request, url):
    return url if url and url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    ) else ""


def contextual_url(url, request):
    """Carry the list/detail state into record links, without nesting it twice."""
    if not request or not safe_local_url(request, url) or route_name(url) not in CONTEXTUAL_ROUTES:
        return url
    parts = urlsplit(url)
    if "return_url" in parse_qs(parts.query):
        return url
    query = parts.query + ("&" if parts.query else "") + urlencode({"return_url": request.get_full_path()})
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def action_route(name):
    for prefix in ("add_mouse", "add_litter", "edit_selected_mice", "view_selected_mice", "delete_selected_mice", "procedure_page"):
        if name.startswith(prefix):
            return prefix
    return name


def build_navigation(context):
    request = context.get("request")
    if not request:
        return {}
    name = request.resolver_match.url_name if request.resolver_match else ""
    action = action_route(name)
    project = context.get("project")
    experiment = context.get("experiment")
    mouse = context.get("mouse")
    protocol = context.get("selected_protocol")
    return_url = safe_local_url(request, context.get("cancel_url") or context.get("return_url") or request.GET.get("return_url", ""))
    ancestors = []
    ancestor = return_url
    for _ in range(8):
        if not ancestor or ancestor in ancestors:
            break
        ancestors.append(ancestor)
        ancestor = safe_local_url(request, parse_qs(urlsplit(ancestor).query).get("return_url", [""])[0])
    from_procedures = any(action_route(route_name(url)) == "procedure_page" for url in ancestors)
    if not protocol and mouse and from_procedures:
        # Mouse editing views select_related this relation; never fetch it here.
        protocol = mouse._state.fields_cache.get("protocol")
    title = PAGE_TITLES.get(action, {
        "add_mouse": "Add animals", "add_litter": "Add animals",
        "edit_selected_mice": "Edit selected animals", "delete_selected_mice": "Delete selected animals",
    }.get(action, "FLARE-ELN"))

    if name == "home":
        section = "home"
    elif name in {
        "settings_page", "import_templates", "rename_import_template", "delete_import_template",
        "image_storage_settings",
        "genotype_categories", "add_genotype_category", "edit_genotype_category",
        "recording_types", "add_recording_type", "edit_recording_type", "delete_recording_type", "add_recording_field", "edit_recording_field",
    }:
        section = "settings"
    elif name == "lines_crossings_page":
        section = "genetics"
    elif name == "reports_page":
        section = "reports"
    elif name == "services_page" or (project and project.is_service):
        section = "services"
    elif project or name in {"projects_page", "add_project"}:
        section = "projects"
    elif action == "procedure_page" or (mouse and from_procedures) or (protocol and action in {"add_mouse", "add_litter", "edit_selected_mice", "view_selected_mice", "delete_selected_mice"}):
        section = "procedures"
    else:
        section = "mice"

    links = [
        {"label": label, "url": reverse(f"lab:{view}"), "active": key == section}
        for key, label, view in SECTIONS
        if key != "mice" and (key != "genetics" or request.user.is_staff)
    ]
    breadcrumbs = [{"label": "Home", "url": reverse("lab:home")}]

    def crumb(label, url=""):
        if not breadcrumbs or breadcrumbs[-1]["url"] != url or not url:
            breadcrumbs.append({"label": str(label), "url": url})

    if name == "login":
        breadcrumbs = [{"label": "Log in", "url": ""}]
        links = []
    elif section == "settings":
        crumb("Settings", reverse("lab:settings_page"))
        if name in {"genotype_categories", "add_genotype_category", "edit_genotype_category"}:
            crumb("Genotype categories", reverse("lab:genotype_categories"))
            if name != "genotype_categories":
                crumb(title)
        elif name in {"recording_types", "add_recording_type", "edit_recording_type", "delete_recording_type", "add_recording_field", "edit_recording_field"}:
            crumb("Recording types", reverse("lab:recording_types"))
            if name in {"add_recording_field", "edit_recording_field"} and context.get("recording_type"):
                kind = context["recording_type"]
                crumb(kind.name, reverse("lab:edit_recording_type", args=[kind.pk]))
            if name != "recording_types":
                crumb(title)
        elif name == "image_storage_settings":
            crumb(title)
        elif name != "settings_page":
            crumb("Import templates", reverse("lab:import_templates"))
        if name in {"rename_import_template", "delete_import_template"}:
            crumb(title)
    elif name != "home":
        _, section_label, section_view = next(item for item in SECTIONS if item[0] == section)
        crumb(section_label, reverse(f"lab:{section_view}"))
        if section == "procedures":
            licence = context.get("selected_licence_reference") or (protocol.licence_reference if protocol else None)
            if licence:
                crumb(licence, reverse("lab:procedure_page_licence", args=[licence]))
            if protocol:
                crumb(protocol.name, reverse("lab:procedure_page_licence_protocol", args=[protocol.licence_reference, protocol.protocol_number]))
        if project and not project.is_service:
            crumb(project.name, reverse("lab:experiments_page", args=[project.pk]))
        if experiment:
            crumb(experiment.name, reverse("lab:recordings_page", args=[project.pk, experiment.pk]))
        if action in {"add_recording", "edit_recording", "delete_recording", "delete_selected_recordings", "select_recording_mouse", "import_recordings", "import_recordings_draft"}:
            crumb("Recordings", reverse("lab:recordings_page", args=[project.pk, experiment.pk]))
        if mouse:
            crumb(mouse.mouse_id, reverse("lab:animal_summary", args=[mouse.pk]))
        if name == "reports_page" and context.get("selected_report") == "home_office_return":
            title = "Home Office Return"
            crumb(title)
        elif not (name in {"mice_page", "projects_page", "services_page", "lines_crossings_page", "reports_page", "experiments_page", "recordings_page"} or action == "procedure_page"):
            crumb(title)

    # Keep a safe return chain's list state on matching ancestor links.
    for ancestor in ancestors:
        parts = urlsplit(ancestor)
        for item in breadcrumbs[:-1]:
            if item["url"] and urlsplit(item["url"]).path == parts.path:
                item["url"] = ancestor

    parent = next((item for item in reversed(breadcrumbs[:-1]) if item["url"]), None)
    back_url = return_url or (parent["url"] if parent else "")
    back_name = action_route(route_name(back_url)) if back_url else ""
    back_label = PAGE_TITLES.get(back_name, {
        "add_mouse": "Add animals", "add_litter": "Add animals",
        "edit_selected_mice": "selected animals", "procedure_page": "Procedures",
    }.get(back_name, parent["label"] if parent else "previous page"))
    if name in {"rename_mouse", "delete_mouse"} and mouse:
        back_url = reverse("lab:edit_mouse", args=[mouse.pk])
        if return_url:
            back_url += "?" + urlencode({"return_url": return_url})
        back_label = "Edit animal"

    if breadcrumbs:
        breadcrumbs[-1]["url"] = ""
    if len(breadcrumbs) <= 2 and not return_url:
        back_url = ""
    primary = None
    if action in PRIMARY_ACTIONS:
        label = PRIMARY_ACTIONS[action]
        if context.get("possible_duplicate_warning"):
            label = "Add animals anyway" if action == "add_litter" else "Save anyway"
        primary = {"label": label, "name": "save_mouse" if action == "edit_mouse" else "", "value": "1"}
    return {
        "links": links, "breadcrumbs": breadcrumbs, "section": section,
        "back_url": back_url if name not in {"home", "login"} else "",
        "back_label": f"Back to {back_label}", "primary": primary,
        "scope": "Animal details" if name == "edit_mouse" else title,
    }
