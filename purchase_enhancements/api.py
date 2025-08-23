import frappe
from frappe.utils import cint

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

        cache_key = f"item_history_{item_code}_{project}_{company}_{limit}"
        cached_result = frappe.cache().get_value(cache_key)

        if cached_result:
            return cached_result

        history = frappe.get_all(
            "Purchase Order Item",
            filters={
                "item_code": item_code,
                "project": project,
                "docstatus": 1,
                "company": company
            },
            fields=["parent", "creation", "qty", "rate", "amount", "supplier", "received_qty"],
            order_by="creation desc",
            limit=limit
        )

        for item in history:
            item['pending_qty'] = (item.get('qty', 0) - item.get('received_qty', 0))
            item['delivery_status'] = 'Completed' if item['pending_qty'] <= 0 else 'Pending'

        frappe.cache().set_value(cache_key, history, expires_in_sec=cache_duration)
        return history

    except Exception as e:
        frappe.log_error(f"Error fetching purchase history: {str(e)}", "Purchase History API")
        return {"error": str(e), "trace": traceback.format_exc()}

