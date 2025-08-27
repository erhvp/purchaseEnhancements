import frappe
from frappe import _
from frappe.utils import nowdate, add_days, flt

class ReminderManager:
    """
    Centralized service for managing the Delivery Reminder lifecycle.
    """

    def __init__(self):
        self.settings = self._load_settings()

    # --- Public Entrypoints (Called from hooks.py) ---
    @staticmethod
    def update_reminders_for_receipt(doc, method=None):
        if not ReminderManager().settings.get("enable_auto_reminders"): return
        ReminderManager()._process_document(doc)

    @staticmethod
    def handle_po_cancellation(doc, method=None):
        ReminderManager()._close_reminders_for_po(doc)

    @staticmethod
    def clear_item_history_cache(doc, method=None):
        ReminderManager()._clear_cache_for_po(doc)

    @staticmethod
    def escalate_overdue_reminders():
        if not ReminderManager().settings.get("auto_escalate_enabled"): return
        ReminderManager()._escalate_overdue()
    
    @staticmethod
    def send_daily_reminder_digest():
        if not ReminderManager().settings.get("send_daily_digest"): return
        ReminderManager()._send_daily_digest()

    @staticmethod
    def cleanup_closed_reminders():
        if not ReminderManager().settings.get("auto_cleanup_enabled"): return
        ReminderManager()._cleanup_closed()

    # --- Core Processing Logic ---
    def _process_document(self, doc):
        notifications_to_send = []
        for item in doc.items:
            if not item.purchase_order_item: continue

            ordered_qty = frappe.db.get_value("Purchase Order Item", item.purchase_order_item, "qty") or 0
            total_received_qty = self._get_total_received_qty(item.purchase_order_item)
            pending_qty = flt(ordered_qty) - flt(total_received_qty)

            existing_reminder = self._find_existing_reminder(item.purchase_order_item)

            if pending_qty <= 0:
                if existing_reminder:
                    self._close_reminder(existing_reminder, f"Fulfilled by {doc.doctype} {doc.name}")
            else:
                if existing_reminder:
                    self._update_reminder(existing_reminder, pending_qty, doc)
                else:
                    new_reminder = self._create_reminder(item, pending_qty, doc)
                    if new_reminder:
                        notifications_to_send.append(new_reminder)
        
        if notifications_to_send:
            self._send_consolidated_notifications(notifications_to_send, "New Delivery Reminders Created")

    # --- CRUD Helpers ---
    def _create_reminder(self, item_row, pending_qty, triggering_doc):
        po_doc = frappe.get_doc("Purchase Order", item_row.purchase_order)
        reminder = frappe.new_doc("Delivery Reminder")
        reminder.update({
            "purchase_order": item_row.purchase_order,
            "purchase_order_item": item_row.purchase_order_item,
            "supplier": po_doc.supplier,
            "item_code": item_row.item_code,
            "pending_qty": pending_qty,
            "status": "Open",
            "auto_created": 1,
            "triggering_receipt": triggering_doc.name,
            "expected_delivery_date": po_doc.schedule_date or add_days(nowdate(), 7),
            "priority": self._calculate_priority(pending_qty, item_row.purchase_order_item),
            "reminder_level": "First",
            "next_follow_up_date": add_days(nowdate(), self.settings.get("default_follow_up_days", 3))
        })
        reminder.insert(ignore_permissions=True)
        return reminder

    def _update_reminder(self, reminder_name, pending_qty, triggering_doc):
        frappe.db.set_value("Delivery Reminder", reminder_name, "pending_qty", pending_qty)
        doc = frappe.get_doc("Delivery Reminder", reminder_name)
        doc.add_comment("Updated", text=f"Pending qty set to {pending_qty} by {triggering_doc.doctype} {triggering_doc.name}")

    def _close_reminder(self, reminder_name, reason):
        frappe.db.set_value("Delivery Reminder", reminder_name, "status", "Closed")
        doc = frappe.get_doc("Delivery Reminder", reminder_name)
        doc.add_comment("Auto-Closed", text=reason)

    # --- Scheduled Task Implementations ---

    def _escalate_overdue(self):
        """
        Escalates reminders whose next_follow_up_date is in the past.
        Increments reminder level and updates priority based on settings.
        """
        overdue_reminders = frappe.get_all(
            "Delivery Reminder",
            filters={"status": "Open", "next_follow_up_date": ["<", nowdate()]},
            fields=["name", "reminder_level"]
        )
        
        for r_data in overdue_reminders:
            reminder = frappe.get_doc("Delivery Reminder", r_data.name)
            new_level = "Final" # Default to final level
            if reminder.reminder_level == "First":
                new_level = "Second"
            
            reminder.reminder_level = new_level
            reminder.priority = "Critical" # Always escalate priority
            reminder.next_follow_up_date = add_days(nowdate(), self.settings.get("default_follow_up_days", 3))
            reminder.save(ignore_permissions=True)
            reminder.add_comment("Escalated", text=f"Reminder level escalated to {new_level}.")
        
        if overdue_reminders:
            frappe.log_error(f"Escalated {len(overdue_reminders)} reminders.", "ReminderManager")

    def _send_daily_digest(self):
        """
        Sends a daily summary of open/urgent reminders to configured recipients.
        """
        recipients = [email.strip() for email in self.settings.get("digest_recipients", "").split(',') if email.strip()]
        if not recipients:
            return

        open_count = frappe.db.count("Delivery Reminder", {"status": "Open"})
        critical_count = frappe.db.count("Delivery Reminder", {"status": "Open", "priority": "Critical"})

        if open_count == 0:
            return # Don't send empty digests

        subject = _("Daily Delivery Reminder Digest: {0} Open, {1} Critical").format(open_count, critical_count)
        
        # Build an HTML body with a link to the list
        reminders_link = frappe.utils.get_url("/app/delivery-reminder")
        message = f"""
            <h3>Daily Purchase Delivery Summary</h3>
            <p>Here is a summary of the current delivery statuses:</p>
            <ul>
                <li><strong>Total Open Reminders:</strong> {open_count}</li>
                <li><strong>Critical Priority Reminders:</strong> {critical_count}</li>
            </ul>
            <p><a href="{reminders_link}">Click here to view all reminders</a></p>
        """
        
        frappe.sendmail(recipients=recipients, subject=subject, message=message)

    def _cleanup_closed(self):
        """
        Archives or deletes closed reminders older than the configured retention period.
        """
        cleanup_days = self.settings.get("cleanup_after_days", 180)
        cutoff_date = add_days(nowdate(), -cleanup_days)

        old_reminders = frappe.get_all(
            "Delivery Reminder",
            filters={"status": "Closed", "modified": ["<", cutoff_date]}
        )
        
        for r in old_reminders:
            if self.settings.get("archive_closed_reminders"):
                # Non-destructive: Set an 'archived' flag (assuming you add this custom field)
                # frappe.db.set_value("Delivery Reminder", r.name, "archived", 1)
                pass # Placeholder for archival logic
            else:
                # Destructive: Delete the record
                frappe.delete_doc("Delivery Reminder", r.name, ignore_permissions=True)
        
        if old_reminders:
            frappe.log_error(f"Cleaned up {len(old_reminders)} old reminders.", "ReminderManager")

    def _send_consolidated_notifications(self, reminders, subject):
        """
        Groups reminders by recipient (PO owner) and sends a single notification.
        """
        reminders_by_owner = {}
        for reminder in reminders:
            owner = frappe.db.get_value("Purchase Order", reminder.purchase_order, "owner")
            if owner not in reminders_by_owner:
                reminders_by_owner[owner] = []
            reminders_by_owner[owner].append(reminder)

        for owner, reminder_list in reminders_by_owner.items():
            content = "<h3>New Delivery Reminders Created</h3><ul>"
            for r in reminder_list:
                content += f"<li><b>{r.item_code}</b>: {r.pending_qty} units pending for PO <a href='/app/purchase-order/{r.purchase_order}'>{r.purchase_order}</a></li>"
            content += "</ul>"

            frappe.new_doc("Notification Log", {
                "subject": subject,
                "document_type": "Delivery Reminder",
                "document_name": reminder_list[0].name, # Link to the first one
                "for_user": owner,
                "email_content": content
            }).insert(ignore_permissions=True)

    # --- Other Helpers ---
    def _load_settings(self):
        return frappe.get_cached_doc("Purchase Enhancement Settings").as_dict()

    def _get_total_received_qty(self, po_item_name):
        qty = frappe.db.sql("SELECT SUM(qty) FROM `tabPurchase Receipt Item` WHERE purchase_order_item = %s AND docstatus = 1", (po_item_name,))
        return flt(qty[0][0]) if qty and qty[0][0] else 0

    def _find_existing_reminder(self, po_item_name):
        return frappe.db.get_value("Delivery Reminder", {"purchase_order_item": po_item_name, "status": "Open"})
        
    def _calculate_priority(self, pending_qty, po_item_name):
        ordered_qty = frappe.db.get_value("Purchase Order Item", po_item_name, "qty") or 1
        percent_pending = (flt(pending_qty) / flt(ordered_qty)) * 100
        if percent_pending >= self.settings.get("critical_priority_percentage", 80): return "Critical"
        if percent_pending >= self.settings.get("high_priority_percentage", 50): return "High"
        if percent_pending >= self.settings.get("medium_priority_percentage", 25): return "Medium"
        return "Low"

    def _close_reminders_for_po(self, po_doc):
        reminders = frappe.get_all("Delivery Reminder", filters={"purchase_order": po_doc.name, "status": "Open"})
        for r in reminders:
            self._close_reminder(r.name, f"PO {po_doc.name} was cancelled.")

    def _clear_cache_for_po(self, po_doc):
        for item in po_doc.items:
            cache_key = f"item_history_{item.item_code}_{item.project}_{po_doc.company}_{self.settings.get('max_history_items', 5)}"
            frappe.cache().delete_value(cache_key)
