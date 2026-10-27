import time

from django.core.management.base import BaseCommand

from apps.notifications.services import MAX_EMAIL_ATTEMPTS, send_pending_emails


class Command(BaseCommand):
    help = (
        "Send queued notification emails. Run it on a schedule (e.g. cron "
        "every minute), or with --loop as a long-running worker."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--max-attempts", type=int, default=MAX_EMAIL_ATTEMPTS)
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Keep running, checking the outbox every --interval seconds.",
        )
        parser.add_argument("--interval", type=float, default=10)

    def handle(self, *args, **options):
        while True:
            # Drain everything that's due before sleeping.
            while True:
                sent, failed = send_pending_emails(
                    batch_size=options["batch_size"],
                    max_attempts=options["max_attempts"],
                )

                if sent or failed:
                    self.stdout.write(f"Sent {sent}, gave up on {failed}.")

                if sent + failed < options["batch_size"]:
                    break

            if not options["loop"]:
                return

            time.sleep(options["interval"])
