"""Targeted family reads; graph matching is loaded separately on demand."""
from collections import OrderedDict, defaultdict

from django.db.models import Case, CharField, Count, F, Value, When, Window
from django.db.models.functions import RowNumber
from django.urls import reverse

from .models import Mouse
from .parentage import ROLES, fingerprint


def record_link(pk, return_url=""):
    from .views.common import url_with_query_params
    return {"id": pk, "url": url_with_query_params(reverse("lab:animal_summary", args=[pk]), return_url=return_url)}


def group_mice_by_parents(mice):
    groups = OrderedDict()
    for mouse in mice:
        identity = [
            ["linked", getattr(mouse, f"{role}_id")] if getattr(mouse, f"{role}_id")
            else ["external", getattr(mouse, f"{role}_external_id").strip()]
            for role in ROLES
        ]
        key = fingerprint(identity)
        groups.setdefault(key, {"key": key, "mice": []})["mice"].append(mouse)
    return list(groups.values())


def parent_group_version(mice):
    return fingerprint([
        [mouse.pk, *[getattr(mouse, f"{role}{suffix}") for role in ROLES for suffix in ("_id", "_external_id")]]
        for mouse in sorted(mice, key=lambda mouse: mouse.pk)
    ])


def parent_section(mouse, return_url=""):
    from .views.common import url_with_query_params
    parents = []
    for role in ROLES:
        parent_id, text = getattr(mouse, f"{role}_id"), getattr(mouse, f"{role}_external_id")
        parents.append({"role": role.title(), "id": parent_id or text, "linked": bool(parent_id),
            "url": record_link(parent_id, return_url)["url"] if parent_id else "",
            "recorded_id": text if parent_id and text != parent_id else ""})
    return {"mouse_id": mouse.pk, "parents": parents,
        "parents_url": url_with_query_params(reverse("lab:animal_relatives", args=[mouse.pk]), relation="parents", return_url=return_url, mouse_editor_tab="individual") if any(p["linked"] for p in parents) else ""}


def build_parent_groups(mice, return_url="", families=None):
    mice = list(mice)
    by_id = {family["mouse_id"]: family for family in families} if families is not None else {}
    groups = []
    for group in group_mice_by_parents(mice):
        first = group["mice"][0]
        family = by_id.get(first.pk) or parent_section(first, return_url)
        parents = []
        for role, parent in zip(ROLES, family["parents"]):
            records = [{**record_link(mouse.pk, return_url), "value": getattr(mouse, f"{role}_external_id")} for mouse in group["mice"]]
            differs = len({record["value"] for record in records}) > 1
            parents.append({**parent, "recorded_ids": records if differs and parent["linked"] else [],
                            "recorded_id": "" if differs else parent["recorded_id"]})
        groups.append({"key": group["key"], "members": [record_link(mouse.pk, return_url) for mouse in group["mice"]],
                       "count": len(group["mice"]), "parents": parents, "parents_url": family["parents_url"]})
    return groups


def offspring_groups(mouse_ids, return_url="", *, previews=True):
    """Four bounded queries: counts and top five per group, for each parent role."""
    from .views.common import url_with_query_params
    grouped = defaultdict(list)
    if not mouse_ids:
        return grouped
    for role in ROLES:
        other = "father" if role == "mother" else "mother"
        base = Mouse.objects.filter(**{f"{role}_id__in": mouse_ids})
        if role == "father":
            base = base.exclude(mother_id=F("father_id"))
        base = base.annotate(_parent=F(f"{role}_id"), _other=F(f"{other}_id"),
            _text=Case(When(**{f"{other}_id__isnull": True}, then=F(f"{other}_external_id")), default=Value(""), output_field=CharField()))
        counts = base.order_by().values("_parent", "_other", "_text").annotate(total=Count("pk"))
        by_identity = {}
        for row in counts:
            pk, other_id, text = row["_parent"], row["_other"], row["_text"]
            identity = ["linked", other_id] if other_id else ["external", text] if text else ["unknown"]
            key = fingerprint([role, identity])
            group = {"key": key, "role": role, "other_role": other, "other_id": other_id or text,
                "other_url": record_link(other_id, return_url)["url"] if other_id else "", "external": bool(text and not other_id),
                "count": row["total"], "preview": [], "identity": identity,
                "url": url_with_query_params(reverse("lab:animal_relatives", args=[pk]), relation="offspring", group=key, return_url=return_url, mouse_editor_tab="family")}
            grouped[pk].append(group)
            by_identity[(pk, other_id, text)] = group
        if previews:
            rows = base.annotate(_rank=Window(expression=RowNumber(), partition_by=[F("_parent"), F("_other"), F("_text")],
                order_by=[F("date_of_birth").asc(nulls_last=True), F("mouse_id").asc()])).filter(_rank__lte=5).order_by(F("date_of_birth").asc(nulls_last=True), "mouse_id").values("_parent", "_other", "_text", "mouse_id", "date_of_birth")
            for row in rows:
                group = by_identity[(row["_parent"], row["_other"], row["_text"])]
                group.setdefault("sort_key", (row["date_of_birth"] is None, str(row["date_of_birth"] or ""), row["mouse_id"]))
                group["preview"].append(record_link(row["mouse_id"], return_url))
    for groups in grouped.values():
        groups.sort(key=lambda g: g.get("sort_key", (True, "", g["key"])))
    return grouped


def build_family_sections(mice, return_url=""):
    from .views.common import url_with_query_params
    mice = list(mice)
    grouped = offspring_groups([mouse.pk for mouse in mice], return_url)
    sections = []
    for mouse in mice:
        groups = grouped[mouse.pk]
        sections.append({**parent_section(mouse, return_url), "groups": groups, "offspring_count": sum(g["count"] for g in groups),
            "offspring_url": url_with_query_params(reverse("lab:animal_relatives", args=[mouse.pk]), relation="offspring", return_url=return_url, mouse_editor_tab="family")})
    return sections
