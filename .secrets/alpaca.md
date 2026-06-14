# Alpaca — private credentials (LOCAL ONLY, gitignored)

> Do not commit or share. This folder (`.secrets/`) is in `.gitignore`.

## Paper-trading API keys (connected to Fey as account id 2, label "my-paper")
- API Key ID: `PKMRL3JTPEKK6SUKGGHO4CBJTG`
- Secret Key: `2Xh51vSnbYhE7AfvcCEL9n2nreErRLXprp1rq3JEke1z`
- Verified: 2026-06-03 — `GET /positions/2` returned 200 (auth OK).

## Other saved value (provided 2026-06-03)
- `775f952a-f165-48d6-bd4a-4d8daec8d16a` — UUID, likely an Alpaca account identifier (not the API pair above).

## Reconnect command (if the DB is reset):
```
curl -s -X POST localhost:8000/accounts -H 'content-type: application/json' \
  -d '{"label":"my-paper","alpaca_key_id":"PKMRL3JTPEKK6SUKGGHO4CBJTG","alpaca_secret":"2Xh51vSnbYhE7AfvcCEL9n2nreErRLXprp1rq3JEke1z"}'
```
