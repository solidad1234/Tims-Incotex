import re
from base64 import b64encode
from io import BytesIO

import qrcode
import requests
import json
import frappe
from frappe.integrations.utils import create_request_log
from tims_incortex.tims_incortex.utils import get_tims_settings
from frappe.integrations.utils import create_request_log

class TimsInvoice:
    def __init__(self, invoice_name, company):
        """Initialize with Sales Invoice document."""
        self.invoice = frappe.get_doc("Sales Invoice", invoice_name)
        self.settings = get_tims_settings(company)

    def sign_invoice(self):
        """Send invoice data to TIMS API and update response."""
        if self.invoice.etr_invoice_number:
            frappe.msgprint("Invoice already signed.", alert=True)
            return

        endpoint = get_endpoint(self.invoice)
        
        url = f"{self.settings['api_url']}api/{endpoint}"
        headers = {"Content-Type": "application/json"}
        payload = self._prepare_payload()

        integration_request = create_request_log(
            data=payload,
            is_remote_request=True,
            service_name="TIMS Incortex",
            request_headers=headers,
            url=url,
            reference_docname=self.invoice.name,
            reference_doctype="Sales Invoice",
        )
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response_data = response.json()

            integration_request.handle_success(json.dumps(response_data)) if response.status_code == 200 else integration_request.handle_failure(response_data)

            if response.status_code == 200 and response_data.get("success"):
                self._update_invoice(response_data)
            else:
                self._handle_failure(response_data)

        except requests.exceptions.RequestException as e:
            frappe.msgprint(f"API request failed: {e}", alert=True)
            self._log_error(str(e))
            integration_request.handler_failure(str(e))
   

    def _prepare_payload(self):
        rel_doc_number = self.invoice.custom_relevant_invoice_number if self.invoice.is_return else ""
        hs_code = tax_amount(self.invoice)
        """Prepare invoice data for TIMS API."""
        return {
            "invoice_date": self.invoice.posting_date.strftime("%d-%m-%Y"),
            "invoice_number": format_invoice_number(self.invoice.name),
            "invoice_pin": self.settings["company_pin"],
            "customer_pin": self.invoice.tax_id or "",
            "customer_exid": "",
            "grand_total": abs(self.invoice.grand_total),
            "net_subtotal": abs(self.invoice.net_total),
            "tax_total": abs(self.invoice.total_taxes_and_charges),
            "net_discount_total": abs(self.invoice.discount_amount or 0.00),
            "sel_currency": currency_code(self.invoice.currency),
            "rel_doc_number": rel_doc_number,
            "items_list": [
                f"{hs_code if self.invoice.total_taxes_and_charges == 0 else ''} "
                f"{re.sub(r'[^a-zA-Z0-9]', '', i.item_code)} {abs(i.qty):.2f} {abs(i.rate):.2f} {abs(i.amount):.2f}"
                for i in self.invoice.items
            ]
        }

    def _update_invoice(self, response_data):
        """Update invoice with TIMS API response using set_value."""
        frappe.db.set_value("Sales Invoice", self.invoice.name, {
            "custom_cu_serial_number": response_data.get("cu_serial_number"),
            "etr_invoice_number": response_data.get("etr_invoice_number"),
            "custom_verify_url": response_data.get("verify_url"),
            "custom_signing_status": "Signed",
            "custom_tims_description": response_data.get("message", "Invoice signed successfully.")
        })
        # frappe.db.commit()

    def handle_failure(self, response_data):
        """Handle failed API response."""
        frappe.msgprint(f"Failed to sign invoice: {response_data.get('message')}", alert=True)
        self._log_error(response_data.get("message"))

        frappe.db.set_value("Sales Invoice", self.invoice.name, {
            "custom_signing_status": "Failed",
            "custom_tims_description": response_data.get("message")
        })
        # frappe.db.commit()

    def _update_invoice(self, response_data):
        """Update invoice with TIMS API response using set_value."""
        frappe.db.set_value("Sales Invoice", self.invoice.name, "etr_serial_number", response_data.get("cu_serial_number"))
        frappe.db.set_value("Sales Invoice", self.invoice.name, "etr_invoice_number", response_data.get("cu_invoice_number"))
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_qr_code", response_data.get("verify_url"))
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_signing_status", "Signed")
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_tims_response_description", response_data.get("message", "Invoice signed successfully."))
        frappe.db.set_value("Sales Invoice", self.invoice.name, "custom_qr_image", get_qr_code(response_data.get("verify_url")))
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
def sign_single_invoice(invoice_name, company):
    """Public function to trigger invoice signing."""
    if is_active(company):
        invoice = TimsInvoice(invoice_name, company)
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
        company = frappe.get_value("Sales Invoice", invoice_name, "company")
        if is_active(company):
            invoice = TimsInvoice(invoice_name)
            invoice.sign_invoice()

def on_submit(doc, method):
    """Trigger invoice signing on submission."""
    if is_active(doc.company):
        invoice = TimsInvoice(doc.name, doc.company)
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

@frappe.whitelist()
def get_invoice(invoice, company):
    settings = get_tims_settings(company)
    if not settings:
        frappe.throw("TIMS settings not configured for this company.")

    url = settings.get("api_url") + settings.get("query_endpoint")
    headers = {
        "Content-Type": "application/json",
    }
    payload = {
        "invoice_number": invoice,
        "username": settings.get("username"),
        "password": settings.get("password")
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response_data = response.json()

        if response_data.get("status") != "00":
            frappe.log_error(f"TIMS Query Failed: {response_data}", "TIMS Invoice Query")
            return {"message": "Query failed", "status": response_data.get("status", "99"), "description": response_data.get("description", "Unknown error")}

        return response_data 

    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Failed to connect: {str(e)}", "TIMS Health Check")
        return {"message": "Error", "status": "99", "description": f"Failed to connect: {str(e)}"}

def get_endpoint(invoice):
    endpoint=""
    if invoice.is_return:
        endpoint = "sign?credit"  
    elif invoice.is_debit_note:
        endpoint = "sign?debit" 
    else:
        endpoint = "sign?invoice"
        
    return endpoint

def is_active(company):
    settings = get_tims_settings(company)
    return settings.get("active")

def tax_amount(invoice):
    hs_code = ''
    if not invoice.total_taxes_and_charges:
        customer = invoice.customer
        tax_category = frappe.get_value("Customer", customer, "tax_category")
        hs_code = frappe.get_value("Tax Category", tax_category, "custom_hs_code")
    return hs_code if hs_code else ""
    
    
def currency_code(currency):
    if currency == "KES":
        return "Ksh"
    
def format_invoice_number(invoice_number):
    """Format the invoice number to ensure there is no special charaters"""
    return re.sub(r"[^a-zA-Z0-9]", "", invoice_number)
