"""
Account and payment status changes can happen through the API, Django
admin or a script, so they're caught on save rather than in one service.
"""

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.accounts.models import User
from apps.payments.models import Payment

from . import events


def _remember_status(sender, instance, update_fields):
    """
    Stash the status as stored, so post_save can tell whether it changed.
    Skips the lookup when the save can't touch the status.
    """
    instance._previous_status = None

    if instance.pk is None:
        return

    if update_fields is not None and "status" not in update_fields:
        return

    instance._previous_status = (
        sender.objects
        .filter(pk=instance.pk)
        .values_list("status", flat=True)
        .first()
    )


@receiver(pre_save, sender=User, dispatch_uid="notifications_user_pre_save")
def user_pre_save(sender, instance, update_fields=None, raw=False, **kwargs):
    if not raw:
        _remember_status(sender, instance, update_fields)


@receiver(post_save, sender=User, dispatch_uid="notifications_user_post_save")
def user_post_save(sender, instance, created, raw=False, **kwargs):
    if raw:
        return

    if created:
        if instance.status == User.Status.PENDING and instance.role == User.Role.MEMBER:
            with transaction.atomic():
                events.registration_pending(instance)
        return

    previous = getattr(instance, "_previous_status", None)

    if previous is not None and previous != instance.status:
        with transaction.atomic():
            events.account_status_changed(instance, previous)


@receiver(pre_save, sender=Payment, dispatch_uid="notifications_payment_pre_save")
def payment_pre_save(sender, instance, update_fields=None, raw=False, **kwargs):
    if not raw:
        _remember_status(sender, instance, update_fields)


@receiver(post_save, sender=Payment, dispatch_uid="notifications_payment_post_save")
def payment_post_save(sender, instance, created, raw=False, **kwargs):
    if raw:
        return

    previous = getattr(instance, "_previous_status", None)

    # A payment created already confirmed (e.g. cash recorded at the
    # office) is worth telling the member about too.
    if created or (previous is not None and previous != instance.status):
        with transaction.atomic():
            events.payment_status_changed(instance)
