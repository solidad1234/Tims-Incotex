## **Tims Incortex Integration for ERPNext**

The **Tims Incortex** integration connects ERPNext with the Incortex ETIMS device, allowing seamless electronic tax invoice submissions to the **Kenya Revenue Authority (KRA)**. This integration ensures that every invoice created in ERPNext is sent to Incortex, acknowledged with a response, and then submitted to KRA, all while storing and tracking the status of each invoice.

----------

### **How the Integration Works**

1.  **Invoice Creation**: Sales Invoices are created in ERPNext as usual.
    
2.  **Invoice Submission**: Upon submission, the invoice is automatically sent to the Incortex device via API.
    
3.  **Response Handling**: Incortex responds with an ID and other related information.
    
4.  **KRA Submission**: Incortex sends the data to KRA and logs the KRA response under the same ID returned to ERPNext.
    
5.  **Invoice Update**: ERPNext updates the invoice with CU Invoice Number, marks it as filed, and stores the verification URL for generating a QR code.
    

----------

### **Setup Configuration**

#### **1. Tims Incortex Settings Doctype**

Set this up for each company:

-   **Company**: This acts as the identifier; each company should have only one configuration.
    
-   **Environment**: Choose between `Production` or `Sandbox`.
    
-   **Base URL** and **API Key**: Provided by Incortex.
    

#### **2. API Endpoints**

Each invoice type and pricing model uses a different endpoint. Configure the following:

-   **Invoice URL (Item Price Inclusive)**: For invoices where item prices include tax.
    
-   **Invoice URL (Item Price Exclusive)**: For invoices where item prices exclude tax.
    
-   **Credit Note URL (Item Price Inclusive)**: For inclusive tax credit notes.
    
-   **Credit Note URL (Item Price Exclusive)**: For exclusive tax credit notes.
    
-   **Query URL**: To confirm details of already-submitted invoices.
    
-   **Health Check URL**: To check the availability/status of the Incortex device.
    

#### **3. Other Settings**

-   **Enable Auto-Signing**: Automatically signs invoices after submission.
    
-   **Health Check Button**: A button to manually test the device status.
    

----------

### **Tax Category Configuration**

-   Attach a **Tax Category** to each customer.
    
-   If tax is **Exempt** or **Zero Rated**:
    
    -   Add a **HS Code** (custom field). This is required and sent with item payload.
        
    -   (Optional) **Tax Code** field exists, but is not required currently.
        

----------

### **Sales Invoice Flow**

1.  Create a **Sales Invoice** in ERPNext.
    
2.  Ensure the following:
    
    -   Tax Category is selected.
        
    -   Taxes are defined in **Sales Taxes and Charges**.
        
    -   This determines whether to use inclusive or exclusive endpoint.
        
3.  Upon submission:
    
    -   The invoice is sent to Incortex.
        
    -   A response is received instantly.
        
    -   Updates are made to the invoice:
        
        -   `System Invoice Number`
            
        -   `CU Invoice Number`
            
        -   `is_filed` checkbox ticked
            
        -   `Verify URL` is generated
            
    -   A **QR Code** is created using the Verify URL.
        
4.  Use a **custom print format** to include the QR code on the printed invoice.
    

> 💡 **If submission fails**, ERPNext will show the failure reason, but the invoice will still be submitted locally.

----------

### **Manual Re-Submission**

-   Use the **ETIMS Action** button labeled **"Submit E-Invoice"** to manually resend the invoice if needed.
    

----------

### **Scheduler for Auto Submission**

-   A background job (scheduler) handles the auto-submission of pending invoices.
    

----------

### **Credit Note Handling**

1.  Always create a **Credit Note** against an existing Sales Invoice.
    
    -   The system will automatically fetch the correct `CU Invoice Number` which is then stores in Relevant Invoice Numer field.
        
2.  Upon submission:
    
    -   The credit note is sent to Incortex.
        
    -   A response is received and recorded, similar to a sales invoice.
        

> ⚠️ If you're creating a **standalone credit note**, **manually enter** the `CU Invoice Number` from the original Sales Invoice to ensure successful submission.