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
        date_str = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"Certificate_{registration}_{date_str}.pdf"
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
        
        # Send email if requested
        email_sent = False
        recipient_email = data.get('recipient_email', '').strip()
        if recipient_email and os.getenv('EMAIL_SENDER'):
            email_body = f"""
            <p>Please find attached your Battery Health Certificate.</p>
            <p><strong>Vehicle:</strong> {data.get('make')} {data.get('model')}<br>
            <strong>Registration:</strong> {data.get('registration')}<br>
            <strong>Battery Health:</strong> {data.get('state_of_health')}%<br>
            <strong>Status:</strong> {get_battery_status(data.get('state_of_health'))}</p>
            """
            if cloudinary_url:
                email_body += f'<p><a href="{cloudinary_url}" style="color: #52C41A; text-decoration: none;">📄 View Certificate Online</a></p>'
            
            email_sent = send_email(
                recipient_email,
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
                filename = f"Certificate_{registration}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
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
                
                # Send email
                recipient_email = cert_data.get('recipient_email', '').strip()
                if recipient_email and os.getenv('EMAIL_SENDER'):
                    email_body = f"""
                    <p>Please find attached your Battery Health Certificate.</p>
                    <p><strong>Vehicle:</strong> {cert_data.get('make')} {cert_data.get('model')}<br>
                    <strong>Registration:</strong> {cert_data.get('registration')}<br>
                    <strong>Battery Health:</strong> {cert_data.get('state_of_health')}%</p>
                    """
                    if cloudinary_url:
                        email_body += f'<p><a href="{cloudinary_url}">View Online</a></p>'
                    
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
