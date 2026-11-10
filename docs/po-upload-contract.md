# PO upload workflow — implementation contract

The statuses **Validated PO** and **Approved vendor** are independent. A file upload never approves a vendor. New suppliers become pending vendor review. All imports retain already approved invoice commitments.

## PO schema and policy

Required fields: po_number, vendor_name, po_date (YYYY-MM-DD), currency (explicit ISO-style three letters), subtotal, tax (explicit zero permitted), total, approval_status (approved/pending/rejected), status (open/closed), and >=1 line_items [{description,quantity,unit_price,amount}]. Optional vendor_id, approved_by, description, sku. Monetary values are decimal strings. Totals and line arithmetic must reconcile. Source assertions of approval require an operator's independent authorization attestation before registration.

A PO can be structurally valid yet closed/pending/rejected and therefore ineligible for invoice matching. Every upload is a draft until explicitly confirmed. Uploaded POs matching an existing vendor do not change its approval; new vendors have approved=false and approval_status=pending.

## API

GET /api/procurement/po-policy => {version,required_fields:[{key,label,description}],line_item_fields:[...],rules:[string]}
POST /api/procurement/uploads multipart files (max5 files,10MB each) =>202 {uploads:[Upload]}
GET /api/procurement/uploads => {uploads:[Upload]} (latest50)
GET /api/procurement/uploads/{id} => Upload
GET /api/procurement/uploads/{id}/source => file attachment
POST /api/procurement/uploads/{id}/validate JSON {revision:number,purchase_orders:[PO]} => Upload. Saves edited preview, reruns checks, increments revision. No registry writes.
POST /api/procurement/uploads/{id}/confirm JSON {revision:number,reviewer:string,comment:string,attest_authorized:boolean,allow_updates:boolean} => {upload:Upload,procurement:Procurement}. Requires reviewed fields+authorization checkbox. Commit atomic, revision checked, fully validates again. Replacing changed existing records requires allow_updates. Vendor/currency of committed POs cannot change, budget cannot fall below committed value. Confirmation never approves a vendor. Repeated confirmation returns409.
POST /api/procurement/purchase-orders/{po_number}/review => Upload (a draft from existing record for edit/revalidation; source is JSON snapshot). URI encode po_number.
POST /api/procurement/vendors/{vendor_id}/review JSON {action:'approve'|'reject',reviewer,comment,attest_reviewed:boolean} => {vendor:Vendor,procurement:Procurement}. Separate manual vendor review; approving requires checkbox confirming independent supplier review. Does not validate any PO.
GET /api/procurement/template?format=csv|xlsx|json => downloadable populated example template.

Upload: {id,filename,status:'queued'|'processing'|'ready'|'confirmed'|'failed',created_at,updated_at,revision:number,items:[{purchase_order:PO,validation:{valid,eligible,checks:[{code,field,label,status:'pass'|'warn'|'fail',message,blocking}],missing_fields:[string]},extraction:{method,evidence:object,warnings:[string],raw_text:string}}],events:[{stage,status,message,at}],error:null|string,confirmation:null|{reviewer,comment,at,po_numbers:[string]},review_history:[],source_sha256}
Procurement existing shape {vendors,purchase_orders,policy}; extra PO fields validation_status:'validated'|'unverified',eligibility_status:'eligible'|'pending_vendor'|'ineligible'|'unverified',validation_issues?:[string],reviewer?,validated_at?. Vendor extra approval_status:'approved'|'pending'|'rejected',approved remainsbool,approval_history?:[].

## UI

In Procurement add prominent Upload POs action accepting CSV/XLSX/JSON/PDF/PNG/JPG/WEBP/TIFF/TXT, retain vendor-only advanced import if useful. Show required-column policy + templates. Drafts/history list clickable, poll queued/processing actual jobs. Use friendly editable field and line-item form (not JSON-only) with source evidence, validation errors. Save & validate then explicit Confirm import with reviewer/reason/checkbox. Checkbox for allowing updates defaultfalse. Clear after field changes: validations shown are last saved; user must save beforeconfirm.

Registered PO table shows validation and eligibility separate from vendorapproval; Review/validate button createsdraft. New pending vendor cards allow separate Approve/reject form with independentreviewattestation. Existingapprovedvendors remainapproved. ExistinglegacyPOs without validatedschema/review provenance showUnverified and newinvoiceevaluationmustblockuntilvalidated; pastinvoicehistory/commitmentsstayunchanged. Demo/seedPOfixtureswillbe complete and prevalidated as explicitlyfictionalfixtures.

## Invoice allocation and cumulative quantities

Invoice review can store `line_items[].po_line_ref`, a server-derived identity for a selected PO product. Unique SKU or normalized exact-description matches need no manual selection; ambiguity and conflicting SKUs require correction. Each invoice is checked against quantities already consumed by approved invoices, with repeated rows summed. Monetary tolerance does not authorize excess quantity. Rejected/pending invoices do not consume capacity. Product net prices include discounts and remove included tax before comparison.

Procurement returns derived `line_commitments` and `approved_invoice_count`. Unallocated approved history is reported through `quantity_history_unresolved`. PO confirmation blocks identity changes/removal or quantity reductions that erase prior consumption. Invoice totals and quantities are checked again within the approval transaction. This is PO-to-invoice verification, not physical goods-receipt verification.
