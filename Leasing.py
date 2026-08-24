import os
import io
import time
import datetime
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dataclasses import dataclass
from PIL import Image as PILImage

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

TELEGRAM_TOKEN = "8902761856:AAEmSuEs96Bxm2XA-H3vBiyrPU0wNqhPB9g"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- ΕΝΣΩΜΑΤΩΣΗ ΕΛΛΗΝΙΚΗΣ ΓΡΑΜΜΑΤΟΣΕΙΡΑΣ (ΑΠΟ ΤΟ font.ttf ΤΟΥ GITHUB) ---
FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"

custom_font_path = os.path.join(BASE_DIR, "font.ttf")
if os.path.exists(custom_font_path):
    try:
        pdfmetrics.registerFont(TTFont("CustomGreekFont", custom_font_path))
        FONT_REGULAR = "CustomGreekFont"
        FONT_BOLD = "CustomGreekFont"
    except Exception as e:
        print(f"Font loading error: {e}")

# --- DUMMY HTTP SERVER ΓΙΑ RENDER HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Beepit Leasing Bot is Running!")

    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# Καταστάσεις διαλόγου
VEHICLE_NAME, CUSTOM_PRICE, CUSTOM_YEAR, CUSTOM_ODOMETER, CUSTOM_FUEL, PLAN_STEP, DP_STEP, DURATION_STEP, START_MONTH_STEP, KM_STEP, ADDONS_STEP = range(11)

@dataclass
class LeaseQuote:
    car_name: str
    monthly_rate_excl_vat: float
    monthly_rate_incl_vat: float
    upfront_guarantee: float
    upfront_downpayment: float
    upfront_total_payable: float
    buyout_nominal_incl_vat: float
    buyout_final_payable: float
    addons_cost: float
    selected_addons: list
    annual_km: int

def calculate_leasing(
    car_name: str,
    car_value: float,
    car_year: int,
    current_odometer: int,
    plan_type: str,
    months: int,
    downpayment_pct: float,
    annual_km: int,
    fuel_type: str,
    start_month: int,
    selected_addons: list
) -> LeaseQuote:
    vat_rate = 0.24
    margin_rate = 0.11 if car_value > 25000 else 0.10

    current_year = datetime.datetime.now().year
    car_age = max(0, current_year - car_year)

    base_depreciation = {'gasoline': 0.11, 'diesel': 0.10, 'hybrid': 0.09, 'electric': 0.13}
    if car_age > 5 or current_odometer >= 150000:
        annual_dep_rate = 0.145
    else:
        annual_dep_rate = base_depreciation.get(fuel_type.lower(), 0.11)
        if car_age > 2:
            annual_dep_rate *= 0.95

    extra_annual_km = max(0, annual_km - 20000)
    annual_dep_rate += (extra_annual_km / 10000) * 0.015

    effective_months = 12 if plan_type.lower() == 'flex' else months
    years = effective_months / 12.0

    residual_value = car_value * ((1.0 - annual_dep_rate) ** years)
    if current_odometer >= 100000: residual_value *= 0.90
    elif current_odometer >= 80000: residual_value *= 0.92
    elif current_odometer >= 50000: residual_value *= 0.96

    downpayment_euro = (downpayment_pct / 100.0) * car_value
    net_financed_amount = max(0.0, car_value - downpayment_euro)
    total_depreciation = max(0.0, net_financed_amount - residual_value)

    monthly_opex_base = 145.0 if car_value >= 25000 else 85.0
    if current_odometer >= 90000: monthly_opex_base += 35.0
    elif current_odometer >= 60000 or car_age >= 5: monthly_opex_base += 15.0
    
    monthly_opex_km = (annual_km / 10000.0) * 10.0
    monthly_opex = monthly_opex_base + monthly_opex_km
    total_opex = monthly_opex * effective_months

    financial_cost = ((net_financed_amount + residual_value) / 2) * margin_rate * years

    flex_premium = 0.0
    if plan_type.lower() == 'flex':
        flex_premium = (total_depreciation + total_opex + financial_cost) * 0.05

    total_cost_excl_vat = total_depreciation + total_opex + financial_cost + flex_premium

    if plan_type.lower() == 'flex':
        base_monthly_rate_excl_vat = total_cost_excl_vat / 12.0
        monthly_rate_excl_vat = base_monthly_rate_excl_vat * (1 - 0.235)
        if start_month in [6, 7, 8, 9]:
            monthly_rate_excl_vat *= 1.25
    else:
        monthly_rate_excl_vat = total_cost_excl_vat / effective_months

    addons_monthly_total = 0.0
    if any("Απαλλαγή" in a for a in selected_addons):
        addons_monthly_total += 25.0
    if any("Οδηγός" in a for a in selected_addons):
        addons_monthly_total += 15.0

    addons_excl_vat = addons_monthly_total / (1 + vat_rate)
    monthly_rate_excl_vat += addons_excl_vat
    monthly_rate_incl_vat = monthly_rate_excl_vat * (1 + vat_rate)

    if plan_type.lower() == 'flex':
        upfront_guarantee = 0.0
        buyout_nominal_incl_vat = 0.0
        buyout_final_payable = 0.0
    else:
        upfront_guarantee = monthly_rate_incl_vat * 2
        buyout_nominal_incl_vat = residual_value * (1 + vat_rate)
        discounted_buyout = buyout_nominal_incl_vat * (1 - 0.12)
        guarantee_bonus = upfront_guarantee * 2
        buyout_final_payable = max(0.0, discounted_buyout - guarantee_bonus)

    upfront_total_payable = monthly_rate_incl_vat + upfront_guarantee + downpayment_euro

    return LeaseQuote(
        car_name=car_name,
        monthly_rate_excl_vat=round(monthly_rate_excl_vat, 2),
        monthly_rate_incl_vat=round(monthly_rate_incl_vat, 2),
        upfront_guarantee=round(upfront_guarantee, 2),
        upfront_downpayment=round(downpayment_euro, 2),
        upfront_total_payable=round(upfront_total_payable, 2),
        buyout_nominal_incl_vat=round(buyout_nominal_incl_vat, 2),
        buyout_final_payable=round(buyout_final_payable, 2),
        addons_cost=addons_monthly_total,
        selected_addons=selected_addons,
        annual_km=annual_km
    )

def generate_pdf_quote(quote: LeaseQuote, plan_type: str, months: int) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=25,
        bottomMargin=25
    )
    styles = getSampleStyleSheet()
    story = []

    # Τυπογραφικά στυλ
    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName=FONT_BOLD, fontSize=15, textColor=colors.HexColor("#0D233A"), spaceAfter=2)
    meta_style = ParagraphStyle('MetaStyle', parent=styles['Normal'], fontName=FONT_REGULAR, fontSize=8.5, textColor=colors.HexColor("#7F8C8D"), spaceAfter=8)
    normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontName=FONT_REGULAR, fontSize=8.5, textColor=colors.HexColor("#2C3E50"))
    bold_style = ParagraphStyle('BoldStyle', parent=styles['Normal'], fontName=FONT_BOLD, fontSize=8.5, textColor=colors.HexColor("#0D233A"))
    h2_style = ParagraphStyle('H2Style', parent=styles['Heading2'], fontName=FONT_BOLD, fontSize=9.5, textColor=colors.HexColor("#0D233A"), spaceBefore=5, spaceAfter=2)
    
    fine_print_title = ParagraphStyle('FinePrintTitle', parent=styles['Normal'], fontName=FONT_BOLD, fontSize=7, textColor=colors.HexColor("#0D233A"), spaceBefore=4, spaceAfter=2)
    fine_print = ParagraphStyle('FinePrint', parent=styles['Normal'], fontName=FONT_REGULAR, fontSize=5.8, leading=7.2, textColor=colors.HexColor("#5D6D7E"), alignment=4)

    # Λογότυπο
    logo_files = ["Flex-LeaseB.png", "logo.png", os.path.join(BASE_DIR, "Flex-LeaseB.png"), os.path.join(BASE_DIR, "logo.png")]
    for lf in logo_files:
        if os.path.exists(lf):
            try:
                with PILImage.open(lf) as img_temp:
                    orig_w, orig_h = img_temp.size
                target_w = 130.0
                target_h = target_w * (orig_h / orig_w)
                logo_img = Image(lf, width=target_w, height=target_h)
                logo_img.hAlign = 'LEFT'
                story.append(logo_img)
                story.append(Spacer(1, 4))
                break
            except Exception:
                pass

    story.append(Paragraph("BEEPIT LEASING & SUBSCRIPTION SERVICES", title_style))
    story.append(Paragraph(f"Επίσημη Προσφορά Μίσθωσης | Ημερομηνία: {datetime.datetime.now().strftime('%d/%m/%Y')} | Ref: BPT-{int(time.time())%100000}", meta_style))
    story.append(Spacer(1, 4))

    # Πίνακας Οικονομικής Προσφοράς
    data_summary = [
        [Paragraph("Όχημα Προσφοράς", bold_style), Paragraph(str(quote.car_name), normal_style)],
        [Paragraph("Πρόγραμμα Μίσθωσης", bold_style), Paragraph(f"CLASSIC LEASING ({months} Μήνες)" if plan_type == 'classic' else "FLEX LEASING (Μηνιαία Συνδρομή)", normal_style)],
        [Paragraph("Ετήσιο Όριο Χιλιομέτρων", bold_style), Paragraph(f"{quote.annual_km:,} χλμ / έτος", normal_style)],
        [Paragraph("Προκαταβολή", bold_style), Paragraph(f"{quote.upfront_downpayment:,.2f} €", normal_style)],
        [Paragraph("Μηνιαίο Μίσθωμα (με ΦΠΑ 24%)", bold_style), Paragraph(f"{quote.monthly_rate_incl_vat:,.2f} € / μήνα", bold_style)],
        [Paragraph("Μηνιαίο Μίσθωμα (άνευ ΦΠΑ)", bold_style), Paragraph(f"{quote.monthly_rate_excl_vat:,.2f} € / μήνα", normal_style)],
        [Paragraph("Εγγύηση Μισθωμάτων", bold_style), Paragraph(f"{quote.upfront_guarantee:,.2f} € (2 μισθώματα)" if plan_type == 'classic' else "0,00 € (Μηδενική)", normal_style)],
        [Paragraph("ΣΥΝΟΛΙΚΟ ΑΡΧΙΚΟ ΠΟΣΟ ΠΛΗΡΩΜΗΣ", bold_style), Paragraph(f"{quote.upfront_total_payable:,.2f} €", bold_style)],
    ]

    if plan_type == 'classic':
        data_summary.append([
            Paragraph("Δικαίωμα Εξαγοράς (Λήξη)", bold_style),
            Paragraph(f"{quote.buyout_final_payable:,.2f} € (Με -12% έκπτωση & Bonus 2x Εγγύησης)", normal_style)
        ])

    table = Table(data_summary, colWidths=[180, 355])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#EAEDED")),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#F8F9F9")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(table)
    story.append(Spacer(1, 4))

    if quote.selected_addons:
        story.append(Paragraph("Επιλεγμένες Πρόσθετες Καλύψεις (Add-ons):", h2_style))
        for addon in quote.selected_addons:
            story.append(Paragraph(f"• {addon}", normal_style))
        story.append(Spacer(1, 2))

    story.append(Paragraph("Βασικές Παροχές (Συμπεριλαμβάνονται):", h2_style))
    story.append(Paragraph("✔ Πλήρες Service | ✔ Μικτή Ασφάλεια | ✔ Τέλη Κυκλοφορίας | ✔ 24/7 Οδική | ✔ Όχημα Αντικατάστασης.", normal_style))
    story.append(Spacer(1, 5))

    line_table = Table([['']], colWidths=[535])
    line_table.setStyle(TableStyle([('LINEABOVE', (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7"))]))
    story.append(line_table)
    story.append(Spacer(1, 3))

    # --- ΝΟΜΙΚΟΙ ΟΡΟΙ & ΠΟΛΙΤΙΚΗ (FINE PRINT) ---
    story.append(Paragraph("ΓΕΝΙΚΟΙ ΟΡΟΙ, ΠΡΟΫΠΟΘΕΣΕΙΣ ΚΑΙ ΕΜΠΟΡΙΚΗ ΠΟΛΙΤΙΚΗ ΜΙΣΘΩΣΕΩΝ BEEPIT", fine_print_title))
    
    terms = [
        "1. Κυριότητα & Οδηγοί: Το όχημα παραμένει στην αποκλειστική κυριότητα της beepit. Απαγορεύεται αυστηρά η παραχώρηση σε μη εξουσιοδοτημένους οδηγούς. Απαιτείται ηλικία 21 ετών (ή 25 για SUV/Premium).",
        "2. Οικονομικοί Όροι & Καθυστερήσεις: Τα μισθώματα προκαταβάλλονται. Καθυστέρηση άνω των 5 ημερών επιφέρει penalty 15€+ΦΠΑ και δικαίωμα ακινητοποίησης του οχήματος μέσω Τηλεματικής/GPS.",
        "3. Classic Leasing (Δεσμεύσεις & Εξαγορά): Η πρόωρη λύση επιφέρει ποινική ρήτρα 50% των υπολειπόμενων μισθωμάτων και παρακράτηση εγγύησης. Το αποκλειστικό δικαίωμα εξαγοράς στη λήξη υπολογίζεται βάσει RV μείον 12% έκπτωση και μείον το διπλάσιο της εγγύησης.",
        "4. Flex Leasing (Ευελιξία): Ελάχιστη μίσθωση 30 ημέρες, χωρίς προκαταβολή ή εγγύηση (0€ fee). Δικαίωμα διακοπής με ειδοποίηση 5 εργάσιμων ημερών. Για ενάρξεις Ιουνίου-Σεπτεμβρίου ισχύει εποχικότητα +25%.",
        "5. Συντήρηση & Ευθύνες Μισθωτή: Η beepit καλύπτει το προγραμματισμένο service. Ο μισθωτής ελέγχει στάθμη υγρών/λαδιών. Ζημιές κινητήρα από αμέλεια βαρύνουν τον μισθωτή. Φθορά ελαστικών καλύπτεται μόνο μέσω Add-on.",
        "6. Μικτή Ασφάλιση & Εξαιρέσεις: Η μικτή ασφάλεια (CDW/FDW) ΔΕΝ ισχύει σε παραβίαση STOP, φαναριού, μέθη, off-road οδήγηση, ή για ζημιές στο κάτω μέρος (κάρτερ) και στις ζάντες.",
        "7. Περιορισμοί & Κ.Ο.Κ.: Απαγορεύεται η φόρτωση σε πλοίο και η έξοδος στο εξωτερικό χωρίς έγγραφη άδεια. Κλήσεις Κ.Ο.Κ. βαρύνουν τον Μισθωτή με διαχειριστικό κόστος beepit 20€+ΦΠΑ ανά κλήση.",
        "8. Φθορές Επιστροφής (Fair Wear & Tear): Το όχημα ελέγχεται στην επιστροφή. Κάψιμο/σκίσιμο καθισμάτων, βαθιά γδαρσίματα και ελλιπής εξοπλισμός χρεώνονται στον μισθωτή.",
        "9. GDPR & Τηλεματική: Ο Μισθωτής συναινεί στη συλλογή δεδομένων τηλεματικής GPS από την beepit για λόγους ασφαλείας και προστασίας περιουσίας."
    ]

    for term in terms:
        story.append(Paragraph(term, fine_print))
        story.append(Spacer(1, 1))

    doc.build(story)
    buffer.seek(0)
    return buffer

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🚗 **Καλωσήρθατε στο beepit Leasing!**\n\n"
        "1️⃣ Στείλε το **Μοντέλο του Αυτοκινήτου** (π.χ. Peugeot 3008 Allure):",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    return VEHICLE_NAME

async def get_vehicle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['car_name'] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ Στείλε την **τρέχουσα εμπορική αξία (€)** (π.χ. 22500):",
        parse_mode="Markdown"
    )
    return CUSTOM_PRICE

async def get_custom_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['price'] = float(update.message.text.replace('€', '').replace('.', '').replace(',', '.').strip())
        await update.message.reply_text("3️⃣ Δώσε το **έτος κατασκευής / 1ης κυκλοφορίας** (π.χ. 2021):")
        return CUSTOM_YEAR
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρη τιμή (π.χ. 22500):")
        return CUSTOM_PRICE

async def get_custom_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['year'] = int(update.message.text.strip())
        await update.message.reply_text("4️⃣ Δώσε τα **τρέχοντα χιλιόμετρα** (π.χ. 45000):")
        return CUSTOM_ODOMETER
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρο έτος (π.χ. 2021):")
        return CUSTOM_YEAR

async def get_custom_odometer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['odometer'] = int(update.message.text.replace('.', '').replace(',', '').strip())
        reply_keyboard = [["Βενζίνη", "Πετρέλαιο"], ["Υβριδικό", "Ηλεκτρικό"]]
        await update.message.reply_text(
            "5️⃣ Επίλεξε **τύπο καυσίμου**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return CUSTOM_FUEL
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρα χιλιόμετρα (π.χ. 45000):")
        return CUSTOM_ODOMETER

async def get_custom_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fuel_map = {"Βενζίνη": "gasoline", "Πετρέλαιο": "diesel", "Υβριδικό": "hybrid", "Ηλεκτρικό": "electric"}
    context.user_data['fuel'] = fuel_map.get(update.message.text.strip(), "gasoline")
    
    reply_keyboard = [["Classic", "Flex"]]
    await update.message.reply_text(
        "6️⃣ Επίλεξε **πρόγραμμα μίσθωσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return PLAN_STEP

async def get_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan_text = update.message.text.strip().lower()
    
    if "classic" in plan_text:
        context.user_data['plan'] = "classic"
        reply_keyboard = [["36 μήνες", "48 μήνες", "60 μήνες"]]
        await update.message.reply_text(
            "Επίλεξε **διάρκεια μίσθωσης** για το Classic:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return DURATION_STEP
    else:
        context.user_data['plan'] = "flex"
        context.user_data['downpayment_pct'] = 0.0
        context.user_data['months'] = 12
        reply_keyboard = [
            ["1 (Ιαν)", "2 (Φεβ)", "3 (Μαρ)", "4 (Απρ)"],
            ["5 (Μαι)", "6 (Ιουν)", "7 (Ιουλ)", "8 (Αυγ)"],
            ["9 (Σεπ)", "10 (Οκτ)", "11 (Νοε)", "12 (Δεκ)"]
        ]
        await update.message.reply_text(
            "Επίλεξε **μήνα έναρξης** του Flex:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return START_MONTH_STEP

async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data['months'] = int(text.split()[0])
    
    reply_keyboard = [["0%", "10%", "20%"]]
    await update.message.reply_text(
        "Επίλεξε **ποσοστό προκαταβολής**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return DP_STEP

async def get_dp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace('%', '')
    try:
        pct = float(text)
        context.user_data['downpayment_pct'] = pct if pct in [0.0, 10.0, 20.0] else 0.0
    except ValueError:
        context.user_data['downpayment_pct'] = 0.0

    reply_keyboard = [["20000 χλμ", "30000 χλμ", "40000 χλμ"]]
    await update.message.reply_text(
        "Επίλεξε **ετήσια χιλιόμετρα χρήσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KM_STEP

async def get_start_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['start_month'] = int(update.message.text.strip().split()[0])
    reply_keyboard = [["1000 χλμ/μήνα", "2000 χλμ/μήνα", "3000 χλμ/μήνα"]]
    await update.message.reply_text(
        "Επίλεξε **μηνιαία χιλιόμετρα χρήσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KM_STEP

async def get_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    km_val = int(update.message.text.strip().split()[0])
    if context.user_data['plan'] == 'flex':
        context.user_data['annual_km'] = km_val * 12
    else:
        context.user_data['annual_km'] = km_val

    reply_keyboard = [
        ["Χωρίς Add-ons"],
        ["Μηδενική Απαλλαγή (+25€)"],
        ["2ος Οδηγός & Αλλαγή Ελαστικών (+15€)"],
        ["Όλα τα Add-ons (+40€)"]
    ]
    await update.message.reply_text(
        "🛡️ **Επιλέξτε Προαιρετικές Καλύψεις (Add-ons):**",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return ADDONS_STEP

async def get_addons_and_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    selected_addons = []
    if "Μηδενική Απαλλαγή" in choice:
        selected_addons.append("Zero Deductible (+25 EUR)")
    elif "2ος Οδηγός" in choice:
        selected_addons.append("2nd Driver & Tire Replacement (+15 EUR)")
    elif "Όλα" in choice:
        selected_addons.extend(["Zero Deductible (+25 EUR)", "2nd Driver & Tire Replacement (+15 EUR)"])

    data = context.user_data
    quote = calculate_leasing(
        car_name=data.get('car_name', 'Όχημα Leasing'),
        car_value=data['price'],
        car_year=data['year'],
        current_odometer=data['odometer'],
        plan_type=data['plan'],
        months=data.get('months', 36),
        downpayment_pct=data.get('downpayment_pct', 0.0),
        annual_km=data['annual_km'],
        fuel_type=data['fuel'],
        start_month=data.get('start_month', 1),
        selected_addons=selected_addons
    )

    dp_line = f"• Προκαταβολή ({int(data.get('downpayment_pct', 0))}%): *{quote.upfront_downpayment:,.2f} €*\n" if quote.upfront_downpayment > 0 else ""
    buyout_section = ""
    if data['plan'] == 'classic':
        buyout_section = (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "🔑 **ΔΙΚΑΙΩΜΑ ΕΞΑΓΟΡΑΣ (ΣΤΗ ΛΗΞΗ)**\n"
            f"• Αρχική Αξία Εξαγοράς: {quote.buyout_nominal_incl_vat:,.2f} €\n"
            f"• **Τελικό Ποσό Εξαγοράς:** `{quote.buyout_final_payable:,.2f} €`\n"
            "_(Με -12% έκπτωση & διπλασιασμό/συμψηφισμό της εγγύησης)_\n"
        )

    result = (
        f"📋 **ΠΡΟΣΦΟΡΑ: {quote.car_name}**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{dp_line}"
        f"💶 **Μηνιαίο Μίσθωμα (με ΦΠΑ 24%):** `{quote.monthly_rate_incl_vat:,.2f} €`\n"
        f"• Μηνιαίο Μίσθωμα (χωρίς ΦΠΑ): {quote.monthly_rate_excl_vat:,.2f} €\n"
        f"• Εγγύηση: {quote.upfront_guarantee:,.2f} €\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 **ΣΥΝΟΛΙΚΟ ΑΡΧΙΚΟ ΠΟΣΟ ΠΛΗΡΩΜΗΣ:** `{quote.upfront_total_payable:,.2f} €`\n"
        f"{buyout_section}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📄 *Σας αποστέλλεται η επίσημη προσφορά σε PDF με τους πλήρεις όρους & καλύψεις...*"
    )

    await update.message.reply_text(result, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

    pdf_buffer = generate_pdf_quote(quote, data['plan'], data.get('months', 36))
    await update.message.reply_document(
        document=pdf_buffer,
        filename=f"Beepit_Quote_{quote.car_name.replace(' ', '_')}.pdf",
        caption="📄 Beepit Official Leasing Quote & Terms"
    )

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ο υπολογισμός ακυρώθηκε. Πάτησε /start για επανεκκίνηση.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

if __name__ == "__main__":
    t = threading.Thread(target=run_health_server, daemon=True)
    t.start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            VEHICLE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_vehicle_name)],
            CUSTOM_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_price)],
            CUSTOM_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_year)],
            CUSTOM_ODOMETER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_odometer)],
            CUSTOM_FUEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_fuel)],
            PLAN_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan)],
            DP_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dp)],
            DURATION_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration)],
            START_MONTH_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_start_month)],
            KM_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_km)],
            ADDONS_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_addons_and_finish)],
        },
        fallbacks=[CommandHandler('start', start), CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)
    print("🚀 Το Telegram Bot είναι ONLINE με Direct Input & Custom Font PDF!")
    app.run_polling()v
