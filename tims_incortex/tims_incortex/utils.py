import frappe

def get_tims_settings(company):
    """Fetch TIMS Incortex Settings from the doctype."""
    settings = frappe.get_doc("Tims Incortex Settings", {"company": company})


    return {
        "company": settings.company,
        "company_pin": settings.company_pin,
        "api_url": settings.server_base_url,
        "query_endpoint": settings.query_endpoint,
        "health_check_endpoint": settings.health_check_endpoint,
        "password": settings.get_password(fieldname="password", raise_exception=False),
        "username": settings.username
    }
