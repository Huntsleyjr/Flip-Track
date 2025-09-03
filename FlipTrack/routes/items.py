import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required
from models import db, Item, Image, Repair, OtherCost, Supply, SupplyUsage, Setting
from utils import allowed_file, save_uploaded_image, dollars_to_cents

items_bp = Blueprint('items', __name__)

@items_bp.route('/items')
@login_required
def items_list():
    return redirect(url_for('dashboard'))

@items_bp.route('/items/<int:item_id>')
@login_required
def item_detail(item_id):
    item = Item.query.get_or_404(item_id)
    supplies = Supply.query.order_by(Supply.name).all()
    return render_template('items/detail.html', item=item, supplies=supplies)

@items_bp.route('/items/<int:item_id>/mark_sold', methods=['POST'])
@login_required
def item_mark_sold(item_id):
    item = Item.query.get_or_404(item_id)
    sale_date_str = request.form.get('sale_date', '').strip()
    sale_price_str = request.form.get('sale_price', '').strip()
    expected_sale_price_str = request.form.get('expected_sale_price', '').strip()

    if sale_date_str and sale_price_str:
        try:
            item.sale_date = datetime.strptime(sale_date_str, '%Y-%m-%d').date()
            item.sale_price = dollars_to_cents(float(sale_price_str))
            item.status = 'sold'
            if expected_sale_price_str:
                item.expected_sale_price = dollars_to_cents(float(expected_sale_price_str))
            db.session.commit()
            flash('Item marked as sold!', 'success')
        except (ValueError, TypeError):
            flash('Invalid date or price format.', 'error')
    elif expected_sale_price_str:
        try:
            item.expected_sale_price = dollars_to_cents(float(expected_sale_price_str))
            db.session.commit()
            flash('Expected sale price updated.', 'success')
        except (ValueError, TypeError):
            flash('Invalid price format.', 'error')
    else:
        flash('Sale date and price are required.', 'error')

    return redirect(url_for('dashboard'))

@items_bp.route('/items/new')
@login_required
def item_new():
  return render_template(
      'items/new.html',
      item=None,
      default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
      default_tax_rate=Setting.get('default_tax_rate', '8.5')
  )

@items_bp.route('/items/new', methods=['POST'])
@login_required
def item_new_post():
    name = request.form.get('name', '').strip()
    purchase_date_str = request.form.get('purchase_date', '').strip()
    purchase_price_str = request.form.get('purchase_price', '').strip()
    is_auction = 'is_auction' in request.form
    auction_bid_str = request.form.get('auction_bid', '').strip()
    buyer_premium_str = request.form.get('buyer_premium', '').strip()
    tax_rate_str = request.form.get('tax_rate', '').strip()
    expected_sale_price_str = request.form.get('expected_sale_price', '').strip()
    notes = request.form.get('notes', '').strip()
    category = request.form.get('category', '').strip()
    status = request.form.get('status', 'active')

    if not name or not purchase_date_str or (is_auction and not auction_bid_str) or (not is_auction and not purchase_price_str):
        flash('Name, purchase date, and purchase price are required.', 'error')
        return render_template(
            'items/new.html',
            item=None,
            default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
            default_tax_rate=Setting.get('default_tax_rate', '8.5')
        )

    try:
        purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
        if is_auction:
            auction_bid = dollars_to_cents(float(auction_bid_str))
            buyer_premium = float(buyer_premium_str or 0)
            tax_rate = float(tax_rate_str or 0)
            premium_amount = int(auction_bid * (buyer_premium / 100))
            subtotal = auction_bid + premium_amount
            tax_amount = int(subtotal * (tax_rate / 100))
            purchase_price = auction_bid + premium_amount + tax_amount
        else:
            purchase_price = dollars_to_cents(float(purchase_price_str))
    except (ValueError, TypeError):
        flash('Invalid date or price format.', 'error')
        return render_template(
            'items/new.html',
            item=None,
            default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
            default_tax_rate=Setting.get('default_tax_rate', '8.5')
        )

    item = Item(
        name=name,
        purchase_date=purchase_date,
        purchase_price=purchase_price,
        notes=notes,
        category=category or None,
        status=status,
        is_auction=is_auction,
        auction_bid=auction_bid if is_auction else None,
        auction_buyer_premium=buyer_premium if is_auction else None,
        auction_tax_rate=tax_rate if is_auction else None,
        expected_sale_price=dollars_to_cents(float(expected_sale_price_str)) if expected_sale_price_str else None
    )
    db.session.add(item)
    db.session.commit()
    
    # Handle file uploads
    uploaded_files = request.files.getlist('images')
    for file in uploaded_files:
        if file and file.filename and allowed_file(file.filename):
            filename = save_uploaded_image(file, current_app.config['UPLOAD_FOLDER'])
            if filename:
                image = Image(
                    item_id=item.id,
                    filename=filename,
                    original_filename=file.filename,
                    content_type=file.content_type
                )
                db.session.add(image)
    
    db.session.commit()
    flash('Item created successfully!', 'success')
    return redirect(url_for('items.item_detail', item_id=item.id))

@items_bp.route('/items/<int:item_id>/edit')
@login_required
def item_edit(item_id):
    item = Item.query.get_or_404(item_id)
    return render_template(
        'items/edit.html',
        item=item,
        default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
        default_tax_rate=Setting.get('default_tax_rate', '8.5')
    )

@items_bp.route('/items/<int:item_id>/edit', methods=['POST'])
@login_required
def item_edit_post(item_id):
    item = Item.query.get_or_404(item_id)
    
    action = request.form.get('action', 'update')
    
    if action == 'delete':
        # Delete all associated images from filesystem
        for image in item.images:
            try:
                os.unlink(os.path.join(current_app.config['UPLOAD_FOLDER'], image.filename))
            except OSError:
                pass
        
        db.session.delete(item)
        db.session.commit()
        flash('Item deleted successfully.', 'success')
        return redirect(url_for('dashboard'))
    
    elif action == 'mark_sold':
        sale_date_str = request.form.get('sale_date', '').strip()
        sale_price_str = request.form.get('sale_price', '').strip()

        if not sale_date_str or not sale_price_str:
            flash('Sale date and price are required.', 'error')
            return render_template(
                'items/edit.html',
                item=item,
                default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
                default_tax_rate=Setting.get('default_tax_rate', '8.5')
            )
        
        try:
            item.sale_date = datetime.strptime(sale_date_str, '%Y-%m-%d').date()
            item.sale_price = dollars_to_cents(float(sale_price_str))
            item.status = 'sold'
            db.session.commit()
            flash('Item marked as sold!', 'success')
            return redirect(url_for('items.item_detail', item_id=item.id))
        except (ValueError, TypeError):
            flash('Invalid date or price format.', 'error')
            return render_template(
                'items/edit.html',
                item=item,
                default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
                default_tax_rate=Setting.get('default_tax_rate', '8.5')
            )
    
    else:  # update
        name = request.form.get('name', '').strip()
        purchase_date_str = request.form.get('purchase_date', '').strip()
        purchase_price_str = request.form.get('purchase_price', '').strip()
        is_auction = 'is_auction' in request.form
        auction_bid_str = request.form.get('auction_bid', '').strip()
        buyer_premium_str = request.form.get('buyer_premium', '').strip()
        tax_rate_str = request.form.get('tax_rate', '').strip()
        notes = request.form.get('notes', '').strip()
        category = request.form.get('category', '').strip()
        expected_sale_price_str = request.form.get('expected_sale_price', '').strip()
        status = request.form.get('status', item.status)

        if not name or not purchase_date_str or (is_auction and not auction_bid_str) or (not is_auction and not purchase_price_str):
            flash('Name, purchase date, and purchase price are required.', 'error')
            return render_template(
                'items/edit.html',
                item=item,
                default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
                default_tax_rate=Setting.get('default_tax_rate', '8.5')
            )

        try:
            item.name = name
            item.purchase_date = datetime.strptime(purchase_date_str, '%Y-%m-%d').date()
            if is_auction:
                auction_bid = dollars_to_cents(float(auction_bid_str))
                buyer_premium = float(buyer_premium_str or 0)
                tax_rate = float(tax_rate_str or 0)
                premium_amount = int(auction_bid * (buyer_premium / 100))
                subtotal = auction_bid + premium_amount
                tax_amount = int(subtotal * (tax_rate / 100))
                item.purchase_price = auction_bid + premium_amount + tax_amount
                item.is_auction = True
                item.auction_bid = auction_bid
                item.auction_buyer_premium = buyer_premium
                item.auction_tax_rate = tax_rate
            else:
                item.purchase_price = dollars_to_cents(float(purchase_price_str))
                item.is_auction = False
                item.auction_bid = None
                item.auction_buyer_premium = None
                item.auction_tax_rate = None
            item.notes = notes
            item.category = category or None
            item.status = status
            item.expected_sale_price = dollars_to_cents(float(expected_sale_price_str)) if expected_sale_price_str else None

            # Handle thumbnail selection
            thumbnail_id = request.form.get('thumbnail_id')
            if thumbnail_id:
                item.thumbnail_id = int(thumbnail_id)
            
            db.session.commit()
            
            # Handle new file uploads
            uploaded_files = request.files.getlist('images')
            for file in uploaded_files:
                if file and file.filename and allowed_file(file.filename):
                    filename = save_uploaded_image(file, current_app.config['UPLOAD_FOLDER'])
                    if filename:
                        image = Image(
                            item_id=item.id,
                            filename=filename,
                            original_filename=file.filename,
                            content_type=file.content_type
                        )
                        db.session.add(image)
            
            db.session.commit()
            flash('Item updated successfully!', 'success')
            return redirect(url_for('items.item_detail', item_id=item.id))
            
        except (ValueError, TypeError):
            flash('Invalid date or price format.', 'error')
            return render_template(
                'items/edit.html',
                item=item,
                default_buyer_premium=Setting.get('default_buyer_premium', '10.0'),
                default_tax_rate=Setting.get('default_tax_rate', '8.5')
            )

# Image management routes
@items_bp.route('/images/<int:image_id>/delete', methods=['POST'])
@login_required
def delete_image(image_id):
    image = Image.query.get_or_404(image_id)
    item_id = image.item_id
    
    # Remove file from filesystem
    try:
        os.unlink(os.path.join(current_app.config['UPLOAD_FOLDER'], image.filename))
    except OSError:
        pass
    
    db.session.delete(image)
    db.session.commit()
    flash('Image deleted successfully!', 'success')
    
    if item_id:
        return redirect(url_for('items.item_detail', item_id=item_id))
    return redirect(url_for('dashboard'))

# Repair management
@items_bp.route('/items/<int:item_id>/repairs/add', methods=['POST'])
@login_required
def add_repair(item_id):
    item = Item.query.get_or_404(item_id)
    
    notes = request.form.get('notes', '').strip()
    expected_cost_str = request.form.get('expected_cost', '').strip()
    status = request.form.get('status', 'pending')
    
    if not notes:
        flash('Repair notes are required.', 'error')
        return redirect(url_for('items.item_detail', item_id=item_id))
    
    expected_cost = None
    if expected_cost_str:
        try:
            expected_cost = dollars_to_cents(float(expected_cost_str))
        except (ValueError, TypeError):
            flash('Invalid expected cost format.', 'error')
            return redirect(url_for('items.item_detail', item_id=item_id))
    
    repair = Repair(
        item_id=item_id,
        notes=notes,
        expected_cost=expected_cost,
        status=status
    )
    db.session.add(repair)
    db.session.commit()

    # Handle supply usage
    supply_ids = request.form.getlist('supply_id[]')
    quantities = request.form.getlist('quantity_used[]')
    for sid, qty_str in zip(supply_ids, quantities):
        if not sid or not qty_str:
            continue
        try:
            qty = float(qty_str)
        except ValueError:
            continue
        supply = Supply.query.get(int(sid))
        if supply:
            cost = supply.apply_usage(qty)
            usage = SupplyUsage(repair_id=repair.id, supply_id=supply.id, quantity_used=qty, cost_cents=cost)
            db.session.add(usage)

    # Handle repair image uploads
    uploaded_files = request.files.getlist('repair_images[]')
    for file in uploaded_files:
        if file and file.filename and allowed_file(file.filename):
            filename = save_uploaded_image(file, current_app.config['UPLOAD_FOLDER'])
            if filename:
                image = Image(
                    item_id=item_id,
                    repair_id=repair.id,
                    filename=filename,
                    original_filename=file.filename,
                    content_type=file.content_type
                )
                db.session.add(image)

    db.session.commit()
    flash('Repair added successfully!', 'success')
    return redirect(url_for('items.item_detail', item_id=item_id))


@items_bp.route('/repairs/<int:repair_id>/edit', methods=['POST'])
@login_required
def edit_repair(repair_id):
    repair = Repair.query.get_or_404(repair_id)
    item_id = repair.item_id

    notes = request.form.get('notes', '').strip()
    expected_cost_str = request.form.get('expected_cost', '').strip()
    final_cost_str = request.form.get('final_cost', '').strip()
    status = request.form.get('status', repair.status)

    if not notes:
        flash('Repair notes are required.', 'error')
        return redirect(url_for('items.item_detail', item_id=item_id))

    repair.notes = notes
    repair.status = status

    repair.expected_cost = None
    if expected_cost_str:
        try:
            repair.expected_cost = dollars_to_cents(float(expected_cost_str))
        except (ValueError, TypeError):
            flash('Invalid expected cost format.', 'error')
            return redirect(url_for('items.item_detail', item_id=item_id))

    repair.final_cost = None
    if final_cost_str:
        try:
            repair.final_cost = dollars_to_cents(float(final_cost_str))
        except (ValueError, TypeError):
            flash('Invalid final cost format.', 'error')
            return redirect(url_for('items.item_detail', item_id=item_id))

    # Handle supply usage additions
    supply_ids = request.form.getlist('supply_id[]')
    quantities = request.form.getlist('quantity_used[]')
    for sid, qty_str in zip(supply_ids, quantities):
        if not sid or not qty_str:
            continue
        try:
            qty = float(qty_str)
        except ValueError:
            continue
        supply = Supply.query.get(int(sid))
        if supply:
            cost = supply.apply_usage(qty)
            usage = SupplyUsage(repair_id=repair.id, supply_id=supply.id, quantity_used=qty, cost_cents=cost)
            db.session.add(usage)

    # Handle additional repair image uploads
    uploaded_files = request.files.getlist('repair_images[]')
    for file in uploaded_files:
        if file and file.filename and allowed_file(file.filename):
            filename = save_uploaded_image(file, current_app.config['UPLOAD_FOLDER'])
            if filename:
                image = Image(
                    item_id=item_id,
                    repair_id=repair.id,
                    filename=filename,
                    original_filename=file.filename,
                    content_type=file.content_type
                )
                db.session.add(image)

    db.session.commit()
    flash('Repair updated successfully!', 'success')
    return redirect(url_for('items.item_detail', item_id=item_id))

@items_bp.route('/repairs/<int:repair_id>/delete', methods=['POST'])
@login_required
def delete_repair(repair_id):
    repair = Repair.query.get_or_404(repair_id)
    item_id = repair.item_id

    # Delete associated images
    for image in repair.images:
        try:
            os.unlink(os.path.join(current_app.config['UPLOAD_FOLDER'], image.filename))
        except OSError:
            pass

    # Restore supplies
    for usage in repair.supplies:
        if usage.supply:
            usage.supply.quantity += usage.quantity_used
            usage.supply.cost_cents += usage.cost_cents

    db.session.delete(repair)
    db.session.commit()
    flash('Repair deleted successfully!', 'success')
    return redirect(url_for('items.item_detail', item_id=item_id))

# Other costs management
@items_bp.route('/items/<int:item_id>/costs/add', methods=['POST'])
@login_required
def add_other_cost(item_id):
    item = Item.query.get_or_404(item_id)
    
    description = request.form.get('description', '').strip()
    amount_str = request.form.get('amount', '').strip()
    cost_date_str = request.form.get('date', '').strip()
    
    if not description or not amount_str:
        flash('Description and amount are required.', 'error')
        return redirect(url_for('items.item_detail', item_id=item_id))
    
    try:
        amount = dollars_to_cents(float(amount_str))
        cost_date = datetime.strptime(cost_date_str, '%Y-%m-%d').date() if cost_date_str else date.today()
    except (ValueError, TypeError):
        flash('Invalid amount or date format.', 'error')
        return redirect(url_for('items.item_detail', item_id=item_id))
    
    other_cost = OtherCost(
        item_id=item_id,
        description=description,
        amount=amount,
        date=cost_date
    )
    db.session.add(other_cost)
    db.session.commit()
    flash('Cost added successfully!', 'success')
    return redirect(url_for('items.item_detail', item_id=item_id))

@items_bp.route('/costs/<int:cost_id>/edit', methods=['POST'])
@login_required
def edit_other_cost(cost_id):
    cost = OtherCost.query.get_or_404(cost_id)
    item_id = cost.item_id

    description = request.form.get('description', '').strip()
    amount_str = request.form.get('amount', '').strip()
    cost_date_str = request.form.get('date', '').strip()

    if not description or not amount_str:
        flash('Description and amount are required.', 'error')
        return redirect(url_for('items.item_detail', item_id=item_id))

    try:
        amount = dollars_to_cents(float(amount_str))
        cost_date = datetime.strptime(cost_date_str, '%Y-%m-%d').date() if cost_date_str else cost.date
    except (ValueError, TypeError):
        flash('Invalid amount or date format.', 'error')
        return redirect(url_for('items.item_detail', item_id=item_id))

    cost.description = description
    cost.amount = amount
    cost.date = cost_date
    db.session.commit()
    flash('Cost updated successfully!', 'success')
    return redirect(url_for('items.item_detail', item_id=item_id))

@items_bp.route('/costs/<int:cost_id>/delete', methods=['POST'])
@login_required
def delete_other_cost(cost_id):
    cost = OtherCost.query.get_or_404(cost_id)
    item_id = cost.item_id
    db.session.delete(cost)
    db.session.commit()
    flash('Cost deleted successfully!', 'success')
    return redirect(url_for('items.item_detail', item_id=item_id))

