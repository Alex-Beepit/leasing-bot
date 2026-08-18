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
PRICE, YEAR, ODOMETER, PLAN, DP, DURATION, KM, FUEL = range(8)

@dataclass
class LeaseQuote:
    monthly_rate_excl_vat: float
    monthly_rate_incl_vat: float
    residual_value: float
    total_depreciation: float
    total_opex: float
    upfront_guarantee: float
    upfront_downpayment: float
    upfront_total_payable: float

def calculate_leasing(
    car_value: float,
    car_year: int,
    current_odometer: int = 0,
    plan_type: str = "fixed",
    months: int = 36,
    downpayment: float = 0.0,
    annual_km: int = 20000,
    fuel_type: str = "gasoline",
    vat_rate: float = 0.24,
    margin_rate: float = 0.10
) -> LeaseQuote:
    current_year = datetime.now().year
    car_age = max(0, current_year - car_year)

    if car_value > 25000:
        margin_rate = 0.11

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

    effective_months = 12 if plan_type.lower() == "flex" else months
    years = effective_months / 12.0
    
    residual_value = car_value * ((1.0 - annual_dep_rate) ** years)
    
    if current_odometer >= 100000:
        residual_value *= 0.90
    elif current_odometer >= 80000:
        residual_value *= 0.92
    elif current_odometer >= 50000:
        residual_value *= 0.96

    net_financed_amount = max(0.0, car_value - downpayment)
    total_depreciation = max(0.0, net_financed_amount - residual_value)

    monthly_opex_base = 145.0 if car_value >= 25000 else 85.0

    if current_odometer >= 90000:
        monthly_opex_base += 35.0
    elif current_odometer >= 60000 or car_age >= 5:
        monthly_opex_base += 15.0
        
    monthly_opex_km = (annual_km / 10000.0) * 10.0 
    monthly_opex = monthly_opex_base + monthly_opex_km
    total_opex = monthly_opex * effective_months

    financial_cost = (net_financed_amount + residual_value) / 2 * margin_rate * years

    flex_premium = 0.0
    if plan_type.lower() == "flex":
        flex_premium = (total_depreciation + total_opex + financial_cost) * 0.25

    total_cost_excl_vat = total_depreciation + total_opex + financial_cost + flex_premium
    monthly_rate_excl_vat = total_cost_excl_vat / (12.0 if plan_type.lower() == "flex" else effective_months)
    monthly_rate_incl_vat = monthly_rate_excl_vat * (1 + vat_rate)

    upfront_guarantee = monthly_rate_incl_vat * 2
    upfront_downpayment = downpayment
    upfront_total_payable = monthly_rate_incl_vat + upfront_guarantee + upfront_downpayment

    return LeaseQuote(
        monthly_rate_excl_vat=round(monthly_rate_excl_vat, 2),
        monthly_rate_incl_vat=round(monthly_rate_incl_vat, 2),
        residual_value=round(residual_value, 2),
        total_depreciation=round(total_depreciation, 2),
        total_opex=round(total_opex, 2),
        upfront_guarantee=round(upfront_guarantee, 2),
        upfront_downpayment=round(upfront_downpayment, 2),
        upfront_total_payable=round(upfront_total_payable, 2)
    )

# --- TELEGRAM BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚗 **Υπολογιστής Leasing (Spotawheel Style)**\n\n"
        "1️⃣ Στείλε την **τιμή του αυτοκινήτου (€)** (π.χ. 18500):",
        parse_mode="Markdown"
    )
    return PRICE

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['price'] = float(update.message.text.replace('€', '').replace('.', '').replace(',', '.').strip())
        await update.message.reply_text("2️⃣ Δώσε το **έτος κατασκευής / 1ης κυκλοφορίας** (π.χ. 2021):", parse_mode="Markdown")
        return YEAR
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έναν έγκυρο αριθμό για την τιμή (π.χ. 18500):")
        return PRICE

async def get_year(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['year'] = int(update.message.text.strip())
        await update.message.reply_text("3️⃣ Δώσε τα **τρέχοντα χιλιόμετρα** του αυτοκινήτου (π.χ. 65000):", parse_mode="Markdown")
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
        await update.message.reply_text("⚠️ Παρακαλώ δώσε έγκυρα χιλιόμετρα (π.χ. 65000):")
        return ODOMETER

async def get_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plan = update.message.text.strip().lower()
    context.user_data['plan'] = plan
    
    if plan == "fixed":
        await update.message.reply_text(
            "5️⃣ Δώσε το ποσό **προκαταβολής (€)** (γράψε 0 αν δεν θες προκαταβολή):",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        return DP
    else:
        context.user_data['downpayment'] = 0.0
        context.user_data['months'] = 12
        reply_keyboard = [["20000", "30000", "40000"]]
        await update.message.reply_text(
            "6️⃣ Επίλεξε **ετήσια χιλιόμετρα χρήσης**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return KM

async def get_dp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['downpayment'] = float(update.message.text.replace('€', '').replace('.', '').replace(',', '.').strip())
        reply_keyboard = [["24 μήνες", "36 μήνες", "48 μήνες"]]
        await update.message.reply_text(
            "6️⃣ Επίλεξε **διάρκεια μίσθωσης**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return DURATION
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ δώσε ένα έγκυρο ποσό προκαταβολής (π.χ. 0 ή 3500):")
        return DP

async def get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    months = int(text.split()[0])
    context.user_data['months'] = months
    
    reply_keyboard = [["20000", "30000", "40000"]]
    await update.message.reply_text(
        "7️⃣ Επίλεξε **ετήσια χιλιόμετρα χρήσης**:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="Markdown"
    )
    return KM

async def get_km(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['annual_km'] = int(update.message.text.strip())
        reply_keyboard = [["Βενζίνη", "Πετρέλαιο"], ["Υβριδικό", "Ηλεκτρικό"]]
        await update.message.reply_text(
            "8️⃣ Επίλεξε **τύπο καυσίμου**:",
            reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="Markdown"
        )
        return FUEL
    except ValueError:
        await update.message.reply_text("⚠️ Παρακαλώ επίλεξε χιλιόμετρα από τα διαθέσιμα κουμπιά:")
        return KM

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
        downpayment=data.get('downpayment', 0.0),
        annual_km=data['annual_km'],
        fuel_type=fuel
    )

    dp_line = f"• Προκαταβολή: *{quote.upfront_downpayment:,.2f} €*\n" if quote.upfront_downpayment > 0 else ""

    result = (
        "📋 **ΑΠΟΤΕΛΕΣΜΑΤΑ LEASING**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💶 **Μηνιαίο Μίσθωμα (με ΦΠΑ 24%):** `{quote.monthly_rate_incl_vat:,.2f} €`\n"
        f"• Μηνιαίο Μίσθωμα (χωρίς ΦΠΑ): {quote.monthly_rate_excl_vat:,.2f} €\n"
        f"• Υπολειμματική Αξία (RV): {quote.residual_value:,.2f} €\n"
        f"• Εγγύηση (2 μισθώματα): {quote.upfront_guarantee:,.2f} €\n"
        f"{dp_line}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 **Συνολικό Αρχικό Κόστος:** `{quote.upfront_total_payable:,.2f} €`\n"
        "_(1η Δόση + Εγγύηση + Προκαταβολή)_\n\n"
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
            KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_km)],
            FUEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_fuel)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(conv_handler)
    print("🚀 Το Telegram Bot είναι ONLINE! Μπορείς να του στείλεις /start στο Telegram.")
    app.run_polling()