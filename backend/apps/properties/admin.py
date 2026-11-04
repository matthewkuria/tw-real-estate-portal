from django import forms
from django.contrib import admin

from apps.accounts.models import User

from . import services
from .models import PlotAllocation, Property


class PropertyAdminForm(forms.ModelForm):
    """
    Allocation runs through apps.properties.services here too, so the
    same checks apply and the history is kept.
    """

    allocate_to = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.MEMBER, status=User.Status.ACTIVE),
        required=False,
        help_text="Allocate this plot to a member. Only for available plots.",
    )
    waive_deposit = forms.BooleanField(
        required=False,
        help_text="Allocate before the full deposit is paid (give a reason in the notes).",
    )
    allocation_notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    release_reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Fill in to take this plot back from its holder.",
    )

    class Meta:
        model = Property
        fields = (
            "estate",
            "plot_number",
            "description",
            "price",
            "required_deposit",
        )

    def clean(self):
        cleaned = super().clean()
        member = cleaned.get("allocate_to")
        reason = cleaned.get("release_reason", "").strip()

        if member and reason:
            raise forms.ValidationError("Allocate or release, not both at once.")

        if member:
            if self.instance.pk is None:
                raise forms.ValidationError("Save the plot first, then allocate it.")

            try:
                services.check_allocation(
                    self.instance,
                    member,
                    cleaned.get("waive_deposit", False),
                    cleaned.get("allocation_notes", ""),
                )
            except services.AllocationError as exc:
                raise forms.ValidationError(str(exc))

        if reason and self.instance.status != Property.Status.ALLOCATED:
            raise forms.ValidationError("This plot isn't allocated, so there's nothing to release.")

        return cleaned


class PlotAllocationInline(admin.TabularInline):
    model = PlotAllocation
    fields = (
        "member",
        "allocated_at",
        "allocated_by",
        "deposit_paid",
        "deposit_waived",
        "notes",
        "released_at",
        "released_by",
        "release_reason",
    )
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    form = PropertyAdminForm

    list_display = (
        "plot_number",
        "estate",
        "status",
        "allocated_to",
        "price",
        "required_deposit",
    )

    list_filter = (
        "status",
        "estate",
    )

    search_fields = (
        "plot_number",
        "allocated_to__email",
        "allocated_to__first_name",
        "allocated_to__last_name",
    )

    readonly_fields = (
        "status",
        "allocated_to",
        "allocated_at",
    )

    fieldsets = (
        (None, {
            "fields": ("estate", "plot_number", "description", "price", "required_deposit"),
        }),
        ("Allocation", {
            "fields": (
                "status",
                "allocated_to",
                "allocated_at",
                "allocate_to",
                "waive_deposit",
                "allocation_notes",
                "release_reason",
            ),
        }),
    )

    inlines = [PlotAllocationInline]

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        member = form.cleaned_data.get("allocate_to")
        reason = form.cleaned_data.get("release_reason", "").strip()

        if member:
            services.allocate(
                obj,
                member,
                by=request.user,
                notes=form.cleaned_data.get("allocation_notes", ""),
                waive_deposit=form.cleaned_data.get("waive_deposit", False),
            )
        elif reason:
            services.release(obj, by=request.user, reason=reason)
