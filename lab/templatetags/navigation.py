from django import template
from django.conf import settings

from lab.navigation import build_navigation, contextual_url

register = template.Library()


@register.simple_tag
def app_version():
    return settings.FLARE_VERSION


@register.simple_tag(takes_context=True)
def page_navigation(context):
    return build_navigation(context)


@register.simple_tag
def record_link(url, request):
    return contextual_url(url, request)
