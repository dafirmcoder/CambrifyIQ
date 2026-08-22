from django import template

register = template.Library()


@register.filter
def dict_get(d, key):
    """Return d[key] — allows accessing a dict by a variable key in templates."""
    if isinstance(d, dict):
        return d.get(key)
    return None
