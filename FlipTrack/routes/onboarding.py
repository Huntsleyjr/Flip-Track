from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from models import db, Setting, User
from utils import allowed_file, save_uploaded_image

onboarding_bp = Blueprint('onboarding', __name__)


@onboarding_bp.route('/onboarding/step1')
def onboarding_step1():
    if Setting.get('app_initialized'):
        return redirect(url_for('dashboard'))
    return render_template('onboarding/step1.html')


@onboarding_bp.route('/onboarding/step1', methods=['POST'])
def onboarding_step1_post():
    company_name = request.form.get('company_name', '').strip()
    if not company_name:
        flash('Company name is required.', 'error')
        return render_template('onboarding/step1.html')

    Setting.set('company_name', company_name)

    if 'logo' in request.files:
        file = request.files['logo']
        if file and file.filename and allowed_file(file.filename):
            filename = save_uploaded_image(file, current_app.config['PUBLIC_FOLDER'])
            if filename:
                Setting.set('company_logo', filename)

    return redirect(url_for('onboarding.onboarding_step2'))


@onboarding_bp.route('/onboarding/step2')
def onboarding_step2():
    if Setting.get('app_initialized'):
        return redirect(url_for('dashboard'))
    return render_template('onboarding/step2.html')


@onboarding_bp.route('/onboarding/step2', methods=['POST'])
def onboarding_step2_post():
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()

    if not username or not email or not password:
        flash('Username, email and password are required.', 'error')
        return render_template('onboarding/step2.html')

    if password != confirm_password:
        flash('Passwords do not match.', 'error')
        return render_template('onboarding/step2.html')

    if User.query.filter_by(username=username).first() or User.query.filter_by(email=email).first():
        flash('Username or email already exists.', 'error')
        return render_template('onboarding/step2.html')

    admin_user = User(username=username, email=email, is_admin=True)
    admin_user.set_password(password)
    db.session.add(admin_user)

    additional_users = []
    i = 1
    while f'additional_username_{i}' in request.form:
        add_username = request.form.get(f'additional_username_{i}', '').strip()
        add_email = request.form.get(f'additional_email_{i}', '').strip()
        add_password = request.form.get(f'additional_password_{i}', '').strip()
        add_is_admin = f'additional_is_admin_{i}' in request.form

        if add_username and add_email and add_password:
            if User.query.filter(db.or_(User.username == add_username, User.email == add_email)).first():
                flash(f'Username or email "{add_username}" already exists.', 'error')
                return render_template('onboarding/step2.html')

            user = User(username=add_username, email=add_email, is_admin=add_is_admin)
            user.set_password(add_password)
            additional_users.append(user)
        i += 1

    for user in additional_users:
        db.session.add(user)

    db.session.commit()
    return redirect(url_for('onboarding.onboarding_step3'))


@onboarding_bp.route('/onboarding/step3')
def onboarding_step3():
    if Setting.get('app_initialized'):
        return redirect(url_for('dashboard'))
    return render_template('onboarding/step3.html')


@onboarding_bp.route('/onboarding/step3', methods=['POST'])
def onboarding_step3_post():
    Setting.set('default_buyer_premium', request.form.get('buyer_premium', '10.0'))
    Setting.set('default_tax_rate', request.form.get('tax_rate', '8.5'))
    Setting.set('min_refresh_interval', request.form.get('refresh_interval', '30'))
    Setting.set('watchlist_enabled', 'on' if request.form.get('enable_watchlist') else 'off')

    Setting.set('app_initialized', 'true')

    flash('Setup completed successfully!', 'success')
    return redirect(url_for('auth.login'))
