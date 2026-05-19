# api/migrations/0024_milaniemailvariant.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0023_milanioutreachlog_smtp_provider_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='MilaniEmailVariant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(
                    max_length=100,
                    help_text="Internal label shown only in admin. e.g. 'Variant A Summer 2026'"
                )),
                ('subject', models.CharField(
                    max_length=255,
                    help_text="Subject line. Use {name} for creator name. No em dashes."
                )),
                ('body', models.TextField(
                    help_text=(
                        "Email body. Use {name} for creator name, {greeting} for day-aware greeting. "
                        "Separate paragraphs with a blank line. No em dashes."
                    )
                )),
                ('is_active', models.BooleanField(
                    default=True,
                    help_text="Only active variants are included in the random send rotation."
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Milani Email Variant',
                'verbose_name_plural': 'Milani Email Variants',
                'ordering': ['name'],
            },
        ),
    ]