"""Stored, authenticated share destinations and collision-safe word codes."""
from functools import lru_cache
from hashlib import sha256
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from xkcdpass import xkcd_password

from .models import Mouse, Recording, ShareCodeCounter, SharedAnimalIdentity, SharedLink
from .parentage import parentage_transaction
from .usage import record_code_usage

ANIMAL_TABS = {"shared", "individual", "family", "procedures", "services"}


@lru_cache(maxsize=1)
def share_words():
    # The EFF list ships with the dependency. Exclude its four hyphenated
    # entries so each code has exactly two (or three) unambiguous words.
    return tuple(sorted(xkcd_password.generate_wordlist(
        wordfile=xkcd_password.locate_wordfile("eff-long"),
        min_length=1, max_length=32, valid_chars="[a-z]",
    )))


def normalize_share_code(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z\s-]{3,80}", value):
        raise ValidationError("Code not found. Check the code and try again.")
    key = re.sub(r"[\s-]+", "", value).lower()
    if not key or key[0] not in {"m", "r"}:
        raise ValidationError("Code not found. Check the code and try again.")
    return key


def generate_share_code(kind, word_count):
    return kind + "-" + xkcd_password.generate_xkcdpassword(
        share_words(), numwords=word_count, delimiter="-", case="lower",
    )


def allocate_share(kind, user, *, recording=None, identities=(), selection_key=None, tab="", source="sharing"):
    """Called inside a transaction; serialize allocations by prefix, not COUNT(*)."""
    counter, _ = ShareCodeCounter.objects.select_for_update().get_or_create(kind=kind)
    lookup = {"recording": recording} if recording is not None else {"selection_key": selection_key}
    existing = SharedLink.objects.filter(kind=kind, **lookup).first()
    if existing is not None:
        record_code_usage(existing, user, created=False, source=source, object_count=len(identities) if recording is None else 1)
        return existing
    limit = getattr(settings, "SHARE_CODE_TWO_WORD_LIMIT", len(share_words()) ** 2 // 2)
    if counter.allocated >= limit:
        counter.use_three_words = True
    initial_count = 3 if counter.use_three_words else 2
    for word_count in range(initial_count, 4):
        for _ in range(10):
            code = generate_share_code(kind, word_count)
            try:
                # A savepoint keeps a collision from breaking the outer transaction.
                with transaction.atomic():
                    link = SharedLink.objects.create(
                        kind=kind, code=code, lookup_key=normalize_share_code(code),
                        selection_key=selection_key, recording=recording,
                        animal_tab=tab, created_by=user,
                    )
            except IntegrityError:
                continue
            if identities:
                link.animals.set(identities)
            counter.allocated += 1
            counter.save(update_fields=["allocated", "use_three_words"])
            record_code_usage(link, user, created=True, source=source, object_count=len(identities) if recording is None else 1)
            return link
    raise ValidationError("Could not allocate a share code. Please try again.")


@parentage_transaction
def share_animals(mouse_ids, tab, user, *, source="sharing"):
    mouse_ids = list(dict.fromkeys(mouse_ids))
    if not mouse_ids or len(mouse_ids) > 500:
        raise ValidationError("Select between 1 and 500 animals to share.")
    mice = list(Mouse.objects.select_for_update().filter(pk__in=mouse_ids).order_by("pk"))
    if len(mice) != len(mouse_ids):
        raise ValidationError("One or more selected animals are no longer available.")
    # A code identifies the animals, independently of the tab used to share them.
    tab = "shared" if len(mice) == 1 else "individual"
    identities = [SharedAnimalIdentity.objects.get_or_create(mouse=mouse)[0] for mouse in mice]
    key = sha256((",".join(sorted(str(identity.pk) for identity in identities))).encode()).hexdigest()
    return allocate_share(SharedLink.ANIMALS, user, identities=identities, selection_key=key, tab=tab, source=source)


@transaction.atomic
def share_recording(recording_id, user):
    try:
        recording = Recording.objects.select_for_update().get(pk=recording_id)
    except Recording.DoesNotExist as exc:
        raise ValidationError("This recording is no longer available.") from exc
    return allocate_share(SharedLink.RECORDING, user, recording=recording)
