"""Portable output adapters. CSV cells and HTML are escaped for their consumers."""
import csv
import html
import io
import json


def csv_safe(value):
    value = '' if value is None else str(value)
    if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        return "'" + value
    return value


def as_csv(runs):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['run_id', 'filename', 'invoice_number', 'vendor_name', 'po_number', 'currency', 'total', 'status', 'decision', 'summary', 'created_at', 'rules_calculated_total'])
    for run in runs:
        inv, result = run.get('invoice') or {}, run.get('result') or {}
        writer.writerow([csv_safe(v) for v in [run['id'], run['filename'], inv.get('invoice_number'),
            inv.get('vendor_name'), (result.get('matched_po') or {}).get('po_number', inv.get('po_number')),
            inv.get('currency'), inv.get('total'), run['status'], run.get('decision'), result.get('summary', run.get('error')),
            run['created_at'], (result.get('calculation') or {}).get('calculated_total')]])
    return '\ufeff' + output.getvalue()


def as_json(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def as_html(run):
    esc = lambda v: html.escape(str(v if v is not None else '—'))
    inv, result = run.get('invoice') or {}, run.get('result') or {}
    fields = ''.join(f'<tr><th>{esc(k.replace("_", " ").title())}</th><td>{esc(inv.get(k))}</td></tr>' for k in
        ['invoice_number', 'vendor_name', 'po_number', 'invoice_date', 'due_date', 'currency', 'subtotal', 'tax', 'total'])
    checks = ''.join(f'<tr><td>{esc(c["label"])}</td><td>{esc(c["status"])}</td><td>{esc(c["message"])}</td></tr>' for c in result.get('checks', []))
    reviews = ''.join(f'<li>{esc(r["at"])} · {esc(r["reviewer"])} · {esc(r["action"])}: {esc(r["comment"])}</li>' for r in run['reviews'])
    calculations = ''
    for title, analysis in [('Deterministic calculations', result.get('calculation'))]:
        if not analysis:
            continue
        rows = ''.join(f'<tr><td>{esc(step.get("label"))}</td><td>{esc(step.get("formula"))}</td><td>{esc(step.get("calculated"))}</td><td>{esc(step.get("difference"))}</td></tr>' for step in analysis.get('steps', []))
        notes = ''.join(f'<li>{esc(note)}</li>' for note in analysis.get('warnings', []) + analysis.get('assumptions', []))
        calculations += f'<h2>{esc(title)}</h2><p>{esc(analysis.get("status"))} · Calculated total: {esc(analysis.get("calculated_total"))} · Printed total: {esc(analysis.get("printed_total"))} · Difference: {esc(analysis.get("difference"))}</p><p>{esc(analysis.get("error", ""))}</p><table><tr><th>Step</th><td>Equation</td><td>Calculated</td><td>Difference</td></tr>{rows}</table><ul>{notes}</ul>'
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LedgerFlow decision — {esc(inv.get('invoice_number', run['id']))}</title>
<style>body{{font:15px system-ui;max-width:960px;margin:48px auto;padding:0 24px;color:#152b35}}h1{{font-size:30px}}p{{line-height:1.6}}table{{width:100%;border-collapse:collapse;margin:24px 0}}td,th{{text-align:left;border-bottom:1px solid #ddd;padding:12px}}th{{width:28%}}.meta{{color:#60717c;font-size:13px}}@media print{{body{{margin:0}}}}</style>
<h1>LedgerFlow · Invoice decision</h1><p class="meta">Run {esc(run['id'])} · {esc(run['created_at'])}</p>
<h2>{esc(run.get('decision') or run['status']).replace('_', ' ').title()}</h2><p>{esc(result.get('summary') or run.get('error'))}</p>
<table>{fields}</table><h2>Decision checks</h2><table><tr><th>Rule</th><td>Status</td><td>Reason</td></tr>{checks}</table>
{calculations}
<h2>Review history</h2><ul>{reviews or '<li>No manual review recorded.</li>'}</ul>
<p class="meta">Approval reserves procurement capacity; it does not execute a payment. Source SHA-256: {esc(run['source_sha256'])}</p></html>'''
