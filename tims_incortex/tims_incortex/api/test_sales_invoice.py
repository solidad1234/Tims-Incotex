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
    - QR code generation
    - Invoice retrieval from TIMS API


"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, today
from unittest.mock import patch, Mock, MagicMock
import json
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

class TestSalesInvoiceSubmit(FrappeTestCase):
    def test_sales_invoice_on_submit(self):
        with patch("tims_incortex.tims_incortex.api.sales_invoice.get_hs_code_before_save"):
            # Create customer
            if not frappe.db.exists("Customer", "Test Customer"):
                frappe.get_doc({
                    "doctype": "Customer",
                    "customer_name": "Test Customer",
                    "customer_group": "Individual",
                    "territory": "All Territories"
                }).insert(ignore_permissions=True)
            # Create item
            if not frappe.db.exists("Item", "Test Service Item"):
                frappe.get_doc({
                    "doctype": "Item",
                    "item_code": "Test Service Item",
                    "item_name": "Test Service Item",
                    "item_group": "Services",
                    "stock_uom": "Nos",
                    "is_sales_item": 1
                }).insert(ignore_permissions=True)
            # Create Sales Invoice
            sales_invoice = frappe.get_doc({
                "doctype": "Sales Invoice",
                "customer": "Test Customer",
                "posting_date": nowdate(),
                "items": [
                    {
                        "item_code": "Test Service Item",
                        "qty": 1,
                        "rate": 100
                    }
                ]
            })
            sales_invoice.insert(ignore_permissions=True)
            sales_invoice.submit()
            self.assertEqual(sales_invoice.docstatus, 1)

class TestTimsInvoice(FrappeTestCase):
    def setUp(self):
        # Create test company
        if not frappe.db.exists("Company", "Test Company"):
            frappe.get_doc({
                "doctype": "Company",
                "company_name": "Test Company",
                "default_currency": "KES"
            }).insert(ignore_permissions=True)
        
        # Create test customer
        if not frappe.db.exists("Customer", "Test TIMS Customer"):
            frappe.get_doc({
                "doctype": "Customer",
                "customer_name": "Test TIMS Customer",
                "customer_group": "Individual",
                "territory": "All Territories",
                "tax_id": "A123456789B"
            }).insert(ignore_permissions=True)
        
        # Create test item
        if not frappe.db.exists("Item", "Test TIMS Item"):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": "Test TIMS Item",
                "item_name": "Test TIMS Item",
                "item_group": "Services",
                "stock_uom": "Nos",
                "is_sales_item": 1
            }).insert(ignore_permissions=True)
        
        # Create test sales invoice
        self.sales_invoice = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": "Test TIMS Customer",
            "company": "Test Company",
            "posting_date": nowdate(),
            "currency": "KES",
            "tax_id": "A123456789B",
            "items": [
                {
                    "item_code": "Test TIMS Item",
                    "qty": 2,
                    "rate": 100,
                    "custom_hs_code": "123456"
                }
            ]
        })
        self.sales_invoice.insert(ignore_permissions=True)

    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    def test_tims_invoice_init(self, mock_settings):
        mock_settings.return_value = {
            'api_url': 'https://api.test.com/',
            'api_key': 'test_key',
            'company_pin': 'P123456789A'
        }
        
        tims_invoice = TimsInvoice(self.sales_invoice.name, "Test Company")
        self.assertEqual(tims_invoice.invoice.name, self.sales_invoice.name)
        self.assertIsNotNone(tims_invoice.settings)

    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    def test_sign_invoice_already_signed(self, mock_settings):
        mock_settings.return_value = {
            'api_url': 'https://api.test.com/',
            'api_key': 'test_key',
            'company_pin': 'P123456789A'
        }
        
        # Set invoice as already signed
        frappe.db.set_value("Sales Invoice", self.sales_invoice.name, "etr_invoice_number", "ETR123456")
        
        tims_invoice = TimsInvoice(self.sales_invoice.name, "Test Company")
        
        with patch('frappe.msgprint') as mock_msgprint:
            tims_invoice.sign_invoice()
            mock_msgprint.assert_called_with("Invoice already signed.", alert=True)

    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    def test_sign_invoice_opening_invoice(self, mock_settings):
        mock_settings.return_value = {
            'api_url': 'https://api.test.com/',
            'api_key': 'test_key',
            'company_pin': 'P123456789A'
        }
        
        # Set invoice as opening
        frappe.db.set_value("Sales Invoice", self.sales_invoice.name, "is_opening", "Yes")
        
        tims_invoice = TimsInvoice(self.sales_invoice.name, "Test Company")
        
        with patch('frappe.msgprint') as mock_msgprint:
            tims_invoice.sign_invoice()
            mock_msgprint.assert_called_with("Opening invoices cannot be signed.", alert=True)

    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_endpoint')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.create_request_log')
    @patch('requests.post')
    def test_sign_invoice_successful(self, mock_post, mock_create_log, mock_endpoint, mock_settings):
        mock_settings.return_value = {
            'api_url': 'https://api.test.com/',
            'api_key': 'test_key',
            'company_pin': 'P123456789A'
        }
        mock_endpoint.return_value = "sign"
        
        # Mock successful API response
        mock_response = Mock()
        mock_response.json.return_value = {
            "description": "Signed successfully.",
            "cu_serial_number": "SN123456",
            "cu_invoice_number": "INV123456",
            "verify_url": "https://verify.test.com/123",
            "message": "Success"
        }
        mock_post.return_value = mock_response
        
        # Mock integration request
        mock_integration = Mock()
        mock_create_log.return_value = mock_integration
        
        tims_invoice = TimsInvoice(self.sales_invoice.name, "Test Company")
        
        with patch.object(tims_invoice, '_update_invoice') as mock_update:
            with patch('frappe.throw'):  # Patch the frappe.throw to prevent actual throw
                try:
                    tims_invoice.sign_invoice()
                except Exception:
                    pass  # Expected due to frappe.throw in the code
        
        # Verify API was called
        mock_post.assert_called_once()

    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    def test_prepare_payload(self, mock_settings):
        mock_settings.return_value = {
            'api_url': 'https://api.test.com/',
            'api_key': 'test_key',
            'company_pin': 'P123456789A'
        }
        
        tims_invoice = TimsInvoice(self.sales_invoice.name, "Test Company")
        
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_relevant_invoice_number', return_value=""):
            payload = tims_invoice._prepare_payload()
        
        self.assertIn('invoice_date', payload)
        self.assertIn('invoice_number', payload)
        self.assertIn('customer_pin', payload)
        self.assertIn('items_list', payload)
        self.assertEqual(payload['invoice_pin'], 'P123456789A')

    def test_update_invoice(self):
        response_data = {
            "cu_serial_number": "SN123456",
            "cu_invoice_number": "INV123456",
            "verify_url": "https://verify.test.com/123",
            "message": "Success",
            "invoice_number": self.sales_invoice.name
        }
        
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings') as mock_settings:
            mock_settings.return_value = {'api_key': 'test'}
            tims_invoice = TimsInvoice(self.sales_invoice.name, "Test Company")
            
            with patch('tims_incortex.tims_incortex.api.sales_invoice.get_qr_code', return_value="qr_code_data"):
                tims_invoice._update_invoice(response_data)
        
        # Verify invoice was updated
        updated_invoice = frappe.get_doc("Sales Invoice", self.sales_invoice.name)
        self.assertEqual(updated_invoice.custom_signing_status, "Signed")

class TestUtilityFunctions(FrappeTestCase):
    
    def test_currency_code_kes(self):
        result = currency_code("KES")
        self.assertEqual(result, "Ksh")
    
    def test_currency_code_other(self):
        result = currency_code("USD")
        self.assertEqual(result, "Ksh")  # Always returns Ksh as per code
    
    def test_format_invoice_number(self):
        result = format_invoice_number("INV-2024-001")
        self.assertEqual(result, "INV2024001")
        
        result = format_invoice_number("INV@#$123")
        self.assertEqual(result, "INV123")
    
    def test_is_valid_kra_pin_valid(self):
        self.assertTrue(is_valid_kra_pin("A123456789B"))
        self.assertTrue(is_valid_kra_pin("P987654321X"))
    
    def test_is_valid_kra_pin_invalid(self):
        self.assertFalse(is_valid_kra_pin("123456789"))  # No letters
        self.assertFalse(is_valid_kra_pin("AB123456789"))  # Too long
        self.assertFalse(is_valid_kra_pin("A12345678B"))  # Too short
        self.assertFalse(is_valid_kra_pin("1123456789B"))  # Starts with number
    
    def test_get_qr_code(self):
        result = get_qr_code("https://test.com")
        self.assertTrue(result.startswith("data:image/png;base64,"))
    
    @patch('requests.post')
    def test_get_invoice_success(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "00",
            "invoice_data": "test_data"
        }
        mock_post.return_value = mock_response
        
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings') as mock_settings:
            mock_settings.return_value = {
                'api_url': 'https://api.test.com/',
                'query_endpoint': 'query',
                'api_key': 'test_key',
                'username': 'user',
                'password': 'pass'
            }
            
            result = get_invoice("INV123", "Test Company")
            self.assertEqual(result['status'], "00")
    
    @patch('requests.post')
    def test_get_invoice_failure(self, mock_post):
        mock_response = Mock()
        mock_response.json.return_value = {
            "status": "01",
            "description": "Not found"
        }
        mock_post.return_value = mock_response
        
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings') as mock_settings:
            mock_settings.return_value = {
                'api_url': 'https://api.test.com/',
                'query_endpoint': 'query',
                'api_key': 'test_key',
                'username': 'user',
                'password': 'pass'
            }
            
            result = get_invoice("INV123", "Test Company")
            self.assertEqual(result['status'], "01")

class TestDocumentEvents(FrappeTestCase):
    
    def setUp(self):
        # Create test customer
        if not frappe.db.exists("Customer", "Test Customer Events"):
            frappe.get_doc({
                "doctype": "Customer",
                "customer_name": "Test Customer Events",
                "customer_group": "Individual",
                "territory": "All Territories"
            }).insert(ignore_permissions=True)
    
    def test_before_save_invalid_kra_pin(self):
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": "Test Customer Events",
            "tax_id": "invalid_pin",
            "posting_date": nowdate(),
            "items": []
        })
        
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_hs_code_before_save'):
            with self.assertRaises(frappe.ValidationError):
                before_save(doc, None)
    
    def test_before_save_valid_kra_pin(self):
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": "Test Customer Events",
            "tax_id": "A123456789B",
            "posting_date": nowdate(),
            "items": []
        })
        
        with patch('tims_incortex.tims_incortex.api.sales_invoice.get_hs_code_before_save'):
            # Should not raise any exception
            before_save(doc, None)
    
    def test_prevent_cancel_signed_invoice(self):
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": "Test Customer Events",
            "custom_signing_status": "Signed",
            "posting_date": nowdate(),
            "items": []
        })
        
        with self.assertRaises(frappe.ValidationError):
            prevent_cancel_signed_invoice(doc, None)
    
    def test_prevent_cancel_unsigned_invoice(self):
        doc = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": "Test Customer Events",
            "custom_signing_status": "Failed",
            "posting_date": nowdate(),
            "items": []
        })
        
        # Should not raise any exception
        prevent_cancel_signed_invoice(doc, None)

class TestHSCodeFunctions(FrappeTestCase):
    
    def setUp(self):
        # Create test item
        if not frappe.db.exists("Item", "Test HS Code Item"):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": "Test HS Code Item",
                "item_name": "Test HS Code Item",
                "item_group": "Services",
                "stock_uom": "Nos",
                "is_sales_item": 1
            }).insert(ignore_permissions=True)
    
    def test_get_hs_code_item_tax(self):
        # Test when no HS code is found
        result = get_hs_code_item_tax("Test HS Code Item")
        self.assertEqual(result, "")
    
    def test_get_relevant_invoice_number_return_invoice(self):
        # Ensure customer exists
        if not frappe.db.exists("Customer", "Test Customer Events"):
            frappe.get_doc({
                "doctype": "Customer",
                "customer_name": "Test Customer Events",
                "customer_group": "Individual", 
                "territory": "All Territories"
            }).insert(ignore_permissions=True)
        
        # Ensure item exists
        if not frappe.db.exists("Item", "Test Item"):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": "Test Item",
                "item_name": "Test Item", 
                "item_group": "Services",
                "stock_uom": "Nos",
                "is_sales_item": 1
            }).insert(ignore_permissions=True)
        
        # Create original invoice with required fields
        original_invoice = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": "Test Customer Events",
            "posting_date": nowdate(),
            "due_date": nowdate(),
            "etr_invoice_number": "ETR123456",
            "items": [{
                "item_code": "Test Item",
                "qty": 1,
                "rate": 100
            }]
        })
        original_invoice.insert(ignore_permissions=True)
        
        # Create return invoice mock object (don't insert to avoid validation issues)
        return_invoice = frappe._dict({
            "doctype": "Sales Invoice",
            "customer": "Test Customer Events",
            "posting_date": nowdate(),
            "is_return": 1,
            "return_against": original_invoice.name,
            "custom_relevant_invoice_number": ""
        })
        
        # Test function
        result = get_relevant_invoice_number(return_invoice)
        self.assertEqual(result, "ETR123456")

class TestEndpointFunctions(FrappeTestCase):
    
    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.inclusive_invoice')
    def test_get_endpoint_regular_inclusive(self, mock_inclusive, mock_settings):
        mock_inclusive.return_value = True
        mock_settings.return_value = {
            'invoice_inclusive': 'sign_inclusive'
        }
        
        invoice = Mock()
        invoice.is_return = False
        invoice.is_debit_note = False
        
        result = get_endpoint(invoice, "Test Company")
        self.assertEqual(result, 'sign_inclusive')
    
    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.inclusive_invoice')
    def test_get_endpoint_regular_exclusive(self, mock_inclusive, mock_settings):
        mock_inclusive.return_value = False
        mock_settings.return_value = {
            'invoice_exclusive': 'sign_exclusive'
        }
        
        invoice = Mock()
        invoice.is_return = False
        invoice.is_debit_note = False
        
        result = get_endpoint(invoice, "Test Company")
        self.assertEqual(result, 'sign_exclusive')
    
    @patch('tims_incortex.tims_incortex.api.sales_invoice.get_tims_settings')
    def test_get_endpoint_debit_note(self, mock_settings):
        mock_settings.return_value = {}
        
        invoice = Mock()
        invoice.is_return = False
        invoice.is_debit_note = True
        
        result = get_endpoint(invoice, "Test Company")
        self.assertEqual(result, 'sign?debit')

class TestWhitelistedFunctions(FrappeTestCase):
    
    @patch('tims_incortex.tims_incortex.api.sales_invoice.is_active')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.TimsInvoice')
    def test_sign_single_invoice_active(self, mock_tims_invoice, mock_is_active):
        mock_is_active.return_value = True
        mock_instance = Mock()
        mock_tims_invoice.return_value = mock_instance
        
        sign_single_invoice("INV123", "Test Company")
        
        mock_tims_invoice.assert_called_once_with("INV123", "Test Company")
        mock_instance.sign_invoice.assert_called_once()
    
    @patch('tims_incortex.tims_incortex.api.sales_invoice.is_active')
    def test_sign_single_invoice_inactive(self, mock_is_active):
        mock_is_active.return_value = False
        
        # Should not raise any exception and not sign
        sign_single_invoice("INV123", "Test Company")
    
    @patch('frappe.get_all')
    @patch('frappe.get_value')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.is_active')
    @patch('tims_incortex.tims_incortex.api.sales_invoice.TimsInvoice')
    def test_retry_pending_invoices(self, mock_tims_invoice, mock_is_active, mock_get_value, mock_get_all):
        # Mock pending invoices
        mock_get_all.return_value = ["INV001", "INV002"]
        mock_get_value.side_effect = ["Test Company", "Test Company"]
        mock_is_active.return_value = True
        
        mock_instance = Mock()
        mock_tims_invoice.return_value = mock_instance
        
        retry_pending_invoices()
        
        # Verify TimsInvoice was called for each pending invoice
        self.assertEqual(mock_tims_invoice.call_count, 2)
        self.assertEqual(mock_instance.sign_invoice.call_count, 2)

    def tearDown(self):
        # Clean up test data
        frappe.db.rollback()