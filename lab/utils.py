import re

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


def litter_id_prefix(date_of_birth, breeding_pair, crossing):
    """A visible, deterministic prefix for animals without assigned IDs."""
    return f"{date_of_birth:%Y%m%d}_{slugify(breeding_pair)}_{slugify(crossing)}_"


def parse_mouse_numbers(value):
    """Expand comma-separated numbers/ranges, preserving order and zero padding."""
    numbers = []
    seen = set()
    for part in (value or "").split(","):
        match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+)\s*)?", part)
        if not match:
            raise ValueError("Enter numbers or ascending ranges separated by commas, for example 1,3,5-7.")
        start, end = match.groups()
        if len(start) > 100 or (end and len(end) > 100):
            raise ValueError("Animal numbers must be 100 characters or fewer.")
        if end is None:
            values = [start]
        else:
            first, last = int(start), int(end)
            if last < first:
                raise ValueError("Ranges must be ascending, for example 1-3.")
            if last - first + 1 > 500 - len(numbers):
                raise ValueError("Create at most 500 animals at a time.")
            width = max(len(start), len(end)) if start.startswith("0") and len(start) > 1 else 0
            values = [str(number).zfill(width) for number in range(first, last + 1)]
        for number in values:
            if number in seen:
                raise ValueError(f"Animal number {number} is listed more than once.")
            seen.add(number)
            numbers.append(number)
            if len(numbers) > 500:
                raise ValueError("Create at most 500 animals at a time.")
    return numbers
