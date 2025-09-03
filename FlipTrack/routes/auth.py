import secrets
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, PasswordResetToken, EmailChangeToken, Setting
from utils import send_email

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('auth/login.html')

@auth_bp.route('/login', methods=['POST'])
def login_post():
    identifier = request.form.get('identifier', '').strip()
    password = request.form.get('password', '').strip()
    remember = 'remember' in request.form

    if not identifier or not password:
        flash('Username or email and password are required.', 'error')
        return render_template('auth/login.html')

    user = User.query.filter(db.or_(User.username == identifier, User.email == identifier)).first()
    if not user or not user.check_password(password):
        flash('Invalid credentials.', 'error')
        return render_template('auth/login.html')

    login_user(user, remember=remember)
    flash(f'Welcome back, {user.username}!', 'success')
    return redirect(url_for('dashboard'))

@auth_bp.route('/logout')
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))

@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        if not email:
            flash('Email is required.', 'error')
            return render_template('auth/forgot_password.html')

        user = User.query.filter_by(email=email).first()
        if user:
            token = secrets.token_urlsafe(32)
            expires = datetime.utcnow() + timedelta(hours=1)
            prt = PasswordResetToken(user_id=user.id, token=token, expires_at=expires)
            db.session.add(prt)
            db.session.commit()

            reset_url = url_for('auth.reset_password', token=token, _external=True)
            if Setting.get('email_send_password_reset', 'on') == 'on':
                subject = Setting.get('email_template_password_reset_subject', 'Password Reset')
                body_tpl = Setting.get('email_template_password_reset_body', 'Click the link to reset your password: {link}')
                body = body_tpl.format(username=user.username, link=reset_url)
                send_email(user.email, subject, body, html_body=body)

        flash('If that email exists in our system, a reset link has been sent.', 'info')
        return redirect(url_for('auth.login'))
    return render_template('auth/forgot_password.html')

@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    prt = PasswordResetToken.query.filter_by(token=token).first()
    if not prt or prt.expires_at < datetime.utcnow():
        flash('Invalid or expired reset token.', 'error')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        password = request.form.get('password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()
        if not password or password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('auth/reset_password.html', token=token)

        user = User.query.get(prt.user_id)
        if user:
            user.set_password(password)
            db.session.delete(prt)
            db.session.commit()
            flash('Password updated successfully.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('auth/reset_password.html', token=token)

@auth_bp.route('/account')
@login_required
def account():
    return render_template('auth/account.html')

@auth_bp.route('/account', methods=['POST'])
@login_required
def account_post():
    action = request.form.get('action')
    user = current_user

    if action == 'change_username':
        new_username = request.form.get('new_username', '').strip()
        if not new_username:
            flash('Username cannot be empty.', 'error')
        elif User.query.filter_by(username=new_username).filter(User.id != user.id).first():
            flash('Username already exists.', 'error')
        else:
            user.username = new_username
            db.session.commit()
            flash('Username updated successfully.', 'success')

    elif action == 'change_email':
        new_email = request.form.get('new_email', '').strip()
        if not new_email:
            flash('Email cannot be empty.', 'error')
        elif User.query.filter_by(email=new_email).filter(User.id != user.id).first():
            flash('Email already exists.', 'error')
        else:
            token = secrets.token_urlsafe(32)
            expires = datetime.utcnow() + timedelta(hours=24)
            ect = EmailChangeToken(user_id=user.id, new_email=new_email, token=token, expires_at=expires)
            db.session.add(ect)
            db.session.commit()

            confirm_url = url_for('auth.confirm_email_change', token=token, _external=True)
            if Setting.get('email_send_email_change', 'on') == 'on':
                subject = Setting.get('email_template_email_change_subject', 'Confirm your new email')
                body_tpl = Setting.get('email_template_email_change_body', 'Hi {username}, confirm your new email {new_email} by visiting {link}')
                body = body_tpl.format(username=user.username, link=confirm_url, new_email=new_email)
                send_email(new_email, subject, body, html_body=body)

            flash('Confirmation email sent to the new address.', 'success')

    elif action == 'change_password':
        current_password = request.form.get('current_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        if not user.check_password(current_password):
            flash('Current password is incorrect.', 'error')
        elif not new_password:
            flash('New password cannot be empty.', 'error')
        elif new_password != confirm_password:
            flash('New passwords do not match.', 'error')
        else:
            user.set_password(new_password)
            db.session.commit()
            flash('Password updated successfully.', 'success')

    elif action == 'forget_device':
        session.pop('_remember', None)
        session.pop('_remember_seconds', None)
        response = redirect(url_for('auth.account'))
        response.delete_cookie('remember_token')
        flash('Device forgotten successfully.', 'success')
        return response

    return redirect(url_for('auth.account'))

@auth_bp.route('/confirm-email/<token>')
def confirm_email_change(token):
    ect = EmailChangeToken.query.filter_by(token=token).first()
    if not ect or ect.expires_at < datetime.utcnow():
        flash('Invalid or expired email confirmation link.', 'error')
        return redirect(url_for('auth.login'))

    user = User.query.get(ect.user_id)
    if user:
        user.email = ect.new_email
        db.session.delete(ect)
        db.session.commit()
        flash('Email updated successfully.', 'success')
    else:
        flash('User not found.', 'error')

    if current_user.is_authenticated and current_user.id == user.id:
        return redirect(url_for('auth.account'))
    return redirect(url_for('auth.login'))
