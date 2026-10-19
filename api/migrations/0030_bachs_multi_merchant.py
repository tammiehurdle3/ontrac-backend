from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0029_milani_introduction_subject"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesettings",
            name="bachs_account",
            field=models.CharField(default="default", help_text="Active Bachs merchant account key. Credentials are configured in environment variables.", max_length=100),
        ),
        migrations.CreateModel(
            name="BachsPaymentAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tracking_id", models.CharField(db_index=True, max_length=100)),
                ("account_key", models.CharField(db_index=True, max_length=100)),
                ("reference", models.CharField(max_length=255, unique=True)),
                ("checkout_id", models.CharField(blank=True, max_length=255, null=True, unique=True)),
                ("expected_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("expected_currency", models.CharField(default="USD", max_length=10)),
                ("original_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("original_currency", models.CharField(max_length=10)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("underpaid", "Underpaid"), ("expired", "Expired")], default="pending", max_length=20)),
                ("last_event_id", models.CharField(blank=True, default="", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("shipment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="bachs_attempts", to="api.shipment")),
            ],
            options={
                "verbose_name": "Bachs payment attempt",
                "verbose_name_plural": "Bachs payment attempts",
                "ordering": ["-created_at"],
            },
        ),
    ]
