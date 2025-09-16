from .connection import init_db, close_db, db_pool
from .models import init_tables
from .queries import *

# Реэкспортируем все функции для удобного импорта
__all__ = [
    'init_db', 'close_db', 'db_pool', 'init_tables',
    'get_user', 'update_user', 'add_transaction', 'add_purchase',
    'add_sold_product', 'get_pending_transactions', 'update_transaction_status',
    'update_transaction_status_by_uuid', 'get_last_order', 'get_user_orders',
    'is_banned', 'has_active_invoice', 'get_cities_cache', 'get_districts_cache',
    'get_products_cache', 'get_delivery_types_cache', 'get_categories_cache',
    'get_subcategories_cache', 'get_texts_cache', 'get_bot_settings_cache',
    'get_sold_products', 'get_subcategory_quantity', 'reserve_subcategory',
    'release_subcategory', 'get_product_quantity', 'reserve_product',
    'release_product', 'get_product_by_name_city', 'get_product_by_id',
    'get_purchase_with_product', 'update_bot_setting', 'get_all_bot_settings',
    'increment_api_request', 'get_api_limits', 'reset_api_limits',
    'add_generated_address', 'update_address_balance', 'get_generated_addresses',
    'get_deposit_address', 'create_deposit', 'update_deposit_confirmations',
    'get_pending_deposits', 'process_confirmed_deposit', 'update_api_limits',
    'reset_daily_limits', 'get_api_config', 'update_api_config',
    'is_district_available', 'is_delivery_type_available',
    'get_subcategories_by_category', 'add_subcategory', 'update_subcategory',
    'delete_subcategory', 'add_user_referral', 'generate_referral_code',
    'bulk_update_users', 'safe_query', 'get_api_usage_stats',
    'get_user_extended_stats', 'get_popular_products', 'get_daily_stats',
    'get_sales_trends', 'get_geographic_sales', 'get_invoice_stats',
    'search_users', 'get_user_transactions', 'get_transaction_details',
    'bulk_update_settings', 'cleanup_old_data', 'create_backup',
    'restore_backup', 'get_system_info', 'get_top_users_by_purchases',
    'get_top_users_by_spending', 'get_city_stats', 'get_category_stats',
    'get_subcategory_stats', 'get_delivery_stats', 'get_daily_revenue',
    'get_average_order_value', 'get_repeat_customers', 'get_time_metrics',
    'check_database_health', 'optimize_database', 'get_database_size',
    'get_table_info', 'export_data', 'import_data', 'get_error_logs',
    'clear_logs', 'load_cache', 'refresh_cache', 'get_text', 'get_bot_setting',
    'db_execute', 'db_connection'
]
