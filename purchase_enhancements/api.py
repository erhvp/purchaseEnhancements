import frappe
from frappe.utils import cint
import traceback  # Added for error logging

@frappe.whitelist()
def get_item_project_history(item_code, project, company, limit=5):
    """
    Fetches purchase history with enhanced error handling and settings integration.
    """
    try:
        if not all([item_code, project, company]):
            return []

        settings = frappe.get_cached_doc("Purchase Enhancement Settings")
        if not settings.enable_purchase_history:
            return []

        cache_duration = settings.history_cache_duration or 600
        limit = cint(limit) or settings.max_history_items or 5

        cache_key = f"item_history_{item_code}_{project}_{company}_{supplier}_{limit}"
        cached_result = frappe.cache().get_value(cache_key)

        if cached_result:
            return cached_result

        history = frappe.db.sql("""
            SELECT 
                po.name AS purchase_order,
                po.transaction_date,
                po.supplier,
                poi.item_code,
                poi.project,
                poi.qty,
                poi.rate,
                poi.amount,
                poi.received_qty
            FROM 
                `tabPurchase Order` po
            INNER JOIN 
                `tabPurchase Order Item` poi ON po.name = poi.parent
            WHERE 
                poi.item_code = %s AND
                poi.project = %s AND
                po.company = %s
            ORDER BY 
                po.transaction_date DESC
        """, (item_code, project, company), as_dict=True)

        for item in history:
            item['pending_qty'] = (item.get('qty', 0) - item.get('received_qty', 0))
            item['delivery_status'] = 'Completed' if item['pending_qty'] <= 0 else 'Pending'

        frappe.cache().set_value(cache_key, history, expires_in_sec=cache_duration)
        return history

    except Exception as e:
        frappe.log_error(
            message=traceback.format_exc(),
            title="get_item_project_history - Full Traceback"
        )
        return {"error": str(e), "trace": traceback.format_exc()}
