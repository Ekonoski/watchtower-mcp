 (keep the previous good content and append this pointer at the top or in the deployment section)

## Railway Environment Variables for the Custom Connector

See RAILWAY_ENV_VARS.md in this repo for the exact list of variables you must set **in the Railway dashboard for the watchtower-mcp service only**.

Critical ones for live functionality (what you are missing right now per your report):
- POLYGON_API_KEY (for live momentum, bearish, regime, research via the connector)
- XAI_API_KEY (for Grok theses)

The main watchtower repo has its own keys for the local scheduled job. Do not confuse the two services in Railway.

After setting in the correct service's Variables tab, redeploy.
