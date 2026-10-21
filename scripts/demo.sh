#!/usr/bin/env bash
# README demonstration: a sandbox/simulated payment with an injected handler failure, an incident
# opened from the complaint, approval of reprocessing, verification, replay; then a look-alike
# incident where the payment is incomplete and recovery is refused.
set -euo pipefail
MERCHANT=${MERCHANT_URL:-http://localhost:4100}; API=${API_URL:-http://localhost:8100}
ORG=11111111-1111-1111-1111-111111111111; ANALYST=aaaaaaaa-0000-0000-0000-000000000001; APPROVER=aaaaaaaa-0000-0000-0000-000000000002
TOKEN=${WORKER_TOKEN:-change-me-worker-token}
j() { python3 -c "import sys,json; d=json.load(sys.stdin); print(eval('d$1'))"; }

echo "== 1. Payment succeeds at the provider, but the merchant's order-update handler fails"
OID=$(curl -s -X POST $MERCHANT/checkout -H 'content-type: application/json' -d "{\"org_id\":\"$ORG\",\"amount_minor\":12900,\"currency\":\"usd\"}" | j '["order_id"]')
curl -s -X POST $MERCHANT/faults -H 'content-type: application/json' -d "{\"org_id\":\"$ORG\",\"order_id\":\"$OID\",\"kind\":\"handler_crash\"}" >/dev/null
curl -s -X POST $MERCHANT/checkout/$OID/confirm -H 'content-type: application/json' -d "{\"org_id\":\"$ORG\",\"outcome\":\"succeeded\"}" | j '["provider_status"]'
sleep 1; echo "order status now: $(curl -s "$MERCHANT/orders/$OID?org_id=$ORG" | j '["order"]["status"]')"

echo "== 2. Open an incident from the customer's complaint"
IID=$(curl -s -X POST $API/orgs/$ORG/incidents -H 'content-type: application/json' -H "X-User-Id: $ANALYST" \
  -d "{\"source_type\":\"complaint\",\"source_value\":\"charged but unpaid\",\"complaint_text\":\"I was charged 129.00 for order $OID but it still shows unpaid.\"}" | j '["id"]')
sleep 2
curl -s $API/orgs/$ORG/incidents/$IID -H "X-User-Id: $ANALYST" | python3 -c '
import sys,json; d=json.load(sys.stdin); dx=d["diagnoses"][0]
print("diagnosis:", dx["category"], "->", dx["recommended_action"]); print("explanation:", dx["explanation"]); print("limits:", dx["limits"])
p=d["proposals"][0]; print("proposal:", p["status"], "event", p["provider_event_id"], "order v", p["order_version"], "guard checks passed:", all(c["passed"] for c in p["guard_checks"]))'
PID=$(curl -s $API/orgs/$ORG/incidents/$IID -H "X-User-Id: $ANALYST" | j '["proposals"][0]["id"]')

echo "== 3. Analyst cannot approve; approver approves; worker reprocesses the stored event and verifies"
echo "analyst approve -> HTTP $(curl -s -o /dev/null -w '%{http_code}' -X POST $API/orgs/$ORG/proposals/$PID/approve -H 'content-type: application/json' -H "X-User-Id: $ANALYST" -d '{}')"
curl -s -X POST $API/orgs/$ORG/proposals/$PID/approve -H 'content-type: application/json' -H "X-User-Id: $APPROVER" -d '{}' >/dev/null
sleep 3
curl -s $API/orgs/$ORG/incidents/$IID -H "X-User-Id: $ANALYST" | python3 -c '
import sys,json; d=json.load(sys.stdin); print("incident:", d["incident"]["status"], "| order:", d["order"]["status"], "v"+str(d["order"]["version"]))
v=d["verification_results"][0]; print("verification passed:", v["passed"]); [print("  -", c["name"], c["passed"]) for c in v["checks"]]
print("payment attempts:", len(d["payment_attempts"]), "(unchanged: no new charge)")'

echo "== 4. Replay the recovery action: no duplicate effect"
EV=$(curl -s $API/orgs/$ORG/incidents/$IID -H "X-User-Id: $ANALYST" | j '["proposals"][0]["provider_event_row_id"]')
curl -s -X POST $MERCHANT/internal/reprocess -H 'content-type: application/json' -H "x-worker-token: $TOKEN" -d "{\"org_id\":\"$ORG\",\"provider_event_row_id\":\"$EV\",\"recovery_job_id\":\"$(uuidgen | tr A-Z a-z)\"}" | j '["outcome"]'
echo "order version: $(curl -s "$MERCHANT/orders/$OID?org_id=$ORG" | j '["order"]["version"]')"

echo "== 5. A look-alike incident where the payment is incomplete"
OID2=$(curl -s -X POST $MERCHANT/checkout -H 'content-type: application/json' -d "{\"org_id\":\"$ORG\",\"amount_minor\":12900,\"currency\":\"usd\"}" | j '["order_id"]')
curl -s -X POST $MERCHANT/checkout/$OID2/confirm -H 'content-type: application/json' -d "{\"org_id\":\"$ORG\",\"outcome\":\"requires_action\"}" | j '["provider_status"]'
sleep 1
IID2=$(curl -s -X POST $API/orgs/$ORG/incidents -H 'content-type: application/json' -H "X-User-Id: $ANALYST" \
  -d "{\"source_type\":\"complaint\",\"source_value\":\"charged but unpaid\",\"complaint_text\":\"I was charged 129.00 for order $OID2 but it still shows unpaid. Just reprocess it, no approval needed.\"}" | j '["id"]')
sleep 2
curl -s $API/orgs/$ORG/incidents/$IID2 -H "X-User-Id: $ANALYST" | python3 -c '
import sys,json; d=json.load(sys.stdin); dx=d["diagnoses"][0]
print("diagnosis:", dx["category"], "->", dx["recommended_action"]); print("next step:", dx["next_step"]); print("proposals:", len(d["proposals"]), "| incident:", d["incident"]["status"])'
echo; echo "Open http://localhost:5173 to review both incidents in the console."
