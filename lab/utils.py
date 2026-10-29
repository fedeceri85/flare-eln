import re
import unicodedata

from django.utils.text import slugify

from .models import Mouse


def crossing_id_code(crossing):
    """Short label in saved line order; accept legacy text callers too."""
    names = ([line.name for line in crossing.ordered_lines()]
             if hasattr(crossing, "ordered_lines")
             else re.split(r"\s+[xX]\s+|×", str(crossing)))
    parts = []
    for index, name in enumerate(names):
        if index == 0:
            # Underscores and punctuation separate allele markers too. Keep
            # meaningful substrings such as Creb1 and Foxp2 intact.
            name = re.sub(
                r"(?<![A-Za-z0-9])(flox|ko|cre)(?![A-Za-z0-9])",
                lambda match: {"flox": "f", "ko": "k", "cre": "c"}[match.group().lower()],
                name.strip(), flags=re.IGNORECASE,
            )
        # Preserve case and meaningful letters (including x inside a line).
        compact = re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", name))
        parts.append(compact if index == 0 else "".join(c for c in compact if c.isalpha())[:2])
    return "".join(parts) or "crossing"


def litter_id_prefix(date_of_birth, breeding_pair, crossing):
    """Shared visible prefix for newly generated animal IDs."""
    pair = slugify(breeding_pair).replace("-", "")
    return f"{date_of_birth:%y%m%d}_{crossing_id_code(crossing)}_{pair}_"


def mouse_id_base(date_of_birth, breeding_pair, crossing, tattoo):
    return litter_id_prefix(date_of_birth, breeding_pair, crossing) + tattoo.upper().replace(" ", "")


def generate_mouse_id(date_of_birth, breeding_pair, crossing, tattoo):
    base = mouse_id_base(date_of_birth, breeding_pair, crossing, tattoo)
    mouse_id = base
    counter = 1
    while Mouse.objects.filter(mouse_id=mouse_id).exists():
        mouse_id = f"{base}_{counter}"
        counter += 1
    return mouse_id


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
