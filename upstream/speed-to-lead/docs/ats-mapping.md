# ATS / HRIS mapping

The same qualify→route pipeline runs a recruiting funnel: an inbound applicant
is a lead, scored for fit and intent, then pushed to your ATS. A live connector
ships for **Greenhouse** (its Harvest API is accessible without an enterprise
sandbox); **Workday**, **SAP SuccessFactors**, and **Paychex** are documented
here as field mappings — wiring them is the same `Ats` protocol plus their auth.

## Field mapping

| Internal (`Lead` / `QualificationResult`) | Greenhouse (Harvest) | Workday (Recruiting) | SAP SuccessFactors | Paychex |
|---|---|---|---|---|
| `lead.name` → first/last | `first_name`, `last_name` | `Legal_Name_Data` | `firstName`, `lastName` | `employee.name` |
| `lead.email` | `email_addresses[].value` | `Email_Address_Data` | `email` | `contact.email` |
| `lead.company` | `company` | `Company_Reference` | `company` | `clientId` |
| `qual.tier` | `tags[] tier:*` | `Candidate_Stage` | `status` | custom field |
| `qual.intent` | `tags[] intent:*` | `Source` | `sourceChannel` | custom field |
| `qual.reasons` | `notes` | `Comment_Data` | `comments` | `notes` |

## Auth, per system

- **Greenhouse** — Basic auth (API key as username, blank password) + an
  `On-Behalf-Of` user id header. Implemented in `integrations/ats.py`.
- **Workday** — OAuth 2.0 against a tenant; SOAP/REST Recruiting service. Needs an ISU + tenant URL.
- **SAP SuccessFactors** — OAuth 2.0 (SAML assertion) against the OData API.
- **Paychex** — OAuth 2.0 client-credentials against the Paychex API.

> Each is the same shape as the Greenhouse connector: build the auth header, map
> the fields above, `POST` the candidate. The differences are auth and endpoint,
> not pipeline logic — which is the point.
