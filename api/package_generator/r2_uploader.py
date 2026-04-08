import boto3
import os
import uuid
from botocore.config import Config


def upload_to_r2(image_bytes: bytes, filename: str = None) -> str:
    account_id = os.environ.get('R2_ACCOUNT_ID')
    access_key = os.environ.get('R2_ACCESS_KEY_ID')
    secret_key = os.environ.get('R2_SECRET_ACCESS_KEY')
    bucket_name = os.environ.get('R2_BUCKET_NAME', 'ontrac-delivery-photos')
    public_url_base = os.environ.get('R2_PUBLIC_URL', '')

    if not all([account_id, access_key, secret_key]):
        raise ValueError("R2 credentials not configured.")

    if not filename:
        filename = f"delivery_{uuid.uuid4().hex}.jpg"

    s3_client = boto3.client(
        's3',
        endpoint_url=f'https://{account_id}.r2.cloudflarestorage.com',
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version='s3v4'),
        region_name='auto',
    )

    s3_client.put_object(
        Bucket=bucket_name,
        Key=filename,
        Body=image_bytes,
        ContentType='image/jpeg',
    )

    return f"{public_url_base.rstrip('/')}/{filename}"
