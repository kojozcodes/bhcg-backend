"""
Battery Health Certificate Generator - Mobile Backend
Flask API with Authentication, PDF generation, Cloudinary upload, and Email delivery
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
import tempfile
import base64
import uuid
import bcrypt
import secrets
import jwt
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image
import traceback
import PyPDF2
import re
from functools import wraps

# PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import qrcode

# Email
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# Environment
from dotenv import load_dotenv

# Load environment
load_dotenv()

app = Flask(__name__)
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '').split(',')
CORS(app, origins=ALLOWED_ORIGINS)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()

# PASSWORD CONFIGURATION
ADMIN_PASSWORD_HASH = os.environ.get('ADMIN_PASSWORD_HASH')
if not ADMIN_PASSWORD_HASH:
    raise ValueError("ADMIN_PASSWORD_HASH must be set in environment variables")

TOKEN_EXPIRATION_HOURS = 8

# Car database
CAR_DATA = {
    "BYD": ["Sealion 7"],
    "Hyundai": ["Ioniq", "Tucson"],
    "KIA": ["EV6", "Niro EV", "Niro (DE EV)", "Sportage", "Xeed"],
    "MG": ["MG-5", "MG-ZS"],
    "Nissan": ["Ariya", "Leaf"],
    "Polestar": ["Polestar 2"],
    "Skoda": ["Enyaq", "Octavia"],
    "Tesla": ["Model 3", "Model Y"],
    "Toyota": ["BZ4X", "Corolla", "Prius"],
    "Volkswagen": ["ID3", "ID4", "ID7", "ID-Buzz"]
}

# PDF Configuration
QR_SIZE = 75
QR_X = 490
QR_Y = 741

# Cloudinary (optional)
try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
    
    if all([os.getenv('CLOUDINARY_CLOUD_NAME'), 
            os.getenv('CLOUDINARY_API_KEY'), 
            os.getenv('CLOUDINARY_API_SECRET')]):
        cloudinary.config(
            cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
            api_key=os.getenv('CLOUDINARY_API_KEY'),
            api_secret=os.getenv('CLOUDINARY_API_SECRET')
        )
        print("✅ Cloudinary configured")
except ImportError:
    CLOUDINARY_AVAILABLE = False
    print("⚠️ Cloudinary not available")


# ============================================================================
# AUTHENTICATION
# ============================================================================

def verify_password(password, password_hash):
    """Verify password against bcrypt hash"""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def generate_token(user_id='admin'):
    """Generate JWT token"""
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(hours=TOKEN_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')
    return token


def verify_token(token):
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({'success': False, 'error': 'No authorization token provided'}), 401
        
        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify({'success': False, 'error': 'Invalid authorization header'}), 401
        
        payload = verify_token(token)
        if not payload:
            return jsonify({'success': False, 'error': 'Invalid or expired token'}), 401
        
        request.user_id = payload['user_id']
        
        return f(*args, **kwargs)
    
    return decorated_function


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def register_fonts():
    """Register custom fonts for PDF generation"""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        
        regular_path = os.path.join(script_dir, 'canva-sans-regular.ttf')
        bold_path = os.path.join(script_dir, 'canva-sans-bold.ttf')
        
        if os.path.exists(regular_path):
            pdfmetrics.registerFont(TTFont('CanvaSans', regular_path))
        
        if os.path.exists(bold_path):
            pdfmetrics.registerFont(TTFont('CanvaSans-Bold', bold_path))
            
        return True
    except Exception as e:
        print(f"Font registration warning: {e}")
        return False


def get_battery_status(state_of_health):
    """Get battery status from health percentage"""
    soh = int(state_of_health)
    if soh >= 85:
        return "Excellent"
    elif soh >= 65:
        return "Good"
    else:
        return "Bad"


def generate_qr_code(url):
    """Generate QR code image"""
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        return buffer
    except Exception as e:
        print(f"QR generation error: {e}")
        return None


def upload_to_cloudinary(pdf_path, cert_id):
    """Upload PDF to Cloudinary and return URL"""
    if not CLOUDINARY_AVAILABLE:
        return None
    
    try:
        response = cloudinary.uploader.upload(
            pdf_path,
            resource_type='raw',
            public_id=f'battery-certificates/{cert_id}',
            overwrite=True
        )
        return response.get('secure_url')
    except Exception as e:
        print(f"Cloudinary upload error: {e}")
        return None


def send_email(recipient, subject, body, attachment_path=None):
    """Send email via Gmail SMTP with optional PDF attachment.
    
    Always ensures evcertificate@ottocar.co.uk receives the email:
    - If recipient IS that address, sends only to them.
    - If recipient is someone else, sends to that person AND BCCs evcertificate@ottocar.co.uk.
    """
    
    try:
        smtp_user     = os.getenv('EMAIL_SENDER')
        smtp_password = os.getenv('EMAIL_PASSWORD')
        bcc_email     = os.getenv('EMAIL_BCC', 'evcertificate@ottocar.co.uk').strip()

        if not smtp_user or not smtp_password:
            print("❌ EMAIL_SENDER or EMAIL_PASSWORD not configured in environment variables")
            return False

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f"Hv Battery <{smtp_user}>"
        msg['To']      = recipient

        recipients = [recipient]
        if bcc_email and bcc_email.lower() != recipient.lower():
            msg['Bcc'] = bcc_email
            recipients.append(bcc_email)
            print(f"📧 BCC set to: {bcc_email}")

        html_content = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <img src="https://ottocar.co.uk/logo.png" alt="Otto Car"
                     style="max-width: 150px; margin-bottom: 20px;">
                <h2 style="color: #52C41A;">Battery Health Certificate</h2>
                {body}
                <hr style="border: none; border-top: 1px solid #e0e0e0; margin: 20px 0;">
                <p style="color: #666; font-size: 14px;">
                    Best regards,<br>
                    <strong>Otto Car Team</strong><br>
                    <a href="https://ottocar.co.uk" style="color: #52C41A;">ottocar.co.uk</a>
                </p>
            </div>
        </body>
        </html>
        """
        msg.attach(MIMEText(html_content, 'html'))

        if attachment_path and os.path.exists(attachment_path):
            try:
                with open(attachment_path, 'rb') as f:
                    part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
                part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
                msg.attach(part)
                print(f"📎 PDF attachment added: {os.path.basename(attachment_path)}")
            except Exception as e:
                print(f"⚠️ Warning: Could not attach PDF: {e}")

        print(f"📧 Sending email to {recipients} via Gmail SMTP (port 587)...")
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, recipients, msg.as_string())

        print(f"✅ Email sent successfully via Gmail SMTP!")
        print(f"   Recipients: {recipients}")
        return True

    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Gmail SMTP authentication failed: {e}")
        print("   Use a Gmail App Password, not your account password.")
        print("   Generate one at: https://myaccount.google.com/apppasswords")
        return False

    except Exception as e:
        print(f"❌ Unexpected email error: {e}")
        import traceback
        traceback.print_exc()
        return False

def extract_from_filename(filename):
    """Extract data from filename - works for any car brand"""
    data = {}
    
    # Try multiple filename patterns
    patterns = [
        r'([A-Z][A-Za-z]+)-([A-Za-z0-9\s]+?)(?:EEV)?-(\d{4})',  # Brand-Model-Year
        r'([A-Z][A-Za-z]+)[-_]([A-Za-z0-9\s]+?)[-_](\d{4})',    # Brand_Model_Year
        r'([A-Z]{2,})-([A-Za-z0-9]+)-(\d{4})',                   # BRAND-Model-Year (uppercase brand)
    ]
    
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            data['make'] = match.group(1)
            data['model'] = match.group(2)
            data['year'] = match.group(3)
            break
    
    return data


def parse_diagnostic_report(text, filename=""):
    """Parse diagnostic report text - works for any vehicle brand"""
    data = {
        'vin': '', 'make': '', 'model': '', 'year': '',
        'mileage': '', 'battery_capacity': '', 'soc': '', 'test_date': ''
    }
    
    print(f"\n{'='*60}")
    print(f"PARSING DIAGNOSTIC REPORT")
    print(f"{'='*60}")
    
    # Try to extract from filename first (optional, as fallback)
    if filename:
        filename_data = extract_from_filename(filename)
        if filename_data:
            print(f"📂 From filename: {filename_data}")
            data.update({k: v for k, v in filename_data.items() if v})
    
    # VIN - standard 17 character format
    vin_patterns = [
        r'VIN[:\s]*([A-HJ-NPR-Z0-9]{17})',
        r'Vehicle\s+Identification\s+Number[:\s]*([A-HJ-NPR-Z0-9]{17})',
        r'Chassis\s+Number[:\s]*([A-HJ-NPR-Z0-9]{17})',
    ]
    for pattern in vin_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['vin'] = match.group(1)
            print(f"✅ VIN: {data['vin']}")
            break
    
    # Make - any car brand
    make_patterns = [
        r'Make[:\s]+([A-Za-z]+)',
        r'Manufacturer[:\s]+([A-Za-z]+)',
        r'Brand[:\s]+([A-Za-z]+)',
    ]
    for pattern in make_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['make'] = match.group(1).upper()
            print(f"✅ Make: {data['make']}")
            break
    
    # Model - capture with parentheses and special characters
    model_patterns = [
        r'Model[:\s]+([A-Za-z0-9\s\(\)\-]+?)\s+Year:',  # Pattern 1: "Model: Niro (DE EV) Year:"
        r'Model[:\s]+([A-Za-z0-9\s\(\)\-]+?)\n',        # Pattern 2: "Model: Niro (DE EV)\n"
        r'Model[:\s]*:?\s*([^\n]+?)(?:\s+Year|\n)',     # Pattern 3: More flexible
        r'Vehicle\s+Model[:\s]+([^\n]+)',               # Pattern 4: "Vehicle Model:"
    ]
    for i, pattern in enumerate(model_patterns, 1):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            model_text = match.group(1).strip()
            # Clean up extra whitespace
            model_text = re.sub(r'\s+', ' ', model_text)
            # Remove any trailing punctuation or junk
            model_text = re.sub(r'[:\s]+$', '', model_text)
            if model_text and len(model_text) > 1:
                data['model'] = model_text
                print(f"✅ Model (pattern {i}): {data['model']}")
                break
    
    # Year
    year_patterns = [
        r'Year[:\s]+(\d{4})',
        r'Model\s+Year[:\s]+(\d{4})',
    ]
    for pattern in year_patterns:
        match = re.search(pattern, text)
        if match:
            data['year'] = match.group(1)
            print(f"✅ Year: {data['year']}")
            break
    
    # Mileage - multiple formats
    mileage_patterns = [
        r'Distance\s+Traveled[:\s]+([\d,.\s]+)\s*Miles',
        r'Distance\s+Traveled[:\s]+([\d,.\s]+)Miles',  # No space before "Miles"
        r'Mileage[:\s]+([\d,.\s]+)\s*(?:miles|km)?',
        r'Odometer[:\s]+([\d,.\s]+)\s*(?:miles|km)?',
        r'Total\s+Distance[:\s]+([\d,.\s]+)\s*Miles',
    ]
    for i, pattern in enumerate(mileage_patterns, 1):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            miles_str = match.group(1).replace(',', '').replace(' ', '').strip()
            try:
                miles = float(miles_str)
                # Format with comma separators
                data['mileage'] = f"{int(miles):,} miles"
                print(f"✅ Mileage (pattern {i}): {data['mileage']}")
                break
            except ValueError:
                continue
    
    # State of Charge (SOC) - this is what we use for state_of_health
    soc_patterns = [
        r'Display\s+state\s+of\s+charge\s*\(?\s*SOC\s*\)?[:\s]*(\d+)\s*%',
        r'State\s+of\s+Charge[:\s]*(\d+)\s*%',
        r'SOC[:\s]*(\d+)\s*%',
    ]
    for pattern in soc_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            soc_value = int(match.group(1))
            if 0 <= soc_value <= 100:
                data['soc'] = soc_value
                print(f"✅ SOC (used as State of Health): {data['soc']}%")
                break
    
    # Test Date - multiple date formats
    date_patterns = [
        r'Date\s+Created[:\s]*(\d{4})[/-](\d{2})[/-](\d{2})',
        r'Test\s+Date[:\s]*(\d{4})[/-](\d{2})[/-](\d{2})',
        r'Diagnostic\s+Date[:\s]*(\d{4})[/-](\d{2})[/-](\d{2})',
        r'Report\s+Date[:\s]*(\d{4})[/-](\d{2})[/-](\d{2})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            y, m, d = match.groups()
            data['test_date'] = f"{d}/{m}/{y}"
            print(f"✅ Test Date: {data['test_date']}")
            break
    
    # Battery Capacity - capture as-is (don't convert)
    battery_patterns = [
        r'Battery\s+Capacity[:\s]*([^\n]+?)(?:\n|$)',
        r'Capacity[:\s]*([^\n]+?)(?:\n|$)',
        r'Engine[:\s]+([^\s\n]+(?:/[^\s\n]+)?)',  # For diagnostic reports
        r'Power[:\s]+([^\n]+?)(?:kW|KW)',
        r'Battery\s+Size[:\s]*([^\n]+)',
    ]
    for i, pattern in enumerate(battery_patterns, 1):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            capacity = match.group(1).strip()
            # Clean up - remove extra whitespace
            capacity = ' '.join(capacity.split())
            if capacity and len(capacity) > 0:
                data['battery_capacity'] = capacity
                print(f"✅ Battery Capacity (pattern {i}): {data['battery_capacity']}")
                break
    
    print(f"{'='*60}\n")
    return data


def extract_data_from_pdf(pdf_path):
    """Extract vehicle and battery data from PDF diagnostic report - Universal for all brands"""
    try:
        filename = os.path.basename(pdf_path)
        
        # Extract text from PDF
        text = ""
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page in pdf_reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        
        if not text.strip():
            print("❌ PDF appears to be empty or contains only images")
            return None
        
        print(f"📄 Extracted text length: {len(text)} characters")
        
        # Parse the diagnostic report
        parsed_data = parse_diagnostic_report(text, filename)
        
        # Convert to expected output format
        result = {}
        
        # Test date - convert from DD/MM/YYYY to YYYY-MM-DD for HTML date input
        if parsed_data.get('test_date'):
            parts = parsed_data['test_date'].split('/')
            if len(parts) == 3:
                result['test_date'] = f"{parts[2]}-{parts[1]}-{parts[0]}"
        
        # Make and Model
        if parsed_data.get('make'):
            result['make'] = parsed_data['make']
        
        if parsed_data.get('model'):
            result['model'] = parsed_data['model']
        
        # VIN
        if parsed_data.get('vin'):
            result['vin'] = parsed_data['vin']
        
        # Mileage (already formatted)
        if parsed_data.get('mileage'):
            result['mileage'] = parsed_data['mileage']
        
        # Battery Capacity - return as-is, no conversion
        if parsed_data.get('battery_capacity'):
            result['battery_capacity'] = parsed_data['battery_capacity']
        
        # State of Health - use SOC (Display state of charge)
        if parsed_data.get('soc'):
            result['state_of_health'] = parsed_data['soc']
        
        print(f"✅ Extracted fields: {list(result.keys())}")
        for key, value in result.items():
            print(f"   {key}: {value}")
        
        return result if result else None
        
    except Exception as e:
        print(f"❌ Error extracting PDF data: {e}")
        import traceback
        print(traceback.format_exc())
        return None


def generate_certificate_pdf(data, output_path, qr_url=None):
    """Generate battery health certificate PDF - Using main.py's perfect implementation"""
    
    fonts_loaded = register_fonts()
    font_bold = 'CanvaSans-Bold' if fonts_loaded else 'Helvetica-Bold'
    font_regular = 'CanvaSans' if fonts_loaded else 'Helvetica'
    
    # Ensure both fonts are available
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if font_bold == "CanvaSans-Bold":
        canva_regular_ttf = os.path.join(script_dir, "canva-sans-regular.ttf")
        if os.path.exists(canva_regular_ttf):
            try:
                pdfmetrics.registerFont(TTFont("CanvaSans", canva_regular_ttf))
                font_regular = "CanvaSans"
            except:
                font_regular = font_bold
        else:
            font_regular = font_bold
    else:
        font_regular = font_bold.replace("-Bold", "")
    
    # Verify fonts are registered
    available_fonts = pdfmetrics.getRegisteredFontNames()
    if font_regular not in available_fonts:
        font_regular = font_bold
    if font_bold not in available_fonts:
        font_bold = "Helvetica-Bold"
        font_regular = "Helvetica"
    
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4
    
    # Draw background template
    template_path = os.path.join(script_dir, 'certificate_bg_2.png')
    if os.path.exists(template_path):
        c.drawImage(template_path, 0, 0, width=width, height=height, mask='auto')
    
    # QR Code
    if qr_url:
        qr_img = generate_qr_code(qr_url)
        if qr_img:
            temp_qr = "temp_qr.png"
            with open(temp_qr, "wb") as f:
                f.write(qr_img.read())
            c.drawImage(temp_qr, QR_X, QR_Y, width=QR_SIZE, height=QR_SIZE, mask="auto")
            if os.path.exists(temp_qr):
                os.remove(temp_qr)
    
    # Header section - Test Date, Tested By, Status
    c.setFont(font_bold, 15)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(90, height - 216, data.get('test_date', ''))
    
    c.setFont(font_bold, 15)
    c.setFillColorRGB(1, 1, 1)  # White text for Tested By
    c.drawString(270, height - 216, data.get('tested_by', ''))
    
    status = get_battery_status(data.get('state_of_health', 90))
    c.setFont(font_bold, 15)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(480, height - 215, status)
    
    # Vehicle Information section
    c.setFont(font_bold, 15)
    c.drawString(85, height - 354, data.get('make', ''))
    c.drawString(405, height - 354, data.get('registration', ''))
    c.drawString(85, height - 392, data.get('model', ''))
    c.drawString(405, height - 392, data.get('first_registered', ''))
    c.drawString(85, height - 431, data.get('vin', ''))
    c.drawString(405, height - 432, data.get('mileage', ''))
    
    # Battery Capacity
    c.setFont(font_bold, 22)
    battery_text = data.get('battery_capacity', '')
    c.drawString(180, height - 585, battery_text)
    
    # State of Health Progress Bar
    bar_x, bar_y, bar_width, bar_height = 180, height - 675, 300, 30
    
    try:
        percent_value = int(str(data.get('state_of_health', 90)).replace("%", "").strip())
    except ValueError:
        percent_value = 90
    
    # Draw background bar (gray)
    c.setFillColorRGB(0.9, 0.9, 0.9)
    c.roundRect(bar_x, bar_y, bar_width, bar_height, radius=5, fill=1, stroke=1)
    
    # Draw filled bar with color based on health
    if percent_value > 85:
        c.setFillColorRGB(0, 0.7, 0)  # Green
    elif percent_value >= 65:
        c.setFillColorRGB(1, 0.65, 0)  # Orange
    else:
        c.setFillColorRGB(0.9, 0, 0)  # Red
    
    fill_width = (bar_width * percent_value) / 100
    c.roundRect(bar_x, bar_y, fill_width, bar_height, radius=5, fill=1, stroke=0)
    
    # Draw percentage box (white box with black text)
    c.setFillColorRGB(1, 1, 1)  # White background
    c.roundRect(bar_x + bar_width + 5, bar_y, 80, bar_height, radius=5, fill=1, stroke=1)
    
    # Draw percentage text
    c.setFont(font_bold, 20)
    c.setFillColorRGB(0, 0, 0)  # Black text
    c.drawCentredString(bar_x - 12.5 + bar_width + 60, bar_y + 8, f"{percent_value}%")
    
    c.save()
    return output_path


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'message': 'Battery Health Certificate API Running',
        'cloudinary': CLOUDINARY_AVAILABLE and bool(os.getenv('CLOUDINARY_CLOUD_NAME')),
        'email': bool(os.getenv('EMAIL_SENDER'))
    })


@app.route('/api/login', methods=['POST'])
def login():
    """Login endpoint"""
    try:
        data = request.json
        password = data.get('password', '')
        
        if not password:
            return jsonify({
                'success': False,
                'error': 'Password is required'
            }), 400
        
        if not verify_password(password, ADMIN_PASSWORD_HASH):
            import time
            time.sleep(1)
            return jsonify({
                'success': False,
                'error': 'Invalid password'
            }), 401
        
        token = generate_token()
        
        return jsonify({
            'success': True,
            'token': token,
            'expires_in': TOKEN_EXPIRATION_HOURS * 3600
        })
        
    except Exception as e:
        print(f"Login error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Login failed'
        }), 500


@app.route('/api/verify-token', methods=['GET'])
@require_auth
def verify_token_endpoint():
    """Verify token endpoint"""
    return jsonify({
        'success': True,
        'user_id': request.user_id
    })


@app.route('/api/car-data', methods=['GET'])
@require_auth
def get_car_data():
    """Get car makes and models"""
    return jsonify({
        'success': True,
        'data': CAR_DATA
    })


@app.route('/api/validate', methods=['POST'])
@require_auth
def validate_certificate():
    """Validate certificate data"""
    try:
        data = request.json
        errors = []
        
        if not data.get('tested_by', '').strip():
            errors.append('Tested By is required')
        if not data.get('make', '').strip():
            errors.append('Make is required')
        if not data.get('model', '').strip():
            errors.append('Model is required')
        if not data.get('registration', '').strip():
            errors.append('Registration is required')
        if not data.get('battery_capacity', '').strip():
            errors.append('Battery Capacity is required')
        
        is_valid = len(errors) == 0
        
        return jsonify({
            'success': True,
            'is_valid': is_valid,
            'errors': errors
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': 'Validation failed'
        }), 500


@app.route('/api/generate-certificate', methods=['POST'])
@require_auth
def generate_certificate():
    """Generate single certificate PDF"""
    try:
        data = request.json
        
        # Validate
        errors = []
        if not data.get('tested_by'):
            errors.append('Tested By is required')
        if not data.get('make'):
            errors.append('Make is required')
        if not data.get('model'):
            errors.append('Model is required')
        if not data.get('registration'):
            errors.append('Registration is required')
        if not data.get('battery_capacity'):
            errors.append('Battery Capacity is required')
        
        if errors:
            return jsonify({
                'success': False,
                'errors': errors
            }), 400
        
        cert_id = str(uuid.uuid4())
        registration = data.get('registration', 'UNKNOWN').strip().upper().replace(' ', '')
        filename = f"{registration}.pdf"  # Changed: simplified filename
        output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Generate without QR first
        temp_pdf = output_path + '.temp'
        generate_certificate_pdf(data, temp_pdf)
        
        # Upload to Cloudinary
        cloudinary_url = None
        if CLOUDINARY_AVAILABLE and os.getenv('CLOUDINARY_CLOUD_NAME'):
            cloudinary_url = upload_to_cloudinary(temp_pdf, cert_id)
        
        # Re-generate with QR if URL available
        if cloudinary_url:
            generate_certificate_pdf(data, output_path, qr_url=cloudinary_url)
            try:
                os.unlink(temp_pdf)
            except:
                pass
        else:
            os.rename(temp_pdf, output_path)
        
        # ALWAYS send email (changed behavior)
        email_sent = False
        recipient_email = data.get('recipient_email', '').strip()
        bcc_email = os.getenv('EMAIL_BCC', '').strip()
        
        # Determine who to send to
        if os.getenv('EMAIL_SENDER'):
            email_body = f"""
            <p>Please find attached your Battery Health Certificate.</p>
            <p><strong>Vehicle:</strong> {data.get('make')} {data.get('model')}<br>
            <strong>Registration:</strong> {data.get('registration')}<br>
            <strong>Battery Health:</strong> {data.get('state_of_health')}%<br>
            <strong>Status:</strong> {get_battery_status(data.get('state_of_health'))}</p>
            """
            if cloudinary_url:
                email_body += f'<p><a href="{cloudinary_url}" style="color: #52C41A; text-decoration: none;">📄 View Certificate Online</a></p>'
            
            # Determine primary recipient - prefer user email, fallback to BCC email
            send_to = recipient_email if recipient_email else bcc_email
            
            if send_to:
                email_sent = send_email(
                    send_to,
                    f"Battery Health Certificate - {data.get('registration')}",
                    email_body,
                    output_path
                )
        
        print(f"✅ Certificate generated by '{request.user_id}': {filename}")
        if cloudinary_url:
            print(f"   📤 Cloudinary URL: {cloudinary_url}")
        if email_sent:
            print(f"   ✉️ Email sent to: {recipient_email}")
        
        return send_file(
            output_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"Error generating certificate: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': 'Certificate generation failed'
        }), 500


@app.route('/api/batch-generate', methods=['POST'])
@require_auth
def batch_generate():
    """Generate multiple certificates"""
    try:
        certificates = request.json.get('certificates', [])
        
        if not certificates:
            return jsonify({
                'success': False,
                'error': 'No certificates provided'
            }), 400
        
        results = {
            'successful': 0,
            'failed': 0,
            'files': [],
            'errors': []
        }
        
        for idx, cert_data in enumerate(certificates):
            try:
                # Validate
                if not all([cert_data.get('tested_by'), 
                           cert_data.get('make'),
                           cert_data.get('model'),
                           cert_data.get('registration'),
                           cert_data.get('battery_capacity')]):
                    results['failed'] += 1
                    results['errors'].append(f"Certificate {idx + 1}: Missing required fields")
                    continue
                
                cert_id = str(uuid.uuid4())
                registration = cert_data.get('registration', '').strip().upper().replace(' ', '')
                filename = f"{registration}.pdf"  # Changed: simplified filename
                output_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                temp_pdf = output_path + '.temp'
                generate_certificate_pdf(cert_data, temp_pdf)
                
                cloudinary_url = None
                if CLOUDINARY_AVAILABLE and os.getenv('CLOUDINARY_CLOUD_NAME'):
                    cloudinary_url = upload_to_cloudinary(temp_pdf, cert_id)
                
                if cloudinary_url:
                    generate_certificate_pdf(cert_data, output_path, qr_url=cloudinary_url)
                    try:
                        os.unlink(temp_pdf)
                    except:
                        pass
                else:
                    os.rename(temp_pdf, output_path)
                
                # ALWAYS send email (changed behavior)
                recipient_email = cert_data.get('recipient_email', '').strip()
                bcc_email = os.getenv('EMAIL_BCC', '').strip()
                
                if os.getenv('EMAIL_SENDER'):
                    email_body = f"""
                    <p>Please find attached your Battery Health Certificate.</p>
                    <p><strong>Vehicle:</strong> {cert_data.get('make')} {cert_data.get('model')}<br>
                    <strong>Registration:</strong> {cert_data.get('registration')}<br>
                    <strong>Battery Health:</strong> {cert_data.get('state_of_health')}%</p>
                    """
                    if cloudinary_url:
                        email_body += f'<p><a href="{cloudinary_url}">View Online</a></p>'
                    
                    # Determine primary recipient - prefer user email, fallback to BCC email
                    send_to = recipient_email if recipient_email else bcc_email
                    
                    if send_to:
                        send_email(
                            send_to,
                            f"Battery Health Certificate - {cert_data.get('registration')}",
                            email_body,
                            output_path
                        )
                
                results['successful'] += 1
                results['files'].append(filename)
                
            except Exception as e:
                print(f"Failed certificate {idx + 1}: {e}")
                results['failed'] += 1
                results['errors'].append(f"Certificate {idx + 1}: Generation failed")
        
        return jsonify({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        print(f"Batch generation error: {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Batch generation failed'
        }), 500


@app.route('/api/extract-pdf', methods=['POST'])
@require_auth
def extract_pdf():
    """Extract certificate data from uploaded PDF"""
    try:
        # Check if file is in request
        if 'file' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file uploaded'
            }), 400
        
        file = request.files['file']
        
        # Check if file is selected
        if file.filename == '':
            return jsonify({
                'success': False,
                'error': 'No file selected'
            }), 400
        
        # Validate file type
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({
                'success': False,
                'error': 'Only PDF files are allowed'
            }), 400
        
        # Save temporarily
        temp_filename = f'temp_{uuid.uuid4()}_{file.filename}'
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], temp_filename)
        
        print(f"📤 Receiving PDF: {file.filename}")
        file.save(temp_path)
        
        # Check file size (should be under MAX_CONTENT_LENGTH)
        file_size = os.path.getsize(temp_path)
        print(f"📏 File size: {file_size / 1024:.2f} KB")
        
        # Extract data
        extracted_data = extract_data_from_pdf(temp_path)
        
        # Clean up temp file
        try:
            os.unlink(temp_path)
            print(f"🗑️  Cleaned up temp file")
        except Exception as e:
            print(f"⚠️  Warning: Could not delete temp file: {e}")
        
        if extracted_data:
            print(f"✅ Successfully extracted data from {file.filename}")
            return jsonify({
                'success': True,
                'data': extracted_data,
                'message': f'Successfully extracted {len(extracted_data)} fields from PDF'
            })
        else:
            print(f"⚠️  No data extracted from {file.filename}")
            return jsonify({
                'success': False,
                'error': 'Could not extract any data from PDF. Please ensure it\'s a valid battery health certificate.'
            }), 400
            
    except Exception as e:
        print(f"❌ PDF extraction error: {str(e)}")
        print(traceback.format_exc())
        
        # Clean up temp file if it exists
        try:
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.unlink(temp_path)
        except:
            pass
        
        return jsonify({
            'success': False,
            'error': 'Error processing PDF'
        }), 500


if __name__ == '__main__':
    print("\n" + "="*60)
    print("🔋 BATTERY HEALTH CERTIFICATE API - STARTING")
    print("="*60)
    print(f"\n✅ Cloudinary: {'Configured' if CLOUDINARY_AVAILABLE and os.getenv('CLOUDINARY_CLOUD_NAME') else 'Not configured'}")
    print(f"✅ Email: {'Configured' if os.getenv('EMAIL_SENDER') else 'Not configured'}")
    print(f"✅ Authentication: Enabled")
    print("\n" + "="*60 + "\n")
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)