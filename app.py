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
import hashlib
import secrets
import jwt
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image
import traceback
import PyPDF2
import re

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
CORS(app)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max
app.config['UPLOAD_FOLDER'] = tempfile.gettempdir()

# PASSWORD CONFIGURATION
ADMIN_PASSWORD_HASH = os.environ.get(
    'ADMIN_PASSWORD_HASH',
    # Default: "BatteryHealth2024"
    'c8d5a8f4b2e1d7c6a5f3b9e2d1c7a8b4f5e3d2c1a9b8e7f6d5c4a3b2e1d0c9f8'
)

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

def hash_password(password):
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password, password_hash):
    """Verify password against hash"""
    return hash_password(password) == password_hash


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
    
    decorated_function.__name__ = f.__name__
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
    """Send email with optional PDF attachment"""
    sender = os.getenv('EMAIL_SENDER')
    password = os.getenv('EMAIL_PASSWORD')
    bcc = os.getenv('EMAIL_BCC', '')
    
    if not all([sender, password]):
        print("Email not configured")
        return False
    
    try:
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = recipient
        msg['Subject'] = subject
        
        if bcc:
            msg['Bcc'] = bcc
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                <img src="https://ottocar.co.uk/logo.png" alt="Otto Car" style="max-width: 150px; margin-bottom: 20px;">
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
        
        msg.attach(MIMEText(html_body, 'html'))
        
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, 'rb') as f:
                pdf_attachment = MIMEApplication(f.read(), _subtype='pdf')
                pdf_attachment.add_header('Content-Disposition', 'attachment', 
                                         filename=os.path.basename(attachment_path))
                msg.attach(pdf_attachment)
        
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(sender, password)
            recipients = [recipient]
            if bcc:
                recipients.append(bcc)
            server.send_message(msg, from_addr=sender, to_addrs=recipients)
        
        return True
    except Exception as e:
        print(f"Email error: {e}")
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
        r'Model[:\s]+([A-Za-z0-9\s\(\)\-]+?)\s+Year:',  # Pattern 1
        r'Model[:\s]+([A-Za-z0-9\s\(\)\-]+?)\n',        # Pattern 2
        r'Model[:\s]*:?\s*([^\n]+?)(?:\s+Year|\n)',     # Pattern 3
        r'Vehicle\s+Model[:\s]+([^\n]+)',               # Pattern 4
    ]
    for i, pattern in enumerate(model_patterns, 1):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            model_text = match.group(1).strip()
            model_text = re.sub(r'\s+', ' ', model_text)
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
    
    # Mileage
    mileage_patterns = [
        r'Distance\s+Traveled[:\s]+([\d,.\s]+)\s*Miles',
        r'Distance\s+Traveled[:\s]+([\d,.\s]+)Miles',
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
                data['mileage'] = f"{int(miles):,} miles"
                print(f"✅ Mileage (pattern {i}): {data['mileage']}")
                break
            except ValueError:
                continue
    
    # State of Charge (SOC)
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
    
    # Test Date
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
    
    # Battery Capacity
    battery_patterns = [
        r'Battery\s+Capacity[:\s]*([^\n]+?)(?:\n|$)',
        r'Capacity[:\s]*([^\n]+?)(?:\n|$)',
        r'Engine[:\s]+([^\s\n]+(?:/[^\s\n]+)?)',
        r'Power[:\s]+([^\n]+?)(?:kW|KW)',
        r'Battery\s+Size[:\s]*([^\n]+)',
    ]
    for i, pattern in enumerate(battery_patterns, 1):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            capacity = match.group(1).strip()
            capacity = ' '.join(capacity.split())
            if capacity and len(capacity) > 0:
                data['battery_capacity'] = capacity
                print(f"✅ Battery Capacity (pattern {i}): {data['battery_capacity']}")
                break
    
    print(f"{'='*60}\n")
    return data


def extract_data_from_pdf(pdf_path):
    """Extract vehicle and battery data from PDF diagnostic report"""
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
        
        # Test date - convert from DD/MM/YYYY to YYYY-MM-DD
        if parsed_data.get('test_date'):
            parts = parsed_data['test_date'].split('/')
            if len(parts) == 3:
                result['test_date'] = f"{parts[2]}-{parts[1]}-{parts[0]}"
        
        if parsed_data.get('make'):
            result['make'] = parsed_data['make']
        
        if parsed_data.get('model'):
            result['model'] = parsed_data['model']
        
        if parsed_data.get('vin'):
            result['vin'] = parsed_data['vin']
        
        if parsed_data.get('mileage'):
            result['mileage'] = parsed_data['mileage']
        
        if parsed_data.get('battery_capacity'):
            result['battery_capacity'] = parsed_data['battery_capacity']
        
        if parsed_data.get('soc'):
            result['state_of_health'] = parsed_data['soc']
        
        print(f"✅ Extracted fields: {list(result.keys())}")
        for key, value in result.items():
            print(f"   {key}: {value}")
        
        return result if result else None
        
    except Exception as e:
        print(f"❌ Error extracting PDF data: {e}")
        print(traceback.format_exc())
        return None


def generate_certificate_pdf(data, output_path, qr_url=None):
    """Generate battery health certificate PDF"""
    
    fonts_loaded = register_fonts()
    font_name = 'CanvaSans' if fonts_loaded else 'Helvetica'
    font_bold = 'CanvaSans-Bold' if fonts_loaded else 'Helvetica-Bold'
    
    c = canvas.Canvas(output_path, pagesize=A4)
    width, height = A4
    
    # Draw background template
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, 'certificate_bg_2.png')
    
    if os.path.exists(template_path):
        c.drawImage(template_path, 0, 0, width=width, height=height, 
                   preserveAspectRatio=True, mask='auto')
    
    c.setFont(font_name, 10)
    
    # Header section (dark blue bar)
    c.setFillColorRGB(1, 1, 1)  # White text
    c.setFont(font_name, 11)
    
    if data.get('test_date'):
        c.drawString(160, 760, data['test_date'])
    
    if data.get('tested_by'):
        c.drawString(370, 760, data['tested_by'])
    
    if data.get('state_of_health'):
        status = get_battery_status(data['state_of_health'])
        c.drawString(730, 760, status)
    
    # Vehicle Information
    c.setFillColorRGB(0.12, 0.14, 0.16)
    c.setFont(font_name, 12)
    
    if data.get('make'):
        c.drawString(160, 580, data['make'])
    
    if data.get('registration'):
        c.drawString(510, 580, data['registration'])
    
    if data.get('model'):
        c.drawString(160, 540, data['model'])
    
    if data.get('first_registered'):
        c.drawString(510, 540, data['first_registered'])
    
    if data.get('vin'):
        c.drawString(160, 500, data['vin'])
    
    if data.get('mileage'):
        c.drawString(510, 500, data['mileage'])
    
    # Battery Health
    c.setFont(font_bold, 14)
    
    if data.get('battery_capacity'):
        capacity_text = f"{data['battery_capacity']} kWh"
        c.drawString(180, 370, capacity_text)
    
    if data.get('state_of_health'):
        soh_text = f"{data['state_of_health']}%"
        c.setFont(font_bold, 18)
        c.drawString(180, 290, soh_text)
    
    # QR Code
    if qr_url:
        qr_img = generate_qr_code(qr_url)
        if qr_img:
            qr_temp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            with open(qr_temp.name, 'wb') as f:
                f.write(qr_img.read())
            
            c.drawImage(qr_temp.name, QR_X, QR_Y, width=QR_SIZE, height=QR_SIZE,
                       preserveAspectRatio=True, mask='auto')
            
            try:
                os.unlink(qr_temp.name)
            except:
                pass
    
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
            'error': str(e)
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
        filename = f"{registration}.pdf"
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
        
        # Email logic: ALWAYS send to BCC, optionally send to recipient
        email_sent_to_bcc = False
        email_sent_to_recipient = False
        
        recipient_email = data.get('recipient_email', '').strip()
        bcc_email = os.getenv('EMAIL_BCC', '').strip()
        
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
            
            # ALWAYS send to BCC email (for storage)
            if bcc_email:
                email_sent_to_bcc = send_email(
                    bcc_email,
                    f"Battery Health Certificate - {data.get('registration')}",
                    email_body,
                    output_path
                )
            
            # Optionally send to recipient email (if provided and different from BCC)
            if recipient_email and recipient_email.lower() != bcc_email.lower():
                email_sent_to_recipient = send_email(
                    recipient_email,
                    f"Battery Health Certificate - {data.get('registration')}",
                    email_body,
                    output_path
                )
        
        print(f"✅ Certificate generated by '{request.user_id}': {filename}")
        if cloudinary_url:
            print(f"   📤 Cloudinary URL: {cloudinary_url}")
        if email_sent_to_bcc:
            print(f"   ✉️ Email sent to BCC: {bcc_email}")
        if email_sent_to_recipient:
            print(f"   ✉️ Email sent to recipient: {recipient_email}")
        
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
            'error': str(e)
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
                filename = f"{registration}.pdf"
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
                
                # Email logic: ALWAYS send to BCC, optionally send to recipient
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
                    
                    # ALWAYS send to BCC
                    if bcc_email:
                        send_email(
                            bcc_email,
                            f"Battery Health Certificate - {cert_data.get('registration')}",
                            email_body,
                            output_path
                        )
                    
                    # Optionally send to recipient (if provided and different)
                    if recipient_email and recipient_email.lower() != bcc_email.lower():
                        send_email(
                            recipient_email,
                            f"Battery Health Certificate - {cert_data.get('registration')}",
                            email_body,
                            output_path
                        )
                
                results['successful'] += 1
                results['files'].append(filename)
                
            except Exception as e:
                print(f"Failed certificate {idx + 1}: {e}")
                results['failed'] += 1
                results['errors'].append(f"Certificate {idx + 1}: {str(e)}")
        
        return jsonify({
            'success': True,
            'results': results
        })
        
    except Exception as e:
        print(f"Batch generation error: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
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
        
        # Check file size
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
            # Add source_pdf field to track where data came from
            extracted_data['source_pdf'] = file.filename
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
            'error': f'Failed to extract data from PDF: {str(e)}'
        }), 500


if __name__ == '__main__':
    print("\n" + "="*60)
    print("🔋 BATTERY HEALTH CERTIFICATE API - STARTING")
    print("="*60)
    print(f"\n✅ Cloudinary: {'Configured' if CLOUDINARY_AVAILABLE and os.getenv('CLOUDINARY_CLOUD_NAME') else 'Not configured'}")
    print(f"✅ Email: {'Configured' if os.getenv('EMAIL_SENDER') else 'Not configured'}")
    print(f"✅ Authentication: Enabled")
    print(f"✅ Default Password: BatteryHealth2024 (CHANGE THIS!)")
    print("\n" + "="*60 + "\n")
    
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)