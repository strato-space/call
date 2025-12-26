# Google auth / billing / Vertex AI troubleshooting — chat digest

> Scope: fast-agent / media-gen-mcp running on server (p2), Google Cloud auth, service accounts, Vertex AI, Sheets/Drive/Docs access.

## 1) Symptoms observed

### A. fast-agent e2e test failing (Gemini)
1. **Missing API key error** (Developer API path):
   - `Google API key not configured`.
2. **Config type mismatch**:
   - `AttributeError: 'dict' object has no attribute 'enabled'` when code expected `context.config.google.vertex_ai.enabled`.
3. **Billing disabled on Vertex project**:
   - `403 PERMISSION_DENIED ... requires billing to be enabled`.
4. **Model not found** for Vertex publisher model:
   - `404 Publisher Model ... gemini-2.5-flash-preview-09-2025 not found`.

### B. Direct Dev API call blocked by region
- `generativelanguage.googleapis.com ... 400 FAILED_PRECONDITION: User location is not supported for the API use.`

### C. Tooling (gsh / Sheets) requiring reauth daily
- Errors like: `Reauthentication is needed. Please run gcloud auth application-default login`.

### D. Service account key creation denied
- `FAILED_PRECONDITION: Key creation is not allowed on this service account.`
- Violation type: `constraints/iam.disableServiceAccountKeyCreation`.

### E. After switching to service-account JSON, token refresh fails
- `google.auth.exceptions.RefreshError: invalid_scope: Invalid OAuth scope or ID token audience provided.`

---

## 2) Root causes identified

### A. Config object shape is inconsistent
- In some executions, `context.config.google.vertex_ai` arrived as a **dict/mapping**, not a Pydantic model/object.
- Code expected `.enabled/.project_id/.location` attributes → crash.

### B. Vertex AI vs Developer API differences
- Dev API uses API key and is sensitive to user location restrictions.
- Vertex AI uses ADC/IAM and expects **publisher resource model names**:
  - `projects/{project}/locations/{location}/publishers/google/models/{model}`
- Some Dev “preview” model IDs don’t exist in Vertex regions → need a fallback.

### C. Organization policy blocked SA key creation
- Org policy `iam.disableServiceAccountKeyCreation` enforced at the org level:
  - `organizations/810543963398/policies/iam.disableServiceAccountKeyCreation` with `enforce: true`.

### D. Reauth loop for user-based ADC
- User credential ADC requires periodic reauth; for servers this is fragile.
- Fix is to use **service account credentials** (or Workload Identity) instead of user ADC.

### E. invalid_scope when refreshing SA credentials
- Service account JWT grant can fail if:
  - requested scopes are malformed / unsupported (e.g., comma-joined string),
  - wrong audience / ID token flow used,
  - client library is requesting something inconsistent.

---

## 3) Actions taken (chronological)

### A. fast-agent code fixes (Vertex auth + model name resolution)
- Added logic to:
  - treat Vertex config as dict *or* object,
  - use Vertex ADC path without requiring API key,
  - resolve Vertex model names to fully qualified publisher resources,
  - apply a preview→base fallback for Vertex (e.g., `*-preview-*` → base model).
- After patch: `pytest tests/e2e/workflow/test_router_agent_e2e.py` → **PASS**.

### B. Billing and cards
- Encountered billing errors and payment issues (`OR_BACR2_31`).
- Confirmed billing linkage for admin-created project later.

### C. Creating new admin project
- `gcloud projects create strato-space-ai-prj`
- Attempted billing account discovery:
  - initially `gcloud beta billing accounts list` returned none;
  - later billing account visible: `01B2C2-DDC036-F7E70C`.
- Linked billing:
  - `gcloud beta billing projects link strato-space-ai-prj --billing-account=01B2C2-DDC036-F7E70C`.

### D. Service account creation
- VP project (`strato-space-ai`):
  - created `fast-agent-vp-service-account`.
  - granted `roles/aiplatform.user` and `roles/serviceusage.serviceUsageConsumer`.
- Admin project (`strato-space-ai-prj`):
  - created `fast-agent-admin-sa`.
  - granted `roles/aiplatform.user`.

### E. SA key creation initially blocked (org policy)
- Diagnosed org policy enforcement:
  - `gcloud organizations list` → org `810543963398`.
  - `gcloud org-policies describe constraints/iam.disableServiceAccountKeyCreation --organization=810543963398` showed enforce=true.
- Lacked permission to change org policy → added role:
  - `gcloud organizations add-iam-policy-binding 810543963398 --member="user:admin@strato.space" --role="roles/orgpolicy.policyAdmin"`
- Set policy to allow key creation:
  - created `/tmp/allow-sa-keys.yaml` and ran `gcloud org-policies set-policy /tmp/allow-sa-keys.yaml`.
- After that, **SA keys created successfully**:
  - `/etc/fast-agent/gcp/fast-agent-admin.json`
  - `/etc/fast-agent/gcp/fast-agent-vp.json`

### F. Runtime permission failure for VEO model
- media-gen-mcp error:
  - `403 ... Permission 'aiplatform.endpoints.predict' denied ... veo-2.0-generate-001`
- Indicates missing IAM permission for publisher model inference in that region/project.

### G. Still getting “Reauthentication needed” from gsh
- Even after `GOOGLE_APPLICATION_CREDENTIALS=/etc/fast-agent/gcp/fast-agent-vp.json`, gsh tool returned:
  - `Reauthentication is needed. Please run gcloud auth application-default login`
- Suggests gsh implementation is using **ADC file** (`application_default_credentials.json`) rather than respecting `GOOGLE_APPLICATION_CREDENTIALS`, or is hardwired to gcloud-based auth.

### H. Service-account refresh test showed invalid_scope
- Removed ADC JSON, used `google.auth.default()` → picked SA creds.
- `creds.refresh(Request())` failed with `invalid_scope`.

---

## 4) Current state

### ✅ Working
- fast-agent e2e routing tests pass for `gemini25` when Vertex path and model name resolution are applied.
- Billing account exists for admin project.
- Organization policy updated to allow SA key creation.
- Both SA JSON keys exist on disk with secure permissions.

### ❌ Still failing / open items
1. **gsh / Sheets API tool** still requests user ADC reauth instead of using SA.
2. **SA token refresh invalid_scope** when using `google.auth.default()` (needs scope/audience debugging).
3. **Vertex VEO call denied** (`aiplatform.endpoints.predict`) for publisher model in `us-central1`.

---

## 5) Recommended next steps (actionable)

### A. Fix gsh tool to use service-account JSON
1. Verify gsh code path:
   - Confirm it uses `google.auth.default()` and respects `GOOGLE_APPLICATION_CREDENTIALS`.
   - If it uses gcloud/ADC explicitly, change it.
2. Ensure correct scopes are passed when constructing credentials for Sheets/Drive:
   - Sheets: `https://www.googleapis.com/auth/spreadsheets`
   - Drive: `https://www.googleapis.com/auth/drive`
   - Docs: `https://www.googleapis.com/auth/documents`

### B. Fix invalid_scope for SA refresh
- Reproduce with explicit scopes:
  - Load SA creds from file and call `with_scopes([...])` before refresh.
- Validate there is no comma-joined scope string.
- Confirm no ID-token audience is being requested unless needed.

### C. Fix Vertex VEO permission
- Grant the SA a role that includes `aiplatform.endpoints.predict` for publisher models.
- Candidate roles (depending on Google’s current IAM mapping):
  - `roles/aiplatform.user` may be insufficient;
  - may require `roles/aiplatform.admin` or a more specific permission set.
- Also confirm region/model availability and whether allowlisting is required for VEO.

### D. Hardening for servers
- Prefer service accounts (or Workload Identity Federation) instead of user `gcloud auth application-default login`.
- Keep org policy allowing key creation as narrow as possible (project/folder exceptions) once bootstrap is done.

---

## 6) Key commands used (reference)

### Org policy
```bash
gcloud organizations list
gcloud org-policies describe constraints/iam.disableServiceAccountKeyCreation --organization=810543963398

gcloud organizations add-iam-policy-binding 810543963398 \
  --member="user:admin@strato.space" \
  --role="roles/orgpolicy.policyAdmin"

cat > /tmp/allow-sa-keys.yaml <<'YAML'
name: organizations/810543963398/policies/iam.disableServiceAccountKeyCreation
spec:
  rules:
  - enforce: false
YAML

gcloud org-policies set-policy /tmp/allow-sa-keys.yaml
```

### Service account + key
```bash
gcloud iam service-accounts create fast-agent-vp-service-account \
  --display-name="fast-agent vp service account"

SA_VP="fast-agent-vp-service-account@strato-space-ai.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding strato-space-ai \
  --member="serviceAccount:${SA_VP}" \
  --role="roles/aiplatform.user"

sudo install -d -m 700 /etc/fast-agent/gcp

gcloud iam service-accounts keys create /etc/fast-agent/gcp/fast-agent-vp.json \
  --iam-account="${SA_VP}"

sudo chmod 600 /etc/fast-agent/gcp/fast-agent-vp.json
```

### Debugging credentials
```bash
rm -f /root/.config/gcloud/application_default_credentials.json
export GOOGLE_APPLICATION_CREDENTIALS=/etc/fast-agent/gcp/fast-agent-vp.json
python - <<'PY'
import google.auth
from google.auth.transport.requests import Request
creds, proj = google.auth.default()
print("default project:", proj)
print("creds type:", type(creds))
print("service_account_email:", getattr(creds, "service_account_email", None))
creds.refresh(Request())
print("token ok, expires:", getattr(creds, "expiry", None))
PY
```

---

## 7) Notes
- Allowing SA key creation org-wide is a significant security relaxation; once keys are minted, consider tightening policy again and using a safer long-term approach (Workload Identity Federation / no long-lived keys).
