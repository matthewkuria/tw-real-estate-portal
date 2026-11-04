from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('properties', '0003_backfill_allocations'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='property',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('allocated_to__isnull', False), ('status', 'ALLOCATED')), models.Q(('allocated_at__isnull', True), ('allocated_to__isnull', True), ('status', 'AVAILABLE')), _connector='OR'), name='property_allocation_consistent'),
        ),
    ]
