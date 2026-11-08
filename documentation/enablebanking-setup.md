# Enable Banking Setup Guide

This guide goes from nothing to a passing `psd2-demo run` against one of your own Belgian personal accounts, using Enable Banking's **production** environment in **restricted mode**. Restricted mode is free, needs no contract, and is limited to accounts you link yourself. Enable Banking's terms allow it for evaluation and for personal, non-commercial use.

> Items marked **(unverified)** come from third-party guides or assumptions, not from Enable Banking's documentation.

## 1. Create an Enable Banking account
1. Go to <https://enablebanking.com/cp/applications> and sign up (or log in).
2. You land on the **API applications** page of the Control Panel.

## 2. Register a production application
Click **Add a new application** and fill in the form:

| Field | Value |
|---|---|
| **Environment** | **Production**. Sandbox only offers mock banks. |
| **Private key** | Keep the default: the key is generated in your browser. |
| **Application name** | Any name, e.g. `psd2-demo personal`. Your bank shows it on its consent screen. |
| **Redirect URLs** | See [Choosing a redirect URL](#choosing-a-redirect-url). Recommended: `https://localhost/callback`. |
| Privacy policy / Terms URLs (if asked) | Must be full URLs starting with `https://`. Without the prefix the form returns **"Invalid URL"** (reported by third-party guides, unverified). |
| Description / e-mail (if asked) | Free text / your e-mail. |

Click **Register**. The browser downloads a file named `<application-id>.pem`, e.g. `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.pem`.
- The **file name is your application ID**. You need it as `app_id`; it is the JWT `kid`.
- The key **cannot be downloaded again**. If you lose it, register a new application.

### Choosing a redirect URL
After you approve access at your bank, the bank sends your browser to this URL with `?code=...&state=...` appended. The demo needs that URL back.

| Redirect URL | Capture mode | How it works |
|---|---|---|
| `https://localhost/callback` (recommended) or any `https://` URL you control | `paste` | The browser lands on the URL. The page will likely fail to load, which is fine. Copy the full URL from the address bar and paste it into the terminal. |
| `http://localhost:8765/callback` | `listener` | The demo listens on `127.0.0.1:8765` and captures the redirect automatically. Whether Enable Banking accepts `http://localhost` redirect URLs is **(unverified)**. If registration rejects it, use paste mode. |

The `redirect_url` in your config must match a whitelisted URL **exactly**.

## 3. Store the private key
Keep the key outside the repository and readable only by you:
```bash
mkdir -p ~/.config/psd2demo
mv ~/Downloads/<application-id>.pem ~/.config/psd2demo/
chmod 600 ~/.config/psd2demo/<application-id>.pem
```

## 4. Activate the application by linking your account
A new production application is **Inactive**.
1. In the Control Panel, open your application and click **Activate by linking accounts**.
2. Choose your bank (country Belgium), and authenticate at the bank (itsme, card reader, or app).
3. Select the account you want to use in the demo and confirm.
4. The application status becomes **Active** (restricted mode). From now on it can only read the linked accounts.

Linking does **not** replace the demo's own consent: `psd2-demo run` still sends you to your bank once to approve API access.

## 5. Configure the demo
```bash
cp config.example.toml config.toml   # config.toml is git-ignored
```
Edit `config.toml`:

| Key | Where it comes from | Env var |
|---|---|---|
| `app_id` | the `.pem` file name (without `.pem`) | `EB_APP_ID` |
| `key_path` | the path from step 3 (`~` is expanded) | `EB_KEY_PATH` |
| `redirect_url` | the URL you whitelisted in step 2 | `EB_REDIRECT_URL` |
| `bank_name` | your bank's exact name at Enable Banking (step 6 helps) | `EB_BANK_NAME` |
| `bank_country` | `BE` (default) | `EB_BANK_COUNTRY` |
| `iban` | the account linked in step 4; spaces are allowed | `EB_IBAN` |
| `capture_mode` | `auto` (default): `listener` for `http://localhost:<port>`, otherwise `paste` | `EB_CAPTURE_MODE` |
| `consent_days` | requested consent length (default 1, capped at the bank's maximum) | `EB_CONSENT_DAYS` |
| `callback_timeout` | seconds to wait for the bank redirect (default 300) | `EB_CALLBACK_TIMEOUT` |
| `psu_ip_address` | your public IP (`curl -s https://ifconfig.me`), sent as `Psu-Ip-Address`. Recommended: see below | `EB_PSU_IP` |

**PSU headers.** Banks publish the "PSU headers" they need (`required_psu_headers`, printed by `check`). These tell the bank that you are present. Enable Banking only accepts **all of them or none**: a partial set fails with `PSU_HEADER_NOT_PROVIDED`. The demo sends `Psu-User-Agent` itself, and `Psu-Ip-Address` only if `psu_ip_address` is set.
- When every required header is available, all are sent, and the requests count as "you are present".
- Otherwise none are sent, and the requests count as unattended access. That works, but under PSD2 a bank may limit it to 4 requests per day per account. The demo prints a note when this happens.

Each key can also be overridden per run with a flag (`--bank`, `--iban`, …; see `psd2-demo run --help`).

## 6. Verify the setup
```bash
psd2-demo check
```
It loads the config and key, signs a JWT, and lists the Belgian banks (`GET /aspsps`). It does not start a consent. Expected output:
```
[✓] CONFIG       app aaaaaaaa-…, account BE68 **** **** 7034, capture=paste
[✓] BANK         KBC (BE), max consent validity 180 days
      bank requires PSU headers: Psu-Ip-Address, Psu-User-Agent
```
If the bank name doesn't match exactly, `check` prints the candidate names. Copy one into `bank_name`.

## 7. First run
```bash
psd2-demo run          # "run" is the default: plain `psd2-demo` does the same
```
1. `CONFIG` and `BANK` pass as in `check`.
2. The demo starts a consent (`POST /auth`), prints the bank URL, and opens it in your browser.
3. At the bank: log in, select the **same account** you linked, and approve.
4. The bank redirects to your redirect URL:
   - **paste mode**: the page probably fails to load ("site can't be reached"). Copy the **whole** URL from the address bar (it contains `?code=...&state=...`) and paste it at the prompt within 5 minutes.
   - **listener mode**: the tab shows "Authorization received" and the terminal continues on its own.
5. The demo creates the session, finds your IBAN, and fetches the last 10 days of transactions.

Expected output:
```
[✓] CONFIG       app aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee, account BE68 **** **** 7034, capture=paste
[✓] BANK         KBC (BE), max consent validity 180 days
      approve access at your bank in the browser; if it does not open, visit:
      https://tilisy.enablebanking.com/welcome?sessionid=...
Paste the full redirect URL from the browser address bar: https://localhost/callback?code=...&state=...
[✓] CONSENT      new session valid until 2026-11-09T11:59:00+00:00
[✓] ACCOUNT      BE68 **** **** 7034 Zichtrekening EUR (uid 3f1c...)
[✓] TRANSACTIONS 2 transactions 2026-10-29..2026-11-08, 1 page(s)

Booked      Value          Amount  Cur  Counterparty  Counterparty IBAN  Remittance             Status  Ref
----------  ----------  ---------  ---  ------------  -----------------  ---------------------  ------  -------
2026-11-05  2026-11-05     -42.50  EUR  Delhaize      BE71096123456769   Groceries / Card 1234  BOOK    REF-001
2026-11-01  2026-11-01  +2,500.00  EUR  Employer NV   BE62510007547061   Salary October         BOOK    TX-002

Summary:
  EUR: 2 transactions, credits +2,500.00, debits -42.50, net +2,457.50
```
Exit code `0` means every step passed. Zero transactions is also a pass ("no transactions in range").

### Later runs
- While the consent is valid (`consent_days`, default 1 day), `psd2-demo run` reuses the stored session (`[✓] CONSENT reused session ...`) and doesn't open the browser.
- `psd2-demo run --fresh` forces a new consent.
- `psd2-demo run --export exports/run.json` also writes the normalized transactions and the raw API pages (file mode 0600; `exports/` is git-ignored).
- `psd2-demo run --json` prints a machine-readable result to stdout instead of the tables. If a new consent is needed, the bank URL and paste prompt still appear on stderr, so stdout stays valid JSON.
- `--verbose` logs each HTTP call (tokens, codes and other IBANs redacted).

Local state: `.psd2demo/session.json` (mode 0600) stores the session ID, account UID, validity, and a SHA-256 of the IBAN. Delete it to forget the session.

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `[✗] BANK Application does not exist` (403) | `app_id` doesn't match a registered application | Use the `.pem` file name exactly as `app_id`; check that you registered under the same Control Panel account |
| `[✗] BANK` 401 `Invalid JWT` / signature error | Key doesn't belong to `app_id`, or the system clock is off | Pair the right `.pem` with its app ID; sync the clock (`timedatectl status`) |
| 403 mentioning an inactive/restricted application (exact wording unverified) | App still **Inactive** | Step 4: **Activate by linking accounts** |
| Bank shows an error about the redirect URL, or `/auth` fails with a redirect URL error (exact wording unverified) | `redirect_url` isn't whitelisted exactly | Add it to the application's Redirect URLs, or make the config match character for character |
| `[✗] BANK bank 'X' not found` | Name differs from Enable Banking's ASPSP name | Copy a name from the printed candidates |
| `[✗] CONSENT authorization failed at the bank: access_denied` | Consent cancelled or refused at the bank | Rerun and approve; use the same bank login as when linking |
| `[✗] CONSENT callback state does not match` | Pasted a redirect URL from an older attempt | Paste the URL from the current run |
| `[✗] CONSENT no callback received within 300 s` | Bank flow not finished in time | Rerun; raise `callback_timeout` if needed |
| `[✗] ACCOUNT account ... not in the authorized session` | Account not linked to the app, or not selected at the bank | Link it in the Control Panel (step 4) and select it at the bank; `psd2-demo run --fresh` |
| `[✗] TRANSACTIONS` 401/403 after a reused session | Consent expired or revoked at the bank | `psd2-demo run --fresh` |
| `422 PSU_HEADER_NOT_PROVIDED ... psu-ip-address is not provided` | The bank requires a full set of PSU headers and got a partial one (fixed since the demo sends all or none) | Set `psu_ip_address` to your public IP so the full set is sent |
| `429 Too many requests` | Rate limit; under PSD2 a bank may cap access without the user present at 4 per day per account | Wait and retry later; avoid running in loops |
| `ASPSP_ERROR` | Bank-side problem | Retry later |
| Registration form says **"Invalid URL"** | Privacy/terms URL without `https://` (reported by third-party guides, unverified) | Enter full `https://...` URLs |
| `[✗] CONFIG private key ... cannot be loaded` | Wrong file, or file corrupted or edited | Point `key_path` to the original downloaded `.pem` |
| Warning `private key ... is readable by others` | Loose file permissions | `chmod 600 <key>` |

Still stuck? Run with `--verbose` to see each API response (secrets redacted) and compare with the [Enable Banking API reference](https://enablebanking.com/docs/api/reference/).
