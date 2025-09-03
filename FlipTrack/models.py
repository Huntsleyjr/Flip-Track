from app import db
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import relationship
import json

class Setting(db.Model):
    __tablename__ = 'settings'
    
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, key, default=None):
        setting = cls.query.filter_by(key=key).first()
        return setting.value if setting else default
    
    @classmethod
    def set(cls, key, value):
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = value
            setting.updated_at = datetime.utcnow()
        else:
            setting = cls(key=key, value=value)
            db.session.add(setting)
        db.session.commit()
        return setting

class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    remember_tokens = relationship("RememberToken", backref="user", cascade="all, delete-orphan")
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def get_id(self):
        return str(self.id)
    
    @property
    def is_authenticated(self):
        return True
    
    @property
    def is_active(self):
        return True
    
    @property
    def is_anonymous(self):
        return False

class RememberToken(db.Model):
    __tablename__ = 'remember_tokens'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(256), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(256), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)


class EmailChangeToken(db.Model):
    __tablename__ = 'email_change_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    new_email = db.Column(db.String(120), nullable=False)
    token = db.Column(db.String(256), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)

class Item(db.Model):
    __tablename__ = 'items'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    purchase_date = db.Column(db.Date, nullable=False)
    purchase_price = db.Column(db.Integer, nullable=False)  # Store in cents
    is_auction = db.Column(db.Boolean, default=False, nullable=False)
    auction_bid = db.Column(db.Integer)  # Store in cents
    auction_buyer_premium = db.Column(db.Float)
    auction_tax_rate = db.Column(db.Float)
    sale_date = db.Column(db.Date)
    sale_price = db.Column(db.Integer)  # Store in cents
    expected_sale_price = db.Column(db.Integer)  # Store in cents
    notes = db.Column(db.Text)
    category = db.Column(db.String(100))
    status = db.Column(
        db.Enum('active', 'listed', 'in_repair', 'sold', name='item_status'),
        default='active',
        nullable=False
    )
    thumbnail_id = db.Column(db.Integer, db.ForeignKey('images.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    images = relationship("Image", foreign_keys="Image.item_id", backref="item", cascade="all, delete-orphan")
    repairs = relationship("Repair", backref="item", cascade="all, delete-orphan")
    other_costs = relationship("OtherCost", backref="item", cascade="all, delete-orphan")
    thumbnail = relationship("Image", foreign_keys=[thumbnail_id])
    
    @property
    def total_costs(self):
        repair_costs = sum(repair.total_cost for repair in self.repairs)
        other_costs_total = sum(cost.amount for cost in self.other_costs)
        return self.purchase_price + repair_costs + other_costs_total
    
    @property
    def profit(self):
        if not self.sale_price:
            return None
        return self.sale_price - self.total_costs

    @property
    def roi(self):
        if not self.sale_price or self.total_costs == 0:
            return None
        return ((self.sale_price - self.total_costs) / self.total_costs) * 100

    @property
    def potential_profit(self):
        if self.expected_sale_price is None:
            return None
        return self.expected_sale_price - self.total_costs

    @property
    def potential_roi(self):
        if self.expected_sale_price is None or self.total_costs == 0:
            return None
        return ((self.expected_sale_price - self.total_costs) / self.total_costs) * 100
    
    @property
    def is_sold(self):
        return self.status == 'sold'

class Image(db.Model):
    __tablename__ = 'images'
    
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'))
    repair_id = db.Column(db.Integer, db.ForeignKey('repairs.id'))
    filename = db.Column(db.String(200), nullable=False)
    original_filename = db.Column(db.String(200))
    file_size = db.Column(db.Integer)
    content_type = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Repair(db.Model):
    __tablename__ = 'repairs'

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    notes = db.Column(db.Text, nullable=False)
    expected_cost = db.Column(db.Integer)  # Store in cents
    final_cost = db.Column(db.Integer)  # Store in cents
    status = db.Column(db.String(50), default='pending')  # pending, in_progress, completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    images = relationship("Image", backref="repair", cascade="all, delete-orphan")
    supplies = relationship("SupplyUsage", backref="repair", cascade="all, delete-orphan")

    @property
    def total_cost(self):
        base = self.final_cost if self.final_cost is not None else self.expected_cost or 0
        supply_total = sum(usage.cost_cents for usage in self.supplies)
        return base + supply_total

class OtherCost(db.Model):
    __tablename__ = 'other_costs'

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('items.id'), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Integer, nullable=False)  # Store in cents
    date = db.Column(db.Date, default=datetime.utcnow().date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Asset(db.Model):
    __tablename__ = 'assets'

    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(200), nullable=False)
    amount = db.Column(db.Integer, nullable=False)  # Store in cents
    category = db.Column(db.String(100))
    date = db.Column(db.Date, default=datetime.utcnow().date)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Supply(db.Model):

    __tablename__ = 'supplies'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Float, nullable=False, default=0.0)
    unit = db.Column(db.String(50))
    cost_cents = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    usages = relationship("SupplyUsage", backref="supply", cascade="all, delete-orphan")

    @property
    def cost_per_unit(self):
        return self.cost_cents / self.quantity if self.quantity else 0

    def apply_usage(self, qty):
        if qty > self.quantity:
            raise ValueError("Insufficient supply quantity")
        cost = int(round(self.cost_per_unit * qty))
        self.quantity -= qty
        self.cost_cents -= cost
        return cost


class SupplyUsage(db.Model):
    __tablename__ = 'supply_usages'

    id = db.Column(db.Integer, primary_key=True)
    repair_id = db.Column(db.Integer, db.ForeignKey('repairs.id'), nullable=False)
    supply_id = db.Column(db.Integer, db.ForeignKey('supplies.id'), nullable=False)
    quantity_used = db.Column(db.Float, nullable=False)
    cost_cents = db.Column(db.Integer, nullable=False)


class Catalog(db.Model):
    __tablename__ = 'catalogs'
    
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), nullable=False)
    title = db.Column(db.String(200))
    auction_date = db.Column(db.Date)
    buyer_premium = db.Column(db.Float)
    total_lots = db.Column(db.Integer, default=0)
    last_scraped = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    lots = relationship("Lot", backref="catalog", cascade="all, delete-orphan")

class Lot(db.Model):
    __tablename__ = 'lots'
    
    id = db.Column(db.Integer, primary_key=True)
    catalog_id = db.Column(db.Integer, db.ForeignKey('catalogs.id'), nullable=False)
    lot_number = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(500))
    description = db.Column(db.Text)
    current_bid = db.Column(db.Integer, default=0)  # Store in cents
    buyer_premium = db.Column(db.Float)  # Override, use catalog default if null
    tax_rate = db.Column(db.Float)  # Override, use catalog default if null
    shipping_cost = db.Column(db.Integer, default=0)  # Store in cents
    notes = db.Column(db.Text)
    images_json = db.Column(db.Text)  # JSON array of image URLs
    url = db.Column(db.String(500))
    etag = db.Column(db.String(200))
    last_modified = db.Column(db.String(200))
    last_checked = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    @property
    def images(self):
        if self.images_json:
            try:
                return json.loads(self.images_json)
            except (json.JSONDecodeError, TypeError):
                return []
        return []
    
    @images.setter
    def images(self, value):
        self.images_json = json.dumps(value) if value else None
    
    @property
    def effective_buyer_premium(self):
        if self.buyer_premium is not None:
            return self.buyer_premium
        if self.catalog and self.catalog.buyer_premium is not None:
            return self.catalog.buyer_premium
        return float(Setting.get('default_buyer_premium', '10.0'))
    
    @property
    def effective_tax_rate(self):
        if self.tax_rate is not None:
            return self.tax_rate
        return float(Setting.get('default_tax_rate', '8.5'))
    
    @property
    def total_cost(self):
        """Calculate total cost including bid, buyer's premium, tax, and shipping"""
        subtotal = self.current_bid
        premium = int(subtotal * (self.effective_buyer_premium / 100))
        tax = int((subtotal + premium) * (self.effective_tax_rate / 100))
        return subtotal + premium + tax + self.shipping_cost