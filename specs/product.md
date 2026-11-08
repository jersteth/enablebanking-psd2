# Product Overview

## Purpose
Access to your own personal bank accounts through PSD2 (EU Payment Services Directive 2), in any country Enable Banking supports (tested with Belgian banks), as a CLI, a Python API, agent skills and an MCP server. It uses the Enable Banking API (enablebanking.com) as the aggregator instead of integrating with each bank separately. Every user brings their own Enable Banking application in restricted production mode. It began as a test client to try out and validate PSD2 account access end to end.

## Target Users
- Developers (and their agents) reading their own personal bank accounts, using their own Enable Banking application
- Developers evaluating Enable Banking as a PSD2 aggregator

## Core Value Proposition
- One integration (Enable Banking) instead of one per bank
- Works through the regulated PSD2 consent flow, so no screen scraping and no sharing bank credentials
- A low-risk way to test the full flow (sandbox first, then real accounts) before building anything larger

## Key Capabilities
- **Bank discovery**: list the banks (ASPSPs) of a country available through Enable Banking
- **Consent / authorization**: start a PSD2 authorization, send the user to their bank to approve, and handle the callback to set up a session
- **Account information (AIS)**: list the accounts under a consent and fetch account details, balances, and transactions
- **Session lifecycle**: track consent validity and expiry, re-authorize when needed, and revoke sessions
- **Sandbox and production**: run against Enable Banking's sandbox (mock banks) and against real banks

## Out of Scope (initially)
- Payment initiation (PIS)
- Business/corporate accounts
- Multi-user or hosted deployment (each user runs their own copy with their own Enable Banking application)

## Distribution (005)
A PyPI package (`enablebanking-psd2`), a Claude Code plugin/marketplace with the `bank-account-history` and `bank-account-setup` skills, and an optional read-only MCP server.
