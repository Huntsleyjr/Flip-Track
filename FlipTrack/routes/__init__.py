import os
import json
import secrets
import shutil
import mimetypes
import requests
import threading
import uuid
import io
import zipfile
import tempfile
import sqlite3
import statistics
from datetime import datetime, date, timedelta
from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    send_from_directory,
    current_app,
    Response,
    send_file,
)
from werkzeug.utils import secure_filename
from PIL import Image as PILImage
from PIL import ImageOps
import pillow_heif
from models import (
    db,
    User,
    Setting,
    Item,
    Image,
    Repair,
    OtherCost,
    Asset,
    Catalog,
    Lot,
    Supply,
    SupplyUsage,
    PasswordResetToken,
    EmailChangeToken,
)
from utils import allowed_file, save_uploaded_image, cents_to_dollars, dollars_to_cents, send_email
from urllib.parse import urljoin, urlparse
from scrapers.hibid import polite_get, parse_catalog, parse_lot, collect_lot_map_for
from flask_login import login_user, logout_user, login_required, current_user
from functools import wraps


class HiBidScraper:
    def __init__(self):
        pass

    def scrape_catalog(self, catalog_url, target_lot_numbers=None):
        # Ensure deterministic numeric order of target lot numbers
        def _as_int(val):
            try:
                return int(val)
            except (TypeError, ValueError):
                return float("inf")

        target_lot_numbers = sorted(set(target_lot_numbers or []), key=_as_int)
        # fetch catalog
        resp, _, _ = polite_get(catalog_url, timeout=25)
        html = resp.text
        title, _end_text, lot_map = parse_catalog(html)

        # extend mapping across pages to make sure all target lots are findable
        if target_lot_numbers:
            extra = collect_lot_map_for(catalog_url, [str(n) for n in target_lot_numbers])
            lot_map.update(extra)

        base = f"{urlparse(catalog_url).scheme}://{urlparse(catalog_url).netloc}"
        lots = []
        for num in target_lot_numbers:
            key = str(num)
            href = lot_map.get(key) or lot_map.get(key.lstrip("0"))
            lot_url = href if (href or "").startswith("http") else urljoin(base, (href or "")) if href else catalog_url

            lresp, _, _ = polite_get(lot_url, timeout=20)
            d = parse_lot(lresp.text)

            lots.append({
                "lot_number": key,
                "title": d.get("title") or f"Lot {key}",
                "current_bid": d.get("current_bid_cents") or 0,   # cents int
                "buyer_premium": d.get("bp_pct"),
                "tax_rate": d.get("tax_pct"),
                "images": (d.get("image_urls") or [])[:3],
                "url": lot_url,
                "description": d.get("description"),
            })

        return {"title": title, "lots": lots}

    def scrape_lot(self, lot_url):
        resp, _, _ = polite_get(lot_url, timeout=20)
        d = parse_lot(resp.text)
        return {
            "title": d.get("title"),
            "current_bid": d.get("current_bid_cents") or 0,   # cents int
            "buyer_premium": d.get("bp_pct"),
            "tax_rate": d.get("tax_pct"),
            "images": (d.get("image_urls") or [])[:3],
            "url": lot_url,
            "description": d.get("description"),
        }
# === end adapter ===
# Register pillow_heif to handle HEIC/HEIF files
pillow_heif.register_heif_opener()

SCRAPE_PROGRESS = {}

def register_routes(app):
    

    

    def require_admin(f):
        """Decorator to require admin privileges"""
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            if not current_user.is_admin:
                flash('Admin privileges required.', 'error')
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated_function

    # Dashboard and main routes
    @app.route('/')
    @login_required
    def dashboard():
        search_query = request.args.get('search', '').strip()
        sort_by = request.args.get('sort', 'newest')
        category_filter = request.args.get('category', '').strip()
        page = request.args.get('page', 1, type=int)

        # Build query
        query = Item.query
        if search_query:
            query = query.filter(
                db.or_(
                    Item.name.ilike(f'%{search_query}%'),
                    Item.notes.ilike(f'%{search_query}%')
                )
            )
        if category_filter:
            query = query.filter(Item.category == category_filter)
        
        # Apply sorting
        if sort_by == 'oldest':
            query = query.order_by(Item.purchase_date.asc())
        elif sort_by == 'profit_high':
            # This is a simplified approach; in production you might want to use a computed column
            query = query.order_by((Item.sale_price - Item.purchase_price).desc().nulls_last())
        elif sort_by == 'profit_low':
            query = query.order_by((Item.sale_price - Item.purchase_price).asc().nulls_last())
        else:  # newest
            query = query.order_by(Item.purchase_date.desc())

        pagination = db.paginate(query, page=page, per_page=10)

        categories = [c[0] for c in db.session.query(Item.category).distinct().order_by(Item.category).all() if c[0]]

        # Calculate totals for current page
        active_items = [item for item in pagination.items if item.status != 'sold']
        sold_items = [item for item in pagination.items if item.status == 'sold']

        active_inventory_value = sum(item.total_costs for item in active_items)
        sold_count = len(sold_items)
        total_profit = sum(item.profit for item in sold_items if item.profit is not None)
        total_assets_cost = sum(asset.amount for asset in Asset.query.all())
        potential_profit_total = sum(item.potential_profit for item in active_items if item.potential_profit is not None)

        show_projections = Setting.get('dashboard_show_projections', 'on') == 'on'
        potential_profit_total = (
            sum(item.potential_profit for item in active_items if item.potential_profit is not None)
            if show_projections else 0
        )

        return render_template(
            'dashboard.html',
            active_items=active_items,
            sold_items=sold_items,
            search_query=search_query,
            sort_by=sort_by,
            active_inventory_value=active_inventory_value,
            sold_count=sold_count,
            total_assets_cost=total_assets_cost,
            total_profit=total_profit,
            potential_profit_total=potential_profit_total,
            dashboard_show_projections=show_projections,
            pagination=pagination,
            categories=categories,
            category_filter=category_filter,
        )


    # Supply routes
    @app.route('/supplies', methods=['GET', 'POST'])
    @login_required
    def supplies():
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            quantity = float(request.form.get('quantity', 0) or 0)
            unit = request.form.get('unit', '').strip() or None
            cost_str = request.form.get('cost', '').strip()
            if not name:
                flash('Name is required.', 'error')
                return redirect(url_for('supplies'))
            try:
                cost_cents = dollars_to_cents(float(cost_str)) if cost_str else 0
            except (ValueError, TypeError):
                flash('Invalid cost format.', 'error')
                return redirect(url_for('supplies'))
            supply = Supply(name=name, quantity=quantity, unit=unit, cost_cents=cost_cents)
            db.session.add(supply)
            db.session.commit()
            flash('Supply added successfully!', 'success')
            return redirect(url_for('supplies'))

        supplies = Supply.query.order_by(Supply.name).all()
        return render_template('supplies/list.html', supplies=supplies, cents_to_dollars=cents_to_dollars)

    @app.route('/supplies/<int:supply_id>/edit', methods=['POST'])
    @login_required
    def edit_supply(supply_id):
        supply = Supply.query.get_or_404(supply_id)
        name = request.form.get('name', '').strip()
        quantity = float(request.form.get('quantity', supply.quantity) or 0)
        unit = request.form.get('unit', '').strip() or None
        cost_str = request.form.get('cost', '').strip()
        if not name:
            flash('Name is required.', 'error')
            return redirect(url_for('supplies'))
        try:
            cost_cents = dollars_to_cents(float(cost_str)) if cost_str else supply.cost_cents
        except (ValueError, TypeError):
            flash('Invalid cost format.', 'error')
            return redirect(url_for('supplies'))
        supply.name = name
        supply.quantity = quantity
        supply.unit = unit
        supply.cost_cents = cost_cents
        db.session.commit()
        flash('Supply updated successfully!', 'success')
        return redirect(url_for('supplies'))

    @app.route('/supplies/<int:supply_id>/delete', methods=['POST'])
    @login_required
    def delete_supply(supply_id):
        supply = Supply.query.get_or_404(supply_id)
        db.session.delete(supply)
        db.session.commit()
        flash('Supply deleted successfully!', 'success')
        return redirect(url_for('supplies'))

    @app.route('/assets', methods=['GET', 'POST'])
    @login_required
    def assets():
        if request.method == 'POST':
            description = request.form.get('description', '').strip()
            amount_str = request.form.get('amount', '').strip()
            category = request.form.get('category', '').strip() or None
            date_str = request.form.get('date', '').strip()
            if not description or not amount_str:
                flash('Description and amount are required.', 'error')
                return redirect(url_for('assets'))
            try:
                amount = dollars_to_cents(float(amount_str))
            except (ValueError, TypeError):
                flash('Invalid amount format.', 'error')
                return redirect(url_for('assets'))
            try:
                asset_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else date.today()
            except ValueError:
                asset_date = date.today()
            asset = Asset(description=description, amount=amount, category=category, date=asset_date)
            db.session.add(asset)
            db.session.commit()
            flash('Asset added successfully!', 'success')
            return redirect(url_for('assets'))

        assets = Asset.query.order_by(Asset.date.desc()).all()
        return render_template('assets.html', assets=assets, cents_to_dollars=cents_to_dollars)

    @app.route('/assets/<int:asset_id>/edit', methods=['POST'])
    @login_required
    def edit_asset(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        description = request.form.get('description', '').strip()
        amount_str = request.form.get('amount', '').strip()
        category = request.form.get('category', '').strip() or None
        date_str = request.form.get('date', '').strip()
        if not description or not amount_str:
            flash('Description and amount are required.', 'error')
            return redirect(url_for('assets'))
        try:
            amount = dollars_to_cents(float(amount_str))
        except (ValueError, TypeError):
            flash('Invalid amount format.', 'error')
            return redirect(url_for('assets'))
        try:
            asset_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else asset.date
        except ValueError:
            asset_date = asset.date
        asset.description = description
        asset.amount = amount
        asset.category = category
        asset.date = asset_date
        db.session.commit()
        flash('Asset updated successfully!', 'success')
        return redirect(url_for('assets'))

    @app.route('/assets/<int:asset_id>/delete', methods=['POST'])
    @login_required
    def delete_asset(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        db.session.delete(asset)
        db.session.commit()
        flash('Asset deleted successfully!', 'success')
        return redirect(url_for('assets'))

    @app.route('/assets/<int:asset_id>/transfer', methods=['POST'])
    @login_required
    def transfer_asset(asset_id):
        asset = Asset.query.get_or_404(asset_id)
        item = Item(
            name=asset.description,
            purchase_date=asset.date or date.today(),
            purchase_price=asset.amount,
        )
        db.session.add(item)
        db.session.delete(asset)
        db.session.commit()
        flash('Asset transferred to inventory.', 'success')
        return redirect(url_for('items.item_detail', item_id=item.id))

    # Watchlist routes
    @app.route('/watchlist')
    @login_required
    def watchlist_catalogs():
        if Setting.get('watchlist_enabled') != 'on':
            flash('Watchlist feature is disabled.', 'info')
            return redirect(url_for('dashboard'))
        
        catalogs = Catalog.query.order_by(Catalog.created_at.desc()).all()
        return render_template('watchlist/catalogs.html', catalogs=catalogs)
    
    @app.route('/watchlist/add', methods=['POST'])
    @login_required
    def add_watchlist_item():
        catalog_url = request.form.get('catalog_url', '').strip()
        lot_numbers_raw = request.form.get('lot_numbers', '').strip()

        if not catalog_url:
            flash('Catalog URL is required.', 'error')
            return redirect(url_for('watchlist_catalogs'))

        # Parse lot numbers into a set for quick lookup
        lot_numbers = {
            num.strip() for num in lot_numbers_raw.split(',') if num.strip()
        }

        scraper = HiBidScraper()
        try:
            catalog_data = scraper.scrape_catalog(catalog_url, target_lot_numbers=lot_numbers)
            if catalog_data:
                # Create or update catalog using HiBid title
                catalog = Catalog.query.filter_by(url=catalog_url).first()
                if not catalog:
                    catalog = Catalog(url=catalog_url)
                    db.session.add(catalog)

                catalog.title = catalog_data['title']
                catalog.auction_date = catalog_data.get('auction_date')
                catalog.last_scraped = datetime.utcnow()

                added_count = 0

                def _lot_key(data):
                    try:
                        return int(data['lot_number'])
                    except (TypeError, ValueError):
                        return float("inf")

                for lot_data in sorted(catalog_data['lots'], key=_lot_key):
                    if lot_numbers and lot_data['lot_number'] not in lot_numbers:
                        continue

                    # Skip if lot already exists
                    existing = Lot.query.filter_by(
                        catalog_id=catalog.id, lot_number=lot_data['lot_number']
                    ).first()
                    if existing:
                        continue

                    lot = Lot(
                        catalog_id=catalog.id,
                        lot_number=lot_data['lot_number'],
                        title=lot_data['title'],
                        description=lot_data['description'],
                        current_bid=lot_data['current_bid'],
                        url=lot_data['url'],
                        buyer_premium=lot_data.get('buyer_premium')
                    )

                    # Download lot images to public folder
                    downloaded = []
                    for i, image_url in enumerate(lot_data['images']):
                        try:
                            resp = requests.get(
                                image_url,
                                timeout=10,
                                headers={IMAGE_DOWNLOAD_REFERER_HEADER: lot_data.get('url') or catalog_url}
                            )
                            if resp.status_code == 200:
                                ext = infer_image_ext(resp, image_url)
                                filename = (
                                    f"lot_{lot_data['lot_number']}_{i+1}_{secrets.token_hex(8)}.{ext}"
                                )
                                filepath = os.path.join(
                                    current_app.config['PUBLIC_FOLDER'], filename
                                )
                                with open(filepath, 'wb') as f:
                                    f.write(resp.content)
                                downloaded.append(url_for('public_file', filename=filename))
                        except Exception as e:
                            current_app.logger.error(
                                f"Error downloading image {image_url}: {str(e)}"
                            )

                    lot.images = downloaded
                    db.session.add(lot)
                    added_count += 1

                original_count = catalog.total_lots or 0
                catalog.total_lots = original_count + added_count
                db.session.commit()

                if added_count:
                    if original_count:
                        flash(
                            f'Catalog updated with {added_count} new lots!', 'success'
                        )
                    else:
                        flash(
                            f'Catalog added with {added_count} lots!', 'success'
                        )
                else:
                    flash('No matching lots were added.', 'warning')
            else:
                flash('Failed to scrape catalog data.', 'error')
        except Exception as e:
            current_app.logger.error(
                f'Error scraping catalog {catalog_url}: {str(e)}'
            )
            flash('Error occurred while scraping. Please check the URL.', 'error')

        return redirect(url_for('watchlist_catalogs'))


    @app.route('/watchlist/catalog/create', methods=['GET', 'POST'])
    @login_required
    def create_catalog_manual():
        """Create a catalog without scraping HiBid"""
        if request.method == 'POST':
            url = request.form.get('url', '').strip()
            title = request.form.get('title', '').strip()
            auction_date_str = request.form.get('auction_date', '').strip()
            bp_str = request.form.get('buyer_premium', '').strip()

            if not url:
                flash('Catalog URL is required.', 'error')
                return render_template(
                    'watchlist/add_catalog_manual.html',
                    default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
                )

            catalog = Catalog(url=url, title=title or None)

            if auction_date_str:
                try:
                    catalog.auction_date = datetime.strptime(
                        auction_date_str, '%Y-%m-%d'
                    ).date()
                except ValueError:
                    flash('Invalid auction date format. Use YYYY-MM-DD.', 'error')

            if bp_str:
                try:
                    catalog.buyer_premium = float(bp_str)
                except ValueError:
                    flash('Invalid buyer premium value.', 'error')

            db.session.add(catalog)
            db.session.commit()
            flash('Catalog created successfully!', 'success')
            return redirect(url_for('watchlist_catalogs'))

        return render_template(
            'watchlist/add_catalog_manual.html',
            default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
        )

    @app.route('/watchlist/catalog/<int:catalog_id>/lot/create', methods=['GET', 'POST'])
    @login_required
    def manual_add_lot(catalog_id):
        catalog = Catalog.query.get_or_404(catalog_id)

        if request.method == 'POST':
            lot_number = request.form.get('lot_number', '').strip()
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            current_bid = request.form.get('current_bid', '').strip()
            buyer_premium = request.form.get('buyer_premium', '').strip()
            tax_rate = request.form.get('tax_rate', '').strip()
            shipping_cost = request.form.get('shipping_cost', '').strip()

            if not lot_number:
                flash('Lot number is required.', 'error')
                return redirect(url_for('manual_add_lot', catalog_id=catalog_id))

            try:
                lot = Lot(
                    catalog_id=catalog.id,
                    lot_number=lot_number,
                    title=title or None,
                    description=description or None,
                    current_bid=dollars_to_cents(float(current_bid)) if current_bid else 0,
                    buyer_premium=float(buyer_premium) if buyer_premium else None,
                    tax_rate=float(tax_rate) if tax_rate else None,
                    shipping_cost=dollars_to_cents(float(shipping_cost)) if shipping_cost else 0,
                )

                uploaded_files = request.files.getlist('images')
                image_urls = []
                for file in uploaded_files:
                    if file and file.filename and allowed_file(file.filename):
                        filename = save_uploaded_image(file, current_app.config['PUBLIC_FOLDER'])
                        if filename:
                            image_urls.append(url_for('public_file', filename=filename))
                if image_urls:
                    lot.images = image_urls

                db.session.add(lot)
                catalog.total_lots = (catalog.total_lots or 0) + 1
                db.session.commit()

                flash('Lot added successfully!', 'success')
                return redirect(url_for('catalog_detail', catalog_id=catalog.id))
            except Exception as e:
                current_app.logger.error(f'Error adding manual lot: {e}')
                flash('Error adding lot.', 'error')
                return redirect(url_for('manual_add_lot', catalog_id=catalog.id))

        return render_template('watchlist/add_lot_manual.html', catalog=catalog)

    
    @app.route('/watchlist/catalog/<int:catalog_id>')
    @login_required
    def catalog_detail(catalog_id):
        catalog = Catalog.query.get_or_404(catalog_id)

        def _lot_sort_key(lot):
            try:
                return int(lot.lot_number)
            except (TypeError, ValueError):
                return float("inf")

        lots = sorted(
            Lot.query.filter_by(catalog_id=catalog_id).all(),
            key=_lot_sort_key,
        )
        return render_template('watchlist/catalog_detail.html', catalog=catalog, lots=lots)

    @app.route('/watchlist/catalog/<int:catalog_id>/edit', methods=['GET', 'POST'])
    @login_required
    def edit_catalog(catalog_id):
        catalog = Catalog.query.get_or_404(catalog_id)
        if request.method == 'POST':
            catalog.title = request.form.get('title', '').strip()
            auction_date_str = request.form.get('auction_date', '').strip()
            catalog.url = request.form.get('url', '').strip()

            if auction_date_str:
                try:
                    catalog.auction_date = datetime.strptime(auction_date_str, '%Y-%m-%d').date()
                except ValueError:
                    catalog.auction_date = None
            else:
                catalog.auction_date = None

            db.session.commit()
            flash('Catalog updated successfully!', 'success')
            return redirect(url_for('catalog_detail', catalog_id=catalog_id))

        return render_template('watchlist/edit_catalog.html', catalog=catalog)

    @app.route('/watchlist/catalog/<int:catalog_id>/delete', methods=['POST'])
    @login_required
    def delete_catalog(catalog_id):
        catalog = Catalog.query.get_or_404(catalog_id)
        db.session.delete(catalog)
        db.session.commit()
        flash('Catalog deleted successfully!', 'success')
        return redirect(url_for('watchlist_catalogs'))
    
    @app.route('/watchlist/lot/<int:lot_id>')
    @login_required
    def lot_detail(lot_id):
        lot = Lot.query.get_or_404(lot_id)
        return render_template('watchlist/lot_detail.html', lot=lot)
    
    @app.route('/watchlist/lot/<int:lot_id>/edit', methods=['POST'])
    @login_required
    def edit_lot(lot_id):
        lot = Lot.query.get_or_404(lot_id)
        
        lot.notes = request.form.get('notes', '').strip()

        # Handle overrides
        buyer_premium = request.form.get('buyer_premium', '').strip()
        tax_rate = request.form.get('tax_rate', '').strip()
        shipping_cost = request.form.get('shipping_cost', '').strip()
        current_bid = request.form.get('current_bid', '').strip()

        try:
            # Handle uploaded images
            uploaded_files = request.files.getlist('images')
            if uploaded_files:
                image_urls = list(lot.images)
                for file in uploaded_files:
                    if file and file.filename and allowed_file(file.filename):
                        filename = save_uploaded_image(file, current_app.config['PUBLIC_FOLDER'])
                        if filename:
                            image_urls.append(url_for('public_file', filename=filename))
                if image_urls:
                    lot.images = image_urls

            lot.buyer_premium = float(buyer_premium) if buyer_premium else None
            lot.tax_rate = float(tax_rate) if tax_rate else None
            lot.shipping_cost = dollars_to_cents(float(shipping_cost)) if shipping_cost else 0
            if current_bid:
                lot.current_bid = dollars_to_cents(float(current_bid))
            
            db.session.commit()
            flash('Lot updated successfully!', 'success')
        except (ValueError, TypeError):
            flash('Invalid number format in one or more fields.', 'error')
        
        return redirect(url_for('lot_detail', lot_id=lot_id))
    
    @app.route('/watchlist/lot/<int:lot_id>/import', methods=['POST'])
    @login_required
    def import_lot(lot_id):
        lot = Lot.query.get_or_404(lot_id)
        delete_lot = request.form.get('delete_lot')
        purchase_date_str = request.form.get('purchase_date')
        purchase_price_str = request.form.get('purchase_price')

        purchase_date = date.today()
        if purchase_date_str:
            try:
                purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
            except ValueError:
                pass

        purchase_price = lot.total_cost
        if purchase_price_str:
            try:
                purchase_price = dollars_to_cents(float(purchase_price_str))
            except ValueError:
                pass

        # Create new item from lot
        item = Item(
            name=lot.title or f"Lot {lot.lot_number}",
            purchase_date=purchase_date,
            purchase_price=purchase_price,
            notes=f"Imported from HiBid lot {lot.lot_number}\n\n{lot.description or ''}",
            status='active',
            is_auction=True,
            auction_bid=lot.current_bid,
            auction_buyer_premium=lot.effective_buyer_premium,
            auction_tax_rate=lot.effective_tax_rate
        )
        db.session.add(item)
        db.session.commit()
        
        # Download or copy and save images
        for i, image_url in enumerate(lot.images):
            try:
                if image_url.startswith('/public/'):
                    src_filename = image_url.split('/')[-1]
                    src_path = os.path.join(
                        current_app.config['PUBLIC_FOLDER'], src_filename
                    )
                    ext = src_filename.split('.')[-1].lower()
                    filename = f"lot_{lot.lot_number}_{i+1}_{secrets.token_hex(8)}.{ext}"
                    dest_path = os.path.join(
                        current_app.config['PUBLIC_FOLDER'], filename
                    )
                    shutil.copyfile(src_path, dest_path)
                    content_type = mimetypes.guess_type(src_path)[0] or 'image/jpeg'
                    image = Image(
                        item_id=item.id,
                        filename=filename,
                        original_filename=src_filename,
                        content_type=content_type,
                    )
                    db.session.add(image)
                else:
                    # >>> Updated: use Referer + infer real extension
                    response = requests.get(
                        image_url,
                        timeout=10,
                        headers={IMAGE_DOWNLOAD_REFERER_HEADER: lot.url or ""}
                    )
                    if response.status_code == 200:
                        ext = infer_image_ext(response, image_url)
                        filename = f"lot_{lot.lot_number}_{i+1}_{secrets.token_hex(8)}.{ext}"
                        filepath = os.path.join(
                            current_app.config['PUBLIC_FOLDER'], filename
                        )
                        with open(filepath, 'wb') as f:
                            f.write(response.content)
                        image = Image(
                            item_id=item.id,
                            filename=filename,
                            original_filename=f"lot_{lot.lot_number}_{i+1}.{ext}",
                            content_type=response.headers.get('content-type', 'image/jpeg'),
                        )
                        db.session.add(image)
            except Exception as e:
                current_app.logger.error(
                    f'Error handling image {image_url}: {str(e)}'
                )
        
        db.session.commit()
        if delete_lot:
            db.session.delete(lot)
            db.session.commit()
            flash(f'Lot imported as item "{item.name}" and removed from catalog!', 'success')
        else:
            flash(f'Lot imported as item "{item.name}"!', 'success')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return "", 204
        return redirect(url_for('items.item_detail', item_id=item.id))

    # Bulk operations
    @app.route('/watchlist/bulk', methods=['POST'])
    @login_required
    def bulk_operations():
        action = request.form.get('action')
        lot_ids = request.form.getlist('lot_ids')
        redirect_url = request.referrer or url_for('watchlist_catalogs')        
        
        if not lot_ids:
            flash('No lots selected.', 'error')
            return redirect(redirect_url)
        
        lot_ids = [int(id) for id in lot_ids]
        lots = Lot.query.filter(Lot.id.in_(lot_ids)).all()
        
        if action == 'refresh':
            scraper = HiBidScraper()
            updated_count = 0
            for lot in lots:
                try:
                    lot_data = scraper.scrape_lot(lot.url)
                    if lot_data:
                        lot.current_bid = lot_data['current_bid']
                        lot.title = lot_data['title']
                        lot.description = lot_data['description']

                        downloaded = []
                        for i, image_url in enumerate(lot_data['images']):
                            try:
                                resp = requests.get(
                                    image_url,
                                    timeout=10,
                                    headers={IMAGE_DOWNLOAD_REFERER_HEADER: lot.url or ""}
                                )
                                if resp.status_code == 200:
                                    ext = infer_image_ext(resp, image_url)
                                    filename = (
                                        f"lot_{lot.lot_number}_{i+1}_{secrets.token_hex(8)}.{ext}"
                                    )
                                    filepath = os.path.join(
                                        current_app.config['PUBLIC_FOLDER'], filename
                                    )
                                    with open(filepath, 'wb') as f:
                                        f.write(resp.content)
                                    downloaded.append(
                                        url_for('public_file', filename=filename)
                                    )
                            except Exception as e:
                                current_app.logger.error(
                                    f"Error downloading image {image_url}: {str(e)}"
                                )

                        lot.images = downloaded
                        lot.last_checked = datetime.utcnow()
                        updated_count += 1
                except Exception as e:
                    current_app.logger.error(
                        f'Error refreshing lot {lot.id}: {str(e)}'
                    )
            
            db.session.commit()
            flash(f'Refreshed {updated_count} lot(s).', 'success')
        
        elif action == 'delete':
            for lot in lots:
                db.session.delete(lot)
            db.session.commit()
            flash(f'Deleted {len(lots)} lot(s).', 'success')
        else:
            flash('Invalid action.', 'error')

        return redirect(redirect_url)

    # Settings routes
    @app.route('/settings/users')
    @require_admin
    def settings_users():
        users = User.query.order_by(User.created_at.desc()).all()
        return render_template('settings/users.html', users=users)

    @app.route('/settings/users', methods=['POST'])
    @require_admin
    def settings_users_post():
        action = request.form.get('action')
        
        if action == 'add_user':
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '').strip()
            is_admin = 'is_admin' in request.form

            if not username or not email or not password:
                flash('Username, email and password are required.', 'error')
                return redirect(url_for('settings_users'))

            if User.query.filter(db.or_(User.username == username, User.email == email)).first():
                flash('Username or email already exists.', 'error')
                return redirect(url_for('settings_users'))

            user = User(username=username, email=email, is_admin=is_admin)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'User "{username}" created successfully!', 'success')
        
        elif action == 'toggle_admin':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            if user and user.id != current_user.id:  # Can't change own admin status
                user.is_admin = not user.is_admin
                db.session.commit()
                flash(f'Admin status updated for "{user.username}".', 'success')

        elif action == 'edit_user':
            user_id = request.form.get('user_id')
            new_email = request.form.get('email', '').strip()
            new_password = request.form.get('password', '').strip()
            user = User.query.get(user_id)

            if user:
                if new_email and User.query.filter_by(email=new_email).filter(User.id != user.id).first():
                    flash('Email already exists.', 'error')
                    return redirect(url_for('settings_users'))
                if new_email:
                    user.email = new_email
                if new_password:
                    user.set_password(new_password)
                db.session.commit()
                flash(f'User "{user.username}" updated.', 'success')

        elif action == 'delete_user':
            user_id = request.form.get('user_id')
            user = User.query.get(user_id)
            if user and user.id != current_user.id:  # Can't delete self
                db.session.delete(user)
                db.session.commit()
                flash(f'User "{user.username}" deleted.', 'success')

        return redirect(url_for('settings_users'))

    @app.route('/settings/appearance')
    @require_admin
    def settings_appearance():
        return render_template('settings/appearance.html',
                             theme=Setting.get('theme', 'system'))

    @app.route('/settings/appearance', methods=['POST'])
    @require_admin
    def settings_appearance_post():
        theme = request.form.get('theme', 'system')
        Setting.set('theme', theme)
        flash('Appearance settings updated.', 'success')
        return redirect(url_for('settings_appearance'))

    @app.route('/settings')
    @require_admin
    def settings():
        return render_template('settings/main.html',
                             company_name=Setting.get('company_name', ''),
                             company_logo=Setting.get('company_logo', 'logo.png'),
                             favicon=Setting.get('favicon', 'icon.png'),
                             watchlist_enabled=Setting.get('watchlist_enabled', 'off') == 'on',
                             default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
                             default_tax_rate=Setting.get('default_tax_rate', '8.5'),
                             min_refresh_interval=Setting.get('min_refresh_interval', '30'),
                             dashboard_show_projections=Setting.get('dashboard_show_projections', 'on') == 'on')

    @app.route('/settings', methods=['POST'])
    @require_admin
    def settings_post():
        company_name = request.form.get('company_name', '').strip()
        if company_name:
            Setting.set('company_name', company_name)
            flash('Company name updated.', 'success')
        
        # Handle logo upload
        if 'logo' in request.files:
            file = request.files['logo']
            if file and file.filename and allowed_file(file.filename):
                filename = save_uploaded_image(file, current_app.config['PUBLIC_FOLDER'])
                if filename:
                    # Remove old logo
                    old_logo = Setting.get('company_logo')
                    if old_logo:
                        try:
                            os.unlink(os.path.join(current_app.config['PUBLIC_FOLDER'], old_logo))
                        except OSError:
                            pass
                    
                    Setting.set('company_logo', filename)
                    flash('Logo updated successfully!', 'success')

        # Handle favicon upload
        if 'favicon' in request.files:
            file = request.files['favicon']
            if file and file.filename and allowed_file(file.filename):
                filename = save_uploaded_image(file, current_app.config['PUBLIC_FOLDER'])
                if filename:
                    old_icon = Setting.get('favicon')
                    if old_icon:
                        try:
                            os.unlink(os.path.join(current_app.config['PUBLIC_FOLDER'], old_icon))
                        except OSError:
                            pass
                    Setting.set('favicon', filename)
                    flash('Favicon updated successfully!', 'success')
        
        # Watchlist settings
        Setting.set('watchlist_enabled', 'on' if request.form.get('watchlist_enabled') else 'off')
        Setting.set('default_buyer_premium', request.form.get('default_buyer_premium', '10.0'))
        Setting.set('default_tax_rate', request.form.get('default_tax_rate', '8.5'))
        Setting.set('min_refresh_interval', request.form.get('min_refresh_interval', '30'))

        # Dashboard settings
        Setting.set('dashboard_show_projections',
                    'on' if request.form.get('dashboard_show_projections') else 'off')
        
        flash('Settings updated successfully!', 'success')
        return redirect(url_for('settings'))

    @app.route('/settings/backend')
    @require_admin
    def settings_backend():
        return render_template('settings/backend.html',
                             smtp_host=Setting.get('smtp_host', ''),
                             smtp_port=Setting.get('smtp_port', ''),
                             smtp_username=Setting.get('smtp_username', ''),
                             smtp_password=Setting.get('smtp_password', ''),
                             smtp_from_email=Setting.get('smtp_from_email', ''),
                             smtp_use_tls=Setting.get('smtp_use_tls', 'on') == 'on',
                             smtp_use_ssl=Setting.get('smtp_use_ssl', 'off') == 'on',
                             email_send_password_reset=Setting.get('email_send_password_reset', 'on') == 'on',
                             email_template_password_reset_subject=Setting.get('email_template_password_reset_subject', 'Password Reset'),
                             email_template_password_reset_body=Setting.get('email_template_password_reset_body', 'Click the link to reset your password: {link}'),
                             email_send_email_change=Setting.get('email_send_email_change', 'on') == 'on',
                             email_template_email_change_subject=Setting.get('email_template_email_change_subject', 'Confirm your new email'),
                             email_template_email_change_body=Setting.get('email_template_email_change_body', 'Hi {username}, confirm your new email {new_email} by visiting {link}'))

    @app.route('/settings/backend', methods=['POST'])
    @require_admin
    def settings_backend_post():
        Setting.set('smtp_host', request.form.get('smtp_host', ''))
        Setting.set('smtp_port', request.form.get('smtp_port', ''))
        Setting.set('smtp_username', request.form.get('smtp_username', ''))
        Setting.set('smtp_password', request.form.get('smtp_password', ''))
        Setting.set('smtp_from_email', request.form.get('smtp_from_email', ''))
        Setting.set('smtp_use_tls', 'on' if request.form.get('smtp_use_tls') else 'off')
        Setting.set('smtp_use_ssl', 'on' if request.form.get('smtp_use_ssl') else 'off')

        Setting.set('email_send_password_reset', 'on' if request.form.get('email_send_password_reset') else 'off')
        Setting.set('email_template_password_reset_subject', request.form.get('email_template_password_reset_subject', ''))
        Setting.set('email_template_password_reset_body', request.form.get('email_template_password_reset_body', ''))

        Setting.set('email_send_email_change', 'on' if request.form.get('email_send_email_change') else 'off')
        Setting.set('email_template_email_change_subject', request.form.get('email_template_email_change_subject', ''))
        Setting.set('email_template_email_change_body', request.form.get('email_template_email_change_body', ''))

        flash('Backend settings updated.', 'success')
        return redirect(url_for('settings_backend'))

    @app.route('/settings/backend/test', methods=['POST'])
    @require_admin
    def settings_backend_test():
        test_email = request.form.get('test_email', '').strip()
        if not test_email:
            flash('Please provide a test email address.', 'error')
        else:
            if send_email(test_email, 'SMTP Test', 'This is a test email from FlipTrack.'):
                flash('Test email sent successfully!', 'success')
            else:
                flash('Failed to send test email. Check SMTP settings.', 'error')
        return redirect(url_for('settings_backend'))

    # Analytics route
    @app.route('/analytics')
    @login_required
    def analytics():
        # Get filter parameters
        range_type = request.args.get('range', 'month')  # week, month, year
        offset = int(request.args.get('offset', 0))  # For prev/next navigation
        
        # Calculate date range
        today = date.today()
        if range_type == 'week':
            start_date = today - timedelta(days=today.weekday() + 7 * offset)
            end_date = start_date + timedelta(days=6)
        elif range_type == 'year':
            year = today.year - offset
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)
        else:  # month
            if offset == 0:
                start_date = today.replace(day=1)
                if start_date.month == 12:
                    end_date = date(start_date.year + 1, 1, 1) - timedelta(days=1)
                else:
                    end_date = date(start_date.year, start_date.month + 1, 1) - timedelta(days=1)
            else:
                # Calculate target month/year
                target_month = today.month - offset
                target_year = today.year
                while target_month <= 0:
                    target_month += 12
                    target_year -= 1
                while target_month > 12:
                    target_month -= 12
                    target_year += 1
                
                start_date = date(target_year, target_month, 1)
                if target_month == 12:
                    end_date = date(target_year + 1, 1, 1) - timedelta(days=1)
                else:
                    end_date = date(target_year, target_month + 1, 1) - timedelta(days=1)
        
        # Query sold items and asset purchases in date range
        sold_items = Item.query.filter(
            Item.status == 'sold',
            Item.sale_date.between(start_date, end_date),
            Item.sale_price.isnot(None)
        ).all()
        assets = Asset.query.filter(Asset.date.between(start_date, end_date)).all()

        # Calculate metrics
        total_sales = len(sold_items)
        total_revenue = sum(item.sale_price for item in sold_items)
        total_assets = sum(a.amount for a in assets)
        total_costs = sum(item.total_costs for item in sold_items)
        total_profit = total_revenue - total_costs
        avg_roi = 0
        if sold_items:
            valid_rois = [item.roi for item in sold_items if item.roi is not None]
            if valid_rois:
                avg_roi = sum(valid_rois) / len(valid_rois)

        unsold_items = Item.query.filter(
            Item.status != 'sold',
            Item.expected_sale_price.isnot(None)
        ).all()
        projected_profit = sum(item.potential_profit for item in unsold_items if item.potential_profit is not None)
        avg_potential_roi = 0
        if unsold_items:
            potential_rois = [item.potential_roi for item in unsold_items if item.potential_roi is not None]
            if potential_rois:
                avg_potential_roi = sum(potential_rois) / len(potential_rois)

        avg_sale_price = total_revenue / total_sales if total_sales else 0
        time_to_sales = [
            (item.sale_date - item.purchase_date).days
            for item in sold_items
            if item.sale_date and item.purchase_date
        ]
        median_time_to_sale = statistics.median(time_to_sales) if time_to_sales else 0
        top_item = max(sold_items, key=lambda i: i.profit or 0, default=None)

        period_days = (end_date - start_date).days + 1
        prev_start = start_date - timedelta(days=period_days)
        prev_end = start_date - timedelta(days=1)
        prev_items = Item.query.filter(
            Item.status == 'sold',
            Item.sale_date.between(prev_start, prev_end),
            Item.sale_price.isnot(None)
        ).all()
        prev_revenue = sum(item.sale_price for item in prev_items)
        prev_profit = sum(item.sale_price - item.total_costs for item in prev_items)
        revenue_change = ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue else None
        profit_change = ((total_profit - prev_profit) / prev_profit * 100) if prev_profit else None

        daily = {}
        for item in sold_items:
            key = item.sale_date.strftime('%Y-%m-%d')
            daily.setdefault(key, {'revenue': 0, 'profit': 0, 'assets': 0})
            daily[key]['revenue'] += item.sale_price
            daily[key]['profit'] += item.sale_price - item.total_costs
        for asset in assets:
            key = asset.date.strftime('%Y-%m-%d')
            daily.setdefault(key, {'revenue': 0, 'profit': 0, 'assets': 0})
            daily[key]['assets'] += asset.amount
        chart_labels = sorted(daily.keys())
        revenue_running = 0
        profit_running = 0
        assets_running = 0
        chart_revenue = []
        chart_profit = []
        chart_cash_flow = []
        for d in chart_labels:
            revenue_running += daily[d]['revenue']
            profit_running += daily[d]['profit']
            assets_running += daily[d]['assets']
            chart_revenue.append(revenue_running / 100)
            chart_profit.append(profit_running / 100)
            chart_cash_flow.append((profit_running - assets_running) / 100)

        category_map = {}
        for item in sold_items:
            cat = item.category or 'Uncategorized'
            category_map.setdefault(cat, 0)
            category_map[cat] += item.sale_price
        category_labels = list(category_map.keys())
        category_values = [category_map[c] / 100 for c in category_labels]

        return render_template(
            'analytics.html',
            range_type=range_type,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            sold_items=sold_items,
            total_sales=total_sales,
            total_revenue=total_revenue,
            total_costs=total_costs,
            total_assets=total_assets,
            total_profit=total_profit,
            avg_roi=avg_roi,
            avg_sale_price=avg_sale_price,
            median_time_to_sale=median_time_to_sale,
            top_item=top_item,
            revenue_change=revenue_change,
            profit_change=profit_change,
            chart_labels=chart_labels,
            chart_revenue=chart_revenue,
            chart_profit=chart_profit,
            chart_cash_flow=chart_cash_flow,
            category_labels=category_labels,
            category_values=category_values,
            projected_profit=projected_profit,
            avg_potential_roi=avg_potential_roi
        )


    # Import/Export routes
    @app.route('/export', methods=['GET', 'POST'])
    @login_required
    def export_items():
        """Create an application backup with selectable components."""
        if request.method == 'POST':
            selected = request.form.getlist('data')
            if not selected:
                flash('No data selected for export.', 'error')
                return redirect(request.referrer or url_for('dashboard'))
        else:
            selected = ['items', 'supplies', 'watchlist', 'settings', 'photos']

        include_db = any(x in selected for x in ['items', 'supplies', 'watchlist', 'settings'])
        include_photos = 'photos' in selected

        backup_io = io.BytesIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_db_path = None
            if include_db:
                # Copy the SQLite file then strip unselected tables
                db_path = db.engine.url.database
                if db_path:
                    db_path = db_path if os.path.isabs(db_path) else os.path.join(current_app.root_path, db_path)
                    if os.path.exists(db_path):
                        tmp_db_path = os.path.join(tmpdir, 'inventory.db')
                        shutil.copy(db_path, tmp_db_path)

                        conn = sqlite3.connect(tmp_db_path)
                        cur = conn.cursor()

                        table_groups = {
                            'items': ['items', 'images', 'repairs', 'other_costs'],
                            'supplies': ['supplies'],
                            'watchlist': ['catalogs', 'lots'],
                            'settings': ['settings', 'users', 'remember_tokens'],
                        }
                        keep_tables = set()
                        for key in selected:
                            keep_tables.update(table_groups.get(key, []))
                        if 'items' in selected and 'supplies' in selected:
                            keep_tables.add('supply_usages')

                        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                        all_tables = {r[0] for r in cur.fetchall()}
                        drop_tables = all_tables - keep_tables
                        for tbl in drop_tables:
                            if tbl.startswith('sqlite_'):
                                continue
                            cur.execute(f'DROP TABLE IF EXISTS {tbl}')
                        conn.commit()
                        conn.close()

            with zipfile.ZipFile(backup_io, 'w', zipfile.ZIP_DEFLATED) as zf:
                if tmp_db_path:
                    zf.write(tmp_db_path, arcname='inventory.db')

                if include_photos:
                    for folder in [current_app.config.get('UPLOAD_FOLDER'), current_app.config.get('PUBLIC_FOLDER')]:
                        if not folder:
                            continue
                        folder_path = os.path.join(current_app.root_path, folder)
                        if os.path.exists(folder_path):
                            for root_dir, _, files in os.walk(folder_path):
                                for file_name in files:
                                    abs_path = os.path.join(root_dir, file_name)
                                    rel_path = os.path.relpath(abs_path, current_app.root_path)
                                    zf.write(abs_path, arcname=rel_path)

        backup_io.seek(0)
        return send_file(
            backup_io,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'fliptrack_backup_{date.today().isoformat()}.zip'
        )
    
    @app.route('/import', methods=['POST'])
    @login_required
    def import_items():
        """Restore application data from a backup created by export_items."""
        if 'file' not in request.files or not request.files['file'].filename:
            flash('No file selected.', 'error')
            return redirect(url_for('dashboard'))

        file = request.files['file']

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_path = os.path.join(tmpdir, 'backup.zip')
                file.save(zip_path)

                with zipfile.ZipFile(zip_path, 'r') as zf:
                    zf.extractall(tmpdir)

                db_path = db.engine.url.database
                if db_path:
                    db_path = db_path if os.path.isabs(db_path) else os.path.join(current_app.root_path, db_path)
                    db.session.remove()
                    db.engine.dispose()
                    src_db = os.path.join(tmpdir, 'inventory.db')
                    if os.path.exists(src_db):
                        if os.path.exists(db_path):
                            os.remove(db_path)
                        shutil.move(src_db, db_path)
                db.create_all()

                for folder in [current_app.config.get('UPLOAD_FOLDER'), current_app.config.get('PUBLIC_FOLDER')]:
                    if not folder:
                        continue
                    src_dir = os.path.join(tmpdir, folder)
                    if os.path.exists(src_dir):
                        dest_dir = os.path.join(current_app.root_path, folder)
                        if os.path.exists(dest_dir):
                            shutil.rmtree(dest_dir)
                        shutil.move(src_dir, dest_dir)

            flash('Backup imported successfully!', 'success')
        except Exception as e:
            flash(f'Error importing backup: {str(e)}', 'error')

        return redirect(url_for('dashboard'))

    # File serving routes
    @app.route('/uploads/<filename>')
    @login_required
    def uploaded_file(filename):
        return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
    
    @app.route('/public/<filename>')
    def public_file(filename):
        return send_from_directory(current_app.config['PUBLIC_FOLDER'], filename)

    # Template context processors
    @app.context_processor
    def inject_globals():
        return {
            'company_name': Setting.get('company_name', 'Inventory Tracker'),
            'company_logo': Setting.get('company_logo', 'logo.png'),
            'favicon': Setting.get('favicon', 'icon.png'),
            'theme': Setting.get('theme', 'system'),
            'watchlist_enabled': Setting.get('watchlist_enabled') == 'on',
            'cents_to_dollars': cents_to_dollars,
            'datetime': datetime,
            'date': date
        }

    @app.route('/watchlist/start', methods=['POST'])
    @login_required
    def watchlist_start():
        catalog_url = request.form.get('catalog_url', '').strip()
        lot_numbers_raw = request.form.get('lot_numbers', '').strip()
        if not catalog_url:
            return jsonify({"error":"Catalog URL is required."}), 400
        
        lot_numbers = {num.strip() for num in lot_numbers_raw.split(',') if num.strip()}
        progress_id = str(uuid.uuid4())
        SCRAPE_PROGRESS[progress_id] = {
            "found": 0,
            "total": len(lot_numbers) if lot_numbers else None,
            "done": False,
            "page": 0,
            "message": "Starting…"
        }

        def run_job(progress_id, catalog_url, lot_numbers):
            with app.app_context():
                def progress_cb(ev):
                    st = SCRAPE_PROGRESS.get(progress_id)
                    if not st: 
                        return
                    if ev.get("phase") == "paging":
                        st["page"] = ev.get("page", st.get("page", 0))
                        st["message"] = f"Scanning page {st['page']}…"
                    elif ev.get("phase") == "found":
                        st["found"] = st.get("found", 0) + 1
                        st["message"] = f"Found {st['found']}/{st.get('total') or '?'} (page {st.get('page',0)})"
                    elif ev.get("phase") == "done":
                        st["done"] = True
                        st["message"] = st.get("message","Done")
                try:
                    scraper = HiBidScraper()
                    catalog_data = scraper.scrape_catalog(catalog_url, target_lot_numbers=lot_numbers or None, progress_cb=progress_cb)
                    # Store a flag if nothing found to help UI
                    SCRAPE_PROGRESS[progress_id]["result_total"] = (catalog_data or {}).get("total_lots") if catalog_data else 0
                    SCRAPE_PROGRESS[progress_id]["done"] = True
                    SCRAPE_PROGRESS[progress_id]["message"] = "Completed"
                except Exception as e:
                    SCRAPE_PROGRESS[progress_id]["done"] = True
                    SCRAPE_PROGRESS[progress_id]["error"] = str(e)
                    SCRAPE_PROGRESS[progress_id]["message"] = "Error"

        th = threading.Thread(target=run_job, args=(progress_id, catalog_url, lot_numbers), daemon=True)
        th.start()
        return jsonify({"progress_id": progress_id})

    @app.route('/watchlist/progress/<progress_id>')
    @login_required
    def watchlist_progress(progress_id):
        st = SCRAPE_PROGRESS.get(progress_id)
        if not st:
            return jsonify({"error":"Not found"}), 404
        return jsonify(st)
    
