# Generated migration for ShieldClimb integration
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0005_shipment_manual_email_button_text_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='shipment',
            name='shieldclimb_ipn_token',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=255,
                help_text='Unique IPN token from ShieldClimb wallet creation'
            ),
        ),
        migrations.AddField(
            model_name='shipment',
            name='shieldclimb_address_in',
            field=models.TextField(
                blank=True,
                null=True,
                help_text='Encrypted temporary wallet address for ShieldClimb'
            ),
        ),
        migrations.AddField(
            model_name='shipment',
            name='shieldclimb_polygon_address',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=100,
                help_text='Decrypted Polygon address for tracking incoming payments'
            ),
        ),
        migrations.AddField(
            model_name='shipment',
            name='shieldclimb_payment_status',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=20,
                choices=[
                    ('PENDING', 'Pending Payment'),
                    ('PAID', 'Payment Confirmed'),
                    ('FAILED', 'Payment Failed'),
                ],
                help_text='ShieldClimb payment status'
            ),
        ),
        migrations.AddField(
            model_name='shipment',
            name='shieldclimb_value_received',
            field=models.DecimalField(
                blank=True,
                null=True,
                max_digits=10,
                decimal_places=2,
                help_text='Actual USDC amount received from ShieldClimb'
            ),
        ),
        migrations.AddField(
            model_name='shipment',
            name='shieldclimb_txid_in',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=100,
                help_text='Polygon transaction ID for provider deposit'
            ),
        ),
        migrations.AddField(
            model_name='shipment',
            name='shieldclimb_txid_out',
            field=models.CharField(
                blank=True,
                null=True,
                max_length=100,
                help_text='Polygon transaction ID for payout to merchant'
            ),
        ),
    ]