"""
Before this, allocation was three fields edited by hand. Make them agree
(the holder decides) and record each current holder in the history, so
the constraint added next holds and releases have a row to close.
"""

from django.db import migrations


NOTE = "Allocated before allocation history was kept."


def backfill(apps, schema_editor):
    Property = apps.get_model("properties", "Property")
    PlotAllocation = apps.get_model("properties", "PlotAllocation")

    for plot in Property.objects.all():
        if plot.allocated_to_id:
            plot.status = "ALLOCATED"
            plot.allocated_at = plot.allocated_at or plot.updated_at
            plot.save(update_fields=["status", "allocated_at"])

            PlotAllocation.objects.create(
                property=plot,
                member_id=plot.allocated_to_id,
                allocated_at=plot.allocated_at,
                notes=NOTE,
            )
        elif plot.status != "AVAILABLE" or plot.allocated_at is not None:
            plot.status = "AVAILABLE"
            plot.allocated_at = None
            plot.save(update_fields=["status", "allocated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0002_plot_allocation'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
