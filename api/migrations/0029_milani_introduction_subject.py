"""Change only the user's already approved reusable Milani email subject.

No email is sent, queued or approved by this migration. Existing prepared
manual batches detect the updated revision and require human re-review.
"""
from django.db import migrations, transaction
from django.utils import timezone

NAME = 'Milani Creator Introduction - Evergreen'
CAMPAIGN = 'Milani Creator Introductions'
OLD_SUBJECT = 'Milani creator collaboration, {name}'
NEW_SUBJECT = 'A Potential Collaboration with Milani Cosmetics'


def _change_subject(apps, schema_editor, *, old, new):
    Variant = apps.get_model('api', 'MilaniEmailVariant')
    db = schema_editor.connection.alias
    with transaction.atomic(using=db):
        variants = list(
            Variant.objects.using(db).select_for_update().filter(
                name=NAME, campaign_name=CAMPAIGN,
                approval_state='approved', is_active=True,
            )
        )
        # Newly created databases do not contain the production-specific email.
        if not variants:
            return
        if len(variants) != 1:
            raise RuntimeError('Expected exactly one existing approved Milani introduction email.')
        variant = variants[0]
        if variant.subject == new:
            return
        if variant.subject != old:
            raise RuntimeError(
                'The Milani introduction subject changed independently. '
                'Preserve it and review the conflict before proceeding.'
            )
        variant.subject = new
        variant.updated_at = timezone.now()
        variant.save(using=db, update_fields=['subject', 'updated_at'])


def forwards(apps, schema_editor):
    _change_subject(
        apps, schema_editor, old=OLD_SUBJECT, new=NEW_SUBJECT,
    )


def backwards(apps, schema_editor):
    _change_subject(
        apps, schema_editor, old=NEW_SUBJECT, new=OLD_SUBJECT,
    )


class Migration(migrations.Migration):
    dependencies = [('api', '0028_re_review_snapshots')]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
