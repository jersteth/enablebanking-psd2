# Product Overview

## Purpose
A test client for accessing Belgian personal bank accounts through PSD2 (EU Payment Services Directive 2). It uses the Enable Banking API (enablebanking.com) as the aggregator instead of integrating with each bank separately. The goal is to try out and validate PSD2 account access end to end.

## Target Users
- The developer/owner of the project, using it to explore PSD2 access to their own Belgian personal accounts
- Developers evaluating Enable Banking as a PSD2 aggregator for Belgian banks

## Core Value Proposition
- One integration (Enable Banking) instead of one per bank
- Works through the regulated PSD2 consent flow, so no screen scraping and no sharing bank credentials
- A low-risk way to test the full flow (sandbox first, then real accounts) before building anything larger

## Key Capabilities
- **Bank discovery**: list the Belgian banks (ASPSPs) available through Enable Banking
- **Consent / authorization**: start a PSD2 authorization, send the user to their bank to approve, and handle the callback to set up a session
- **Account information (AIS)**: list the accounts under a consent and fetch account details, balances, and transactions
- **Session lifecycle**: track consent validity and expiry, re-authorize when needed, and revoke sessions
- **Sandbox and production**: run against Enable Banking's sandbox (mock banks) and against real Belgian banks

## Out of Scope (initially)
- Payment initiation (PIS)
- Business/corporate accounts
- Multi-user or hosted deployment
