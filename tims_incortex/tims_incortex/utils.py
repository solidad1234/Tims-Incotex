import frappe

def get_tims_settings():
    """Fetch TIMS Incortex Settings from the doctype."""
    settings = frappe.get_single("Tims Incortex Settings")

    return {
        "api_url": settings.api_url,
        "cu_number": settings.cu_number,
        "serial_number": settings.serial_number
    }
