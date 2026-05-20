from flask import Flask, render_template, request, redirect, url_for, Response
from datetime import datetime, date, timedelta
from io import StringIO
import csv
import os
import math
import unicodedata

app = Flask(__name__)

PRODUCTS_FILE = "data/products.csv"
STOCKS_FILE = "data/stocks.csv"
SALES_FILE = "data/sales.csv"
SALES_IMPORT_FILE = "data/sales_import.txt"

PRODUCT_FIELDS = ["id", "name", "gender", "color", "size"]
STOCK_FIELDS = ["product_id", "name", "color", "size", "stock", "updated_at"]
SALE_FIELDS = ["id", "date", "transaction_id", "product_id", "quantity", "source", "imported_at"]

# Réglages du moteur de prévision
REPORT_START_DATE = "2026-05-18"
DELIVERY_DAYS = 14
ORDER_PERIOD_DAYS = 14
RECENT_SALES_DAYS = 21
SECURITY_MARGIN = 0.10
MIN_LAST_YEAR_SALES = 3


# -------------------------
# Lecture et écriture CSV
# -------------------------

def read_csv_file(file_path):
    rows = []

    if not os.path.exists(file_path):
        return rows

    with open(file_path, newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        for row in reader:
            rows.append(row)

    return rows


def write_csv_file(file_path, fieldnames, rows):
    with open(file_path, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            clean_row = {}

            for field in fieldnames:
                clean_row[field] = row.get(field, "")

            writer.writerow(clean_row)


def read_products():
    return read_csv_file(PRODUCTS_FILE)


def save_products(products):
    write_csv_file(PRODUCTS_FILE, PRODUCT_FIELDS, products)


def read_stocks():
    return read_csv_file(STOCKS_FILE)


def save_stocks(stocks):
    # À chaque import Shopify, l'ancien fichier de stock est remplacé
    write_csv_file(STOCKS_FILE, STOCK_FIELDS, stocks)


def read_sales():
    return read_csv_file(SALES_FILE)


def save_sales(sales):
    write_csv_file(SALES_FILE, SALE_FIELDS, sales)


# -------------------------
# Fonctions utilitaires
# -------------------------

def clean_text(text):
    if not text:
        return ""

    text = str(text).strip().lower()

    # Les exports n'ont pas toujours exactement le même format de texte
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")

    text = text.replace("–", "-")
    text = text.replace("—", "-")
    text = text.replace("+", "")
    text = text.replace("'", "")
    text = " ".join(text.split())

    return text


def get_column_value(row, column_name):
    searched_column = clean_text(column_name)

    for key, value in row.items():
        if clean_text(key) == searched_column:
            return str(value).strip()

    return ""


def get_number(value):
    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def format_export_date(raw_date):
    raw_date = str(raw_date).strip()

    # GA4 exporte généralement les dates sous la forme 20260519
    if len(raw_date) == 8 and raw_date.isdigit():
        return raw_date[0:4] + "-" + raw_date[4:6] + "-" + raw_date[6:8]

    return raw_date


def parse_date(date_text):
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def build_product_map(products):
    product_map = {}

    for product in products:
        product_map[product["id"]] = product

    return product_map


def group_products(products):
    grouped_products = {}

    products = sorted(products, key=lambda product: product["name"].lower())

    # Regroupement utilisé pour afficher : produit > couleur > taille
    for product in products:
        name = product["name"]
        color = product["color"] or "Sans couleur"

        if name not in grouped_products:
            grouped_products[name] = {}

        if color not in grouped_products[name]:
            grouped_products[name][color] = []

        grouped_products[name][color].append(product)

    for name in grouped_products:
        for color in grouped_products[name]:
            grouped_products[name][color] = sorted(
                grouped_products[name][color],
                key=lambda product: product["size"].lower()
            )

    return grouped_products


def get_size_order(size):
    size = clean_text(size)

    size_order = {
        "xxs": 1,
        "xs": 2,
        "s": 3,
        "m": 4,
        "l": 5,
        "xl": 6,
        "xxl": 7,
        "2xl": 7,
        "3xl": 8,
        "4xl": 9,
        "taille unique": 10,
        "unique": 10
    }

    return size_order.get(size, 99)


# -------------------------
# Dates des derniers imports
# -------------------------

def get_last_stock_update():
    stocks = read_stocks()
    dates = []

    for stock in stocks:
        updated_at = stock.get("updated_at", "").strip()

        if updated_at:
            dates.append(updated_at)

    if not dates:
        return None

    return max(dates)


def save_last_sales_import_date():
    with open(SALES_IMPORT_FILE, mode="w", encoding="utf-8") as file:
        file.write(datetime.now().strftime("%Y-%m-%d"))


def get_last_sales_import_date():
    if not os.path.exists(SALES_IMPORT_FILE):
        return None

    with open(SALES_IMPORT_FILE, encoding="utf-8") as file:
        import_date = file.read().strip()

    if not import_date:
        return None

    return import_date


# -------------------------
# Import des produits Shopify
# -------------------------

def get_variant_color_size(row):
    color = ""
    size = ""

    for index in ["1", "2", "3"]:
        option_name = get_column_value(row, "Option" + index + " Name").lower()
        option_value = get_column_value(row, "Option" + index + " Value")

        if "couleur" in option_name or "color" in option_name:
            color = option_value

        if "taille" in option_name or "size" in option_name or "pointure" in option_name:
            size = option_value

    return color, size


def make_variant_id(handle, color, size):
    return clean_text(handle) + "-" + clean_text(color) + "-" + clean_text(size)


def read_product_export(file):
    imported_products = []
    seen = set()

    current_title = ""
    current_handle = ""
    current_status = ""
    current_gender = ""

    content = file.stream.read().decode("utf-8-sig").splitlines()
    reader = csv.DictReader(content)

    for row in reader:
        title = get_column_value(row, "Title")
        handle = get_column_value(row, "Handle")
        status = get_column_value(row, "Status")

        # Dans un export Shopify, les infos produit ne sont pas répétées à chaque variante
        if title:
            current_title = title
            current_handle = handle
            current_status = status.lower()
            current_gender = get_column_value(row, "Sexe cible (product.metafields.shopify.target-gender)")

        if current_status != "active":
            continue

        if not current_title or not current_handle:
            continue

        color, size = get_variant_color_size(row)

        if not color and not size:
            continue

        product_id = make_variant_id(current_handle, color, size)

        if product_id in seen:
            continue

        seen.add(product_id)

        imported_products.append({
            "id": product_id,
            "name": current_title,
            "gender": current_gender,
            "color": color,
            "size": size
        })

    return imported_products


# -------------------------
# Import des stocks Shopify
# -------------------------

def read_stock_export(file):
    content = file.stream.read().decode("utf-8-sig").splitlines()

    if not content:
        return {
            "by_variant": {},
            "by_title": {}
        }

    reader = csv.DictReader(content)

    stocks_by_variant = {}
    stocks_by_title = {}

    for row in reader:
        handle = get_column_value(row, "Handle")
        title = get_column_value(row, "Title")
        available = get_column_value(row, "Available (not editable)")
        stock_value = get_number(available)

        if stock_value is None:
            continue

        color, size = get_variant_color_size(row)

        # Correspondance la plus fiable : handle + couleur + taille
        if handle and color and size:
            variant_id = make_variant_id(handle, color, size)

            if variant_id not in stocks_by_variant or stock_value > stocks_by_variant[variant_id]:
                stocks_by_variant[variant_id] = stock_value

        # Sécurité si une variante ne correspond pas exactement
        if title:
            title_key = clean_text(title)

            if title_key not in stocks_by_title or stock_value > stocks_by_title[title_key]:
                stocks_by_title[title_key] = stock_value

    return {
        "by_variant": stocks_by_variant,
        "by_title": stocks_by_title
    }


def find_stock_for_product(product, stock_export):
    product_id = clean_text(product.get("id", ""))
    product_title = clean_text(product.get("name", ""))

    stocks_by_variant = stock_export.get("by_variant", {})
    stocks_by_title = stock_export.get("by_title", {})

    if product_id in stocks_by_variant:
        return stocks_by_variant[product_id]

    if product_title in stocks_by_title:
        return stocks_by_title[product_title]

    return None


def get_stock_for_product(product_id, stocks):
    for stock in stocks:
        if stock.get("product_id") == product_id:
            stock_value = stock.get("stock", "").strip()

            if not stock_value:
                return None

            return get_number(stock_value)

    return None


# -------------------------
# Import des ventes GA4
# -------------------------

def split_variant(variant):
    parts = variant.split("/")

    color = ""
    size = ""

    if len(parts) >= 1:
        color = parts[0].strip()

    if len(parts) >= 2:
        size = parts[1].strip()

    return color, size


def is_free_source(source):
    source = clean_text(source)

    paid_words = ["paid", "cpc", "ppc", "ads", "shopping"]

    for word in paid_words:
        if word in source:
            return False

    return True


def find_product_from_sale(product_name, variant, products):
    sale_name = clean_text(product_name)
    sale_color, sale_size = split_variant(variant)

    sale_color = clean_text(sale_color)
    sale_size = clean_text(sale_size)

    for product in products:
        product_name = clean_text(product["name"])
        product_color = clean_text(product["color"])
        product_size = clean_text(product["size"])

        # Correspondance stricte pour ne pas rattacher une vente au mauvais produit
        if product_name == sale_name and product_color == sale_color and product_size == sale_size:
            return product

    return None


def build_sale_key(transaction_id, sale_date, product_id):
    return transaction_id + "|" + sale_date + "|" + product_id


def read_ga4_export_lines(file):
    content = file.stream.read().decode("utf-8-sig").splitlines()

    csv_lines = []
    header_found = False

    # Les exports GA4 contiennent parfois quelques lignes avant le vrai tableau
    for line in content:
        if line.startswith("Date,"):
            header_found = True

        if header_found:
            csv_lines.append(line)

    return csv_lines


# -------------------------
# Moteur de prévision
# -------------------------

def shift_one_year_back(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def get_sales_between(product_id, sales, start_date, end_date):
    total = 0

    for sale in sales:
        if sale.get("product_id") != product_id:
            continue

        sale_date = parse_date(sale.get("date", ""))

        if not sale_date:
            continue

        if start_date <= sale_date < end_date:
            quantity = get_number(sale.get("quantity", ""))

            if quantity is not None:
                total += quantity

    return total


def get_report_status(today=None):
    if today is None:
        today = date.today()

    start_date = parse_date(REPORT_START_DATE)

    if not start_date:
        return {
            "next_date": today,
            "days_left": 0
        }

    if today < start_date:
        days_left = (start_date - today).days

        return {
            "next_date": start_date,
            "days_left": days_left
        }

    days_since_start = (today - start_date).days
    days_left = (14 - (days_since_start % 14)) % 14
    next_date = today + timedelta(days=days_left)

    return {
        "next_date": next_date,
        "days_left": days_left
    }


def get_order_period(order_date):
    period_start = order_date + timedelta(days=DELIVERY_DAYS)
    period_end = period_start + timedelta(days=ORDER_PERIOD_DAYS)

    return period_start, period_end


def get_recent_daily_sales(product_id, sales, today):
    start_date = today - timedelta(days=RECENT_SALES_DAYS)
    end_date = today
    total_sales = get_sales_between(product_id, sales, start_date, end_date)

    return total_sales / RECENT_SALES_DAYS


def get_season_coefficient(product_id, sales, today):
    recent_start = today - timedelta(days=RECENT_SALES_DAYS)
    recent_end = today
    future_start, future_end = get_order_period(today)

    last_year_recent_start = shift_one_year_back(recent_start)
    last_year_recent_end = shift_one_year_back(recent_end)
    last_year_future_start = shift_one_year_back(future_start)
    last_year_future_end = shift_one_year_back(future_end)

    recent_last_year_sales = get_sales_between(
        product_id,
        sales,
        last_year_recent_start,
        last_year_recent_end
    )

    future_last_year_sales = get_sales_between(
        product_id,
        sales,
        last_year_future_start,
        last_year_future_end
    )

    # Pas assez d'historique : on ne corrige pas la tendance
    if recent_last_year_sales < MIN_LAST_YEAR_SALES:
        return 1

    recent_daily_last_year = recent_last_year_sales / RECENT_SALES_DAYS
    future_daily_last_year = future_last_year_sales / ORDER_PERIOD_DAYS

    if recent_daily_last_year == 0:
        return 1

    coefficient = future_daily_last_year / recent_daily_last_year

    if coefficient < 0.6:
        return 0.6

    if coefficient > 1.8:
        return 1.8

    return coefficient


def calculate_product_order(product, stocks, sales, order_date):
    product_id = product["id"]
    current_stock = get_stock_for_product(product_id, stocks)

    if current_stock is None:
        return {
            "name": product["name"],
            "color": product["color"],
            "size": product["size"],
            "stock": "Inconnu",
            "days_before_empty": None,
            "stock_warning": "🔴",
            "quantity_to_order": 0,
            "estimated_daily_sales": 0,
            "season_coefficient": 1,
            "stock_at_delivery": None,
            "priority_score": 9999
        }

    recent_daily_sales = get_recent_daily_sales(product_id, sales, order_date)
    season_coefficient = get_season_coefficient(product_id, sales, order_date)

    # Même estimation pour les jours avant rupture et le rapport de commande
    estimated_daily_sales = recent_daily_sales * season_coefficient

    if estimated_daily_sales > 0:
        days_before_empty = math.floor(current_stock / estimated_daily_sales)
    else:
        days_before_empty = None

    sales_before_delivery = estimated_daily_sales * DELIVERY_DAYS
    stock_at_delivery = current_stock - sales_before_delivery

    if stock_at_delivery < 0:
        stock_at_delivery = 0

    sales_to_cover = estimated_daily_sales * ORDER_PERIOD_DAYS
    sales_to_cover_with_margin = sales_to_cover * (1 + SECURITY_MARGIN)

    quantity_to_order = math.ceil(sales_to_cover_with_margin - stock_at_delivery)

    if quantity_to_order < 0:
        quantity_to_order = 0

    if estimated_daily_sales == 0:
        stock_warning = "🟢"
    elif current_stock < sales_before_delivery:
        stock_warning = "🔴"
    elif current_stock < sales_before_delivery * (1 + SECURITY_MARGIN):
        stock_warning = "🟡"
    else:
        stock_warning = "🟢"

    if days_before_empty is None:
        priority_score = 9998
    else:
        priority_score = days_before_empty

    return {
        "name": product["name"],
        "color": product["color"],
        "size": product["size"],
        "stock": current_stock,
        "days_before_empty": days_before_empty,
        "stock_warning": stock_warning,
        "quantity_to_order": quantity_to_order,
        "estimated_daily_sales": round(estimated_daily_sales, 2),
        "season_coefficient": round(season_coefficient, 2),
        "stock_at_delivery": math.floor(stock_at_delivery),
        "priority_score": priority_score
    }


def calculate_forecast(order_date=None):
    if order_date is None:
        order_date = date.today()

    products = read_products()
    stocks = read_stocks()
    sales = read_sales()

    forecast_rows = []

    for product in products:
        forecast_rows.append(
            calculate_product_order(product, stocks, sales, order_date)
        )

    forecast_rows = sorted(
        forecast_rows,
        key=lambda row: (
            row["priority_score"],
            -row["quantity_to_order"],
            row["name"].lower()
        )
    )

    return forecast_rows


def build_order_report_rows(order_date=None):
    if order_date is None:
        order_date = date.today()

    forecast_rows = calculate_forecast(order_date)

    return sorted(
        forecast_rows,
        key=lambda row: (
            row["name"].lower(),
            row["color"].lower(),
            get_size_order(row["size"])
        )
    )


# -------------------------
# Routes principales
# -------------------------

@app.route("/")
def home():
    return redirect(url_for("forecast"))


@app.route("/products")
def products():
    products = read_products()

    return render_template(
        "products.html",
        grouped_products=group_products(products)
    )


@app.route("/stocks")
def stocks():
    products = read_products()
    stocks_data = read_stocks()

    last_stock_update = get_last_stock_update()
    today = datetime.now().strftime("%Y-%m-%d")
    stock_update_is_today = last_stock_update == today

    stock_rows = []

    for product in products:
        stock_quantity = get_stock_for_product(product["id"], stocks_data)

        stock_rows.append({
            "product_id": product["id"],
            "name": product["name"],
            "gender": product["gender"],
            "color": product["color"],
            "size": product["size"],
            "stock": stock_quantity if stock_quantity is not None else "Inconnu"
        })

    return render_template(
        "stocks.html",
        grouped_stock_rows=group_products(stock_rows),
        last_stock_update=last_stock_update,
        stock_update_is_today=stock_update_is_today
    )


@app.route("/sales")
def sales():
    products = read_products()
    product_map = build_product_map(products)
    sales_data = read_sales()

    last_sales_import = get_last_sales_import_date()
    today = datetime.now().strftime("%Y-%m-%d")
    sales_import_is_today = last_sales_import == today

    display_sales = sorted(
        sales_data,
        key=lambda sale: sale.get("date", ""),
        reverse=True
    )

    for sale in display_sales:
        product = product_map.get(sale.get("product_id", ""))

        if product:
            sale["product_name"] = product["name"]
            sale["product_color"] = product["color"]
            sale["product_size"] = product["size"]
        else:
            sale["product_name"] = "Produit inconnu"
            sale["product_color"] = "-"
            sale["product_size"] = "-"

    return render_template(
        "sales.html",
        sales=display_sales,
        last_sales_import=last_sales_import,
        sales_import_is_today=sales_import_is_today
    )


@app.route("/forecast")
def forecast():
    report_status = get_report_status()
    forecast_rows = calculate_forecast()

    return render_template(
        "forecast.html",
        forecast_rows=forecast_rows,
        report_status=report_status,
        next_order_date=report_status.get("next_date")
    )


@app.route("/report")
def report():
    order_date = date.today()
    report_rows = build_order_report_rows(order_date)

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(["Produit", "Couleur", "Taille", "À commander"])

    for row in report_rows:
        writer.writerow([
            row["name"],
            row["color"],
            row["size"],
            row["quantity_to_order"]
        ])

    filename = "rapport_commande_" + order_date.strftime("%Y-%m-%d") + ".csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=" + filename
        }
    )


# -------------------------
# Routes d'import
# -------------------------

@app.route("/import-products", methods=["POST"])
def import_products():
    file = request.files.get("file")

    if not file:
        return redirect(url_for("products"))

    imported_products = read_product_export(file)
    save_products(imported_products)

    return redirect(url_for("products"))


@app.route("/import-stocks", methods=["POST"])
def import_stocks():
    file = request.files.get("file")

    if not file:
        return redirect(url_for("stocks"))

    products = read_products()
    stock_export = read_stock_export(file)
    today = datetime.now().strftime("%Y-%m-%d")

    new_stocks = []

    for product in products:
        stock_value = find_stock_for_product(product, stock_export)

        new_stocks.append({
            "product_id": product["id"],
            "name": product["name"],
            "color": product["color"],
            "size": product["size"],
            "stock": str(stock_value) if stock_value is not None else "",
            "updated_at": today
        })

    save_stocks(new_stocks)

    return redirect(url_for("stocks"))


@app.route("/import-sales", methods=["POST"])
def import_sales():
    file = request.files.get("file")

    if not file:
        return redirect(url_for("sales"))

    products = read_products()
    sales_data = read_sales()
    existing_sales = set()

    for sale in sales_data:
        transaction_id = sale.get("transaction_id", "")
        sale_date = sale.get("date", "")
        product_id = sale.get("product_id", "")

        if transaction_id and sale_date and product_id:
            existing_sales.add(build_sale_key(transaction_id, sale_date, product_id))

    csv_lines = read_ga4_export_lines(file)

    if not csv_lines:
        save_last_sales_import_date()
        return redirect(url_for("sales"))

    reader = csv.DictReader(csv_lines)
    imported_count = 0
    today = datetime.now().strftime("%Y-%m-%d")

    for row in reader:
        raw_date = get_column_value(row, "Date")
        transaction_id = get_column_value(row, "ID de la transaction")
        product_name = get_column_value(row, "Nom de l'article")
        variant = get_column_value(row, "Variante de l'article")
        source = get_column_value(row, "Source/Support de la session")
        quantity = get_column_value(row, "Articles achetés")

        if not raw_date or not product_name or not quantity:
            continue

        if not is_free_source(source):
            continue

        product = find_product_from_sale(product_name, variant, products)

        if not product:
            continue

        sale_date = format_export_date(raw_date)
        product_id = product["id"]

        if not transaction_id:
            transaction_id = sale_date + "-" + product_id + "-" + quantity

        sale_key = build_sale_key(transaction_id, sale_date, product_id)

        if sale_key in existing_sales:
            continue

        sales_data.append({
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f") + str(imported_count),
            "date": sale_date,
            "transaction_id": transaction_id,
            "product_id": product_id,
            "quantity": quantity,
            "source": source,
            "imported_at": today
        })

        existing_sales.add(sale_key)
        imported_count += 1

    save_sales(sales_data)
    save_last_sales_import_date()

    return redirect(url_for("sales"))


# -------------------------
# Routes de suppression
# -------------------------

@app.route("/delete-product/<product_id>")
def delete_product(product_id):
    products = read_products()

    products = [
        product for product in products
        if product["id"] != product_id
    ]

    save_products(products)

    return redirect(url_for("products"))


@app.route("/delete-sale/<sale_id>")
def delete_sale(sale_id):
    sales_data = read_sales()

    sales_data = [
        sale for sale in sales_data
        if sale.get("id") != sale_id
    ]

    save_sales(sales_data)

    return redirect(url_for("sales"))


if __name__ == "__main__":
    app.run(debug=True, port=5050)
