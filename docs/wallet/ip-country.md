# IP country check for Cardano wallet linking

Linking a Cardano stake address is refused for residents and citizens of the
countries in `WALLET_LINK_RESTRICTED_COUNTRIES` (default `US,CA`). The code is
in `src/services/wallet/eligibility.py`; the database reader is
`src/services/wallet/ip_country.py`.

## How the request country is decided

1. `WALLET_LINK_COUNTRY_HEADER`, when set, names a header that a trusted edge
   always sets (for example `cf-ipcountry`). Unset in production: nothing in
   front of the API sets one.
2. Otherwise, when `WALLET_LINK_IP_COUNTRY_DB_PATH` names a database file, the
   country of the client IP. The client IP comes from
   `src.services.auth_rate_limiting.get_client_ip`, which reads the one header
   chosen by `TRUSTED_CLIENT_IP_HEADER` (production: `x-real-ip`).
3. Otherwise there is no country, and the user's attestation is the only check.

| Situation | Result |
|---|---|
| Header present, restricted country | 451 `region_restricted`; account marked restricted (sticky); audit `country_source: header` |
| Header absent, IP country restricted | 451 `region_restricted`; account marked restricted (sticky); audit `country_source: ip` |
| Header absent, IP country allowed | Attestation required as before; the country is stored with the attestation; audit `country_source: ip` |
| Database not configured, missing or unreadable | Attestation only, as before; audit `country_source: none` |
| IP not in the database, private, or not an IP | Attestation only, as before; audit `country_source: none` |

A VPN or proxy changes the apparent country, so the IP check cannot replace
the attestation; it only catches requests that come from a restricted country
openly. The attestation stays required in every case.

## Providing the database file

The backend reads a local file in MaxMind DB format (`.mmdb`). It never
downloads one. Any country database in that format works; two options:

- **DB-IP "IP to Country Lite"** (https://db-ip.com/db/download/ip-to-country-lite),
  MMDB format. Free, no account. Licence: Creative Commons Attribution 4.0
  (CC BY 4.0). The licence requires the attribution "IP Geolocation by DB-IP"
  with a link to https://db-ip.com wherever the data is used, for example on
  the page or terms that mention the region check. Updated monthly.
- **MaxMind GeoLite2 Country** (`GeoLite2-Country.mmdb`). Needs a free MaxMind
  account and a licence key to download, and its own licence terms apply.

Where to put it:

- **Railway volume** (simplest): mount a volume (for example at `/data`),
  copy the file there, and set
  `WALLET_LINK_IP_COUNTRY_DB_PATH=/data/dbip-country-lite.mmdb`.
- **Build step**: download the file during the image build into the image (for
  example `/app/data/`) and point the variable at it. The download needs to
  run where the licence allows it (MaxMind needs the licence key as a build
  secret).

Do not commit the file to this repository.

Updating: replace the file in place (write to a temporary name, then rename).
The backend notices the new modification time on the next lookup and loads
the new file; no restart is needed. Refresh monthly.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WALLET_LINK_IP_COUNTRY_DB_PATH` | empty (off) | Path to the `.mmdb` file |
| `WALLET_LINK_COUNTRY_HEADER` | empty (off) | Trusted country header; wins over the IP lookup |
| `WALLET_LINK_RESTRICTED_COUNTRIES` | `US,CA` | ISO 3166-1 alpha-2 codes |
| `TRUSTED_CLIENT_IP_HEADER` | `x-forwarded-for-rightmost` | Which header carries the client IP (production: `x-real-ip`) |

## Checking it

`GET /admin/status` lists `ip_country_db` under the integrations block:
`not_configured` when the path is empty, `down` with the load error when the
file is missing or unreadable, `ok` with the database type and build epoch
when it is loaded. A file that cannot be loaded is logged once at warning
level and is tried again when it changes.
