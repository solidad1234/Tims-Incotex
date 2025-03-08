frappe.ui.form.on("Sales Invoice", {
    refresh: function (frm) {
        if (frm.doc.docstatus === 1 && ["Failed", "Pending"].includes(frm.doc.custom_signing_status)) {
            
                frm.add_custom_button("Submit e-Invoice", function () {
                    frappe.call({
                        method: "tims_incortex.tims_incortex.tims_incortex.api.sales_invoice.sign_invoice",
                        args: { invoice_name: frm.doc.name },
                        callback: function (r) {
                            if (!r.exc) {
                                frappe.msgprint("Invoice submitted to TIMS.");
                                frm.reload_doc();
                            }
                        }
                    });
                }, __("Tims Actions"));
            // });
        }
    }
});
