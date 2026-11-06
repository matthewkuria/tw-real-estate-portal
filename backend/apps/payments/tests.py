from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.estates.models import Estate
from apps.notifications.models import Notification
from apps.notifications.types import NotificationType
from apps.properties import services as plot_services
from apps.properties.models import Property

from .models import Payment


def make_user(email, role=User.Role.MEMBER, **extra):
    return User.objects.create_user(
        email=email,
        password="Pass-word-123!",
        role=role,
        status=User.Status.ACTIVE,
        is_active=True,
        **extra,
    )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PaymentTestCase(APITestCase):
    list_url = reverse("payments:payment-list")

    def setUp(self):
        self.admin = make_user("admin@example.com", User.Role.ADMIN)
        self.manager = make_user("manager@example.com", User.Role.MANAGER)
        self.finance = make_user("finance@example.com", User.Role.FINANCE_OFFICER)
        self.member = make_user("jane@example.com", first_name="Jane", last_name="Wanjiru")
        self.other = make_user("otis@example.com")

        self.oak = Estate.objects.create(name="Oak", code="oak", location="Nairobi")
        self.palm = Estate.objects.create(name="Palm", code="palm", location="Mombasa")

        self.plot = Property.objects.create(
            estate=self.oak,
            plot_number="14",
            price=Decimal("1000000"),
            required_deposit=Decimal("200000"),
        )

        self.client.force_authenticate(self.finance)

    def url(self, payment, name="detail"):
        return reverse(f"payments:payment-{name}", args=[payment.pk])

    def payload(self, **extra):
        data = {
            "member": self.member.pk,
            "estate": self.oak.pk,
            "amount": "50000.00",
            "method": Payment.Method.MPESA,
            "reference": "QFX123ABC",
        }
        data.update(extra)
        return {k: v for k, v in data.items() if v is not None}

    def record(self, **extra):
        return self.client.post(self.list_url, self.payload(**extra), format="json")

    def make_payment(self, status=Payment.Status.PENDING, member=None, reference="REF1", **extra):
        confirmed = status == Payment.Status.CONFIRMED
        fields = {
            "member": member or self.member,
            "estate": self.oak,
            "amount": Decimal("50000"),
            "method": Payment.Method.MPESA,
            "reference": reference,
            "status": status,
            "recorded_by": self.finance,
            "confirmed_by": self.finance if confirmed else None,
            "confirmed_at": timezone.now() if confirmed else None,
        }
        fields.update(extra)
        return Payment.objects.create(**fields)


class RecordTests(PaymentTestCase):
    def test_staff_record_a_pending_payment(self):
        for user in (self.manager, self.finance):
            self.client.force_authenticate(user)
            response = self.record(reference=f"REF-{user.pk}")

            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
            self.assertEqual(response.data["code"], "S103")
            self.assertEqual(response.data["data"]["status"], Payment.Status.PENDING)
            self.assertEqual(response.data["data"]["recorded_by"]["id"], user.pk)

    def test_approvers_can_record_as_confirmed(self):
        response = self.record(method=Payment.Method.CASH, reference=None, confirm=True)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(payment.confirmed_by, self.finance)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.member, type=NotificationType.PAYMENT_CONFIRMED
            ).exists()
        )

    def test_managers_cant_record_as_confirmed(self):
        self.client.force_authenticate(self.manager)
        response = self.record(confirm=True)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirm", response.data)

    def test_staff_must_name_a_member(self):
        response = self.record(member=None)
        self.assertIn("member", response.data)

        response = self.record(member=self.manager.pk)
        self.assertIn("member", response.data)

    def test_member_submits_their_own_payment(self):
        self.client.force_authenticate(self.member)
        response = self.record(member=self.other.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        payment = Payment.objects.get()
        self.assertEqual(payment.member, self.member)
        self.assertEqual(payment.recorded_by, self.member)
        self.assertEqual(payment.status, Payment.Status.PENDING)

    def test_member_cant_confirm(self):
        self.client.force_authenticate(self.member)
        self.assertIn("confirm", self.record(confirm=True).data)

    def test_reference_rules(self):
        self.assertIn("reference", self.record(reference="").data)

        self.make_payment(reference="QFX123ABC")
        self.assertIn("reference", self.record(reference="qfx123abc").data)

        # A rejected payment's reference can be used again.
        Payment.objects.update(status=Payment.Status.REJECTED)
        self.assertEqual(self.record().status_code, status.HTTP_201_CREATED)

        # Cash needs no reference.
        response = self.record(method=Payment.Method.CASH, reference="")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

    def test_plot_must_be_the_members_in_that_estate(self):
        self.assertIn("property", self.record(property=self.plot.pk).data)

        self.make_payment(Payment.Status.CONFIRMED, reference="DEP", amount=Decimal("200000"))
        plot_services.allocate(self.plot, self.member, by=self.admin)

        response = self.record(property=self.plot.pk)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["data"]["plot_number"], "14")

        self.assertIn("property", self.record(property=self.plot.pk, estate=self.palm.pk, reference="X2").data)
        self.assertIn("property", self.record(property=self.plot.pk, member=self.other.pk, reference="X3").data)

    def test_validation(self):
        self.assertIn("amount", self.record(amount="0").data)
        self.assertIn("received_at", self.record(received_at="2999-01-01T00:00:00Z").data)

        self.oak.is_active = False
        self.oak.save()
        self.assertIn("estate", self.record().data)

    def test_others_have_no_access(self):
        tenant_less = make_user("x@example.com", User.Role.SUPER_ADMIN)
        self.client.force_authenticate(tenant_less)
        self.assertEqual(self.record().status_code, status.HTTP_201_CREATED)

        self.client.logout()
        self.assertEqual(self.client.get(self.list_url).status_code, status.HTTP_401_UNAUTHORIZED)


class ListTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.mine = self.make_payment(Payment.Status.CONFIRMED, reference="A1")
        self.pending = self.make_payment(reference="B2", method=Payment.Method.CASH)
        self.theirs = self.make_payment(Payment.Status.CONFIRMED, member=self.other, reference="C3")

    def ids(self, response):
        return {row["id"] for row in response.data["data"]["results"]}

    def test_staff_see_and_filter_everything(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.data["code"], "S101")
        self.assertEqual(self.ids(response), {self.mine.pk, self.pending.pk, self.theirs.pk})
        self.assertEqual(response.data["data"]["total_confirmed"], "100000.00")

        filters = {
            "member": (self.other.pk, {self.theirs.pk}),
            "status": ("PENDING", {self.pending.pk}),
            "method": ("CASH", {self.pending.pk}),
            "search": ("wanjiru", {self.mine.pk, self.pending.pk}),
        }
        for name, (value, expected) in filters.items():
            self.assertEqual(self.ids(self.client.get(self.list_url, {name: value})), expected, name)

        today = timezone.localdate().isoformat()
        self.assertEqual(len(self.ids(self.client.get(self.list_url, {"received_from": today}))), 3)
        self.assertEqual(self.ids(self.client.get(self.list_url, {"received_to": "2000-01-01"})), set())

    def test_members_see_only_their_own(self):
        self.client.force_authenticate(self.member)

        response = self.client.get(self.list_url, {"member": self.other.pk})
        self.assertEqual(self.ids(response), {self.mine.pk, self.pending.pk})
        self.assertEqual(response.data["data"]["total_confirmed"], "50000.00")

        self.assertEqual(self.client.get(self.url(self.mine)).data["code"], "S102")
        self.assertEqual(self.client.get(self.url(self.theirs)).status_code, status.HTTP_404_NOT_FOUND)

    def test_bad_filters_are_rejected(self):
        for name, value in (("status", "PAID"), ("method", "BTC"), ("estate", "x"), ("received_from", "today")):
            response = self.client.get(self.list_url, {name: value})
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, name)


class EditTests(PaymentTestCase):
    def test_staff_edit_pending_payments(self):
        payment = self.make_payment()
        self.client.force_authenticate(self.manager)

        response = self.client.patch(self.url(payment), {"amount": "60000"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["code"], "S104")
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("60000"))

    def test_reviewed_payments_are_locked(self):
        payment = self.make_payment(Payment.Status.CONFIRMED)
        response = self.client.patch(self.url(payment), {"amount": "1"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "E026")

    def test_member_cant_be_changed_or_edit(self):
        payment = self.make_payment()

        response = self.client.patch(self.url(payment), {"member": self.other.pk}, format="json")
        self.assertIn("member", response.data)

        # Editing keeps its own reference without clashing with itself.
        response = self.client.patch(self.url(payment), {"notes": "Checked"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        self.client.force_authenticate(self.member)
        response = self.client.patch(self.url(payment), {"amount": "1"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_delete(self):
        payment = self.make_payment()
        response = self.client.delete(self.url(payment))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class ReviewTests(PaymentTestCase):
    def notified(self, kind):
        return Notification.objects.filter(recipient=self.member, type=kind).exists()

    def test_confirm(self):
        payment = self.make_payment()
        response = self.client.post(self.url(payment, "confirm"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["code"], "S105")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.CONFIRMED)
        self.assertEqual(payment.confirmed_by, self.finance)
        self.assertTrue(self.notified(NotificationType.PAYMENT_CONFIRMED))

        response = self.client.post(self.url(payment, "confirm"))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "E025")

    def test_reject_needs_reason(self):
        payment = self.make_payment()

        self.assertIn("reason", self.client.post(self.url(payment, "reject")).data)

        response = self.client.post(self.url(payment, "reject"), {"reason": "Not on statement"}, format="json")
        self.assertEqual(response.data["code"], "S106")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.REJECTED)
        self.assertEqual(payment.status_changed_by, self.finance)
        self.assertEqual(payment.status_reason, "Not on statement")
        self.assertTrue(self.notified(NotificationType.PAYMENT_REJECTED))

    def test_reverse_only_confirmed(self):
        pending = self.make_payment(reference="P1")
        response = self.client.post(self.url(pending, "reverse"), {"reason": "x"}, format="json")
        self.assertEqual(response.data["code"], "E025")

        confirmed = self.make_payment(Payment.Status.CONFIRMED, reference="C1")
        response = self.client.post(self.url(confirmed, "reverse"), {"reason": "Cheque bounced"}, format="json")
        self.assertEqual(response.data["code"], "S107")
        confirmed.refresh_from_db()
        self.assertEqual(confirmed.status, Payment.Status.REVERSED)
        self.assertTrue(self.notified(NotificationType.PAYMENT_REVERSED))

    def test_reversal_lowers_plot_balance(self):
        deposit = self.make_payment(Payment.Status.CONFIRMED, reference="DEP", amount=Decimal("200000"))
        plot_services.allocate(self.plot, self.member, by=self.admin)
        plot_url = reverse("properties:plot-detail", args=[self.plot.pk])

        def paid():
            return Decimal(self.client.get(plot_url).data["data"]["amount_paid"])

        self.assertEqual(paid(), Decimal("200000"))

        self.client.post(self.url(deposit, "reverse"), {"reason": "Reversed by M-Pesa"}, format="json")

        self.assertEqual(paid(), Decimal("0"))
        self.plot.refresh_from_db()
        self.assertEqual(self.plot.status, Property.Status.ALLOCATED)

    def test_only_approvers_review(self):
        payment = self.make_payment()

        for user in (self.manager, self.member):
            self.client.force_authenticate(user)
            for name in ("confirm", "reject", "reverse"):
                response = self.client.post(self.url(payment, name), {"reason": "x"}, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, (user.email, name))

        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.post(self.url(payment, "confirm")).status_code, status.HTTP_200_OK)
