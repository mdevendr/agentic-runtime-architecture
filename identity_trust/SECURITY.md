# Prompt 4 Identity Lab Security Notes

This folder creates real cloud identity resources. Treat it as a security-sensitive lab, not as disposable sample code.

## Secret Material

Never commit or paste these values:

- IAM access keys and secret access keys
- STS session tokens
- Cognito app client secrets
- Google OAuth client secrets
- Microsoft Entra client secrets
- OAuth authorization codes
- JWT access tokens, ID tokens, and refresh tokens
- Bearer `Authorization` headers
- Generated `.env` files
- Raw evidence containing tokens or credentials

The generated env files are gitignored. The setup scripts write secrets to those files but print redacted placeholders for sensitive values.

## JWT Handling

JWTs are bearer credentials. Anyone with a valid access token can use it until it expires if the receiving service trusts its issuer, audience, and claims.

For this lab:

- Do not save full JWTs in documentation, screenshots, issue comments, or evidence.
- Decode only the non-sensitive claims needed for debugging, such as `iss`, `aud`, `azp`, `scp`, `roles`, `tid`, `ver`, and `exp`.
- Redact the full token string whenever logging request headers, token responses, or callback payloads.
- Prefer short token lifetimes for test clients.

## Local Evidence

`identity_tests.py run-scenario` redacts common secret patterns before writing evidence files:

- JWT-looking strings
- `Authorization: Bearer ...`
- OAuth token JSON fields
- client secret fields
- IAM secret access keys and session tokens

Evidence should still be reviewed before sharing because providers can introduce new field names.

## Rotation Required

Rotate any credential that was printed, pasted, screenshotted, or stored outside the gitignored env files.

For this lab, rotate/delete:

- the Microsoft Entra client secret that was previously pasted during setup
- Cognito machine app client secret if it was printed before redaction was added
- IAM user access keys created before redaction was added
- any Google OAuth client secret that was copied into chat, docs, terminal logs, or screenshots

## Production Guidance

For production-style implementations:

- Store secrets in AWS Secrets Manager, SSM Parameter Store, Azure Key Vault, or an equivalent managed secret store.
- Prefer temporary credentials over IAM users.
- Scope IAM policies to exact Runtime ARNs and runtime endpoints.
- Scope JWT authorizers to exact issuer, audience, client/app ID, and required scopes or roles.
- Add endpoint policies for private VPC endpoints.
- Keep token and auth failure logs useful but redacted.
- Add cleanup automation for test users, app clients, roles, Lambda functions, and runtimes.
