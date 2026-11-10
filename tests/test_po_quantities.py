from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.models import normalize_invoice
from backend.app.policy import evaluate_invoice
from backend.app.po_matching import po_line_options
from tests.test_po_workflow import upload,confirm,edit
from tests.test_integration_review import submit,approve


VENDOR={'id':'v-test','name':'Test goods','approved':True}
PO={'po_number':'P-TEST','vendor_id':'v-test','vendor_name':'Test goods','po_date':'2026-01-01',
    'currency':'USD','subtotal':'100','tax':'0','total':'100','committed':'0','status':'open','approval_status':'approved',
    'line_items':[dict(description='Widget',sku='W1',quantity='10',unit_price='10',amount='100')]}


def invoice(number='INV-1',quantity='4',**updates):
    quantity=Decimal(quantity)
    raw=dict(invoice_number=number,vendor_name=VENDOR['name'],po_number=PO['po_number'],invoice_date='2026-01-01',
             currency='USD',subtotal=str(quantity*10),tax='0',total=str(quantity*10),extraction_method='structured',
             line_items=[dict(description='Widget',sku='W1',quantity=str(quantity),unit_price='10',amount=str(quantity*10))])
    raw.update(updates)
    return normalize_invoice(raw)


def evaluate(value,prior=None,po=None):
    return evaluate_invoice(value,[VENDOR],[po or deepcopy(PO)],prior or [])


def prior(value,decision='approved'):
    return {'id':value['invoice_number'],'status':'completed','invoice':value,'decision':decision,'result':{'matched_po':deepcopy(PO)}}


def test_partial_invoices_consume_quantity_and_amount_cumulatively():
    p=deepcopy(PO); p['committed']='40'
    result=evaluate(invoice('INV-2','3'),[prior(invoice())],p)
    assert result['decision']=='approved'
    reconciliation=result['po_reconciliation']; row=reconciliation['lines'][0]
    assert (row['previously_invoiced'],row['current_quantity'],row['cumulative_quantity'],row['remaining_quantity'])==('4','3','7','3')
    assert reconciliation['cumulative_total']=='70' and reconciliation['remaining_amount']=='30'
    assert reconciliation['amount_status']=='partially_invoiced'
    assert reconciliation['receipt_status']=='not_recorded'


def test_over_quantity_blocks_even_if_generous_amount_budget_would_pass():
    p=deepcopy(PO); p.update(total='10000',committed='80')
    result=evaluate(invoice('INV-2','3'),[prior(invoice(quantity='8'))],p)
    assert result['decision']=='needs_review'
    assert any(c['code']=='po_quantity_1' and c['blocking'] for c in result['checks'])
    assert result['po_reconciliation']['lines'][0]['remaining_quantity']=='-1'


def test_split_rows_for_one_po_product_cannot_bypass_the_ordered_quantity():
    row=dict(description='Widget',sku='W1',quantity='6',unit_price='10',amount='60')
    result=evaluate(invoice(quantity='12',line_items=[row,deepcopy(row)]))
    assert result['decision']=='needs_review'
    assert all(r['cumulative_quantity']=='12' and r['status']=='not_matched' for r in result['po_reconciliation']['lines'])


@pytest.mark.parametrize('decision',['needs_review','rejected'])
def test_unapproved_runs_do_not_consume_quantities(decision):
    result=evaluate(invoice('INV-2','6'),[prior(invoice(quantity='8'),decision)])
    assert result['decision']=='approved'
    assert result['po_reconciliation']['lines'][0]['previously_invoiced']=='0'


def test_exact_description_matching_and_explicit_mapping_for_ambiguous_names():
    p=deepcopy(PO); p['line_items'][0].pop('sku')
    value=invoice(); value['line_items'][0].pop('sku')
    assert evaluate(value,po=p)['decision']=='approved'
    value['line_items'][0]['description']='Widget packed'
    assert evaluate(value,po=p)['decision']=='needs_review'
    value['line_items'][0]['po_line_ref']=po_line_options(p)[0]['ref']
    assert evaluate(value,po=p)['decision']=='approved'
    p['line_items'][0]['description']='Changed product'
    assert evaluate(value,po=p)['decision']=='needs_review'


def test_ambiguous_or_conflicting_skus_cannot_be_silently_assigned():
    p=deepcopy(PO); p['line_items'].append(deepcopy(p['line_items'][0]))
    assert evaluate(invoice(),po=p)['decision']=='needs_review'
    value=invoice(); value['line_items'][0].update(sku='OTHER',po_line_ref=po_line_options(PO)[0]['ref'])
    assert evaluate(value)['decision']=='needs_review'


def test_po_price_compares_net_after_discount_and_tax_inclusion():
    value=invoice(quantity='2'); row=value['line_items'][0]
    row.update(unit_price='15',discount_amount='10',tax_rate='18',amount='23.60',amount_tax_mode='inclusive',unit_price_tax_mode='exclusive')
    value.update(subtotal='20',tax='3.60',total='23.60',tax_status='stated')
    assert evaluate(value)['decision']=='approved'
    row.update(unit_price='11.80',discount_amount='0',unit_price_tax_mode='inclusive')
    assert evaluate(value)['decision']=='approved'


def test_unallocated_approved_history_is_flagged_instead_of_assuming_zero():
    old=invoice('OLD','4'); old['line_items'][0].update(sku='unknown',description='Unmapped')
    result=evaluate(invoice('NEW','2'),[prior(old)])
    assert result['decision']=='needs_review'
    assert result['po_reconciliation']['history_unresolved']==['OLD']


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as c:
        order=deepcopy(PO)
        draft=upload(c,[order]); response=confirm(c,draft)
        assert response.status_code==200,response.text
        response=c.post('/api/procurement/vendors/v-test/review',json={'action':'approve','reviewer':'Test','comment':'Test supplier review','attest_reviewed':True})
        assert response.status_code==200
        yield c


def test_rejection_releases_both_amount_and_quantity_commitments(client):
    run=submit(client,invoice(quantity='6'))
    assert run['decision']=='approved'
    def po(): return next(p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number']=='P-TEST')
    assert po()['line_commitments'][0]['invoiced_quantity']=='6'
    response=client.post('/api/runs/'+run['id']+'/review',json={'action':'reject','reviewer':'Test','comment':'Cancelled test invoice'})
    assert response.status_code==200
    assert po()['committed']=='0' and po()['line_commitments'][0]['invoiced_quantity']=='0'
    assert submit(client,invoice('NEXT','10'))['decision']=='approved'


def test_preview_checks_mapping_and_quantity_without_writing(client):
    value=invoice(); value['line_items'][0].update(description='Reviewer product',sku=None)
    run=submit(client,value); url='/api/runs/'+run['id']
    assert run['decision']=='needs_review'
    context=client.get(url+'/review-context').json()
    body={'invoice':context['review_invoice'],'expected_invoice_fingerprint':context['invoice_fingerprint']}
    body['invoice']['line_items'][0]['po_line_ref']=po_line_options(PO)[0]['ref']
    with client.app.state.store.connection() as conn: before='\n'.join(conn.iterdump())
    response=client.post(url+'/line-preview',json=body)
    assert response.status_code==200
    assert response.json()['po_reconciliation']['lines'][0]['status']=='matched'
    full=client.post(url+'/correction-preview',json=body).json()
    assert full['invoice']['line_items'][0]['po_line_ref']==po_line_options(PO)[0]['ref']
    assert full['result']['decision']=='approved'
    with client.app.state.store.connection() as conn: assert before=='\n'.join(conn.iterdump())


def test_concurrent_approvals_recheck_quantity_under_the_transaction_lock(client):
    larger=deepcopy(PO)
    larger.update(subtotal='1000',total='1000')
    larger['line_items'].append(dict(description='Other product',sku='OTHER',quantity='90',unit_price='10',amount='900'))
    assert confirm(client,upload(client,[larger]),allow_updates=True).status_code==200
    # Hold vendor approval so neither invoice can auto-approve before the race.
    body={'reviewer':'Test','comment':'Supplier review for concurrency test','attest_reviewed':True}
    assert client.post('/api/procurement/vendors/v-test/review',json={**body,'action':'reject'}).status_code==200
    values=[invoice('RACE-'+str(i),'6') for i in range(2)]
    runs=[submit(client,v) for v in values]
    assert all(r['decision']!='approved' for r in runs)
    assert client.post('/api/procurement/vendors/v-test/review',json={**body,'action':'approve'}).status_code==200
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda r:approve(client,r),runs))
    assert sorted(r.status_code for r in responses)==[200,409]
    assert 'ordered 10' in next(r.text for r in responses if r.status_code==409)
    po=next(p for p in client.get('/api/procurement').json()['purchase_orders'] if p['po_number']=='P-TEST')
    assert po['line_commitments'][0]['invoiced_quantity']=='6'


def test_po_amendment_cannot_reset_consumed_product_quantities(client):
    assert submit(client,invoice(quantity='6'))['decision']=='approved'
    amended=deepcopy(PO)
    amended['line_items'][0].update(quantity='5',unit_price='20',amount='100')
    draft=upload(client,[amended])
    response=confirm(client,draft,allow_updates=True)
    assert response.status_code==409 and 'quantity' in response.text
