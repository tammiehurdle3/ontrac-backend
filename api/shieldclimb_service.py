import requests
import urllib.parse
from django.conf import settings
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class ShieldClimbService:
    """Service class for ShieldClimb API interactions"""
    
    BASE_URL = "https://api.shieldclimb.com"
    
    @staticmethod
    def convert_to_usd(amount, from_currency):
        """Convert amount from source currency to USD"""
        if from_currency.upper() == 'USD':
            return {'usd_amount': amount, 'exchange_rate': '1.00', 'original_currency': 'USD'}
        try:
            url = f"{ShieldClimbService.BASE_URL}/control/convert.php"
            params = {'from': from_currency.upper(), 'value': str(amount)}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('status') == 'success':
                return {'usd_amount': Decimal(data['value_coin']), 'exchange_rate': data['exchange_rate'], 'original_currency': from_currency.upper()}
            return None
        except Exception as e:
            logger.error(f"Error converting to USD: {str(e)}")
            return None

    @staticmethod
    def create_wallet(shipment_id, callback_url):
        """Create a temporary ShieldClimb wallet"""
        try:
            full_callback = f"{callback_url}?tracking_id={shipment_id}"
            url = f"{ShieldClimbService.BASE_URL}/control/wallet.php"
            params = {
                'address': settings.SHIELDCLIMB_PAYOUT_WALLET,
                'callback': urllib.parse.quote(full_callback, safe='')
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if all(k in data for k in ['address_in', 'polygon_address_in', 'ipn_token']):
                return data
            return None
        except Exception as e:
            logger.error(f"Error creating wallet: {str(e)}")
            return None

    @staticmethod
    def build_checkout_url(address_in, amount_usd, email, currency='USD', use_hosted=True):
        """
        CUSTOM DOMAIN FIX:
        1. Points to 'pay.ontracourier.us' (Your Brand)
        2. Unquotes address first (Prevents the %253D bug)
        3. Sets provider='hosted' (Forces the UI to load, no matter what IP)
        """
        try:
            # 1. Target your Custom Domain
            custom_domain = "pay.ontracourier.us"
            url = f"https://{custom_domain}/pay.php"

            # 2. FIX: Unquote the address so it doesn't get double-encoded
            clean_address = urllib.parse.unquote(address_in)

            params = {
                'address': clean_address,
                'amount': f"{float(amount_usd):.2f}",
                'email': email,
                'currency': 'USD',  # Forces Card UI
                'provider': 'hosted', # CRITICAL: Unlocks the menu on Custom Domains
                'domain': custom_domain,
                'logo': getattr(settings, 'SHIELDCLIMB_LOGO_URL', ''),
                'background': '#f5f7fa',
                'theme': getattr(settings, 'SHIELDCLIMB_THEME_COLOR', '#1778F2'),
                'button': '#1459B1'
            }

            query_string = urllib.parse.urlencode(params)
            return f"{url}?{query_string}"

        except Exception as e:
            logger.error(f"Error building checkout URL: {str(e)}")
            return None

    @staticmethod
    def check_payment_status(ipn_token):
        """Check status via ShieldClimb API"""
        try:
            url = f"{ShieldClimbService.BASE_URL}/control/payment-status.php"
            params = {'ipn_token': ipn_token}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error checking status: {str(e)}")
            return None