"""Authenticated workspace maintenance; no staff restriction on family resolution."""
from time import time
from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import InvalidPage, Paginator
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..family_maintenance import SCAN_VERSION, resolve_missing_family_links, scan_missing_family_links
from .common import edit_selected_mice_all_url, url_with_query_params

SESSION_KEY = 'family_maintenance_scan'
PAGE_SIZE = 50
SCAN_LIFETIME = 3600


@require_http_methods(['GET', 'HEAD', 'POST'])
def maintenance(request):
    scan = request.session.get(SESSION_KEY)
    if scan and (scan.get('owner') != request.user.pk or scan.get('version') != SCAN_VERSION or time() - scan.get('created', 0) > SCAN_LIFETIME):
        request.session.pop(SESSION_KEY, None)
        scan = None
        messages.info(request, 'The previous family-link scan expired. Scan the database again for current matches.')
    errors = []
    if request.method == 'POST':
        if request.POST.get('action') == 'scan':
            scan = {**scan_missing_family_links(), 'id': uuid4().hex, 'owner': request.user.pk, 'created': time()}
            scan['selected'] = [entry['key'] for entry in scan['entries'] if entry['eligible']]
            request.session[SESSION_KEY] = scan
            return redirect('lab:maintenance')
        try:
            if not scan or request.POST.get('scan_id') != scan['id']:
                raise ValidationError('This scan is no longer current. Scan the database again before resolving links.')
            if scan.get('stale'):
                raise ValidationError('Scan the database again before resolving links.')
            try:
                page = Paginator(scan['entries'], PAGE_SIZE).page(request.POST.get('current_page', '1'))
            except InvalidPage:
                raise ValidationError('The selected page is unavailable. Refresh the maintenance page.')
            page_keys = {entry['key'] for entry in page if entry['eligible']}
            submitted = set(request.POST.getlist('family_link'))
            if not submitted <= page_keys:
                raise ValidationError('Select only eligible links from this page.')
            scan['selected'] = sorted((set(scan['selected']) - page_keys) | submitted)
            request.session[SESSION_KEY] = scan
            if request.POST.get('action') == 'resolve':
                result = resolve_missing_family_links(scan['fingerprint'], scan['selected'])
                request.session.pop(SESSION_KEY, None)
                messages.success(request, f"Linked {result['links']} parent relationship(s) across {result['animals']} animal(s). Run another scan whenever more parent records are added.")
                for warning in result['warnings']:
                    messages.warning(request, warning)
                return redirect('lab:maintenance')
            if 'next_page' in request.POST:
                target = Paginator(scan['entries'], PAGE_SIZE).get_page(request.POST['next_page']).number
                return redirect(url_with_query_params(reverse('lab:maintenance'), page=target))
            raise ValidationError('Choose Scan database or Resolve selected links.')
        except ValidationError as exc:
            errors = exc.messages
            if getattr(exc, 'code', '') == 'stale' and scan:
                scan['stale'] = True
                request.session[SESSION_KEY] = scan
    page = None
    if scan:
        page = Paginator(scan['entries'], PAGE_SIZE).get_page(request.GET.get('page', request.POST.get('current_page', 1)))
        page.object_list = [dict(entry) for entry in page.object_list]
        selected = set(scan['selected'])
        return_url = url_with_query_params(reverse('lab:maintenance'), page=page.number)
        for entry in page:
            entry['checked'] = entry['key'] in selected
            entry['animal_url'] = url_with_query_params(reverse('lab:animal_summary', args=[entry['mouse_id']]), return_url=return_url)
            entry['parent_url'] = url_with_query_params(reverse('lab:animal_summary', args=[entry['parent_id']]), return_url=return_url) if entry['parent_id'] else ''
            entry['edit_url'] = url_with_query_params(edit_selected_mice_all_url(), selected_mouse=[entry['mouse_id']], mouse_editor_tab='family', return_url=return_url)
    response = render(request, 'lab/maintenance.html', {'scan': scan, 'page_obj': page, 'errors': errors,
        'scanned_at': timezone.datetime.fromtimestamp(scan['created'], tz=timezone.get_current_timezone()) if scan else None,
        'selected_count': len(scan['selected']) if scan else 0})
    response['Cache-Control'] = 'private, no-store'
    return response
