import os
import secrets
import smtplib
from email.message import EmailMessage
from PIL import Image as PILImage
from PIL import ImageOps
import pillow_heif
from werkzeug.utils import secure_filename
import logging

from models import Setting

logger = logging.getLogger(__name__)

# Register HEIF support
pillow_heif.register_heif_opener()

# Include common image formats plus ICO for browser favicons
ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'heic', 'heif', 'ico'
}

def allowed_file(filename):
    """Check if uploaded file has an allowed extension"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_uploaded_image(file, upload_folder):
    """
    Save uploaded image file, converting HEIC/HEIF to JPEG if needed
    Returns the saved filename or None if failed
    """
    try:
        # Secure the filename
        original_filename = secure_filename(file.filename)
        file_ext = original_filename.rsplit('.', 1)[1].lower()
        
        # Generate unique filename
        unique_filename = f"{secrets.token_hex(16)}.{file_ext}"
        file_path = os.path.join(upload_folder, unique_filename)
        
        # Save the file temporarily
        file.save(file_path)
        
        try:
            # Open with PIL to validate and potentially convert
            with PILImage.open(file_path) as img:
                # Convert HEIC/HEIF to JPEG
                if file_ext in ['heic', 'heif']:
                    # Convert to RGB if necessary
                    if img.mode in ['RGBA', 'LA', 'P']:
                        img = img.convert('RGB')
                    
                    # Save as JPEG
                    jpeg_filename = f"{secrets.token_hex(16)}.jpg"
                    jpeg_path = os.path.join(upload_folder, jpeg_filename)
                    img.save(jpeg_path, 'JPEG', quality=90, optimize=True)
                    
                    # Remove original HEIC file
                    os.unlink(file_path)
                    
                    return jpeg_filename
                else:
                    # For other formats, apply basic optimizations
                    # Auto-rotate based on EXIF
                    img = ImageOps.exif_transpose(img)
                    
                    # Re-save with optimization
                    if file_ext in ['jpg', 'jpeg']:
                        img.save(file_path, 'JPEG', quality=90, optimize=True)
                    elif file_ext == 'png':
                        img.save(file_path, 'PNG', optimize=True)
                    else:
                        img.save(file_path)
                    
                    return unique_filename
        
        except Exception as e:
            logger.error(f"Error processing image {original_filename}: {str(e)}")
            # Clean up
            if os.path.exists(file_path):
                os.unlink(file_path)
            return None
    
    except Exception as e:
        logger.error(f"Error saving image: {str(e)}")
        return None

def cents_to_dollars(cents):
    """Convert cents (integer) to dollars (float)"""
    if cents is None:
        return 0.0
    return cents / 100.0

def dollars_to_cents(dollars):
    """Convert dollars (float) to cents (integer)"""
    if dollars is None:
        return 0
    return int(round(dollars * 100))


def send_email(to_email, subject, body, html_body=None):
    """Send an email using SMTP settings stored in Setting.

    Args:
        to_email: Recipient address.
        subject: Email subject line.
        body: Plain-text body for clients that do not render HTML.
        html_body: Optional HTML version of the message. If provided it will
            be sent as an alternative MIME part so capable clients render the
            HTML while others fall back to the plain text ``body``.
    """
    host = Setting.get('smtp_host')
    port = Setting.get('smtp_port', '587')
    username = Setting.get('smtp_username')
    password = Setting.get('smtp_password')
    use_tls = Setting.get('smtp_use_tls', 'on') == 'on'
    use_ssl = Setting.get('smtp_use_ssl', 'off') == 'on'
    from_email = Setting.get('smtp_from_email') or username

    if not host or not port or not from_email:
        logger.error('SMTP settings are incomplete.')
        return False

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, int(port))
        else:
            server = smtplib.SMTP(host, int(port))
            if use_tls:
                server.starttls()
        if username and password:
            server.login(username, password)

        msg = EmailMessage()
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email
        msg.set_content(body)
        if html_body:
            msg.add_alternative(html_body, subtype='html')

        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        logger.error(f'Error sending email: {e}')
        return False
