import os

from django.core.management.base import BaseCommand

from apps.accounts.models import User


class Command(BaseCommand):
    help = (
        "Create the first Super Admin from DJANGO_SUPERUSER_EMAIL and "
        "DJANGO_SUPERUSER_PASSWORD. Safe to run on every deploy: does "
        "nothing if the variables are unset or the account already exists."
    )

    def handle(self, *args, **options):
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")

        if not email or not password:
            self.stdout.write("No superuser details set; skipping.")
            return

        if User.objects.filter(email__iexact=email).exists():
            self.stdout.write(f"{email} already exists; leaving it unchanged.")
            return

        User.objects.create_superuser(email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created Super Admin {email}."))
