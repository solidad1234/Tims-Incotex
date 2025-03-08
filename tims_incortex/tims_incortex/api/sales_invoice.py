import re
from base64 import b64encode
from datetime import timedelta
from io import BytesIO
from typing import Literal

import qrcode
import requests
import json
import frappe
from frappe.integrations.utils import create_request_log
from frappe.model.document import Document
from frappe.utils import get_formatted_email
from frappe.utils.user import get_users_with_role
from tims_incortex.tims_incortex.tims_incortex.utils import get_tims_settings

class TimsInvoice:
    def __init__(self, invoice_name):
        """Initialize with Sales Invoice document."""
        self.invoice = frappe.get_doc("Sales Invoice", invoice_name)
        self.settings = get_tims_settings()

    def sign_invoice(self):
        """Send invoice data to TIMS API and update response."""
        if self.invoice.custom_cu_invoice_number:
            frappe.msgprint("Invoice already signed.", alert=True)
            return

        # Determine the correct endpoint
        if self.invoice.is_return:
            endpoint = "sign?credit"  # Credit Note
        elif self.invoice.debit_note:
            endpoint = "sign?debit"  # Debit Note
        else:
            endpoint = "sign?invoice"  # Standard Invoice
        
        url = f"{self.settings['api_url']}/{endpoint}"
        headers = {"Content-Type": "application/json"}
        payload = self._prepare_payload()

        # Log the API request
        integration_request = frappe.get_doc({
            "doctype": "Integration Request",
            "integration_type": "Remote",
            "status": "Queued",
            "reference_doctype": "Sales Invoice",
            "reference_docname": self.invoice.name,
            "url": url,
            "data": json.dumps(payload),
        })
        integration_request.insert(ignore_permissions=True)

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response_data = response.json()

            # Update integration request with response
            integration_request.status = "Completed" if response.status_code == 200 else "Failed"
            integration_request.response = json.dumps(response_data)
            integration_request.save()
            frappe.db.commit()

            if response.status_code == 200 and response_data.get("success"):
                self._update_invoice(response_data)
            else:
                self._handle_failure(response_data)

        except requests.exceptions.RequestException as e:
            frappe.msgprint(f"API request failed: {e}", alert=True)
            self._log_error(str(e))
            
            integration_request.status = "Failed"
            integration_request.response = str(e)
            integration_request.save()
            frappe.db.commit()


    def _prepare_payload(self):
        """Prepare invoice data for TIMS API."""
        return {
            "invoice_date": self.invoice.posting_date.strftime("%d_%m_%Y"),
            "invoice_number": self.invoice.name,
            "invoice_pin": self.settings["invoice_pin"],
            "customer_pin": self.invoice.tax_id or "",
            "customer_exid": self.invoice.customer,
            "grand_total": str(self.invoice.grand_total),
            "net_subtotal": str(self.invoice.net_total),
            "tax_total": str(self.invoice.total_taxes_and_charges),
            "net_discount_total": str(self.invoice.discount_amount or "0.00"),
            "sel_currency": self.invoice.currency,
            "rel_doc_number": self.invoice.custom_rel_doc_number or "",
            "items_list": [
                f"{i.item_name} {i.qty:.2f} {i.rate:.2f} {i.amount:.2f}" 
                for i in self.invoice.items
            ]
        }

    def _update_invoice(self, response_data):
        """Update invoice with TIMS API response using set_value."""
        frappe.db.set_value("Sales Invoice", self.invoice.name, {
            "custom_cu_serial_number": response_data.get("cu_serial_number"),
            "custom_cu_invoice_number": response_data.get("cu_invoice_number"),
            "custom_verify_url": response_data.get("verify_url"),
            "custom_signing_status": "Signed",
            "custom_tims_description": response_data.get("message", "Invoice signed successfully.")
        })
        # frappe.db.commit()


    def _update_invoice(self, response_data):
        """Update invoice with TIMS API response using set_value."""
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_cu_serial_number", response_data.get("cu_serial_number"))
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_cu_invoice_number", response_data.get("cu_invoice_number"))
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_verify_url", response_data.get("verify_url"))
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_signing_status", "Signed")
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_tims_description", response_data.get("message", "Invoice signed successfully."))
        # frappe.db.commit()


    def _log_error(self, message):
        """Log API errors."""
        frappe.log_error(f"TIMS API Error: {message}", "TimsInvoice Error")
        
        frappe.db.set_value("Sales Invoice", self.invoice.name, {
            "custom_signing_status": "Failed",
            "custom_tims_description": message
        })
        # frappe.db.commit()


@frappe.whitelist()
def sign_invoice(invoice_name):
    """Public function to trigger invoice signing."""
    invoice = TimsInvoice(invoice_name)
    invoice.sign_invoice()

@frappe.whitelist()
def retry_pending_invoices():
    """Retry signing invoices that failed."""
    pending_invoices = frappe.get_all(
        "Sales Invoice",
        filters={"custom_signing_status": "Failed"},
        pluck="name"
    )

    for invoice_name in pending_invoices:
        invoice = TimsInvoice(invoice_name)
        invoice.sign_invoice()

def on_submit(doc, method):
    """Trigger invoice signing on submission."""
    invoice = TimsInvoice(doc.name)
    invoice.sign_invoice()
    
def get_qr_code(data: str) -> str:
    """Generate QR Code data

    Args:
        data (str): The information used to generate the QR Code

    Returns:
        str: The QR Code.
    """
    qr_code_bytes = get_qr_code_bytes(data, format="PNG")
    base_64_string = bytes_to_base64_string(qr_code_bytes)

    return add_file_info(base_64_string)


def add_file_info(data: str) -> str:
    """Add info about the file type and encoding.

    This is required so the browser can make sense of the data."""
    return f"data:image/png;base64, {data}"

def get_qr_code_bytes(data: bytes | str, format: str = "PNG") -> bytes:
    """Create a QR code and return the bytes."""
    img = qrcode.make(data)

    buffered = BytesIO()
    img.save(buffered, format=format)

    return buffered.getvalue()


def bytes_to_base64_string(data: bytes) -> str:
    """Convert bytes to a base64 encoded string."""
    return b64encode(data).decode("utf-8")



def format_time_for_invoice(time: str) -> str:
    """Format time to ensure leading zero for single-digit hours."""
    hour, minute, second = time.split(":")
    return f"{int(hour):02d}:{minute}:{second}"