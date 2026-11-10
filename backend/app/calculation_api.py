"""Read-only deterministic calculations. No model calls or approval writes."""
from fastapi import APIRouter, HTTPException
from .models import invoice_fingerprint
from .calculations import calculate_rules, RULES_VERSION


def calculation_router(store, settings):
    router = APIRouter(tags=['Invoice calculations'])

    @router.get('/api/runs/{run_id}/calculations')
    def calculations(run_id: str):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, 'Run not found.')
        if run['status'] != 'completed' or not run.get('invoice'):
            raise HTTPException(409, 'Calculations are available after invoice extraction completes.')
        saved = (run.get('result') or {}).get('calculation')
        current = saved and saved.get('version') == RULES_VERSION
        return {'run_id': run_id, 'invoice_fingerprint': invoice_fingerprint(run['invoice']),
                'rules': saved if current else calculate_rules(run['invoice']),
                'rules_basis': 'decision_snapshot' if current else 'current_rules_preview',
                'superseded': bool(run.get('superseded_by'))}

    return router
