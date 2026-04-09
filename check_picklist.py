import requests
import json

ZOHO_CLIENT_ID     = "1000.7FHQAIE3QWLJNLZ6E8R2T4MK2832GU"
ZOHO_CLIENT_SECRET = "15d90522b0da8cab12989a4245cf90f9bb989aff3a"
ZOHO_REFRESH_TOKEN = "1000.e9f4151bc029dc53c981b36eef22ddaa.79189d0c343dd6abc6bff1efae6e96e9"
ZOHO_API_DOMAIN    = "https://www.zohoapis.com"

# Get access token
r = requests.post("https://accounts.zoho.com/oauth/v2/token", data={
    "refresh_token": ZOHO_REFRESH_TOKEN,
    "client_id": ZOHO_CLIENT_ID,
    "client_secret": ZOHO_CLIENT_SECRET,
    "grant_type": "refresh_token"
})
token_data = r.json()
access_token = token_data.get("access_token", "")
api_domain = token_data.get("api_domain", ZOHO_API_DOMAIN)
print(f"Access token: {access_token[:30]}...")

# Get CustomModule1 fields to find payment_kind picklist values
headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}

# Try to get field metadata for CustomModule1
resp = requests.get(f"{api_domain}/crm/v2/settings/fields?module=CustomModule1", headers=headers)
fields_data = resp.json()

print("\n=== CustomModule1 Fields ===")
for field in fields_data.get("fields", []):
    fname = field.get("field_label", "")
    api_name = field.get("api_name", "")
    ftype = field.get("data_type", "")
    if ftype in ["picklist", "multiselectpicklist"] or "תשלום" in fname or "payment" in api_name.lower():
        print(f"\nField: {fname} | API: {api_name} | Type: {ftype}")
        # Show picklist values
        pick_list = field.get("pick_list_values", [])
        for pv in pick_list:
            print(f"  display_value: {pv.get('display_value')} | actual_value: {pv.get('actual_value')}")
