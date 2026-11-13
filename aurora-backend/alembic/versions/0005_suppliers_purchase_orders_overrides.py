"""suppliers, purchase orders, forecast overrides

Additive only: three new tables, purchase order lines, and a nullable products.supplier_id.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27 05:37:36.610470
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('suppliers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('contact_name', sa.String(length=200), nullable=True),
    sa.Column('email', sa.String(length=320), nullable=True),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('lead_time_days', sa.Integer(), nullable=True),
    sa.Column('min_order_value', sa.Float(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lead_time_days IS NULL OR (lead_time_days >= 1 AND lead_time_days <= 365)', name='ck_suppliers_lead'),
    sa.CheckConstraint('min_order_value IS NULL OR min_order_value >= 0', name='ck_suppliers_min_value'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_suppliers_org_name', 'suppliers', ['org_id', sa.literal_column('lower(name)')], unique=True)
    op.create_table('purchase_orders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('supplier_id', sa.Uuid(), nullable=True),
    sa.Column('supplier_name', sa.String(length=200), nullable=True),
    sa.Column('expected_date', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('approved_by', sa.Uuid(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('draft', 'approved', 'sent', 'partially_received', 'received', 'cancelled')", name='ck_purchase_orders_status'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['supplier_id'], ['suppliers.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'number', name='uq_purchase_orders_org_number')
    )
    op.create_index('ix_purchase_orders_org_status', 'purchase_orders', ['org_id', 'status'], unique=False)
    op.create_table('forecast_overrides',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('set', 'scale')", name='ck_overrides_kind'),
    sa.CheckConstraint('end_date >= start_date', name='ck_overrides_dates'),
    sa.CheckConstraint('value >= 0', name='ck_overrides_value'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_overrides_org_product', 'forecast_overrides', ['org_id', 'product_id'], unique=False)
    op.create_table('purchase_order_lines',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('org_id', sa.Uuid(), nullable=False),
    sa.Column('purchase_order_id', sa.Uuid(), nullable=False),
    sa.Column('product_id', sa.Uuid(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('unit_cost', sa.Float(), nullable=True),
    sa.Column('received_qty', sa.Integer(), nullable=False),
    sa.Column('suggested_qty', sa.Integer(), nullable=True),
    sa.Column('suggestion', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint('quantity > 0', name='ck_po_lines_qty_pos'),
    sa.CheckConstraint('received_qty >= 0 AND received_qty <= quantity', name='ck_po_lines_received'),
    sa.CheckConstraint('unit_cost IS NULL OR unit_cost >= 0', name='ck_po_lines_cost'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('purchase_order_id', 'product_id', name='uq_po_lines_order_product')
    )
    op.create_index(op.f('ix_purchase_order_lines_product_id'), 'purchase_order_lines', ['product_id'], unique=False)
    op.create_index(op.f('ix_purchase_order_lines_purchase_order_id'), 'purchase_order_lines', ['purchase_order_id'], unique=False)
    op.add_column('products', sa.Column('supplier_id', sa.Uuid(), nullable=True))
    op.create_index(op.f('ix_products_supplier_id'), 'products', ['supplier_id'], unique=False)
    op.create_foreign_key('products_supplier_id_fkey', 'products', 'suppliers', ['supplier_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('products_supplier_id_fkey', 'products', type_='foreignkey')
    op.drop_index(op.f('ix_products_supplier_id'), table_name='products')
    op.drop_column('products', 'supplier_id')
    op.drop_index(op.f('ix_purchase_order_lines_purchase_order_id'), table_name='purchase_order_lines')
    op.drop_index(op.f('ix_purchase_order_lines_product_id'), table_name='purchase_order_lines')
    op.drop_table('purchase_order_lines')
    op.drop_index('ix_overrides_org_product', table_name='forecast_overrides')
    op.drop_table('forecast_overrides')
    op.drop_index('ix_purchase_orders_org_status', table_name='purchase_orders')
    op.drop_table('purchase_orders')
    op.drop_index('uq_suppliers_org_name', table_name='suppliers')
    op.drop_table('suppliers')
