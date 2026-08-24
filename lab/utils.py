from django.utils.text import slugify

from .models import Mouse


def generate_mouse_id(date_of_birth, breeding_pair, crossing, tattoo):
    breeding_pair = slugify(breeding_pair).replace("-", "")
    crossing = slugify(crossing).replace("-", "").replace("x","")
    tattoo = tattoo.upper().replace(" ", "")

    base = f"{date_of_birth:%Y%m%d}_{crossing}_{breeding_pair}_{tattoo}"

    mouse_id = base
    counter = 1

    while Mouse.objects.filter(mouse_id=mouse_id).exists():
        mouse_id = f"{base}_{counter}"
        counter += 1

    return mouse_id
