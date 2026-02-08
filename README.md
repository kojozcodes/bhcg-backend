# 🔋 Battery Health Certificate - Backend

Flask API for generating battery health certificates with authentication, PDF generation, and cloud integration.

---

## 📋 SETUP

### Local Development:
```bash
# Install dependencies
pip install -r requirements.txt

# Copy assets (IMPORTANT!)
# Copy these files to this directory:
# - certificate_bg_2.png
# - BHG_logo.png (optional)
# - canva-sans-regular.ttf
# - canva-sans-bold.ttf

# Configure .env
# Edit .env file and set your credentials

# Run
python app.py
# Runs on http://localhost:5000
```

---

## 🚀 DEPLOYMENT (Railway)

### Step 1: Create Railway Project
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Initialize
railway init

# Link to project
railway link
```

### Step 2: Upload Required Files
```
✅ app.py
✅ requirements.txt
✅ Procfile
✅ railway.json
✅ .env
✅ certificate_bg_2.png (REQUIRED!)
✅ canva-sans-regular.ttf
✅ canva-sans-bold.ttf
```

### Step 3: Set Environment Variables in Railway Dashboard
```
ADMIN_PASSWORD_HASH=your_hash_here
SECRET_KEY=your_secret_key_here
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret
EMAIL_SENDER=your_email@domain.com
EMAIL_PASSWORD=your_app_password
EMAIL_BCC=bcc@domain.com
```

### Step 4: Deploy
```bash
railway up
```

### Step 5: Get Deployment URL
```bash
railway domain
# Copy the URL (e.g., https://your-app.up.railway.app)
```

---

## 🔐 PASSWORD MANAGEMENT

### Default Password:
```
BatteryHealth2024
```

### Change Password:
```bash
# Generate new hash
python3 -c "import hashlib; print(hashlib.sha256('YOUR_NEW_PASSWORD'.encode()).hexdigest())"

# Copy the hash and set in Railway:
# Variables → ADMIN_PASSWORD_HASH → paste hash
```

---

## 📡 API ENDPOINTS

### Authentication:
- `POST /api/login` - Login with password
- `GET /api/verify-token` - Verify JWT token

### Data:
- `GET /api/car-data` - Get car makes/models
- `POST /api/validate` - Validate certificate data

### Generation:
- `POST /api/generate-certificate` - Generate single PDF
- `POST /api/batch-generate` - Generate multiple PDFs

### Health:
- `GET /health` - Check API status

---

## ✅ TESTING

```bash
# Health check
curl https://your-api.railway.app/health

# Login
curl -X POST https://your-api.railway.app/api/login \
  -H "Content-Type: application/json" \
  -d '{"password": "BatteryHealth2024"}'

# Get car data (with token)
curl https://your-api.railway.app/api/car-data \
  -H "Authorization: Bearer YOUR_TOKEN_HERE"
```

---

## 📁 REQUIRED FILES

### Must be in backend directory:
1. ✅ **certificate_bg_2.png** - PDF template background
2. ✅ **canva-sans-regular.ttf** - Font file
3. ✅ **canva-sans-bold.ttf** - Font file (bold)

### Without these files:
- ❌ PDFs will generate without template/fonts
- ⚠️ Will use default Helvetica font
- ⚠️ No background image

---

## 🔧 FEATURES

### ✅ Included:
- Password authentication (JWT)
- PDF generation (ReportLab)
- QR code generation
- Cloudinary upload (optional)
- Email delivery (optional)
- Batch processing
- Car database (10 makes, 30+ models)
- Form validation

### 🎨 PDF Features:
- Custom template background
- Canva Sans fonts
- QR code (top-right)
- Battery status (Excellent/Good/Bad)
- Professional formatting

---

## ⚙️ CONFIGURATION OPTIONS

### Required:
- `ADMIN_PASSWORD_HASH` - Login password hash
- `SECRET_KEY` - JWT secret

### Optional:
- `CLOUDINARY_*` - Cloud storage & QR codes
- `EMAIL_*` - Send certificates via email

### Without Optional Config:
- ✅ App works normally
- ❌ No QR codes
- ❌ No email sending
- ✅ PDFs still generate and download

---

## 🐛 TROUBLESHOOTING

### "Font not found" error:
```bash
# Make sure these files exist:
ls -la canva-sans-*.ttf
```

### "Template not found" error:
```bash
# Make sure this file exists:
ls -la certificate_bg_2.png
```

### "Cloudinary error":
```bash
# Check environment variables
echo $CLOUDINARY_CLOUD_NAME
echo $CLOUDINARY_API_KEY
# If empty, QR codes won't be generated
```

### "Email error":
```bash
# Check email config
echo $EMAIL_SENDER
echo $EMAIL_PASSWORD
# Use Gmail App Password, not regular password
```

---

## 📊 LOGS

```bash
# View Railway logs
railway logs

# Look for:
# ✅ Cloudinary: Configured
# ✅ Email: Configured
# ✅ Authentication: Enabled
```

---

## 🎯 PRODUCTION CHECKLIST

- [ ] Change default password
- [ ] Set secure SECRET_KEY
- [ ] Upload certificate_bg_2.png
- [ ] Upload font files
- [ ] Configure Cloudinary (optional)
- [ ] Configure Email (optional)
- [ ] Test login
- [ ] Test PDF generation
- [ ] Get deployment URL
- [ ] Update frontend API URL

---

Ready to deploy! 🚀
