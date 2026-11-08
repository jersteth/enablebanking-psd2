---
name: bank-account-setup
description: Help the user set up access to their own bank accounts through Enable Banking (PSD2): install the enablebanking CLI, create the config, and authorize each bank once. Use when the bank-account-history skill reports a missing CLI, config or consent.
when_to_use: The user wants to connect their bank accounts, or enablebanking is not installed or configured, or an account shows consent_required, or a consent is expiring.
argument-hint: "[status]"
allowed-tools: Bash(enablebanking --version) Bash(enablebanking check) Bash(enablebanking authorize --status *)
---

# Bank account setup

Setting up needs the user at their keyboard (an Enable Banking account, a private key, and a login at their bank). You guide; the user runs the interactive steps themselves. In Claude Code they can type `! <command>` to run a command in this session.

Never read the user's config file, private key (`*.pem`) or state directory, and never ask them to paste a key or an authorization code into the chat.

## 1. Is it installed?
Run `enablebanking --version`. If the command is missing, tell the user to run `pipx install enablebanking-psd2` (or `uvx enablebanking-psd2`). Do not install it yourself.

## 2. Enable Banking application (once)
The user registers their own application at https://enablebanking.com/cp/applications (production, "restricted" mode is enough for their own accounts), downloads the private key (`<application id>.pem`), keeps it outside any git repository with `chmod 600`, and links their own accounts to the application in the control panel. The redirect URL must be whitelisted on the application. The project's `documentation/enablebanking-setup.md` has the details.

## 3. Create the config (once)
Ask the user to run `! enablebanking init`. It asks for the application id, the key path, the redirect URL, their country, and finds their bank and accounts; then it writes the config and runs a check. Afterwards you can run `enablebanking check` to confirm.

## 4. Authorize each bank (every ~180 days)
Ask the user to run `! enablebanking authorize`. It opens the bank's login in their browser and asks them to paste the redirect URL back. One consent per bank covers all that bank's configured accounts.

Confirm with `enablebanking authorize --status --json`: every account should be `authorized`. `expiring` means less than 7 days left: ask the user to run `! enablebanking authorize` again. `consent_required` means no valid consent.

## 5. Then
Once accounts are `authorized`, the `bank-account-history` skill can answer questions about transactions and balances.
