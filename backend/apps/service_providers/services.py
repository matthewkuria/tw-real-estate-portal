import re

from django.db import transaction
from django.utils import timezone

from .models import ServiceProvider, StatusChange


class InvalidTransition(Exception):
    pass


def phone_key(phone):
    """
    The last nine digits, so 0712 345 678 and +254 712 345 678 compare
    equal.
    """
    digits = re.sub(r"\D", "", phone or "")
    return digits[-9:] if len(digits) >= 9 else digits


def normalize_kra_pin(pin):
    return (pin or "").strip().upper()


def blacklisted_matches(phone="", email="", kra_pin="", exclude_pk=None):
    """
    Blacklisted providers sharing a phone number, email or KRA PIN with
    these details: a sign the same business is coming back under a new
    name.
    """
    phone = phone_key(phone)
    email = (email or "").strip().lower()
    kra_pin = normalize_kra_pin(kra_pin)

    matches = []
    blacklisted = ServiceProvider.objects.filter(status=ServiceProvider.Status.BLACKLISTED)

    if exclude_pk is not None:
        blacklisted = blacklisted.exclude(pk=exclude_pk)

    for provider in blacklisted.only("pk", "name", "phone", "email", "kra_pin"):
        fields = []

        if phone and phone_key(provider.phone) == phone:
            fields.append("phone")
        if email and provider.email.lower() == email:
            fields.append("email")
        if kra_pin and provider.kra_pin == kra_pin:
            fields.append("kra_pin")

        if fields:
            matches.append({"id": provider.pk, "name": provider.name, "matched_on": fields})

    return matches


@transaction.atomic
def blacklist(provider, reason, user):
    provider = ServiceProvider.objects.select_for_update().get(pk=provider.pk)

    if provider.status == ServiceProvider.Status.BLACKLISTED:
        raise InvalidTransition("This provider is already blacklisted.")

    provider.status = ServiceProvider.Status.BLACKLISTED
    provider.blacklist_reason = reason
    provider.blacklisted_at = timezone.now()
    provider.blacklisted_by = user
    provider.save(update_fields=[
        "status", "blacklist_reason", "blacklisted_at", "blacklisted_by", "updated_at",
    ])

    StatusChange.objects.create(
        provider=provider,
        action=StatusChange.Action.BLACKLISTED,
        reason=reason,
        changed_by=user,
    )

    return provider


@transaction.atomic
def reinstate(provider, reason, user):
    provider = ServiceProvider.objects.select_for_update().get(pk=provider.pk)

    if provider.status != ServiceProvider.Status.BLACKLISTED:
        raise InvalidTransition("This provider isn't blacklisted.")

    provider.status = ServiceProvider.Status.ACTIVE
    provider.blacklist_reason = ""
    provider.blacklisted_at = None
    provider.blacklisted_by = None
    provider.save(update_fields=[
        "status", "blacklist_reason", "blacklisted_at", "blacklisted_by", "updated_at",
    ])

    StatusChange.objects.create(
        provider=provider,
        action=StatusChange.Action.REINSTATED,
        reason=reason,
        changed_by=user,
    )

    return provider
