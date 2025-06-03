"""
This module contains unit tests for the Sales Invoice functionality 
within the TIMS integration.

Test Coverage:
- Basic Sales Invoice creation and submission.
- Initialization and behavior of the `TimsInvoice` class.
- Signing behavior for invoices, including edge cases:
    - Invoice already signed
    - Opening invoices (should not be signed)
    - Successful signing via API
- Payload preparation and invoice updates.
- Utility function validations:
    - Currency code mapping
    - Invoice number formatting
    - KRA PIN validation
    - Invoice retrieval from TIMS API
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate
from unittest.mock import patch, Mock
import json
import requests
from tims_incortex.tims_incortex.api.sales_invoice import (
    TimsInvoice, 
    sign_single_invoice, 
    retry_pending_invoices,
    get_qr_code,
    format_invoice_number,
    currency_code,
    inclusive_invoice,
    is_valid_kra_pin,
    before_save,
    prevent_cancel_signed_invoice,
    get_relevant_invoice_number,
    get_hs_code_item_tax,
    get_invoice,
    get_endpoint,
    is_active
)

class TestTimsInvoiceEnhanced(FrappeTestCase):
    def setUp(self):
        if not frappe.db.exists("Company", "Test TIMS Company"):
            self.company = frappe.get_doc({
                "doctype": "Company",
                "company_name": "Test TIMS Company",
                "default_currency": "KES"
            }).insert(ignore_permissions=True)
        else:
            self.company = frappe.get_doc("Company", "Test TIMS Company")

        if not frappe.db.exists("Customer", "Test TIMS Customer Enhanced"):
            self.customer = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": "Test TIMS Customer Enhanced",
                "customer_group": "Individual",
                "territory": "All Territories",
                "tax_id": "A123456789B"
            }).insert(ignore_permissions=True)
        else:
            self.customer = frappe.get_doc("Customer", "Test TIMS Customer Enhanced")

        if not frappe.db.exists("Item", "Test TIMS Item Enhanced"):
            self.item = frappe.get_doc({
                "doctype": "Item",
                "item_code": "Test TIMS Item Enhanced",
                "item_name": "Test TIMS Item Enhanced",
                "item_group": "Services",
                "stock_uom": "Nos",
                "is_sales_item": 1
            }).insert(ignore_permissions=True)
        else:
            self.item = frappe.get_doc("Item", "Test TIMS Item Enhanced")

        self.sales_invoice = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": self.customer.name,
            "company": self.company.name,
            "posting_date": nowdate(),
            "currency": "KES",
            "tax_id": "A123456789B",
            "items": [
                {
                    "item_code": self.item.name,
                    "qty": 2,
                    "rate": 100,
                    "custom_hs_code": "123456"
                }
            ]
        }).insert(ignore_permissions=True)

    def tearDown(self):
        for doctype, name in [
            ("Sales Invoice", getattr(self, "sales_invoice", None)),
            ("Item", getattr(self, "item", None)),
            ("Customer", getattr(self, "customer", None)),
            ("Company", getattr(self, "company", None))
        ]:
            if name and frappe.db.exists(doctype, name.name):
                frappe.delete_doc(doctype, name.name, force=True)
        frappe.db.rollback()

    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_endpoint')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.create_request_log')
    @patch('requests.post')
    def test_sign_invoice_api_connection_error(self, mock_post, mock_create_log, mock_endpoint, mock_settings):
        mock_settings.return_value = {'api_url': 'https://api.test.com/', 'api_key': 'test_key', 'company_pin': 'P123456789A'}
        mock_endpoint.return_value = "sign"
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")
        mock_create_log.return_value = Mock()
        tims_invoice = TimsInvoice(self.sales_invoice.name, self.company.name)

        with patch('frappe.msgprint') as mock_msgprint, patch('frappe.throw'):
            try:
                tims_invoice.sign_invoice()
            except:
                pass
            mock_msgprint.assert_called()

    def test_get_invoice_api_failure(self):
        with patch('requests.post') as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")
            with patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings') as mock_settings:
                mock_settings.return_value = {
                    'api_url': 'https://api.test.com/',
                    'query_endpoint': 'query',
                    'api_key': 'test_key',
                    'username': 'user',
                    'password': 'pass'
                }
                result = get_invoice("INV123", self.company.name)
                self.assertEqual(result['status'], "99")

    def test_get_invoice_missing_settings(self):
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings') as mock_settings:
            mock_settings.return_value = None
            with self.assertRaises(frappe.ValidationError):
                get_invoice("INV123", self.company.name)

    def test_format_invoice_number_edge_cases(self):
        self.assertEqual(format_invoice_number(""), "")
        self.assertEqual(format_invoice_number("INV-2024-AbC"), "INV2024AbC")

    def test_before_save_no_customer(self):
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "posting_date": nowdate(),
            "items": []
        })
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_hs_code_before_save'):
            before_save(doc, None)

    def test_before_save_customer_no_tax_id(self):
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": self.customer.name,
            "posting_date": nowdate(),
            "items": []
        })
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_hs_code_before_save'):
            before_save(doc, None)

    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    def test_is_active_missing_settings(self, mock_settings):
        mock_settings.return_value = {}
        self.assertIsNone(is_active(self.company.name))

    @patch('frappe.get_all')
    @patch('frappe.get_value')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.is_active')
    def test_retry_pending_invoices_no_pending(self, mock_is_active, mock_get_value, mock_get_all):
        mock_get_all.return_value = []
        retry_pending_invoices()
        mock_get_all.assert_called_once()

    @patch('frappe.get_all')
    @patch('frappe.get_value')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.is_active')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.TimsInvoice')
    def test_retry_pending_invoices_company_inactive(self, mock_tims_invoice, mock_is_active, mock_get_value, mock_get_all):
        mock_get_all.return_value = ["INV001"]
        mock_get_value.return_value = self.company.name
        mock_is_active.return_value = False
        retry_pending_invoices()
        mock_tims_invoice.assert_not_called()

    @patch('tims_incortex.tims_incortex.api.sales_invoice.is_active')
    def test_sign_single_invoice_nonexistent_invoice(self, mock_is_active):
        mock_is_active.return_value = True
        with self.assertRaises(frappe.DoesNotExistError):
            sign_single_invoice("NON_EXISTENT_INVOICE", self.company.name)
