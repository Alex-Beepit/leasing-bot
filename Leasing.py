import logging
from dataclasses import dataclass
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Το API Token του Bot σου
TELEGRAM_TOKEN = "8902761856:AAEmSuEs96Bxm2XA-H3vBiyrPU0wNqhPB9g"

# Καταστάσεις διαλόγου (States)
PRICE, YEAR, ODOMETER, PLAN, DP, DURATION, START_MONTH, KM, FUEL = range(9)

@dataclass
class LeaseQuote:
    monthly_rate_excl_vat: float
    monthly_rate_incl_vat: float
    upfront_guarantee: float
    upfront_downpayment: float
    upfront_total_payable: float
    buyout_nominal_incl_vat: float
    buyout_final_payable: float

def calculate_leasing(
    car_value: float,
    car_year: int,
    current_odometer: int = 0,
    plan_type: str = "fixed",
    months: int = 36,
    downpayment_pct: float = 0.0,
    annual_km: int = 20000,
    fuel_type: str = "gasoline",
    start_month: int = 1,
    vat_rate: float = 0.24,
    margin_rate: float = 0.10
) -> LeaseQuote:
    current_year = datetime.now().year
    car_age = max(0, current_year - car_year)

    if car_value > 25000:
        margin_rate = 0.11

    # 1. Υποτίμηση
    base_depreciation_rates = {
        "gasoline": 0.11,
        "diesel": 0.10,
        "hybrid": 0.09,
        "electric": 0.13
    }
    
    if car_age > 5 or current_odometer >= 150000:
        annual_dep_rate = 0.145
    else:
        annual_dep_rate = base_depreciation_rates.get(fuel_type.lower(), 0.11)
        if car_age > 2:
            annual_dep_rate *= 0.95

    extra_annual_km = max(0, annual_km - 20000)
    annual_dep_rate += (extra_annual_km / 10000) * 0.015

    # 2. RV (Υπολειμματική Αξία)
    effective_months = 12 if plan_type.lower() == "flex" else months
    years = effective_months / 12.0
    
    residual_value = car_value * ((1.0 - annual_dep_rate) ** years)
    
    if current_odometer >= 100000:
        residual_value *= 0.90
    elif current_odometer >= 80000:
        residual_value *= 0.92
    elif current_odometer >= 50000:
        residual_value *= 0.96

    # 3. Απόσβεση Κεφαλαίου
    downpayment_euro = (downpayment_pct / 100.0) * car_value
    net_financed_amount = max(0.0, car_value - downpayment_euro)
    total_depreciation = max(0.0, net_financed_amount - residual_value)

    # 4. OPEX (Λειτουργικά Έξοδα)
    monthly_opex_base = 145.0 if car_value >= 25000 else 85.0
    if current_odometer >= 90000:
        monthly_opex_base += 35.0
    elif current_odometer >= 60000 or car_age >= 5:
        monthly_opex_base += 15.0
        
    monthly_opex_km = (annual_km / 10000.0) * 10.0 
    monthly_opex = monthly_opex_base + monthly_opex_km
    total_opex = monthly_opex * effective_months

    # 5. Χρηματοδοτικό Κόστος & Flex Premium
    financial_cost = ((net_financed_amount + residual_value) / 2) * margin_rate * years

    flex_premium = 0.0
    if plan_type.lower() == "flex":
        flex_premium = (total_depreciation + total_opex + financial_cost) * 0.05

    # 6. Τελικό Μίσθωμα & High Season
    total_cost_excl_vat = total_depreciation + total_opex + financial_cost + flex_premium
    
    if plan_type.lower() == "flex":
        base_monthly_rate_excl_vat = total_cost_excl_vat / 12.0
        consistency_discount = 0.235
        monthly_rate_excl_vat = base_monthly_rate_excl_vat * (1 - consistency_discount)
        
        # High Season προσαύξηση 25% (Ιούνιος - Σεπτέμβριος)
        if start_month in [6, 7, 8, 9]:
            monthly_rate_excl_vat *= 1.25
    else:
        monthly_rate_excl_vat = total_cost_excl_vat / effective_months
        
    monthly_rate_incl_vat = monthly_rate_excl_vat * (1 + vat_rate)

    # 7. Αρχικά Έξοδα & Εξαγορά
    if plan_type.lower() == "flex":
        upfront_guarantee = 0.0
        buyout_nominal_incl_vat = 0.0
        buyout_final_payable = 0.0
    else:
        upfront_guarantee = monthly_rate_incl_vat * 2
        buyout_nominal_incl_vat = residual_value * (1 + vat_rate)
        discounted_buyout = buyout_nominal_incl_vat * (1 - 0.12)
        guarantee_and_bonus = upfront_guarantee * 2
        buyout_final_payable = max(0.0, discounted_buyout - guarantee_and_bonus)

    upfront_total_payable = monthly_rate_incl_vat + upfront_guarantee + downpayment_euro

    return LeaseQuote(
        monthly_rate_excl_vat=round(monthly_rate_excl_vat, 2),
        monthly_rate_incl_vat=round(monthly_rate_incl_vat, 2),
        upfront_guarantee=round(upfront_guarantee, 2),
        upfront_downpayment=round(downpayment_euro, 2),
        upfront_total_payable=round(upfront_total_payable, 2),
        buyout_nominal_incl_vat=round(buyout_nominal_incl_vat, 2),
        buyout_final_payable=round(buyout_final_payable, 2)
    )

# --- TELEGRAM BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚗 **Υπολογιστής Leasing**\n\n"
        "1️⃣ Στείλε την **τρέχουσα αξία του αυτοκινήτου (€)** (π.χ. 18480):",
        parse_mode="Markdown"
    )
    return PRICE

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['price'] = float(update.message.text.replace('€', '').replace('.', '').replace(',', '.').strip())
        await update.message.reply_text("2️⃣ Δώσε το **έτος κατασκευής / 1ης κυκλοφορίας** (π.χ. 2021):", parse_mode="Markdown")
        return YEAR
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έναν έγκυρο αριθμό για την τιμή (π.χ. 18480):")
        return PRICE

async def get_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['year'] = int(update.message.text.strip())
        await update.message.reply_text("3️⃣ Δώσε τα **τρέχοντα χιλιόμετρα** του αυτοκινήτου (π.χ. 98800):", parse_mode="Markdown")
        return ODOMETER
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε ένα έγκυρο έτος (π.χ. 2021):")
        return YEAR

async def get_odometer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['odometer'] = int(update.message.text.replace('.', '').replace(',', '').strip())
        reply_keyboard = [["Fixed", "Flex"]]
        await update.message.reply_text(
            "4️⃣ Επίλεξε **πρόγραμμα μίσθωσης**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return PLAN
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρα χιλιόμετρα (π.χ. 98800):")
        return ODOMETER

async def get_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = update.message.text.strip().lower()
    context.user_data['plan'] = plan
    
    if plan == "fixed":
        reply_keyboard = [["0%", "10%", "20%"]]
        await update.message.reply_text(
            "5️⃣ Επίλεξε **ποσοστό προκαταβολής**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return DP
    else:
        context.user_data['downpayment_pct'] = 0.0
        context.user_data['months'] = 12
        reply_keyboard = [
            ["1 (Ιαν)", "2 (Φεβ)", "3 (Μαρ)", "4 (Απρ)"],
            ["5 (Μαι)", "6 (Ιουν)", "7 (Ιουλ)", "8 (Αυγ)"],
            ["9 (Σεπ)", "10 (Οκτ)", "11 (Νοε)", "12 (Δεκ)"]
        ]
        await update.message.reply_text(
            "5️⃣ Επίλεξε **μήνα έναρξης** του Flex:",
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
        "6️⃣ Επίλεξε **διάρκεια μίσθωσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return DURATION

async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    months = int(text.split()[0])
    context.user_data['months'] = months
    context.user_data['start_month'] = 1
    
    reply_keyboard = [["20000 χλμ", "30000 χλμ", "40000 χλμ"]]
    await update.message.reply_text(
        "7️⃣ Επίλεξε **ετήσια χιλιόμετρα χρήσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KM

async def get_start_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    month_num = int(text.split()[0])
    context.user_data['start_month'] = month_num
    
    reply_keyboard = [["1000 χλμ/μήνα", "2000 χλμ/μήνα", "3000 χλμ/μήνα"]]
    await update.message.reply_text(
        "6️⃣ Επίλεξε **μηνιαία χιλιόμετρα χρήσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KM

async def get_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split()[0]
    km_val = int(text)
    
    if context.user_data['plan'] == 'flex':
        context.user_data['annual_km'] = km_val * 12
    else:
        context.user_data['annual_km'] = km_val
        
    reply_keyboard = [["Βενζίνη", "Πετρέλαιο"], ["Υβριδικό", "Ηλεκτρικό"]]
    await update.message.reply_text(
        "8️⃣ Επίλεξε **τύπο καυσίμου**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return FUEL

async def get_fuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fuel_choice = update.message.text.strip()
    fuel_map = {
        "Βενζίνη": "gasoline",
        "Πετρέλαιο": "diesel",
        "Υβριδικό": "hybrid",
        "Ηλεκτρικό": "electric"
    }
    fuel = fuel_map.get(fuel_choice, "gasoline")
    
    data = context.user_data
    quote = calculate_leasing(
        car_value=data['price'],
        car_year=data['year'],
        current_odometer=data['odometer'],
        plan_type=data['plan'],
        months=data.get('months', 36),
        downpayment_pct=data.get('downpayment_pct', 0.0),
        annual_km=data['annual_km'],
        fuel_type=fuel,
        start_month=data.get('start_month', 1)
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
        "📋 **ΑΠΟΤΕΛΕΣΜΑΤΑ LEASING**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{dp_line}"
        f"💶 **Μηνιαίο Μίσθωμα (με ΦΠΑ 24%):** `{quote.monthly_rate_incl_vat:,.2f} €`\n"
        f"• Μηνιαίο Μίσθωμα (χωρίς ΦΠΑ): {quote.monthly_rate_excl_vat:,.2f} €\n"
        f"• Εγγύηση: {quote.upfront_guarantee:,.2f} €\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 **ΣΥΝΟΛΙΚΟ ΑΡΧΙΚΟ ΠΟΣΟ ΠΛΗΡΩΜΗΣ:** `{quote.upfront_total_payable:,.2f} €`\n"
        f"{buyout_section}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔄 Για νέο υπολογισμό πάτησε /start"
    )

    await update.message.reply_text(result, reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ο υπολογισμός ακυρώθηκε. Πάτησε /start για επανεκκίνηση.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
            YEAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_year)],
            ODOMETER: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_odometer)],
            PLAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_plan)],
            DP: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dp)],
            DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_duration)],
            START_MONTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_start_month)],
            KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_km)],
            FUEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fuel)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)
    print("🚀 Το Telegram Bot είναι ONLINE! Μπορείς να του στείλεις /start στο Telegram.")
    app.run_polling()
