import os
import io
import datetime
from dataclasses import dataclass
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

TELEGRAM_TOKEN = "8902761856:AAEmSuEs96Bxm2XA-H3vBiyrPU0wNqhPB9g"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Καταστάσεις διαλόγου
PRESET_OR_CUSTOM, CUSTOM_PRICE, CUSTOM_YEAR, CUSTOM_ODOMETER, CUSTOM_FUEL, PLAN, DP, DURATION, START_MONTH, KM, ADDONS = range(11)

FLEET_PRESETS = {
    "🚗 Fiat 500 Hybrid (2022)": {"name": "Fiat 500 Hybrid", "price": 14500.0, "year": 2022, "odometer": 35000, "fuel": "hybrid"},
    "🚙 Peugeot 208 Diesel (2021)": {"name": "Peugeot 208 Diesel", "price": 16800.0, "year": 2021, "odometer": 52000, "fuel": "diesel"},
    "⚡ Peugeot e-2008 EV (2022)": {"name": "Peugeot e-2008 Electric", "price": 24500.0, "year": 2022, "odometer": 28000, "fuel": "electric"},
    "🚘 Nissan Qashqai (2021)": {"name": "Nissan Qashqai 1.3T", "price": 21500.0, "year": 2021, "odometer": 45000, "fuel": "gasoline"},
}

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
    if "Μηδενική Απαλλαγή (+25€)" in selected_addons or "Zero Deductible (+25 EUR)" in selected_addons:
        addons_monthly_total += 25.0
    if "2ος Οδηγός & Αλλαγή Ελαστικών (+15€)" in selected_addons or "2nd Driver & Tire Replacement (+15 EUR)" in selected_addons:
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
        selected_addons=selected_addons
    )

def generate_pdf_quote(quote: LeaseQuote, plan_type: str, months: int) -> io.BytesIO:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('TitleStyle', parent=styles['Heading1'], fontName="Helvetica-Bold", fontSize=16, textColor=colors.HexColor("#0D233A"), spaceAfter=5)
    normal_style = ParagraphStyle('NormalStyle', parent=styles['Normal'], fontName="Helvetica", fontSize=10, textColor=colors.HexColor("#2C3E50"))
    bold_style = ParagraphStyle('BoldStyle', parent=styles['Normal'], fontName="Helvetica-Bold", fontSize=10, textColor=colors.HexColor("#0D233A"))

    logo_files = ["Flex-LeaseB.png", "logo.png", os.path.join(BASE_DIR, "Flex-LeaseB.png"), os.path.join(BASE_DIR, "logo.png")]
    for lf in logo_files:
        if os.path.exists(lf):
            try:
                logo_img = Image(lf, width=150, height=42)
                logo_img.hAlign = 'LEFT'
                story.append(logo_img)
                story.append(Spacer(1, 10))
                break
            except Exception:
                pass

    story.append(Paragraph("BEEPIT LEASING - OFFICIAL QUOTE", title_style))
    story.append(Paragraph(f"Date: {datetime.datetime.now().strftime('%d/%m/%Y')}", normal_style))
    story.append(Spacer(1, 15))

    data_summary = [
        [Paragraph("Vehicle", bold_style), Paragraph(str(quote.car_name), normal_style)],
        [Paragraph("Plan Type", bold_style), Paragraph(f"{plan_type.upper()} ({months} Months)" if plan_type == 'fixed' else "FLEX (Month-to-Month)", normal_style)],
        [Paragraph("Downpayment", bold_style), Paragraph(f"{quote.upfront_downpayment:,.2f} EUR", normal_style)],
        [Paragraph("Monthly Rate (incl. 24% VAT)", bold_style), Paragraph(f"<b>{quote.monthly_rate_incl_vat:,.2f} EUR</b>", bold_style)],
        [Paragraph("Monthly Rate (excl. VAT)", bold_style), Paragraph(f"{quote.monthly_rate_excl_vat:,.2f} EUR", normal_style)],
        [Paragraph("Security Deposit", bold_style), Paragraph(f"{quote.upfront_guarantee:,.2f} EUR", normal_style)],
        [Paragraph("TOTAL UPFRONT PAYMENT", bold_style), Paragraph(f"<b>{quote.upfront_total_payable:,.2f} EUR</b>", bold_style)],
    ]

    if plan_type == 'fixed':
        data_summary.append([Paragraph("Buyout Option at End", bold_style), Paragraph(f"<b>{quote.buyout_final_payable:,.2f} EUR</b> (-12% Discount & Bonus)", normal_style)])

    table = Table(data_summary, colWidths=[200, 300])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F8F9F9")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#BDC3C7")),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    story.append(Spacer(1, 15))

    if quote.selected_addons:
        story.append(Paragraph("Selected Add-ons:", bold_style))
        for addon in quote.selected_addons:
            story.append(Paragraph(f"• {addon}", normal_style))
        story.append(Spacer(1, 10))

    story.append(Paragraph("Included: Full Maintenance & Service, Comprehensive Insurance, 24/7 Road Assistance, Vehicle Replacement.", normal_style))
    doc.build(story)
    buffer.seek(0)
    return buffer

# --- TELEGRAM HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    reply_keyboard = [[k] for k in FLEET_PRESETS.keys()] + [["🔧 Χειροκίνητη Εισαγωγή Αυτοκινήτου"]]
    await update.message.reply_text(
        "🚗 **Καλωσήρθατε στο beepit Leasing!**\n\n"
        "Επιλέξτε ένα όχημα από τον στόλο μας ή εισάγετε τα δικά σας στοιχεία:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return PRESET_OR_CUSTOM

async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    if choice in FLEET_PRESETS:
        data = FLEET_PRESETS[choice]
        context.user_data['car_name'] = data['name']
        context.user_data['price'] = data['price']
        context.user_data['year'] = data['year']
        context.user_data['odometer'] = data['odometer']
        context.user_data['fuel'] = data['fuel']
        
        reply_keyboard = [["Fixed", "Flex"]]
        await update.message.reply_text(
            f"✅ Επιλέξατε: **{data['name']}**\n\nΕπιλέξτε **πρόγραμμα μίσθωσης**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return PLAN
    else:
        context.user_data['car_name'] = "Custom Vehicle"
        await update.message.reply_text("1️⃣ Στείλε την **τρέχουσα αξία του αυτοκινήτου (€)**:", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
        return CUSTOM_PRICE

async def get_custom_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['price'] = float(update.message.text.replace('€', '').replace('.', '').replace(',', '.').strip())
        await update.message.reply_text("2️⃣ Δώσε το **έτος κατασκευής / 1ης κυκλοφορίας**:")
        return CUSTOM_YEAR
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρη τιμή:")
        return CUSTOM_PRICE

async def get_custom_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['year'] = int(update.message.text.strip())
        await update.message.reply_text("3️⃣ Δώσε τα **τρέχοντα χιλιόμετρα**:")
        return CUSTOM_ODOMETER
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρο έτος:")
        return CUSTOM_YEAR

async def get_custom_odometer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['odometer'] = int(update.message.text.replace('.', '').replace(',', '').strip())
        reply_keyboard = [["Βενζίνη", "Πετρέλαιο"], ["Υβριδικό", "Ηλεκτρικό"]]
        await update.message.reply_text(
            "4️⃣ Επίλεξε **τύπο καυσίμου**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return CUSTOM_FUEL
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρα χιλιόμετρα:")
        return CUSTOM_ODOMETER

async def get_custom_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fuel_map = {"Βενζίνη": "gasoline", "Πετρέλαιο": "diesel", "Υβριδικό": "hybrid", "Ηλεκτρικό": "electric"}
    context.user_data['fuel'] = fuel_map.get(update.message.text.strip(), "gasoline")
    
    reply_keyboard = [["Fixed", "Flex"]]
    await update.message.reply_text(
        "5️⃣ Επίλεξε **πρόγραμμα μίσθωσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return PLAN

async def get_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = update.message.text.strip().lower()
    context.user_data['plan'] = plan
    
    if "fixed" in plan:
        context.user_data['plan'] = "fixed"
        reply_keyboard = [["0%", "10%", "20%"]]
        await update.message.reply_text(
            "Επίλεξε **ποσοστό προκαταβολής**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return DP
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
        return START_MONTH

async def get_dp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace('%', '')
    try:
        pct = float(text)
        context.user_data['downpayment_pct'] = pct if pct in [0.0, 10.0, 20.0] else 0.0
    except ValueError:
        context.user_data['downpayment_pct'] = 0.0

    reply_keyboard = [["36 μήνες", "48 μήνες", "60 μήνες"]]
    await update.message.reply_text(
        "Επίλεξε **διάρκεια μίσθωσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return DURATION

async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    context.user_data['months'] = int(text.split()[0])
    context.user_data['start_month'] = 1
    
    reply_keyboard = [["20000 χλμ", "30000 χλμ", "40000 χλμ"]]
    await update.message.reply_text(
        "Επίλεξε **ετήσια χιλιόμετρα χρήσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KM

async def get_start_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['start_month'] = int(update.message.text.strip().split()[0])
    reply_keyboard = [["1000 χλμ/μήνα", "2000 χλμ/μήνα", "3000 χλμ/μήνα"]]
    await update.message.reply_text(
        "Επίλεξε **μηνιαία χιλιόμετρα χρήσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KM

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
    return ADDONS

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
    if data['plan'] == 'fixed':
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
        "📄 *Σας αποστέλλεται η επίσημη προσφορά σε PDF...*"
    )

    await update.message.reply_text(result, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")

    pdf_buffer = generate_pdf_quote(quote, data['plan'], data.get('months', 12))
    await update.message.reply_document(
        document=pdf_buffer,
        filename=f"Beepit_Quote_{quote.car_name.replace(' ', '_')}.pdf",
        caption="📄 Beepit Official Leasing Quote"
    )

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ο υπολογισμός ακυρώθηκε. Πάτησε /start για επανεκκίνηση.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PRESET_OR_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_choice)],
            CUSTOM_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_price)],
            CUSTOM_YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_year)],
            CUSTOM_ODOMETER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_odometer)],
            CUSTOM_FUEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_custom_fuel)],
            PLAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan)],
            DP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dp)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration)],
            START_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_start_month)],
            KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_km)],
            ADDONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_addons_and_finish)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)
    print("🚀 Το Telegram Bot είναι ONLINE!")
    app.run_polling()
